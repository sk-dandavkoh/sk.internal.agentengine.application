"""Third-party Python libraries of plugins (ADR 0004).

A manifest lists PEP 508 ``python_requirements``. They are installed with
``uv pip install --target`` into one directory per plugin, which is put on
``sys.path`` while the plugin is enabled::

    <plugins_dir>/.deps/<plugin_id>/                 the installed libraries
    <plugins_dir>/.deps/<plugin_id>/akasha-requirements.json
        what they were resolved for: requirements, Python, engine libraries

Plugins share the engine's interpreter, so a library has exactly one
version per process. Resolution enforces that:

- every library the engine itself has is pinned to the engine's version
  (``--constraint``), so a plugin can never up- or downgrade one;
- every library another installed plugin has is pinned to that version,
  so two plugins never need different versions of one library;
- the directory is appended to ``sys.path``: engine libraries always win.

A conflict fails the install with ``PLUGIN_DEPENDENCIES_FAILED`` and uv's
explanation. Imported modules cannot be unloaded: once a library version is
active in a process, a different version needs a restart; ``activate``
refuses it instead of silently running the old one.

Only wheels are installed (``--only-binary :all:``): no build scripts run on
the engine host and no compiler is needed. The index comes from uv's own
configuration: ``UV_DEFAULT_INDEX``, ``UV_INDEX``, ``UV_FIND_LINKS``,
``UV_OFFLINE`` or a uv.toml.
"""

import contextlib
import hashlib
import importlib.metadata
import json
import logging
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import Final

from packaging.utils import canonicalize_name

from akasha.domain.plugin import PluginMetadata
from akasha.errors import AkashaError
from akasha.plugins.manifest import ENGINE_DISTRIBUTION

logger = logging.getLogger(__name__)

DEPS_DIR: Final = ".deps"
MARKER_FILE: Final = "akasha-requirements.json"
INSTALL_TIMEOUT_SECONDS: Final = 600

# Distribution → version of every plugin library made importable in this
# process. Process-wide on purpose: so is ``sys.modules``.
_ACTIVE: dict[str, str] = {}
# The ``sys.path`` entries added here, so they never count as engine libraries.
_ADDED_PATHS: set[str] = set()


class PluginDependencyError(AkashaError):
    code = "PLUGIN_DEPENDENCIES_FAILED"
    http_status = 422


class PluginDependencies:
    """Installs, activates and removes the libraries of each plugin."""

    def __init__(self, plugins_dir: Path) -> None:
        self._root = plugins_dir / DEPS_DIR
        self._engine = _distributions(p for p in sys.path if p not in _ADDED_PATHS)
        self._engine.pop(ENGINE_DISTRIBUTION, None)  # not on any index
        self._fingerprint = {
            "python": sys.version,
            "engine": hashlib.sha256(
                json.dumps(sorted(self._engine.items())).encode()
            ).hexdigest(),
        }

    def path(self, plugin_id: str) -> Path:
        return self._root / plugin_id

    def ensure(self, metadata: PluginMetadata) -> Path | None:
        """The plugin's library directory, (re)installed when missing or
        resolved for other requirements, Python or engine libraries; None
        when the plugin needs none. Slow when it installs: runs uv."""
        target = self.path(metadata.plugin_id)
        if not metadata.python_requirements:
            self.remove(metadata.plugin_id)  # requirements dropped since
            return None
        marker = self._marker(metadata)
        if _read_marker(target) == marker:
            return target
        self._root.mkdir(exist_ok=True)
        staging = self._root / f".install-{uuid.uuid4().hex}"
        try:
            self._install(metadata, staging)
            (staging / MARKER_FILE).write_text(json.dumps(marker))
            # ponytail: replace-then-rename is not atomic. Another process
            # (API and worker start together) may install the same set at
            # once; whichever rename loses accepts the winner's identical
            # result. A process still importing from a replaced directory
            # needs a restart anyway: its libraries changed.
            shutil.rmtree(target, ignore_errors=True)
            try:
                staging.rename(target)
            except OSError:
                if _read_marker(target) != marker:
                    raise
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        libraries = _distributions([str(target)])
        logger.info(
            "plugin_dependencies_installed",
            extra={
                "event": "plugin_dependencies_installed",
                "plugin_id": metadata.plugin_id,
                "distributions": [f"{n}=={v}" for n, v in sorted(libraries.items())],
            },
        )
        return target

    def activate(self, plugin_id: str, path: Path) -> None:
        """Make the libraries at ``path`` importable."""
        libraries = _distributions([str(path)])
        clashes = sorted(n for n, v in libraries.items() if _ACTIVE.get(n, v) != v)
        if clashes:
            raise PluginDependencyError(
                "Another version of a library is already loaded in this "
                "process; restart the engine to load this plugin.",
                details={"plugin_id": plugin_id, "distributions": clashes},
            )
        _ACTIVE.update(libraries)
        entry = str(path)
        if entry not in sys.path:
            sys.path.append(entry)  # appended: engine libraries come first
        _ADDED_PATHS.add(entry)

    def deactivate(self, plugin_id: str) -> None:
        """Stop new imports from the plugin's libraries. Modules already
        imported stay loaded until the process restarts."""
        entry = str(self.path(plugin_id))
        if entry in sys.path:
            sys.path.remove(entry)
        _ADDED_PATHS.discard(entry)

    def remove(self, plugin_id: str) -> None:
        self.deactivate(plugin_id)
        shutil.rmtree(self.path(plugin_id), ignore_errors=True)
        with contextlib.suppress(OSError):  # only when empty: no plugin left
            self._root.rmdir()

    # -- internals --------------------------------------------------------

    def _marker(self, metadata: PluginMetadata) -> dict[str, object]:
        return {
            "requirements": list(metadata.python_requirements),
            **self._fingerprint,
        }

    def _constraints(self, plugin_id: str) -> dict[str, str]:
        """Engine libraries, then those of other plugins installed for this
        engine (stale directories are reinstalled when their plugin loads)."""
        pins = dict(self._engine)
        for path in sorted(self._root.iterdir()) if self._root.is_dir() else ():
            if path.name.startswith(".") or path.name == plugin_id:
                continue
            marker = _read_marker(path)
            if marker is not None and all(
                marker.get(k) == v for k, v in self._fingerprint.items()
            ):
                for name, version in _distributions([str(path)]).items():
                    pins.setdefault(name, version)
        return pins

    def _install(self, metadata: PluginMetadata, target: Path) -> None:
        uv = shutil.which("uv")
        if uv is None:
            raise PluginDependencyError(
                "The plugin needs Python libraries, but uv is not installed "
                "on the engine host.",
                details={"plugin_id": metadata.plugin_id},
            )
        with tempfile.TemporaryDirectory(prefix="akasha-plugin-deps-") as tmp:
            constraints = Path(tmp) / "constraints.txt"
            constraints.write_text(
                "".join(
                    f"{n}=={v}\n"
                    for n, v in sorted(self._constraints(metadata.plugin_id).items())
                )
            )
            argv = [
                uv,
                "pip",
                "install",
                "--quiet",
                "--no-cache",
                "--only-binary",
                ":all:",
                "--python",
                sys.executable,
                "--target",
                str(target),
                "--constraint",
                str(constraints),
                "--",
                *metadata.python_requirements,
            ]
            try:
                # cwd: no project's pyproject.toml or uv.toml applies.
                result = subprocess.run(
                    argv,
                    cwd=tmp,
                    capture_output=True,
                    text=True,
                    timeout=INSTALL_TIMEOUT_SECONDS,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                raise PluginDependencyError(
                    "Installing the plugin's Python libraries timed out.",
                    details={
                        "plugin_id": metadata.plugin_id,
                        "timeout_seconds": INSTALL_TIMEOUT_SECONDS,
                    },
                ) from None
        if result.returncode != 0:
            output = _summary(result.stderr)
            logger.warning(
                "plugin_dependencies_failed",
                extra={
                    "event": "plugin_dependencies_failed",
                    "plugin_id": metadata.plugin_id,
                    "output": output,
                },
            )
            raise PluginDependencyError(
                "The plugin's Python libraries could not be installed.",
                details={"plugin_id": metadata.plugin_id, "output": output},
            )


def _distributions(paths: Iterable[str]) -> dict[str, str]:
    """Canonical name → version of the distributions on ``paths``; the
    first occurrence wins, like an import would."""
    found: dict[str, str] = {}
    for dist in importlib.metadata.distributions(path=list(paths)):
        name = dist.metadata["Name"]
        if name:
            found.setdefault(canonicalize_name(name), dist.version)
    return found


def _read_marker(path: Path) -> dict[str, object] | None:
    try:
        marker = json.loads((path / MARKER_FILE).read_bytes())
    except (OSError, ValueError):
        return None
    return marker if isinstance(marker, dict) else None


# Credentials in index URLs (https://user:token@host/...).
_URL_CREDENTIALS = re.compile(r"(?<=://)[^/\s@]+@")


def _summary(stderr: str) -> str:
    """uv's explanation (which libraries conflict, which are missing),
    bounded and without index credentials."""
    tail = "\n".join(stderr.strip().splitlines()[-30:])[-4000:]
    return _URL_CREDENTIALS.sub("***@", tail)
