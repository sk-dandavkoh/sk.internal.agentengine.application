import { z } from 'zod'
import type { ApiClient } from './client'
import { timestamp } from './schemas'

// Contract of backend api/sharing.py. Every rule (who may share, who may
// edit) is enforced there; `access` only lets the UI hide what the backend
// would refuse.

export const workflowRoleSchema = z.enum(['viewer', 'editor'])
export type WorkflowRole = z.infer<typeof workflowRoleSchema>

/** The caller's access: manage (owner, admin) ⊃ edit (editor) ⊃ view. */
export const workflowAccessSchema = z.enum(['manage', 'edit', 'view'])
export type WorkflowAccess = z.infer<typeof workflowAccessSchema>

const personSchema = z.object({
    user_id: z.string(),
    username: z.string(),
    display_name: z.string().nullable(),
})
export type Person = z.infer<typeof personSchema>

export const workflowMemberSchema = personSchema.extend({
    role: workflowRoleSchema,
    created_at: timestamp,
})
export type WorkflowMember = z.infer<typeof workflowMemberSchema>

export const workflowSharingSchema = z.object({
    access: workflowAccessSchema,
    /** Null: an ownerless workflow (admin-only unless shared). */
    owner: personSchema.nullable(),
    members: z.array(workflowMemberSchema),
})
export type WorkflowSharing = z.infer<typeof workflowSharingSchema>

const path = (workflowId: string, username?: string) =>
    `/workflows/${encodeURIComponent(workflowId)}/members` +
    (username === undefined ? '' : `/${encodeURIComponent(username)}`)

export function getWorkflowSharing(
    client: ApiClient,
    workflowId: string,
    signal?: AbortSignal,
) {
    return client.request('GET', path(workflowId), workflowSharingSchema, {
        signal,
    })
}

/** Shares the workflow with `username`, or changes their role. */
export function putWorkflowMember(
    client: ApiClient,
    workflowId: string,
    username: string,
    role: WorkflowRole,
) {
    return client.request(
        'PUT',
        path(workflowId, username),
        workflowMemberSchema,
        { body: { role } },
    )
}

export function removeWorkflowMember(
    client: ApiClient,
    workflowId: string,
    username: string,
) {
    return client.request('DELETE', path(workflowId, username), z.undefined())
}

/** Makes `username` the owner; the caller (if the owner) stays an editor.
 * Answers the sharing as the caller sees it afterwards. */
export function transferWorkflowOwnership(
    client: ApiClient,
    workflowId: string,
    username: string,
) {
    return client.request(
        'PUT',
        `/workflows/${encodeURIComponent(workflowId)}/owner`,
        workflowSharingSchema,
        { body: { username } },
    )
}

/** The caller stops being a member (owners transfer instead). */
export function leaveWorkflow(client: ApiClient, workflowId: string) {
    return client.request(
        'DELETE',
        `/workflows/${encodeURIComponent(workflowId)}/membership`,
        z.undefined(),
    )
}
