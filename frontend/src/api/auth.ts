import { z } from 'zod'
import type { ApiClient } from './client'

// Contracts of backend api/auth.py. The browser session is an HttpOnly
// cookie (ADR 0002): no function here ever hands a token to the app.

export const userSchema = z.object({
    id: z.string(),
    username: z.string(),
    role: z.enum(['admin', 'member']),
    disabled: z.boolean(),
    created_at: z.iso.datetime({ offset: true }),
    updated_at: z.iso.datetime({ offset: true }),
})
export type User = z.infer<typeof userSchema>

export const sessionSchema = z.object({
    // Null only for a personal access token without expiry; a browser
    // session always expires.
    expires_at: z.iso.datetime({ offset: true }).nullable(),
    user: userSchema,
})
export type Session = z.infer<typeof sessionSchema>

// `token` is null for a cookie login; listed only so the contract stays exact.
export const loginResponseSchema = sessionSchema.extend({
    token: z.string().nullable(),
})

export interface Credentials {
    username: string
    password: string
}

export async function login(
    client: ApiClient,
    credentials: Credentials,
    signal?: AbortSignal,
): Promise<Session> {
    const { expires_at, user } = await client.request(
        'POST',
        '/auth/login',
        loginResponseSchema,
        { body: { ...credentials, cookie: true }, signal },
    )
    return { expires_at, user }
}

export const setupStatusSchema = z.object({ required: z.boolean() })

/** Whether this installation still needs its first admin. */
export function fetchSetupStatus(client: ApiClient, signal?: AbortSignal) {
    return client.request('GET', '/auth/setup', setupStatusSchema, { signal })
}

/** Creates the first admin; the backend refuses (409) once any account
 * exists. Signs nobody in: the caller logs in afterwards. */
export function setup(
    client: ApiClient,
    credentials: Credentials,
    signal?: AbortSignal,
) {
    return client.request('POST', '/auth/setup', userSchema, {
        body: credentials,
        signal,
    })
}

export function logout(client: ApiClient, signal?: AbortSignal) {
    return client.request('POST', '/auth/logout', z.undefined(), { signal })
}

export function fetchSession(client: ApiClient, signal?: AbortSignal) {
    return client.request('GET', '/auth/session', sessionSchema, { signal })
}

export const accessTokenSchema = z.object({
    id: z.string(),
    name: z.string(),
    hint: z.string(),
    created_at: z.iso.datetime({ offset: true }),
    expires_at: z.iso.datetime({ offset: true }).nullable(),
    last_used_at: z.iso.datetime({ offset: true }).nullable(),
})
export type AccessToken = z.infer<typeof accessTokenSchema>

const accessTokenListSchema = z.object({ items: z.array(accessTokenSchema) })

/** The value is in this response only: it is never shown again. */
export const createdAccessTokenSchema = accessTokenSchema.extend({
    token: z.string(),
})
export type CreatedAccessToken = z.infer<typeof createdAccessTokenSchema>

export interface CreateAccessTokenInput {
    name: string
    /** The account password (the backend requires it). */
    password: string
    /** Null: valid until revoked. */
    expires_in_days: number | null
}

/** The signed-in user's personal access tokens (metadata only). */
export async function listAccessTokens(
    client: ApiClient,
    signal?: AbortSignal,
): Promise<AccessToken[]> {
    const { items } = await client.request(
        'GET',
        '/auth/tokens',
        accessTokenListSchema,
        { signal },
    )
    return items
}

export function createAccessToken(
    client: ApiClient,
    body: CreateAccessTokenInput,
    signal?: AbortSignal,
) {
    return client.request('POST', '/auth/tokens', createdAccessTokenSchema, {
        body,
        signal,
    })
}

export function revokeAccessToken(
    client: ApiClient,
    id: string,
    signal?: AbortSignal,
) {
    return client.request(
        'DELETE',
        `/auth/tokens/${encodeURIComponent(id)}`,
        z.undefined(),
        { signal },
    )
}

export function changePassword(
    client: ApiClient,
    body: { current_password: string; new_password: string },
    signal?: AbortSignal,
) {
    return client.request('POST', '/auth/password', z.undefined(), {
        body,
        signal,
    })
}
