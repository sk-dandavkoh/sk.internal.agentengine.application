import type { Plugin } from '../../api/plugins'

/** Display name; the id when the manifest could not be read. */
export function pluginLabel(plugin: Plugin) {
    return plugin.metadata?.name ?? plugin.plugin_id
}
