import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { z } from 'zod'
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
    apiError,
    ok,
    pending,
    renderRoute,
    stubBackend,
    testSession,
    testUser,
} from '../../test/app'

// A canary: if it shows up anywhere but the outgoing request body, it leaked.
const SECRET = 'reset-canary-password-42'
const AT = '2026-10-02T08:00:00Z'

function user(id: string, overrides: object = {}): Record<string, unknown> {
    return {
        id,
        username: id,
        display_name: null,
        role: 'member',
        disabled: false,
        created_at: AT,
        updated_at: AT,
        last_login_at: null,
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

const member = { ...testSession, user: { ...testUser, role: 'member' } }
const self = user(testUser.id, { username: testUser.username, role: 'admin' })

afterEach(() => {
    vi.unstubAllGlobals()
})

async function openDialog(name: string) {
    fireEvent.click(await screen.findByRole('button', { name }))
    return screen.findByRole('dialog')
}

function fill(dialog: HTMLElement, label: RegExp, value: string) {
    fireEvent.change(within(dialog).getByLabelText(label), {
        target: { value },
    })
}

describe('UsersPage', () => {
    it('lists users with role, status and last login', async () => {
        stubBackend({
            'GET /users': () =>
                ok(
                    page([
                        self,
                        user('alice', {
                            display_name: 'Alice A',
                            last_login_at: AT,
                            // A backend bug must not reach the screen:
                            // unknown fields are stripped on parse.
                            password_hash: 'scrypt$leak',
                        }),
                        user('bob', { disabled: true }),
                    ]),
                ),
        })
        renderRoute('/users')
        const table = await screen.findByRole('table', { name: 'Users' })
        const alice = within(table).getByText('Alice A').closest('tr')
        if (!alice) throw new Error('expected a row')
        expect(within(alice).getByText('alice')).toBeVisible()
        expect(within(alice).getByText('Member')).toBeVisible()
        expect(within(alice).getByText('Active')).toBeVisible()
        expect(within(table).getByText('Disabled')).toBeVisible()
        expect(within(table).getAllByText('Never').length).toBeGreaterThan(0)
        expect(document.body.innerHTML).not.toContain('scrypt$')
        // One's own row offers no lockout actions (the backend refuses them).
        expect(within(table).getByText('(you)')).toBeVisible()
        expect(
            screen.queryByRole('button', {
                name: `Disable ${testUser.username}`,
            }),
        ).not.toBeInTheDocument()
        expect(
            screen.getByRole('button', { name: 'Disable Alice A' }),
        ).toBeVisible()
        expect(screen.getByRole('button', { name: 'Enable bob' })).toBeVisible()
    })

    it('shows loading, then an error with retry', async () => {
        let fail = true
        stubBackend({
            'GET /users': () =>
                fail
                    ? apiError(500, 'INTERNAL_ERROR', 'Boom.')
                    : ok(page([self])),
        })
        renderRoute('/users')
        expect(await screen.findByText(/Boom/)).toBeVisible()
        fail = false
        fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
        expect(
            await screen.findByRole('table', { name: 'Users' }),
        ).toBeVisible()
    })

    it('keeps the loading state while the list is pending', async () => {
        stubBackend({ 'GET /users': pending })
        renderRoute('/users')
        expect(await screen.findByText('Loading users…')).toBeVisible()
        expect(screen.queryByRole('table')).not.toBeInTheDocument()
    })

    it('sends search and filters to the backend', async () => {
        const requests = stubBackend({ 'GET /users': () => ok(page([])) })
        renderRoute('/users?role=admin&status=disabled')
        expect(
            await screen.findByRole('heading', { name: 'No matching users' }),
        ).toBeVisible()
        fireEvent.change(screen.getByLabelText('Search users'), {
            target: { value: ' ali ' },
        })
        fireEvent.click(screen.getByRole('button', { name: 'Search' }))
        await waitFor(() => {
            const last = requests.at(-1)
            expect(last?.searchParams.get('q')).toBe('ali')
            expect(last?.searchParams.get('role')).toBe('admin')
            expect(last?.searchParams.get('status')).toBe('disabled')
        })
    })

    it('creates a user and refreshes the list, keeping the password nowhere', async () => {
        let posted: Record<string, unknown> = {}
        let items: unknown[] = [self]
        const requests = stubBackend({
            'GET /users': () => ok(page(items)),
            'POST /users': (_url, init) => {
                posted = jsonBody(init)
                items = [self, user('carol', { display_name: 'Carol' })]
                return { status: 201, body: items[1] }
            },
        })
        const { queryClient } = renderRoute('/users')
        const dialog = await openDialog('New user')
        fill(dialog, /^Username/, 'carol')
        fill(dialog, /^Display name/, ' Carol ')
        fireEvent.change(within(dialog).getByLabelText('Role'), {
            target: { value: 'member' },
        })
        const password = within(dialog).getByLabelText(/^Initial password/)
        expect(password).toHaveAttribute('type', 'password')
        expect(password).toHaveAttribute('autocomplete', 'new-password')
        fill(dialog, /^Initial password/, SECRET)
        fill(dialog, /^Confirm initial password/, 'something else entirely')
        fireEvent.click(
            within(dialog).getByRole('button', { name: 'Create user' }),
        )
        expect(
            await within(dialog).findByText('The passwords do not match.'),
        ).toBeVisible()
        expect(posted).toEqual({})

        fill(dialog, /^Confirm initial password/, SECRET)
        fireEvent.click(
            within(dialog).getByRole('button', { name: 'Create user' }),
        )
        expect(await screen.findByText('User “carol” created')).toBeVisible()
        expect(posted).toEqual({
            username: 'carol',
            password: SECRET,
            role: 'member',
            display_name: 'Carol',
        })
        expect(await screen.findByRole('cell', { name: 'Carol' })).toBeVisible()
        expect(document.body.innerHTML).not.toContain(SECRET)
        for (const url of requests) expect(url.href).not.toContain(SECRET)
        const cached = queryClient
            .getMutationCache()
            .getAll()
            .map((m) => m.state.variables)
        expect(JSON.stringify(cached)).not.toContain(SECRET)
    })

    it('shows a taken username at the field', async () => {
        stubBackend({
            'GET /users': () => ok(page([self])),
            'POST /users': () =>
                apiError(409, 'USERNAME_TAKEN', 'The username is taken.'),
        })
        renderRoute('/users')
        const dialog = await openDialog('New user')
        fill(dialog, /^Username/, 'ada')
        fill(dialog, /^Initial password/, SECRET)
        fill(dialog, /^Confirm initial password/, SECRET)
        fireEvent.click(
            within(dialog).getByRole('button', { name: 'Create user' }),
        )
        expect(
            await within(dialog).findByText('The username is taken.'),
        ).toBeVisible()
        expect(within(dialog).getByLabelText(/^Username/)).toHaveAttribute(
            'aria-invalid',
            'true',
        )
    })

    it('edits the display name and calls out a role change before saving', async () => {
        let patched: Record<string, unknown> = {}
        let alice = user('alice')
        stubBackend({
            'GET /users': () => ok(page([self, alice])),
            'PATCH /users/alice': (_url, init) => {
                patched = jsonBody(init)
                alice = { ...alice, display_name: 'Alice', role: 'admin' }
                return ok(alice)
            },
        })
        renderRoute('/users')
        const dialog = await openDialog('Edit alice')
        expect(within(dialog).queryByLabelText(/^Username/)).toBeNull()
        fill(dialog, /^Display name/, 'Alice')
        expect(
            within(dialog).getByRole('button', { name: 'Save changes' }),
        ).toBeVisible()
        fireEvent.change(within(dialog).getByLabelText('Role'), {
            target: { value: 'admin' },
        })
        expect(
            within(dialog).getByText('Change administrator role?'),
        ).toBeVisible()
        fireEvent.click(
            within(dialog).getByRole('button', {
                name: 'Change role and save',
            }),
        )
        expect(await screen.findByText('User “alice” updated')).toBeVisible()
        expect(patched).toEqual({ display_name: 'Alice', role: 'admin' })
        expect(await screen.findByRole('cell', { name: 'Alice' })).toBeVisible()
    })

    it('does not offer a role change for one’s own account', async () => {
        stubBackend({ 'GET /users': () => ok(page([self])) })
        renderRoute('/users')
        const dialog = await openDialog(`Edit ${testUser.username}`)
        expect(within(dialog).getByLabelText('Role')).toBeDisabled()
    })

    it('disables and enables after confirmation', async () => {
        const bodies: Record<string, unknown>[] = []
        let alice = user('alice')
        stubBackend({
            'GET /users': () => ok(page([self, alice])),
            'PATCH /users/alice': (_url, init) => {
                const body = jsonBody(init)
                bodies.push(body)
                alice = { ...alice, disabled: body['disabled'] === true }
                return ok(alice)
            },
        })
        renderRoute('/users')
        let dialog = await openDialog('Disable alice')
        expect(
            within(dialog).getByText(/no longer be able to sign in/),
        ).toBeVisible()
        fireEvent.click(
            within(dialog).getByRole('button', { name: 'Disable user' }),
        )
        expect(await screen.findByText('User “alice” disabled')).toBeVisible()
        dialog = await openDialog('Enable alice')
        fireEvent.click(
            within(dialog).getByRole('button', { name: 'Enable user' }),
        )
        expect(await screen.findByText('User “alice” enabled')).toBeVisible()
        expect(bodies).toEqual([{ disabled: true }, { disabled: false }])
    })

    it('shows the backend refusing to disable the last admin', async () => {
        stubBackend({
            'GET /users': () =>
                ok(page([self, user('root', { role: 'admin' })])),
            'PATCH /users/root': () =>
                apiError(
                    409,
                    'LAST_ADMIN',
                    'The last active admin cannot be disabled or demoted.',
                ),
        })
        renderRoute('/users')
        const dialog = await openDialog('Disable root')
        fireEvent.click(
            within(dialog).getByRole('button', { name: 'Disable user' }),
        )
        expect(
            await within(dialog).findByText(/last active admin/),
        ).toBeVisible()
    })

    it('resets a password and never shows it', async () => {
        let patched: Record<string, unknown> = {}
        stubBackend({
            'GET /users': () => ok(page([self, user('alice')])),
            'PATCH /users/alice': (_url, init) => {
                patched = jsonBody(init)
                return ok(user('alice'))
            },
        })
        renderRoute('/users')
        const dialog = await openDialog('Reset password of alice')
        expect(
            within(dialog).getByText(/existing password is invalidated/),
        ).toBeVisible()
        fill(dialog, /^New password/, SECRET)
        fill(dialog, /^Confirm new password/, SECRET)
        fireEvent.click(
            within(dialog).getByRole('button', { name: 'Reset password' }),
        )
        expect(
            await screen.findByText('Password of “alice” reset'),
        ).toBeVisible()
        expect(patched).toEqual({ password: SECRET })
        await waitFor(() => {
            expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
        })
        expect(document.body.innerHTML).not.toContain(SECRET)
    })

    it('refuses members without asking the backend', async () => {
        const requests = stubBackend({ 'GET /auth/session': () => ok(member) })
        renderRoute('/users')
        expect(
            await screen.findByRole('heading', {
                name: 'Admin access required',
            }),
        ).toBeVisible()
        expect(requests.filter((u) => u.pathname.endsWith('/users'))).toEqual(
            [],
        )
        expect(
            screen.queryByRole('link', { name: 'Users' }),
        ).not.toBeInTheDocument()
    })

    it('shows the backend’s 403 when the role changed meanwhile', async () => {
        stubBackend({
            'GET /users': () =>
                apiError(
                    403,
                    'PERMISSION_DENIED',
                    'This operation requires an admin.',
                ),
        })
        renderRoute('/users')
        expect(
            await screen.findByRole('heading', {
                name: 'Admin access required',
            }),
        ).toBeVisible()
        expect(screen.getByRole('button', { name: 'New user' })).toBeDisabled()
    })

    it('lists Users in the navigation for admins only', async () => {
        stubBackend({ 'GET /users': () => ok(page([self])) })
        renderRoute('/users')
        expect(await screen.findByRole('link', { name: 'Users' })).toBeVisible()
    })
})
