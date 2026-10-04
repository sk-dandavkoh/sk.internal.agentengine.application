"""Plugin upload archives (``akasha.plugins.archive``): unpacking never
writes outside the target and rejects archives that are not plain trees of
regular files. Real ZIPs in temporary directories."""

import io
import stat
import zipfile
from pathlib import Path

import pytest

from akasha.plugins import archive
from akasha.plugins.archive import unpack_package
from akasha.plugins.registry import PluginPackageError

MANIFEST = "akasha-plugin.json"


def make_zip(*entries: tuple[str | zipfile.ZipInfo, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, data in entries:
            zf.writestr(name, data)
    return buffer.getvalue()


def symlink(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name)
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    return info


@pytest.fixture
def target(tmp_path: Path) -> Path:
    path = tmp_path / "unpacked"
    path.mkdir()
    return path


def test_unpacks_a_package_at_the_root(target: Path) -> None:
    data = make_zip((MANIFEST, b"{}"), ("sub/", b""), ("sub/x.py", b"x"))

    assert unpack_package(data, target) == target
    assert (target / MANIFEST).read_bytes() == b"{}"
    assert (target / "sub" / "x.py").read_bytes() == b"x"


def test_unpacks_a_package_in_one_top_level_directory(target: Path) -> None:
    data = make_zip(("pkg/" + MANIFEST, b"{}"), ("pkg/__init__.py", b""))

    assert unpack_package(data, target) == target / "pkg"


@pytest.mark.parametrize(
    "entries",
    [
        [("a/x", b""), ("b/y", b"")],  # two roots, no manifest
        [("x.py", b"")],  # no manifest at all
    ],
)
def test_requires_a_manifest_at_root_or_one_directory(
    target: Path, entries: list[tuple[str, bytes]]
) -> None:
    with pytest.raises(PluginPackageError, match=r"no akasha-plugin\.json"):
        unpack_package(make_zip(*entries), target)


@pytest.mark.parametrize(
    "name",
    [
        "../evil.py",
        "pkg/../../evil.py",
        "/etc/evil.py",
        "pkg\\..\\evil.py",
        "C:/evil.py",
        "pkg/./x.py",
        "pkg//x.py",
    ],
)
def test_rejects_unsafe_paths(tmp_path: Path, target: Path, name: str) -> None:
    with pytest.raises(PluginPackageError) as info:
        unpack_package(make_zip((name, b"x")), target)

    assert info.value.details == {"path": name}
    assert not (tmp_path / "evil.py").exists()
    assert list(target.rglob("*")) == []


def test_rejects_symlinks(target: Path) -> None:
    data = make_zip((MANIFEST, b"{}"), (symlink("link"), b"/etc/passwd"))

    with pytest.raises(PluginPackageError, match="not a regular file"):
        unpack_package(data, target)


def test_rejects_duplicates_and_file_directory_clashes(target: Path) -> None:
    with pytest.raises(PluginPackageError, match="duplicate"):
        unpack_package(make_zip(("a", b"1"), ("a", b"2")), target)
    with pytest.raises(PluginPackageError):
        unpack_package(make_zip(("b", b"1"), ("b/c", b"2")), target)


def test_rejects_corrupt_archives(target: Path) -> None:
    with pytest.raises(PluginPackageError, match="not a valid ZIP"):
        unpack_package(b"PK\x03\x04 truncated", target)


def test_enforces_entry_count_and_unpacked_size(
    target: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(archive, "MAX_ENTRIES", 2)
    with pytest.raises(PluginPackageError, match="too many entries"):
        unpack_package(make_zip(("a", b""), ("b", b""), ("c", b"")), target)

    monkeypatch.setattr(archive, "MAX_UNPACKED_BYTES", 10)
    with pytest.raises(PluginPackageError, match="too large"):
        unpack_package(make_zip(("a", b"x" * 11)), target)
