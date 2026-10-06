import { z } from 'zod'
import type { ApiClient } from './client'
import { dependencySchema } from './workflows'

// Contract of backend api/plugins.py (admin only, ADR 0001). Every rule —
// compatibility, dependencies, whether a plugin is in use — is decided by
// the backend; this module only carries its answers.

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

export const pluginListSchema = z.object({
    items: z.array(pluginSchema),
    /** Whether this installation accepts package uploads; otherwise the
     *  operator installs with `akasha plugin install`. */
    install_enabled: z.boolean(),
})
export type PluginList = z.infer<typeof pluginListSchema>

export const removePluginResponseSchema = z.object({
    plugin_id: z.string(),
    /** Workflows that used the plugin (non-empty only when forced). */
    affected_workflows: z.array(z.string()),
})

/** Matches the backend's cap (plugins/archive.py MAX_ARCHIVE_BYTES). */
export const MAX_PLUGIN_PACKAGE_BYTES = 16 * 1024 * 1024
const INSTALL_TIMEOUT_MS = 120_000

const path = (pluginId: string) => `/plugins/${encodeURIComponent(pluginId)}`

export function listPlugins(client: ApiClient, signal?: AbortSignal) {
    return client.request('GET', '/plugins', pluginListSchema, { signal })
}

export function installPlugin(client: ApiClient, file: Blob) {
    return client.request('POST', '/plugins', pluginSchema, {
        rawBody: { data: file, contentType: 'application/zip' },
        timeoutMs: INSTALL_TIMEOUT_MS,
    })
}

export function enablePlugin(client: ApiClient, pluginId: string) {
    return client.request('POST', `${path(pluginId)}/enable`, pluginSchema)
}

export function disablePlugin(client: ApiClient, pluginId: string) {
    return client.request('POST', `${path(pluginId)}/disable`, pluginSchema)
}

export function removePlugin(
    client: ApiClient,
    pluginId: string,
    { force = false }: { force?: boolean } = {},
) {
    return client.request(
        'DELETE',
        path(pluginId),
        removePluginResponseSchema,
        {
            query: force ? { force: true } : {},
        },
    )
}
