"""Project import / export against PostgreSQL (roadmap Phase 18 acceptance):

    export → (another installation) → import → validate → compare semantic
    project → publish → execute

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
from akasha.application.packages import ProjectPackageService
from akasha.application.project_package import (
    NodeRequirement,
    ProjectPackage,
    ProjectPackageIncompatibleError,
    Requirements,
    write_package,
)
from akasha.application.projects import (
    ProjectNotFoundError,
    ProjectService,
    ProjectVersionNotFoundError,
    UowFactory,
)
from akasha.domain.credentials import CredentialKind, PlaintextSecretError, Secret
from akasha.domain.event import EventType
from akasha.domain.execution import ExecutionStatus
from akasha.domain.graph import Edge, Graph, NodeInstance
from akasha.domain.graph_validation import ValidationCode
from akasha.domain.node import NodeDefinition
from akasha.domain.primitives import new_id
from akasha.domain.project import Dependency, Project, ProjectDraft, ProjectStatus
from akasha.domain.trigger import Trigger, TriggerType
from akasha.infrastructure.database import Database
from akasha.nodes.builtins import create_builtin_registry
from akasha.nodes.registry import NodeRegistry
from akasha.providers.litellm_adapter import LiteLLMAdapter
from akasha.runtime.runtime import ExecutionRuntime
from akasha.security.crypto import SecretCipher
from tests.application.test_project_package import read
from tests.integration.conftest import SINK, SOURCE, truncate_all
from tests.support import CHAT_COMPLETION, LocalHttpServer, project_semantics

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


def llm_registry(server: LocalHttpServer) -> NodeRegistry:
    adapter = LiteLLMAdapter("openai", api_base=f"{server.base_url}/v1")
    return create_builtin_registry(llm=AIService([LLMProvider("local", adapter)]))


def llm_draft() -> ProjectDraft:
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
    return ProjectDraft(
        graph=graph,
        triggers=[
            Trigger.create(TriggerType.MANUAL),
            Trigger.create(TriggerType.CRON, config={"cron": "0 9 * * 1"}),
        ],
        dependencies=[Dependency("node", "core.llm", ">=1.0.0,<2.0.0")],
        description="Qualifies inbound leads.",
    )


async def store_draft(uow: UowFactory, project_id: str, draft: ProjectDraft) -> None:
    """Write a draft (files included) the way an import would."""
    async with uow() as u:
        project = await u.projects.get(project_id)
        assert project is not None
        project.replace_draft(draft, name=project.name)
        await u.projects.save(project)
        await u.commit()


async def store_raw(uow: UowFactory, project: Project) -> None:
    """Persist without the domain write gate: data stored before Phase 13."""
    async with uow() as u:
        await u.projects.save(project)
        await u.commit()


async def events(uow: UowFactory, project_id: str) -> list[EventType]:
    async with uow() as u:
        return [e.event_type for e in await u.events.list(project_id=project_id)]


async def project_count(uow: UowFactory) -> int:
    async with uow() as u:
        return len(await u.projects.list())


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
    # Source installation: credential, project with files, published v1.
    source_key = os.urandom(32)
    source_store = CredentialService(uow, SecretCipher(source_key))
    await source_store.create(
        "llm-key", kind=CredentialKind.PROVIDER, value=Secret(SOURCE_VALUE)
    )
    source_nodes = llm_registry(http_server)
    source_projects = ProjectService(uow, source_nodes.definitions)
    project = await source_projects.create_project("Leads")
    await store_draft(uow, project.id, replace(llm_draft(), files=FILES))
    version = await source_projects.publish_project(project.id)
    assert version.files == FILES
    published = await source_projects.get_version(project.id, version.version)
    assert published.files == FILES  # persisted snapshot

    package = await ProjectPackageService(uow, source_nodes.definitions).export_project(
        project.id, version=version.version
    )
    assert_secret_free(
        package,
        SOURCE_VALUE.encode(),
        source_key,
        base64.b64encode(source_key),
    )
    assert await events(uow, project.id) == [
        EventType.PROJECT_PUBLISHED,
        EventType.PROJECT_EXPORTED,
    ]
    expected = project_semantics(
        "Leads",
        ProjectDraft(
            graph=published.graph,
            triggers=list(published.triggers),
            dependencies=list(published.dependencies),
            description=published.description,
            files=dict(published.files),
        ),
    )

    # Target installation: empty database, its own key, nodes and secret.
    await truncate_all(database)
    target_nodes = llm_registry(http_server)
    target_projects = ProjectService(uow, target_nodes.definitions)
    target_packages = ProjectPackageService(uow, target_nodes.definitions)
    result = await target_packages.import_project(package)
    assert result.missing_credentials == ("llm-key",)
    assert result.validation.ok
    imported = await target_projects.get_project(result.project.id)
    assert imported.id != project.id
    assert (imported.status, imported.current_version) == (ProjectStatus.DRAFT, 0)
    assert project_semantics(imported.name, imported.draft) == expected
    assert (await target_projects.validate_project(imported.id)).ok
    assert await events(uow, imported.id) == [EventType.PROJECT_IMPORTED]

    target_store = CredentialService(uow, SecretCipher(os.urandom(32)))
    await target_store.create(
        "llm-key", kind=CredentialKind.PROVIDER, value=Secret(TARGET_VALUE)
    )
    target_version = await target_projects.publish_project(imported.id)
    assert target_version.files == FILES

    http_server.state.llm_body = answer('{"label": "hot"}')
    executions = ExecutionService(uow)
    queued = await executions.create_execution(
        imported.id,
        target_version.version,
        input={"lead": {"name": "Ada", "company": "Analytical Engines"}},
    )
    await ExecutionRuntime(uow, target_nodes.catalog, credentials=target_store).run(
        queued.id
    )
    done = await executions.get_execution(queued.id)
    assert done.status is ExecutionStatus.COMPLETED
    out = next(n for n in imported.draft.graph.nodes.values() if n.label == "out")
    assert done.node(out.id).outputs == {"value": {"label": "hot"}}
    # The target resolved its own credential under the same reference.
    (request,) = http_server.state.requests
    assert request.headers["authorization"] == f"Bearer {TARGET_VALUE}"

    # Re-export on the target: the same semantic project again.
    again = read(await target_packages.export_project(imported.id, version=1))
    assert project_semantics(again.name, again.draft) == expected


async def test_draft_export_of_work_in_progress_imports_with_its_issues(
    uow: UowFactory, project_service: ProjectService, node_definitions: Any
) -> None:
    project = await project_service.create_project("wip")
    graph = Graph()
    graph.add_node(NodeInstance.create("test.sink", config={"prefix": ">"}))
    await store_draft(uow, project.id, ProjectDraft(graph=graph, files=FILES))
    packages = ProjectPackageService(uow, node_definitions)

    data = await packages.export_project(project.id)  # the draft
    assert read(data).source_project_version is None
    result = await packages.import_project(data)
    assert {i.code for i in result.validation.issues} == {
        ValidationCode.MISSING_REQUIRED_INPUT,
        ValidationCode.DISCONNECTED_NODE,
    }
    assert result.project.draft.files == FILES

    # Editing the imported draft through the draft API keeps its files.
    edited = await project_service.update_draft(
        result.project.id,
        expected_revision=result.project.revision,
        name="renamed",
        draft=ProjectDraft(graph=graph),
    )
    assert edited.draft.files == FILES
    assert (await project_service.get_project(edited.id)).draft.files == FILES


# -- secrets ---------------------------------------------------------------


@pytest.mark.parametrize(
    "draft",
    [
        ProjectDraft(
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
        ProjectDraft(files={"docs/setup.md": b"token: sk-legacy-plaintext-0123456789"}),
    ],
    ids=["config", "file"],
)
async def test_export_refuses_legacy_plaintext_secrets(
    uow: UowFactory, node_definitions: Any, draft: ProjectDraft
) -> None:
    project = Project(id=new_id("proj"), name="legacy", draft=draft)
    await store_raw(uow, project)
    with pytest.raises(PlaintextSecretError) as info:
        await ProjectPackageService(uow, node_definitions).export_project(project.id)
    assert "sk-legacy" not in repr(info.value.details)
    assert await events(uow, project.id) == []  # refused, nothing recorded


async def test_import_rejects_plaintext_secrets_atomically(
    uow: UowFactory, node_definitions: Any
) -> None:
    node = NodeInstance.create(
        "test.sink", config={"prefix": "Bearer abcdefghijklmnop"}
    )
    graph = Graph()
    graph.add_node(node)
    data = write_package(  # a hand-made package: the writer does not police
        ProjectPackage(
            name="evil",
            draft=ProjectDraft(graph=graph),
            requires=Requirements(
                nodes=[NodeRequirement(node_type="test.sink", version="1.0.0")]
            ),
            engine_version="0.1.0",
        )
    )
    with pytest.raises(PlaintextSecretError) as info:
        await ProjectPackageService(uow, node_definitions).import_project(data)
    assert info.value.details == {"paths": [f"nodes.{node.id}.config.prefix"]}
    assert await project_count(uow) == 0


async def test_credential_values_never_enter_an_export(
    uow: UowFactory, project_service: ProjectService, http_server: LocalHttpServer
) -> None:
    key = os.urandom(32)
    store = CredentialService(uow, SecretCipher(key))
    await store.create(
        "llm-key", kind=CredentialKind.PROVIDER, value=Secret(SOURCE_VALUE)
    )
    nodes = llm_registry(http_server)
    projects = ProjectService(uow, nodes.definitions)
    project = await projects.create_project("p")
    await store_draft(uow, project.id, llm_draft())
    data = await ProjectPackageService(uow, nodes.definitions).export_project(
        project.id
    )
    assert_secret_free(data, SOURCE_VALUE.encode(), key, base64.b64encode(key))
    assert read(data).requires.credentials == ["llm-key"]


# -- dependencies and versions ---------------------------------------------------


def sink_at(version: str) -> NodeDefinition:
    return replace(SINK, metadata=replace(SINK.metadata, version=version))


async def exported_sink_project(
    uow: UowFactory, dependencies: list[Dependency] | None = None
) -> bytes:
    graph = Graph()
    source = NodeInstance.create("test.source")
    sink = NodeInstance.create("test.sink", config={"prefix": ">"})
    graph.add_node(source)
    graph.add_node(sink)
    graph.add_edge(Edge(source.id, "text", sink.id, "text"))
    project = Project.create("deps")
    project.replace_draft(
        ProjectDraft(graph=graph, dependencies=dependencies or []), name="deps"
    )
    await store_raw(uow, project)
    definitions = {d.node_id: d for d in (SOURCE, SINK)}
    data = await ProjectPackageService(
        uow, definitions, plugins={"text_tools": "1.2.0"}
    ).export_project(project.id)
    await truncate_all_projects(uow)
    return data


async def truncate_all_projects(uow: UowFactory) -> None:
    async with uow() as u:
        for p in await u.projects.list():
            await u.projects.delete(p.id, revision=p.revision)
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
    data = await exported_sink_project(uow)
    with pytest.raises(ProjectPackageIncompatibleError) as info:
        await ProjectPackageService(uow, target).import_project(data)
    assert [i.code for i in info.value.issues] == [code]
    (issue,) = info.value.issues
    assert info.value.details == {
        "issues": [{"code": code.value, "message": issue.message}]
    }
    assert await project_count(uow) == 0


async def test_import_accepts_a_compatible_newer_minor(uow: UowFactory) -> None:
    data = await exported_sink_project(uow)
    target = {"test.source": SOURCE, "test.sink": sink_at("1.4.0")}
    result = await ProjectPackageService(uow, target).import_project(data)
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
    data = await exported_sink_project(
        uow, [Dependency("plugin", "text_tools", ">=1.0.0,<2.0.0")]
    )
    service = ProjectPackageService(
        uow, {d.node_id: d for d in (SOURCE, SINK)}, plugins=plugins
    )
    if code is None:
        assert (await service.import_project(data)).validation.ok
        return
    with pytest.raises(ProjectPackageIncompatibleError) as info:
        await service.import_project(data)
    assert [i.code for i in info.value.issues] == [code]
    assert await project_count(uow) == 0


async def test_export_refuses_a_project_using_an_uninstalled_node_type(
    uow: UowFactory, node_definitions: Any
) -> None:
    graph = Graph()
    graph.add_node(NodeInstance.create("gone.plugin_node"))
    project = Project.create("orphan")
    project.replace_draft(ProjectDraft(graph=graph), name="orphan")
    await store_raw(uow, project)
    with pytest.raises(ProjectPackageIncompatibleError) as info:
        await ProjectPackageService(uow, node_definitions).export_project(project.id)
    assert info.value.details == {"node_type": "gone.plugin_node"}


async def test_export_unknown_project_or_version(
    uow: UowFactory, project_service: ProjectService, node_definitions: Any
) -> None:
    packages = ProjectPackageService(uow, node_definitions)
    with pytest.raises(ProjectNotFoundError):
        await packages.export_project(new_id("proj"))
    project = await project_service.create_project("p")
    with pytest.raises(ProjectVersionNotFoundError):
        await packages.export_project(project.id, version=1)
