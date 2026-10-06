"""Request/response schemas for /api/v1/projects (boundary models).

Explicit DTOs, never the domain dataclasses themselves (backend/AGENTS.md
§10/§12). Request bodies forbid unknown fields so typos fail loudly instead
of being silently dropped. ``to_domain`` only maps shapes: structural
invariants (id format, duplicate nodes, dangling edges, trigger config) are
enforced by the domain constructors and surface as 422
``DOMAIN_VALIDATION_ERROR``; semantic graph rules are reported by
``/validate``.
"""

from collections.abc import Mapping
from dataclasses import asdict
from datetime import datetime
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    FiniteFloat,
    JsonValue,
    StringConstraints,
)

from akasha.application.packages import ImportResult
from akasha.application.projects import Page, issue_payload
from akasha.domain.graph import Edge, Graph, NodeInstance, Position
from akasha.domain.graph_validation import ValidationCode, ValidationResult
from akasha.domain.project import (
    Dependency,
    Project,
    ProjectDraft,
    ProjectStatus,
    ProjectVersion,
)
from akasha.domain.trigger import Trigger, TriggerType

# ponytail: no per-list size caps yet; request/payload limits are Phase 24.
Name = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]
Description = Annotated[str, StringConstraints(max_length=10_000)]


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PositionBody(_Request):
    """Editor canvas coordinates; layout only, ignored at runtime."""

    x: FiniteFloat
    y: FiniteFloat


class NodeInstanceBody(_Request):
    id: str
    node_type: str
    config: dict[str, JsonValue] = Field(default_factory=dict)
    label: str = ""
    # null: never placed in the editor (created via API or import).
    position: PositionBody | None = None


class EdgeBody(_Request):
    source_node_id: str
    source_port: str
    target_node_id: str
    target_port: str


class GraphBody(_Request):
    nodes: list[NodeInstanceBody] = Field(default_factory=list)
    edges: list[EdgeBody] = Field(default_factory=list)


class TriggerBody(_Request):
    id: str
    trigger_type: TriggerType
    config: dict[str, JsonValue] = Field(default_factory=dict)
    enabled: bool = True


class DependencyBody(_Request):
    kind: Literal["node", "plugin"]
    name: str
    version_constraint: str = "*"


class DraftBody(_Request):
    """The editable graph state. GET returns it; PUT sends it back whole."""

    graph: GraphBody = Field(default_factory=GraphBody)
    triggers: list[TriggerBody] = Field(default_factory=list)
    dependencies: list[DependencyBody] = Field(default_factory=list)

    def to_domain(self, description: str) -> ProjectDraft:
        graph = Graph()
        for n in self.graph.nodes:
            graph.add_node(
                NodeInstance(
                    id=n.id,
                    node_type=n.node_type,
                    config=dict(n.config),
                    label=n.label,
                    position=None
                    if n.position is None
                    else Position(x=n.position.x, y=n.position.y),
                )
            )
        for e in self.graph.edges:
            graph.add_edge(Edge(**e.model_dump()))
        return ProjectDraft(
            graph=graph,
            triggers=[
                Trigger(
                    id=t.id,
                    trigger_type=t.trigger_type,
                    config=dict(t.config),
                    enabled=t.enabled,
                )
                for t in self.triggers
            ],
            dependencies=[Dependency(**d.model_dump()) for d in self.dependencies],
            description=description,
        )


def _graph_body(graph: Graph) -> GraphBody:
    return GraphBody(
        # Sorted like the persistence codec: deterministic output (§72).
        nodes=[
            NodeInstanceBody(
                id=n.id,
                node_type=n.node_type,
                config=n.config,
                label=n.label,
                position=None
                if n.position is None
                else PositionBody(x=n.position.x, y=n.position.y),
            )
            for n in sorted(graph.nodes.values(), key=lambda n: n.id)
        ],
        edges=[EdgeBody.model_validate(asdict(e)) for e in graph.edges],
    )


def _triggers(triggers: list[Trigger] | tuple[Trigger, ...]) -> list[TriggerBody]:
    return [
        TriggerBody(
            id=t.id, trigger_type=t.trigger_type, config=t.config, enabled=t.enabled
        )
        for t in triggers
    ]


def _dependencies(
    deps: list[Dependency] | tuple[Dependency, ...],
) -> list[DependencyBody]:
    return [DependencyBody.model_validate(asdict(d)) for d in deps]


class ProjectFileOut(BaseModel):
    """A project file's metadata; content travels in project packages only."""

    path: str
    size: int


def _files(files: Mapping[str, bytes]) -> list[ProjectFileOut]:
    return [ProjectFileOut(path=p, size=len(files[p])) for p in sorted(files)]


class CreateProjectRequest(_Request):
    name: Name
    description: Description = ""


class DuplicateProjectRequest(_Request):
    name: Name
    # Copy this published version instead of the current draft.
    version: int | None = Field(default=None, ge=1)


class RestoreVersionRequest(_Request):
    """``expected_revision`` is the draft revision being replaced."""

    expected_revision: int = Field(ge=1)


class UpdateProjectRequest(_Request):
    """Full replacement of name, description and draft (PUT semantics).

    ``expected_revision`` is the ``revision`` the client last read; a stale
    value is rejected with 409 instead of overwriting newer work.
    """

    expected_revision: int = Field(ge=1)
    name: Name
    description: Description = ""
    draft: DraftBody


class PublishProjectRequest(_Request):
    """Optional: pin publishing to the revision the client validated."""

    expected_revision: int | None = Field(default=None, ge=1)


class ValidateProjectRequest(_Request):
    """Optional: pin validation to the revision the client shows."""

    expected_revision: int | None = Field(default=None, ge=1)


class ProjectSummary(BaseModel):
    id: str
    name: str
    description: str
    status: ProjectStatus
    revision: int
    current_version: int
    created_at: datetime
    updated_at: datetime
    owner_id: str | None

    @classmethod
    def from_domain(cls, p: Project) -> "ProjectSummary":
        return cls(
            id=p.id,
            name=p.name,
            description=p.draft.description,
            status=p.status,
            revision=p.revision,
            current_version=p.current_version,
            created_at=p.timestamps.created_at,
            updated_at=p.timestamps.updated_at,
            owner_id=p.owner_id,
        )


class ProjectDetail(ProjectSummary):
    draft: DraftBody
    files: list[ProjectFileOut]

    @classmethod
    def from_domain(cls, p: Project) -> "ProjectDetail":
        return cls(
            **ProjectSummary.from_domain(p).model_dump(),
            draft=DraftBody(
                graph=_graph_body(p.draft.graph),
                triggers=_triggers(p.draft.triggers),
                dependencies=_dependencies(p.draft.dependencies),
            ),
            files=_files(p.draft.files),
        )


class AuthorOut(BaseModel):
    """Who published a version. ``username`` is null only if the account
    cannot be found (users are disabled, never deleted)."""

    id: str
    username: str | None


Usernames = Mapping[str, str]


class ProjectVersionSummary(BaseModel):
    id: str
    version: int
    schema_version: int
    description: str
    published_at: datetime
    # Null when unknown (CLI publishes, versions from before 0011).
    published_by: AuthorOut | None

    @classmethod
    def from_domain(
        cls, v: ProjectVersion, names: Usernames | None = None
    ) -> "ProjectVersionSummary":
        author = v.published_by
        return cls(
            id=v.id,
            version=v.version,
            schema_version=v.schema_version,
            description=v.description,
            published_at=v.published_at,
            published_by=None
            if author is None
            else AuthorOut(id=author, username=(names or {}).get(author)),
        )


class ProjectVersionDetail(ProjectVersionSummary):
    graph: GraphBody
    triggers: list[TriggerBody]
    dependencies: list[DependencyBody]
    files: list[ProjectFileOut]
    # Plugin id -> the exact version this version runs on (pinned at
    # publish); runs refuse any other installed version.
    plugin_versions: dict[str, str]

    @classmethod
    def from_domain(
        cls, v: ProjectVersion, names: Usernames | None = None
    ) -> "ProjectVersionDetail":
        return cls(
            **ProjectVersionSummary.from_domain(v, names).model_dump(),
            graph=_graph_body(v.graph),
            triggers=_triggers(v.triggers),
            dependencies=_dependencies(v.dependencies),
            files=_files(v.files),
            plugin_versions=dict(v.plugin_versions),
        )


class ValidationIssueOut(BaseModel):
    code: ValidationCode
    message: str
    node_id: str | None
    port: str | None
    edge: EdgeBody | None
    trigger_id: str | None


class ValidationResponse(BaseModel):
    valid: bool
    issues: list[ValidationIssueOut]

    @classmethod
    def from_domain(cls, result: ValidationResult) -> "ValidationResponse":
        return cls(
            valid=result.ok,
            issues=[
                ValidationIssueOut.model_validate(issue_payload(i))
                for i in result.issues
            ],
        )


class PageInfo(BaseModel):
    offset: int
    limit: int
    has_more: bool


class ProjectPage(BaseModel):
    items: list[ProjectSummary]
    page: PageInfo

    @classmethod
    def from_page(cls, page: Page[Project]) -> "ProjectPage":
        return cls(
            items=[ProjectSummary.from_domain(p) for p in page.items],
            page=PageInfo(offset=page.offset, limit=page.limit, has_more=page.has_more),
        )


class ProjectVersionPage(BaseModel):
    items: list[ProjectVersionSummary]
    page: PageInfo

    @classmethod
    def from_page(
        cls, page: Page[ProjectVersion], names: Usernames
    ) -> "ProjectVersionPage":
        return cls(
            items=[ProjectVersionSummary.from_domain(v, names) for v in page.items],
            page=PageInfo(offset=page.offset, limit=page.limit, has_more=page.has_more),
        )


class ImportProjectResponse(BaseModel):
    """The new project (an unpublished draft), its validation result and the
    referenced credentials this installation does not have yet."""

    project: ProjectDetail
    validation: ValidationResponse
    missing_credentials: list[str]

    @classmethod
    def from_domain(cls, result: ImportResult) -> "ImportProjectResponse":
        return cls(
            project=ProjectDetail.from_domain(result.project),
            validation=ValidationResponse.from_domain(result.validation),
            missing_credentials=list(result.missing_credentials),
        )
