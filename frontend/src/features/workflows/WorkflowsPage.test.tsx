import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { z } from 'zod'
import {
    apiError,
    ok,
    renderRoute,
    stubBackend,
    testSession,
    testUser,
    type Reply,
} from '../../test/app'

// -- factories (valid by default) -------------------------------------------------

const AT = '2026-10-02T08:00:00Z'

function summary(id: string, name: string, overrides: object = {}) {
    return {
        id,
        name,
        description: '',
        status: 'draft',
        owner_id: 'user_1',
        revision: 1,
        current_version: 0,
        created_at: AT,
        updated_at: AT,
        ...overrides,
    }
}

function detail(id: string, name: string, overrides: object = {}) {
    return {
        ...summary(id, name),
        draft: {
            graph: {
                // Wire defaults included: the form sends the draft back as read.
                nodes: [
                    {
                        id: 'n1',
                        node_type: 'core.input',
                        config: {},
                        label: '',
                        // Editor layout must survive a details-only save.
                        position: { x: 10, y: 20 },
                    },
                ],
                edges: [],
            },
            triggers: [],
            dependencies: [],
        },
        files: [],
        ...overrides,
    }
}

const page = (items: unknown[], has_more = false) => ({
    items,
    page: { offset: 0, limit: 25, has_more },
})

const created = (body: unknown): Reply => ({ status: 201, body })

/** The members endpoint for the caller's access to workflow `id`. */
function sharing(id: string, access = 'manage', members: unknown[] = []) {
    return {
        [`GET /workflows/${id}/members`]: () =>
            ok({
                access,
                owner: {
                    user_id: 'u_admin',
                    username: 'ada',
                    display_name: null,
                },
                members,
            }),
    }
}
const noContent: Reply = { status: 204 }

function deferred() {
    let resolve: (reply: Reply) => void = () => undefined
    const promise = new Promise<Reply>((r) => (resolve = r))
    return { promise, resolve }
}

function jsonBody(init: RequestInit): unknown {
    return typeof init.body === 'string' ? JSON.parse(init.body) : undefined
}

const listRequests = (requests: URL[]) =>
    requests.filter((u) => u.pathname === '/api/v1/workflows')

function present<T>(value: T | null | undefined): T {
    if (value == null) throw new Error('expected an element')
    return value
}

async function openDialog(button: string) {
    // The page header's button (an empty state may repeat it).
    const [first] = await screen.findAllByRole('button', { name: button })
    fireEvent.click(present(first))
    return screen.findByRole('dialog')
}

// -- list --------------------------------------------------------------------------

describe('Workflows list', () => {
    it('shows workflows with status, latest version and a link to open them', async () => {
        const requests = stubBackend({
            'GET /workflows': () =>
                ok(
                    page([
                        summary('wf_a', 'Alpha', { description: 'first' }),
                        summary('wf_b', 'Beta', {
                            status: 'published',
                            current_version: 3,
                        }),
                    ]),
                ),
        })
        renderRoute('/workflows')

        const table = await screen.findByRole('table', { name: 'Workflows' })
        const rows = within(table).getAllByRole('row')
        expect(rows[1]).toHaveTextContent(/Alpha.*first.*Draft.*—/)
        expect(rows[2]).toHaveTextContent(/Beta.*Published.*v3/)
        expect(
            within(table).getByRole('link', { name: 'Beta' }),
        ).toHaveAttribute('href', '/workflows/wf_b')
        const [url] = listRequests(requests)
        expect(url?.searchParams.get('sort')).toBe('-updated_at')
        expect(url?.searchParams.get('limit')).toBe('25')
        expect(url?.searchParams.has('q')).toBe(false)
    })

    it('opens a workflow from the list', async () => {
        stubBackend({
            'GET /workflows': () => ok(page([summary('wf_a', 'Alpha')])),
            'GET /workflows/wf_a': () => ok(detail('wf_a', 'Alpha')),
        })
        const { router } = renderRoute('/workflows')

        fireEvent.click(await screen.findByRole('link', { name: 'Alpha' }))

        expect(
            await screen.findByRole('heading', { level: 1, name: 'Alpha' }),
        ).toBeInTheDocument()
        expect(router.state.location.pathname).toBe('/workflows/wf_a')
    })

    it('invites to create the first workflow when there are none', async () => {
        stubBackend({ 'GET /workflows': () => ok(page([])) })
        renderRoute('/workflows')

        const empty = await screen.findByRole('heading', {
            name: 'No workflows yet',
        })
        const state = present(empty.parentElement)
        fireEvent.click(
            within(state).getByRole('button', { name: 'New workflow' }),
        )
        expect(
            await screen.findByRole('dialog', { name: 'New workflow' }),
        ).toBeInTheDocument()
    })

    it('searches, filters and sorts through the backend, kept in the URL', async () => {
        const requests = stubBackend({
            'GET /workflows': (url) =>
                ok(
                    page(
                        url.searchParams.has('q')
                            ? []
                            : [summary('wf_a', 'Alpha')],
                    ),
                ),
        })
        const { router } = renderRoute('/workflows')
        await screen.findByRole('link', { name: 'Alpha' })

        fireEvent.change(screen.getByLabelText('Status'), {
            target: { value: 'published' },
        })
        fireEvent.change(screen.getByLabelText('Sort by'), {
            target: { value: 'name' },
        })
        fireEvent.change(screen.getByRole('searchbox'), {
            target: { value: '  zeta ' },
        })
        fireEvent.click(screen.getByRole('button', { name: 'Search' }))

        expect(
            await screen.findByRole('heading', {
                name: 'No matching workflows',
            }),
        ).toBeInTheDocument()
        const last = listRequests(requests).at(-1)
        expect(last?.searchParams.get('q')).toBe('zeta')
        expect(last?.searchParams.getAll('status')).toEqual(['published'])
        expect(last?.searchParams.get('sort')).toBe('name')
        expect(router.state.location.search).toContain('status=published')

        fireEvent.click(screen.getByRole('button', { name: 'Clear filters' }))
        expect(await screen.findByRole('link', { name: 'Alpha' })).toBeVisible()
        expect(screen.getByRole('searchbox')).toHaveValue('')
        expect(listRequests(requests).at(-1)?.searchParams.has('status')).toBe(
            false,
        )
    })

    it('pages with the backend’s has_more', async () => {
        const requests = stubBackend({
            'GET /workflows': (url) =>
                url.searchParams.get('offset') === '25'
                    ? ok(page([summary('wf_z', 'Zeta')]))
                    : ok(page([summary('wf_a', 'Alpha')], true)),
        })
        renderRoute('/workflows')
        await screen.findByRole('link', { name: 'Alpha' })
        expect(screen.getByRole('button', { name: 'Previous' })).toBeDisabled()

        fireEvent.click(screen.getByRole('button', { name: 'Next' }))

        expect(await screen.findByRole('link', { name: 'Zeta' })).toBeVisible()
        expect(listRequests(requests).at(-1)?.searchParams.get('offset')).toBe(
            '25',
        )
        expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled()
    })

    it('shows a loading state, then an error that can be retried', async () => {
        const first = deferred()
        let reply = () => first.promise
        stubBackend({ 'GET /workflows': () => reply() })
        renderRoute('/workflows')
        expect(
            await screen.findByText('Loading workflows…'),
        ).toBeInTheDocument()

        first.resolve(
            apiError(500, 'INTERNAL_ERROR', 'An internal error occurred.'),
        )
        const failed = await screen.findByRole('alert')
        expect(failed).toHaveTextContent('Could not load workflows')
        expect(failed).toHaveTextContent('An internal error occurred.')

        reply = () => Promise.resolve(ok(page([summary('wf_a', 'Alpha')])))
        fireEvent.click(within(failed).getByRole('button', { name: 'Retry' }))
        expect(await screen.findByRole('link', { name: 'Alpha' })).toBeVisible()
    })
})

// -- create ------------------------------------------------------------------------

describe('Create workflow', () => {
    it('creates through the backend and opens the new workflow', async () => {
        let bodies: unknown[] = []
        const requests = stubBackend({
            'GET /workflows': () => ok(page([])),
            'POST /workflows': (_url, init) => {
                bodies = [...bodies, jsonBody(init)]
                return created(detail('wf_new', 'Gamma'))
            },
            'GET /workflows/wf_new': () => ok(detail('wf_new', 'Gamma')),
        })
        const { router } = renderRoute('/workflows')
        await screen.findByRole('heading', { name: 'No workflows yet' })
        const dialog = await openDialog('New workflow')

        fireEvent.change(within(dialog).getByLabelText(/Name/), {
            target: { value: 'Gamma' },
        })
        fireEvent.change(within(dialog).getByLabelText('Description'), {
            target: { value: 'third' },
        })
        fireEvent.click(
            within(dialog).getByRole('button', { name: 'Create workflow' }),
        )

        expect(
            await screen.findByRole('heading', { level: 1, name: 'Gamma' }),
        ).toBeInTheDocument()
        expect(bodies).toEqual([{ name: 'Gamma', description: 'third' }])
        expect(router.state.location.pathname).toBe('/workflows/wf_new')
        expect(
            await screen.findByText('Workflow “Gamma” created'),
        ).toBeVisible()
        // The list was invalidated, not patched locally.
        expect(listRequests(requests).length).toBeGreaterThanOrEqual(1)
    })

    it('shows backend validation errors at the field and stays open', async () => {
        stubBackend({
            'GET /workflows': () => ok(page([])),
            'POST /workflows': () => ({
                status: 422,
                body: {
                    code: 'VALIDATION_ERROR',
                    message: 'Request validation failed.',
                    details: {
                        'body.name': 'String should have at least 1 character',
                    },
                    request_id: 'r',
                },
            }),
        })
        const { router } = renderRoute('/workflows')
        await screen.findByRole('heading', { name: 'No workflows yet' })
        const dialog = await openDialog('New workflow')

        // Whitespace passes the browser's `required`; the backend decides.
        fireEvent.change(within(dialog).getByLabelText(/Name/), {
            target: { value: '   ' },
        })
        fireEvent.click(
            within(dialog).getByRole('button', { name: 'Create workflow' }),
        )

        const name = within(dialog).getByLabelText(/Name/)
        await waitFor(() => {
            expect(name).toHaveAttribute('aria-invalid', 'true')
        })
        expect(name).toHaveAccessibleDescription(
            'String should have at least 1 character',
        )
        expect(dialog).toBeVisible()
        expect(router.state.location.pathname).toBe('/workflows')
    })

    it('shows a server error in the form', async () => {
        stubBackend({
            'GET /workflows': () => ok(page([])),
            'POST /workflows': () =>
                apiError(500, 'INTERNAL_ERROR', 'An internal error occurred.'),
        })
        renderRoute('/workflows')
        await screen.findByRole('heading', { name: 'No workflows yet' })
        const dialog = await openDialog('New workflow')

        fireEvent.change(within(dialog).getByLabelText(/Name/), {
            target: { value: 'Gamma' },
        })
        fireEvent.click(
            within(dialog).getByRole('button', { name: 'Create workflow' }),
        )

        expect(
            await within(dialog).findByText('An internal error occurred.'),
        ).toBeVisible()
        // Dismissing and reopening starts clean.
        fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))
        const again = await openDialog('New workflow')
        expect(
            within(again).queryByText('An internal error occurred.'),
        ).not.toBeInTheDocument()
    })
})

// -- detail ------------------------------------------------------------------------

describe('Workflow detail', () => {
    it('shows the workflow metadata and the way into the editor', async () => {
        stubBackend({
            'GET /workflows/wf_a': () =>
                ok(
                    detail('wf_a', 'Alpha', {
                        description: 'Line one\nLine two',
                        status: 'published',
                        current_version: 2,
                        revision: 7,
                    }),
                ),
        })
        renderRoute('/workflows/wf_a')

        expect(
            await screen.findByRole('heading', { level: 1, name: 'Alpha' }),
        ).toBeInTheDocument()
        const details = present(
            screen.getByRole('heading', { name: 'Details' }).closest('section'),
        )
        expect(details).toHaveTextContent('Published')
        expect(details).toHaveTextContent('v2')
        expect(details).toHaveTextContent('7')
        expect(details).toHaveTextContent(
            '1 nodes, 0 connections, 0 triggers, 0 files',
        )
        expect(details).toHaveTextContent('Line one Line two')
        expect(
            screen.getByRole('link', { name: 'Open editor' }),
        ).toHaveAttribute('href', '/workflows/wf_a/editor')
    })

    it('explains a workflow that does not exist or is not accessible', async () => {
        stubBackend({
            'GET /workflows/wf_x': () =>
                apiError(404, 'WORKFLOW_NOT_FOUND', 'Workflow not found.'),
        })
        renderRoute('/workflows/wf_x')

        expect(
            await screen.findByRole('heading', {
                level: 1,
                name: 'Workflow not found',
            }),
        ).toBeInTheDocument()
        expect(
            screen.getByRole('link', { name: 'Back to all workflows' }),
        ).toBeInTheDocument()
    })
})

// -- edit details (authoritative, not optimistic) ------------------------------

describe('Edit workflow details', () => {
    it('sends the unchanged draft with the read revision and shows the new name only once the backend confirms', async () => {
        const save = deferred()
        let body: unknown
        stubBackend({
            ...sharing('wf_a'),
            'GET /workflows/wf_a': () => ok(detail('wf_a', 'Alpha')),
            'PUT /workflows/wf_a': (_url, init) => {
                body = jsonBody(init)
                return save.promise
            },
        })
        renderRoute('/workflows/wf_a')
        await screen.findByRole('heading', { level: 1, name: 'Alpha' })
        const dialog = await openDialog('Edit details')

        fireEvent.change(within(dialog).getByLabelText(/Name/), {
            target: { value: 'Renamed' },
        })
        fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))

        await waitFor(() => {
            expect(body).toBeDefined()
        })
        expect(body).toEqual({
            name: 'Renamed',
            description: '',
            expected_revision: 1,
            draft: detail('wf_a', 'Alpha').draft,
        })
        // In flight: nothing changes before the backend answers.
        expect(
            screen.getByRole('heading', { level: 1, name: 'Alpha' }),
        ).toBeInTheDocument()
        expect(
            within(dialog).getByRole('button', { name: /Save/ }),
        ).toHaveAttribute('aria-busy', 'true')

        save.resolve(ok(detail('wf_a', 'Renamed', { revision: 2 })))

        expect(
            await screen.findByRole('heading', { level: 1, name: 'Renamed' }),
        ).toBeInTheDocument()
        expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    })

    it('keeps the server state on a conflict and reloads the workflow', async () => {
        let reads = 0
        stubBackend({
            ...sharing('wf_a'),
            'GET /workflows/wf_a': () => {
                reads += 1
                return ok(
                    reads === 1
                        ? detail('wf_a', 'Alpha')
                        : detail('wf_a', 'Alpha (edited elsewhere)', {
                              revision: 2,
                          }),
                )
            },
            'PUT /workflows/wf_a': () =>
                apiError(
                    409,
                    'CONCURRENT_MODIFICATION',
                    'The workflow was modified since it was read; reload and retry.',
                ),
        })
        renderRoute('/workflows/wf_a')
        await screen.findByRole('heading', { level: 1, name: 'Alpha' })
        const dialog = await openDialog('Edit details')

        fireEvent.change(within(dialog).getByLabelText(/Name/), {
            target: { value: 'Mine' },
        })
        fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))

        expect(
            await within(dialog).findByText(
                'The workflow was modified since it was read; reload and retry.',
            ),
        ).toBeVisible()
        expect(
            await screen.findByRole('heading', {
                level: 1,
                name: 'Alpha (edited elsewhere)',
            }),
        ).toBeInTheDocument()
        expect(screen.queryByText('Mine')).not.toBeInTheDocument()
    })
})

// -- duplicate and delete ----------------------------------------------------------

describe('Duplicate and delete', () => {
    it('duplicates through the backend and opens the copy', async () => {
        let body: unknown
        stubBackend({
            'GET /workflows/wf_a': () => ok(detail('wf_a', 'Alpha')),
            'POST /workflows/wf_a/duplicate': (_url, init) => {
                body = jsonBody(init)
                return created(detail('wf_c', 'Copy of Alpha'))
            },
            'GET /workflows/wf_c': () => ok(detail('wf_c', 'Copy of Alpha')),
        })
        const { router } = renderRoute('/workflows/wf_a')
        await screen.findByRole('heading', { level: 1, name: 'Alpha' })
        const dialog = await openDialog('Duplicate')

        expect(within(dialog).getByLabelText(/Name/)).toHaveValue(
            'Copy of Alpha',
        )
        fireEvent.click(
            within(dialog).getByRole('button', { name: 'Duplicate' }),
        )

        expect(
            await screen.findByRole('heading', {
                level: 1,
                name: 'Copy of Alpha',
            }),
        ).toBeInTheDocument()
        expect(body).toEqual({ name: 'Copy of Alpha' })
        expect(router.state.location.pathname).toBe('/workflows/wf_c')
    })

    it('deletes after confirmation and returns to the list', async () => {
        const requests = stubBackend({
            ...sharing('wf_a'),
            'GET /workflows/wf_a': () => ok(detail('wf_a', 'Alpha')),
            'DELETE /workflows/wf_a': () => noContent,
            'GET /workflows': () => ok(page([])),
        })
        const { router } = renderRoute('/workflows/wf_a')
        await screen.findByRole('heading', { level: 1, name: 'Alpha' })
        const dialog = await openDialog('Delete')

        expect(dialog).toHaveTextContent('This cannot be undone.')
        fireEvent.click(
            within(dialog).getByRole('button', { name: 'Delete workflow' }),
        )

        expect(
            await screen.findByRole('heading', { name: 'No workflows yet' }),
        ).toBeInTheDocument()
        expect(router.state.location.pathname).toBe('/workflows')
        expect(screen.getByText('Workflow “Alpha” deleted')).toBeVisible()
        expect(
            requests.filter((u) => u.pathname === '/api/v1/workflows/wf_a'),
        ).toHaveLength(2) // the read and the delete
    })

    it('shows the backend’s refusal to delete a published workflow', async () => {
        stubBackend({
            ...sharing('wf_a'),
            'GET /workflows/wf_a': () =>
                ok(
                    detail('wf_a', 'Alpha', {
                        status: 'published',
                        current_version: 1,
                    }),
                ),
            'DELETE /workflows/wf_a': () =>
                apiError(
                    409,
                    'WORKFLOW_HAS_VERSIONS',
                    'A workflow with published versions cannot be deleted.',
                ),
        })
        const { router } = renderRoute('/workflows/wf_a')
        await screen.findByRole('heading', { level: 1, name: 'Alpha' })
        const dialog = await openDialog('Delete')

        fireEvent.click(
            within(dialog).getByRole('button', { name: 'Delete workflow' }),
        )

        expect(
            await within(dialog).findByText(
                'A workflow with published versions cannot be deleted.',
            ),
        ).toBeVisible()
        expect(router.state.location.pathname).toBe('/workflows/wf_a')
        expect(
            screen.getByRole('heading', { level: 1, name: 'Alpha' }),
        ).toBeInTheDocument()
    })
})

describe('Sharing', () => {
    it('lets a viewer read but offers no edit or delete', async () => {
        stubBackend({
            'GET /workflows/wf_a': () => ok(detail('wf_a', 'Alpha')),
            ...sharing('wf_a', 'view'),
        })
        renderRoute('/workflows/wf_a')
        expect(
            await screen.findByText('You can view this workflow'),
        ).toBeVisible()
        expect(
            screen.queryByRole('button', { name: 'Edit details' }),
        ).toBeNull()
        expect(screen.queryByRole('button', { name: 'Delete' })).toBeNull()
        expect(
            screen.queryByRole('form', { name: 'Share workflow' }),
        ).toBeNull()
        expect(screen.getByRole('button', { name: 'Duplicate' })).toBeVisible()
    })

    it('shares by username, changes a role and stops sharing', async () => {
        const bodies: { method: string; path: string; body: unknown }[] = []
        let members: unknown[] = []
        const member = (role: string) => ({
            user_id: 'u_bob',
            username: 'bob',
            display_name: 'Bob B',
            role,
            created_at: AT,
        })
        const record = (method: string) => (url: URL, init: RequestInit) => {
            bodies.push({
                method,
                path: url.pathname,
                body: init.body ? jsonBody(init) : undefined,
            })
        }
        stubBackend({
            'GET /workflows/wf_a': () => ok(detail('wf_a', 'Alpha')),
            'GET /workflows/wf_a/members': () =>
                ok({ access: 'manage', owner: null, members }),
            'PUT /workflows/wf_a/members/bob': (url, init) => {
                record('PUT')(url, init)
                const { role } = z
                    .object({ role: z.string() })
                    .parse(jsonBody(init))
                members = [member(role)]
                return ok(members[0])
            },
            'DELETE /workflows/wf_a/members/bob': (url, init) => {
                record('DELETE')(url, init)
                members = []
                return noContent
            },
        })
        renderRoute('/workflows/wf_a')
        const form = await screen.findByRole('form', { name: 'Share workflow' })
        fireEvent.change(within(form).getByLabelText(/^Username/), {
            target: { value: ' Bob ' },
        })
        fireEvent.click(within(form).getByRole('button', { name: 'Share' }))
        expect(
            await screen.findByText('Shared with “Bob B” as viewer'),
        ).toBeVisible()
        const table = await screen.findByRole('table', { name: 'Shared with' })
        fireEvent.change(within(table).getByLabelText('Role of Bob B'), {
            target: { value: 'editor' },
        })
        expect(await screen.findByText('“Bob B” is now editor')).toBeVisible()
        fireEvent.click(
            screen.getByRole('button', { name: 'Stop sharing with Bob B' }),
        )
        const dialog = await screen.findByRole('dialog')
        fireEvent.click(
            within(dialog).getByRole('button', { name: 'Stop sharing' }),
        )
        expect(await screen.findByText('Not shared with anyone.')).toBeVisible()
        expect(bodies).toEqual([
            {
                method: 'PUT',
                path: '/api/v1/workflows/wf_a/members/bob',
                body: { role: 'viewer' },
            },
            {
                method: 'PUT',
                path: '/api/v1/workflows/wf_a/members/bob',
                body: { role: 'editor' },
            },
            {
                method: 'DELETE',
                path: '/api/v1/workflows/wf_a/members/bob',
                body: undefined,
            },
        ])
    })

    it('shows an unknown username at the field', async () => {
        stubBackend({
            'GET /workflows/wf_a': () => ok(detail('wf_a', 'Alpha')),
            ...sharing('wf_a'),
            'PUT /workflows/wf_a/members/ghost': () =>
                apiError(404, 'USER_NOT_FOUND', 'User not found.'),
        })
        renderRoute('/workflows/wf_a')
        const form = await screen.findByRole('form', { name: 'Share workflow' })
        fireEvent.change(within(form).getByLabelText(/^Username/), {
            target: { value: 'ghost' },
        })
        fireEvent.click(within(form).getByRole('button', { name: 'Share' }))
        expect(await within(form).findByText('User not found.')).toBeVisible()
        expect(within(form).getByLabelText(/^Username/)).toHaveAttribute(
            'aria-invalid',
            'true',
        )
    })

    it('marks workflows shared with the caller in the list', async () => {
        stubBackend({
            'GET /workflows': () =>
                ok(
                    page([
                        summary('wf_a', 'Mine', { owner_id: 'u_admin' }),
                        summary('wf_b', 'Theirs', { owner_id: 'u_other' }),
                    ]),
                ),
            'GET /auth/session': () =>
                ok({ ...testSession, user: { ...testUser, role: 'member' } }),
        })
        renderRoute('/workflows')
        const table = await screen.findByRole('table')
        const theirs = within(table).getByText('Theirs').closest('tr')
        const mine = within(table).getByText('Mine').closest('tr')
        if (!theirs || !mine) throw new Error('expected rows')
        expect(within(theirs).getByText('Shared with you')).toBeVisible()
        expect(within(mine).queryByText('Shared with you')).toBeNull()
    })

    it('transfers ownership by username', async () => {
        let body: unknown
        stubBackend({
            'GET /workflows/wf_a': () => ok(detail('wf_a', 'Alpha')),
            ...sharing('wf_a'),
            'PUT /workflows/wf_a/owner': (_url, init) => {
                body = jsonBody(init)
                return ok({
                    access: 'manage',
                    owner: {
                        user_id: 'u_bob',
                        username: 'bob',
                        display_name: 'Bob B',
                    },
                    members: [],
                })
            },
        })
        renderRoute('/workflows/wf_a')
        fireEvent.click(
            await screen.findByRole('button', { name: 'Transfer ownership' }),
        )
        const dialog = await screen.findByRole('dialog')
        expect(dialog).toHaveTextContent('stays on as an editor')
        fireEvent.change(within(dialog).getByLabelText(/^Username/), {
            target: { value: ' Bob ' },
        })
        fireEvent.click(
            within(dialog).getByRole('button', { name: 'Transfer ownership' }),
        )
        expect(
            await screen.findByText('“Bob B” now owns the workflow'),
        ).toBeVisible()
        expect(body).toEqual({ username: 'bob' })
    })

    it('shows a disabled new owner at the field', async () => {
        stubBackend({
            'GET /workflows/wf_a': () => ok(detail('wf_a', 'Alpha')),
            ...sharing('wf_a'),
            'PUT /workflows/wf_a/owner': () =>
                apiError(
                    409,
                    'USER_DISABLED',
                    'A disabled user cannot own a workflow.',
                ),
        })
        renderRoute('/workflows/wf_a')
        fireEvent.click(
            await screen.findByRole('button', { name: 'Transfer ownership' }),
        )
        const dialog = await screen.findByRole('dialog')
        fireEvent.change(within(dialog).getByLabelText(/^Username/), {
            target: { value: 'carol' },
        })
        fireEvent.click(
            within(dialog).getByRole('button', { name: 'Transfer ownership' }),
        )
        expect(
            await within(dialog).findByText(
                'A disabled user cannot own a workflow.',
            ),
        ).toBeVisible()
        expect(within(dialog).getByLabelText(/^Username/)).toHaveAttribute(
            'aria-invalid',
            'true',
        )
    })

    it('lets a member leave and returns to the list', async () => {
        const requests = stubBackend({
            'GET /auth/session': () =>
                ok({ ...testSession, user: { ...testUser, role: 'member' } }),
            'GET /workflows/wf_a': () => ok(detail('wf_a', 'Alpha')),
            ...sharing('wf_a', 'edit', [
                {
                    user_id: testUser.id,
                    username: testUser.username,
                    display_name: null,
                    role: 'editor',
                    created_at: AT,
                },
            ]),
            'DELETE /workflows/wf_a/membership': () => noContent,
            'GET /workflows': () => ok(page([])),
        })
        const { router } = renderRoute('/workflows/wf_a')
        expect(
            screen.queryByRole('button', { name: 'Transfer ownership' }),
        ).toBeNull()
        fireEvent.click(
            await screen.findByRole('button', { name: 'Leave workflow' }),
        )
        const dialog = await screen.findByRole('dialog')
        fireEvent.click(
            within(dialog).getByRole('button', { name: 'Leave workflow' }),
        )
        expect(await screen.findByText('You left the workflow')).toBeVisible()
        await waitFor(() => {
            expect(router.state.location.pathname).toBe('/workflows')
        })
        expect(
            requests.some(
                (u) => u.pathname === '/api/v1/workflows/wf_a/membership',
            ),
        ).toBe(true)
    })

    it('offers the owner no way to leave', async () => {
        stubBackend({
            'GET /workflows/wf_a': () => ok(detail('wf_a', 'Alpha')),
            ...sharing('wf_a'),
        })
        renderRoute('/workflows/wf_a')
        expect(
            await screen.findByRole('button', { name: 'Transfer ownership' }),
        ).toBeVisible()
        expect(
            screen.queryByRole('button', { name: 'Leave workflow' }),
        ).toBeNull()
    })
})
