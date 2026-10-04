"""Windowed execution monitoring (frontend Phase 16, architecture §113).

A read-only aggregate over *persisted* state, like a report: the executions
created in ``[since, until)`` and their node executions. Prometheus
(``/metrics``) stays the per-process scrape target; this answers "how did
my runs do in this window" from the database, which every process shares,
so it never mixes per-process counters.

Semantics the UI must not reinterpret:
- one population: executions *created* in the window, whatever their
  status now or when they finished;
- durations are of finished executions only, per terminal status (mixing
  cancelled and completed runs would be meaningless); node durations are of
  the last attempt, as in the report;
- tokens, cost and retries are summed by ``MetricsTally``, the report's own
  rules, so a run counts the same here and in its report.
"""

import math
from collections import Counter
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Final

from pydantic import BaseModel, ConfigDict

from akasha.application.persistence import ExecutionSample, NodeSample
from akasha.application.projects import UowFactory
from akasha.application.reports import MetricsReport, MetricsTally, usage_and_cost
from akasha.domain.execution import (
    TERMINAL_EXECUTION_STATUSES,
    ExecutionStatus,
)
from akasha.errors import DomainValidationError

MAX_WINDOW: Final = timedelta(days=31)
# Bucket sizes on the timeline: the smallest that keeps it at MAX_BUCKETS or
# fewer. Fixed steps keep buckets on readable boundaries.
MAX_BUCKETS: Final = 100
_BUCKET_SIZES: Final = tuple(
    timedelta(seconds=s) for s in (60, 300, 900, 3600, 6 * 3600, 86400)
)
TOP_ERRORS: Final = 10


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class DurationStats(_Model):
    """Over ``count`` samples. Percentiles are nearest-rank: always a
    duration that was actually observed."""

    count: int
    mean_ms: int
    p50_ms: int
    p95_ms: int
    max_ms: int


class TimelineBucket(_Model):
    """Executions created in ``[start, end)``. The last bucket ends at
    ``until`` and may be shorter than ``bucket_seconds``."""

    start: datetime
    end: datetime
    counts: dict[ExecutionStatus, int]


class ErrorCount(_Model):
    code: str
    kind: str
    count: int


class NodeTypeStats(_Model):
    node_type: str
    metrics: MetricsReport
    durations: DurationStats | None  # of the last attempt; None: none finished


class MonitoringSummary(_Model):
    since: datetime
    until: datetime
    bucket_seconds: int
    executions: int
    status_counts: dict[ExecutionStatus, int]  # every status, zero included
    durations: dict[ExecutionStatus, DurationStats]  # terminal, with samples
    timeline: list[TimelineBucket]
    # Codes of failed/timed-out executions, most frequent first (then by
    # code), at most TOP_ERRORS; ``errors_total`` counts them all.
    errors: list[ErrorCount]
    errors_total: int
    nodes: list[NodeTypeStats]  # by node_type
    totals: MetricsReport  # over every node execution


class MonitoringService:
    def __init__(self, uow: UowFactory) -> None:
        self._uow = uow

    async def summary(
        self, *, since: datetime, until: datetime, owner_id: str | None = None
    ) -> MonitoringSummary:
        """Over the executions created in ``[since, until)``, limited to the
        projects of ``owner_id`` when given. Both reads share one
        transaction, so node counts belong to the executions counted."""
        check_window(since, until)
        # ponytail: aggregates in Python over every row of the window (at
        # most 31 days); move to SQL GROUP BY / rollups when a window holds
        # more rows than one request should load.
        async with self._uow() as uow:
            runs = await uow.executions.sample_executions(
                since=since, until=until, owner_id=owner_id
            )
            nodes = await uow.executions.sample_node_executions(
                since=since, until=until, owner_id=owner_id
            )
        return summarize(since, until, runs, nodes)


def check_window(since: datetime, until: datetime) -> None:
    if since.tzinfo is None or until.tzinfo is None:
        raise DomainValidationError("since and until need a timezone")
    if until <= since:
        raise DomainValidationError("until must be after since")
    if until - since > MAX_WINDOW:
        raise DomainValidationError(
            f"the window may span at most {MAX_WINDOW.days} days"
        )


def bucket_size(since: datetime, until: datetime) -> timedelta:
    span = until - since
    for size in _BUCKET_SIZES:
        if math.ceil(span / size) <= MAX_BUCKETS:
            return size
    return _BUCKET_SIZES[-1]  # unreachable within MAX_WINDOW


def summarize(
    since: datetime,
    until: datetime,
    executions: Sequence[ExecutionSample],
    nodes: Sequence[NodeSample],
) -> MonitoringSummary:
    """Pure. ``nodes`` in a stable order (float cost sums depend on it)."""
    check_window(since, until)
    size = bucket_size(since, until)
    starts = [since + i * size for i in range(math.ceil((until - since) / size))]
    timeline = [{status: 0 for status in ExecutionStatus} for _ in starts]
    durations: dict[ExecutionStatus, list[int]] = {}
    errors: Counter[tuple[str, str]] = Counter()
    for e in executions:
        if not since <= e.created_at < until:
            raise ValueError("execution sample outside the window")
        timeline[(e.created_at - since) // size][e.status] += 1
        if (
            e.status in TERMINAL_EXECUTION_STATUSES
            and (ms := _ms(e.started_at, e.finished_at)) is not None
        ):
            durations.setdefault(e.status, []).append(ms)
        if e.error_code is not None and e.status in (
            ExecutionStatus.FAILED,
            ExecutionStatus.TIMEOUT,
        ):
            errors[(e.error_code, e.error_kind or "unknown")] += 1

    totals = MetricsTally()
    per_type: dict[str, tuple[MetricsTally, list[int]]] = {}
    for n in nodes:
        usage, cost = usage_and_cost({"metadata": n.metadata})
        tally, node_ms = per_type.setdefault(n.node_type, (MetricsTally(), []))
        for t in (tally, totals):
            t.add(n.status, n.attempts, n.failed_attempts, usage, cost)
        if (ms := _ms(n.started_at, n.finished_at)) is not None:
            node_ms.append(ms)

    ranked = sorted(errors.items(), key=lambda item: (-item[1], item[0]))
    return MonitoringSummary(
        since=since,
        until=until,
        bucket_seconds=int(size.total_seconds()),
        executions=len(executions),
        status_counts={
            status: sum(bucket[status] for bucket in timeline)
            for status in ExecutionStatus
        },
        durations={
            status: _stats(values) for status, values in sorted(durations.items())
        },
        timeline=[
            TimelineBucket(start=start, end=min(start + size, until), counts=counts)
            for start, counts in zip(starts, timeline, strict=True)
        ],
        errors=[
            ErrorCount(code=code, kind=kind, count=count)
            for (code, kind), count in ranked[:TOP_ERRORS]
        ],
        errors_total=sum(errors.values()),
        nodes=[
            NodeTypeStats(
                node_type=node_type,
                metrics=tally.report(),
                durations=_stats(node_ms) if node_ms else None,
            )
            for node_type, (tally, node_ms) in sorted(per_type.items())
        ],
        totals=totals.report(),
    )


def _ms(start: datetime | None, end: datetime | None) -> int | None:
    if start is None or end is None:
        return None
    return max((end - start) // timedelta(milliseconds=1), 0)


def _stats(values: list[int]) -> DurationStats:
    ordered = sorted(values)

    def rank(p: float) -> int:
        return ordered[max(math.ceil(p * len(ordered)) - 1, 0)]

    return DurationStats(
        count=len(ordered),
        mean_ms=round(sum(ordered) / len(ordered)),
        p50_ms=rank(0.5),
        p95_ms=rank(0.95),
        max_ms=ordered[-1],
    )
