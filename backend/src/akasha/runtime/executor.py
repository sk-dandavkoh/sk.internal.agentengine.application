"""NodeExecutor: run one node attempt and classify the outcome.

Never raises for node behaviour — every way a node can go wrong becomes a
structured ``ExecutionError`` the runtime records. Only ``CancelledError``
(the runtime stopping the node) propagates, so cancellation is never
mistaken for a node failure.
"""

import asyncio
import logging
import traceback
from collections.abc import Mapping
from datetime import timedelta

from akasha.domain.execution import ExecutionError
from akasha.domain.node import (
    NodeError,
    NodeErrorKind,
    NodeExecutionContext,
    NodeFactory,
    NodeResult,
)
from akasha.errors import NodeValidationError

logger = logging.getLogger("akasha.runtime.executor")

NodeOutcome = NodeResult | ExecutionError


def _invalid(code: str, message: str, node_id: str) -> ExecutionError:
    return ExecutionError(
        code=code, message=message, kind=NodeErrorKind.VALIDATION, node_id=node_id
    )


async def execute_node(
    node_cls: NodeFactory,
    context: NodeExecutionContext,
    *,
    limit: timedelta | None,
) -> NodeOutcome:
    node_id = context.node_instance_id
    definition = node_cls.definition
    try:
        definition.validate_inputs(context.inputs)
    except NodeValidationError as exc:
        return _invalid("NODE_INPUT_INVALID", exc.message, node_id)

    scope = asyncio.timeout(None if limit is None else limit.total_seconds())
    try:
        async with scope:
            result = await node_cls().execute(context)
    except NodeError as exc:
        return ExecutionError.from_node_error(exc, node_id=node_id)
    except TimeoutError:
        if limit is not None and scope.expired():
            return ExecutionError.node_timeout(node_id=node_id, timeout=limit)
        # The node's own TimeoutError (e.g. an inner I/O timeout).
        return ExecutionError(
            code="NODE_TIMEOUT",
            message="node reported a timeout",
            kind=NodeErrorKind.TIMEOUT,
            node_id=node_id,
        )
    except Exception as exc:
        # Trusted but possibly buggy node code must not take the runtime
        # down. The exception *message* may carry secrets (redaction is
        # Phase 13), so only the type is persisted/logged; the frame list
        # keeps it diagnosable server-side.
        logger.error(
            "node_unexpected_error",
            extra={
                "event": "node_unexpected_error",
                "execution_id": context.execution_id,
                "node_execution_id": context.node_execution_id,
                "node_id": node_id,
                "error_type": type(exc).__name__,
                "traceback": "".join(traceback.format_tb(exc.__traceback__)),
            },
        )
        return ExecutionError(
            code="NODE_UNEXPECTED_ERROR",
            message=f"node raised an unexpected {type(exc).__name__}",
            kind=NodeErrorKind.PERMANENT,
            node_id=node_id,
        )
    if limit is not None and scope.expired():
        # The node swallowed the timeout cancellation and returned anyway;
        # the limit was still exceeded.
        return ExecutionError.node_timeout(node_id=node_id, timeout=limit)

    if not isinstance(result, NodeResult) or not isinstance(result.outputs, Mapping):
        return _invalid(
            "NODE_OUTPUT_INVALID", "node did not return a NodeResult", node_id
        )
    try:
        definition.validate_outputs(result.outputs)
    except NodeValidationError as exc:
        return _invalid("NODE_OUTPUT_INVALID", exc.message, node_id)
    return result
