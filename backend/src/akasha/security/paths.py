"""Workspace path guard for user-supplied file paths (backend AGENTS §50).

File nodes (and the command node's working directory) operate inside one
directory per project below the operator-configured workspace root. A path
from config, an input port or a model's tool call is resolved here first;
anything that would leave the project directory is refused: absolute
paths, ``..`` segments, backslashes, NUL bytes, and symlinks pointing
outside (``resolve`` follows them before the containment check).
"""

from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from akasha.domain.node import NodeError, NodeErrorKind


def _invalid(reason: str) -> NodeError:
    return NodeError(
        f"the path is not allowed: {reason}",
        kind=NodeErrorKind.VALIDATION,
        code="PATH_NOT_ALLOWED",
    )


@dataclass(frozen=True)
class Workspace:
    """``root`` must exist; project directories below it are created on
    first use. Project ids are engine-generated, but are checked like any
    other path segment anyway."""

    root: Path

    def project_dir(self, project_id: str) -> Path:
        base = self.root.resolve(strict=True)
        directory = (base / project_id).resolve()
        if directory.parent != base or not project_id.isprintable():
            raise _invalid("invalid project directory")
        directory.mkdir(exist_ok=True)
        return directory

    def resolve(self, project_id: str, relative: str) -> Path:
        """``relative`` resolved inside the project directory.

        ponytail: check-then-use; a process that swaps a directory for a
        symlink between this check and the open wins the race. Upgrade
        path: openat2(RESOLVE_BENEATH) once a portable binding exists.
        """
        base = self.project_dir(project_id)
        if "\x00" in relative or "\\" in relative:
            raise _invalid("NUL bytes and backslashes are not allowed")
        posix = PurePosixPath(relative)
        if posix.is_absolute():
            raise _invalid("absolute paths are not allowed")
        if ".." in posix.parts:
            raise _invalid("parent directory segments are not allowed")
        target = (base / posix).resolve()
        if target != base and not target.is_relative_to(base):
            raise _invalid("it leaves the workspace")
        return target

    def relative(self, project_id: str, path: Path) -> str:
        """``path`` as the project-relative POSIX string tools report."""
        rel = path.relative_to(self.project_dir(project_id)).as_posix()
        return rel or "."
