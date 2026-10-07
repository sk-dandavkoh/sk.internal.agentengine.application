"""Workflow import / export against PostgreSQL (roadmap Phase 18 acceptance):

    export → (another installation) → import → validate → compare semantic
    workflow → publish → execute

plus secret exclusion, dependency/version incompatibility and atomicity.
"Another installation" is the same migrated schema emptied, with its own
encryption key, node registry and credential values: nothing of the source
survives except the package bytes.
"""

import base64
import io
import os
import zipfile
from dataclasses import replace
from typing import Any

import pytest

from akasha.application.ai import AIService, LLMProvider
from akasha.application.credentials import CredentialService
from akasha.application.executions import ExecutionService
from akasha.application.packages import WorkflowPackageService
from akasha.application.workflow_package import (
    NodeRequirement,
    Requirements,
    WorkflowPackage,
    WorkflowPackageIncompatibleError,
    write_package,
)
from akasha.application.workflows import (
    UowFactory,
    WorkflowNotFoundError,
    WorkflowService,
    WorkflowVersionNotFoundError,
)
from akasha.domain.credentials import CredentialKind, PlaintextSecretError, Secret
from akasha.domain.event import EventType
from akasha.domain.execution import ExecutionStatus
from akasha.domain.graph import Edge, Graph, NodeInstance
from akasha.domain.graph_validation import ValidationCode
from akasha.domain.node import NodeDefinition, NodeServices
from akasha.domain.primitives import new_id
from akasha.domain.trigger import Trigger, TriggerType
from akasha.domain.workflow import Dependency, Workflow, WorkflowDraft, WorkflowStatus
from akasha.infrastructure.database import Database
from akasha.nodes.builtins import create_builtin_registry
from akasha.providers.litellm_adapter import LiteLLMAdapter
from akasha.runtime.runtime import ExecutionRuntime
from akasha.security.crypto import SecretCipher
from tests.application.test_workflow_package import read
from tests.integration.fixtures import SINK, SOURCE, truncate_all
from tests.support import CHAT_COMPLETION, LocalHttpServer, workflow_semantics

pytestmark = pytest.mark.usefixtures("clean_db")

SOURCE_VALUE = "canary-export-source-7f3a-never-leak"
TARGET_VALUE = "canary-export-target-19bd-never-leak"
FILES = {
    "docs/README.md": b"# Lead qualification\n\nNeeds credential:llm-key.\n",
    "prompts/system.txt": b"Du klassifizierst Leads.",
    "resources/examples/leads.csv": b"name,company\nAda,Analytical Engines\n",
    "assets/logo.png": bytes(range(256)) * 4,
}
LLM_CONFIG = {
    "provider": "local",
    "model": "gpt-test",
    "credential": "credential:llm-key",
    "system_prompt": "You classify leads.",
    "user_prompt": "Classify {{ lead.name }} from {{ lead.company }}",
    "temperature": 0,
    "output_schema": {
        "type": "object",
        "properties": {"label": {"type": "string", "enum": ["hot", "cold"]}},
        "required": ["label"],
    },
}


def llm_services(server: LocalHttpServer) -> NodeServices:
    adapter = LiteLLMAdapter("openai", api_base=f"{server.base_url}/v1")
    return NodeServices(llm=AIService([LLMProvider("local", adapter)]))


def llm_draft() -> WorkflowDraft:
    """input → llm → output, with triggers and declared dependencies."""
    graph = Graph()
    nodes = [
        NodeInstance.create("core.input", config={}, label="input"),
        NodeInstance.create("core.llm", config=LLM_CONFIG, label="llm"),
        NodeInstance.create("core.output", config={}, label="out"),
    ]
    for node in nodes:
        graph.add_node(node)
    graph.add_edge(Edge(nodes[0].id, "value", nodes[1].id, "variables"))
    graph.add_edge(Edge(nodes[1].id, "structured", nodes[2].id, "value"))
    return WorkflowDraft(
        graph=graph,
        triggers=[
            Trigger.create(TriggerType.MANUAL),
            Trigger.create(TriggerType.CRON, config={"cron": "0 9 * * 1"}),
        ],
        dependencies=[Dependency("node", "core.llm", ">=1.0.0,<2.0.0")],
        description="Qualifies inbound leads.",
    )


async def store_draft(uow: UowFactory, workflow_id: str, draft: WorkflowDraft) -> None:
    """Write a draft (files included) the way an import would."""
    async with uow() as u:
        workflow = await u.workflows.get(workflow_id)
        assert workflow is not None
        workflow.replace_draft(draft, name=workflow.name)
        await u.workflows.save(workflow)
        await u.commit()


async def store_raw(uow: UowFactory, workflow: Workflow) -> None:
    """Persist without the domain write gate: data stored before Phase 13."""
    async with uow() as u:
        await u.workflows.save(workflow)
        await u.commit()


async def events(uow: UowFactory, workflow_id: str) -> list[EventType]:
    async with uow() as u:
        return [e.event_type for e in await u.events.list(workflow_id=workflow_id)]


async def workflow_count(uow: UowFactory) -> int:
    async with uow() as u:
        return len(await u.workflows.list())


def assert_secret_free(package: bytes, *secrets: bytes) -> None:
    """Neither the archive nor any unpacked entry contains the values,
    raw or base64-encoded."""
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        blobs = [package] + [archive.read(i) for i in archive.infolist()]
    for secret in secrets:
        for form in (secret, base64.b64encode(secret)):
            assert all(form not in blob for blob in blobs)


def answer(content: str) -> dict[str, Any]:
    return CHAT_COMPLETION | {
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ]
    }


# -- the acceptance loop -------------------------------------------------------


async def test_round_trip_across_installations(
    database: Database, uow: UowFactory, http_server: LocalHttpServer
) -> None:
    # Source installation: credential, workflow with files, published v1.
    source_key = os.urandom(32)
    source_store = CredentialService(uow, SecretCipher(source_key))
    await source_store.create(
        "llm-key", kind=CredentialKind.PROVIDER, value=Secret(SOURCE_VALUE)
    )
    source_nodes = create_builtin_registry()
    source_workflows = WorkflowService(uow, source_nodes.definitions)
    workflow = await source_workflows.create_workflow("Leads")
    await store_draft(uow, workflow.id, replace(llm_draft(), files=FILES))
    version = await source_workflows.publish_workflow(workflow.id)
    assert version.files == FILES
    published = await source_workflows.get_version(workflow.id, version.version)
    assert published.files == FILES  # persisted snapshot

    package = await WorkflowPackageService(
        uow, source_nodes.definitions
    ).export_workflow(workflow.id, version=version.version)
    assert_secret_free(
        package,
        SOURCE_VALUE.encode(),
        source_key,
        base64.b64encode(source_key),
    )
    assert await events(uow, workflow.id) == [
        EventType.WORKFLOW_PUBLISHED,
        EventType.WORKFLOW_EXPORTED,
    ]
    expected = workflow_semantics(
        "Leads",
        WorkflowDraft(
            graph=published.graph,
            triggers=list(published.triggers),
            dependencies=list(published.dependencies),
            description=published.description,
            files=dict(published.files),
        ),
    )

    # Target installation: empty database, its own key, nodes and secret.
    await truncate_all(database)
    target_nodes = create_builtin_registry()
    target_workflows = WorkflowService(uow, target_nodes.definitions)
    target_packages = WorkflowPackageService(uow, target_nodes.definitions)
    result = await target_packages.import_workflow(package)
    assert result.missing_credentials == ("llm-key",)
    assert result.validation.ok
    imported = await target_workflows.get_workflow(result.workflow.id)
    assert imported.id != workflow.id
    assert (imported.status, imported.current_version) == (WorkflowStatus.DRAFT, 0)
    assert workflow_semantics(imported.name, imported.draft) == expected
    assert (await target_workflows.validate_workflow(imported.id)).ok
    assert await events(uow, imported.id) == [EventType.WORKFLOW_IMPORTED]

    target_store = CredentialService(uow, SecretCipher(os.urandom(32)))
    await target_store.create(
        "llm-key", kind=CredentialKind.PROVIDER, value=Secret(TARGET_VALUE)
    )
    target_version = await target_workflows.publish_workflow(imported.id)
    assert target_version.files == FILES

    http_server.state.llm_body = answer('{"label": "hot"}')
    executions = ExecutionService(uow)
    queued = await executions.create_execution(
        imported.id,
        target_version.version,
        input={"lead": {"name": "Ada", "company": "Analytical Engines"}},
    )
    await ExecutionRuntime(
        uow,
        target_nodes.catalog,
        credentials=target_store,
        services=llm_services(http_server),
    ).run(queued.id)
    done = await executions.get_execution(queued.id)
    assert done.status is ExecutionStatus.COMPLETED
    out = next(n for n in imported.draft.graph.nodes.values() if n.label == "out")
    assert done.node(out.id).outputs == {"value": {"label": "hot"}}
    # The target resolved its own credential under the same reference.
    (request,) = http_server.state.requests
    assert request.headers["authorization"] == f"Bearer {TARGET_VALUE}"

    # Re-export on the target: the same semantic workflow again.
    again = read(await target_packages.export_workflow(imported.id, version=1))
    assert workflow_semantics(again.name, again.draft) == expected


async def test_draft_export_of_work_in_progress_imports_with_its_issues(
    uow: UowFactory, workflow_service: WorkflowService, node_definitions: Any
) -> None:
    workflow = await workflow_service.create_workflow("wip")
    graph = Graph()
    graph.add_node(NodeInstance.create("test.sink", config={"prefix": ">"}))
    await store_draft(uow, workflow.id, WorkflowDraft(graph=graph, files=FILES))
    packages = WorkflowPackageService(uow, node_definitions)

    data = await packages.export_workflow(workflow.id)  # the draft
    assert read(data).source_workflow_version is None
    result = await packages.import_workflow(data)
    assert {i.code for i in result.validation.issues} == {
        ValidationCode.MISSING_REQUIRED_INPUT,
        ValidationCode.DISCONNECTED_NODE,
    }
    assert result.workflow.draft.files == FILES

    # Editing the imported draft through the draft API keeps its files.
    edited = await workflow_service.update_draft(
        result.workflow.id,
        expected_revision=result.workflow.revision,
        name="renamed",
        draft=WorkflowDraft(graph=graph),
    )
    assert edited.draft.files == FILES
    assert (await workflow_service.get_workflow(edited.id)).draft.files == FILES


# -- secrets ---------------------------------------------------------------


@pytest.mark.parametrize(
    "draft",
    [
        WorkflowDraft(
            graph=Graph(
                nodes={
                    "node_" + "a" * 32: NodeInstance(
                        id="node_" + "a" * 32,
                        node_type="test.sink",
                        config={"api_key": "sk-legacy-plaintext-0123456789"},
                    )
                }
            )
        ),
        WorkflowDraft(
            files={"docs/setup.md": b"token: sk-legacy-plaintext-0123456789"}
        ),
    ],
    ids=["config", "file"],
)
async def test_export_refuses_legacy_plaintext_secrets(
    uow: UowFactory, node_definitions: Any, draft: WorkflowDraft
) -> None:
    workflow = Workflow(id=new_id("wf"), name="legacy", draft=draft)
    await store_raw(uow, workflow)
    with pytest.raises(PlaintextSecretError) as info:
        await WorkflowPackageService(uow, node_definitions).export_workflow(workflow.id)
    assert "sk-legacy" not in repr(info.value.details)
    assert await events(uow, workflow.id) == []  # refused, nothing recorded


async def test_import_rejects_plaintext_secrets_atomically(
    uow: UowFactory, node_definitions: Any
) -> None:
    node = NodeInstance.create(
        "test.sink", config={"prefix": "Bearer abcdefghijklmnop"}
    )
    graph = Graph()
    graph.add_node(node)
    data = write_package(  # a hand-made package: the writer does not police
        WorkflowPackage(
            name="evil",
            draft=WorkflowDraft(graph=graph),
            requires=Requirements(
                nodes=[NodeRequirement(node_type="test.sink", version="1.0.0")]
            ),
            engine_version="0.1.0",
        )
    )
    with pytest.raises(PlaintextSecretError) as info:
        await WorkflowPackageService(uow, node_definitions).import_workflow(data)
    assert info.value.details == {"paths": [f"nodes.{node.id}.config.prefix"]}
    assert await workflow_count(uow) == 0


async def test_credential_values_never_enter_an_export(
    uow: UowFactory, workflow_service: WorkflowService, http_server: LocalHttpServer
) -> None:
    key = os.urandom(32)
    store = CredentialService(uow, SecretCipher(key))
    await store.create(
        "llm-key", kind=CredentialKind.PROVIDER, value=Secret(SOURCE_VALUE)
    )
    nodes = create_builtin_registry()
    workflows = WorkflowService(uow, nodes.definitions)
    workflow = await workflows.create_workflow("p")
    await store_draft(uow, workflow.id, llm_draft())
    data = await WorkflowPackageService(uow, nodes.definitions).export_workflow(
        workflow.id
    )
    assert_secret_free(data, SOURCE_VALUE.encode(), key, base64.b64encode(key))
    assert read(data).requires.credentials == ["llm-key"]


# -- dependencies and versions ---------------------------------------------------


def sink_at(version: str) -> NodeDefinition:
    return replace(SINK, metadata=replace(SINK.metadata, version=version))


async def exported_sink_workflow(
    uow: UowFactory, dependencies: list[Dependency] | None = None
) -> bytes:
    graph = Graph()
    source = NodeInstance.create("test.source")
    sink = NodeInstance.create("test.sink", config={"prefix": ">"})
    graph.add_node(source)
    graph.add_node(sink)
    graph.add_edge(Edge(source.id, "text", sink.id, "text"))
    workflow = Workflow.create("deps")
    workflow.replace_draft(
        WorkflowDraft(graph=graph, dependencies=dependencies or []), name="deps"
    )
    await store_raw(uow, workflow)
    definitions = {d.node_id: d for d in (SOURCE, SINK)}
    data = await WorkflowPackageService(
        uow, definitions, plugins={"text_tools": "1.2.0"}
    ).export_workflow(workflow.id)
    await truncate_all_workflows(uow)
    return data


async def truncate_all_workflows(uow: UowFactory) -> None:
    async with uow() as u:
        for p in await u.workflows.list():
            await u.workflows.delete(p.id, revision=p.revision)
        await u.commit()


@pytest.mark.parametrize(
    ("target", "code"),
    [
        ({"test.source": SOURCE}, ValidationCode.MISSING_DEPENDENCY),
        (
            {"test.source": SOURCE, "test.sink": sink_at("2.0.0")},
            ValidationCode.INCOMPATIBLE_DEPENDENCY,
        ),
        (
            {"test.source": SOURCE, "test.sink": sink_at("0.9.0")},
            ValidationCode.INCOMPATIBLE_DEPENDENCY,
        ),
    ],
    ids=["missing-node", "newer-major", "older-version"],
)
async def test_import_rejects_unavailable_or_incompatible_node_types(
    uow: UowFactory, target: dict[str, NodeDefinition], code: ValidationCode
) -> None:
    data = await exported_sink_workflow(uow)
    with pytest.raises(WorkflowPackageIncompatibleError) as info:
        await WorkflowPackageService(uow, target).import_workflow(data)
    assert [i.code for i in info.value.issues] == [code]
    (issue,) = info.value.issues
    assert info.value.details == {
        "issues": [{"code": code.value, "message": issue.message}]
    }
    assert await workflow_count(uow) == 0


async def test_import_accepts_a_compatible_newer_minor(uow: UowFactory) -> None:
    data = await exported_sink_workflow(uow)
    target = {"test.source": SOURCE, "test.sink": sink_at("1.4.0")}
    result = await WorkflowPackageService(uow, target).import_workflow(data)
    assert result.validation.ok


@pytest.mark.parametrize(
    ("plugins", "code"),
    [
        ({}, ValidationCode.MISSING_DEPENDENCY),
        ({"text_tools": "2.0.0"}, ValidationCode.INCOMPATIBLE_DEPENDENCY),
        ({"text_tools": "1.5.0"}, None),
    ],
)
async def test_import_checks_declared_plugin_dependencies(
    uow: UowFactory, plugins: dict[str, str], code: ValidationCode | None
) -> None:
    data = await exported_sink_workflow(
        uow, [Dependency("plugin", "text_tools", ">=1.0.0,<2.0.0")]
    )
    service = WorkflowPackageService(
        uow, {d.node_id: d for d in (SOURCE, SINK)}, plugins=plugins
    )
    if code is None:
        assert (await service.import_workflow(data)).validation.ok
        return
    with pytest.raises(WorkflowPackageIncompatibleError) as info:
        await service.import_workflow(data)
    assert [i.code for i in info.value.issues] == [code]
    assert await workflow_count(uow) == 0


async def test_export_refuses_a_workflow_using_an_uninstalled_node_type(
    uow: UowFactory, node_definitions: Any
) -> None:
    graph = Graph()
    graph.add_node(NodeInstance.create("gone.plugin_node"))
    workflow = Workflow.create("orphan")
    workflow.replace_draft(WorkflowDraft(graph=graph), name="orphan")
    await store_raw(uow, workflow)
    with pytest.raises(WorkflowPackageIncompatibleError) as info:
        await WorkflowPackageService(uow, node_definitions).export_workflow(workflow.id)
    assert info.value.details == {"node_type": "gone.plugin_node"}


async def test_export_unknown_workflow_or_version(
    uow: UowFactory, workflow_service: WorkflowService, node_definitions: Any
) -> None:
    packages = WorkflowPackageService(uow, node_definitions)
    with pytest.raises(WorkflowNotFoundError):
        await packages.export_workflow(new_id("wf"))
    workflow = await workflow_service.create_workflow("p")
    with pytest.raises(WorkflowVersionNotFoundError):
        await packages.export_workflow(workflow.id, version=1)
