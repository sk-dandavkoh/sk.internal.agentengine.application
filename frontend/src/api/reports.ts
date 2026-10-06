import { z } from 'zod'
import type { ApiClient } from './client'
import { executionStatusSchema } from './executions'
import { triggerTypeSchema } from './workflows'
import { jsonObject, timestamp } from './schemas'

// Contract of backend application/reports.py (GET /reports/executions/{id}):
// an execution reconstructed from persisted state, node by node.

export const nodeExecutionStatusSchema = z.enum([
    'pending',
    'ready',
    'running',
    // Asked for outside input (core.human); answering makes it ready again.
    'waiting',
    'completed',
    'failed',
    'skipped',
    'cancelled',
])
export type NodeExecutionStatus = z.infer<typeof nodeExecutionStatusSchema>

export const executionErrorSchema = z.object({
    code: z.string(),
    message: z.string(),
    kind: z.string(),
    node_id: z.string().nullable(),
    details: z.record(z.string(), z.string()),
    retryable: z.boolean(),
})
export type ExecutionError = z.infer<typeof executionErrorSchema>

/** A failed attempt of a node; `attempt` numbers it (1 = the first). */
export const nodeFailureSchema = executionErrorSchema.extend({
    attempt: z.number().int(),
})
export type NodeFailure = z.infer<typeof nodeFailureSchema>

export const tokenUsageSchema = z.object({
    input_tokens: z.number().int(),
    output_tokens: z.number().int(),
    total_tokens: z.number().int(),
})
export type TokenUsage = z.infer<typeof tokenUsageSchema>

export const costSchema = z.object({
    amount: z.number(),
    currency: z.string(),
})
export type Cost = z.infer<typeof costSchema>

/** One node of the pinned graph within an execution. */
export const nodeExecutionSchema = z.object({
    node_id: z.string(),
    node_type: z.string(),
    label: z.string(),
    status: nodeExecutionStatusSchema,
    attempts: z.number().int(),
    retries: z.number().int(),
    started_at: timestamp.nullable(),
    finished_at: timestamp.nullable(),
    duration_ms: z.number().int().nullable(),
    error: executionErrorSchema.nullable(),
    failures: z.array(nodeFailureSchema),
    outputs: jsonObject.nullable(),
    usage: tokenUsageSchema.nullable(),
    cost: costSchema.nullable(),
})
export type NodeExecution = z.infer<typeof nodeExecutionSchema>

export const executionMetricsSchema = z.object({
    node_status_counts: z.record(z.string(), z.number().int()),
    attempts: z.number().int(),
    retries: z.number().int(),
    failed_attempts: z.number().int(),
    usage: tokenUsageSchema,
    costs: z.array(costSchema),
    cost_complete: z.boolean(),
})
export type ExecutionMetrics = z.infer<typeof executionMetricsSchema>

export const executionReportSchema = z.object({
    schema_version: z.number().int(),
    execution_id: z.string(),
    workflow: z.object({
        id: z.string(),
        name: z.string().nullable(),
        version: z.number().int(),
        version_description: z.string(),
        published_at: timestamp,
    }),
    execution: z.object({
        status: executionStatusSchema,
        trigger_type: triggerTypeSchema,
        trigger_id: z.string().nullable(),
        correlation_id: z.string().nullable(),
        engine_version: z.string(),
        node_versions: z.record(z.string(), z.string()),
        created_at: timestamp,
        started_at: timestamp.nullable(),
        finished_at: timestamp.nullable(),
        duration_ms: z.number().int().nullable(),
        input: jsonObject,
        error: executionErrorSchema.nullable(),
        cancellation: z
            .object({ requested_at: timestamp, reason: z.string() })
            .nullable(),
    }),
    nodes: z.array(nodeExecutionSchema),
    metrics: executionMetricsSchema,
    events: z.array(
        z.object({
            position: z.number().int().nullable(),
            event_type: z.string(),
            occurred_at: timestamp,
            node_id: z.string().nullable(),
            payload: jsonObject,
        }),
    ),
    events_truncated: z.boolean(),
})
export type ExecutionReport = z.infer<typeof executionReportSchema>

export function getExecutionReport(
    client: ApiClient,
    executionId: string,
    signal?: AbortSignal,
) {
    return client.request(
        'GET',
        `/reports/executions/${encodeURIComponent(executionId)}`,
        executionReportSchema,
        { signal },
    )
}

/**
 * The export: the backend's canonical report JSON, byte for byte (sorted
 * keys, deterministic for the same persisted state). Validated first, so a
 * malformed report is an error, never a saved file.
 */
export async function exportExecutionReport(
    client: ApiClient,
    executionId: string,
    signal?: AbortSignal,
) {
    const { body } = await client.requestWithBody(
        'GET',
        `/reports/executions/${encodeURIComponent(executionId)}`,
        executionReportSchema,
        { signal },
    )
    return body
}
