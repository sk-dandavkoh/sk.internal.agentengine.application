import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import {
    apiError,
    ok,
    renderRoute,
    stubBackend,
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
    requests.filter((u) => u.pathname === '/api/v1/projects')

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

describe('Projects list', () => {
    it('shows projects with status, latest version and a link to open them', async () => {
        const requests = stubBackend({
            'GET /projects': () =>
                ok(
                    page([
                        summary('proj_a', 'Alpha', { description: 'first' }),
                        summary('proj_b', 'Beta', {
                            status: 'published',
                            current_version: 3,
                        }),
                    ]),
                ),
        })
        renderRoute('/projects')

        const table = await screen.findByRole('table', { name: 'Projects' })
        const rows = within(table).getAllByRole('row')
        expect(rows[1]).toHaveTextContent(/Alpha.*first.*Draft.*—/)
        expect(rows[2]).toHaveTextContent(/Beta.*Published.*v3/)
        expect(
            within(table).getByRole('link', { name: 'Beta' }),
        ).toHaveAttribute('href', '/projects/proj_b')
        const [url] = listRequests(requests)
        expect(url?.searchParams.get('sort')).toBe('-updated_at')
        expect(url?.searchParams.get('limit')).toBe('25')
        expect(url?.searchParams.has('q')).toBe(false)
    })

    it('opens a project from the list', async () => {
        stubBackend({
            'GET /projects': () => ok(page([summary('proj_a', 'Alpha')])),
            'GET /projects/proj_a': () => ok(detail('proj_a', 'Alpha')),
        })
        const { router } = renderRoute('/projects')

        fireEvent.click(await screen.findByRole('link', { name: 'Alpha' }))

        expect(
            await screen.findByRole('heading', { level: 1, name: 'Alpha' }),
        ).toBeInTheDocument()
        expect(router.state.location.pathname).toBe('/projects/proj_a')
    })

    it('invites to create the first project when there are none', async () => {
        stubBackend({ 'GET /projects': () => ok(page([])) })
        renderRoute('/projects')

        const empty = await screen.findByRole('heading', {
            name: 'No projects yet',
        })
        const state = present(empty.parentElement)
        fireEvent.click(
            within(state).getByRole('button', { name: 'New project' }),
        )
        expect(
            await screen.findByRole('dialog', { name: 'New project' }),
        ).toBeInTheDocument()
    })

    it('searches, filters and sorts through the backend, kept in the URL', async () => {
        const requests = stubBackend({
            'GET /projects': (url) =>
                ok(
                    page(
                        url.searchParams.has('q')
                            ? []
                            : [summary('proj_a', 'Alpha')],
                    ),
                ),
        })
        const { router } = renderRoute('/projects')
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
                name: 'No matching projects',
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
            'GET /projects': (url) =>
                url.searchParams.get('offset') === '25'
                    ? ok(page([summary('proj_z', 'Zeta')]))
                    : ok(page([summary('proj_a', 'Alpha')], true)),
        })
        renderRoute('/projects')
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
        stubBackend({ 'GET /projects': () => reply() })
        renderRoute('/projects')
        expect(await screen.findByText('Loading projects…')).toBeInTheDocument()

        first.resolve(
            apiError(500, 'INTERNAL_ERROR', 'An internal error occurred.'),
        )
        const failed = await screen.findByRole('alert')
        expect(failed).toHaveTextContent('Could not load projects')
        expect(failed).toHaveTextContent('An internal error occurred.')

        reply = () => Promise.resolve(ok(page([summary('proj_a', 'Alpha')])))
        fireEvent.click(within(failed).getByRole('button', { name: 'Retry' }))
        expect(await screen.findByRole('link', { name: 'Alpha' })).toBeVisible()
    })
})

// -- create ------------------------------------------------------------------------

describe('Create project', () => {
    it('creates through the backend and opens the new project', async () => {
        let bodies: unknown[] = []
        const requests = stubBackend({
            'GET /projects': () => ok(page([])),
            'POST /projects': (_url, init) => {
                bodies = [...bodies, jsonBody(init)]
                return created(detail('proj_new', 'Gamma'))
            },
            'GET /projects/proj_new': () => ok(detail('proj_new', 'Gamma')),
        })
        const { router } = renderRoute('/projects')
        await screen.findByRole('heading', { name: 'No projects yet' })
        const dialog = await openDialog('New project')

        fireEvent.change(within(dialog).getByLabelText(/Name/), {
            target: { value: 'Gamma' },
        })
        fireEvent.change(within(dialog).getByLabelText('Description'), {
            target: { value: 'third' },
        })
        fireEvent.click(
            within(dialog).getByRole('button', { name: 'Create project' }),
        )

        expect(
            await screen.findByRole('heading', { level: 1, name: 'Gamma' }),
        ).toBeInTheDocument()
        expect(bodies).toEqual([{ name: 'Gamma', description: 'third' }])
        expect(router.state.location.pathname).toBe('/projects/proj_new')
        expect(await screen.findByText('Project “Gamma” created')).toBeVisible()
        // The list was invalidated, not patched locally.
        expect(listRequests(requests).length).toBeGreaterThanOrEqual(1)
    })

    it('shows backend validation errors at the field and stays open', async () => {
        stubBackend({
            'GET /projects': () => ok(page([])),
            'POST /projects': () => ({
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
        const { router } = renderRoute('/projects')
        await screen.findByRole('heading', { name: 'No projects yet' })
        const dialog = await openDialog('New project')

        // Whitespace passes the browser's `required`; the backend decides.
        fireEvent.change(within(dialog).getByLabelText(/Name/), {
            target: { value: '   ' },
        })
        fireEvent.click(
            within(dialog).getByRole('button', { name: 'Create project' }),
        )

        const name = within(dialog).getByLabelText(/Name/)
        await waitFor(() => {
            expect(name).toHaveAttribute('aria-invalid', 'true')
        })
        expect(name).toHaveAccessibleDescription(
            'String should have at least 1 character',
        )
        expect(dialog).toBeVisible()
        expect(router.state.location.pathname).toBe('/projects')
    })

    it('shows a server error in the form', async () => {
        stubBackend({
            'GET /projects': () => ok(page([])),
            'POST /projects': () =>
                apiError(500, 'INTERNAL_ERROR', 'An internal error occurred.'),
        })
        renderRoute('/projects')
        await screen.findByRole('heading', { name: 'No projects yet' })
        const dialog = await openDialog('New project')

        fireEvent.change(within(dialog).getByLabelText(/Name/), {
            target: { value: 'Gamma' },
        })
        fireEvent.click(
            within(dialog).getByRole('button', { name: 'Create project' }),
        )

        expect(
            await within(dialog).findByText('An internal error occurred.'),
        ).toBeVisible()
        // Dismissing and reopening starts clean.
        fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))
        const again = await openDialog('New project')
        expect(
            within(again).queryByText('An internal error occurred.'),
        ).not.toBeInTheDocument()
    })
})

// -- detail ------------------------------------------------------------------------

describe('Project detail', () => {
    it('shows the project metadata and the way into the editor', async () => {
        stubBackend({
            'GET /projects/proj_a': () =>
                ok(
                    detail('proj_a', 'Alpha', {
                        description: 'Line one\nLine two',
                        status: 'published',
                        current_version: 2,
                        revision: 7,
                    }),
                ),
        })
        renderRoute('/projects/proj_a')

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
        ).toHaveAttribute('href', '/projects/proj_a/editor')
    })

    it('explains a project that does not exist or is not accessible', async () => {
        stubBackend({
            'GET /projects/proj_x': () =>
                apiError(404, 'PROJECT_NOT_FOUND', 'Project not found.'),
        })
        renderRoute('/projects/proj_x')

        expect(
            await screen.findByRole('heading', {
                level: 1,
                name: 'Project not found',
            }),
        ).toBeInTheDocument()
        expect(
            screen.getByRole('link', { name: 'Back to all projects' }),
        ).toBeInTheDocument()
    })
})

// -- edit details (authoritative, not optimistic) ------------------------------

describe('Edit project details', () => {
    it('sends the unchanged draft with the read revision and shows the new name only once the backend confirms', async () => {
        const save = deferred()
        let body: unknown
        stubBackend({
            'GET /projects/proj_a': () => ok(detail('proj_a', 'Alpha')),
            'PUT /projects/proj_a': (_url, init) => {
                body = jsonBody(init)
                return save.promise
            },
        })
        renderRoute('/projects/proj_a')
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
            draft: detail('proj_a', 'Alpha').draft,
        })
        // In flight: nothing changes before the backend answers.
        expect(
            screen.getByRole('heading', { level: 1, name: 'Alpha' }),
        ).toBeInTheDocument()
        expect(
            within(dialog).getByRole('button', { name: /Save/ }),
        ).toHaveAttribute('aria-busy', 'true')

        save.resolve(ok(detail('proj_a', 'Renamed', { revision: 2 })))

        expect(
            await screen.findByRole('heading', { level: 1, name: 'Renamed' }),
        ).toBeInTheDocument()
        expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    })

    it('keeps the server state on a conflict and reloads the project', async () => {
        let reads = 0
        stubBackend({
            'GET /projects/proj_a': () => {
                reads += 1
                return ok(
                    reads === 1
                        ? detail('proj_a', 'Alpha')
                        : detail('proj_a', 'Alpha (edited elsewhere)', {
                              revision: 2,
                          }),
                )
            },
            'PUT /projects/proj_a': () =>
                apiError(
                    409,
                    'CONCURRENT_MODIFICATION',
                    'The project was modified since it was read; reload and retry.',
                ),
        })
        renderRoute('/projects/proj_a')
        await screen.findByRole('heading', { level: 1, name: 'Alpha' })
        const dialog = await openDialog('Edit details')

        fireEvent.change(within(dialog).getByLabelText(/Name/), {
            target: { value: 'Mine' },
        })
        fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))

        expect(
            await within(dialog).findByText(
                'The project was modified since it was read; reload and retry.',
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
            'GET /projects/proj_a': () => ok(detail('proj_a', 'Alpha')),
            'POST /projects/proj_a/duplicate': (_url, init) => {
                body = jsonBody(init)
                return created(detail('proj_c', 'Copy of Alpha'))
            },
            'GET /projects/proj_c': () => ok(detail('proj_c', 'Copy of Alpha')),
        })
        const { router } = renderRoute('/projects/proj_a')
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
        expect(router.state.location.pathname).toBe('/projects/proj_c')
    })

    it('deletes after confirmation and returns to the list', async () => {
        const requests = stubBackend({
            'GET /projects/proj_a': () => ok(detail('proj_a', 'Alpha')),
            'DELETE /projects/proj_a': () => noContent,
            'GET /projects': () => ok(page([])),
        })
        const { router } = renderRoute('/projects/proj_a')
        await screen.findByRole('heading', { level: 1, name: 'Alpha' })
        const dialog = await openDialog('Delete')

        expect(dialog).toHaveTextContent('This cannot be undone.')
        fireEvent.click(
            within(dialog).getByRole('button', { name: 'Delete project' }),
        )

        expect(
            await screen.findByRole('heading', { name: 'No projects yet' }),
        ).toBeInTheDocument()
        expect(router.state.location.pathname).toBe('/projects')
        expect(screen.getByText('Project “Alpha” deleted')).toBeVisible()
        expect(
            requests.filter((u) => u.pathname === '/api/v1/projects/proj_a'),
        ).toHaveLength(2) // the read and the delete
    })

    it('shows the backend’s refusal to delete a published project', async () => {
        stubBackend({
            'GET /projects/proj_a': () =>
                ok(
                    detail('proj_a', 'Alpha', {
                        status: 'published',
                        current_version: 1,
                    }),
                ),
            'DELETE /projects/proj_a': () =>
                apiError(
                    409,
                    'PROJECT_HAS_VERSIONS',
                    'A project with published versions cannot be deleted.',
                ),
        })
        const { router } = renderRoute('/projects/proj_a')
        await screen.findByRole('heading', { level: 1, name: 'Alpha' })
        const dialog = await openDialog('Delete')

        fireEvent.click(
            within(dialog).getByRole('button', { name: 'Delete project' }),
        )

        expect(
            await within(dialog).findByText(
                'A project with published versions cannot be deleted.',
            ),
        ).toBeVisible()
        expect(router.state.location.pathname).toBe('/projects/proj_a')
        expect(
            screen.getByRole('heading', { level: 1, name: 'Alpha' }),
        ).toBeInTheDocument()
    })
})
