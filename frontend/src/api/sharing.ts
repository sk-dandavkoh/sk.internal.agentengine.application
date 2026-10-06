import { z } from 'zod'
import type { ApiClient } from './client'
import { timestamp } from './schemas'

// Contract of backend api/sharing.py. Every rule (who may share, who may
// edit) is enforced there; `access` only lets the UI hide what the backend
// would refuse.

export const projectRoleSchema = z.enum(['viewer', 'editor'])
export type ProjectRole = z.infer<typeof projectRoleSchema>

/** The caller's access: manage (owner, admin) ⊃ edit (editor) ⊃ view. */
export const projectAccessSchema = z.enum(['manage', 'edit', 'view'])
export type ProjectAccess = z.infer<typeof projectAccessSchema>

const personSchema = z.object({
    user_id: z.string(),
    username: z.string(),
    display_name: z.string().nullable(),
})
export type Person = z.infer<typeof personSchema>

export const projectMemberSchema = personSchema.extend({
    role: projectRoleSchema,
    created_at: timestamp,
})
export type ProjectMember = z.infer<typeof projectMemberSchema>

export const projectSharingSchema = z.object({
    access: projectAccessSchema,
    /** Null: an ownerless project (admin-only unless shared). */
    owner: personSchema.nullable(),
    members: z.array(projectMemberSchema),
})
export type ProjectSharing = z.infer<typeof projectSharingSchema>

const path = (projectId: string, username?: string) =>
    `/projects/${encodeURIComponent(projectId)}/members` +
    (username === undefined ? '' : `/${encodeURIComponent(username)}`)

export function getProjectSharing(
    client: ApiClient,
    projectId: string,
    signal?: AbortSignal,
) {
    return client.request('GET', path(projectId), projectSharingSchema, {
        signal,
    })
}

/** Shares the project with `username`, or changes their role. */
export function putProjectMember(
    client: ApiClient,
    projectId: string,
    username: string,
    role: ProjectRole,
) {
    return client.request(
        'PUT',
        path(projectId, username),
        projectMemberSchema,
        { body: { role } },
    )
}

export function removeProjectMember(
    client: ApiClient,
    projectId: string,
    username: string,
) {
    return client.request('DELETE', path(projectId, username), z.undefined())
}

/** Makes `username` the owner; the caller (if the owner) stays an editor.
 * Answers the sharing as the caller sees it afterwards. */
export function transferProjectOwnership(
    client: ApiClient,
    projectId: string,
    username: string,
) {
    return client.request(
        'PUT',
        `/projects/${encodeURIComponent(projectId)}/owner`,
        projectSharingSchema,
        { body: { username } },
    )
}

/** The caller stops being a member (owners transfer instead). */
export function leaveProject(client: ApiClient, projectId: string) {
    return client.request(
        'DELETE',
        `/projects/${encodeURIComponent(projectId)}/membership`,
        z.undefined(),
    )
}
