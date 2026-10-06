"""MCP tools over the plugin lifecycle (``application.plugins.PluginService``).

Admin only, like ``/api/v1/plugins``. Plugin sources travel inside the tool
call as a map of package-relative path → UTF-8 text; there is no server-side
workspace and no filesystem path an agent could name. A package is always
built with ``pack_files`` and opened with ``unpack_package``, the same
archive checks an uploaded ZIP gets (no absolute paths, no ``..``, no
symlinks, bounded sizes).

Trust boundary (docs/mcp.md): plugins are executable code that runs in the
engine process. Tools that never run package code (sdk, create, validate,
package, list/get, enable/disable/remove of installed plugins) need only an
admin. ``plugin_test``, ``plugin_install`` and ``plugin_update`` run or
install new code and additionally need ``AKASHA_MCP_PLUGIN_CODE=true``.
"""

import base64
import hashlib
from pathlib import Path
from typing import Annotated, Any, Final

from pydantic import BaseModel, Field, JsonValue

from akasha import __version__
from akasha.api.plugins import PluginList, PluginOut, RemovePluginResponse
from akasha.application.auth import require_admin
from akasha.application.plugins import PackageCheck, PluginService
from akasha.domain.node import NodeErrorKind, PortType
from akasha.domain.plugin import PluginMetadata
from akasha.errors import AkashaError, ConfigurationError
from akasha.mcp.authoring import SDK_GUIDE, manifest_schema, scaffold
from akasha.mcp.server import Kit, current_identity
from akasha.mcp.tools import decode_base64
from akasha.plugins.archive import MAX_ARCHIVE_BYTES, pack_files
from akasha.plugins.registry import PluginPackageError
from akasha.sdk import SDK_VERSION
from akasha.security.redaction import redact

_MAX_DOC_BYTES: Final = 32 * 1024

PluginId = Annotated[
    str, Field(min_length=1, max_length=128, description="Plugin id, e.g. 'acme'.")
]
Files = Annotated[
    dict[str, str],
    Field(
        max_length=200,
        description="The plugin package: package-relative path ('nodes.py',"
        " 'akasha-plugin.json', 'pkg/util.py') -> UTF-8 file content. Start"
        " from plugin_create.",
    ),
]
PackageBase64 = Annotated[
    str, Field(description="A plugin package ZIP from plugin_package, base64.")
]
Force = Annotated[
    bool,
    Field(
        description="Proceed although projects use the plugin (their"
        " validation, publishing and runs may fail until it is back)."
    ),
]


class PluginCodeNotAllowedError(AkashaError):
    code = "PLUGIN_CODE_NOT_ALLOWED"
    http_status = 403


class SdkInfo(BaseModel):
    sdk_version: str
    engine_version: str
    # Markdown: manifest, node contract, context, errors, lifecycle.
    guide: str
    manifest_schema: dict[str, Any]
    port_types: list[str]
    error_kinds: list[str]
    max_package_bytes: int
    plugin_code_allowed: bool


class ScaffoldOut(BaseModel):
    plugin_id: str
    node_type: str
    files: dict[str, str]


class IssueOut(BaseModel):
    code: str
    message: str
    path: str | None
    line: int | None


class PackageCheckOut(BaseModel):
    valid: bool
    plugin_id: str | None
    version: str | None
    node_types: list[str]
    # State of an installed plugin with the same id: install refuses it,
    # plugin_update replaces it. Null when not installed.
    installed_state: str | None
    issues: list[IssueOut]


class PluginPackageOut(BaseModel):
    plugin_id: str
    version: str
    filename: str
    size_bytes: int
    sha256: str
    package_base64: str


class PluginTestOut(BaseModel):
    node_type: str
    outputs: dict[str, JsonValue]
    events: list[str]
    metrics: dict[str, float]


class PluginDetail(PluginOut):
    # The plugin's documentation file (truncated), untrusted text.
    documentation: str | None


class PluginUpdateOut(BaseModel):
    plugin: PluginOut
    # Projects using the plugin (non-empty only with force): their published
    # versions pinned to the old version no longer run.
    affected_projects: list[str]


def _check_out(check: PackageCheck) -> PackageCheckOut:
    m = check.metadata
    return PackageCheckOut(
        valid=check.ok,
        plugin_id=None if m is None else m.plugin_id,
        version=None if m is None else m.version,
        node_types=[] if m is None else [n.node_id for n in m.nodes],
        installed_state=None if check.installed is None else check.installed.value,
        issues=[
            IssueOut(code=i.code, message=i.message, path=i.path, line=i.line)
            for i in check.issues
        ],
    )


def _archive(files: dict[str, str] | None, package_base64: str | None) -> bytes:
    if files is not None and package_base64 is None:
        data = pack_files(files)
        if len(data) > MAX_ARCHIVE_BYTES:
            raise PluginPackageError(
                f"A plugin package must not exceed {MAX_ARCHIVE_BYTES} bytes."
            )
        return data
    if package_base64 is not None and files is None:
        return decode_base64(package_base64, MAX_ARCHIVE_BYTES)
    raise PluginPackageError("Pass exactly one of files and package_base64.")


def _documentation(service: PluginService, plugin_id: str) -> str | None:
    record = service.get(plugin_id)
    if record.metadata is None or not record.metadata.documentation:
        return None
    root = service.registry.path(plugin_id).resolve()
    named = root / record.metadata.documentation
    path = named.resolve()
    # Re-checked here although install validated it: the file is read now.
    if named.is_symlink() or not path.is_relative_to(root) or not path.is_file():
        return None
    with Path(path).open("rb") as handle:
        return handle.read(_MAX_DOC_BYTES).decode("utf-8", errors="replace")


def register(kit: Kit) -> None:
    c = kit.container

    def admin() -> None:
        require_admin(current_identity())

    def service() -> PluginService:
        admin()
        if c.plugin_service is None:
            raise ConfigurationError(
                "No plugin directory is configured.",
                details={"setting": "AKASHA_PLUGINS_DIR"},
            )
        return c.plugin_service

    def code_allowed() -> None:
        if not c.settings.mcp_plugin_code:
            raise PluginCodeNotAllowedError(
                "Running or installing plugin code over MCP is disabled on this"
                " installation; an operator must set it, or install with the CLI.",
                details={"setting": "AKASHA_MCP_PLUGIN_CODE"},
            )

    def checked(svc: PluginService, data: bytes) -> PluginMetadata:
        check = svc.check_archive(data)
        if check.metadata is None or not check.ok:
            raise PluginPackageError(
                "The plugin package is invalid; see plugin_validate.",
                details={"issues": [i.message for i in check.issues][:50]},
            )
        return check.metadata

    @kit.tool(title="List plugins", read_only=True)
    async def plugin_list() -> PluginList:
        """Admin only. Installed plugins with state (enabled, disabled,
        failed + error code, e.g. PLUGIN_INCOMPATIBLE), version and node
        types."""
        admin()
        records = [] if c.plugin_service is None else c.plugin_service.list()
        return PluginList(
            items=[PluginOut.from_record(r) for r in records],
            install_enabled=c.plugin_service is not None and c.settings.mcp_plugin_code,
        )

    @kit.tool(title="Get plugin", read_only=True)
    async def plugin_get(plugin_id: PluginId) -> PluginDetail:
        """Admin only. One installed plugin and its documentation file."""
        svc = service()
        return PluginDetail(
            **PluginOut.from_record(svc.get(plugin_id)).model_dump(),
            documentation=_documentation(svc, plugin_id),
        )

    @kit.tool(title="Get plugin SDK", read_only=True)
    async def plugin_sdk_get() -> SdkInfo:
        """Admin only. Everything needed to write a plugin: the SDK guide
        (manifest, node class, ports, config schema, context, credentials,
        logging, metrics, errors, lifecycle), the manifest JSON Schema and
        the compatibility versions. Read before plugin_create."""
        admin()
        return SdkInfo(
            sdk_version=SDK_VERSION,
            engine_version=__version__,
            guide=SDK_GUIDE,
            manifest_schema=manifest_schema(),
            port_types=[p.name for p in PortType],
            error_kinds=[k.name for k in NodeErrorKind],
            max_package_bytes=MAX_ARCHIVE_BYTES,
            plugin_code_allowed=c.settings.mcp_plugin_code,
        )

    @kit.tool(title="Create plugin scaffold", read_only=True)
    async def plugin_create(
        plugin_id: PluginId,
        node_name: Annotated[
            str,
            Field(
                pattern=r"^[a-z][a-z0-9_]*$",
                max_length=64,
                description="Node type name; the type id is <plugin_id>.<name>.",
            ),
        ],
        name: Annotated[str, Field(min_length=1, max_length=128)],
        description: Annotated[str, Field(max_length=2000)] = "",
    ) -> ScaffoldOut:
        """Admin only. A complete, valid starter package (manifest, one node
        with a string input/output and a config option, README) as files.
        Nothing is stored or installed: edit the files, then
        plugin_validate."""
        admin()
        return ScaffoldOut(
            plugin_id=plugin_id,
            node_type=f"{plugin_id}.{node_name}",
            files=scaffold(plugin_id, name, node_name, description),
        )

    @kit.tool(title="Validate plugin", read_only=True)
    async def plugin_validate(
        files: Files | None = None, package_base64: PackageBase64 | None = None
    ) -> PackageCheckOut:
        """Admin only. Check a plugin package without running any of its
        code: file paths, manifest, engine/SDK/dependency compatibility,
        Python syntax and entry points. Each issue has a code, message and
        (where known) file path and line."""
        return _check_out(service().check_archive(_archive(files, package_base64)))

    @kit.tool(title="Test plugin node")
    async def plugin_test(
        node_type: Annotated[str, Field(max_length=200)],
        files: Files | None = None,
        package_base64: PackageBase64 | None = None,
        config: dict[str, JsonValue] | None = None,
        inputs: dict[str, JsonValue] | None = None,
        timeout_seconds: Annotated[float, Field(gt=0, le=300)] = 30.0,
    ) -> PluginTestOut:
        """Admin + AKASHA_MCP_PLUGIN_CODE. Run one node of a not yet
        installed package once, with the engine's checks (contract, config
        schema, inputs, outputs). RUNS THE PACKAGE CODE in the engine
        process. Nothing is installed. Packages with python_requirements
        must be installed first."""
        svc = service()
        code_allowed()
        data = _archive(files, package_base64)
        checked(svc, data)
        run = await svc.test_archive(
            data,
            node_type,
            config=config,
            inputs=inputs,
            timeout_seconds=timeout_seconds,
        )
        outputs: Any = redact(dict(run.outputs))
        return PluginTestOut(
            node_type=node_type, outputs=outputs, events=run.events, metrics=run.metrics
        )

    @kit.tool(title="Package plugin", read_only=True)
    async def plugin_package(files: Files) -> PluginPackageOut:
        """Admin only. Build the plugin package ZIP (deterministic) from
        files, after the plugin_validate checks. The result installs with
        plugin_install / plugin_update or `akasha plugin install`."""
        svc = service()
        data = _archive(files, None)
        metadata = checked(svc, data)
        return PluginPackageOut(
            plugin_id=metadata.plugin_id,
            version=metadata.version,
            filename=f"{metadata.plugin_id}-{metadata.version}.zip",
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            package_base64=base64.b64encode(data).decode("ascii"),
        )

    @kit.tool(title="Install plugin")
    async def plugin_install(
        files: Files | None = None, package_base64: PackageBase64 | None = None
    ) -> PluginOut:
        """Admin + AKASHA_MCP_PLUGIN_CODE. Validate, install and enable a
        new plugin; its node types join the catalog (node_types_list).
        Atomic: on failure nothing is left behind. An installed id answers
        PLUGIN_ALREADY_INSTALLED (use plugin_update)."""
        svc = service()
        code_allowed()
        data = _archive(files, package_base64)
        checked(svc, data)
        return PluginOut.from_record(await svc.install_archive(data))

    @kit.tool(title="Update plugin", destructive=True)
    async def plugin_update(
        files: Files | None = None,
        package_base64: PackageBase64 | None = None,
        force: Force = False,
    ) -> PluginUpdateOut:
        """Admin + AKASHA_MCP_PLUGIN_CODE. Replace an installed plugin with
        a new package of the same id (and re-enable it if it was enabled).
        Atomic: on failure the old version is restored. Refused with
        PLUGIN_IN_USE while projects use it unless force: published versions
        stay pinned to the old version and refuse to run on the new one."""
        svc = service()
        code_allowed()
        data = _archive(files, package_base64)
        checked(svc, data)
        record, users = await svc.update_archive(data, force=force)
        return PluginUpdateOut(
            plugin=PluginOut.from_record(record), affected_projects=list(users)
        )

    @kit.tool(title="Enable plugin", idempotent=False)
    async def plugin_enable(plugin_id: PluginId) -> PluginOut:
        """Admin only. Activate an installed, disabled (or failed) plugin;
        compatibility is re-checked. Enabling an enabled plugin answers
        INVALID_STATE_TRANSITION."""
        return PluginOut.from_record(await service().enable(plugin_id))

    @kit.tool(title="Disable plugin")
    async def plugin_disable(plugin_id: PluginId) -> PluginOut:
        """Admin only. Deactivate a plugin: its node types leave the catalog
        and new runs using them fail; its metadata stays inspectable. Blocked
        (PLUGIN_IN_USE) while enabled plugins depend on it."""
        return PluginOut.from_record(await service().disable(plugin_id))

    @kit.tool(title="Remove plugin", destructive=True)
    async def plugin_remove(
        plugin_id: PluginId, force: Force = False
    ) -> RemovePluginResponse:
        """Admin only. Delete an installed plugin. Refused with PLUGIN_IN_USE
        (listing the projects whose draft or published versions use it)
        unless force."""
        users = await service().remove(plugin_id, force=force)
        return RemovePluginResponse(plugin_id=plugin_id, affected_projects=list(users))
