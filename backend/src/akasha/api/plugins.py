"""/api/v1/plugins: the installed plugins and their lifecycle (architecture
§22, §36, ADR 0001). Admin only (mounted behind ``require_admin``).

- GET    /plugins                 installed plugins with state + metadata
- POST   /plugins                 install an uploaded package (application/zip);
                                  only with AKASHA_PLUGINS_HTTP_INSTALL=true
- POST   /plugins/{id}/enable     activate (compatibility re-checked)
- POST   /plugins/{id}/disable    deactivate; blocked while enabled plugins
                                  depend on it
- DELETE /plugins/{id}?force=     remove; blocked while workflows use it
                                  unless ``force`` (same use case as the CLI)

Every operation is ``application.plugins.PluginService`` (shared with the
MCP server, one lifecycle lock per process) — no plugin rule lives here.
Changes apply to this process; dedicated worker processes see them after a
restart (§36).
"""

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel

from akasha.api.schemas import ErrorResponse, PayloadTooLargeError, read_limited_body
from akasha.api.workflow_schemas import DependencyBody, _dependencies
from akasha.application.plugins import PluginService
from akasha.bootstrap import Container
from akasha.errors import AkashaError, ConfigurationError
from akasha.plugins.archive import MAX_ARCHIVE_BYTES, MEDIA_TYPE
from akasha.plugins.registry import PluginRecord, PluginState


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
    # Workflows that used the plugin; non-empty only for a forced removal.
    affected_workflows: list[str]


def _container(request: Request) -> Container:
    container: Container = request.app.state.container
    return container


def _service(request: Request) -> PluginService:
    service = _container(request).plugin_service
    if service is None:
        raise ConfigurationError(
            "No plugin directory is configured.",
            details={"setting": "AKASHA_PLUGINS_DIR"},
        )
    return service


def _install_enabled(request: Request) -> bool:
    container = _container(request)
    return container.plugins is not None and container.settings.plugins_http_install


def create_plugins_router() -> APIRouter:
    errors: dict[int | str, dict[str, Any]] = {
        code: {"model": ErrorResponse} for code in (403, 404, 409, 422, 500)
    }
    router = APIRouter(prefix="/plugins", tags=["plugins"], responses=errors)

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
        # ponytail: unpack, copy and import stay on the event loop (bounded
        # by the archive limits); only installing libraries is slow.
        record = await _service(request).install_archive(data)
        return PluginOut.from_record(record)

    @router.post("/{plugin_id}/enable")
    async def enable_plugin(plugin_id: str, request: Request) -> PluginOut:
        return PluginOut.from_record(await _service(request).enable(plugin_id))

    @router.post("/{plugin_id}/disable")
    async def disable_plugin(plugin_id: str, request: Request) -> PluginOut:
        return PluginOut.from_record(await _service(request).disable(plugin_id))

    @router.delete("/{plugin_id}")
    async def delete_plugin(
        plugin_id: str,
        request: Request,
        force: Annotated[
            bool, Query(description="Remove even though workflows use it")
        ] = False,
    ) -> RemovePluginResponse:
        users = await _service(request).remove(plugin_id, force=force)
        return RemovePluginResponse(plugin_id=plugin_id, affected_workflows=list(users))

    return router
