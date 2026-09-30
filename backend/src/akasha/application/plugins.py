"""Plugin removal use case (architecture §143: prefer safe blocking).

``PluginRegistry.remove`` alone never looks at projects; this is the
operator-facing removal that does. A project needs a plugin when its draft
or any published version uses one of the plugin's node types or declares a
plugin dependency on it. Published versions count too: they are retained
history that executions may still run.
"""

import logging
from collections.abc import Sequence

from akasha.application.projects import ProjectService
from akasha.plugins.registry import PluginInUseError, PluginRegistry

logger = logging.getLogger("akasha.application.plugins")


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
    metadata = registry.get(plugin_id).metadata
    node_types = [] if metadata is None else [n.node_id for n in metadata.nodes]
    users = await projects.projects_using_plugin(plugin_id, node_types)
    if users and not force:
        raise PluginInUseError(
            "Projects use this plugin; remove it with force to break them.",
            details={"plugin_id": plugin_id, "projects": list(users)},
        )
    registry.remove(plugin_id)
    if users:
        logger.warning(
            "plugin_removed_in_use",
            extra={
                "event": "plugin_removed_in_use",
                "plugin_id": plugin_id,
                "project_count": len(users),
            },
        )
    return users
