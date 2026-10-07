"""Node test runs: one node of a workflow *draft*, alone, with given inputs
(ADR 0010).

The editor's "Test node": the node's saved draft config runs once through
the same executor as an execution attempt (input validation, timeout,
error classification, secret masking), with the same credential scope (only
references in that config) and log capture. Nothing is persisted: no
execution, no events, no node state; the result goes back to the caller
only. Published versions stay the only thing that *executes* (architecture
§6.2): a test run is a probe of one node, not a run of the workflow.

Side effects are real (an SMTP node sends, a file node writes): the editor
says so before a test of a node that declares ``has_side_effects``. Nodes
that wait for a person cannot run alone (nothing could answer them).
"""

import asyncio
import logging
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from secrets import token_hex
from typing import Any, Final, cast

from akasha.application.persistence import ConcurrentModificationError
from akasha.application.workflows import WorkflowService
from akasha.domain.execution import ExecutionError
from akasha.domain.limits import ResourceLimits, json_size
from akasha.domain.node import (
    NodeErrorKind,
    NodeEvents,
    NodeExecutionContext,
    NodeMetrics,
    NodeResult,
    NodeWait,
)
from akasha.domain.primitives import utcnow
from akasha.errors import AkashaError, NodeConfigurationError
from akasha.observability.logging import log_context
from akasha.runtime.credentials import NodeCredentials, SecretSource
from akasha.runtime.executor import execute_node
from akasha.runtime.node_logs import LogLine, NodeLogCapture, capturing
from akasha.runtime.plan import NodeCatalog
from akasha.security.redaction import redact

# ponytail: one fixed cap for interactive tests; a run's node_timeout can be
# far longer, but nobody waits 15 minutes on a button. Make it a setting if
# a slow node needs longer tests.
TEST_TIMEOUT: Final = timedelta(minutes=2)

logger = logging.getLogger("akasha.runtime.node_test")


class DraftNodeNotFoundError(AkashaError):
    code = "NODE_NOT_FOUND"
    http_status = 404


class NodeNotTestableError(AkashaError):
    code = "NODE_NOT_TESTABLE"
    http_status = 422


@dataclass(frozen=True)
class NodeTestResult:
    status: str  # "completed" | "failed"
    duration_ms: int
    outputs: dict[str, Any] | None = None
    error: ExecutionError | None = None
    logs: list[LogLine] = field(default_factory=list)


class NodeTester:
    def __init__(
        self,
        workflows: WorkflowService,
        catalog: NodeCatalog,
        *,
        credentials: SecretSource | None,
        limits: ResourceLimits,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._workflows = workflows
        self._catalog = catalog
        self._credentials = credentials
        self._limits = limits
        self._clock = clock

    async def test(
        self,
        workflow_id: str,
        node_id: str,
        *,
        expected_revision: int,
        inputs: Mapping[str, Any],
        execution_input: Mapping[str, Any],
    ) -> NodeTestResult:
        workflow = await self._workflows.get_workflow(workflow_id)
        # The caller tests what it shows: never a draft changed meanwhile.
        if workflow.revision != expected_revision:
            raise ConcurrentModificationError(
                "The draft was changed since it was read; reload and retry.",
                details={
                    "expected_revision": expected_revision,
                    "current_revision": workflow.revision,
                },
            )
        instance = workflow.draft.graph.nodes.get(node_id)
        if instance is None:
            raise DraftNodeNotFoundError(
                "The draft has no such node.", details={"node_id": node_id}
            )
        factory = self._catalog.get(instance.node_type)
        if factory is None:
            raise NodeNotTestableError(
                "The node type is not installed.",
                details={"node_type": instance.node_type},
            )
        definition = factory.definition
        if definition.capabilities.waits_for_input:
            raise NodeNotTestableError(
                "A node that waits for a person cannot be tested on its own.",
                details={"node_type": instance.node_type},
            )

        started = self._clock()
        try:
            definition.validate_config(instance.config)
        except NodeConfigurationError as exc:
            return self._failed(
                ExecutionError(
                    code=exc.code,
                    message=exc.message,
                    kind=NodeErrorKind.CONFIGURATION,
                    node_id=node_id,
                ),
                started,
                [],
            )

        test_id = token_hex(16)
        context = NodeExecutionContext(
            execution_id=f"test_{test_id}",
            node_execution_id=f"test_{test_id}",
            node_instance_id=node_id,
            workflow_id=workflow_id,
            workflow_version="draft",
            config=deepcopy(instance.config),
            credentials=None
            if self._credentials is None
            else NodeCredentials(self._credentials, instance.config),
            inputs=deepcopy(dict(inputs)),
            logger=logging.getLogger(f"akasha.nodes.{instance.node_type}"),
            is_cancelled=lambda: False,  # bounded by TEST_TIMEOUT instead
            runtime_metadata={"test_run": True, "attempt": 1},
            execution_input=deepcopy(dict(execution_input)),
            metrics=NodeMetrics(definition.metrics),
            events=NodeEvents(definition.events),
        )
        logs = NodeLogCapture(self._clock)
        # A task of its own, as in an execution: resolved secrets and log
        # capture live in its context, so masking and capture match a run.
        with (
            capturing(logs),
            log_context(
                workflow_id=workflow_id,
                node_id=node_id,
                node_type=instance.node_type,
                test_run_id=test_id,
            ),
        ):
            task = asyncio.create_task(
                execute_node(
                    factory,
                    context,
                    limit=min(TEST_TIMEOUT, self._limits.node_timeout),
                )
            )
        outcome = await task

        if isinstance(outcome, NodeWait):  # excluded above; defensive
            outcome = ExecutionError(
                code="NODE_NOT_TESTABLE",
                message="the node asked to wait for input",
                kind=NodeErrorKind.VALIDATION,
                node_id=node_id,
            )
        if isinstance(outcome, NodeResult):
            exceeded = self._limits.check_output(
                node_id, json_size(dict(outcome.outputs)), 0
            )
            if exceeded is not None:
                outcome = exceeded[1]
        result = (
            self._failed(outcome, started, logs.lines())
            if isinstance(outcome, ExecutionError)
            else NodeTestResult(
                status="completed",
                duration_ms=self._ms(started),
                outputs=cast("dict[str, Any]", redact(dict(outcome.outputs))),
                logs=logs.lines(),
            )
        )
        logger.info(
            "node_test_run",
            extra={
                "event": "node_test_run",
                "workflow_id": workflow_id,
                "node_id": node_id,
                "node_type": instance.node_type,
                "status": result.status,
                "error_code": result.error.code if result.error else None,
                "duration_ms": result.duration_ms,
            },
        )
        return result

    def _failed(
        self, error: ExecutionError, started: datetime, logs: list[LogLine]
    ) -> NodeTestResult:
        return NodeTestResult(
            status="failed", duration_ms=self._ms(started), error=error, logs=logs
        )

    def _ms(self, started: datetime) -> int:
        return int((self._clock() - started).total_seconds() * 1000)
