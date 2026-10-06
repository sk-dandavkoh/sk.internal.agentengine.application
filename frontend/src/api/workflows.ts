import { z } from 'zod'
import type { ApiClient } from './client'
import { jsonObject, pageSchema, timestamp, type PageParams } from './schemas'

// Contracts of backend api/workflows.py and api/workflow_schemas.py.
// Defaults mirror the backend's: those fields are optional on the wire.

export const workflowStatusSchema = z.enum([
    'draft',
    'published',
    'active',
    'disabled',
    'archived',
])
export type WorkflowStatus = z.infer<typeof workflowStatusSchema>

/** Editor canvas coordinates; layout only, the runtime ignores them. */
export const nodePositionSchema = z.object({ x: z.number(), y: z.number() })
export type NodePosition = z.infer<typeof nodePositionSchema>

export const nodeInstanceSchema = z.object({
    id: z.string(),
    node_type: z.string(),
    config: jsonObject.default({}),
    label: z.string().default(''),
    /** null: never placed in the editor (created via API or import). */
    position: nodePositionSchema.nullable().default(null),
})
export type NodeInstance = z.infer<typeof nodeInstanceSchema>

export const edgeSchema = z.object({
    source_node_id: z.string(),
    source_port: z.string(),
    target_node_id: z.string(),
    target_port: z.string(),
})
export type Edge = z.infer<typeof edgeSchema>

export const graphSchema = z.object({
    nodes: z.array(nodeInstanceSchema).default([]),
    edges: z.array(edgeSchema).default([]),
})
export type Graph = z.infer<typeof graphSchema>

export const triggerTypeSchema = z.enum([
    'manual',
    'api',
    'cron',
    'interval',
    'webhook',
    'event',
])
export type TriggerType = z.infer<typeof triggerTypeSchema>

export const triggerSchema = z.object({
    id: z.string(),
    trigger_type: triggerTypeSchema,
    config: jsonObject.default({}),
    enabled: z.boolean().default(true),
})
export type Trigger = z.infer<typeof triggerSchema>

export const dependencySchema = z.object({
    kind: z.enum(['node', 'plugin']),
    name: z.string(),
    version_constraint: z.string().default('*'),
})
export type Dependency = z.infer<typeof dependencySchema>

/** The mutable, editable state of a workflow (GET returns it, PUT replaces it). */
export const workflowDraftSchema = z.object({
    graph: graphSchema.default({ nodes: [], edges: [] }),
    triggers: z.array(triggerSchema).default([]),
    dependencies: z.array(dependencySchema).default([]),
})
export type WorkflowDraft = z.infer<typeof workflowDraftSchema>

export const workflowFileSchema = z.object({
    path: z.string(),
    size: z.number().int(),
})
export type WorkflowFile = z.infer<typeof workflowFileSchema>

export const workflowSummarySchema = z.object({
    id: z.string(),
    name: z.string(),
    description: z.string(),
    status: workflowStatusSchema,
    owner_id: z.string().nullable(),
    /** Optimistic-concurrency token: send it back as `expected_revision`. */
    revision: z.number().int(),
    /** 0 until the first publish. */
    current_version: z.number().int(),
    created_at: timestamp,
    updated_at: timestamp,
})
export type WorkflowSummary = z.infer<typeof workflowSummarySchema>

export const workflowSchema = workflowSummarySchema.extend({
    draft: workflowDraftSchema,
    files: z.array(workflowFileSchema),
})
export type Workflow = z.infer<typeof workflowSchema>

export const workflowVersionSummarySchema = z.object({
    id: z.string(),
    version: z.number().int(),
    description: z.string(),
    schema_version: z.number().int(),
    published_at: timestamp,
    /** Null when not recorded (CLI publishes, older versions). */
    published_by: z
        .object({
            id: z.string(),
            /** Null only if the account cannot be found. */
            username: z.string().nullable(),
        })
        .nullable(),
})
export type WorkflowVersionSummary = z.infer<
    typeof workflowVersionSummarySchema
>

/** An immutable published snapshot of a draft. */
export const workflowVersionSchema = workflowVersionSummarySchema.extend({
    graph: graphSchema,
    triggers: z.array(triggerSchema),
    dependencies: z.array(dependencySchema),
    files: z.array(workflowFileSchema),
    // Plugin id -> exact version the version was published against (pinned).
    plugin_versions: z.record(z.string(), z.string()),
})
export type WorkflowVersion = z.infer<typeof workflowVersionSchema>

// Validation codes are reported, never decided, by the frontend: the
// backend's validate/publish answer is authoritative (AGENTS §4).
export const validationCodeSchema = z.enum([
    'empty_graph',
    'unknown_node_type',
    'edge_unknown_node',
    'duplicate_edge',
    'unknown_port',
    'invalid_port_connection',
    'missing_required_input',
    'invalid_config',
    'plaintext_secret',
    'cycle_detected',
    'disconnected_node',
    'missing_dependency',
    'incompatible_dependency',
    'invalid_dependency',
    'duplicate_trigger_id',
    'invalid_trigger_schedule',
    'invalid_trigger_event',
    'webhook_secret_required',
])
export type ValidationCode = z.infer<typeof validationCodeSchema>

/** One problem, located at a node, port, edge or trigger where it has one. */
export const validationIssueSchema = z.object({
    code: validationCodeSchema,
    message: z.string(),
    node_id: z.string().nullable(),
    port: z.string().nullable(),
    edge: edgeSchema.nullable(),
    trigger_id: z.string().nullable(),
})
export type ValidationIssue = z.infer<typeof validationIssueSchema>

export const validationResultSchema = z.object({
    valid: z.boolean(),
    issues: z.array(validationIssueSchema),
})
export type ValidationResult = z.infer<typeof validationResultSchema>

export const workflowPageSchema = pageSchema(workflowSummarySchema)
export type WorkflowPage = z.infer<typeof workflowPageSchema>
export const workflowVersionPageSchema = pageSchema(
    workflowVersionSummarySchema,
)
export type WorkflowVersionPage = z.infer<typeof workflowVersionPageSchema>

// backend application/persistence.py WorkflowSort; "-" means descending.
export const workflowSortSchema = z.enum([
    'created_at',
    '-created_at',
    'updated_at',
    '-updated_at',
    'name',
    '-name',
])
export type WorkflowSort = z.infer<typeof workflowSortSchema>

export interface WorkflowListParams extends PageParams {
    /** Name contains this text (any case); omit or leave blank for all. */
    q?: string
    /** Only these statuses; all when omitted. */
    status?: readonly WorkflowStatus[]
    /** Backend default: created_at (oldest first). */
    sort?: WorkflowSort
}

export interface CreateWorkflowInput {
    name: string
    description?: string
}

/** PUT semantics: name, description and draft are replaced whole. */
export interface UpdateWorkflowInput {
    name: string
    description: string
    draft: WorkflowDraft
    expected_revision: number
}

const path = (workflowId: string) =>
    `/workflows/${encodeURIComponent(workflowId)}`

export function listWorkflows(
    client: ApiClient,
    { q, ...params }: WorkflowListParams = {},
    signal?: AbortSignal,
) {
    // The backend rejects a blank search (422): blank means "no filter".
    const search = q?.trim()
    return client.request('GET', '/workflows', workflowPageSchema, {
        query: { ...params, q: search === '' ? undefined : search },
        signal,
    })
}

export function getWorkflow(
    client: ApiClient,
    workflowId: string,
    signal?: AbortSignal,
) {
    return client.request('GET', path(workflowId), workflowSchema, { signal })
}

export function createWorkflow(client: ApiClient, input: CreateWorkflowInput) {
    return client.request('POST', '/workflows', workflowSchema, { body: input })
}

export function updateWorkflow(
    client: ApiClient,
    workflowId: string,
    input: UpdateWorkflowInput,
) {
    return client.request('PUT', path(workflowId), workflowSchema, {
        body: input,
    })
}

/**
 * A new, never-published workflow with a copy of this workflow's draft, or
 * of its published `version`.
 */
export function duplicateWorkflow(
    client: ApiClient,
    workflowId: string,
    name: string,
    version?: number,
) {
    return client.request(
        'POST',
        `${path(workflowId)}/duplicate`,
        workflowSchema,
        { body: version === undefined ? { name } : { name, version } },
    )
}

/**
 * Replaces the draft with a copy of published `version` (the version is
 * unchanged). A draft changed since `expectedRevision` is a 409.
 */
export function restoreWorkflowVersion(
    client: ApiClient,
    workflowId: string,
    version: number,
    expectedRevision: number,
) {
    return client.request(
        'POST',
        `${path(workflowId)}/versions/${String(version)}/restore`,
        workflowSchema,
        { body: { expected_revision: expectedRevision } },
    )
}

export function deleteWorkflow(client: ApiClient, workflowId: string) {
    return client.request('DELETE', path(workflowId), z.undefined())
}

/**
 * Validates the stored draft; nothing is persisted. With `expectedRevision`
 * the backend rejects (409) a draft other than the one the caller shows.
 */
export function validateWorkflow(
    client: ApiClient,
    workflowId: string,
    expectedRevision?: number,
) {
    return client.request(
        'POST',
        `${path(workflowId)}/validate`,
        validationResultSchema,
        expectedRevision === undefined
            ? {}
            : { body: { expected_revision: expectedRevision } },
    )
}

/**
 * Publishes the draft as a new immutable version. With `expectedRevision`
 * the backend rejects (409) a draft changed since it was loaded.
 */
export function publishWorkflow(
    client: ApiClient,
    workflowId: string,
    expectedRevision?: number,
) {
    return client.request(
        'POST',
        `${path(workflowId)}/publish`,
        workflowVersionSchema,
        expectedRevision === undefined
            ? {}
            : { body: { expected_revision: expectedRevision } },
    )
}

export function listWorkflowVersions(
    client: ApiClient,
    workflowId: string,
    page: PageParams = {},
    signal?: AbortSignal,
) {
    return client.request(
        'GET',
        `${path(workflowId)}/versions`,
        workflowVersionPageSchema,
        { query: { ...page }, signal },
    )
}

export function getWorkflowVersion(
    client: ApiClient,
    workflowId: string,
    version: number,
    signal?: AbortSignal,
) {
    return client.request(
        'GET',
        `${path(workflowId)}/versions/${String(version)}`,
        workflowVersionSchema,
        { signal },
    )
}

/** Matches the backend's cap (application/workflow_package.py). */
export const MAX_WORKFLOW_PACKAGE_BYTES = 16 * 1024 * 1024

/** The workflow package (.akasha.zip) of the draft, or of published
 * `version`. Credential values are never part of it: only references. */
export function exportWorkflow(
    client: ApiClient,
    workflowId: string,
    version?: number,
) {
    return client.download('GET', `${path(workflowId)}/export`, {
        query: { version },
    })
}

export const importResultSchema = z.object({
    /** A new, never-published workflow holding the package's draft. */
    workflow: workflowSchema,
    validation: validationResultSchema,
    /** Referenced credentials this installation does not have yet. */
    missing_credentials: z.array(z.string()),
})
export type ImportResult = z.infer<typeof importResultSchema>

export function importWorkflow(client: ApiClient, file: Blob) {
    return client.request('POST', '/workflows/import', importResultSchema, {
        rawBody: { data: file, contentType: 'application/zip' },
    })
}
