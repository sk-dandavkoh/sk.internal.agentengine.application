"""PluginRegistry: the plugin lifecycle (architecture §36, §141-§143, §157).

    discover → validate → check compatibility → install → load → register
    → use → disable → remove

Roadmap names map onto this module like ``NodeRegistry`` does:

- PluginManifest / PluginValidator  ``manifest.read_manifest`` (no code runs)
- PluginCompatibilityChecker        ``domain.plugin.compatibility_issues``
- PluginInstaller                   ``install`` / ``remove``
- PluginLoader                      ``_load`` (the only place plugin code runs)
- PluginRegistry                    ``load_installed`` / ``enable`` /
                                    ``disable`` / ``get`` / ``list_plugins``

Plugin nodes join the ``NodeRegistry`` through its ordinary ``register``;
nothing downstream (publish, runtime) knows a node came from a plugin.

Trust model (V1): plugin code is trusted, installation-level code running in
the engine process — manifests are validated so malformed metadata cannot
corrupt the registry, but this is not a sandbox. ``_load`` is the isolation
seam: it turns a package into node classes for ``NodeRegistry.register``;
an isolated loader would register factories of out-of-process proxy nodes
instead, and nothing else would change.

Installed layout under the configured directory::

    <plugins_dir>/<plugin_id>/        an installed package (copy of the source)
    <plugins_dir>/.deps/<plugin_id>/  its Python libraries (``dependencies``)
    <plugins_dir>/state.json          {"disabled": [plugin ids]}

Single-process: another process (future workers) sees changes after a
restart. Removal is not blocked by Projects using the plugin's nodes:
their validate/publish/run fail with structured MISSING_* issues until it is
reinstalled (project data is untouched).
"""

import importlib
import importlib.util
import json
import logging
import os
import shutil
import sys
import uuid
from dataclasses import dataclass
from enum import StrEnum, auto
from pathlib import Path
from types import MappingProxyType

from akasha import __version__ as ENGINE_VERSION
from akasha.domain.node import Node
from akasha.domain.plugin import PluginMetadata, compatibility_issues
from akasha.errors import AkashaError, ConfigurationError, InvalidStateTransitionError
from akasha.nodes.registry import NodeRegistry
from akasha.plugins.dependencies import PluginDependencies
from akasha.plugins.manifest import PluginManifestError, read_manifest
from akasha.sdk import SDK_VERSION

logger = logging.getLogger(__name__)

_STATE_FILE = "state.json"


class PluginNotFoundError(AkashaError):
    code = "PLUGIN_NOT_FOUND"
    http_status = 404


class PluginAlreadyInstalledError(AkashaError):
    code = "PLUGIN_ALREADY_INSTALLED"
    http_status = 409


class PluginIncompatibleError(AkashaError):
    code = "PLUGIN_INCOMPATIBLE"
    http_status = 409


class PluginInUseError(AkashaError):
    code = "PLUGIN_IN_USE"
    http_status = 409


class PluginPackageError(AkashaError):
    code = "PLUGIN_PACKAGE_INVALID"
    http_status = 422


class PluginLoadError(AkashaError):
    code = "PLUGIN_LOAD_FAILED"
    http_status = 422


class PluginState(StrEnum):
    ENABLED = auto()
    DISABLED = auto()
    FAILED = auto()  # installed, but could not be activated at startup


@dataclass(frozen=True)
class PluginRecord:
    plugin_id: str  # the directory name when the manifest is unreadable
    state: PluginState
    metadata: PluginMetadata | None = None
    error: str | None = None  # stable error code when FAILED


class PluginRegistry:
    def __init__(
        self,
        directory: Path,
        nodes: NodeRegistry,
        *,
        engine_version: str = ENGINE_VERSION,
        sdk_version: str = SDK_VERSION,
    ) -> None:
        if not directory.is_dir():
            raise ConfigurationError(
                "The plugin directory does not exist.",
                details={"setting": "AKASHA_PLUGINS_DIR"},
            )
        self._dir = directory
        self._nodes = nodes
        self._deps = PluginDependencies(directory)
        self._engine_version = engine_version
        self._sdk_version = sdk_version
        self._records: dict[str, PluginRecord] = {}
        self._enabled: dict[str, PluginMetadata] = {}
        self._versions: dict[str, str] = {}
        # Enabled plugin id → version; ``ProjectService`` checks project
        # plugin dependencies against this live view.
        self.versions = MappingProxyType(self._versions)

    # -- queries ----------------------------------------------------------

    def get(self, plugin_id: str) -> PluginRecord:
        try:
            return self._records[plugin_id]
        except KeyError:
            raise PluginNotFoundError(
                "Unknown plugin.", details={"plugin_id": plugin_id}
            ) from None

    def list_plugins(self) -> list[PluginRecord]:
        return [self._records[k] for k in sorted(self._records)]

    def path(self, plugin_id: str) -> Path:
        """Where the installed package of ``plugin_id`` lives."""
        return self._dir / plugin_id

    # -- lifecycle --------------------------------------------------------

    def load_installed(self) -> None:
        """Discover installed packages and activate the enabled ones, each
        after the plugins it depends on. A plugin that cannot be activated
        is recorded as FAILED (and logged); the engine still starts."""
        disabled = self._read_state()
        pending: dict[str, tuple[Path, PluginMetadata]] = {}
        for path in sorted(self._dir.iterdir()):
            if path.name.startswith(".") or not path.is_dir():
                continue
            try:
                metadata = read_manifest(path)
                if metadata.plugin_id != path.name:
                    raise PluginManifestError(
                        "The plugin directory name does not match its id."
                    )
            except PluginManifestError as exc:
                self._fail(path.name, None, exc)
                continue
            if metadata.plugin_id in disabled:
                self._records[path.name] = PluginRecord(
                    path.name, PluginState.DISABLED, metadata
                )
            else:
                pending[path.name] = (path, metadata)
        while pending:
            ready = [
                pid
                for pid in sorted(pending)
                if not pending[pid][1].plugin_dependencies & pending.keys()
            ]
            if not ready:  # a dependency cycle: none can go first
                for pid, (_, metadata) in sorted(pending.items()):
                    self._fail(
                        pid,
                        metadata,
                        PluginIncompatibleError("Plugin dependency cycle."),
                    )
                break
            for pid in ready:
                path, metadata = pending.pop(pid)
                try:
                    self._activate(path, metadata)
                except AkashaError as exc:
                    self._fail(pid, metadata, exc)

    def install(self, source: Path) -> PluginRecord:
        """Validate, copy and activate the plugin package at ``source``.

        Nothing changes unless every step succeeds: a failure leaves no
        files, no registered nodes and no record behind. Upgrading is
        ``remove`` then ``install``.
        """
        metadata = read_manifest(source)
        target = self._dir / metadata.plugin_id
        if metadata.plugin_id in self._records or target.exists():
            raise PluginAlreadyInstalledError(
                "A plugin with this id is already installed.",
                details={"plugin_id": metadata.plugin_id},
            )
        try:
            self._check_compatible(metadata)
            staging = self._dir / f".install-{uuid.uuid4().hex}"
            try:
                _copy_package(source, staging)
                # The source may have changed since it was validated.
                if read_manifest(staging) != metadata:
                    raise PluginPackageError(
                        "The plugin package changed during install."
                    )
                staging.rename(target)
            finally:
                shutil.rmtree(staging, ignore_errors=True)
            try:
                self._activate(target, metadata)
            except BaseException:
                shutil.rmtree(target, ignore_errors=True)
                raise
        except BaseException:
            # Libraries ``prepare`` or ``_activate`` installed for it.
            self._deps.remove(metadata.plugin_id)
            raise
        self._save_state()  # drops a stale "disabled" entry of an old install
        _log("plugin_installed", metadata)
        return self._records[metadata.plugin_id]

    def prepare(self, package: Path) -> None:
        """Install the Python libraries of the package at ``package`` (a
        source to ``install``, or an installed plugin to ``enable``) ahead
        of that call, which then finds them current. This is the slow part
        of both, and it changes no registry state: callers may run it off
        the event loop. Not concurrently with another lifecycle call."""
        metadata = read_manifest(package)
        installed = self._dir / metadata.plugin_id
        if installed.exists() and package.resolve() != installed.resolve():
            return  # ``install`` refuses it: leave the installed one's alone
        self._check_compatible(metadata)
        self._deps.ensure(metadata)

    def enable(self, plugin_id: str) -> PluginRecord:
        record = self.get(plugin_id)
        if record.state is PluginState.ENABLED or record.metadata is None:
            raise InvalidStateTransitionError(
                "The plugin cannot be enabled from its current state.",
                details={"plugin_id": plugin_id, "state": record.state},
            )
        self._activate(self._dir / plugin_id, record.metadata)
        self._save_state()
        _log("plugin_enabled", record.metadata)
        return self._records[plugin_id]

    def disable(self, plugin_id: str) -> PluginRecord:
        record = self.get(plugin_id)
        if record.state is not PluginState.ENABLED:
            raise InvalidStateTransitionError(
                "Only an enabled plugin can be disabled.",
                details={"plugin_id": plugin_id, "state": record.state},
            )
        metadata = self._deactivate(plugin_id)
        self._records[plugin_id] = PluginRecord(
            plugin_id, PluginState.DISABLED, metadata
        )
        self._save_state()
        _log("plugin_disabled", metadata)
        return self._records[plugin_id]

    def remove(self, plugin_id: str) -> None:
        """Unload (if enabled) and delete an installed plugin."""
        record = self.get(plugin_id)
        if record.state is PluginState.ENABLED:
            self._deactivate(plugin_id)
        del self._records[plugin_id]
        shutil.rmtree(self._dir / plugin_id)
        self._deps.remove(plugin_id)
        self._save_state()
        logger.info(
            "plugin_removed", extra={"event": "plugin_removed", "plugin_id": plugin_id}
        )

    # -- internals --------------------------------------------------------

    def _check_compatible(self, metadata: PluginMetadata) -> None:
        issues = compatibility_issues(
            metadata,
            engine_version=self._engine_version,
            sdk_version=self._sdk_version,
            definitions=self._nodes.definitions,
            plugins=self._versions,
        )
        if issues:
            raise PluginIncompatibleError(
                "The plugin is not compatible with this engine.",
                details={
                    "plugin_id": metadata.plugin_id,
                    "issues": [i.message for i in issues],
                },
            )

    def _activate(self, path: Path, metadata: PluginMetadata) -> None:
        self._check_compatible(metadata)
        libraries = self._deps.ensure(metadata)
        if libraries is not None:
            self._deps.activate(metadata.plugin_id, libraries)
        registered: list[str] = []
        try:
            for node_cls in _load(path, metadata):
                self._nodes.register(node_cls)
                registered.append(node_cls.definition.node_id)
        except BaseException as exc:
            for node_id in registered:
                self._nodes.unregister(node_id)
            _unimport(metadata.plugin_id)
            self._deps.deactivate(metadata.plugin_id)
            if isinstance(exc, AkashaError) or not isinstance(exc, Exception):
                raise
            # ``register`` instantiates each node once: a plugin node's
            # constructor raised.
            raise _load_error(metadata, exc) from exc
        self._records[metadata.plugin_id] = PluginRecord(
            metadata.plugin_id, PluginState.ENABLED, metadata
        )
        self._enabled[metadata.plugin_id] = metadata
        self._versions[metadata.plugin_id] = metadata.version

    def _deactivate(self, plugin_id: str) -> PluginMetadata:
        dependents = sorted(
            pid
            for pid, m in self._enabled.items()
            if plugin_id in m.plugin_dependencies
        )
        if dependents:
            raise PluginInUseError(
                "Enabled plugins depend on this plugin.",
                details={"plugin_id": plugin_id, "dependents": dependents},
            )
        metadata = self._enabled.pop(plugin_id)
        for node in metadata.nodes:
            self._nodes.unregister(node.node_id)
        del self._versions[plugin_id]
        _unimport(plugin_id)
        self._deps.deactivate(plugin_id)
        return metadata

    def _fail(
        self, plugin_id: str, metadata: PluginMetadata | None, exc: AkashaError
    ) -> None:
        self._records[plugin_id] = PluginRecord(
            plugin_id, PluginState.FAILED, metadata, exc.code
        )
        logger.warning(
            "plugin_failed",
            extra={
                "event": "plugin_failed",
                "plugin_id": plugin_id,
                "error_code": exc.code,
                "details": exc.details,
            },
        )

    def _read_state(self) -> frozenset[str]:
        path = self._dir / _STATE_FILE
        if not path.exists():
            return frozenset()
        try:
            disabled = json.loads(path.read_bytes())["disabled"]
            if not isinstance(disabled, list) or not all(
                isinstance(d, str) for d in disabled
            ):
                raise TypeError
        except (ValueError, KeyError, TypeError) as exc:
            raise ConfigurationError(
                "The plugin state file is malformed.",
                details={"file": _STATE_FILE},
            ) from exc
        return frozenset(disabled)

    def _save_state(self) -> None:
        disabled = sorted(
            pid for pid, r in self._records.items() if r.state is PluginState.DISABLED
        )
        tmp = self._dir / f".{_STATE_FILE}.tmp"
        tmp.write_text(json.dumps({"disabled": disabled}))
        os.replace(tmp, self._dir / _STATE_FILE)  # atomic: never half-written


def _module_name(plugin_id: str) -> str:
    # Hex keeps distinct ids distinct ("a.b" vs "a_b") and valid identifiers.
    return f"akasha_plugin_{plugin_id.encode().hex()}"


def _unimport(plugin_id: str) -> None:
    """Forget a plugin's modules so a reinstall imports the new code."""
    package = _module_name(plugin_id)
    for name in [n for n in sys.modules if n == package or n.startswith(f"{package}.")]:
        del sys.modules[name]


def _load(path: Path, metadata: PluginMetadata) -> list[type[Node]]:
    """Import the package at ``path`` and return its declared node classes."""
    package = _module_name(metadata.plugin_id)
    _unimport(metadata.plugin_id)
    classes: list[type[Node]] = []
    try:
        spec = importlib.util.spec_from_file_location(
            package, path / "__init__.py", submodule_search_locations=[str(path)]
        )
        if spec is None or spec.loader is None:
            raise ImportError(package)
        module = importlib.util.module_from_spec(spec)
        sys.modules[package] = module
        spec.loader.exec_module(module)
        for declared in metadata.nodes:
            module_path, _, attribute = declared.entry_point.partition(":")
            obj = getattr(
                importlib.import_module(f"{package}.{module_path}"), attribute
            )
            if not (isinstance(obj, type) and issubclass(obj, Node)):
                raise TypeError(f"{declared.entry_point} is not a Node class")
            node_id = getattr(getattr(obj, "definition", None), "node_id", None)
            if node_id != declared.node_id:
                raise TypeError(
                    f"{declared.entry_point} does not define {declared.node_id}"
                )
            classes.append(obj)
    # Plugin code is trusted but may be broken: whatever its import raises is
    # a failed load of that plugin, never a crash of the engine. The
    # traceback stays in the server log.
    except Exception as exc:
        _unimport(metadata.plugin_id)
        raise _load_error(metadata, exc) from exc
    return classes


def _load_error(metadata: PluginMetadata, exc: Exception) -> PluginLoadError:
    logger.warning(
        "plugin_load_failed",
        exc_info=exc,
        extra={"event": "plugin_load_failed", "plugin_id": metadata.plugin_id},
    )
    return PluginLoadError(
        "The plugin code could not be loaded.",
        details={"plugin_id": metadata.plugin_id, "error": type(exc).__name__},
    )


def _copy_package(source: Path, target: Path) -> None:
    """Copy regular files and directories only: a symlink could smuggle
    files from outside the package into the plugin directory."""
    for path in [source, *source.rglob("*")]:
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise PluginPackageError(
                "Plugin packages may contain only regular files and directories.",
                details={"path": str(path.relative_to(source))},
            )
    shutil.copytree(
        source, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
    )


def _log(event: str, metadata: PluginMetadata) -> None:
    logger.info(
        event,
        extra={
            "event": event,
            "plugin_id": metadata.plugin_id,
            "plugin_version": metadata.version,
        },
    )
