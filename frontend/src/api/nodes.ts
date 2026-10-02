import { z } from 'zod'
import type { ApiClient } from './client'
import { jsonObject, type JsonObject } from './schemas'

// Contract of backend api/nodes.py: the node catalog from the backend's
// Node Registry. The frontend keeps no list of its own (AGENTS §24).

export const nodePortSchema = z.object({
    name: z.string(),
    type: z.string(),
    required: z.boolean(),
    nullable: z.boolean(),
    multiple: z.boolean(),
    description: z.string(),
    /** JSON Schema of the value, where the port declares one. */
    schema: jsonObject.nullable().optional(),
})
export type NodePort = z.infer<typeof nodePortSchema>

export const nodeDefinitionSchema = z.object({
    node_type: z.string(),
    version: z.string(),
    name: z.string(),
    description: z.string(),
    category: z.string(),
    documentation_url: z.string().nullable(),
    inputs: z.array(nodePortSchema),
    outputs: z.array(nodePortSchema),
    /** JSON Schema of the node's config; drives the inspector form. */
    config_schema: jsonObject,
    capabilities: z.record(z.string(), z.boolean()),
})
export type NodeDefinition = z.infer<typeof nodeDefinitionSchema>

export const nodeCatalogSchema = z.object({
    items: z.array(nodeDefinitionSchema),
    /** The backend's port rule as data: output signature (`portSignature`)
     * to the input signatures it may connect to. */
    port_compatibility: z.record(z.string(), z.array(z.string())),
})
export type NodeCatalog = z.infer<typeof nodeCatalogSchema>

export function listNodeDefinitions(client: ApiClient, signal?: AbortSignal) {
    return client.request('GET', '/nodes', nodeCatalogSchema, { signal })
}

/** One violation, located by keys and list indexes from the config root. */
export const configIssueSchema = z.object({
    path: z.array(z.union([z.string(), z.number().int()])),
    message: z.string(),
})
export type ConfigIssue = z.infer<typeof configIssueSchema>

export const configValidationSchema = z.object({
    valid: z.boolean(),
    issues: z.array(configIssueSchema),
})
export type ConfigValidation = z.infer<typeof configValidationSchema>

/**
 * The backend's verdict on one node config: everything publish would
 * reject, per field. Stateless (nothing is stored); the backend stays the
 * only judge of config validity (AGENTS §4).
 */
export function validateNodeConfig(
    client: ApiClient,
    nodeType: string,
    config: JsonObject,
    signal?: AbortSignal,
) {
    return client.request(
        'POST',
        `/nodes/${encodeURIComponent(nodeType)}/config/validate`,
        configValidationSchema,
        { body: { config }, signal },
    )
}
