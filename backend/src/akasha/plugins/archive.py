"""Plugin package archives: a ZIP of a plugin package, unpacked for
``PluginRegistry.install`` (``POST /api/v1/plugins``, ADR 0001).

The archive is untrusted input until the registry has validated the
manifest, so unpacking only ever writes regular files below ``target``:
no absolute paths, no ``..``, no symlinks or devices, bounded entry count
and sizes (declared sizes are not trusted, reads are capped). Either layout
is accepted: the package files at the archive root, or inside one top-level
directory (what ``zip -r text_tools.zip text_tools`` produces).

Nothing here imports plugin code.
"""

import stat
import zipfile
import zlib
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Final

from akasha.plugins.manifest import MANIFEST_FILE
from akasha.plugins.registry import PluginPackageError

MEDIA_TYPE: Final = "application/zip"
MAX_ARCHIVE_BYTES: Final = 16 * 1024 * 1024
MAX_UNPACKED_BYTES: Final = 64 * 1024 * 1024
MAX_ENTRIES: Final = 2000


def unpack_package(data: bytes, target: Path) -> Path:
    """Unpack the archive into the empty directory ``target``; returns the
    package directory (``target`` or its single top-level directory).

    Raises ``PluginPackageError``."""
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_ENTRIES:
                raise PluginPackageError("The plugin archive has too many entries.")
            if sum(i.file_size for i in infos) > MAX_UNPACKED_BYTES:
                raise PluginPackageError("The plugin archive unpacks too large.")
            written = 0
            for info in infos:
                written += _extract(archive, info, target, MAX_UNPACKED_BYTES - written)
    # Same family as project packages: corrupt, truncated or unsupported ZIPs.
    except (
        zipfile.BadZipFile,
        zipfile.LargeZipFile,
        zlib.error,
        EOFError,
        ValueError,
        NotImplementedError,
        # A file and a directory of the same name ("a" and "a/b").
        FileExistsError,
        NotADirectoryError,
    ) as exc:
        raise PluginPackageError("The upload is not a valid ZIP archive.") from exc
    if (target / MANIFEST_FILE).exists():
        return target
    children = list(target.iterdir())
    if len(children) == 1 and children[0].is_dir():
        return children[0]
    raise PluginPackageError(
        f"The archive has no {MANIFEST_FILE} at its root or in one top-level directory."
    )


def _extract(
    archive: zipfile.ZipFile, info: zipfile.ZipInfo, target: Path, budget: int
) -> int:
    """Write one entry below ``target``; returns the bytes written."""
    # As stored: on Windows ``filename`` has backslashes turned into "/" and
    # is cut at a NUL, so validating it would differ by platform.
    name = info.orig_filename
    path = PurePosixPath(name)
    if (
        not name
        or "\\" in name
        or "\x00" in name
        or path.is_absolute()
        or any(part in ("", ".", "..") for part in name.rstrip("/").split("/"))
        or ":" in path.parts[0]  # "C:" drive prefixes
    ):
        raise _entry_error("unsafe path", name)
    kind = stat.S_IFMT(info.external_attr >> 16)
    dest = target.joinpath(*path.parts)
    if info.is_dir():
        if kind not in (0, stat.S_IFDIR) or info.file_size:
            raise _entry_error("not a plain directory", name)
        dest.mkdir(parents=True, exist_ok=True)
        return 0
    if kind not in (0, stat.S_IFREG):
        raise _entry_error("not a regular file", name)  # symlinks, devices
    if info.flag_bits & 0x1:
        raise _entry_error("encrypted entry", name)
    if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
        raise _entry_error("unsupported compression", name)
    if dest.exists():
        raise _entry_error("duplicate entry", name)
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Declared sizes can lie: read at most one byte past the budget. A
    # complete read also verifies the entry's CRC.
    with archive.open(info) as stream:
        content = stream.read(budget + 1)
    if len(content) > budget:
        raise PluginPackageError("The plugin archive unpacks too large.")
    dest.write_bytes(content)
    return len(content)


def _entry_error(reason: str, name: str) -> PluginPackageError:
    return PluginPackageError(
        f"The plugin archive contains an invalid entry ({reason}).",
        details={"path": name[:300]},
    )
