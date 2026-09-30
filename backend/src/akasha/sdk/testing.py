"""Test helpers for custom Nodes: run one node the way the engine does.

``run_node`` applies the engine's checks in the engine's order, so a node
that passes here behaves the same inside a published Project:

1. contract (``check_node_contract``) — as at registration;
2. config: no plaintext secrets, then the config schema — as at publish;
3. inputs against the declared ports — as by the runtime before ``execute``;
4. ``execute`` with a fresh ``NodeExecutionContext``;
5. outputs against the declared ports — as by the runtime after ``execute``.

A failing check raises the same SDK error the engine would report
(``NodeContractError``, ``NodeConfigurationError``, ``NodeValidationError``);
a ``NodeError`` from ``execute`` propagates unchanged. Credentials are
resolved with the engine's own access rule: only references that appear in
``config`` resolve.

Not reproduced here: timeouts, cancellation by the runtime and secret
redaction of persisted outputs — those are engine behaviour, covered by the
engine's tests.
"""

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from akasha.application.credentials import CredentialNotFoundError
from akasha.domain.credentials import Secret, plaintext_secret_paths
from akasha.domain.node import (
    Node,
    NodeEvents,
    NodeExecutionContext,
    NodeMetrics,
    NodeResult,
    check_node_contract,
)
from akasha.domain.primitives import new_id
from akasha.errors import NodeConfigurationError, NodeValidationError
from akasha.runtime.credentials import NodeCredentials


@dataclass(frozen=True)
class NodeRun:
    """What one ``run_node`` call produced."""

    outputs: Mapping[str, Any]
    events: list[str]  # names passed to context.events.emit, in order
    metrics: dict[str, float]  # totals per context.metrics name


class _Secrets:
    def __init__(self, values: Mapping[str, str]) -> None:
        self._values = values

    async def resolve(self, reference: str) -> Secret:
        if reference not in self._values:
            raise CredentialNotFoundError(
                "Credential not found.", details={"name": reference}
            )
        return Secret(self._values[reference])


async def run_node(
    node: Node,
    *,
    config: Mapping[str, Any] | None = None,
    inputs: Mapping[str, Any] | None = None,
    execution_input: Mapping[str, Any] | None = None,
    credentials: Mapping[str, str] | None = None,
    is_cancelled: Callable[[], bool] = lambda: False,
) -> NodeRun:
    """Run ``node`` once. ``credentials`` maps credential references (e.g.
    ``"credential:api-key"``) to secret values; ``None`` means the engine has
    no credential store, so ``context.resolve_credential`` fails."""
    check_node_contract(type(node))
    definition = node.definition
    config = dict(config or {})
    if leaks := plaintext_secret_paths(config):
        raise NodeConfigurationError(
            "configuration contains a secret value; use a credential reference",
            details={"paths": ",".join(f"config{p}" for p in leaks)},
        )
    definition.validate_config(config)
    inputs = dict(inputs or {})
    definition.validate_inputs(inputs)
    metrics, events = NodeMetrics(definition.metrics), NodeEvents(definition.events)
    context = NodeExecutionContext(
        execution_id=new_id("exec"),
        node_execution_id=new_id("nexec"),
        node_instance_id=new_id("node"),
        project_id=new_id("proj"),
        project_version="1",
        config=config,
        inputs=inputs,
        logger=logging.getLogger(f"akasha.nodes.{definition.node_id}"),
        credentials=None
        if credentials is None
        else NodeCredentials(_Secrets(credentials), config),
        is_cancelled=is_cancelled,
        runtime_metadata={"attempt": 1},
        execution_input=dict(execution_input or {}),
        metrics=metrics,
        events=events,
    )
    result = await node.execute(context)
    if not isinstance(result, NodeResult) or not isinstance(result.outputs, Mapping):
        raise NodeValidationError("node did not return a NodeResult")
    definition.validate_outputs(result.outputs)
    return NodeRun(dict(result.outputs), list(events.emitted), dict(metrics.totals))


__all__ = ["NodeRun", "run_node"]
