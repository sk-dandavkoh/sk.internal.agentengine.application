"""MCP tools over the workflow, execution and node application services.

Each tool is one application-service call (or one ``WorkflowService.edit_draft``
around a domain ``Graph``/draft method), preceded by the authorization check
the matching REST route gets from its guards. Results reuse the REST DTOs
(``akasha.api.*``) wherever one fits, so MCP and REST describe the same
objects the same way; the few MCP-only shapes below are agent-oriented
summaries that keep results small.

Draft writes always take ``expected_revision`` and return the new revision:
an agent's stale view is a CONCURRENT_MODIFICATION error, never a silent
overwrite.
"""

import base64
import binascii
import hashlib
from collections.abc import Callable, Sequence
from typing import Annotated

from pydantic import BaseModel, Field, JsonValue

from akasha import __version__
from akasha.api.credentials import CredentialPage
from akasha.api.executions import (
    EventOut,
    EventPage,
    ExecutionPage,
    LogLineOut,
    LogPage,
)
from akasha.api.nodes import ConfigIssueOut, ConfigValidationOut, NodeTypeOut
from akasha.api.trigger_schemas import ExecutionDetail
from akasha.api.workflow_schemas import (
    DraftBody,
    GraphBody,
    ImportWorkflowResponse,
    PageInfo,
    TriggerBody,
    ValidationResponse,
    WorkflowDetail,
    WorkflowFileOut,
    WorkflowPage,
    WorkflowSummary,
    WorkflowVersionDetail,
    WorkflowVersionPage,
    _graph_body,
    _triggers,
)
from akasha.application.auth import AuthorizationService, require_admin
from akasha.application.credentials import CredentialService
from akasha.application.executions import ExecutionService
from akasha.application.packages import WorkflowPackageService
from akasha.application.persistence import WorkflowSort
from akasha.application.reports import (
    ErrorReport,
    ExecutionReport,
    MetricsReport,
    NodeReport,
    ReportService,
)
from akasha.application.workflow_package import MAX_PACKAGE_BYTES
from akasha.application.workflows import MAX_PAGE_SIZE, WorkflowService
from akasha.domain.execution import ExecutionStatus, LogLevel
from akasha.domain.graph import Edge, NodeInstance, Position
from akasha.domain.graph_validation import port_compatibility
from akasha.domain.identity import Access
from akasha.domain.trigger import Trigger, TriggerType
from akasha.domain.workflow import Workflow, WorkflowDraft, WorkflowStatus
from akasha.errors import AkashaError, ConfigurationError, DomainValidationError
from akasha.mcp.server import Kit, current_identity
from akasha.observability.logging import current_log_context
from akasha.runtime.runtime import ExecutionRuntime
from akasha.sdk import SDK_VERSION

# -- argument types -----------------------------------------------------------

WorkflowId = Annotated[str, Field(description="Workflow id (wf_...).", max_length=64)]
ExecutionId = Annotated[
    str, Field(description="Execution id (exec_...).", max_length=64)
]
NodeId = Annotated[
    str, Field(description="Node instance id in the graph (node_...).", max_length=64)
]
NodeType = Annotated[
    str, Field(description="Node type id, e.g. core.http.", max_length=200)
]
Revision = Annotated[
    int,
    Field(
        ge=1,
        description="The workflow revision you last read (from workflow_get,"
        " graph_get or the previous write). A newer stored revision rejects"
        " the write with CONCURRENT_MODIFICATION: re-read and retry.",
    ),
]
Offset = Annotated[int, Field(ge=0, description="Items to skip.")]
Limit = Annotated[
    int, Field(ge=1, le=MAX_PAGE_SIZE, description="Items per page (max 100).")
]
Config = Annotated[
    dict[str, JsonValue],
    Field(
        description="Node configuration matching the node type's config_schema"
        " (node_type_get). Secrets only as credential references"
        " 'credential:<name>' (credential_list)."
    ),
]
Name = Annotated[str, Field(min_length=1, max_length=200)]
Description = Annotated[str, Field(max_length=10_000)]
PortName = Annotated[str, Field(min_length=1, max_length=200)]


# -- result models (MCP-only shapes) ------------------------------------------


class CallerOut(BaseModel):
    user_id: str
    username: str
    role: str


class SystemInfo(BaseModel):
    engine_version: str
    sdk_version: str
    ready: bool
    caller: CallerOut
    # Whether started executions run without further action: a queue hands
    # them to workers. False: they stay QUEUED (an operator runs workers).
    executions_run_automatically: bool
    credentials_available: bool
    plugins_available: bool
    # AKASHA_MCP_PLUGIN_CODE: plugin_install/update/test are permitted.
    plugin_code_allowed: bool
    max_graph_nodes: int
    max_response_bytes: int


class WorkflowOverview(WorkflowSummary):
    node_count: int
    edge_count: int
    trigger_count: int


class DraftOut(BaseModel):
    workflow_id: str
    revision: int
    name: str
    description: str
    draft: DraftBody
    files: list[WorkflowFileOut]


class GraphOut(BaseModel):
    workflow_id: str
    revision: int
    graph: GraphBody


class DraftChange(BaseModel):
    """A successful draft write: pass ``revision`` to the next write."""

    workflow_id: str
    revision: int
    node_id: str | None = None
    trigger_id: str | None = None
    removed_edges: int | None = None


class WorkflowValidation(ValidationResponse):
    workflow_id: str
    revision: int


class NodeTypeSummary(BaseModel):
    node_type: str
    name: str
    version: str
    category: str
    description: str
    # The enabled plugin providing it; null for built-in node types.
    plugin_id: str | None


class NodeTypePage(BaseModel):
    items: list[NodeTypeSummary]
    page: PageInfo
    # Output port signature -> input signatures it may connect to: the
    # backend's edge rule (type, '[]' suffix when the port is multiple).
    port_compatibility: dict[str, list[str]]


class NodeTypeDetail(NodeTypeOut):
    plugin_id: str | None


class StartedExecution(ExecutionDetail):
    runs_automatically: bool


class NodeStateOut(BaseModel):
    node_id: str
    node_type: str
    label: str
    status: str
    attempts: int
    error: ErrorReport | None


class ExecutionOverview(ExecutionDetail):
    error: ErrorReport | None
    node_versions: dict[str, str]
    duration_ms: int | None
    nodes: list[NodeStateOut]
    metrics: MetricsReport


class TriggerList(BaseModel):
    workflow_id: str
    revision: int
    # Draft triggers; published versions carry their own copy, which is
    # what actually fires.
    triggers: list[TriggerBody]


class WorkflowPackageOut(BaseModel):
    workflow_id: str
    version: int | None
    filename: str
    size_bytes: int
    sha256: str
    package_base64: str


# -- helpers ------------------------------------------------------------------


def _need[T](service: T | None, what: str) -> T:
    if service is None:
        raise ConfigurationError(f"{what} is not configured on this installation.")
    return service


def _workflow_by(workflow: Workflow) -> DraftChange:
    return DraftChange(workflow_id=workflow.id, revision=workflow.revision)


def _overview(p: Workflow) -> WorkflowOverview:
    return WorkflowOverview(
        **WorkflowSummary.from_domain(p).model_dump(),
        node_count=len(p.draft.graph.nodes),
        edge_count=len(p.draft.graph.edges),
        trigger_count=len(p.draft.triggers),
    )


def _edge(source: str, source_port: str, target: str, target_port: str) -> Edge:
    return Edge(
        source_node_id=source,
        source_port=source_port,
        target_node_id=target,
        target_port=target_port,
    )


def _trigger(draft: WorkflowDraft, trigger_id: str) -> Trigger:
    for trigger in draft.triggers:
        if trigger.id == trigger_id:
            return trigger
    raise DomainValidationError(
        "The draft has no trigger with this id.", details={"trigger_id": trigger_id}
    )


def register(kit: Kit) -> None:
    c = kit.container

    def workflows() -> WorkflowService:
        return _need(c.workflows, "Workflow persistence")

    def executions() -> ExecutionService:
        return _need(c.executions, "Execution persistence")

    def authz() -> AuthorizationService:
        return _need(c.authorization, "Authentication")

    def reports() -> ReportService:
        return _need(c.reports, "Execution persistence")

    # Access levels as on the REST routes (api.auth.requires): reading needs
    # view access, changing edit access, deleting manage access.
    async def workflow(workflow_id: str, need: Access = Access.VIEW) -> None:
        await authz().require_workflow(current_identity(), workflow_id, need)

    async def execution(execution_id: str, need: Access = Access.VIEW) -> None:
        await authz().require_execution(current_identity(), execution_id, need)

    async def sources(workflow_id: str, after: Sequence[Trigger]) -> None:
        """Event-trigger sources the caller adds must be visible to them."""
        before = (await workflows().get_workflow(workflow_id)).draft.triggers
        await authz().require_trigger_sources(current_identity(), before, after)

    async def edit(
        workflow_id: str,
        revision: int,
        change: Callable[[WorkflowDraft], None],
        name: str | None = None,
    ) -> Workflow:
        await workflow(workflow_id, Access.EDIT)
        return await workflows().edit_draft(
            workflow_id, expected_revision=revision, edit=change, name=name
        )

    # -- discovery ----------------------------------------------------------

    @kit.tool(title="System information", read_only=True)
    async def system_info() -> SystemInfo:
        """Who you are and what this installation can do. Call first: tells
        your role (admin tools need 'admin'), whether started executions
        run automatically, and whether plugin code may be installed."""
        identity = current_identity()
        failures = await c.readiness_failures()
        return SystemInfo(
            engine_version=__version__,
            sdk_version=SDK_VERSION,
            ready=not failures,
            caller=CallerOut(
                user_id=identity.user.id,
                username=identity.user.username,
                role=identity.user.role.value,
            ),
            executions_run_automatically=c.queue is not None,
            credentials_available=c.credentials is not None,
            plugins_available=c.plugins is not None,
            plugin_code_allowed=c.settings.mcp_plugin_code,
            max_graph_nodes=c.settings.limit_graph_nodes,
            max_response_bytes=c.settings.mcp_max_response_bytes,
        )

    # -- node discovery -----------------------------------------------------

    @kit.tool(title="List node types", read_only=True)
    async def node_types_list(
        query: Annotated[
            str | None,
            Field(
                max_length=200,
                description="Case-insensitive text matched against type id,"
                " name, description and category.",
            ),
        ] = None,
        category: Annotated[str | None, Field(max_length=100)] = None,
        offset: Offset = 0,
        limit: Limit = 50,
    ) -> NodeTypePage:
        """The canonical node catalog (built-in and enabled plugin node
        types), sorted by id. Use it to find a node type for a task; then
        node_type_get for its ports and config schema. If nothing fits, an
        admin can add one with a plugin (plugin_sdk_get)."""
        plugin_of = c.plugins.node_plugins if c.plugins is not None else {}
        needle = (query or "").lower()
        matches = [
            d
            for d in sorted(c.nodes.list_nodes(), key=lambda d: d.node_id)
            if (category is None or d.metadata.category == category)
            and needle
            in " ".join(
                (
                    d.node_id,
                    d.metadata.name,
                    d.metadata.description,
                    d.metadata.category,
                )
            ).lower()
        ]
        window = matches[offset : offset + limit]
        return NodeTypePage(
            items=[
                NodeTypeSummary(
                    node_type=d.node_id,
                    name=d.metadata.name,
                    version=d.version,
                    category=d.metadata.category,
                    description=d.metadata.description,
                    plugin_id=plugin_of.get(d.node_id),
                )
                for d in window
            ],
            page=PageInfo(
                offset=offset, limit=limit, has_more=offset + limit < len(matches)
            ),
            port_compatibility=port_compatibility(),
        )

    @kit.tool(title="Get node type", read_only=True)
    async def node_type_get(node_type: NodeType) -> NodeTypeDetail:
        """Full contract of one node type: input/output ports (name, type,
        required, multiple), config_schema (JSON Schema for the node's
        config), capabilities and documentation link. Edges connect an
        output port name to an input port name."""
        definition = c.nodes.get(node_type).definition
        plugin_of = c.plugins.node_plugins if c.plugins is not None else {}
        return NodeTypeDetail(
            **NodeTypeOut.from_definition(definition).model_dump(by_alias=False),
            plugin_id=plugin_of.get(node_type),
        )

    @kit.tool(title="Validate node config", read_only=True)
    async def node_config_validate(
        node_type: NodeType, config: Config
    ) -> ConfigValidationOut:
        """Check a config against a node type's schema before putting it in
        a draft: every issue publish would report, with its path. Stores
        nothing."""
        issues = c.nodes.get(node_type).definition.config_issues(config)
        return ConfigValidationOut(
            valid=not issues,
            issues=[
                ConfigIssueOut(path=list(i.path), message=i.message) for i in issues
            ],
        )

    # -- workflows -----------------------------------------------------------

    @kit.tool(title="List workflows", read_only=True)
    async def workflow_list(
        query: Annotated[
            str | None, Field(max_length=200, description="Name contains (any case).")
        ] = None,
        status: list[WorkflowStatus] | None = None,
        offset: Offset = 0,
        limit: Limit = 20,
    ) -> WorkflowPage:
        """Workflows you may see (yours; all for admins), newest first."""
        page = await workflows().list_workflows(
            offset=offset,
            limit=limit,
            visible_to=AuthorizationService.visible_to(current_identity()),
            search=query,
            statuses=status or (),
            sort=WorkflowSort.CREATED,
        )
        return WorkflowPage.from_page(page)

    @kit.tool(title="Get workflow", read_only=True)
    async def workflow_get(workflow_id: WorkflowId) -> WorkflowOverview:
        """Workflow summary: name, description, status, current revision,
        latest published version (current_version, 0 = never published) and
        draft size. The draft itself: workflow_draft_get / graph_get."""
        await workflow(workflow_id)
        return _overview(await workflows().get_workflow(workflow_id))

    @kit.tool(title="Create workflow", idempotent=False)
    async def workflow_create(
        name: Name, description: Description = ""
    ) -> WorkflowOverview:
        """Create an empty workflow owned by you. Returns its id and revision
        (pass to graph_node_add). Calling twice creates two workflows."""
        created = await workflows().create_workflow(
            name, description=description, owner_id=current_identity().user.id
        )
        return _overview(created)

    @kit.tool(title="Update workflow")
    async def workflow_update(
        workflow_id: WorkflowId,
        expected_revision: Revision,
        name: Name | None = None,
        description: Description | None = None,
    ) -> WorkflowOverview:
        """Rename a workflow or change its description (draft metadata).
        Published versions keep their own description."""

        def change(draft: WorkflowDraft) -> None:
            if description is not None:
                draft.description = description

        return _overview(await edit(workflow_id, expected_revision, change, name))

    @kit.tool(title="Delete workflow", destructive=True)
    async def workflow_delete(workflow_id: WorkflowId) -> DraftChange:
        """Delete a never-published workflow permanently. Workflows with
        published versions cannot be deleted (WORKFLOW_HAS_VERSIONS). Needs
        the owner or an admin."""
        await workflow(workflow_id, Access.MANAGE)
        await workflows().delete_workflow(workflow_id)
        return DraftChange(workflow_id=workflow_id, revision=0)

    @kit.tool(title="Get workflow draft", read_only=True)
    async def workflow_draft_get(workflow_id: WorkflowId) -> DraftOut:
        """The whole mutable draft: graph (nodes with config, edges),
        triggers, declared dependencies, file list, and its revision."""
        await workflow(workflow_id)
        p = await workflows().get_workflow(workflow_id)
        detail = WorkflowDetail.from_domain(p)
        return DraftOut(
            workflow_id=p.id,
            revision=p.revision,
            name=p.name,
            description=p.draft.description,
            draft=detail.draft,
            files=detail.files,
        )

    @kit.tool(title="Replace workflow draft", destructive=True)
    async def workflow_draft_update(
        workflow_id: WorkflowId,
        expected_revision: Revision,
        draft: DraftBody,
        name: Name | None = None,
        description: Description | None = None,
    ) -> DraftChange:
        """Replace the whole draft (graph, triggers, dependencies) in one
        write, like the editor's save. Prefer the graph_* tools for small
        changes. Drafts may be invalid; workflow_validate reports issues.
        Node ids must look like node_<32 hex chars>."""
        await workflow(workflow_id, Access.EDIT)
        new_draft = draft.to_domain("")
        await sources(workflow_id, new_draft.triggers)
        current = await workflows().get_workflow(workflow_id)
        updated = await workflows().update_draft(
            workflow_id,
            expected_revision=expected_revision,
            name=current.name if name is None else name,
            draft=draft.to_domain(
                current.draft.description if description is None else description
            ),
        )
        return _workflow_by(updated)

    # -- graph --------------------------------------------------------------

    @kit.tool(title="Get graph", read_only=True)
    async def graph_get(workflow_id: WorkflowId) -> GraphOut:
        """The draft graph: nodes (id, node_type, config, label) and edges
        (source_node_id.source_port -> target_node_id.target_port)."""
        await workflow(workflow_id)
        p = await workflows().get_workflow(workflow_id)
        return GraphOut(
            workflow_id=p.id, revision=p.revision, graph=_graph_body(p.draft.graph)
        )

    @kit.tool(title="Add graph node")
    async def graph_node_add(
        workflow_id: WorkflowId,
        expected_revision: Revision,
        node_type: NodeType,
        config: Config | None = None,
        label: Annotated[str, Field(max_length=200)] = "",
        x: float | None = None,
        y: float | None = None,
    ) -> DraftChange:
        """Add a node of ``node_type`` to the draft graph. Returns the new
        node_id (use it for edges) and revision. x/y place it in the editor
        (optional)."""
        node = NodeInstance.create(
            node_type,
            config=dict(config or {}),
            label=label,
            position=None if x is None or y is None else Position(x=x, y=y),
        )
        p = await edit(workflow_id, expected_revision, lambda d: d.graph.add_node(node))
        return DraftChange(workflow_id=p.id, revision=p.revision, node_id=node.id)

    @kit.tool(title="Update graph node")
    async def graph_node_update(
        workflow_id: WorkflowId,
        expected_revision: Revision,
        node_id: NodeId,
        config: Annotated[
            dict[str, JsonValue] | None,
            Field(description="Replaces the whole config when given."),
        ] = None,
        label: Annotated[str | None, Field(max_length=200)] = None,
    ) -> DraftChange:
        """Replace a node's config and/or label. Omitted fields stay."""

        def change(draft: WorkflowDraft) -> None:
            node = draft.graph.node(node_id)
            if config is not None:
                node.config = dict(config)
            if label is not None:
                node.label = label

        p = await edit(workflow_id, expected_revision, change)
        return DraftChange(workflow_id=p.id, revision=p.revision, node_id=node_id)

    @kit.tool(title="Delete graph node", destructive=True)
    async def graph_node_delete(
        workflow_id: WorkflowId, expected_revision: Revision, node_id: NodeId
    ) -> DraftChange:
        """Remove a node and every edge touching it."""
        removed = 0

        def change(draft: WorkflowDraft) -> None:
            nonlocal removed
            before = len(draft.graph.edges)
            draft.graph.remove_node(node_id)
            removed = before - len(draft.graph.edges)

        p = await edit(workflow_id, expected_revision, change)
        return DraftChange(
            workflow_id=p.id,
            revision=p.revision,
            node_id=node_id,
            removed_edges=removed,
        )

    @kit.tool(title="Add graph edge")
    async def graph_edge_add(
        workflow_id: WorkflowId,
        expected_revision: Revision,
        source_node_id: NodeId,
        source_port: PortName,
        target_node_id: NodeId,
        target_port: PortName,
    ) -> DraftChange:
        """Connect an output port of one node to an input port of another.
        Port names and type compatibility are checked by workflow_validate."""
        e = _edge(source_node_id, source_port, target_node_id, target_port)
        p = await edit(workflow_id, expected_revision, lambda d: d.graph.add_edge(e))
        return _workflow_by(p)

    @kit.tool(title="Delete graph edge", destructive=True)
    async def graph_edge_delete(
        workflow_id: WorkflowId,
        expected_revision: Revision,
        source_node_id: NodeId,
        source_port: PortName,
        target_node_id: NodeId,
        target_port: PortName,
    ) -> DraftChange:
        """Remove one edge (identified by its four endpoints)."""
        e = _edge(source_node_id, source_port, target_node_id, target_port)
        p = await edit(workflow_id, expected_revision, lambda d: d.graph.remove_edge(e))
        return _workflow_by(p)

    # -- validation and publishing -------------------------------------------

    @kit.tool(title="Validate workflow", read_only=True)
    async def workflow_validate(
        workflow_id: WorkflowId, expected_revision: Revision | None = None
    ) -> WorkflowValidation:
        """Validate the draft exactly as publish will: graph structure, port
        types, required inputs, node configs, dependencies, triggers. Each
        issue names its node/port/edge/trigger. Valid => publishable."""
        await workflow(workflow_id)
        svc = workflows()
        result = await svc.validate_workflow(
            workflow_id, expected_revision=expected_revision
        )
        p = await svc.get_workflow(workflow_id)
        return WorkflowValidation(
            workflow_id=workflow_id,
            revision=p.revision,
            valid=result.ok,
            issues=ValidationResponse.from_domain(result).issues,
        )

    @kit.tool(title="Publish workflow")
    async def workflow_publish(
        workflow_id: WorkflowId, expected_revision: Revision | None = None
    ) -> WorkflowVersionDetail:
        """Freeze the draft as the next immutable version (validated first;
        WORKFLOW_VALIDATION_FAILED lists the issues). Pass the revision you
        validated so a newer draft is not published by mistake. The version
        pins the plugin versions it uses (plugin_versions)."""
        await workflow(workflow_id, Access.EDIT)
        identity = current_identity()
        version = await workflows().publish_workflow(
            workflow_id,
            expected_revision=expected_revision,
            published_by=identity.user.id,
        )
        names = {identity.user.id: identity.user.username}
        return WorkflowVersionDetail.from_domain(version, names)

    @kit.tool(title="List workflow versions", read_only=True)
    async def workflow_versions_list(
        workflow_id: WorkflowId, offset: Offset = 0, limit: Limit = 20
    ) -> WorkflowVersionPage:
        """Published versions of a workflow, oldest first."""
        await workflow(workflow_id)
        svc = workflows()
        page = await svc.list_versions(workflow_id, offset=offset, limit=limit)
        names = await svc.usernames([v.published_by for v in page.items])
        return WorkflowVersionPage.from_page(page, names)

    @kit.tool(title="Get workflow version", read_only=True)
    async def workflow_version_get(
        workflow_id: WorkflowId, version: Annotated[int, Field(ge=1)]
    ) -> WorkflowVersionDetail:
        """One immutable published version: its graph, triggers,
        dependencies and pinned plugin versions."""
        await workflow(workflow_id)
        svc = workflows()
        found = await svc.get_version(workflow_id, version)
        return WorkflowVersionDetail.from_domain(
            found, await svc.usernames([found.published_by])
        )

    # -- executions -----------------------------------------------------------

    @kit.tool(title="Start execution")
    async def execution_start(
        workflow_id: WorkflowId,
        version: Annotated[
            int | None,
            Field(ge=1, description="Published version; default: the latest."),
        ] = None,
        input: Annotated[
            dict[str, JsonValue] | None,
            Field(description="Execution input; core.input nodes read it."),
        ] = None,
    ) -> StartedExecution:
        """Run a published version (never the draft). Returns immediately
        with the execution id in status QUEUED; poll execution_get until a
        terminal status (completed, failed, cancelled, timeout). Each call
        starts a new run."""
        await workflow(workflow_id, Access.EDIT)
        if version is None:
            version = (await workflows().get_workflow(workflow_id)).current_version
            if version == 0:
                raise DomainValidationError(
                    "The workflow has no published version; publish it first.",
                    details={"workflow_id": workflow_id},
                )
        started = await _need(c.triggers, "Execution persistence").fire_manual(
            workflow_id,
            version,
            input=input,
            correlation_id=_correlation_id(),
        )
        return StartedExecution(
            **ExecutionDetail.from_domain(started).model_dump(),
            runs_automatically=c.queue is not None,
        )

    @kit.tool(title="List executions", read_only=True)
    async def execution_list(
        workflow_id: Annotated[
            str | None, Field(max_length=64, description="Only this workflow's runs.")
        ] = None,
        version: Annotated[int | None, Field(ge=1)] = None,
        status: list[ExecutionStatus] | None = None,
        offset: Offset = 0,
        limit: Limit = 20,
    ) -> ExecutionPage:
        """Executions newest first: of one workflow (optionally one version),
        or across the workflows you may see (optionally by status)."""
        svc = executions()
        if workflow_id is not None:
            await workflow(workflow_id)
            page = await svc.list_executions(
                workflow_id, version=version, offset=offset, limit=limit
            )
        else:
            page = await svc.list_recent(
                visible_to=AuthorizationService.visible_to(current_identity()),
                statuses=status or (),
                offset=offset,
                limit=limit,
            )
        return ExecutionPage.from_page(page)

    @kit.tool(title="Get execution", read_only=True)
    async def execution_get(execution_id: ExecutionId) -> ExecutionOverview:
        """State of one execution: status, error, every node's status and
        attempts, token/cost metrics. Node outputs: execution_node_get."""
        await execution(execution_id)
        report = await reports().execution_report(execution_id)
        found = await executions().get_execution(execution_id)
        return ExecutionOverview(
            **ExecutionDetail.from_domain(found).model_dump(),
            error=report.execution.error,
            node_versions=report.execution.node_versions,
            duration_ms=report.execution.duration_ms,
            nodes=[
                NodeStateOut(
                    node_id=n.node_id,
                    node_type=n.node_type,
                    label=n.label,
                    status=n.status.value,
                    attempts=n.attempts,
                    error=n.error,
                )
                for n in report.nodes
            ],
            metrics=report.metrics,
        )

    @kit.tool(title="Get node execution", read_only=True)
    async def execution_node_get(
        execution_id: ExecutionId, node_id: NodeId
    ) -> NodeReport:
        """One node's run within an execution: status, attempts, every
        failure, outputs (redacted), token usage and cost."""
        await execution(execution_id)
        report = await reports().execution_report(execution_id)
        for node in report.nodes:
            if node.node_id == node_id:
                return node
        raise _NodeNotInExecutionError(
            "The execution has no node with this id.",
            details={"execution_id": execution_id, "node_id": node_id},
        )

    @kit.tool(title="Cancel execution", destructive=True)
    async def execution_cancel(
        execution_id: ExecutionId,
        reason: Annotated[str, Field(max_length=500)] = "cancelled through MCP",
    ) -> ExecutionDetail:
        """Request cooperative cancellation. The run stops at its next
        heartbeat; a finished execution answers INVALID_STATE_TRANSITION."""
        await execution(execution_id, Access.EDIT)
        runtime: ExecutionRuntime = _need(c.runtime, "Execution persistence")
        await runtime.cancel(execution_id, reason=reason)
        return ExecutionDetail.from_domain(
            await executions().get_execution(execution_id)
        )

    @kit.tool(title="Get execution events", read_only=True)
    async def execution_events_get(
        execution_id: ExecutionId,
        after: Annotated[
            int, Field(ge=0, description="Last seen position; 0 = from the start.")
        ] = 0,
        limit: Limit = 50,
    ) -> EventPage:
        """Stored lifecycle events (execution/node started, completed,
        failed, ...), ascending. Continue with after=next_after."""
        await execution(execution_id)
        page = await executions().list_events(execution_id, after=after, limit=limit)
        items = [EventOut.from_domain(e) for e in page.items]
        return EventPage(
            items=items,
            next_after=items[-1].position if items else after,
            has_more=page.has_more,
        )

    @kit.tool(title="Get execution logs", read_only=True)
    async def execution_logs_get(
        execution_id: ExecutionId,
        after: Annotated[int, Field(ge=0)] = 0,
        limit: Limit = 50,
        node_id: Annotated[str | None, Field(max_length=64)] = None,
        level: LogLevel = LogLevel.DEBUG,
        contains: Annotated[str | None, Field(min_length=1, max_length=200)] = None,
    ) -> LogPage:
        """What the execution's nodes logged (redacted), ascending;
        optionally one node, a minimum level, or lines containing text.
        Continue with after=next_after."""
        await execution(execution_id)
        page = await executions().list_logs(
            execution_id,
            after=after,
            limit=limit,
            node_id=node_id,
            min_level=level,
            contains=contains,
        )
        items = [LogLineOut.from_domain(e) for e in page.items]
        return LogPage(
            items=items,
            next_after=items[-1].position if items else after,
            has_more=page.has_more,
        )

    @kit.tool(title="Get execution metrics", read_only=True)
    async def execution_metrics_get(execution_id: ExecutionId) -> MetricsReport:
        """Aggregates of one execution: node status counts, attempts,
        retries, failed attempts, token usage and estimated cost."""
        await execution(execution_id)
        return (await reports().execution_report(execution_id)).metrics

    @kit.tool(title="Get execution report", read_only=True)
    async def report_get(execution_id: ExecutionId) -> ExecutionReport:
        """The complete, redacted execution report (the same document the
        REST API exports): workflow, execution, every node with outputs,
        metrics and the event timeline. Large; prefer execution_get."""
        await execution(execution_id)
        return await reports().execution_report(execution_id)

    # -- triggers ------------------------------------------------------------

    @kit.tool(title="List triggers", read_only=True)
    async def trigger_list(workflow_id: WorkflowId) -> TriggerList:
        """The draft's triggers (manual, api, cron, interval, webhook,
        event). They take effect when the workflow is published."""
        await workflow(workflow_id)
        p = await workflows().get_workflow(workflow_id)
        return TriggerList(
            workflow_id=p.id, revision=p.revision, triggers=_triggers(p.draft.triggers)
        )

    @kit.tool(title="Create trigger")
    async def trigger_create(
        workflow_id: WorkflowId,
        expected_revision: Revision,
        trigger_type: TriggerType,
        config: Annotated[
            dict[str, JsonValue] | None,
            Field(
                description="cron: {'cron': '*/5 * * * *'}; interval:"
                " {'interval_seconds': 60}; webhook: {'secret_credential':"
                " 'credential:<name>'}; event: {'event': 'execution.completed'}."
            ),
        ] = None,
        enabled: bool = True,
    ) -> DraftChange:
        """Add a trigger to the draft. Publish to activate it."""
        trigger = Trigger.create(trigger_type, dict(config or {}))
        trigger.enabled = enabled
        await workflow(workflow_id, Access.EDIT)
        await sources(workflow_id, [trigger])
        p = await edit(
            workflow_id, expected_revision, lambda d: d.triggers.append(trigger)
        )
        return DraftChange(workflow_id=p.id, revision=p.revision, trigger_id=trigger.id)

    @kit.tool(title="Update trigger")
    async def trigger_update(
        workflow_id: WorkflowId,
        expected_revision: Revision,
        trigger_id: Annotated[str, Field(max_length=64)],
        config: dict[str, JsonValue] | None = None,
        enabled: Annotated[
            bool | None, Field(description="false disables, true enables.")
        ] = None,
    ) -> DraftChange:
        """Replace a draft trigger's config and/or enable or disable it.
        Publish to apply the change."""
        if config is not None:
            await workflow(workflow_id, Access.EDIT)
            current = (await workflows().get_workflow(workflow_id)).draft
            old = _trigger(current, trigger_id)
            new = Trigger(id=old.id, trigger_type=old.trigger_type, config=dict(config))
            await sources(workflow_id, [new])

        def change(draft: WorkflowDraft) -> None:
            old = _trigger(draft, trigger_id)
            new = Trigger(
                id=old.id,
                trigger_type=old.trigger_type,
                config=old.config if config is None else dict(config),
                enabled=old.enabled if enabled is None else enabled,
            )
            draft.triggers[draft.triggers.index(old)] = new

        p = await edit(workflow_id, expected_revision, change)
        return DraftChange(workflow_id=p.id, revision=p.revision, trigger_id=trigger_id)

    @kit.tool(title="Delete trigger", destructive=True)
    async def trigger_delete(
        workflow_id: WorkflowId,
        expected_revision: Revision,
        trigger_id: Annotated[str, Field(max_length=64)],
    ) -> DraftChange:
        """Remove a trigger from the draft. Publish to apply."""
        p = await edit(
            workflow_id,
            expected_revision,
            lambda d: d.triggers.remove(_trigger(d, trigger_id)),
        )
        return DraftChange(workflow_id=p.id, revision=p.revision, trigger_id=trigger_id)

    # -- import / export -------------------------------------------------------

    @kit.tool(title="Export workflow", read_only=True)
    async def workflow_export(
        workflow_id: WorkflowId,
        version: Annotated[
            int | None, Field(ge=1, description="Published version; default: draft.")
        ] = None,
    ) -> WorkflowPackageOut:
        """The workflow package (.akasha.zip, base64): graph, triggers,
        dependencies and files, never secrets. Import it with
        workflow_import."""
        await workflow(workflow_id)
        packages: WorkflowPackageService = _need(c.packages, "Workflow persistence")
        data = await packages.export_workflow(workflow_id, version=version)
        suffix = "draft" if version is None else f"v{version}"
        return WorkflowPackageOut(
            workflow_id=workflow_id,
            version=version,
            filename=f"{workflow_id}-{suffix}.akasha.zip",
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            package_base64=base64.b64encode(data).decode("ascii"),
        )

    @kit.tool(title="Import workflow")
    async def workflow_import(
        package_base64: Annotated[
            str, Field(description="A workflow package (.akasha.zip), base64.")
        ],
    ) -> ImportWorkflowResponse:
        """Create a new draft workflow owned by you from a workflow package.
        Returns it with its validation result and the credential references
        this installation lacks. Each call creates a new workflow."""
        data = decode_base64(package_base64, MAX_PACKAGE_BYTES)
        packages: WorkflowPackageService = _need(c.packages, "Workflow persistence")
        result = await packages.import_workflow(
            data, owner_id=current_identity().user.id
        )
        return ImportWorkflowResponse.from_domain(result)

    # -- credentials (metadata only) -------------------------------------------

    @kit.tool(title="List credentials", read_only=True)
    async def credential_list(offset: Offset = 0, limit: Limit = 50) -> CredentialPage:
        """Admin only. Credential names and kinds (never values), to put
        'credential:<name>' references into node configs. Creating or
        changing secrets is not possible over MCP: secrets must not pass
        through an agent's context."""
        require_admin(current_identity())
        svc: CredentialService = _need(c.credentials, "The credential store")
        return CredentialPage.from_page(await svc.list(offset=offset, limit=limit))


class _NodeNotInExecutionError(AkashaError):
    code = "NODE_EXECUTION_NOT_FOUND"
    http_status = 404


def _correlation_id() -> str | None:
    rid = current_log_context().get("request_id")
    return rid if isinstance(rid, str) else None


def decode_base64(value: str, max_bytes: int) -> bytes:
    """Strict base64 with a size cap checked before decoding."""
    if len(value) > (max_bytes + 2) // 3 * 4:
        raise DomainValidationError(
            f"The decoded content must not exceed {max_bytes} bytes."
        )
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raise DomainValidationError("The content is not valid base64.") from None
