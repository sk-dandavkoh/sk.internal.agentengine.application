import { z } from 'zod'
import type { ApiClient } from './client'
import { triggerTypeSchema } from './workflows'
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
    workflow_id: z.string(),
    /** The published workflow version this execution runs. */
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

/** A listed execution: listings also name the workflow (its current name). */
export const executionListItemSchema = executionSchema.extend({
    workflow_name: z.string(),
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
    workflow_id: z.string(),
    workflow_version: z.number().int().nullable(),
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

/**
 * What a waiting node asks for (core.human). `response_schema` uses the
 * node config schema subset, so the config form renders it; the backend
 * validates every answer against it.
 */
export const pendingInputSchema = z.object({
    node_id: z.string(),
    /** Rendered from workflow data: show as text, never as markup. */
    prompt: z.string(),
    response_schema: jsonObject,
    /** After it the node continues as timed out. */
    deadline: timestamp.nullable(),
})
export type PendingInput = z.infer<typeof pendingInputSchema>

export const pendingInputsSchema = z.object({
    items: z.array(pendingInputSchema),
})

const path = (executionId: string) =>
    `/executions/${encodeURIComponent(executionId)}`

/** Executions across the caller's workflows (all for an admin), newest first. */
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

export interface WorkflowExecutionListParams extends PageParams {
    /** Only runs of this published version; all when omitted. */
    version?: number
}

export function listWorkflowExecutions(
    client: ApiClient,
    workflowId: string,
    params: WorkflowExecutionListParams = {},
    signal?: AbortSignal,
) {
    return client.request(
        'GET',
        `/workflows/${encodeURIComponent(workflowId)}/executions`,
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

export function listPendingInputs(
    client: ApiClient,
    executionId: string,
    signal?: AbortSignal,
) {
    return client.request(
        'GET',
        `${path(executionId)}/inputs`,
        pendingInputsSchema,
        { signal },
    )
}

/** Answers a waiting node; the execution is queued to continue. */
export function respondToInput(
    client: ApiClient,
    executionId: string,
    nodeId: string,
    response: JsonObject,
) {
    return client.request(
        'POST',
        `${path(executionId)}/inputs/${encodeURIComponent(nodeId)}`,
        executionSchema,
        { body: { response } },
    )
}

/** Starts a manual run of a published version with `input`. */
export function executeWorkflowVersion(
    client: ApiClient,
    workflowId: string,
    version: number,
    input: JsonObject = {},
) {
    return client.request(
        'POST',
        `/workflows/${encodeURIComponent(workflowId)}/versions/${String(version)}/execute`,
        executionSchema,
        { body: input },
    )
}

/** Fires a manual or API trigger of the workflow's current version. */
export function invokeTrigger(
    client: ApiClient,
    workflowId: string,
    triggerId: string,
    input: JsonObject = {},
) {
    return client.request(
        'POST',
        `/workflows/${encodeURIComponent(workflowId)}/triggers/${encodeURIComponent(triggerId)}/invoke`,
        executionSchema,
        { body: input },
    )
}
