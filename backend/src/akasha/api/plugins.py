"""/api/v1/plugins: the installed plugins and their lifecycle (architecture
§22, §36, ADR 0001). Admin only (mounted behind ``require_admin``).

- GET    /plugins                 installed plugins with state + metadata
- POST   /plugins                 install an uploaded package (application/zip);
                                  only with AKASHA_PLUGINS_HTTP_INSTALL=true
- POST   /plugins/{id}/enable     activate (compatibility re-checked)
- POST   /plugins/{id}/disable    deactivate; blocked while enabled plugins
                                  depend on it
- DELETE /plugins/{id}?force=     remove; blocked while projects use it
                                  unless ``force`` (same use case as the CLI)

Every operation is ``PluginRegistry`` / ``application.plugins`` — no plugin
rule lives here. Changes apply to this process; dedicated worker processes
see them after a restart (§36).
"""

import asyncio
import tempfile
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel

from akasha.api.project_schemas import DependencyBody, _dependencies
from akasha.api.schemas import ErrorResponse, PayloadTooLargeError, read_limited_body
from akasha.application.plugins import remove_plugin
from akasha.application.projects import ProjectService
from akasha.bootstrap import Container
from akasha.errors import AkashaError, ConfigurationError
from akasha.plugins.archive import MAX_ARCHIVE_BYTES, MEDIA_TYPE, unpack_package
from akasha.plugins.registry import PluginRecord, PluginRegistry, PluginState


class PluginInstallDisabledError(AkashaError):
    code = "PLUGIN_INSTALL_DISABLED"
    http_status = 403


class PluginMetadataOut(BaseModel):
    name: str
    version: str
    description: str
    engine_compatibility: str
    sdk_compatibility: str
    dependencies: list[DependencyBody]
    node_types: list[str]


class PluginOut(BaseModel):
    plugin_id: str
    state: PluginState
    # Stable error code when FAILED (e.g. PLUGIN_LOAD_FAILED).
    error: str | None
    # None when the manifest itself could not be read.
    metadata: PluginMetadataOut | None

    @classmethod
    def from_record(cls, record: PluginRecord) -> "PluginOut":
        m = record.metadata
        return cls(
            plugin_id=record.plugin_id,
            state=record.state,
            error=record.error,
            metadata=None
            if m is None
            else PluginMetadataOut(
                name=m.name,
                version=m.version,
                description=m.description,
                engine_compatibility=m.engine_compatibility,
                sdk_compatibility=m.sdk_compatibility,
                dependencies=_dependencies(m.dependencies),
                node_types=[n.node_id for n in m.nodes],
            ),
        )


class PluginList(BaseModel):
    # ponytail: unpaginated like the node catalog — bounded by the installed
    # packages, not user data. Paginate if installations grow large.
    items: list[PluginOut]
    # Whether POST /plugins accepts uploads here (plugin directory set and
    # AKASHA_PLUGINS_HTTP_INSTALL on); otherwise install with the CLI.
    install_enabled: bool


class RemovePluginResponse(BaseModel):
    plugin_id: str
    # Projects that used the plugin; non-empty only for a forced removal.
    affected_projects: list[str]


def _container(request: Request) -> Container:
    container: Container = request.app.state.container
    return container


def _registry(request: Request) -> PluginRegistry:
    registry = _container(request).plugins
    if registry is None:
        raise ConfigurationError(
            "No plugin directory is configured.",
            details={"setting": "AKASHA_PLUGINS_DIR"},
        )
    return registry


def _projects(request: Request) -> ProjectService:
    projects = _container(request).projects
    if projects is None:
        raise ConfigurationError("Plugin removal needs the database.")
    return projects


def _install_enabled(request: Request) -> bool:
    container = _container(request)
    return container.plugins is not None and container.settings.plugins_http_install


def create_plugins_router() -> APIRouter:
    errors: dict[int | str, dict[str, Any]] = {
        code: {"model": ErrorResponse} for code in (403, 404, 409, 422, 500)
    }
    router = APIRouter(prefix="/plugins", tags=["plugins"], responses=errors)
    # Serializes lifecycle calls: ``prepare`` runs in a thread (it may
    # install Python libraries for minutes, ADR 0004) and the registry is
    # not locked itself. Registry state only changes on the event loop.
    lifecycle = asyncio.Lock()

    @router.get("")
    async def list_plugins(request: Request) -> PluginList:
        # No plugin directory configured: the installation has no plugins.
        registry = _container(request).plugins
        records = [] if registry is None else registry.list_plugins()
        return PluginList(
            items=[PluginOut.from_record(r) for r in records],
            install_enabled=_install_enabled(request),
        )

    @router.post(
        "",
        status_code=status.HTTP_201_CREATED,
        responses={413: {"model": ErrorResponse}, 415: {"model": ErrorResponse}},
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {
                    MEDIA_TYPE: {"schema": {"type": "string", "format": "binary"}}
                },
            }
        },
    )
    async def install_plugin(request: Request) -> PluginOut:
        # Checked before the body is read: a refused upload is never buffered.
        if not _install_enabled(request):
            raise PluginInstallDisabledError(
                "Plugin upload is disabled on this installation; "
                "install plugins with the CLI (akasha plugin install).",
                details={"setting": "AKASHA_PLUGINS_HTTP_INSTALL"},
            )
        media_type = request.headers.get("content-type", "").split(";")[0].strip()
        if media_type != MEDIA_TYPE:
            raise HTTPException(
                status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                f"A plugin package must be sent as {MEDIA_TYPE}.",
            )
        data = await read_limited_body(
            request,
            MAX_ARCHIVE_BYTES,
            PayloadTooLargeError(
                f"A plugin package must not exceed {MAX_ARCHIVE_BYTES} bytes."
            ),
        )
        registry = _registry(request)
        # ponytail: unpack, copy and import stay on the event loop (bounded
        # by the archive limits); only installing libraries is slow.
        async with lifecycle:
            with tempfile.TemporaryDirectory(prefix="akasha-plugin-upload-") as tmp:
                package = unpack_package(data, Path(tmp))
                await asyncio.to_thread(registry.prepare, package)
                record = registry.install(package)
        return PluginOut.from_record(record)

    @router.post("/{plugin_id}/enable")
    async def enable_plugin(plugin_id: str, request: Request) -> PluginOut:
        registry = _registry(request)
        async with lifecycle:
            if registry.get(plugin_id).metadata is not None:
                await asyncio.to_thread(registry.prepare, registry.path(plugin_id))
            return PluginOut.from_record(registry.enable(plugin_id))

    @router.post("/{plugin_id}/disable")
    async def disable_plugin(plugin_id: str, request: Request) -> PluginOut:
        async with lifecycle:
            return PluginOut.from_record(_registry(request).disable(plugin_id))

    @router.delete("/{plugin_id}")
    async def delete_plugin(
        plugin_id: str,
        request: Request,
        force: Annotated[
            bool, Query(description="Remove even though projects use it")
        ] = False,
    ) -> RemovePluginResponse:
        async with lifecycle:
            users = await remove_plugin(
                _registry(request), _projects(request), plugin_id, force=force
            )
        return RemovePluginResponse(plugin_id=plugin_id, affected_projects=list(users))

    return router
