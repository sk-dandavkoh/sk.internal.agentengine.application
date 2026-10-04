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
    /** The backend state machine allows cancel now; never derived here. */
    cancellable: z.boolean(),
})
export type Execution = z.infer<typeof executionSchema>

/** A listed execution: listings also name the project (its current name). */
export const executionListItemSchema = executionSchema.extend({
    project_name: z.string(),
})
export type ExecutionListItem = z.infer<typeof executionListItemSchema>

export const executionPageSchema = pageSchema(executionListItemSchema)
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

export const logLevelSchema = z.enum([
    'DEBUG',
    'INFO',
    'WARNING',
    'ERROR',
    'CRITICAL',
])
export type LogLevel = z.infer<typeof logLevelSchema>

/**
 * A line a node wrote through its logger. Redacted and bounded by the
 * backend; still untrusted text: render it as text, never as markup.
 */
export const logLineSchema = z.object({
    /** Monotonic per execution: pass the last seen one as `after`. */
    position: z.number().int(),
    node_id: z.string(),
    attempt: z.number().int(),
    level: logLevelSchema,
    message: z.string(),
    logged_at: timestamp,
    fields: jsonObject,
})
export type LogLine = z.infer<typeof logLineSchema>

export const logPageSchema = z.object({
    items: z.array(logLineSchema),
    next_after: z.number().int(),
    has_more: z.boolean(),
})
export type LogPage = z.infer<typeof logPageSchema>

export interface LogFilter {
    node_id?: string
    /** Minimum level; all levels when omitted. */
    level?: LogLevel
    /** Text the message contains (case-insensitive, literal). */
    q?: string
}

/** Every status with its count; the backend includes zeros. */
export const executionStatusCountsSchema = z.object({
    counts: z.record(executionStatusSchema, z.number().int()),
})
export type ExecutionStatusCounts = z.infer<typeof executionStatusCountsSchema>

export interface ExecutionListParams extends PageParams {
    /** Only these statuses; all when omitted. */
    status?: readonly ExecutionStatus[]
}

const path = (executionId: string) =>
    `/executions/${encodeURIComponent(executionId)}`

/** Executions across the caller's projects (all for an admin), newest first. */
export function listExecutions(
    client: ApiClient,
    params: ExecutionListParams = {},
    signal?: AbortSignal,
) {
    return client.request('GET', '/executions', executionPageSchema, {
        query: { ...params },
        signal,
    })
}

export function getExecutionStatusCounts(
    client: ApiClient,
    signal?: AbortSignal,
) {
    return client.request(
        'GET',
        '/executions/status-counts',
        executionStatusCountsSchema,
        { signal },
    )
}

export interface ProjectExecutionListParams extends PageParams {
    /** Only runs of this published version; all when omitted. */
    version?: number
}

export function listProjectExecutions(
    client: ApiClient,
    projectId: string,
    params: ProjectExecutionListParams = {},
    signal?: AbortSignal,
) {
    return client.request(
        'GET',
        `/projects/${encodeURIComponent(projectId)}/executions`,
        executionPageSchema,
        { query: { ...params }, signal },
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

export function listExecutionLogs(
    client: ApiClient,
    executionId: string,
    params: LogFilter & { after?: number; limit?: number } = {},
    signal?: AbortSignal,
) {
    return client.request('GET', `${path(executionId)}/logs`, logPageSchema, {
        query: { ...params },
        signal,
    })
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
