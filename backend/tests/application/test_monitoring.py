"""Monitoring aggregation (frontend Phase 16): pure, no database."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from akasha.application.monitoring import MAX_BUCKETS, bucket_size, summarize
from akasha.application.persistence import ExecutionSample, NodeSample
from akasha.domain.execution import ExecutionStatus, NodeExecutionStatus
from akasha.errors import DomainValidationError

T0 = datetime(2026, 10, 1, tzinfo=UTC)
HOUR = timedelta(hours=1)


def run(
    status: ExecutionStatus,
    minute: int = 0,
    duration_ms: int | None = None,
    code: str | None = None,
) -> ExecutionSample:
    created = T0 + timedelta(minutes=minute)
    return ExecutionSample(
        status=status,
        created_at=created,
        started_at=None if duration_ms is None else created,
        finished_at=None
        if duration_ms is None
        else created + timedelta(milliseconds=duration_ms),
        error_code=code,
        error_kind=None if code is None else "permanent",
    )


def node(
    node_type: str,
    status: NodeExecutionStatus = NodeExecutionStatus.COMPLETED,
    attempts: int = 1,
    duration_ms: int | None = 10,
    metadata: Any = None,
) -> NodeSample:
    return NodeSample(
        node_type=node_type,
        status=status,
        attempts=attempts,
        # Every attempt but a successful last one failed.
        failed_attempts=attempts
        - (status is not NodeExecutionStatus.FAILED and attempts > 0),
        started_at=None if duration_ms is None else T0,
        finished_at=None
        if duration_ms is None
        else T0 + timedelta(milliseconds=duration_ms),
        metadata=metadata,
    )


def llm(tokens: tuple[int, int], cost: float | None) -> dict[str, Any]:
    usage = {"input_tokens": tokens[0], "output_tokens": tokens[1]}
    meta: dict[str, Any] = {"usage": usage}
    if cost is not None:
        meta["cost"] = {"amount": cost, "currency": "USD"}
    return meta


def test_empty_window_is_all_zero_not_missing() -> None:
    s = summarize(T0, T0 + HOUR, [], [])
    assert s.executions == 0
    assert set(s.status_counts.values()) == {0}
    assert len(s.status_counts) == len(ExecutionStatus)
    assert s.durations == {} and s.errors == [] and s.nodes == []
    assert s.errors_total == 0
    assert len(s.timeline) == 60 and s.bucket_seconds == 60
    assert s.totals.usage.total_tokens == 0 and s.totals.costs == []
    assert s.totals.cost_complete


def test_counts_durations_per_terminal_status_and_timeline() -> None:
    s = summarize(
        T0,
        T0 + HOUR,
        [
            run(ExecutionStatus.COMPLETED, 0, 100),
            run(ExecutionStatus.COMPLETED, 0, 300),
            run(ExecutionStatus.COMPLETED, 5, 200),
            run(ExecutionStatus.FAILED, 5, 5_000, code="HTTP_ERROR"),
            run(ExecutionStatus.RUNNING, 59),  # unfinished: no duration
        ],
        [],
    )
    assert s.executions == 5
    assert s.status_counts[ExecutionStatus.COMPLETED] == 3
    assert s.status_counts[ExecutionStatus.RUNNING] == 1
    done = s.durations[ExecutionStatus.COMPLETED]
    assert (done.count, done.mean_ms, done.p50_ms, done.max_ms) == (3, 200, 200, 300)
    # Never mixed: the failed run's 5 s does not move the completed stats.
    assert s.durations[ExecutionStatus.FAILED].max_ms == 5_000
    assert ExecutionStatus.RUNNING not in s.durations
    assert s.timeline[0].counts[ExecutionStatus.COMPLETED] == 2
    assert s.timeline[5].counts[ExecutionStatus.FAILED] == 1
    assert s.timeline[59].counts[ExecutionStatus.RUNNING] == 1
    assert s.timeline[59].end == T0 + HOUR


def test_percentiles_are_observed_values() -> None:
    runs = [run(ExecutionStatus.COMPLETED, 0, ms) for ms in range(1, 101)]
    stats = summarize(T0, T0 + HOUR, runs, []).durations[ExecutionStatus.COMPLETED]
    assert (stats.p50_ms, stats.p95_ms, stats.max_ms) == (50, 95, 100)
    single = summarize(T0, T0 + HOUR, [run(ExecutionStatus.TIMEOUT, 0, 7)], [])
    assert single.durations[ExecutionStatus.TIMEOUT].p95_ms == 7


def test_errors_ranked_by_frequency_then_code_and_capped() -> None:
    runs = [run(ExecutionStatus.FAILED, 0, 1, code=f"E_{i:02}") for i in range(12)]
    runs += [run(ExecutionStatus.TIMEOUT, 0, 1, code="E_99")] * 2
    # A cancelled run keeps no error code in the trend, even with one set.
    runs.append(run(ExecutionStatus.CANCELLED, 0, 1, code="E_CANCEL"))
    s = summarize(T0, T0 + HOUR, runs, [])
    assert [e.code for e in s.errors][:2] == ["E_99", "E_00"]
    assert s.errors[0].count == 2
    assert len(s.errors) == 10
    assert s.errors_total == 14


def test_node_types_sum_retries_tokens_and_cost_like_the_report() -> None:
    s = summarize(
        T0,
        T0 + HOUR,
        [],
        [
            node("core.llm", attempts=3, metadata=llm((10, 5), 0.25)),
            node("core.llm", metadata=llm((1, 1), None)),  # usage without cost
            node("core.http", NodeExecutionStatus.FAILED, attempts=2),
            node(
                "core.http", NodeExecutionStatus.SKIPPED, attempts=0, duration_ms=None
            ),
            node("core.llm", metadata={"usage": "malformed"}),
        ],
    )
    assert [n.node_type for n in s.nodes] == ["core.http", "core.llm"]
    http, llm_type = s.nodes
    assert http.metrics.node_status_counts["failed"] == 1
    assert http.metrics.node_status_counts["skipped"] == 1
    assert (http.metrics.attempts, http.metrics.retries) == (2, 1)
    assert http.metrics.failed_attempts == 2
    assert http.durations is not None and http.durations.count == 1
    assert llm_type.metrics.retries == 2
    assert llm_type.metrics.usage.total_tokens == 17
    assert [(c.amount, c.currency) for c in llm_type.metrics.costs] == [(0.25, "USD")]
    assert not llm_type.metrics.cost_complete
    assert s.totals.retries == 3
    assert s.totals.usage.input_tokens == 11
    assert not s.totals.cost_complete


def test_large_window_stays_bounded() -> None:
    since = T0 - timedelta(days=31)
    runs = [run(ExecutionStatus.COMPLETED, -i, 50) for i in range(1, 31 * 24 * 60, 7)]
    s = summarize(since, T0, runs, [])
    assert len(s.timeline) <= MAX_BUCKETS
    assert sum(b.counts[ExecutionStatus.COMPLETED] for b in s.timeline) == len(runs)


@pytest.mark.parametrize(
    ("span", "seconds", "buckets"),
    [
        (HOUR, 60, 60),
        (24 * HOUR, 900, 96),
        (7 * 24 * HOUR, 6 * 3600, 28),
        (timedelta(days=31), 86400, 31),
        (timedelta(minutes=1), 60, 1),
    ],
)
def test_bucket_sizes(span: timedelta, seconds: int, buckets: int) -> None:
    assert bucket_size(T0, T0 + span) == timedelta(seconds=seconds)
    assert len(summarize(T0, T0 + span, [], []).timeline) == buckets


def test_partial_last_bucket_ends_at_until() -> None:
    s = summarize(T0, T0 + timedelta(minutes=90, seconds=30), [], [])
    assert s.timeline[-1].end - s.timeline[-1].start == timedelta(seconds=30)


@pytest.mark.parametrize(
    ("since", "until"),
    [
        (T0, T0),
        (T0 + HOUR, T0),
        (T0, T0 + timedelta(days=31, seconds=1)),
        (T0.replace(tzinfo=None), T0 + HOUR),
    ],
)
def test_invalid_windows_are_rejected(since: datetime, until: datetime) -> None:
    with pytest.raises(DomainValidationError):
        summarize(since, until, [], [])


def test_sample_outside_the_window_is_a_bug_not_a_wrapped_index() -> None:
    with pytest.raises(ValueError, match="outside"):
        summarize(T0, T0 + HOUR, [run(ExecutionStatus.COMPLETED, -1)], [])
