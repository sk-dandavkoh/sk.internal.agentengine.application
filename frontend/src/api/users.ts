import { z } from 'zod'
import { userSchema, type UserRole } from './auth'
import type { ApiClient } from './client'
import { pageSchema, type PageParams } from './schemas'

// Contract of backend api/auth.py /users (admin only). Responses carry
// account metadata only: never a password, hash, session or token. Every
// rule (admin-only, no self-lockout, last admin) is enforced there; the UI
// only mirrors them to avoid pointless requests.

export const userPageSchema = pageSchema(userSchema)
export type UserPage = z.infer<typeof userPageSchema>

export const userStatusSchema = z.enum(['active', 'disabled'])
export type UserStatus = z.infer<typeof userStatusSchema>

export interface UserListParams extends PageParams {
    /** Username or display name contains (any case). */
    q?: string
    role?: UserRole
    status?: UserStatus
}

export interface CreateUserInput {
    username: string
    password: string
    role: UserRole
    display_name?: string
}

/** Omitted fields stay unchanged. `display_name: ''` clears it; `password`
 * resets it and signs the user out everywhere. */
export interface UpdateUserInput {
    display_name?: string
    role?: UserRole
    disabled?: boolean
    password?: string
}

const path = (id: string) => `/users/${encodeURIComponent(id)}`

export function listUsers(
    client: ApiClient,
    { q, ...params }: UserListParams = {},
    signal?: AbortSignal,
) {
    const search = q?.trim()
    return client.request('GET', '/users', userPageSchema, {
        query: { ...params, q: search === '' ? undefined : search },
        signal,
    })
}

export function getUser(client: ApiClient, id: string, signal?: AbortSignal) {
    return client.request('GET', path(id), userSchema, { signal })
}

export function createUser(client: ApiClient, input: CreateUserInput) {
    return client.request('POST', '/users', userSchema, { body: input })
}

export function updateUser(
    client: ApiClient,
    id: string,
    input: UpdateUserInput,
) {
    return client.request('PATCH', path(id), userSchema, { body: input })
}
