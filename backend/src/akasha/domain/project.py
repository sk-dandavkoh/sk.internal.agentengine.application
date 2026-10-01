"""Project, ProjectDraft, ProjectVersion — the publish core of the Engine.

Drafts are mutable. Publishing deep-copies the draft into a frozen
ProjectVersion; afterwards no draft mutation can reach the version
(architecture §3.7 "Immutable Published Versions"). Version numbers are
monotonically increasing and unique per project.
"""

import re
from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum, auto

from akasha.domain.credentials import (
    PlaintextSecretError,
    is_credential_reference,
    plaintext_secret_paths,
)
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
    is_storable,
    new_id,
    utcnow,
    validate_id,
    validate_name,
    validate_text,
    version_satisfies,
)
from akasha.domain.trigger import (
    WEBHOOK_SIGNING_FIELD,
    Trigger,
    TriggerType,
    schedule_problem,
)
from akasha.errors import DomainValidationError
from akasha.security.redaction import redact_text

# Project structure schema; bumped when the format changes. Project packages
# (Phase 18) carry it, so an importer can reject structures it cannot read.
PROJECT_SCHEMA_VERSION = 1

# Project files: documentation, prompt templates, resources and assets that
# belong to a project and travel with it in project packages. Stored inline
# with the draft and snapshotted into every version.
# ponytail: inline JSONB storage, hence the small caps; move file content to
# artifact storage if projects need larger or more files.
PROJECT_FILE_ROOTS = ("docs", "prompts", "resources", "assets")
MAX_PROJECT_FILES = 100
MAX_PROJECT_FILE_BYTES = 1024 * 1024
MAX_PROJECT_FILES_TOTAL_BYTES = 4 * 1024 * 1024
# "<root>/<segment>[/<segment>...]": every segment starts with a letter or
# digit, so "." / ".." / hidden files and absolute or backslash paths can
# never be expressed.
_FILE_PATH_RE = re.compile(
    rf"^(?:{'|'.join(PROJECT_FILE_ROOTS)})(?:/[A-Za-z0-9][A-Za-z0-9._-]{{0,99}}){{1,8}}$"
)


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
    dependencies: Iterable[Dependency],
    definitions: Mapping[str, NodeDefinition],
    plugins: Mapping[str, str] | None = None,
) -> tuple[ValidationIssue, ...]:
    """Check declared dependencies against the available node types and,
    when ``plugins`` (enabled plugin id → version) is given, plugins.

    A malformed constraint is a validation issue, not a construction error:
    already persisted projects must stay loadable to be fixed. The runtime
    passes no ``plugins``: a run needs node types, which a disabled
    plugin's nodes no longer are; plugin dependencies gate validate/publish.
    """
    issues: list[ValidationIssue] = []
    for dep in dependencies:
        if dep.kind == "node":
            label = "node type"
            definition = definitions.get(dep.name)
            available = None if definition is None else definition.version
        elif plugins is not None:
            label = "plugin"
            available = plugins.get(dep.name)
        else:
            continue
        if available is None:
            issues.append(
                ValidationIssue(
                    ValidationCode.MISSING_DEPENDENCY,
                    f"required {label} {dep.name!r} is not available",
                )
            )
            continue
        try:
            ok = version_satisfies(available, dep.version_constraint)
        except DomainValidationError:
            issues.append(
                ValidationIssue(
                    ValidationCode.INVALID_DEPENDENCY,
                    f"malformed version constraint for {label} {dep.name!r}",
                )
            )
            continue
        if not ok:
            issues.append(
                ValidationIssue(
                    ValidationCode.INCOMPATIBLE_DEPENDENCY,
                    f"{label} {dep.name!r} {available} does not satisfy "
                    f"{dep.version_constraint!r}",
                )
            )
    return tuple(issues)


def is_project_file_path(path: str) -> bool:
    return _FILE_PATH_RE.match(path) is not None


def validate_project_files(files: Mapping[str, bytes]) -> None:
    """Paths and size limits of project files (construction rule: files
    arrive only through imports, which reject whole packages)."""
    if len(files) > MAX_PROJECT_FILES:
        raise DomainValidationError(
            f"a project holds at most {MAX_PROJECT_FILES} files"
        )
    total = 0
    for path, content in files.items():
        if not isinstance(path, str) or not is_project_file_path(path):
            raise DomainValidationError(
                "invalid project file path",
                details={"path": str(path)[:300]},
            )
        if not isinstance(content, bytes):
            raise DomainValidationError(
                "project file content must be bytes", details={"path": path}
            )
        if len(content) > MAX_PROJECT_FILE_BYTES:
            raise DomainValidationError(
                f"project file exceeds {MAX_PROJECT_FILE_BYTES} bytes",
                details={"path": path},
            )
        total += len(content)
    if total > MAX_PROJECT_FILES_TOTAL_BYTES:
        raise DomainValidationError(
            f"project files exceed {MAX_PROJECT_FILES_TOTAL_BYTES} bytes in total"
        )


def plaintext_secret_locations(
    graph: Graph, triggers: Iterable[Trigger], files: Mapping[str, bytes]
) -> list[str]:
    """Where (never what) secret material sits in project content.

    Configs follow ``plaintext_secret_paths``. Text files must not contain
    credential-shaped strings; binary files cannot be inspected and are the
    author's responsibility.
    """
    leaks = [
        f"nodes.{node_id}.config{path}"
        for node_id in sorted(graph.nodes)
        for path in plaintext_secret_paths(graph.nodes[node_id].config)
    ] + [
        f"triggers.{trigger.id}.config{path}"
        for trigger in triggers
        for path in plaintext_secret_paths(trigger.config)
    ]
    for path in sorted(files):
        try:
            text = files[path].decode("utf-8")
        except UnicodeDecodeError:
            continue
        if redact_text(text) != text:
            leaks.append(f"files.{path}")
    return leaks


def unstorable_locations(draft: "ProjectDraft") -> list[str]:
    """Where draft content holds values PostgreSQL cannot store (NaN,
    Infinity, NUL characters, unpaired surrogates; see ``is_storable``).
    Locations only."""
    found = [] if is_storable(draft.description) else ["description"]
    found += [
        f"nodes.{node_id}"
        for node_id in sorted(draft.graph.nodes)
        if not is_storable(
            [
                draft.graph.nodes[node_id].node_type,
                draft.graph.nodes[node_id].label,
                draft.graph.nodes[node_id].config,
            ]
        )
    ]
    found += [
        f"edges[{i}]"
        for i, e in enumerate(draft.graph.edges)
        if not is_storable([e.source_port, e.target_port])
    ]
    found += [f"triggers.{t.id}" for t in draft.triggers if not is_storable(t.config)]
    found += [
        f"dependencies[{i}]"
        for i, d in enumerate(draft.dependencies)
        if not is_storable([d.name, d.version_constraint])
    ]
    return found


def validate_triggers(triggers: Iterable[Trigger]) -> tuple[ValidationIssue, ...]:
    """Trigger rules that publishing requires.

    Trigger ids must be unique within a project: webhook and invoke calls
    address a trigger by (project, trigger id). Schedules must be
    evaluable (``schedule_problem``). Webhooks are public URLs, so they
    must name the credential that signs their requests. All are validation
    issues, not construction errors, for the same reason as dependency
    constraints: persisted drafts must stay loadable so they can be fixed.
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
        if trigger.trigger_type is TriggerType.WEBHOOK and not (
            is_credential_reference(trigger.config.get(WEBHOOK_SIGNING_FIELD))
        ):
            issues.append(
                ValidationIssue(
                    ValidationCode.WEBHOOK_SECRET_REQUIRED,
                    f"webhook trigger requires '{WEBHOOK_SIGNING_FIELD}', the"
                    " credential reference that signs its requests",
                    trigger_id=trigger.id,
                )
            )
    return tuple(issues)


def _validate(
    graph: Graph,
    dependencies: Iterable[Dependency],
    definitions: Mapping[str, NodeDefinition],
    plugins: Mapping[str, str] | None = None,
) -> ValidationResult:
    issues = validate_graph(graph, definitions).issues
    return ValidationResult(
        issues + validate_dependencies(dependencies, definitions, plugins)
    )


@dataclass
class ProjectDraft:
    """The editable state of a project. Mutable by design."""

    graph: Graph = field(default_factory=Graph)
    triggers: list[Trigger] = field(default_factory=list)
    dependencies: list[Dependency] = field(default_factory=list)
    description: str = ""
    # path → content; see ``validate_project_files``.
    files: dict[str, bytes] = field(default_factory=dict)

    def validate(
        self,
        definitions: Mapping[str, NodeDefinition],
        plugins: Mapping[str, str] | None = None,
    ) -> ValidationResult:
        """Full graph, dependency and trigger validation without executing.

        Callers (ValidateProject / PublishProject, Phase 06) decide what to
        do with the issues; the draft itself never raises on user data here.
        ``plugins`` (enabled id → version): see ``validate_dependencies``.
        """
        result = _validate(self.graph, self.dependencies, definitions, plugins)
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
            schema_version=PROJECT_SCHEMA_VERSION,
            graph=deepcopy(self.graph),
            triggers=tuple(deepcopy(t) for t in self.triggers),
            dependencies=tuple(self.dependencies),
            description=self.description,
            files=dict(self.files),  # bytes are immutable; the dict is not
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
    files: Mapping[str, bytes] = field(default_factory=dict)

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

    ``owner_id`` is the user who created the project; it is fixed for the
    project's life. None means admin-only (see ``may_access_project``).
    """

    name: str
    draft: ProjectDraft = field(default_factory=ProjectDraft)
    status: ProjectStatus = ProjectStatus.DRAFT
    current_version: int = 0
    revision: int = 0
    timestamps: Timestamps = field(default_factory=Timestamps)
    owner_id: str | None = None

    def __post_init__(self) -> None:
        self.id = validate_id(self.id)
        if self.owner_id is not None:
            validate_id(self.owner_id)
        validate_name(self.name)
        self.status = ProjectStatus(self.status)
        if self.current_version < 0 or self.revision < 0:
            raise DomainValidationError("version counters must be >= 0")

    @classmethod
    def create(
        cls, name: str, *, description: str = "", owner_id: str | None = None
    ) -> "Project":
        validate_text(description, "description")
        return cls(
            id=new_id("proj"),
            name=name,
            draft=ProjectDraft(description=description),
            owner_id=owner_id,
        )

    def replace_draft(self, draft: ProjectDraft, *, name: str) -> None:
        """Replace the editable state wholesale (autosave semantics).

        Never touches published versions or the version counter. Rejects
        secret values in node/trigger config and text files (only credential
        references may be stored); the error names locations, never values.
        """
        validate_name(name)
        if unstorable := unstorable_locations(draft):
            raise DomainValidationError(
                "Project content must not contain NaN, Infinity or NUL characters,"
                " or unpaired surrogates.",
                details={"paths": unstorable},
            )
        validate_project_files(draft.files)
        leaks = plaintext_secret_locations(draft.graph, draft.triggers, draft.files)
        if leaks:
            raise PlaintextSecretError(
                "Project configuration and files must reference credentials"
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
