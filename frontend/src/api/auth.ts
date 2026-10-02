import { z } from 'zod'
import type { ApiClient } from './client'

// Contracts of backend api/auth.py. Session handling (where the token lives,
// login UI, redirects) belongs to the auth feature (roadmap Phase 21).

export const userSchema = z.object({
    id: z.string(),
    username: z.string(),
    role: z.enum(['admin', 'member']),
    disabled: z.boolean(),
    created_at: z.iso.datetime({ offset: true }),
    updated_at: z.iso.datetime({ offset: true }),
})
export type User = z.infer<typeof userSchema>

export const loginResponseSchema = z.object({
    token: z.string().min(1),
    expires_at: z.iso.datetime({ offset: true }),
    user: userSchema,
})
export type LoginResponse = z.infer<typeof loginResponseSchema>

export interface Credentials {
    username: string
    password: string
}

export function login(
    client: ApiClient,
    credentials: Credentials,
    signal?: AbortSignal,
) {
    return client.request('POST', '/auth/login', loginResponseSchema, {
        body: credentials,
        signal,
    })
}

export function logout(client: ApiClient, signal?: AbortSignal) {
    return client.request('POST', '/auth/logout', z.undefined(), { signal })
}

export function fetchCurrentUser(client: ApiClient, signal?: AbortSignal) {
    return client.request('GET', '/auth/me', userSchema, { signal })
}
