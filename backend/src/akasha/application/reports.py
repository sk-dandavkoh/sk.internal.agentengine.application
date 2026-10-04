"""Execution reports (roadmap Phase 19, architecture §55).

A report is a read-only projection of *persisted* state: the Execution
aggregate (canonical status, node executions, errors, outputs) plus its
pinned ProjectVersion. The event log is attached as a timeline only; no
report field is derived from events (architecture §18).

Deterministic: the same persisted state yields the same report and, through
``report_json``, the same bytes. Hence no "generated at" timestamp and no
clock: a still-running execution has no duration rather than one that grows.

Redacted: execution input, node outputs, error messages/details and the
cancellation reason pass through ``akasha.security.redaction`` (AGENTS §35).
Known-value masking already happened when the runtime persisted them; this is
the structural pass on top, so a report never needs credential plaintext.
"""

import json
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any, Final, cast

from pydantic import BaseModel, ConfigDict, JsonValue

from akasha.application.executions import load_execution
from akasha.application.persistence import UnitOfWork
from akasha.application.projects import UowFactory
from akasha.domain.event import Event
from akasha.domain.execution import (
    Execution,
    ExecutionError,
    ExecutionStatus,
    NodeExecution,
    NodeExecutionStatus,
)
from akasha.domain.trigger import TriggerType
from akasha.security.redaction import redact, redact_mapping, redact_text

# Bump when a field changes meaning; adding an optional field does not.
REPORT_SCHEMA_VERSION: Final = 1
# Events per execution grow with nodes x attempts; the cap keeps a
# pathological history from producing an unbounded report (AGENTS §43/§75).
MAX_REPORT_EVENTS: Final = 10_000
_EVENT_PAGE: Final = 500


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ErrorReport(_Model):
    code: str
    message: str
    kind: str
    retryable: bool
    node_id: str | None
    details: dict[str, str]


class FailureReport(ErrorReport):
    """A failed attempt of a node. ``attempt`` is derived, not stored: the
    only way back to READY is ``retry``, which records the failure, so the
    n-th recorded failure always ended attempt n (``NODE_ACTIONS``)."""

    attempt: int


class TokenUsageReport(_Model):
    input_tokens: int
    output_tokens: int
    total_tokens: int


class CostReport(_Model):
    """A list-price estimate, never billing data (see ``CostEstimate``)."""

    amount: float
    currency: str


class ProjectReport(_Model):
    id: str
    name: str | None  # None: the project row no longer exists
    version: int
    version_description: str
    published_at: datetime


class CancellationReport(_Model):
    requested_at: datetime
    reason: str


class ExecutionSection(_Model):
    status: ExecutionStatus
    trigger_type: TriggerType
    trigger_id: str | None
    correlation_id: str | None
    engine_version: str
    node_versions: dict[str, str]
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    duration_ms: int | None
    input: dict[str, JsonValue]
    error: ErrorReport | None
    cancellation: CancellationReport | None


class NodeReport(_Model):
    node_id: str
    node_type: str
    label: str
    status: NodeExecutionStatus
    attempts: int
    retries: int
    started_at: datetime | None  # start of the last attempt
    finished_at: datetime | None
    duration_ms: int | None  # of the last attempt
    error: ErrorReport | None  # the failure that ended the node
    failures: list[FailureReport]  # every failed attempt, retries included
    outputs: dict[str, JsonValue] | None
    usage: TokenUsageReport | None
    cost: CostReport | None


class MetricsReport(_Model):
    node_status_counts: dict[str, int]
    attempts: int
    retries: int
    failed_attempts: int
    usage: TokenUsageReport
    # Summed per currency, ordered by currency.
    costs: list[CostReport]
    # False when a node reported token usage without a cost estimate, so
    # ``costs`` understates the run.
    cost_complete: bool


class EventReport(_Model):
    position: int | None
    event_type: str
    occurred_at: datetime
    node_id: str | None
    payload: dict[str, JsonValue]


class ExecutionReport(_Model):
    schema_version: int
    execution_id: str
    project: ProjectReport
    execution: ExecutionSection
    nodes: list[NodeReport]  # ordered by node_id
    metrics: MetricsReport
    events: list[EventReport]  # timeline, ordered by store position
    events_truncated: bool


# -- generation ---------------------------------------------------------------


def build_report(
    execution: Execution,
    *,
    project_name: str | None,
    events: Sequence[Event] = (),
    events_truncated: bool = False,
) -> ExecutionReport:
    """Pure: persisted state in, report out."""
    version = execution.project_version
    graph_nodes = version.graph.nodes
    nodes = [
        _node(ne, graph_nodes[node_id].node_type, graph_nodes[node_id].label)
        for node_id, ne in sorted(execution.node_executions.items())
    ]
    return ExecutionReport(
        schema_version=REPORT_SCHEMA_VERSION,
        execution_id=execution.id,
        project=ProjectReport(
            id=execution.project_id,
            name=project_name,
            version=version.version,
            version_description=redact_text(version.description),
            published_at=version.published_at,
        ),
        execution=ExecutionSection(
            status=execution.status,
            trigger_type=execution.metadata.trigger_type,
            trigger_id=execution.metadata.trigger_id,
            correlation_id=execution.metadata.correlation_id,
            engine_version=execution.metadata.engine_version,
            node_versions=dict(sorted(execution.metadata.node_versions.items())),
            created_at=execution.created_at,
            started_at=execution.started_at,
            finished_at=execution.finished_at,
            duration_ms=_duration_ms(execution.started_at, execution.finished_at),
            input=_redacted_object(execution.input),
            error=_error(execution.error),
            cancellation=None
            if execution.cancellation is None
            else CancellationReport(
                requested_at=execution.cancellation.requested_at,
                reason=redact_text(execution.cancellation.reason),
            ),
        ),
        nodes=nodes,
        metrics=_metrics(nodes),
        events=[
            EventReport(
                position=e.position,
                event_type=e.event_type.value,
                occurred_at=e.occurred_at,
                node_id=e.node_id,
                payload=_redacted_object(e.payload),
            )
            for e in events
        ],
        events_truncated=events_truncated,
    )


def report_json(report: ExecutionReport) -> str:
    """The export format: canonical JSON (sorted keys), byte-stable for the
    same persisted state."""
    return json.dumps(
        report.model_dump(mode="json"),
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
        indent=2,
    )


def _node(ne: NodeExecution, node_type: str, label: str) -> NodeReport:
    usage, cost = usage_and_cost(ne.outputs)
    return NodeReport(
        node_id=ne.node_id,
        node_type=node_type,
        label=redact_text(label),
        status=ne.status,
        attempts=ne.attempt,
        retries=max(ne.attempt - 1, 0),
        started_at=ne.started_at,
        finished_at=ne.finished_at,
        duration_ms=_duration_ms(ne.started_at, ne.finished_at),
        error=_error(ne.error),
        failures=[
            FailureReport(**e.model_dump(), attempt=n)
            for n, f in enumerate(ne.failures, start=1)
            if (e := _error(f)) is not None
        ],
        outputs=None if ne.outputs is None else _redacted_object(ne.outputs),
        usage=usage,
        cost=cost,
    )


def _metrics(nodes: Sequence[NodeReport]) -> MetricsReport:
    tally = MetricsTally()
    for node in nodes:  # node_id order: float sums are reproducible
        tally.add(node.status, node.attempts, len(node.failures), node.usage, node.cost)
    return tally.report()


class MetricsTally:
    """Sums node executions into a ``MetricsReport``. One definition of the
    sums for a single report and for monitoring across executions, so both
    count retries, tokens and cost the same way. Add in a stable order:
    float cost sums depend on it."""

    def __init__(self) -> None:
        self._counts = dict.fromkeys(sorted(s.value for s in NodeExecutionStatus), 0)
        self._costs: dict[str, float] = {}
        self._input_tokens = self._output_tokens = 0
        self._attempts = self._retries = self._failed_attempts = 0
        self._cost_complete = True

    def add(
        self,
        status: NodeExecutionStatus,
        attempts: int,
        failed_attempts: int,
        usage: TokenUsageReport | None,
        cost: CostReport | None,
    ) -> None:
        self._counts[status] += 1
        self._attempts += attempts
        self._retries += max(attempts - 1, 0)
        self._failed_attempts += failed_attempts
        if usage is not None:
            self._input_tokens += usage.input_tokens
            self._output_tokens += usage.output_tokens
            self._cost_complete &= cost is not None
        if cost is not None:
            self._costs[cost.currency] = (
                self._costs.get(cost.currency, 0.0) + cost.amount
            )

    def report(self) -> MetricsReport:
        i, o = self._input_tokens, self._output_tokens
        return MetricsReport(
            node_status_counts=dict(self._counts),
            attempts=self._attempts,
            retries=self._retries,
            failed_attempts=self._failed_attempts,
            usage=TokenUsageReport(input_tokens=i, output_tokens=o, total_tokens=i + o),
            costs=[
                CostReport(amount=a, currency=c) for c, a in sorted(self._costs.items())
            ],
            cost_complete=self._cost_complete,
        )


def usage_and_cost(
    outputs: Mapping[str, Any] | None,
) -> tuple[TokenUsageReport | None, CostReport | None]:
    """Token usage and cost as AI nodes report them: an output ``metadata``
    object with ``usage`` {input_tokens, output_tokens} and optional ``cost``
    {amount, currency} (``core.llm``'s shape). Outputs are node-written, so
    anything malformed counts as "not reported" rather than failing."""
    meta = (outputs or {}).get("metadata")
    if not isinstance(meta, Mapping):
        return None, None
    usage = meta.get("usage")
    if not isinstance(usage, Mapping):
        return None, None
    tokens = usage.get("input_tokens"), usage.get("output_tokens")
    if not all(type(t) is int and t >= 0 for t in tokens):
        return None, None
    i, o = cast("tuple[int, int]", tokens)
    report = TokenUsageReport(input_tokens=i, output_tokens=o, total_tokens=i + o)
    cost = meta.get("cost")
    if not isinstance(cost, Mapping):
        return report, None
    amount, currency = cost.get("amount"), cost.get("currency")
    if (
        not isinstance(amount, int | float)
        or isinstance(amount, bool)
        or not 0 <= amount < float("inf")  # also rejects NaN
        or not isinstance(currency, str)
        or not currency.strip()
    ):
        return report, None
    return report, CostReport(amount=float(amount), currency=currency)


def _error(error: ExecutionError | None) -> ErrorReport | None:
    if error is None:
        return None
    return ErrorReport(
        code=error.code,
        message=redact_text(error.message),
        kind=error.kind.value,
        retryable=error.retryable,
        node_id=error.node_id,
        details={k: str(v) for k, v in redact_mapping(error.details).items()},
    )


def _redacted_object(value: Mapping[str, Any]) -> dict[str, JsonValue]:
    return cast("dict[str, JsonValue]", redact(value))


def _duration_ms(start: datetime | None, end: datetime | None) -> int | None:
    if start is None or end is None:
        return None
    return (end - start) // timedelta(milliseconds=1)


# -- use case -------------------------------------------------------------------


class ReportService:
    """GenerateReport (architecture §20)."""

    def __init__(self, uow: UowFactory) -> None:
        self._uow = uow

    async def execution_report(self, execution_id: str) -> ExecutionReport:
        async with self._uow() as uow:
            # Events first: under READ COMMITTED a concurrent runtime commit
            # may land between the reads, and the timeline must never run
            # ahead of the state it accompanies.
            events, truncated = await _load_events(uow, execution_id)
            execution = await load_execution(uow, execution_id)
            project = await uow.projects.get(execution.project_id)
        return build_report(
            execution,
            project_name=None if project is None else project.name,
            events=events,
            events_truncated=truncated,
        )


async def _load_events(uow: UnitOfWork, execution_id: str) -> tuple[list[Event], bool]:
    events: list[Event] = []
    after = 0
    while len(events) <= MAX_REPORT_EVENTS:
        page = await uow.events.list(
            execution_id=execution_id, after=after, limit=_EVENT_PAGE
        )
        events.extend(page)
        if len(page) < _EVENT_PAGE:
            return events[:MAX_REPORT_EVENTS], len(events) > MAX_REPORT_EVENTS
        after = cast("int", page[-1].position)
    return events[:MAX_REPORT_EVENTS], True
