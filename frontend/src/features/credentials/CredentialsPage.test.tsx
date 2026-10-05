import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import type { QueryClient } from '@tanstack/react-query'
import { z } from 'zod'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
    apiError,
    ok,
    renderRoute,
    stubBackend,
    type Reply,
} from '../../test/app'

// A canary: if it shows up anywhere but the outgoing request body, it leaked.
const SECRET = 'sk-live-canary-9f8e7d6c5b4a'
const AT = '2026-10-02T08:00:00Z'

function credential(name: string, overrides: object = {}) {
    return {
        name,
        kind: 'api_key',
        description: '',
        reference: `credential:${name}`,
        created_at: AT,
        updated_at: AT,
        ...overrides,
    }
}

const page = (items: unknown[], has_more = false) => ({
    items,
    page: { offset: 0, limit: 50, has_more },
})

function jsonBody(init: RequestInit): Record<string, unknown> {
    const parsed: unknown =
        typeof init.body === 'string' ? JSON.parse(init.body) : {}
    return z.record(z.string(), z.unknown()).parse(parsed)
}

const consoleCalls: unknown[][] = []

beforeEach(() => {
    consoleCalls.length = 0
    for (const level of ['log', 'info', 'warn', 'error', 'debug'] as const)
        vi.spyOn(console, level).mockImplementation((...args) => {
            consoleCalls.push(args)
        })
    localStorage.clear()
    sessionStorage.clear()
})

afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
})

/** Every place the frontend could keep or leak the secret, except the
 * request that is supposed to carry it. */
function assertSecretNowhere(
    queryClient: QueryClient,
    router: {
        state: { location: { pathname: string; search: string; hash: string } }
    },
    requests: URL[],
) {
    expect(document.body.innerHTML).not.toContain(SECRET)
    for (const input of document.querySelectorAll('input'))
        expect(input.value).not.toContain(SECRET)
    expect(JSON.stringify(Object.entries(localStorage))).not.toContain(SECRET)
    expect(JSON.stringify(Object.entries(sessionStorage))).not.toContain(SECRET)
    const { pathname, search, hash } = router.state.location
    expect(pathname + search + hash).not.toContain(SECRET)
    for (const url of requests) expect(url.href).not.toContain(SECRET)
    expect(JSON.stringify(consoleCalls)).not.toContain(SECRET)
    const cached = [
        ...queryClient
            .getMutationCache()
            .getAll()
            .map((m) => m.state),
        ...queryClient
            .getQueryCache()
            .getAll()
            .map((q) => q.state.data),
    ]
    expect(JSON.stringify(cached)).not.toContain(SECRET)
}

async function openDialog(name: RegExp | string) {
    const [first] = await screen.findAllByRole('button', { name })
    if (!first) throw new Error('expected a button')
    fireEvent.click(first)
    return screen.findByRole('dialog')
}

describe('CredentialsPage', () => {
    it('lists metadata and references, never a value', async () => {
        stubBackend({
            'GET /credentials': () =>
                ok(
                    page([
                        credential('crm-api', { description: 'CRM token' }),
                        // A backend bug that returns a value must not reach
                        // the screen: unknown fields are stripped on parse.
                        credential('smtp-main', {
                            kind: 'smtp',
                            value: SECRET,
                        }),
                    ]),
                ),
        })
        const { queryClient } = renderRoute('/credentials')

        const table = await screen.findByRole('table', { name: 'Credentials' })
        expect(within(table).getByText('crm-api')).toBeVisible()
        expect(within(table).getByText('CRM token')).toBeVisible()
        expect(within(table).getByText('credential:smtp-main')).toBeVisible()
        expect(within(table).getByText('SMTP')).toBeVisible()
        expect(within(table).getAllByLabelText('Hidden')).toHaveLength(2)
        expect(document.body.innerHTML).not.toContain(SECRET)
        expect(
            JSON.stringify(
                queryClient
                    .getQueryCache()
                    .getAll()
                    .map((q) => q.state.data),
            ),
        ).not.toContain(SECRET)
    })

    it('creates through a masked, write-only field and forgets the value', async () => {
        let posted: Record<string, unknown> = {}
        let items: unknown[] = []
        const requests = stubBackend({
            'GET /credentials': () => ok(page(items)),
            'POST /credentials': (_url, init) => {
                posted = jsonBody(init)
                items = [credential('crm-api')]
                return { status: 201, body: credential('crm-api') }
            },
        })
        const { queryClient, router } = renderRoute('/credentials')
        await screen.findByRole('heading', { name: 'No credentials yet' })

        const dialog = await openDialog('New credential')
        const value = within(dialog).getByLabelText(/^Secret value/)
        expect(value).toHaveAttribute('type', 'password')
        expect(value).toHaveAttribute('autocomplete', 'new-password')
        expect(value).toHaveAttribute('spellcheck', 'false')
        fireEvent.change(within(dialog).getByLabelText(/^Name/), {
            target: { value: 'crm-api' },
        })
        fireEvent.change(within(dialog).getByLabelText('Type'), {
            target: { value: 'api_key' },
        })
        fireEvent.change(value, { target: { value: SECRET } })
        fireEvent.click(
            within(dialog).getByRole('button', { name: 'Create credential' }),
        )

        expect(
            await screen.findByText('Credential “crm-api” created'),
        ).toBeVisible()
        expect(posted).toEqual({
            name: 'crm-api',
            kind: 'api_key',
            description: '',
            value: SECRET,
        })
        await waitFor(() => {
            expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
        })
        expect(await screen.findByText('credential:crm-api')).toBeVisible()
        assertSecretNowhere(queryClient, router, requests)
    })

    it('shows validation and conflict errors at the field without echoing the value', async () => {
        let attempt = 0
        const requests = stubBackend({
            'GET /credentials': () => ok(page([])),
            'POST /credentials': (): Reply =>
                ++attempt === 1
                    ? {
                          status: 422,
                          body: {
                              code: 'VALIDATION_ERROR',
                              message: 'Request validation failed.',
                              details: {
                                  'body.value':
                                      'String should have at most 65536 characters',
                              },
                              request_id: 'r',
                          },
                      }
                    : apiError(
                          409,
                          'CREDENTIAL_ALREADY_EXISTS',
                          'A credential with this name already exists.',
                      ),
        })
        const { queryClient, router } = renderRoute('/credentials')
        await screen.findByRole('heading', { name: 'No credentials yet' })
        const dialog = await openDialog('New credential')
        fireEvent.change(within(dialog).getByLabelText(/^Name/), {
            target: { value: 'crm-api' },
        })
        const value = within(dialog).getByLabelText(/^Secret value/)
        fireEvent.change(value, { target: { value: SECRET } })
        const submit = within(dialog).getByRole('button', {
            name: 'Create credential',
        })

        fireEvent.click(submit)
        expect(
            await within(dialog).findByText(/at most 65536 characters/),
        ).toBeVisible()
        expect(value).toHaveAttribute('aria-invalid', 'true')

        fireEvent.click(submit)
        expect(await within(dialog).findByText(/already exists/)).toBeVisible()
        expect(within(dialog).getByLabelText(/^Name/)).toHaveAttribute(
            'aria-invalid',
            'true',
        )

        fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))
        await waitFor(() => {
            expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
        })
        assertSecretNowhere(queryClient, router, requests)
    })

    it('updates the description without sending a value, and rotates on demand', async () => {
        const patches: Record<string, unknown>[] = []
        const requests = stubBackend({
            'GET /credentials': () =>
                ok(page([credential('crm-api', { description: 'old' })])),
            'PATCH /credentials/crm-api': (_url, init) => {
                patches.push(jsonBody(init))
                return ok(credential('crm-api', { description: 'new' }))
            },
        })
        const { queryClient, router } = renderRoute('/credentials')

        let dialog = await openDialog('Edit crm-api')
        // Name and type are the identity: shown, not editable.
        expect(within(dialog).queryByLabelText(/^Name/)).not.toBeInTheDocument()
        expect(within(dialog).getByText('credential:crm-api')).toBeVisible()
        const rotate = within(dialog).getByLabelText(/^New secret value/)
        expect(rotate).toHaveValue('')
        expect(rotate).toHaveAttribute('type', 'password')
        fireEvent.change(within(dialog).getByLabelText('Description'), {
            target: { value: 'new' },
        })
        fireEvent.click(
            within(dialog).getByRole('button', { name: 'Save changes' }),
        )
        expect(
            await screen.findByText('Credential “crm-api” updated'),
        ).toBeVisible()

        dialog = await openDialog('Edit crm-api')
        fireEvent.change(within(dialog).getByLabelText(/^New secret value/), {
            target: { value: SECRET },
        })
        fireEvent.click(
            within(dialog).getByRole('button', { name: 'Save changes' }),
        )
        expect(
            await screen.findByText('Credential “crm-api” rotated'),
        ).toBeVisible()

        expect(patches).toEqual([
            { description: 'new' },
            { description: 'old', value: SECRET },
        ])
        await waitFor(() => {
            expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
        })
        assertSecretNowhere(queryClient, router, requests)
    })

    it('deletes only after confirmation', async () => {
        let deleted = 0
        stubBackend({
            'GET /credentials': () =>
                ok(page(deleted ? [] : [credential('crm-api')])),
            'DELETE /credentials/crm-api': (): Reply => {
                deleted++
                return { status: 204 }
            },
        })
        renderRoute('/credentials')

        let dialog = await openDialog('Delete crm-api')
        expect(dialog).toHaveTextContent('credential:crm-api')
        expect(dialog).toHaveTextContent('This cannot be undone.')
        fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))
        await waitFor(() => {
            expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
        })
        expect(deleted).toBe(0)

        dialog = await openDialog('Delete crm-api')
        fireEvent.click(
            within(dialog).getByRole('button', { name: 'Delete credential' }),
        )
        expect(
            await screen.findByRole('heading', { name: 'No credentials yet' }),
        ).toBeVisible()
        expect(deleted).toBe(1)
    })

    it('shows a failed delete in the dialog', async () => {
        stubBackend({
            'GET /credentials': () => ok(page([credential('crm-api')])),
            'DELETE /credentials/crm-api': () =>
                apiError(404, 'CREDENTIAL_NOT_FOUND', 'Credential not found.'),
        })
        renderRoute('/credentials')
        const dialog = await openDialog('Delete crm-api')
        fireEvent.click(
            within(dialog).getByRole('button', { name: 'Delete credential' }),
        )
        expect(
            await within(dialog).findByText('Credential not found.'),
        ).toBeVisible()
    })

    it('tells members that only admins manage credentials', async () => {
        stubBackend({
            'GET /credentials': () =>
                apiError(
                    403,
                    'PERMISSION_DENIED',
                    'This operation requires an admin.',
                ),
        })
        renderRoute('/credentials')
        expect(
            await screen.findByRole('heading', {
                name: 'Admin access required',
            }),
        ).toBeVisible()
        expect(
            screen.getByRole('button', { name: 'New credential' }),
        ).toBeDisabled()
    })

    it('sends an expired session to the login page', async () => {
        stubBackend({
            'GET /credentials': () =>
                apiError(
                    401,
                    'AUTHENTICATION_REQUIRED',
                    'Your session has expired.',
                ),
        })
        const { router } = renderRoute('/credentials')
        expect(
            await screen.findByRole('heading', { name: 'Sign in to Akasha' }),
        ).toBeInTheDocument()
        expect(router.state.location.pathname).toBe('/login')
        expect(screen.getByRole('status')).toHaveTextContent(
            'Your session has ended',
        )
    })
})
