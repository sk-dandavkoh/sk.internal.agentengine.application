"""Project Package: the portable format of a Project (architecture §56/§57,
format guide ``docs/project-format.md``).

A package is a ZIP archive::

    manifest.json   format + schema versions, what the project requires
                    (node types with versions, credential names) and an
                    integrity inventory (size + SHA-256 of every other entry)
    project.json    name, description, graph (node configuration, prompt
                    templates included), triggers, declared dependencies
    docs/ prompts/ resources/ assets/
                    project files, byte for byte

Roadmap names: ``ProjectPackage`` and ``ProjectManifest`` live here, and
``read_package`` is the SchemaValidator. ProjectExporter, ProjectImporter
and DependencyValidator are ``ProjectPackageService`` in
``akasha.application.packages``.

``read_package`` is a trust boundary: the bytes come from anywhere. Nothing
is extracted to disk. Entry names, entry types, sizes (declared and actual,
against zip bombs), JSON documents (strict schemas, unknown fields rejected)
and the inventory are all checked before any domain object is built. Every
failure is a package error (``PROJECT_PACKAGE_*``), never an internal error:
the byte-mutation fuzz test in ``tests/application/test_project_package.py``
guards that promise.

Writing is deterministic (AGENTS §72): the same project yields the same bytes
(fixed entry order and timestamps, sorted JSON keys, no export time).
"""

import hashlib
import io
import json
import stat
import zipfile
import zlib
from dataclasses import asdict, dataclass
from typing import Annotated, Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from akasha.domain.credentials import REFERENCE_PREFIX, credential_references
from akasha.domain.graph import Edge, Graph, NodeInstance, Position
from akasha.domain.graph_validation import ValidationIssue
from akasha.domain.primitives import is_storable, semver_key, version_satisfies
from akasha.domain.project import (
    MAX_PROJECT_FILE_BYTES,
    MAX_PROJECT_FILES,
    PROJECT_SCHEMA_VERSION,
    Dependency,
    ProjectDraft,
    is_project_file_path,
    validate_project_files,
)
from akasha.domain.trigger import Trigger, TriggerType
from akasha.errors import AkashaError, DomainValidationError

FORMAT: Final = "akasha-project-package"
FORMAT_VERSION: Final = 1
MANIFEST_FILE: Final = "manifest.json"
PROJECT_FILE: Final = "project.json"
MEDIA_TYPE: Final = "application/zip"

MAX_PACKAGE_BYTES: Final = 16 * 1024 * 1024
_MAX_MANIFEST_BYTES = 256 * 1024
_MAX_PROJECT_DOCUMENT_BYTES = 4 * 1024 * 1024
# Unpacked total: documents plus all project files. Checked on the declared
# sizes before reading and on the actual bytes while reading.
_MAX_UNPACKED_BYTES = (
    _MAX_MANIFEST_BYTES + _MAX_PROJECT_DOCUMENT_BYTES + 4 * 1024 * 1024
)
_MAX_ENTRIES = 512  # directory entries included
# A fixed timestamp keeps archives byte-identical across exports.
_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)


class ProjectPackageInvalidError(AkashaError):
    """Not a well-formed project package (archive, schema or integrity)."""

    code = "PROJECT_PACKAGE_INVALID"
    http_status = 422


class ProjectPackageTooLargeError(AkashaError):
    code = "PROJECT_PACKAGE_TOO_LARGE"
    http_status = 413


class ProjectPackageIncompatibleError(AkashaError):
    """A well-formed package this installation cannot take: unsupported
    format/schema version, or requirements (node types, plugins, versions)
    it does not meet."""

    code = "PROJECT_PACKAGE_INCOMPATIBLE"
    http_status = 422

    def __init__(
        self,
        message: str,
        *,
        issues: tuple[ValidationIssue, ...] = (),
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(
            message,
            details={
                **(details or {}),
                **(
                    {
                        "issues": [
                            {"code": i.code.value, "message": i.message} for i in issues
                        ]
                    }
                    if issues
                    else {}
                ),
            },
        )
        self.issues = issues


# -- schemas (format version 1) ------------------------------------------

# No length caps beyond the domain's: whatever a project may hold must
# export. The document size limit bounds untrusted input.
Text = Annotated[str, Field(min_length=1)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class NodeRequirement(_Strict):
    node_type: Text
    version: str = Field(min_length=1, max_length=128)  # at export time


class Requirements(_Strict):
    nodes: list[NodeRequirement] = Field(default_factory=list)
    # Credential names the configuration references (credential:<name>).
    # Names only: the target installation provides the values.
    credentials: list[Text] = Field(default_factory=list)


class FileEntry(_Strict):
    path: str = Field(min_length=1, max_length=300)
    size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ProjectManifest(_Strict):
    format: Literal["akasha-project-package"]
    format_version: int
    project_schema_version: int
    engine_version: str = Field(min_length=1, max_length=128)  # the exporter
    # Engine versions that may import this package (version constraint,
    # ``version_satisfies`` grammar). Export writes ">=MAJOR.MINOR.0" of
    # the exporting Engine: an older release line may lack what it relied on.
    engine_compatibility: str = Field(min_length=1, max_length=128)
    # The exported published version; None for a draft export.
    source_project_version: int | None = Field(default=None, ge=1)
    requires: Requirements
    files: list[FileEntry]


class _Position(_Strict):
    x: float = Field(allow_inf_nan=False)
    y: float = Field(allow_inf_nan=False)


class _Node(_Strict):
    id: str
    node_type: Text
    config: dict[str, JsonValue] = Field(default_factory=dict)
    label: str = ""
    position: _Position | None = None  # editor layout; optional


class _Edge(_Strict):
    source_node_id: str
    source_port: str
    target_node_id: str
    target_port: str


class _Graph(_Strict):
    nodes: list[_Node] = Field(default_factory=list)
    edges: list[_Edge] = Field(default_factory=list)


class _Trigger(_Strict):
    id: str
    trigger_type: str
    config: dict[str, JsonValue] = Field(default_factory=dict)
    enabled: bool = True


class _Dependency(_Strict):
    kind: Literal["node", "plugin"]
    name: Text
    version_constraint: str = Field(default="*", min_length=1, max_length=128)


class ProjectDocument(_Strict):
    name: Text
    description: str = ""
    graph: _Graph
    triggers: list[_Trigger] = Field(default_factory=list)
    dependencies: list[_Dependency] = Field(default_factory=list)


# -- the package ------------------------------------------------------------


@dataclass(frozen=True)
class ProjectPackage:
    """A project's portable content plus what it requires. Never holds
    secret values: configs carry credential references only."""

    name: str
    draft: ProjectDraft
    requires: Requirements
    engine_version: str
    source_project_version: int | None = None
    project_schema_version: int = PROJECT_SCHEMA_VERSION
    engine_compatibility: str | None = None  # None: derived from engine_version


def write_package(package: ProjectPackage) -> bytes:
    draft = package.draft
    document = ProjectDocument(
        name=package.name,
        description=draft.description,
        graph=_Graph(
            nodes=list(
                _Node(
                    id=n.id,
                    node_type=n.node_type,
                    config=n.config,
                    label=n.label,
                    position=None
                    if n.position is None
                    else _Position(x=n.position.x, y=n.position.y),
                )
                for n in sorted(draft.graph.nodes.values(), key=lambda n: n.id)
            ),
            edges=list(
                _Edge(
                    source_node_id=e.source_node_id,
                    source_port=e.source_port,
                    target_node_id=e.target_node_id,
                    target_port=e.target_port,
                )
                for e in draft.graph.edges
            ),
        ),
        triggers=list(
            _Trigger(
                id=t.id,
                trigger_type=t.trigger_type.value,
                config=t.config,
                enabled=t.enabled,
            )
            for t in draft.triggers
        ),
        dependencies=list(
            _Dependency.model_validate(asdict(d)) for d in draft.dependencies
        ),
    )
    entries = {PROJECT_FILE: _json_bytes(document.model_dump(mode="json"))}
    entries |= {path: draft.files[path] for path in sorted(draft.files)}
    manifest = ProjectManifest(
        format=FORMAT,
        format_version=FORMAT_VERSION,
        project_schema_version=package.project_schema_version,
        engine_version=package.engine_version,
        engine_compatibility=package.engine_compatibility
        or ">={}.{}.0".format(*semver_key(package.engine_version)[:2]),
        source_project_version=package.source_project_version,
        requires=package.requires,
        files=list(
            FileEntry(path=p, size=len(c), sha256=hashlib.sha256(c).hexdigest())
            for p, c in entries.items()
        ),
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path, content in {
            MANIFEST_FILE: _json_bytes(manifest.model_dump(mode="json")),
            **entries,
        }.items():
            info = zipfile.ZipInfo(path, date_time=_ZIP_EPOCH)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, content)
    return buffer.getvalue()


def read_package(data: bytes, *, engine_version: str) -> ProjectPackage:
    """Parse and verify a package for the Engine at ``engine_version``.

    Raises ``ProjectPackageInvalidError``, ``ProjectPackageTooLargeError``
    or ``ProjectPackageIncompatibleError`` (unsupported format or schema
    version, or an Engine outside ``engine_compatibility``); node and
    plugin requirements are checked by the importer against the
    installation."""
    if len(data) > MAX_PACKAGE_BYTES:
        raise ProjectPackageTooLargeError(
            f"A project package must not exceed {MAX_PACKAGE_BYTES} bytes."
        )
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = _read_entries(archive)
    # ValueError: e.g. an entry name flagged UTF-8 that does not decode.
    # NotImplementedError: ZIP features zipfile cannot read (a "version
    # needed" above 6.3, strong encryption, patched data).
    except (
        zipfile.BadZipFile,
        zipfile.LargeZipFile,
        zlib.error,
        EOFError,
        ValueError,
        NotImplementedError,
    ) as exc:
        raise ProjectPackageInvalidError(
            "The package is not a valid ZIP archive."
        ) from exc

    manifest_bytes = entries.pop(MANIFEST_FILE, None)
    if manifest_bytes is None:
        raise ProjectPackageInvalidError(f"The package has no {MANIFEST_FILE}.")
    manifest = _read_manifest(manifest_bytes, engine_version)
    _verify_inventory(manifest, entries)

    document = _validated(
        ProjectDocument, _json_document(entries.pop(PROJECT_FILE), PROJECT_FILE)
    )
    try:
        draft = _to_draft(document, files=entries)
    except ValueError as exc:  # DomainValidationError included
        raise ProjectPackageInvalidError(
            "The project in the package is malformed.", details={"reason": str(exc)}
        ) from exc
    _verify_requirements(manifest.requires, draft)
    return ProjectPackage(
        name=document.name,
        draft=draft,
        requires=manifest.requires,
        engine_version=manifest.engine_version,
        source_project_version=manifest.source_project_version,
        project_schema_version=manifest.project_schema_version,
        engine_compatibility=manifest.engine_compatibility,
    )


# -- reading ------------------------------------------------------------------


def _read_entries(archive: zipfile.ZipFile) -> dict[str, bytes]:
    infos = archive.infolist()
    if len(infos) > _MAX_ENTRIES:
        raise ProjectPackageInvalidError("The package has too many entries.")
    if sum(not i.is_dir() for i in infos) > MAX_PROJECT_FILES + 2:
        raise ProjectPackageInvalidError("The package has too many files.")
    declared = sum(i.file_size for i in infos)
    if declared > _MAX_UNPACKED_BYTES:
        raise ProjectPackageTooLargeError("The package unpacks to too many bytes.")
    entries: dict[str, bytes] = {}
    for info in infos:
        name = info.filename
        if info.is_dir():
            if info.file_size:
                raise _entry_error("directory entry with content", name)
            continue  # zip tools add them; they carry nothing
        if name in (MANIFEST_FILE, PROJECT_FILE):
            limit = (
                _MAX_MANIFEST_BYTES
                if name == MANIFEST_FILE
                else _MAX_PROJECT_DOCUMENT_BYTES
            )
        elif is_project_file_path(name):
            limit = MAX_PROJECT_FILE_BYTES
        else:
            raise _entry_error("unexpected entry", name)
        if name in entries:
            raise _entry_error("duplicate entry", name)
        if stat.S_IFMT(info.external_attr >> 16) not in (0, stat.S_IFREG):
            raise _entry_error("not a regular file", name)  # symlinks, devices
        if info.flag_bits & 0x1:
            raise _entry_error("encrypted entry", name)
        if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            raise _entry_error("unsupported compression", name)
        if info.file_size > limit:
            raise ProjectPackageTooLargeError(
                "A package entry exceeds its size limit.", details={"path": name}
            )
        # Declared sizes can lie: read at most one byte past the limit. A
        # complete read also verifies the entry's CRC.
        with archive.open(info) as stream:
            content = stream.read(limit + 1)
        if len(content) > limit:
            raise ProjectPackageTooLargeError(
                "A package entry exceeds its size limit.", details={"path": name}
            )
        entries[name] = content
    return entries


def _entry_error(reason: str, name: str) -> ProjectPackageInvalidError:
    return ProjectPackageInvalidError(
        f"The package contains an invalid entry ({reason}).",
        details={"path": name[:300]},
    )


def _read_manifest(content: bytes, engine_version: str) -> ProjectManifest:
    raw = _json_document(content, MANIFEST_FILE)
    # Versions first: a newer format may have a different shape, and must
    # be reported as unsupported rather than as a schema error.
    if raw.get("format") != FORMAT:
        raise ProjectPackageInvalidError("The file is not an Akasha project package.")
    format_version = raw.get("format_version")
    if not isinstance(format_version, int) or isinstance(format_version, bool):
        raise ProjectPackageInvalidError("The package format version is malformed.")
    if format_version != FORMAT_VERSION:
        raise ProjectPackageIncompatibleError(
            "The package format version is not supported.",
            details={"format_version": format_version, "supported": [FORMAT_VERSION]},
        )
    manifest = _validated(ProjectManifest, raw)
    # ponytail: only schema 1 exists, so there is no migration to run; an
    # older schema gets a migration step here when schema 2 is introduced.
    if manifest.project_schema_version != PROJECT_SCHEMA_VERSION:
        raise ProjectPackageIncompatibleError(
            "The project schema version is not supported.",
            details={
                "project_schema_version": manifest.project_schema_version,
                "supported": [PROJECT_SCHEMA_VERSION],
            },
        )
    try:
        compatible = version_satisfies(engine_version, manifest.engine_compatibility)
    except DomainValidationError as exc:
        raise ProjectPackageInvalidError(
            "The package engine compatibility constraint is malformed."
        ) from exc
    if not compatible:
        raise ProjectPackageIncompatibleError(
            "This Engine version cannot import the package.",
            details={
                "engine_version": engine_version,
                "engine_compatibility": manifest.engine_compatibility,
            },
        )
    return manifest


def _verify_inventory(manifest: ProjectManifest, entries: dict[str, bytes]) -> None:
    """The manifest lists exactly the other entries, with matching bytes."""
    listed = {f.path: f for f in manifest.files}
    if len(listed) != len(manifest.files) or listed.keys() != entries.keys():
        raise ProjectPackageInvalidError(
            "The package contents do not match the manifest inventory.",
            details={
                "unlisted": sorted(entries.keys() - listed.keys())[:20],
                "missing": sorted(listed.keys() - entries.keys())[:20],
            },
        )
    if PROJECT_FILE not in entries:
        raise ProjectPackageInvalidError(f"The package has no {PROJECT_FILE}.")
    for path, content in entries.items():
        entry = listed[path]
        if entry.size != len(content) or (
            entry.sha256 != hashlib.sha256(content).hexdigest()
        ):
            raise ProjectPackageInvalidError(
                "A package entry does not match its manifest checksum.",
                details={"path": path},
            )


def _verify_requirements(requires: Requirements, draft: ProjectDraft) -> None:
    """The manifest must describe the content honestly: the importer checks
    the installation against ``requires``, so it must name every node type
    the graph uses, once, and nothing else."""
    declared = [r.node_type for r in requires.nodes]
    used = {n.node_type for n in draft.graph.nodes.values()}
    if len(declared) != len(set(declared)) or set(declared) != used:
        raise ProjectPackageInvalidError(
            "The manifest node requirements do not match the project graph."
        )
    configs = [n.config for n in draft.graph.nodes.values()]
    configs += [t.config for t in draft.triggers]
    if set(requires.credentials) != credential_names(configs) or len(
        requires.credentials
    ) != len(set(requires.credentials)):
        raise ProjectPackageInvalidError(
            "The manifest credential requirements do not match the project."
        )


def credential_names(configs: list[dict[str, Any]]) -> set[str]:
    """Names of the credentials the configs reference."""
    return {
        ref.removeprefix(REFERENCE_PREFIX) for ref in credential_references(configs)
    }


def _to_draft(document: ProjectDocument, *, files: dict[str, bytes]) -> ProjectDraft:
    graph = Graph()
    for n in document.graph.nodes:
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
    for e in document.graph.edges:
        graph.add_edge(Edge(**e.model_dump()))
    validate_project_files(files)
    return ProjectDraft(
        graph=graph,
        triggers=[
            Trigger(
                id=t.id,
                trigger_type=TriggerType(t.trigger_type),
                config=dict(t.config),
                enabled=t.enabled,
            )
            for t in document.triggers
        ],
        dependencies=[Dependency(**d.model_dump()) for d in document.dependencies],
        description=document.description,
        files=files,
    )


def _json_document(content: bytes, name: str) -> dict[str, Any]:
    """Strict JSON: only what can be stored (``is_storable``: no NaN,
    Infinity or NUL), so an accepted package never fails at persistence."""
    try:
        value = json.loads(content.decode("utf-8"))
        if not is_storable(value):
            raise ValueError("not storable")
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise ProjectPackageInvalidError(
            f"{name} is not valid UTF-8 JSON.", details={"path": name}
        ) from exc
    if not isinstance(value, dict):
        raise ProjectPackageInvalidError(
            f"{name} must be a JSON object.", details={"path": name}
        )
    return value


def _validated[M: BaseModel](model: type[M], raw: dict[str, Any]) -> M:
    try:
        return model.model_validate(raw)
    except (ValidationError, RecursionError) as exc:
        # Locations and messages only: pydantic's ``input`` echoes values.
        errors = exc.errors() if isinstance(exc, ValidationError) else []
        raise ProjectPackageInvalidError(
            "The package does not match the project package schema.",
            details={
                "errors": [
                    {"loc": ".".join(str(p) for p in e["loc"]), "msg": e["msg"]}
                    for e in errors[:20]
                ]
            },
        ) from exc


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    ).encode("utf-8")
