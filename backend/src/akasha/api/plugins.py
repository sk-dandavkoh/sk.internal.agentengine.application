"""GET /api/v1/plugins: the installed plugins and their state (architecture
§22). Read-only: installing, enabling, disabling and removing plugins stays
an operator CLI task (plugins are trusted code, §65)."""

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

from akasha.api.project_schemas import DependencyBody, _dependencies
from akasha.api.schemas import ErrorResponse
from akasha.plugins.registry import PluginRecord, PluginRegistry, PluginState


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


def create_plugins_router() -> APIRouter:
    errors: dict[int | str, dict[str, Any]] = {500: {"model": ErrorResponse}}
    router = APIRouter(prefix="/plugins", tags=["plugins"], responses=errors)

    @router.get("")
    async def list_plugins(request: Request) -> PluginList:
        # No plugin directory configured: the installation has no plugins.
        registry: PluginRegistry | None = request.app.state.container.plugins
        records = [] if registry is None else registry.list_plugins()
        return PluginList(items=[PluginOut.from_record(r) for r in records])

    return router
