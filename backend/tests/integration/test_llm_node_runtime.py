"""core.llm placed into a Project and executed by the normal runtime
(roadmap Phase 15 acceptance):

    ProjectService (publish) → ExecutionService → ExecutionRuntime → core.llm
    → AIService → LiteLLM adapter → local OpenAI-compatible server,
    with the provider key from the encrypted Credential Store (PostgreSQL).

Asserted on the *persisted* execution; nothing reaches a public provider.
"""

import json
import os
from typing import Any

import pytest

from akasha.application.ai import AIService, LLMProvider
from akasha.application.credentials import CredentialService
from akasha.application.executions import ExecutionService
from akasha.application.projects import ProjectService, ProjectValidationFailedError
from akasha.domain.credentials import CredentialKind, Secret
from akasha.domain.execution import Execution, ExecutionStatus, NodeExecutionStatus
from akasha.domain.graph import Edge, Graph, NodeInstance
from akasha.domain.node import NodeErrorKind
from akasha.domain.project import ProjectDraft
from akasha.infrastructure.database import Database
from akasha.infrastructure.persistence import SqlAlchemyUnitOfWork
from akasha.nodes.builtins import create_builtin_registry
from akasha.providers.litellm_adapter import LiteLLMAdapter
from akasha.runtime.runtime import ExecutionRuntime
from akasha.security.crypto import SecretCipher
from tests.support import CHAT_COMPLETION, LocalHttpServer

pytestmark = pytest.mark.usefixtures("clean_db")

CANARY = "canary-llm-runtime-91c2-never-leak"
REF = "credential:llm-key"
LLM_CONFIG = {
    "provider": "local",
    "model": "gpt-test",
    "credential": REF,
    "system_prompt": "You classify leads.",
    "user_prompt": "Classify {{ lead.name }} from {{ lead.company }}:\n{{ lead }}",
    "temperature": 0,
    "max_tokens": 50,
    "output_schema": {
        "type": "object",
        "properties": {"label": {"type": "string", "enum": ["hot", "cold"]}},
        "required": ["label"],
    },
}
LEAD = {"lead": {"name": "Ada", "company": "Analytical Engines"}}


def graph(config: dict[str, Any]) -> tuple[Graph, dict[str, str]]:
    """input → llm(variables) ; llm.structured → output"""
    g, ids = Graph(), {}
    for label, node_type, cfg in (
        ("input", "core.input", {}),
        ("llm", "core.llm", config),
        ("out", "core.output", {}),
    ):
        instance = NodeInstance.create(node_type, config=cfg, label=label)
        g.add_node(instance)
        ids[label] = instance.id
    g.add_edge(Edge(ids["input"], "value", ids["llm"], "variables"))
    g.add_edge(Edge(ids["llm"], "structured", ids["out"], "value"))
    return g, ids


async def run(
    database: Database, server: LocalHttpServer, config: dict[str, Any]
) -> tuple[Execution, dict[str, str]]:
    def uow() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(database)

    store = CredentialService(uow, SecretCipher(os.urandom(32)))
    await store.create("llm-key", kind=CredentialKind.PROVIDER, value=Secret(CANARY))
    adapter = LiteLLMAdapter("openai", api_base=f"{server.base_url}/v1")
    registry = create_builtin_registry(llm=AIService([LLMProvider("local", adapter)]))

    projects = ProjectService(uow, registry.definitions)
    project = await projects.create_project("llm")
    g, ids = graph(config)
    await projects.update_draft(
        project.id,
        expected_revision=project.revision,
        name="llm",
        draft=ProjectDraft(graph=g),
    )
    version = await projects.publish_project(project.id)
    executions = ExecutionService(uow)
    queued = await executions.create_execution(project.id, version.version, input=LEAD)
    await ExecutionRuntime(uow, registry.catalog, credentials=store).run(queued.id)
    return await executions.get_execution(queued.id), ids


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


async def test_llm_node_runs_through_the_runtime(
    database: Database, http_server: LocalHttpServer
) -> None:
    http_server.state.llm_body = answer('{"label": "hot"}')
    execution, ids = await run(database, http_server, LLM_CONFIG)

    assert execution.status is ExecutionStatus.COMPLETED
    llm = execution.node(ids["llm"]).outputs
    assert llm is not None
    assert llm["structured"] == {"label": "hot"}
    assert llm["metadata"]["usage"] == {
        "input_tokens": 12,
        "output_tokens": 3,
        "total_tokens": 15,
    }
    assert llm["metadata"]["cost"] is None  # custom endpoint: no price known
    assert execution.node(ids["out"]).outputs == {"value": {"label": "hot"}}
    assert execution.metadata.node_versions["core.llm"] == "1.0.0"

    (recorded,) = http_server.state.requests
    body = json.loads(recorded.body)
    assert body["messages"][1]["content"] == (
        "Classify Ada from Analytical Engines:\n"
        '{"name": "Ada", "company": "Analytical Engines"}'
    )
    assert body["temperature"] == 0 and body["max_tokens"] == 50
    assert body["response_format"]["type"] == "json_schema"
    # The key reaches the provider's Authorization header and nothing else.
    assert recorded.headers["authorization"] == f"Bearer {CANARY}"
    assert CANARY not in recorded.body.decode()
    assert CANARY not in repr(execution)


async def test_missing_variable_fails_the_execution_without_a_provider_call(
    database: Database, http_server: LocalHttpServer
) -> None:
    config = LLM_CONFIG | {"user_prompt": "{{ lead.email }}"}
    execution, ids = await run(database, http_server, config)

    assert execution.status is ExecutionStatus.FAILED
    node = execution.node(ids["llm"])
    assert node.status is NodeExecutionStatus.FAILED
    assert node.error is not None
    assert (node.error.code, node.error.kind) == (
        "PROMPT_VARIABLE_MISSING",
        NodeErrorKind.VALIDATION,
    )
    assert execution.node(ids["out"]).status is not NodeExecutionStatus.COMPLETED
    assert http_server.state.requests == []


async def test_provider_error_is_persisted_redacted(
    database: Database, http_server: LocalHttpServer
) -> None:
    http_server.state.llm_status = 429
    http_server.state.llm_body = {
        "error": {"message": f"slow down, key {CANARY}", "type": "rate_limit"}
    }
    execution, ids = await run(database, http_server, LLM_CONFIG)

    assert execution.status is ExecutionStatus.FAILED
    error = execution.node(ids["llm"]).error
    assert error is not None
    assert (error.code, error.kind) == ("PROVIDER_RATE_LIMIT", NodeErrorKind.TRANSIENT)
    assert CANARY not in error.message


async def test_malformed_template_is_rejected_at_publish(
    database: Database, http_server: LocalHttpServer
) -> None:
    config = LLM_CONFIG | {"user_prompt": "{{ __import__('os').getcwd() }}"}
    with pytest.raises(ProjectValidationFailedError):
        await run(database, http_server, config)
    assert http_server.state.requests == []
