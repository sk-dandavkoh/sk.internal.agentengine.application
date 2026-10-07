import { z } from 'zod'
import type { ApiClient } from './client'

// Contract of backend api/routes.py SystemStatus: /health and /ready as
// seen through /api/v1, the only prefix the UI reaches.

export const systemStatusSchema = z.object({
    version: z.string(),
    ready: z.boolean(),
    /** Failing readiness check → its reason. Empty when ready. */
    failures: z.record(z.string(), z.string()),
})
export type SystemStatus = z.infer<typeof systemStatusSchema>

/** What the installation allows (backend EngineLimits), for every user. */
export const engineLimitsSchema = z.object({
    execution_timeout_seconds: z.number(),
    node_timeout_seconds: z.number(),
    node_test_timeout_seconds: z.number(),
    node_max_attempts: z.number().int(),
    graph_max_nodes: z.number().int(),
    graph_max_depth: z.number().int(),
    active_executions_per_workflow: z.number().int(),
    execution_concurrency: z.number().int(),
    node_output_max_bytes: z.number().int(),
    execution_output_max_bytes: z.number().int(),
    llm_timeout_seconds: z.number(),
    llm_max_output_tokens: z.number().int(),
    event_trigger_max_depth: z.number().int(),
})
export type EngineLimits = z.infer<typeof engineLimitsSchema>

export function getEngineLimits(client: ApiClient, signal?: AbortSignal) {
    return client.request('GET', '/system/limits', engineLimitsSchema, {
        signal,
    })
}

/** An LLM provider this installation configured: names and flags only. */
export const llmProviderSchema = z.object({
    name: z.string(),
    requires_credential: z.boolean(),
})
export type LlmProvider = z.infer<typeof llmProviderSchema>

export function listLlmProviders(client: ApiClient, signal?: AbortSignal) {
    return client.request(
        'GET',
        '/system/llm-providers',
        z.array(llmProviderSchema),
        { signal },
    )
}

export function getSystemStatus(client: ApiClient, signal?: AbortSignal) {
    return client.request('GET', '/system/status', systemStatusSchema, {
        signal,
    })
}
