"""Project, ProjectDraft, ProjectVersion — the publish core of the Engine.

Drafts are mutable. Publishing deep-copies the draft into a frozen
ProjectVersion; afterwards no draft mutation can reach the version
(architecture §3.7 "Immutable Published Versions"). Version numbers are
monotonically increasing and unique per project.
"""

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum, auto

from akasha.domain.graph import Graph
from akasha.domain.primitives import (
    Entity,
    Timestamps,
    new_id,
    utcnow,
    validate_id,
    validate_name,
)
from akasha.domain.trigger import Trigger
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


@dataclass
class ProjectDraft:
    """The editable state of a project. Mutable by design."""

    graph: Graph = field(default_factory=Graph)
    triggers: list[Trigger] = field(default_factory=list)
    dependencies: list[Dependency] = field(default_factory=list)
    description: str = ""

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


@dataclass(eq=False)
class Project(Entity):
    """Long-lived identity: owns one mutable draft and its published versions."""

    name: str
    draft: ProjectDraft = field(default_factory=ProjectDraft)
    versions: list[ProjectVersion] = field(default_factory=list)
    status: ProjectStatus = ProjectStatus.DRAFT
    timestamps: Timestamps = field(default_factory=Timestamps)

    def __post_init__(self) -> None:
        self.id = validate_id(self.id)
        validate_name(self.name)
        self.status = ProjectStatus(self.status)

    @classmethod
    def create(cls, name: str) -> "Project":
        return cls(id=new_id("proj"), name=name)

    @property
    def current_version(self) -> int:
        return max((v.version for v in self.versions), default=0)

    def publish_draft(self, *, published_at: datetime | None = None) -> ProjectVersion:
        """Publish the draft as the next version; attach and return it."""
        snapshot = self.draft.publish(
            version=self.current_version + 1, published_at=published_at
        )
        self.versions.append(snapshot)
        if self.status is ProjectStatus.DRAFT:
            self.status = ProjectStatus.PUBLISHED
        self.timestamps = Timestamps(
            created_at=self.timestamps.created_at, updated_at=snapshot.published_at
        )
        return snapshot
