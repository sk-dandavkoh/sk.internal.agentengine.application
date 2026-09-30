"""Project package format (Phase 18): write/read round trip, determinism,
and the malformed and malicious packages ``read_package`` must reject with a
structured error (AGENTS §71/§104). No database: this is the format itself;
the use cases are covered in ``tests/integration/test_project_packages.py``.
"""

import hashlib
import io
import json
import stat
import zipfile
from dataclasses import replace
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from akasha.application.project_package import (
    FORMAT_VERSION,
    MANIFEST_FILE,
    MAX_PACKAGE_BYTES,
    PROJECT_FILE,
    NodeRequirement,
    ProjectPackage,
    ProjectPackageIncompatibleError,
    ProjectPackageInvalidError,
    ProjectPackageTooLargeError,
    Requirements,
    read_package,
    write_package,
)
from akasha.domain.graph import Edge, Graph, NodeInstance
from akasha.domain.primitives import new_id
from akasha.domain.project import (
    MAX_PROJECT_FILE_BYTES,
    MAX_PROJECT_FILES,
    PROJECT_SCHEMA_VERSION,
    Dependency,
    ProjectDraft,
)
from akasha.domain.trigger import Trigger, TriggerType
from akasha.errors import AkashaError
from tests.support import project_semantics

ENGINE = "0.1.0"


def read(data: bytes) -> ProjectPackage:
    return read_package(data, engine_version=ENGINE)


PACKAGE_ERRORS = (
    ProjectPackageInvalidError,
    ProjectPackageTooLargeError,
    ProjectPackageIncompatibleError,
)


def sample(config: dict[str, Any] | None = None) -> ProjectPackage:
    graph = Graph()
    source = NodeInstance.create("core.input", config={"field": "lead"}, label="in")
    llm = NodeInstance.create(
        "core.llm",
        config=config
        if config is not None
        else {
            "credential": "credential:llm-key",
            "system_prompt": "You classify leads. Größe: ü",
            "user_prompt": "Classify {{ lead.name }}",
            "temperature": 0.5,
            "nested": {"list": [1, 2.5, None, True, "x"]},
        },
        label="llm",
    )
    graph.add_node(source)
    graph.add_node(llm)
    graph.add_edge(Edge(source.id, "value", llm.id, "variables"))
    draft = ProjectDraft(
        graph=graph,
        triggers=[
            Trigger.create(TriggerType.MANUAL),
            Trigger(
                id=new_id("trig"),
                trigger_type=TriggerType.CRON,
                config={"cron": "0 * * * *"},
                enabled=False,
            ),
        ],
        dependencies=[Dependency("node", "core.llm", ">=1.0.0,<2.0.0")],
        description="Qualifies leads.",
        files={
            "docs/README.md": b"# Leads\n",
            "prompts/system.txt": "Sei höflich.".encode(),
            "resources/data/leads.csv": b"name\nAda\n",
            "assets/logo.png": bytes(range(256)),
        },
    )
    credentials = ["llm-key"] if config is None else []
    return ProjectPackage(
        name="Lead Qualification",
        draft=draft,
        requires=Requirements(
            nodes=[
                NodeRequirement(node_type="core.input", version="1.0.0"),
                NodeRequirement(node_type="core.llm", version="1.0.0"),
            ],
            credentials=credentials,
        ),
        engine_version="0.1.0",
        source_project_version=3,
    )


# One fixed package: fuzzing needs the same bytes on every example.
VALID = write_package(sample())


def unzip(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {i.filename: archive.read(i) for i in archive.infolist()}


def rezip(
    entries: dict[str, bytes],
    *,
    inventory: bool = True,
    infos: dict[str, zipfile.ZipInfo] | None = None,
) -> bytes:
    """Build an archive from ``entries``. ``inventory`` rewrites the
    manifest's file list so a test can change one thing at a time."""
    entries = dict(entries)
    if inventory and MANIFEST_FILE in entries:
        manifest = json.loads(entries[MANIFEST_FILE])
        manifest["files"] = [
            {"path": p, "size": len(c), "sha256": hashlib.sha256(c).hexdigest()}
            for p, c in entries.items()
            if p != MANIFEST_FILE
        ]
        entries[MANIFEST_FILE] = json.dumps(manifest).encode()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, content in entries.items():
            archive.writestr((infos or {}).get(path, path), content)
    return buffer.getvalue()


def valid_entries() -> dict[str, bytes]:
    return unzip(write_package(sample()))


def with_manifest(**changes: Any) -> bytes:
    entries = valid_entries()
    manifest = json.loads(entries[MANIFEST_FILE])
    for key, value in changes.items():
        if value is ...:
            del manifest[key]
        else:
            manifest[key] = value
    entries[MANIFEST_FILE] = json.dumps(manifest).encode()
    return rezip(entries, inventory="files" not in changes)


def with_project(change: Any) -> bytes:
    entries = valid_entries()
    document = json.loads(entries[PROJECT_FILE])
    change(document)
    entries[PROJECT_FILE] = json.dumps(document).encode()
    return rezip(entries)


# -- round trip ---------------------------------------------------------------


def test_round_trip_preserves_the_semantic_project() -> None:
    package = sample()
    back = read(write_package(package))
    assert project_semantics(back.name, back.draft) == project_semantics(
        package.name, package.draft
    )
    assert back.requires == package.requires
    assert back.engine_version == "0.1.0"
    assert back.source_project_version == 3
    assert back.project_schema_version == PROJECT_SCHEMA_VERSION


def test_export_is_deterministic_and_documented() -> None:
    package = sample()
    data = write_package(package)
    assert data == write_package(package)  # same project, same bytes
    entries = unzip(data)
    assert list(entries) == [
        MANIFEST_FILE,
        PROJECT_FILE,
        "assets/logo.png",
        "docs/README.md",
        "prompts/system.txt",
        "resources/data/leads.csv",
    ]
    manifest = json.loads(entries[MANIFEST_FILE])
    assert manifest["format"] == "akasha-project-package"
    assert manifest["format_version"] == FORMAT_VERSION
    assert manifest["requires"]["credentials"] == ["llm-key"]
    assert {f["path"] for f in manifest["files"]} == set(entries) - {MANIFEST_FILE}
    # Prompt templates travel as node configuration.
    project = json.loads(entries[PROJECT_FILE])
    configs = [n["config"] for n in project["graph"]["nodes"]]
    assert any(c.get("user_prompt") == "Classify {{ lead.name }}" for c in configs)


# Storable JSON: what a persisted project can hold (JSONB has no NaN,
# Infinity, NUL characters or unpaired surrogates; no stored config does).
_text = st.text(
    alphabet=st.characters(blacklist_characters="\0", blacklist_categories=["Cs"]),
    max_size=20,
)
_json = st.recursive(
    st.none()
    | st.booleans()
    | st.integers(-(2**53), 2**53)
    | st.floats(allow_nan=False, allow_infinity=False)
    | _text,
    lambda inner: (
        st.lists(inner, max_size=4) | st.dictionaries(_text, inner, max_size=4)
    ),
    max_leaves=20,
)


@settings(max_examples=60, deadline=None)
@given(config=st.dictionaries(_text, _json, max_size=5))
def test_round_trip_of_arbitrary_json_configuration(config: dict[str, Any]) -> None:
    package = sample(config)
    back = read(write_package(package))
    assert project_semantics(back.name, back.draft) == project_semantics(
        package.name, package.draft
    )


# -- fuzzing: malformed input fails safely -------------------------------------


@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(data=st.data())
def test_corrupted_packages_fail_with_package_errors_only(data: st.DataObject) -> None:
    raw = bytearray(VALID)
    for _ in range(data.draw(st.integers(1, 8))):
        index = data.draw(st.integers(0, len(raw) - 1))
        raw[index] = data.draw(st.integers(0, 255))
    try:
        read(bytes(raw))
    except PACKAGE_ERRORS:
        pass  # the only acceptable failure


@settings(max_examples=100, deadline=None)
@given(raw=st.binary(max_size=2048))
def test_arbitrary_bytes_fail_with_package_errors_only(raw: bytes) -> None:
    with pytest.raises(PACKAGE_ERRORS):
        read(raw)


# -- archive level ---------------------------------------------------------


@pytest.mark.parametrize("raw", [b"", b"not a zip", b"PK\x03\x04garbage"])
def test_not_a_zip(raw: bytes) -> None:
    with pytest.raises(ProjectPackageInvalidError):
        read(raw)


def test_oversized_package_rejected_before_parsing() -> None:
    with pytest.raises(ProjectPackageTooLargeError):
        read(b"\0" * (MAX_PACKAGE_BYTES + 1))


@pytest.mark.parametrize(
    "name",
    [
        "../evil.txt",
        "docs/../../evil.txt",
        "/etc/passwd",
        "docs\\..\\evil.txt",
        "C:/evil.txt",
        "docs/.hidden",
        "run.py",
        "nodes/custom.py",
    ],
)
def test_unexpected_or_traversal_entries_rejected(name: str) -> None:
    with pytest.raises(ProjectPackageInvalidError) as info:
        read(rezip(valid_entries() | {name: b"x"}))
    assert info.value.details["path"] == name


def test_duplicate_entry_rejected() -> None:
    entries = valid_entries()
    buffer = io.BytesIO(rezip(entries))
    with (
        pytest.warns(UserWarning, match="Duplicate name"),
        zipfile.ZipFile(buffer, "a") as archive,
    ):
        archive.writestr("docs/README.md", b"shadow")
    with pytest.raises(ProjectPackageInvalidError, match="duplicate"):
        read(buffer.getvalue())


def test_symlink_entry_rejected() -> None:
    link = zipfile.ZipInfo("docs/link.md")
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    data = rezip(
        valid_entries() | {"docs/link.md": b"/etc/passwd"}, infos={"docs/link.md": link}
    )
    with pytest.raises(ProjectPackageInvalidError, match="regular file"):
        read(data)


def _central_record(data: bytearray, name: bytes) -> int:
    """Offset of ``name``'s central directory record."""
    offset = -1
    while True:
        offset = data.find(b"PK\x01\x02", offset + 1)
        assert offset >= 0, name
        if data[offset + 46 : offset + 46 + len(name)] == name:
            return offset


def test_encrypted_entry_rejected() -> None:
    # zipfile never writes the encryption flag; set it in the central
    # directory record the reader trusts.
    data = bytearray(rezip(valid_entries() | {"docs/secret.md": b"x"}))
    data[_central_record(data, b"docs/secret.md") + 8] |= 0x1
    with pytest.raises(ProjectPackageInvalidError, match="encrypted"):
        read(bytes(data))


def test_unsupported_compression_rejected() -> None:
    info = zipfile.ZipInfo("docs/b.md")
    info.compress_type = zipfile.ZIP_BZIP2
    data = rezip(valid_entries() | {"docs/b.md": b"x"}, infos={"docs/b.md": info})
    with pytest.raises(ProjectPackageInvalidError, match="compression"):
        read(data)


def test_directory_entries_are_tolerated() -> None:
    entries = valid_entries()
    buffer = io.BytesIO(rezip(entries))
    with zipfile.ZipFile(buffer, "a") as archive:
        archive.mkdir("docs")
    assert read(buffer.getvalue()).name == "Lead Qualification"


def test_too_many_entries_rejected() -> None:
    buffer = io.BytesIO(rezip(valid_entries()))
    with zipfile.ZipFile(buffer, "a") as archive:
        for i in range(600):
            archive.mkdir(f"docs/d{i}")
    with pytest.raises(ProjectPackageInvalidError, match="too many"):
        read(buffer.getvalue())


def test_zip_bomb_rejected_by_declared_size() -> None:
    bomb = b"\0" * (MAX_PROJECT_FILE_BYTES * 20)  # compresses to a few KiB
    data = rezip(valid_entries() | {"assets/bomb.bin": bomb})
    assert len(data) < 200_000
    with pytest.raises(ProjectPackageTooLargeError):
        read(data)


def test_zip_bomb_with_a_lying_size_header_rejected() -> None:
    """The central directory claims 10 bytes; the entry inflates to 2 MiB."""
    real = b"\0" * (2 * MAX_PROJECT_FILE_BYTES)
    data = bytearray(rezip(valid_entries() | {"assets/bomb.bin": real}))
    central = _central_record(data, b"assets/bomb.bin")
    data[central + 24 : central + 28] = (10).to_bytes(4, "little")
    with pytest.raises(PACKAGE_ERRORS):
        read(bytes(data))


def test_oversized_single_file_rejected() -> None:
    data = rezip(
        valid_entries() | {"assets/big.bin": b"\1" * (MAX_PROJECT_FILE_BYTES + 1)}
    )
    with pytest.raises(ProjectPackageTooLargeError) as info:
        read(data)
    assert info.value.details == {"path": "assets/big.bin"}


# -- manifest --------------------------------------------------------------


def test_missing_manifest() -> None:
    entries = valid_entries()
    del entries[MANIFEST_FILE]
    with pytest.raises(ProjectPackageInvalidError, match=MANIFEST_FILE):
        read(rezip(entries))


@pytest.mark.parametrize(
    "content", [b"{", b"[]", b"\xff\xfe", b"null", b"[" * 100_000 + b"]" * 100_000]
)
def test_manifest_not_a_json_object(content: bytes) -> None:
    entries = valid_entries() | {MANIFEST_FILE: content}
    with pytest.raises(ProjectPackageInvalidError):
        read(rezip(entries, inventory=False))


@pytest.mark.parametrize("value", ["zip", None, 1])
def test_foreign_format_rejected(value: Any) -> None:
    with pytest.raises(ProjectPackageInvalidError, match="not an Akasha"):
        read(with_manifest(format=value))


@pytest.mark.parametrize("version", ["1", 1.0, True, None])
def test_malformed_format_version(version: Any) -> None:
    with pytest.raises(ProjectPackageInvalidError, match="malformed"):
        read(with_manifest(format_version=version))


@pytest.mark.parametrize("version", [0, 2, 99])
def test_unsupported_format_version(version: int) -> None:
    with pytest.raises(ProjectPackageIncompatibleError) as info:
        read(with_manifest(format_version=version))
    assert info.value.details["supported"] == [FORMAT_VERSION]


def test_newer_format_is_unsupported_even_if_its_shape_changed() -> None:
    with pytest.raises(ProjectPackageIncompatibleError):
        read(with_manifest(format_version=2, requires=..., brand_new={}))


@pytest.mark.parametrize("version", [0, 2])
def test_unsupported_project_schema_version(version: int) -> None:
    with pytest.raises(ProjectPackageIncompatibleError, match="schema version"):
        read(with_manifest(project_schema_version=version))


@pytest.mark.parametrize(
    "changes",
    [
        {"unknown_field": 1},
        {"requires": ...},
        {"engine_version": 1},
        {"source_project_version": 0},
        {"requires": {"nodes": [], "credentials": [], "plugins": []}},
    ],
)
def test_manifest_schema_violations(changes: dict[str, Any]) -> None:
    with pytest.raises(ProjectPackageInvalidError, match="schema"):
        read(with_manifest(**changes))


def test_schema_errors_do_not_echo_input_values() -> None:
    with pytest.raises(ProjectPackageInvalidError) as info:
        read(with_manifest(engine_version={"sk-leaked-value-123456": 1}))
    assert "sk-leaked" not in json.dumps(info.value.details)


# -- inventory ---------------------------------------------------------------


def test_unlisted_entry_rejected() -> None:
    entries = valid_entries() | {"docs/extra.md": b"x"}
    with pytest.raises(ProjectPackageInvalidError, match="inventory") as info:
        read(rezip(entries, inventory=False))
    assert info.value.details["unlisted"] == ["docs/extra.md"]


def test_listed_but_missing_entry_rejected() -> None:
    manifest_listed = valid_entries()
    manifest = manifest_listed.pop(MANIFEST_FILE)
    del manifest_listed["docs/README.md"]
    with pytest.raises(ProjectPackageInvalidError, match="inventory") as info:
        read(rezip({MANIFEST_FILE: manifest, **manifest_listed}, inventory=False))
    assert info.value.details["missing"] == ["docs/README.md"]


@pytest.mark.parametrize("path", [PROJECT_FILE, "prompts/system.txt"])
def test_tampered_content_fails_the_checksum(path: str) -> None:
    entries = valid_entries()
    entries[path] = entries[path].replace(b"e", b"E", 1)
    with pytest.raises(ProjectPackageInvalidError, match="checksum") as info:
        read(rezip(entries, inventory=False))
    assert info.value.details == {"path": path}


def test_duplicate_inventory_listing_rejected() -> None:
    entries = valid_entries()
    manifest = json.loads(entries[MANIFEST_FILE])
    manifest["files"].append(manifest["files"][0])
    entries[MANIFEST_FILE] = json.dumps(manifest).encode()
    with pytest.raises(ProjectPackageInvalidError, match="inventory"):
        read(rezip(entries, inventory=False))


def test_missing_project_document() -> None:
    entries = valid_entries()
    del entries[PROJECT_FILE]
    with pytest.raises(ProjectPackageInvalidError, match=PROJECT_FILE):
        read(rezip(entries))


# -- project document ------------------------------------------------------


def _node(doc: dict[str, Any]) -> dict[str, Any]:
    node: dict[str, Any] = doc["graph"]["nodes"][0]
    return node


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda d: d.update(extra=1), "schema"),
        (lambda d: d.update(name=""), "schema"),
        (lambda d: d["triggers"][0].update(enabled="yes"), "schema"),
        (lambda d: _node(d).update(id="../../etc"), "malformed"),
        (lambda d: _node(d).update(unexpected="x"), "schema"),
        (lambda d: d["triggers"][0].update(trigger_type="shell"), "malformed"),
        (
            lambda d: d["graph"]["edges"][0].update(target_node_id=new_id("node")),
            "malformed",
        ),
        (lambda d: d["graph"]["nodes"].append(dict(_node(d))), "malformed"),
        (lambda d: d["dependencies"].append({"kind": "shell", "name": "x"}), "schema"),
        (
            lambda d: d["triggers"][1].update(config={}),
            "malformed",
        ),  # cron without cron
    ],
)
def test_project_document_violations(change: Any, message: str) -> None:
    with pytest.raises(ProjectPackageInvalidError, match=message):
        read(with_project(change))


def test_deeply_nested_configuration_fails_safely() -> None:
    entries = valid_entries()
    text = entries[PROJECT_FILE].decode()
    deep = '"field": ' + "[" * 50_000 + "]" * 50_000
    entries[PROJECT_FILE] = text.replace('"field": "lead"', deep).encode()
    with pytest.raises(ProjectPackageInvalidError):
        read(rezip(entries))


# -- manifest must describe the content honestly --------------------------------


@pytest.mark.parametrize(
    "requires",
    [
        {
            "nodes": [{"node_type": "core.input", "version": "1.0.0"}],
            "credentials": ["llm-key"],
        },
        {
            "nodes": [
                {"node_type": "core.input", "version": "1.0.0"},
                {"node_type": "core.llm", "version": "1.0.0"},
                {"node_type": "core.http", "version": "1.0.0"},
            ],
            "credentials": ["llm-key"],
        },
        {
            "nodes": [
                {"node_type": "core.input", "version": "1.0.0"},
                {"node_type": "core.llm", "version": "1.0.0"},
                {"node_type": "core.llm", "version": "1.0.0"},
            ],
            "credentials": ["llm-key"],
        },
        {
            "nodes": [
                {"node_type": "core.input", "version": "1.0.0"},
                {"node_type": "core.llm", "version": "1.0.0"},
            ],
            "credentials": [],
        },
    ],
)
def test_requirements_must_match_the_project(requires: dict[str, Any]) -> None:
    with pytest.raises(ProjectPackageInvalidError, match="requirements"):
        read(with_manifest(requires=requires))


def test_invalid_project_file_path_in_inventory_is_an_unexpected_entry() -> None:
    with pytest.raises(ProjectPackageInvalidError, match="unexpected"):
        read(rezip(valid_entries() | {"docs/a b.md": b"x"}))


def test_every_package_error_is_an_akasha_error_with_a_stable_code() -> None:
    for error in PACKAGE_ERRORS:
        assert issubclass(error, AkashaError)
    assert {e.code for e in PACKAGE_ERRORS} == {
        "PROJECT_PACKAGE_INVALID",
        "PROJECT_PACKAGE_TOO_LARGE",
        "PROJECT_PACKAGE_INCOMPATIBLE",
    }


# -- security review regressions ---------------------------------------------


@pytest.mark.parametrize(
    "value",
    ["NaN", "Infinity", "-Infinity", "1e999", '"a\\u0000b"', '"\\ud800"'],
)
def test_values_postgresql_cannot_store_are_rejected(value: str) -> None:
    """JSONB has no NaN/Infinity and no NUL in text: accepting them would
    fail later, at persistence, as an internal error."""
    entries = valid_entries()
    entries[PROJECT_FILE] = (
        entries[PROJECT_FILE].decode().replace('"lead"', value, 1).encode()
    )
    with pytest.raises(ProjectPackageInvalidError, match="JSON"):
        read(rezip(entries))


def test_nul_in_a_key_is_rejected() -> None:
    with pytest.raises(ProjectPackageInvalidError, match="JSON"):
        read(with_project(lambda d: _node(d)["config"].update({"a\0": 1})))


def test_undecodable_utf8_entry_name_is_invalid_not_a_crash() -> None:
    data = bytearray(rezip(valid_entries() | {"docs/abc.md": b"x"}))
    central = _central_record(data, b"docs/abc.md")
    data[central + 9] |= 0x08  # flag bit 11: the entry name is UTF-8
    name_at = central + 46 + len(b"docs/")
    data[name_at] = 0xFF  # not valid UTF-8
    with pytest.raises(PACKAGE_ERRORS):
        read(bytes(data))


def test_too_many_files_rejected_before_reading() -> None:
    files = {f"docs/{i}.md": b"" for i in range(MAX_PROJECT_FILES + 1)}
    with pytest.raises(ProjectPackageInvalidError, match="too many files"):
        read(rezip(valid_entries() | files))


def test_whatever_a_project_may_hold_exports() -> None:
    """The format imposes no caps stricter than the domain's."""
    package = sample()
    node = next(iter(package.draft.graph.nodes.values()))
    node.label = "L" * 5_000
    long = ProjectPackage(
        name="N" * 1_000,
        draft=replace(package.draft, description="D" * 50_000),
        requires=package.requires,
        engine_version="0.1.0",
    )
    back = read(write_package(long))
    assert project_semantics(back.name, back.draft) == project_semantics(
        long.name, long.draft
    )


# -- engine compatibility ------------------------------------------------------


def test_export_records_the_exporting_release_line() -> None:
    manifest = json.loads(unzip(VALID)[MANIFEST_FILE])
    assert manifest["engine_compatibility"] == ">=0.1.0"
    newer = replace(sample(), engine_version="1.4.2")
    manifest = json.loads(unzip(write_package(newer))[MANIFEST_FILE])
    assert manifest["engine_compatibility"] == ">=1.4.0"


@pytest.mark.parametrize(
    ("constraint", "engine", "ok"),
    [
        (">=0.1.0", "0.1.0", True),
        (">=0.1.0", "0.1.7", True),
        (">=0.2.0", "0.1.9", False),  # exported by a newer release line
        (">=0.1.0,<0.2.0", "0.2.0", False),
        ("*", "9.9.9", True),
    ],
)
def test_engine_compatibility_is_enforced(
    constraint: str, engine: str, ok: bool
) -> None:
    data = with_manifest(engine_compatibility=constraint)
    if ok:
        assert read_package(data, engine_version=engine).name
        return
    with pytest.raises(ProjectPackageIncompatibleError, match="Engine version") as info:
        read_package(data, engine_version=engine)
    assert info.value.details == {
        "engine_version": engine,
        "engine_compatibility": constraint,
    }


@pytest.mark.parametrize("constraint", ["~1.0", ">=one", "1.0"])
def test_malformed_engine_compatibility(constraint: str) -> None:
    with pytest.raises(ProjectPackageInvalidError, match="malformed"):
        read(with_manifest(engine_compatibility=constraint))


@pytest.mark.parametrize(
    ("offset", "patch"),
    [
        (6, lambda b: 64),  # "version needed to extract" 6.4
        (8, lambda b: b | 0x20),  # flag bit 5: compressed patched data
        (8, lambda b: b | 0x40),  # flag bit 6: strong encryption
    ],
    ids=["version-6.4", "patched-data", "strong-encryption"],
)
def test_zip_features_zipfile_cannot_read_are_invalid_not_a_crash(
    offset: int, patch: Any
) -> None:
    """``zipfile`` raises NotImplementedError for these central-directory
    values; the fuzz test found that it escaped as a 500 (bug B-9)."""
    data = bytearray(VALID)
    record = _central_record(data, MANIFEST_FILE.encode())
    data[record + offset] = patch(data[record + offset])
    with pytest.raises(ProjectPackageInvalidError):
        read(bytes(data))
