"""Engine metrics in Prometheus exposition format (architecture §113).

Each ``EngineMetrics`` owns its own registry — no process-global state, so
tests and several containers never share counters.

Label values are bounded by construction (AGENTS §41): statuses and error
kinds are enums, ``trigger_type`` is an enum, ``node_type`` is bounded by
the installed node registry, ``metric`` by the names each node type declares
in its definition (``NodeDefinition.metrics``). Never label with ids, error
codes, messages or
anything a workflow author types.
"""

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

CONTENT_TYPE = CONTENT_TYPE_LATEST

# Seconds; executions range from milliseconds to the one-hour default limit.
_BUCKETS = (0.005, 0.025, 0.1, 0.5, 1, 2.5, 5, 10, 30, 60, 300, 900, 3600)


class EngineMetrics:
    def __init__(self) -> None:
        self.registry = registry = CollectorRegistry()
        self.executions_created = Counter(
            "akasha_executions_created",
            "Executions accepted (queued).",
            ["trigger_type"],
            registry=registry,
        )
        self.executions_started = Counter(
            "akasha_executions_started",
            "Executions that started running.",
            registry=registry,
        )
        self.executions_finished = Counter(
            "akasha_executions_finished",
            "Executions that reached a terminal state.",
            ["status"],
            registry=registry,
        )
        self.execution_duration = Histogram(
            "akasha_execution_duration_seconds",
            "Execution run time, start to terminal state.",
            ["status"],
            buckets=_BUCKETS,
            registry=registry,
        )
        self.execution_queue_wait = Histogram(
            "akasha_execution_queue_wait_seconds",
            "Time from creation until the execution started.",
            buckets=_BUCKETS,
            registry=registry,
        )
        self.active_executions = Gauge(
            "akasha_active_executions",
            "Executions currently run by this process.",
            registry=registry,
        )
        self.node_executions_finished = Counter(
            "akasha_node_executions_finished",
            "Node executions that reached a terminal state.",
            ["node_type", "status"],
            registry=registry,
        )
        self.node_execution_duration = Histogram(
            "akasha_node_execution_duration_seconds",
            "Node attempt run time, start to terminal state.",
            ["node_type", "status"],
            buckets=_BUCKETS,
            registry=registry,
        )
        self.node_execution_errors = Counter(
            "akasha_node_execution_errors",
            "Failed node executions by error classification.",
            ["node_type", "error_kind"],
            registry=registry,
        )
        self.node_metric = Counter(
            "akasha_node_metric",
            "Counters incremented by node code (context.metrics).",
            ["node_type", "metric"],
            registry=registry,
        )

    def render(self) -> bytes:
        return generate_latest(self.registry)
