import { z } from 'zod'
import type { ApiClient } from './client'
import { jsonObject } from './schemas'

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
})

export function listNodeDefinitions(client: ApiClient, signal?: AbortSignal) {
    return client.request('GET', '/nodes', nodeCatalogSchema, { signal })
}
