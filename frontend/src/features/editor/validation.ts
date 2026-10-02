import { z } from 'zod'
import { ApiError } from '../../api/errors'
import { validationIssueSchema, type ValidationIssue } from '../../api/projects'

// Where a backend validation issue points in the editor. The issues
// themselves are the backend's verdict; nothing here re-validates.

export type IssueScope = 'node' | 'connection' | 'trigger' | 'project'

export const SCOPE_LABELS: Record<IssueScope, string> = {
    node: 'Nodes',
    connection: 'Connections',
    trigger: 'Triggers',
    project: 'Project',
}

export function issueScope(issue: ValidationIssue): IssueScope {
    if (issue.node_id !== null) return 'node'
    if (issue.edge !== null) return 'connection'
    if (issue.trigger_id !== null) return 'trigger'
    return 'project'
}

/** Issues per node id, for markers on the canvas and the node list. */
export function issueCounts(issues: readonly ValidationIssue[]) {
    const counts = new Map<string, number>()
    for (const { node_id } of issues)
        if (node_id !== null)
            counts.set(node_id, (counts.get(node_id) ?? 0) + 1)
    return counts
}

const publishFailureSchema = z.object({
    issues: z.array(validationIssueSchema),
})

/**
 * The issues a refused publish (422 PROJECT_VALIDATION_FAILED) reports;
 * null for any other failure, or details not matching the contract.
 */
export function publishIssues(error: unknown): ValidationIssue[] | null {
    if (
        !(error instanceof ApiError) ||
        error.code !== 'PROJECT_VALIDATION_FAILED'
    )
        return null
    const parsed = publishFailureSchema.safeParse(error.details)
    return parsed.success ? parsed.data.issues : null
}

export const isConflict = (error: unknown) =>
    error instanceof ApiError && error.kind === 'conflict'
