"""Execution observability: one structured log line and the metrics per
committed lifecycle event.

Called right after an execution save committed, with the events that save
persisted and the aggregate that produced them. Events say *what* happened;
the aggregate supplies the ids and timestamps for timings. Deriving both
logs and metrics from the evented transitions means every state change —
including the terminal cascade that cancels unfinished nodes — is observed
exactly once, without instrumentation scattered through the runtime.

Durations come from the domain timestamps (the runtime's injectable clock),
not from wall-clock measurements here.
"""

import logging
from collections.abc import Sequence
from datetime import datetime

from akasha.domain.event import Event, EventType
from akasha.domain.execution import Execution, ExecutionError
from akasha.observability.metrics import EngineMetrics

logger = logging.getLogger("akasha.execution")

_TERMINAL = {
    EventType.EXECUTION_COMPLETED,
    EventType.EXECUTION_FAILED,
    EventType.EXECUTION_CANCELLED,
    EventType.EXECUTION_TIMED_OUT,
}
_WARN = {
    EventType.EXECUTION_FAILED,
    EventType.EXECUTION_TIMED_OUT,
    EventType.EXECUTION_RECOVERED,
    EventType.NODE_FAILED,
    EventType.NODE_RETRY_SCHEDULED,
}
# Node events that do not end the node execution.
_NOT_FINISHED = {
    EventType.NODE_STARTED,
    EventType.NODE_EMITTED,
    EventType.NODE_RETRY_SCHEDULED,
}


def _seconds(start: datetime | None, end: datetime) -> float | None:
    return None if start is None else (end - start).total_seconds()


def _error_fields(error: ExecutionError | None) -> dict[str, object]:
    if error is None:
        return {}
    # The message is for operators; it was redacted when recorded
    # (``execute_node``) and the log formatter redacts again.
    return {
        "error_code": error.code,
        "error_kind": error.kind.value,
        "error_message": error.message,
        "error_node_id": error.node_id,
    }


def observe_execution(
    execution: Execution, events: Sequence[Event], metrics: EngineMetrics
) -> None:
    for event in events:
        kind = event.event_type
        # "execution.timed_out" -> "timed_out": a closed set, safe as a label.
        status = kind.value.split(".", 1)[1]
        fields: dict[str, object] = {
            "event": kind.value,
            "event_id": event.event_id,
            "execution_id": execution.id,
            "project_id": execution.project_id,
            "project_version": execution.project_version.version,
            "correlation_id": execution.metadata.correlation_id,
            **event.payload,
        }
        seconds: float | None = None
        if event.node_id is not None:
            ne = execution.node(event.node_id)
            node_type = execution.project_version.graph.nodes[event.node_id].node_type
            fields |= {
                "node_id": event.node_id,
                "node_execution_id": ne.id,
                "node_type": node_type,
            }
            if kind in (EventType.NODE_FAILED, EventType.NODE_RETRY_SCHEDULED):
                fields |= _error_fields(ne.failures[-1] if ne.failures else None)
                counter = (
                    metrics.node_execution_errors
                    if kind is EventType.NODE_FAILED
                    else metrics.node_retries
                )
                counter.labels(node_type, str(event.payload["error_kind"])).inc()
            if kind not in _NOT_FINISHED:
                seconds = _seconds(ne.started_at, event.occurred_at)
                metrics.node_executions_finished.labels(node_type, status).inc()
                if seconds is not None:
                    metrics.node_execution_duration.labels(node_type, status).observe(
                        seconds
                    )
        elif kind is EventType.EXECUTION_CREATED:
            metrics.executions_created.labels(execution.metadata.trigger_type).inc()
        elif kind is EventType.EXECUTION_STARTED:
            wait = _seconds(execution.created_at, event.occurred_at)
            if wait is not None:
                fields["queue_wait_ms"] = round(wait * 1000)
                metrics.execution_queue_wait.observe(wait)
            metrics.executions_started.inc()
        elif kind is EventType.EXECUTION_RECOVERED:
            metrics.executions_recovered.inc()
        elif kind in _TERMINAL:
            fields |= _error_fields(execution.error)
            seconds = _seconds(execution.started_at, event.occurred_at)
            metrics.executions_finished.labels(status).inc()
            if seconds is not None:
                metrics.execution_duration.labels(status).observe(seconds)
        if seconds is not None:
            fields["duration_ms"] = round(seconds * 1000)
        level = logging.WARNING if kind in _WARN else logging.INFO
        logger.log(level, kind.value, extra=fields)
