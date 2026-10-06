"""core.agent placed into a Workflow and executed by the normal runtime:

    WorkflowService (publish) → ExecutionService → ExecutionRuntime
    → core.agent ⇄ AIService → LiteLLM adapter → local OpenAI-compatible
      server (scripted tool calls)
    → core.file_write / core.file_read in a temporary workspace,

with the provider key from the encrypted Credential Store (PostgreSQL).
Asserted on the *persisted* execution; nothing reaches a public provider.
"""

import json
import os
from pathlib import Path
from typing import Any

import pytest

from akasha.application.ai import AIService, LLMProvider
from akasha.application.credentials import CredentialService
from akasha.application.executions import ExecutionService
from akasha.application.workflows import WorkflowService
from akasha.domain.credentials import CredentialKind, Secret
from akasha.domain.execution import Execution, ExecutionStatus
from akasha.domain.graph import Edge, Graph, NodeInstance
from akasha.domain.workflow import WorkflowDraft
from akasha.infrastructure.database import Database
from akasha.infrastructure.persistence import SqlAlchemyUnitOfWork
from akasha.nodes.builtins import create_builtin_registry
from akasha.providers.litellm_adapter import LiteLLMAdapter
from akasha.runtime.runtime import ExecutionRuntime
from akasha.security.crypto import SecretCipher
from akasha.security.paths import Workspace
from tests.support import CHAT_COMPLETION, LocalHttpServer

pytestmark = pytest.mark.usefixtures("clean_db")

CANARY = "canary-agent-runtime-3b7c-never-leak"
AGENT_CONFIG = {
    "provider": "local",
    "model": "gpt-test",
    "credential": "credential:llm-key",
    "system_prompt": "You keep research notes in files.",
    "user_prompt": "Save a note about {{ topic }}, then confirm it.",
    "max_iterations": 5,
    "tools": [
        {"name": "write_file", "node_type": "core.file_write"},
        {"name": "read_file", "node_type": "core.file_read"},
    ],
}


def tool_call_reply(call_id: str, name: str, arguments: dict[str, Any]) -> object:
    return CHAT_COMPLETION | {
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": json.dumps(arguments),
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ]
    }


def answer(content: str) -> object:
    return CHAT_COMPLETION | {
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ]
    }


async def run(
    database: Database, server: LocalHttpServer, workspace: Workspace
) -> tuple[Execution, dict[str, str]]:
    def uow() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(database)

    store = CredentialService(uow, SecretCipher(os.urandom(32)))
    await store.create("llm-key", kind=CredentialKind.PROVIDER, value=Secret(CANARY))
    adapter = LiteLLMAdapter("openai", api_base=f"{server.base_url}/v1")
    registry = create_builtin_registry(
        llm=AIService([LLMProvider("local", adapter)]), workspace=workspace
    )

    g, ids = Graph(), {}
    for label, node_type, cfg in (
        ("input", "core.input", {}),
        ("agent", "core.agent", AGENT_CONFIG),
        ("out", "core.output", {}),
    ):
        instance = NodeInstance.create(node_type, config=cfg, label=label)
        g.add_node(instance)
        ids[label] = instance.id
    g.add_edge(Edge(ids["input"], "value", ids["agent"], "variables"))
    g.add_edge(Edge(ids["agent"], "text", ids["out"], "value"))

    workflows = WorkflowService(uow, registry.definitions)
    workflow = await workflows.create_workflow("agent")
    await workflows.update_draft(
        workflow.id,
        expected_revision=workflow.revision,
        name="agent",
        draft=WorkflowDraft(graph=g),
    )
    version = await workflows.publish_workflow(workflow.id)
    executions = ExecutionService(uow)
    queued = await executions.create_execution(
        workflow.id, version.version, input={"topic": "agents"}
    )
    await ExecutionRuntime(uow, registry.catalog, credentials=store).run(queued.id)
    ids["workflow"] = workflow.id
    return await executions.get_execution(queued.id), ids


async def test_agent_uses_tools_through_the_runtime(
    database: Database, http_server: LocalHttpServer, tmp_path: Path
) -> None:
    http_server.state.llm_bodies = [
        tool_call_reply(
            "call_1", "write_file", {"path": "notes/agents.md", "content": "# Agents"}
        ),
        tool_call_reply("call_2", "read_file", {"path": "notes/agents.md"}),
        answer("Note saved: # Agents"),
    ]
    execution, ids = await run(database, http_server, Workspace(tmp_path))

    assert execution.status is ExecutionStatus.COMPLETED
    outputs = execution.node(ids["agent"]).outputs
    assert outputs is not None
    assert outputs["text"] == "Note saved: # Agents"
    assert [(s["tool"], s["ok"]) for s in outputs["steps"]] == [
        ("write_file", True),
        ("read_file", True),
    ]
    assert outputs["metadata"]["iterations"] == 3
    assert outputs["metadata"]["usage"]["total_tokens"] == 45
    assert execution.node(ids["out"]).outputs == {"value": "Note saved: # Agents"}
    note = tmp_path / ids["workflow"] / "notes" / "agents.md"
    assert note.read_text() == "# Agents"

    # The provider saw the derived tool schemas and the full conversation.
    first, _, last = (json.loads(r.body) for r in http_server.state.requests)
    assert [t["function"]["name"] for t in first["tools"]] == [
        "write_file",
        "read_file",
    ]
    assert first["tools"][0]["function"]["parameters"]["required"] == [
        "path",
        "content",
    ]
    assert [m["role"] for m in last["messages"]] == [
        "system",
        "user",
        "assistant",
        "tool",
        "assistant",
        "tool",
    ]
    assert last["messages"][-1]["tool_call_id"] == "call_2"
    assert json.loads(last["messages"][-1]["content"]) == {
        "content": "# Agents",
        "size": 8,
    }
    # The key reaches the provider's Authorization header and nothing else.
    for recorded in http_server.state.requests:
        assert recorded.headers["authorization"] == f"Bearer {CANARY}"
        assert CANARY not in recorded.body.decode()
    assert CANARY not in repr(execution)


async def test_a_runaway_agent_fails_the_execution(
    database: Database, http_server: LocalHttpServer, tmp_path: Path
) -> None:
    http_server.state.llm_body = tool_call_reply("c", "read_file", {"path": "x"})
    execution, ids = await run(database, http_server, Workspace(tmp_path))

    assert execution.status is ExecutionStatus.FAILED
    error = execution.node(ids["agent"]).error
    assert error is not None and error.code == "AGENT_MAX_ITERATIONS"
    assert len(http_server.state.requests) == AGENT_CONFIG["max_iterations"]
