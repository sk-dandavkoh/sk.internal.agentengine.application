"""Plugin lifecycle use cases shared by the REST API and the MCP server
(architecture §36, §143: prefer safe blocking).

``PluginRegistry`` owns the lifecycle mechanics; this module adds what an
operator-facing call needs on top:

- one lock per process, so concurrent install/enable/update/remove calls
  (REST and MCP alike) never interleave: the registry is not locked
  itself, and ``prepare`` runs in a thread;
- dependency-aware removal and update: a project needs a plugin when its
  draft or any published version uses one of the plugin's node types or
  declares a plugin dependency on it. Published versions count too: they
  are retained history that executions may still run.

Whether a caller may hand the engine *new code* (install, update, test) is
interface policy and stays with the interface: ``AKASHA_PLUGINS_HTTP_INSTALL``
for REST, ``AKASHA_MCP_PLUGIN_CODE`` for MCP.
"""

import ast
import asyncio
import logging
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from akasha.application.projects import ProjectService
from akasha.domain.plugin import PluginMetadata
from akasha.errors import AkashaError, ConfigurationError
from akasha.plugins.archive import unpack_package
from akasha.plugins.manifest import read_manifest
from akasha.plugins.registry import (
    PluginInUseError,
    PluginRecord,
    PluginRegistry,
    PluginState,
)
from akasha.sdk import check_node_contract
from akasha.sdk.testing import NodeRun, run_node

logger = logging.getLogger("akasha.application.plugins")


class PluginNodeNotDeclaredError(AkashaError):
    code = "PLUGIN_NODE_NOT_FOUND"
    http_status = 404


class PluginTestFailedError(AkashaError):
    """The tested node's own code raised something other than an engine
    error: the plugin is broken, the engine is fine."""

    code = "PLUGIN_TEST_FAILED"
    http_status = 422


async def remove_plugin(
    registry: PluginRegistry,
    projects: ProjectService,
    plugin_id: str,
    *,
    force: bool = False,
) -> Sequence[str]:
    """Remove ``plugin_id`` unless projects need it; ``force`` removes it
    anyway (the explicit confirmation §143 asks for). Returns the ids of
    the projects that needed it (empty unless forced)."""
    users = await _users(registry, projects, plugin_id, force=force, action="remove")
    registry.remove(plugin_id)
    _warn_forced("plugin_removed_in_use", plugin_id, users)
    return users


async def _users(
    registry: PluginRegistry,
    projects: ProjectService,
    plugin_id: str,
    *,
    force: bool,
    action: str,
) -> Sequence[str]:
    metadata = registry.get(plugin_id).metadata
    node_types = [] if metadata is None else [n.node_id for n in metadata.nodes]
    users = await projects.projects_using_plugin(plugin_id, node_types)
    if users and not force:
        raise PluginInUseError(
            f"Projects use this plugin; {action} it with force to proceed.",
            details={"plugin_id": plugin_id, "projects": list(users)},
        )
    return users


def _warn_forced(event: str, plugin_id: str, users: Sequence[str]) -> None:
    if users:
        logger.warning(
            event,
            extra={"event": event, "plugin_id": plugin_id, "project_count": len(users)},
        )


@dataclass(frozen=True)
class PackageIssue:
    code: str
    message: str
    path: str | None = None
    line: int | None = None


@dataclass(frozen=True)
class PackageCheck:
    """What a static check of a plugin package found. ``metadata`` is None
    when the archive or its manifest is unreadable."""

    metadata: PluginMetadata | None
    issues: tuple[PackageIssue, ...]
    # State of an installed plugin with the same id (install would refuse
    # it; update replaces it), else None.
    installed: PluginState | None = None

    @property
    def ok(self) -> bool:
        return not self.issues


def _code_issues(package: Path, metadata: PluginMetadata) -> list[PackageIssue]:
    """Syntax of every module and the declared entry points, by parsing
    only: no plugin code is imported or run."""
    issues: list[PackageIssue] = []
    classes: dict[str, set[str]] = {}
    for path in sorted(package.rglob("*.py")):
        relative = path.relative_to(package).as_posix()
        try:
            tree = ast.parse(path.read_bytes(), filename=relative)
        except (SyntaxError, ValueError) as exc:
            line = exc.lineno if isinstance(exc, SyntaxError) else None
            issues.append(
                PackageIssue(
                    "PYTHON_SYNTAX_ERROR",
                    f"not valid Python: {type(exc).__name__}",
                    relative,
                    line,
                )
            )
            continue
        module = relative.removesuffix(".py").removesuffix("/__init__")
        classes[module.replace("/", ".")] = {
            n.name for n in tree.body if isinstance(n, ast.ClassDef)
        }
    for node in metadata.nodes:
        module, _, name = node.entry_point.partition(":")
        if module not in classes:
            issues.append(
                PackageIssue(
                    "ENTRY_POINT_NOT_FOUND",
                    f"{node.node_id}: module {module!r} is not in the package",
                )
            )
        elif name not in classes[module]:
            issues.append(
                PackageIssue(
                    "ENTRY_POINT_NOT_FOUND",
                    f"{node.node_id}: module {module!r} defines no class {name!r}",
                )
            )
    return issues


class PluginService:
    """Serialized plugin lifecycle over one ``PluginRegistry``."""

    def __init__(
        self, registry: PluginRegistry, projects: ProjectService | None
    ) -> None:
        """Without ``projects`` (no database) removal and update are
        unavailable: they could not see which projects need the plugin."""
        self.registry = registry
        self._project_service = projects
        self._lock = asyncio.Lock()

    @property
    def _projects(self) -> ProjectService:
        if self._project_service is None:
            raise ConfigurationError("Plugin removal and update need the database.")
        return self._project_service

    def list(self) -> list[PluginRecord]:
        return self.registry.list_plugins()

    def get(self, plugin_id: str) -> PluginRecord:
        return self.registry.get(plugin_id)

    def check_archive(self, data: bytes) -> PackageCheck:
        """Validate the package ZIP ``data`` without running any of its
        code: archive safety, manifest, engine/SDK/dependency compatibility,
        Python syntax and entry points. Install repeats every check that
        matters to it; this is the agent-facing preview."""
        with tempfile.TemporaryDirectory(prefix="akasha-plugin-check-") as tmp:
            try:
                package = unpack_package(data, Path(tmp))
                metadata = read_manifest(package)
            except AkashaError as exc:  # archive or manifest errors
                details = exc.details
                path = details.get("path")
                fields = details.get("fields")
                message = exc.message
                if isinstance(fields, list):
                    message = f"{message} Fields: {', '.join(map(str, fields))}"
                elif (reason := details.get("reason")) is not None:
                    message = f"{message} {reason}"
                issue = PackageIssue(
                    exc.code, message, None if path is None else str(path)
                )
                return PackageCheck(None, (issue,))
            issues = [
                PackageIssue("PLUGIN_INCOMPATIBLE", message)
                for message in self.registry.compatibility_issues(metadata)
            ]
            issues += _code_issues(package, metadata)
        try:
            installed: PluginState | None = self.registry.get(metadata.plugin_id).state
        except AkashaError:
            installed = None
        return PackageCheck(metadata, tuple(issues), installed)

    async def install_archive(self, data: bytes) -> PluginRecord:
        """Validate and install the plugin package ZIP ``data``."""
        async with self._lock:
            with tempfile.TemporaryDirectory(prefix="akasha-plugin-") as tmp:
                package = unpack_package(data, Path(tmp))
                await asyncio.to_thread(self.registry.prepare, package)
                return self.registry.install(package)

    async def update_archive(
        self, data: bytes, *, force: bool = False
    ) -> tuple[PluginRecord, Sequence[str]]:
        """Replace an installed plugin with the package ZIP ``data``. Like
        removal, refused while projects use it unless ``force``: their
        published versions stay pinned to the old version and stop running
        until it is back. Returns the record and the affected projects."""
        async with self._lock:
            with tempfile.TemporaryDirectory(prefix="akasha-plugin-") as tmp:
                package = unpack_package(data, Path(tmp))
                plugin_id = read_manifest(package).plugin_id
                users = await _users(
                    self.registry,
                    self._projects,
                    plugin_id,
                    force=force,
                    action="update",
                )
                record = self.registry.update(package)
        _warn_forced("plugin_updated_in_use", plugin_id, users)
        return record, users

    async def enable(self, plugin_id: str) -> PluginRecord:
        async with self._lock:
            if self.registry.get(plugin_id).metadata is not None:
                path = self.registry.path(plugin_id)
                await asyncio.to_thread(self.registry.prepare, path)
            return self.registry.enable(plugin_id)

    async def disable(self, plugin_id: str) -> PluginRecord:
        async with self._lock:
            return self.registry.disable(plugin_id)

    async def remove(self, plugin_id: str, *, force: bool = False) -> Sequence[str]:
        async with self._lock:
            return await remove_plugin(
                self.registry, self._projects, plugin_id, force=force
            )

    async def test_archive(
        self,
        data: bytes,
        node_type: str,
        *,
        config: Mapping[str, Any] | None = None,
        inputs: Mapping[str, Any] | None = None,
        timeout_seconds: float = 30.0,
    ) -> NodeRun:
        """Run one node of a not yet installed package the way the engine
        would (``akasha.sdk.testing.run_node``), for at most
        ``timeout_seconds``. Runs the package's code in this process;
        nothing is registered or installed. Like the runtime's node timeout,
        the limit stops cooperative (awaiting) code only."""
        with tempfile.TemporaryDirectory(prefix="akasha-plugin-test-") as tmp:
            package = unpack_package(data, Path(tmp))
            classes = {
                c.definition.node_id: c
                for c in self.registry.load_unregistered(package)
            }
            if node_type not in classes:
                raise PluginNodeNotDeclaredError(
                    "The package does not declare this node type.",
                    details={"node_type": node_type, "declared": sorted(classes)},
                )
            check_node_contract(classes[node_type])
            try:
                node = classes[node_type]()
                async with asyncio.timeout(timeout_seconds):
                    return await run_node(node, config=config, inputs=inputs)
            except TimeoutError as exc:
                raise PluginTestFailedError(
                    "The node did not finish in time.",
                    details={
                        "node_type": node_type,
                        "timeout_seconds": timeout_seconds,
                    },
                ) from exc
            except AkashaError:
                raise  # engine checks and NodeError: already structured
            # Plugin code is untrusted here: whatever else it raises is a
            # failed test, reported by type only (messages may echo data).
            except Exception as exc:
                raise PluginTestFailedError(
                    "The node raised an unexpected exception.",
                    details={"node_type": node_type, "error": type(exc).__name__},
                ) from exc
