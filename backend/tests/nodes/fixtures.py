"""``run_node`` drives a node exactly through the contract boundary the
runtime enforces: config validated (as at publish), inputs validated (as by
the executor), then ``execute``, then outputs validated."""

from collections.abc import Callable, Mapping
from typing import Any

import pytest

from akasha.domain.node import CredentialResolver, Node, NodeResult, NodeServices
from tests.support import RunNode, make_context


@pytest.fixture
def run_node() -> RunNode:
    async def run(
        node: Node,
        *,
        config: Mapping[str, Any] | None = None,
        inputs: Mapping[str, Any] | None = None,
        execution_input: Mapping[str, Any] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
        credentials: CredentialResolver | None = None,
        resume: Mapping[str, Any] | None = None,
        services: NodeServices | None = None,
        settings: Mapping[str, Any] | None = None,
    ) -> NodeResult:
        definition = node.definition
        definition.validate_config(config or {})
        definition.validate_inputs(inputs or {})
        context = make_context(
            node,
            config=config,
            inputs=inputs,
            execution_input=execution_input,
            is_cancelled=is_cancelled,
            credentials=credentials,
            resume=resume,
            services=services,
            settings=settings,
        )
        result = await node.execute(context)
        # A NodeWait is not a result; drive waiting nodes with make_context.
        assert isinstance(result, NodeResult), result
        definition.validate_outputs(result.outputs)
        return result

    return run
