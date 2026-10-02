import { z } from 'zod'
import type { ApiClient } from './client'
import { dependencySchema } from './projects'

// Contract of backend api/plugins.py (admin only, read-only): installing
// and enabling plugins is an operator CLI task.

export const pluginStateSchema = z.enum(['enabled', 'disabled', 'failed'])
export type PluginState = z.infer<typeof pluginStateSchema>

export const pluginSchema = z.object({
    plugin_id: z.string(),
    state: pluginStateSchema,
    /** Stable error code when `failed`. */
    error: z.string().nullable(),
    /** Null when the plugin's manifest could not be read. */
    metadata: z
        .object({
            name: z.string(),
            version: z.string(),
            description: z.string(),
            engine_compatibility: z.string(),
            sdk_compatibility: z.string(),
            dependencies: z.array(dependencySchema),
            node_types: z.array(z.string()),
        })
        .nullable(),
})
export type Plugin = z.infer<typeof pluginSchema>

export const pluginListSchema = z.object({ items: z.array(pluginSchema) })

export function listPlugins(client: ApiClient, signal?: AbortSignal) {
    return client.request('GET', '/plugins', pluginListSchema, { signal })
}
