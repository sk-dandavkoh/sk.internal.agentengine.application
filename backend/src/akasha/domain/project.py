"""Project, ProjectDraft, ProjectVersion — the publish core of the Engine.

Drafts are mutable. Publishing deep-copies the draft into a frozen
ProjectVersion; afterwards no draft mutation can reach the version
(architecture §3.7 "Immutable Published Versions"). Version numbers are
monotonically increasing and unique per project.
"""

from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum, auto

from akasha.domain.credentials import PlaintextSecretError, plaintext_secret_paths
from akasha.domain.graph import Graph
from akasha.domain.graph_validation import (
    ValidationCode,
    ValidationIssue,
    ValidationResult,
    validate_graph,
)
from akasha.domain.node import NodeDefinition
from akasha.domain.primitives import (
    Entity,
    Timestamps,
    new_id,
    utcnow,
    validate_id,
    validate_name,
    version_satisfies,
)
from akasha.domain.trigger import Trigger, schedule_problem
from akasha.errors import DomainValidationError

_SCHEMA_VERSION = 1  # project structure schema; bumped when the format changes


class ProjectStatus(StrEnum):
    DRAFT = auto()
    PUBLISHED = auto()
    ACTIVE = auto()
    DISABLED = auto()
    ARCHIVED = auto()


@dataclass(frozen=True)
class Dependency:
    """Declared need for an external component: node type or plugin."""

    kind: str  # "node" or "plugin"
    name: str
    version_constraint: str = "*"  # e.g. ">=1.2.0"; any version if "*"

    def __post_init__(self) -> None:
        if self.kind not in ("node", "plugin"):
            raise DomainValidationError(f"unknown dependency kind: {self.kind!r}")
        validate_name(self.name)
        if not self.version_constraint.strip():
            raise DomainValidationError("version constraint must not be blank")


def validate_dependencies(
    dependencies: Iterable[Dependency], definitions: Mapping[str, NodeDefinition]
) -> tuple[ValidationIssue, ...]:
    """Check declared node dependencies against the available node types.

    A malformed constraint is a validation issue, not a construction error:
    already persisted projects must stay loadable to be fixed. Plugin
    dependencies are not checked here — plugins do not exist before the
    Plugin System (Phase 17), which owns that check.
    """
    issues: list[ValidationIssue] = []
    for dep in dependencies:
        if dep.kind != "node":
            continue
        definition = definitions.get(dep.name)
        if definition is None:
            issues.append(
                ValidationIssue(
                    ValidationCode.MISSING_DEPENDENCY,
                    f"required node type {dep.name!r} is not available",
                )
            )
            continue
        try:
            ok = version_satisfies(definition.version, dep.version_constraint)
        except DomainValidationError:
            issues.append(
                ValidationIssue(
                    ValidationCode.INVALID_DEPENDENCY,
                    f"malformed version constraint for node type {dep.name!r}",
                )
            )
            continue
        if not ok:
            issues.append(
                ValidationIssue(
                    ValidationCode.INCOMPATIBLE_DEPENDENCY,
                    f"node type {dep.name!r} {definition.version} does not satisfy "
                    f"{dep.version_constraint!r}",
                )
            )
    return tuple(issues)


def validate_triggers(triggers: Iterable[Trigger]) -> tuple[ValidationIssue, ...]:
    """Trigger rules that publishing requires.

    Trigger ids must be unique within a project: webhook and invoke calls
    address a trigger by (project, trigger id). Schedules must be
    evaluable (``schedule_problem``). Both are validation issues, not
    construction errors, for the same reason as dependency constraints:
    persisted drafts must stay loadable so they can be fixed.
    """
    issues: list[ValidationIssue] = []
    seen: set[str] = set()
    for trigger in triggers:
        if trigger.id in seen:
            issues.append(
                ValidationIssue(
                    ValidationCode.DUPLICATE_TRIGGER_ID,
                    "trigger id is used more than once in this project",
                    trigger_id=trigger.id,
                )
            )
        seen.add(trigger.id)
        problem = schedule_problem(trigger)
        if problem is not None:
            issues.append(
                ValidationIssue(
                    ValidationCode.INVALID_TRIGGER_SCHEDULE,
                    problem,
                    trigger_id=trigger.id,
                )
            )
    return tuple(issues)


def _validate(
    graph: Graph,
    dependencies: Iterable[Dependency],
    definitions: Mapping[str, NodeDefinition],
) -> ValidationResult:
    issues = validate_graph(graph, definitions).issues
    return ValidationResult(issues + validate_dependencies(dependencies, definitions))


@dataclass
class ProjectDraft:
    """The editable state of a project. Mutable by design."""

    graph: Graph = field(default_factory=Graph)
    triggers: list[Trigger] = field(default_factory=list)
    dependencies: list[Dependency] = field(default_factory=list)
    description: str = ""

    def validate(self, definitions: Mapping[str, NodeDefinition]) -> ValidationResult:
        """Full graph, dependency and trigger validation without executing.

        Callers (ValidateProject / PublishProject, Phase 06) decide what to
        do with the issues; the draft itself never raises on user data here.
        """
        result = _validate(self.graph, self.dependencies, definitions)
        return ValidationResult(result.issues + validate_triggers(self.triggers))

    def publish(
        self,
        *,
        version: int,
        published_at: datetime | None = None,
    ) -> "ProjectVersion":
        """Freeze the current draft state into an immutable version snapshot."""
        if version < 1:
            raise DomainValidationError("version number must be >= 1")
        return ProjectVersion(
            id=new_id("pver"),
            version=version,
            schema_version=_SCHEMA_VERSION,
            graph=deepcopy(self.graph),
            triggers=tuple(deepcopy(t) for t in self.triggers),
            dependencies=tuple(self.dependencies),
            description=self.description,
            published_at=published_at or utcnow(),
        )


@dataclass(frozen=True)
class ProjectVersion:
    """Immutable, executable snapshot of a project. Never mutated after creation."""

    id: str
    version: int
    schema_version: int
    graph: Graph
    triggers: tuple[Trigger, ...] = ()
    dependencies: tuple[Dependency, ...] = ()
    description: str = ""
    published_at: datetime = field(default_factory=utcnow)

    def __post_init__(self) -> None:
        validate_id(self.id)
        if self.version < 1:
            raise DomainValidationError("version number must be >= 1")
        if self.schema_version < 1:
            raise DomainValidationError("schema_version must be >= 1")

    def validate(self, definitions: Mapping[str, NodeDefinition]) -> ValidationResult:
        """The draft's graph and dependency rules: the node catalog may have
        changed since publish, so the runtime re-checks the snapshot before
        every run. Trigger rules are left out on purpose: they gate
        publishing, and a run never depends on how it was triggered."""
        return _validate(self.graph, self.dependencies, definitions)


@dataclass(eq=False)
class Project(Entity):
    """Long-lived identity: owns one mutable draft and a published-version
    counter.

    The published versions themselves are not part of the loaded aggregate:
    history is unbounded and lives behind ``ProjectVersionRepository``.
    ``current_version`` is the number of the latest published version (0 =
    never published) and is all publishing needs to pick the next number.

    ``revision`` is the optimistic-concurrency token of the persisted row
    (0 = never persisted). Persistence advances it on every successful save;
    domain logic only reads it.
    """

    name: str
    draft: ProjectDraft = field(default_factory=ProjectDraft)
    status: ProjectStatus = ProjectStatus.DRAFT
    current_version: int = 0
    revision: int = 0
    timestamps: Timestamps = field(default_factory=Timestamps)

    def __post_init__(self) -> None:
        self.id = validate_id(self.id)
        validate_name(self.name)
        self.status = ProjectStatus(self.status)
        if self.current_version < 0 or self.revision < 0:
            raise DomainValidationError("version counters must be >= 0")

    @classmethod
    def create(cls, name: str, *, description: str = "") -> "Project":
        return cls(
            id=new_id("proj"), name=name, draft=ProjectDraft(description=description)
        )

    def replace_draft(self, draft: ProjectDraft, *, name: str) -> None:
        """Replace the editable state wholesale (autosave semantics).

        Never touches published versions or the version counter. Rejects
        secret values in node/trigger config (only credential references
        may be stored); the error names locations, never values.
        """
        validate_name(name)
        leaks = [
            f"nodes.{node_id}.config{path}"
            for node_id in sorted(draft.graph.nodes)
            for path in plaintext_secret_paths(draft.graph.nodes[node_id].config)
        ] + [
            f"triggers.{trigger.id}.config{path}"
            for trigger in draft.triggers
            for path in plaintext_secret_paths(trigger.config)
        ]
        if leaks:
            raise PlaintextSecretError(
                "Project configuration must reference credentials"
                " (credential:<name>) instead of containing secret values.",
                details={"paths": leaks},
            )
        self.name = name
        self.draft = draft
        self._touch(utcnow())

    def publish_draft(self, *, published_at: datetime | None = None) -> ProjectVersion:
        """Freeze the draft as the next version and return the snapshot.

        The caller persists the snapshot; the project only advances its
        counter, so version numbers stay monotonic and gap-free.
        """
        snapshot = self.draft.publish(
            version=self.current_version + 1, published_at=published_at
        )
        self.current_version = snapshot.version
        if self.status is ProjectStatus.DRAFT:
            self.status = ProjectStatus.PUBLISHED
        self._touch(snapshot.published_at)
        return snapshot

    def _touch(self, at: datetime) -> None:
        self.timestamps = Timestamps(
            created_at=self.timestamps.created_at, updated_at=at
        )
