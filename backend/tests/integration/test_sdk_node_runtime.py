"""An SDK-built custom node inside the real engine (roadmap Phase 16):

    NodeRegistry.register → WorkflowService (publish) → ExecutionRuntime
    → community.hmac_sign with its secret from the encrypted Credential Store

Proves that a node written against ``akasha.sdk`` only is registered,
validated when saved and published, executed, and that what it emits through the SDK
(events, metrics) reaches the event store and Prometheus metrics.
"""

import os
from typing import Any

import pytest
from examples.hmac_signer import HmacSignNode, verify

from akasha.application.credentials import CredentialService
from akasha.application.executions import ExecutionService
from akasha.application.workflows import WorkflowService
from akasha.domain.credentials import CredentialKind, PlaintextSecretError, Secret
from akasha.domain.event import EventType
from akasha.domain.execution import Execution, ExecutionStatus
from akasha.domain.graph import Edge, Graph, NodeInstance
from akasha.domain.workflow import WorkflowDraft
from akasha.infrastructure.database import Database
from akasha.infrastructure.persistence import SqlAlchemyUnitOfWork
from akasha.nodes.builtins import create_builtin_registry
from akasha.observability.metrics import EngineMetrics
from akasha.runtime.runtime import ExecutionRuntime
from akasha.security.crypto import SecretCipher

pytestmark = pytest.mark.usefixtures("clean_db")

CANARY = "whsec-runtime-5d1e-never-leak"
REF = "credential:webhook-secret"


async def run(
    database: Database, config: dict[str, Any], metrics: EngineMetrics
) -> tuple[Execution, str, list[str]]:
    def uow() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(database)

    store = CredentialService(uow, SecretCipher(os.urandom(32)))
    await store.create(
        "webhook-secret", kind=CredentialKind.GENERIC, value=Secret(CANARY)
    )
    registry = create_builtin_registry()
    registry.register(HmacSignNode)

    g = Graph()
    source = NodeInstance.create("core.input", config={}, label="input")
    sign = NodeInstance.create("community.hmac_sign", config=config, label="sign")
    g.add_node(source)
    g.add_node(sign)
    g.add_edge(Edge(source.id, "value", sign.id, "payload"))

    workflows = WorkflowService(uow, registry.definitions)
    workflow = await workflows.create_workflow("signer")
    await workflows.update_draft(
        workflow.id,
        expected_revision=workflow.revision,
        name="signer",
        draft=WorkflowDraft(graph=g),
    )
    version = await workflows.publish_workflow(workflow.id)
    executions = ExecutionService(uow)
    queued = await executions.create_execution(
        workflow.id, version.version, input={"order": 42}
    )
    await ExecutionRuntime(
        uow, registry.catalog, credentials=store, metrics=metrics
    ).run(queued.id)
    async with uow() as u:
        events = await u.events.list(execution_id=queued.id, limit=100)
    trail = [
        f"{e.event_type}:{e.payload['name']}"
        if e.event_type is EventType.NODE_EMITTED
        else str(e.event_type)
        for e in events
        if e.node_id == sign.id
    ]
    return await executions.get_execution(queued.id), sign.id, trail


def metric(metrics: EngineMetrics, name: str) -> float | None:
    return metrics.registry.get_sample_value(
        "akasha_node_metric_total",
        {"node_type": "community.hmac_sign", "metric": name},
    )


async def test_sdk_node_runs_through_the_engine(database: Database) -> None:
    metrics = EngineMetrics()
    execution, sign_id, trail = await run(database, {"secret_credential": REF}, metrics)

    assert execution.status is ExecutionStatus.COMPLETED
    outputs = execution.node(sign_id).outputs
    assert outputs is not None
    assert outputs["body"] == '{"order":42}'
    assert verify(outputs["body"], outputs["signature"], CANARY)
    assert trail == ["node.started", "node.emitted:payload_signed", "node.completed"]
    assert metric(metrics, "payloads_signed") == 1
    assert metric(metrics, "bytes_signed") == len('{"order":42}')
    # node.emitted is not a terminal node event for the lifecycle metrics.
    finished = metrics.registry.get_sample_value(
        "akasha_node_executions_finished_total",
        {"node_type": "community.hmac_sign", "status": "completed"},
    )
    assert finished == 1
    assert (
        metrics.registry.get_sample_value(
            "akasha_node_executions_finished_total",
            {"node_type": "community.hmac_sign", "status": "emitted"},
        )
        is None
    )


async def test_events_of_a_failed_attempt_are_recorded(database: Database) -> None:
    metrics = EngineMetrics()
    execution, sign_id, trail = await run(
        database, {"secret_credential": REF, "max_payload_bytes": 5}, metrics
    )

    assert execution.status is ExecutionStatus.FAILED
    error = execution.node(sign_id).error
    assert error is not None
    assert error.code == "HMAC_PAYLOAD_TOO_LARGE"
    assert trail == ["node.started", "node.emitted:payload_rejected", "node.failed"]
    assert metric(metrics, "payloads_signed") is None


async def test_plaintext_secret_in_config_is_rejected_at_draft_save(
    database: Database,
) -> None:
    with pytest.raises(PlaintextSecretError):
        await run(database, {"secret_credential": CANARY}, EngineMetrics())
