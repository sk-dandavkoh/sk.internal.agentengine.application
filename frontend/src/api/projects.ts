import { z } from 'zod'
import type { ApiClient } from './client'
import { jsonObject, pageSchema, timestamp, type PageParams } from './schemas'

// Contracts of backend api/projects.py and api/project_schemas.py.
// Defaults mirror the backend's: those fields are optional on the wire.

export const projectStatusSchema = z.enum([
    'draft',
    'published',
    'active',
    'disabled',
    'archived',
])
export type ProjectStatus = z.infer<typeof projectStatusSchema>

export const nodeInstanceSchema = z.object({
    id: z.string(),
    node_type: z.string(),
    config: jsonObject.default({}),
    label: z.string().default(''),
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

/** The mutable, editable state of a project (GET returns it, PUT replaces it). */
export const projectDraftSchema = z.object({
    graph: graphSchema.default({ nodes: [], edges: [] }),
    triggers: z.array(triggerSchema).default([]),
    dependencies: z.array(dependencySchema).default([]),
})
export type ProjectDraft = z.infer<typeof projectDraftSchema>

export const projectFileSchema = z.object({
    path: z.string(),
    size: z.number().int(),
})
export type ProjectFile = z.infer<typeof projectFileSchema>

export const projectSummarySchema = z.object({
    id: z.string(),
    name: z.string(),
    description: z.string(),
    status: projectStatusSchema,
    owner_id: z.string().nullable(),
    /** Optimistic-concurrency token: send it back as `expected_revision`. */
    revision: z.number().int(),
    /** 0 until the first publish. */
    current_version: z.number().int(),
    created_at: timestamp,
    updated_at: timestamp,
})
export type ProjectSummary = z.infer<typeof projectSummarySchema>

export const projectSchema = projectSummarySchema.extend({
    draft: projectDraftSchema,
    files: z.array(projectFileSchema),
})
export type Project = z.infer<typeof projectSchema>

export const projectVersionSummarySchema = z.object({
    id: z.string(),
    version: z.number().int(),
    description: z.string(),
    schema_version: z.number().int(),
    published_at: timestamp,
})
export type ProjectVersionSummary = z.infer<typeof projectVersionSummarySchema>

/** An immutable published snapshot of a draft. */
export const projectVersionSchema = projectVersionSummarySchema.extend({
    graph: graphSchema,
    triggers: z.array(triggerSchema),
    dependencies: z.array(dependencySchema),
    files: z.array(projectFileSchema),
})
export type ProjectVersion = z.infer<typeof projectVersionSchema>

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

export const projectPageSchema = pageSchema(projectSummarySchema)
export type ProjectPage = z.infer<typeof projectPageSchema>
export const projectVersionPageSchema = pageSchema(projectVersionSummarySchema)
export type ProjectVersionPage = z.infer<typeof projectVersionPageSchema>

export interface CreateProjectInput {
    name: string
    description?: string
}

/** PUT semantics: name, description and draft are replaced whole. */
export interface UpdateProjectInput {
    name: string
    description: string
    draft: ProjectDraft
    expected_revision: number
}

const path = (projectId: string) => `/projects/${encodeURIComponent(projectId)}`

export function listProjects(
    client: ApiClient,
    page: PageParams = {},
    signal?: AbortSignal,
) {
    return client.request('GET', '/projects', projectPageSchema, {
        query: { ...page },
        signal,
    })
}

export function getProject(
    client: ApiClient,
    projectId: string,
    signal?: AbortSignal,
) {
    return client.request('GET', path(projectId), projectSchema, { signal })
}

export function createProject(client: ApiClient, input: CreateProjectInput) {
    return client.request('POST', '/projects', projectSchema, { body: input })
}

export function updateProject(
    client: ApiClient,
    projectId: string,
    input: UpdateProjectInput,
) {
    return client.request('PUT', path(projectId), projectSchema, {
        body: input,
    })
}

export function deleteProject(client: ApiClient, projectId: string) {
    return client.request('DELETE', path(projectId), z.undefined())
}

/** Validates the current draft; nothing is persisted. */
export function validateProject(client: ApiClient, projectId: string) {
    return client.request(
        'POST',
        `${path(projectId)}/validate`,
        validationResultSchema,
    )
}

/**
 * Publishes the draft as a new immutable version. With `expectedRevision`
 * the backend rejects (409) a draft changed since it was loaded.
 */
export function publishProject(
    client: ApiClient,
    projectId: string,
    expectedRevision?: number,
) {
    return client.request(
        'POST',
        `${path(projectId)}/publish`,
        projectVersionSchema,
        expectedRevision === undefined
            ? {}
            : { body: { expected_revision: expectedRevision } },
    )
}

export function listProjectVersions(
    client: ApiClient,
    projectId: string,
    page: PageParams = {},
    signal?: AbortSignal,
) {
    return client.request(
        'GET',
        `${path(projectId)}/versions`,
        projectVersionPageSchema,
        { query: { ...page }, signal },
    )
}

export function getProjectVersion(
    client: ApiClient,
    projectId: string,
    version: number,
    signal?: AbortSignal,
) {
    return client.request(
        'GET',
        `${path(projectId)}/versions/${String(version)}`,
        projectVersionSchema,
        { signal },
    )
}
