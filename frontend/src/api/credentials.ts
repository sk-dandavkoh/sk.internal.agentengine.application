import { z } from 'zod'
import type { ApiClient } from './client'
import { pageSchema, timestamp, type PageParams } from './schemas'

// Contract of backend api/credentials.py (admin only). Responses carry
// metadata and the reference workflows use; the secret value never comes
// back (AGENTS §35, §36).

export const credentialKindSchema = z.enum([
    'api_key',
    'smtp',
    'provider',
    'generic',
])
export type CredentialKind = z.infer<typeof credentialKindSchema>

export const credentialReferenceSchema = z.object({
    name: z.string(),
    kind: credentialKindSchema,
    description: z.string(),
    /** What a node config holds instead of the secret. */
    reference: z.string(),
    created_at: timestamp,
    updated_at: timestamp,
})
export type CredentialReference = z.infer<typeof credentialReferenceSchema>

export const credentialPageSchema = pageSchema(credentialReferenceSchema)
export type CredentialPage = z.infer<typeof credentialPageSchema>

export interface CreateCredentialInput {
    name: string
    kind?: CredentialKind
    description?: string
    value: string
}

/** Omitted fields stay unchanged; `value` rotates the secret. */
export interface UpdateCredentialInput {
    description?: string
    value?: string
}

const path = (name: string) => `/credentials/${encodeURIComponent(name)}`

export function listCredentials(
    client: ApiClient,
    page: PageParams = {},
    signal?: AbortSignal,
) {
    return client.request('GET', '/credentials', credentialPageSchema, {
        query: { ...page },
        signal,
    })
}

/** Names and kinds for pickers; every signed-in user may list them. */
export const credentialNamePageSchema = pageSchema(
    z.object({ name: z.string(), kind: credentialKindSchema }),
)
export type CredentialNamePage = z.infer<typeof credentialNamePageSchema>

export function listCredentialNames(
    client: ApiClient,
    page: PageParams = {},
    signal?: AbortSignal,
) {
    return client.request(
        'GET',
        '/credential-names',
        credentialNamePageSchema,
        {
            query: { ...page },
            signal,
        },
    )
}

export function getCredential(
    client: ApiClient,
    name: string,
    signal?: AbortSignal,
) {
    return client.request('GET', path(name), credentialReferenceSchema, {
        signal,
    })
}

export function createCredential(
    client: ApiClient,
    input: CreateCredentialInput,
) {
    return client.request('POST', '/credentials', credentialReferenceSchema, {
        body: input,
    })
}

export function updateCredential(
    client: ApiClient,
    name: string,
    input: UpdateCredentialInput,
) {
    return client.request('PATCH', path(name), credentialReferenceSchema, {
        body: input,
    })
}

export function deleteCredential(client: ApiClient, name: string) {
    return client.request('DELETE', path(name), z.undefined())
}
