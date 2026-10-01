"""Engine resource limits (roadmap Phase 24, architecture §75).

One value object holds every engine-wide cap that the application and the
runtime enforce. The operator sets it (``Settings.resource_limits``), so
nothing below it hard-codes a number. Where each cap is enforced:

- Admission (``ExecutionService.create_execution``): execution and node
  duration, attempts (retries), graph size and depth, and active executions
  per project. A request over a cap is rejected before anything is queued.
  Policies a caller leaves out default to the cap, so every persisted
  execution carries finite, explicit limits. A resumed run therefore keeps
  the limits it was admitted with, even if the operator changed them since.
- Runtime (``ExecutionRuntime``): node output size, total output size per
  execution, and nodes in flight per execution. Breaking an output cap
  fails the node with ``NODE_OUTPUT_TOO_LARGE`` or
  ``EXECUTION_OUTPUT_TOO_LARGE`` (PERMANENT, so it is never retried).

The HTTP response size and the LLM output-token caps live with the code
that reads the response (``core.http``, ``AIService``). Both are configured
from the same settings section.
"""

import json
from dataclasses import dataclass, replace
from datetime import timedelta
from enum import StrEnum, auto
from typing import Any

from akasha.domain.execution import ExecutionError, RetryPolicy, TimeoutPolicy
from akasha.domain.graph import Graph
from akasha.domain.node import NodeErrorKind
from akasha.errors import AkashaError, DomainValidationError


class Limit(StrEnum):
    """Which cap was hit. Also the metric label, so the set stays bounded."""

    EXECUTION_DURATION = auto()
    NODE_DURATION = auto()
    NODE_ATTEMPTS = auto()
    GRAPH_NODES = auto()
    GRAPH_DEPTH = auto()
    ACTIVE_EXECUTIONS = auto()
    NODE_OUTPUT = auto()
    EXECUTION_OUTPUT = auto()


class ResourceLimitError(AkashaError):
    """A request asks for more than the engine allows; retrying the same
    request cannot succeed."""

    code = "RESOURCE_LIMIT_EXCEEDED"
    http_status = 422

    def __init__(self, limit: Limit, *, maximum: object, requested: object) -> None:
        super().__init__(
            f"{limit} exceeds the engine limit of {maximum}",
            details={"limit": limit.value, "maximum": maximum, "requested": requested},
        )
        self.limit = limit


class ExecutionCapacityError(ResourceLimitError):
    """The project has too many unfinished executions. Retry once some of
    them finish."""

    code = "EXECUTION_CAPACITY_EXCEEDED"
    http_status = 429


@dataclass(frozen=True)
class ResourceLimits:
    execution_timeout: timedelta = timedelta(hours=1)
    node_timeout: timedelta = timedelta(minutes=15)
    max_node_attempts: int = 5  # including the first, so at most 4 retries
    max_graph_nodes: int = 500
    max_graph_depth: int = 100  # nodes on the longest dependency chain
    max_active_executions_per_project: int = 100
    max_node_output_bytes: int = 1024 * 1024
    max_execution_output_bytes: int = 16 * 1024 * 1024

    def __post_init__(self) -> None:
        if self.execution_timeout <= timedelta(0) or self.node_timeout <= timedelta(0):
            raise DomainValidationError("timeout limits must be positive")
        if self.node_timeout > self.execution_timeout:
            raise DomainValidationError(
                "node_timeout must not exceed execution_timeout"
            )
        for name in (
            "max_node_attempts",
            "max_graph_nodes",
            "max_graph_depth",
            "max_active_executions_per_project",
            "max_node_output_bytes",
            "max_execution_output_bytes",
        ):
            if getattr(self, name) < 1:
                raise DomainValidationError(f"{name} must be >= 1")
        if self.max_node_output_bytes > self.max_execution_output_bytes:
            raise DomainValidationError(
                "max_node_output_bytes must not exceed max_execution_output_bytes"
            )

    def policies(
        self, timeout: TimeoutPolicy | None, retry: RetryPolicy | None
    ) -> tuple[TimeoutPolicy, RetryPolicy]:
        """The policies an execution is admitted with. A missing value takes
        the cap, and a value above a cap is rejected."""
        timeout = timeout or TimeoutPolicy()
        retry = retry or RetryPolicy()
        execution = _capped(
            Limit.EXECUTION_DURATION, timeout.execution_timeout, self.execution_timeout
        )
        node = _capped(Limit.NODE_DURATION, timeout.node_timeout, self.node_timeout)
        if retry.max_attempts > self.max_node_attempts:
            raise ResourceLimitError(
                Limit.NODE_ATTEMPTS,
                maximum=self.max_node_attempts,
                requested=retry.max_attempts,
            )
        return replace(timeout, execution_timeout=execution, node_timeout=node), retry

    def check_graph(self, graph: Graph) -> None:
        if len(graph.nodes) > self.max_graph_nodes:
            raise ResourceLimitError(
                Limit.GRAPH_NODES,
                maximum=self.max_graph_nodes,
                requested=len(graph.nodes),
            )
        depth = graph_depth(graph)
        if depth > self.max_graph_depth:
            raise ResourceLimitError(
                Limit.GRAPH_DEPTH, maximum=self.max_graph_depth, requested=depth
            )

    def check_capacity(self, active: int) -> None:
        """``active``: the project's unfinished executions, not counting the
        one being admitted."""
        if active >= self.max_active_executions_per_project:
            raise ExecutionCapacityError(
                Limit.ACTIVE_EXECUTIONS,
                maximum=self.max_active_executions_per_project,
                requested=active + 1,
            )

    def check_output(
        self, node_id: str, size: int, total: int
    ) -> tuple[Limit, ExecutionError] | None:
        """``size``: this node's serialized outputs. ``total``: what earlier
        nodes of the execution already produced."""
        if size > self.max_node_output_bytes:
            return Limit.NODE_OUTPUT, _output_error(
                "NODE_OUTPUT_TOO_LARGE", node_id, size, self.max_node_output_bytes
            )
        if total + size > self.max_execution_output_bytes:
            return Limit.EXECUTION_OUTPUT, _output_error(
                "EXECUTION_OUTPUT_TOO_LARGE",
                node_id,
                total + size,
                self.max_execution_output_bytes,
            )
        return None


def _capped(limit: Limit, value: timedelta | None, maximum: timedelta) -> timedelta:
    if value is None:
        return maximum
    if value > maximum:
        raise ResourceLimitError(
            limit,
            maximum=maximum.total_seconds(),
            requested=value.total_seconds(),
        )
    return value


def _output_error(code: str, node_id: str, size: int, maximum: int) -> ExecutionError:
    return ExecutionError(
        code=code,
        message=f"node outputs of {size} bytes exceed the limit of {maximum} bytes",
        kind=NodeErrorKind.PERMANENT,
        node_id=node_id,
        details={"size": str(size), "limit": str(maximum)},
    )


def json_size(value: Any) -> int:
    """Approximate stored size in UTF-8 JSON bytes. Values that JSON cannot
    represent are counted by their ``str()``. Nothing is ever rejected
    because it cannot be measured."""
    text = json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
    return len(text.encode("utf-8", "surrogatepass"))


def graph_depth(graph: Graph) -> int:
    """Nodes on the longest path (Kahn order). Nodes on a cycle are never
    reached, so a cyclic graph reports only the depth of its acyclic part.
    Cycles are rejected by graph validation, not here."""
    indegree = dict.fromkeys(graph.nodes, 0)
    out: dict[str, list[str]] = {n: [] for n in graph.nodes}
    for edge in graph.edges:
        if edge.source_node_id in out and edge.target_node_id in indegree:
            out[edge.source_node_id].append(edge.target_node_id)
            indegree[edge.target_node_id] += 1
    depth = dict.fromkeys(graph.nodes, 1)
    ready = [n for n, d in indegree.items() if d == 0]
    deepest = 0
    while ready:
        node = ready.pop()
        deepest = max(deepest, depth[node])
        for target in out[node]:
            depth[target] = max(depth[target], depth[node] + 1)
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
    return deepest
