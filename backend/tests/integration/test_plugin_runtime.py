"""The sample plugin inside the real engine (roadmap Phase 17 acceptance):

    install → register → publish (plugin + node dependencies) → execute
    → disable (publish and runs fail, structured) → enable → remove

Plugin nodes reach WorkflowService and ExecutionRuntime through the ordinary
NodeRegistry views; neither knows the node came from a plugin.
"""

from pathlib import Path

import pytest

from akasha.application.executions import ExecutionService
from akasha.application.workflows import (
    UowFactory,
    WorkflowService,
    WorkflowValidationFailedError,
)
from akasha.domain.execution import ExecutionStatus
from akasha.domain.graph import Edge, Graph, NodeInstance
from akasha.domain.graph_validation import ValidationCode
from akasha.domain.workflow import Dependency, WorkflowDraft
from akasha.nodes.builtins import create_builtin_registry
from akasha.plugins.registry import PluginRegistry
from akasha.runtime.runtime import ExecutionRuntime
from tests.test_plugins import SAMPLE

pytestmark = pytest.mark.usefixtures("clean_db")


async def test_sample_plugin_lifecycle_through_the_engine(
    uow: UowFactory, tmp_path: Path
) -> None:
    nodes = create_builtin_registry()
    plugins = PluginRegistry(tmp_path, nodes)
    plugins.install(SAMPLE)

    graph = Graph()
    source = NodeInstance.create("core.input", config={"field": "title"}, label="input")
    slug = NodeInstance.create("text_tools.slugify", config={}, label="slug")
    graph.add_node(source)
    graph.add_node(slug)
    graph.add_edge(Edge(source.id, "value", slug.id, "text"))
    draft = WorkflowDraft(
        graph=graph,
        dependencies=[
            Dependency("plugin", "text_tools", ">=1.0.0,<2.0.0"),
            Dependency("node", "text_tools.slugify", ">=1.0.0"),
        ],
    )

    workflows = WorkflowService(uow, nodes.definitions, plugins=plugins.versions)
    workflow = await workflows.create_workflow("slugs")
    await workflows.update_draft(
        workflow.id, expected_revision=workflow.revision, name="slugs", draft=draft
    )
    version = await workflows.publish_workflow(workflow.id)

    executions = ExecutionService(uow)
    # As bootstrap wires it: published versions pin plugin versions.
    runtime = ExecutionRuntime(uow, nodes.catalog, plugins=plugins.versions)

    async def run() -> tuple[ExecutionStatus, object]:
        queued = await executions.create_execution(
            workflow.id,
            version.version,
            input={"title": "Plugins, Without Core Changes!"},
        )
        done = await runtime.run(queued.id)
        return done.status, done.node(slug.id).outputs

    assert await run() == (
        ExecutionStatus.COMPLETED,
        {"slug": "plugins-without-core-changes"},
    )

    plugins.disable("text_tools")
    with pytest.raises(WorkflowValidationFailedError) as info:
        await workflows.publish_workflow(workflow.id)
    codes = {i.code for i in info.value.result.issues}
    assert {
        ValidationCode.UNKNOWN_NODE_TYPE,
        ValidationCode.MISSING_DEPENDENCY,
    } <= codes
    status, outputs = await run()  # the published version no longer resolves
    assert (status, outputs) == (ExecutionStatus.FAILED, None)

    plugins.enable("text_tools")
    assert (await run())[0] is ExecutionStatus.COMPLETED

    plugins.remove("text_tools")
    assert "text_tools.slugify" not in nodes.definitions
    assert (await run())[0] is ExecutionStatus.FAILED
