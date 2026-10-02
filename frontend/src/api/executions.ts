import { z } from 'zod'
import type { ApiClient } from './client'
import { triggerTypeSchema } from './projects'
import {
    jsonObject,
    pageSchema,
    timestamp,
    type JsonObject,
    type PageParams,
} from './schemas'

// Contracts of backend api/executions.py and api/triggers.py. Node-level
// detail (NodeExecution) is part of the execution report (reports.ts).

// Transitions between these are the backend's state machine; the frontend
// only displays them (AGENTS §23).
export const executionStatusSchema = z.enum([
    'created',
    'queued',
    'running',
    'paused',
    'waiting',
    'completed',
    'failed',
    'cancelled',
    'timeout',
])
export type ExecutionStatus = z.infer<typeof executionStatusSchema>

export const executionSchema = z.object({
    id: z.string(),
    project_id: z.string(),
    /** The published project version this execution runs. */
    version: z.number().int(),
    status: executionStatusSchema,
    trigger_type: triggerTypeSchema,
    trigger_id: z.string().nullable(),
    correlation_id: z.string().nullable(),
    created_at: timestamp,
    started_at: timestamp.nullable(),
    finished_at: timestamp.nullable(),
})
export type Execution = z.infer<typeof executionSchema>

export const executionPageSchema = pageSchema(executionSchema)
export type ExecutionPage = z.infer<typeof executionPageSchema>

/** A stored execution event; the payload is redacted by the backend. */
export const executionEventSchema = z.object({
    event_id: z.string(),
    event_type: z.string(),
    schema_version: z.number().int(),
    /** Monotonic per execution: pass the last seen one as `after`. */
    position: z.number().int(),
    occurred_at: timestamp,
    project_id: z.string(),
    project_version: z.number().int().nullable(),
    execution_id: z.string().nullable(),
    node_id: z.string().nullable(),
    correlation_id: z.string().nullable(),
    payload: jsonObject,
})
export type ExecutionEvent = z.infer<typeof executionEventSchema>

export const executionEventPageSchema = z.object({
    items: z.array(executionEventSchema),
    next_after: z.number().int(),
    has_more: z.boolean(),
})
export type ExecutionEventPage = z.infer<typeof executionEventPageSchema>

const path = (executionId: string) =>
    `/executions/${encodeURIComponent(executionId)}`

export function listProjectExecutions(
    client: ApiClient,
    projectId: string,
    page: PageParams = {},
    signal?: AbortSignal,
) {
    return client.request(
        'GET',
        `/projects/${encodeURIComponent(projectId)}/executions`,
        executionPageSchema,
        { query: { ...page }, signal },
    )
}

export function getExecution(
    client: ApiClient,
    executionId: string,
    signal?: AbortSignal,
) {
    return client.request('GET', path(executionId), executionSchema, {
        signal,
    })
}

export function listExecutionEvents(
    client: ApiClient,
    executionId: string,
    params: { after?: number; limit?: number } = {},
    signal?: AbortSignal,
) {
    return client.request(
        'GET',
        `${path(executionId)}/events`,
        executionEventPageSchema,
        { query: { ...params }, signal },
    )
}

/** Accepted, not finished: the worker stops at its next heartbeat. */
export function cancelExecution(
    client: ApiClient,
    executionId: string,
    reason?: string,
) {
    return client.request(
        'POST',
        `${path(executionId)}/cancel`,
        executionSchema,
        reason === undefined ? {} : { body: { reason } },
    )
}

/** Starts a manual run of a published version with `input`. */
export function executeProjectVersion(
    client: ApiClient,
    projectId: string,
    version: number,
    input: JsonObject = {},
) {
    return client.request(
        'POST',
        `/projects/${encodeURIComponent(projectId)}/versions/${String(version)}/execute`,
        executionSchema,
        { body: input },
    )
}

/** Fires a manual or API trigger of the project's current version. */
export function invokeTrigger(
    client: ApiClient,
    projectId: string,
    triggerId: string,
    input: JsonObject = {},
) {
    return client.request(
        'POST',
        `/projects/${encodeURIComponent(projectId)}/triggers/${encodeURIComponent(triggerId)}/invoke`,
        executionSchema,
        { body: input },
    )
}
