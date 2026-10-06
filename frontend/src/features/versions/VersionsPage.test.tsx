import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import {
    apiError,
    ok,
    pending,
    renderRoute,
    stubBackend,
    type Reply,
} from '../../test/app'

// -- factories (valid by default) -------------------------------------------------

const AT = '2026-10-02T08:00:00Z'
const PID = 'proj_1'

function project(overrides: object = {}) {
    return {
        id: PID,
        name: 'Lead scoring',
        description: '',
        status: 'published',
        owner_id: 'user_1',
        revision: 5,
        current_version: 2,
        created_at: AT,
        updated_at: AT,
        draft: {
            graph: { nodes: [], edges: [] },
            triggers: [],
            dependencies: [],
        },
        files: [],
        ...overrides,
    }
}

function summary(version: number, overrides: object = {}) {
    return {
        id: `pver_${String(version)}`,
        version,
        description: `Release ${String(version)}`,
        schema_version: 1,
        published_at: AT,
        published_by: { id: 'user_1', username: 'ada' },
        ...overrides,
    }
}

const node = (id: string, config: object = {}) => ({
    id,
    node_type: 'core.log',
    config,
    label: '',
    position: null,
})

function version(n: number, nodes = [node('a')]) {
    return {
        ...summary(n),
        graph: { nodes, edges: [] },
        triggers: [
            { id: 'manual', trigger_type: 'manual', config: {}, enabled: true },
        ],
        dependencies: [],
        files: [{ path: 'docs/readme.md', size: 12 }],
        plugin_versions: {},
    }
}

function execution(id: string, n: number, status = 'completed') {
    return {
        id,
        project_id: PID,
        project_name: 'Lead scoring',
        version: n,
        status,
        trigger_type: 'manual',
        trigger_id: null,
        correlation_id: null,
        created_at: AT,
        started_at: AT,
        finished_at: AT,
        cancellable: false,
    }
}

const page = (items: unknown[], has_more = false) => ({
    items,
    page: { offset: 0, limit: 50, has_more },
})

/** A Card, found by its title. */
async function card(name: string) {
    const heading = await screen.findByRole('heading', { name })
    const section = heading.closest('section')
    if (section === null) throw new Error(`no card "${name}"`)
    return section
}

const routes = {
    project: `GET /projects/${PID}`,
    versions: `GET /projects/${PID}/versions`,
    version: (n: number) => `GET /projects/${PID}/versions/${String(n)}`,
    executions: `GET /projects/${PID}/executions`,
}

// -- list ---------------------------------------------------------------------------

describe('Versions list', () => {
    it('shows each published version with its metadata, latest marked', async () => {
        stubBackend({
            [routes.project]: () => ok(project()),
            [routes.versions]: () =>
                ok(page([summary(1, { published_by: null }), summary(2)])),
        })
        renderRoute(`/projects/${PID}/versions`)

        const table = await screen.findByRole('table', {
            name: 'Published versions',
        })
        const [, first, second] = within(table)
            .getAllByRole('row')
            .map((row) => within(row))
        if (first === undefined || second === undefined)
            throw new Error('expected two version rows')
        expect(first.getByRole('link', { name: 'v1' })).toHaveAttribute(
            'href',
            `/projects/${PID}/versions/1`,
        )
        expect(first.getByText('Not recorded')).toBeInTheDocument()
        expect(first.queryByText('Latest')).not.toBeInTheDocument()
        expect(second.getByText('Latest')).toBeInTheDocument()
        expect(second.getByText('ada')).toHaveAttribute('title', 'user_1')
        expect(second.getByText('Release 2')).toBeInTheDocument()
        expect(
            second.getByText(
                (_, el) =>
                    el?.tagName === 'TIME' &&
                    el.getAttribute('dateTime') === AT,
            ),
        ).toBeInTheDocument()
    })

    it('keeps the draft apart and marks versions immutable', async () => {
        stubBackend({
            [routes.project]: () => ok(project()),
            [routes.versions]: () => ok(page([summary(1)])),
        })
        renderRoute(`/projects/${PID}/versions`)

        const draft = await card('Draft')
        expect(
            within(draft).getByRole('link', { name: 'Open draft in editor' }),
        ).toHaveAttribute('href', `/projects/${PID}/editor`)
        expect(within(draft).getByText('5')).toBeInTheDocument() // revision
        const published = await card('Published versions')
        expect(within(published).getByText('Immutable')).toBeInTheDocument()
        // Nothing on a version list edits it.
        expect(
            within(published).queryByRole('button', { name: /edit|delete/i }),
        ).toBeNull()
    })

    it('says when nothing is published yet', async () => {
        stubBackend({
            [routes.project]: () =>
                ok(project({ current_version: 0, status: 'draft' })),
            [routes.versions]: () => ok(page([])),
        })
        renderRoute(`/projects/${PID}/versions`)

        expect(
            await screen.findByRole('heading', { name: 'Not published yet' }),
        ).toBeInTheDocument()
        expect(screen.queryByRole('table')).not.toBeInTheDocument()
    })

    it('pages through versions', async () => {
        const requests = stubBackend({
            [routes.project]: () => ok(project({ current_version: 51 })),
            [routes.versions]: (url) =>
                url.searchParams.get('offset') === '50'
                    ? ok(page([summary(51)]))
                    : ok(page([summary(1)], true)),
        })
        renderRoute(`/projects/${PID}/versions`)

        fireEvent.click(await screen.findByRole('button', { name: 'Next' }))
        expect(
            await screen.findByRole('link', { name: 'v51' }),
        ).toBeInTheDocument()
        const offsets = requests
            .filter((u) => u.pathname.endsWith('/versions'))
            .map((u) => [
                u.searchParams.get('offset'),
                u.searchParams.get('limit'),
            ])
        expect(offsets).toEqual([
            ['0', '50'],
            ['50', '50'],
        ])
    })

    it('reports a failed load and retries', async () => {
        let calls = 0
        stubBackend({
            [routes.project]: () => ok(project()),
            [routes.versions]: () =>
                ++calls === 1
                    ? apiError(500, 'INTERNAL_ERROR', 'Internal error.')
                    : ok(page([summary(1)])),
        })
        renderRoute(`/projects/${PID}/versions`)

        const alert = await screen.findByRole('alert')
        expect(
            within(alert).getByText('Could not load versions'),
        ).toBeInTheDocument()
        fireEvent.click(within(alert).getByRole('button', { name: 'Retry' }))
        expect(
            await screen.findByRole('link', { name: 'v1' }),
        ).toBeInTheDocument()
    })

    it('shows a missing project as not found', async () => {
        stubBackend({
            [routes.project]: () =>
                apiError(404, 'PROJECT_NOT_FOUND', 'Project not found.'),
        })
        renderRoute(`/projects/${PID}/versions`)

        expect(
            await screen.findByRole('heading', {
                name: 'Project not found',
                level: 1,
            }),
        ).toBeInTheDocument()
    })
})

// -- detail -------------------------------------------------------------------------

describe('Version details', () => {
    it('presents the snapshot as read-only, with its metadata', async () => {
        stubBackend({
            [routes.project]: () => ok(project()),
            [routes.version(2)]: () =>
                ok(version(2, [node('a', { level: 'info' })])),
            [routes.version(1)]: pending,
            [routes.executions]: () => ok(page([])),
        })
        renderRoute(`/projects/${PID}/versions/2`)

        expect(
            await screen.findByRole('heading', {
                name: 'Lead scoring · v2',
                level: 1,
            }),
        ).toBeInTheDocument()
        expect(screen.getByText('Immutable')).toBeInTheDocument()
        expect(screen.getByText('Latest')).toBeInTheDocument()
        expect(
            screen.getByText('Published versions are read-only'),
        ).toBeInTheDocument()
        expect(screen.getByText('pver_2')).toBeInTheDocument()
        expect(screen.getByText('v2 (latest)')).toBeInTheDocument()

        const nodes = screen.getByRole('table', { name: 'Nodes' })
        expect(within(nodes).getByText('level')).toBeInTheDocument()
        // Config values are not shown, only which fields are set.
        expect(within(nodes).queryByText('info')).not.toBeInTheDocument()
        expect(
            within(screen.getByRole('table', { name: 'Files' })).getByText(
                'docs/readme.md',
            ),
        ).toBeInTheDocument()

        // The graph is drawn, frozen: nodes cannot be dragged or selected.
        const graph = screen.getByRole('figure', {
            name: 'Published graph (read-only)',
        })
        const drawn = within(graph).getByRole('group', { name: /^core\.log/ })
        expect(drawn.closest('.react-flow__node')).not.toHaveClass(
            'draggable',
            'selectable',
        )

        // No editing controls: inputs exist only to pick a comparison.
        expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
        expect(
            screen.queryByRole('button', { name: /save|publish|delete/i }),
        ).toBeNull()
    })

    it('compares with another version, the previous one first', async () => {
        stubBackend({
            [routes.project]: () => ok(project({ current_version: 3 })),
            [routes.version(1)]: () => ok(version(1, [node('a')])),
            [routes.version(2)]: () =>
                ok(version(2, [node('a', { level: 'debug' }), node('b')])),
            [routes.version(3)]: () =>
                ok(version(3, [node('a', { level: 'debug' })])),
            [routes.executions]: () => ok(page([])),
        })
        renderRoute(`/projects/${PID}/versions/2`)

        const select = await screen.findByRole('combobox', {
            name: 'Compare with',
        })
        expect(select).toHaveValue('1')
        const result = await screen.findByRole('region', {
            name: 'Comparison result',
        })
        expect(
            within(result).getByText('Changes from v1 to v2:'),
        ).toBeInTheDocument()
        expect(within(result).getByText('b').previousSibling).toHaveTextContent(
            'Added',
        )
        expect(within(result).getByText('a').previousSibling).toHaveTextContent(
            'Changed',
        )

        fireEvent.change(select, { target: { value: '3' } })
        expect(
            await screen.findByText('Changes from v2 to v3:'),
        ).toBeInTheDocument()
        expect(
            within(
                screen.getByRole('region', { name: 'Comparison result' }),
            ).getByText('b').previousSibling,
        ).toHaveTextContent('Removed')
    })

    it('compares the only version with the current draft', async () => {
        stubBackend({
            [routes.project]: () =>
                ok(
                    project({
                        current_version: 1,
                        draft: {
                            graph: { nodes: [node('a'), node('z')], edges: [] },
                            triggers: [],
                            dependencies: [],
                        },
                    }),
                ),
            [routes.version(1)]: () => ok(version(1, [node('a')])),
            [routes.executions]: () => ok(page([])),
        })
        renderRoute(`/projects/${PID}/versions/1`)

        const select = await screen.findByRole('combobox', {
            name: 'Compare with',
        })
        expect(select).toHaveValue('draft')
        const result = screen.getByRole('region', { name: 'Comparison result' })
        expect(
            within(result).getByText('Changes from v1 to the current draft:'),
        ).toBeInTheDocument()
        expect(within(result).getByText('z').previousSibling).toHaveTextContent(
            'Added',
        )
        // The draft's files count too (the version has docs/readme.md).
        expect(
            within(result).getByText('docs/readme.md').previousSibling,
        ).toHaveTextContent('Removed')
    })

    it('lists only the executions of this version', async () => {
        const requests = stubBackend({
            [routes.project]: () => ok(project()),
            [routes.version(1)]: pending,
            [routes.version(2)]: () => ok(version(2)),
            [routes.executions]: () =>
                ok(page([execution('exec_1', 2, 'failed')])),
        })
        renderRoute(`/projects/${PID}/versions/2`)

        const runs = await card('Executions of this version')
        expect(await within(runs).findByText('exec_1')).toBeInTheDocument()
        expect(within(runs).getByText('Failed')).toBeInTheDocument()
        const [listed] = requests.filter((u) =>
            u.pathname.endsWith('/executions'),
        )
        expect(listed?.searchParams.get('version')).toBe('2')
    })

    it('says when a version never ran', async () => {
        stubBackend({
            [routes.project]: () => ok(project()),
            [routes.version(1)]: pending,
            [routes.version(2)]: () => ok(version(2)),
            [routes.executions]: () => ok(page([])),
        })
        renderRoute(`/projects/${PID}/versions/2`)

        expect(
            await screen.findByRole('heading', { name: 'Not run yet' }),
        ).toBeInTheDocument()
    })

    it.each(['9', '0', 'abc', '02'])(
        'shows version "%s" as not found',
        async (param) => {
            stubBackend({
                [routes.project]: () => ok(project()),
                [routes.version(9)]: () =>
                    apiError(
                        404,
                        'PROJECT_VERSION_NOT_FOUND',
                        'Project version not found.',
                    ),
            })
            renderRoute(`/projects/${PID}/versions/${param}`)

            expect(
                await screen.findByRole('heading', {
                    name: 'Version not found',
                    level: 1,
                }),
            ).toBeInTheDocument()
            expect(
                screen.getByRole('link', { name: 'All versions' }),
            ).toHaveAttribute('href', `/projects/${PID}/versions`)
        },
    )

    it('reports a failed version load', async () => {
        stubBackend({
            [routes.project]: () => ok(project()),
            [routes.version(2)]: () =>
                apiError(500, 'INTERNAL_ERROR', 'Internal error.'),
        })
        renderRoute(`/projects/${PID}/versions/2`)

        expect(
            await screen.findByRole('heading', {
                name: 'Could not load version 2',
            }),
        ).toBeInTheDocument()
    })
})

// -- restore and clone ----------------------------------------------------------

function jsonBody(init: RequestInit): unknown {
    return typeof init.body === 'string' ? JSON.parse(init.body) : undefined
}

describe('Restore and clone', () => {
    const base = {
        [routes.version(1)]: pending,
        [routes.version(2)]: () => ok(version(2)),
        [routes.executions]: () => ok(page([])),
        'GET /nodes': pending,
    }

    it('restores a version into the draft, then opens the editor', async () => {
        const bodies: unknown[] = []
        stubBackend({
            ...base,
            [routes.project]: () => ok(project()),
            [`POST /projects/${PID}/versions/2/restore`]: (_url, init) => {
                bodies.push(jsonBody(init))
                return ok(project({ revision: 6 }))
            },
        })
        const { router } = renderRoute(`/projects/${PID}/versions/2`)

        fireEvent.click(
            await screen.findByRole('button', { name: 'Restore to draft…' }),
        )
        const dialog = screen.getByRole('dialog', {
            name: 'Restore v2 to the draft?',
        })
        expect(dialog).toHaveTextContent('revision 5')
        fireEvent.click(
            within(dialog).getByRole('button', { name: 'Replace draft' }),
        )

        expect(
            await screen.findByText('Draft restored from v2'),
        ).toBeInTheDocument()
        expect(bodies).toEqual([{ expected_revision: 5 }])
        await waitFor(() => {
            expect(router.state.location.pathname).toBe(
                `/projects/${PID}/editor`,
            )
        })
    })

    it('refuses to replace a draft changed elsewhere and reloads it', async () => {
        let loads = 0
        stubBackend({
            ...base,
            [routes.project]: () => {
                loads += 1
                return ok(project({ revision: loads === 1 ? 5 : 7 }))
            },
            [`POST /projects/${PID}/versions/2/restore`]: () =>
                apiError(
                    409,
                    'CONCURRENT_MODIFICATION',
                    'The project was modified since it was read; reload and retry.',
                ),
        })
        renderRoute(`/projects/${PID}/versions/2`)

        fireEvent.click(
            await screen.findByRole('button', { name: 'Restore to draft…' }),
        )
        const dialog = screen.getByRole('dialog')
        fireEvent.click(
            within(dialog).getByRole('button', { name: 'Replace draft' }),
        )

        expect(
            await within(dialog).findByText(/modified since it was read/),
        ).toBeInTheDocument()
        // The reloaded draft is what a retry would replace.
        expect(
            await within(dialog).findByText(/revision 7/),
        ).toBeInTheDocument()
    })

    it('clones a version as a new project', async () => {
        const bodies: unknown[] = []
        stubBackend({
            ...base,
            [routes.project]: () => ok(project()),
            [`POST /projects/${PID}/duplicate`]: (_url, init): Reply => {
                bodies.push(jsonBody(init))
                return {
                    status: 201,
                    body: project({
                        id: 'proj_copy',
                        name: 'Copy',
                        current_version: 0,
                    }),
                }
            },
            'GET /projects/proj_copy': pending,
        })
        const { router } = renderRoute(`/projects/${PID}/versions/2`)

        fireEvent.click(
            await screen.findByRole('button', {
                name: 'Clone as new project…',
            }),
        )
        const dialog = screen.getByRole('dialog', {
            name: 'Clone v2 as a new project',
        })
        expect(
            within(dialog).getByRole('textbox', { name: /name/i }),
        ).toHaveValue('Lead scoring (v2)')
        fireEvent.click(
            within(dialog).getByRole('button', { name: 'Create project' }),
        )

        expect(
            await screen.findByText('Project “Copy” created'),
        ).toBeInTheDocument()
        expect(bodies).toEqual([{ name: 'Lead scoring (v2)', version: 2 }])
        await waitFor(() => {
            expect(router.state.location.pathname).toBe('/projects/proj_copy')
        })
    })

    it('runs the version with a JSON input, then opens the execution', async () => {
        const bodies: unknown[] = []
        const queued = {
            ...execution('exec_new', 2, 'queued'),
            cancellable: true,
        }
        stubBackend({
            ...base,
            [routes.project]: () => ok(project()),
            [`POST /projects/${PID}/versions/2/execute`]: (_url, init) => {
                bodies.push(jsonBody(init))
                return { status: 201, body: queued }
            },
            'GET /executions/exec_new': () => ok(queued),
            'GET /reports/executions/exec_new': pending,
        })
        const { router } = renderRoute(`/projects/${PID}/versions/2`)

        fireEvent.click(await screen.findByRole('button', { name: 'Run…' }))
        const dialog = screen.getByRole('dialog', { name: 'Run v2' })
        const input = within(dialog).getByLabelText('Input (JSON object)')
        fireEvent.change(input, { target: { value: '[1, 2]' } })
        fireEvent.click(
            within(dialog).getByRole('button', { name: 'Start execution' }),
        )
        // Rejected locally before any request: not an object.
        expect(
            within(dialog).getByText(/must be a JSON object/),
        ).toBeInTheDocument()
        expect(bodies).toEqual([])

        fireEvent.change(input, { target: { value: '{"lead": "acme"}' } })
        fireEvent.click(
            within(dialog).getByRole('button', { name: 'Start execution' }),
        )
        await waitFor(() => {
            expect(router.state.location.pathname).toBe('/executions/exec_new')
        })
        expect(bodies).toEqual([{ lead: 'acme' }])
        expect(
            await screen.findByText('Execution of v2 started'),
        ).toBeInTheDocument()
        expect(
            await screen.findByRole('button', {
                name: 'Cancel execution exec_new',
            }),
        ).toBeInTheDocument()
    })

    it('shows why the backend refused to start a run', async () => {
        stubBackend({
            ...base,
            [routes.project]: () => ok(project()),
            [`POST /projects/${PID}/versions/2/execute`]: () =>
                apiError(
                    422,
                    'VALIDATION_ERROR',
                    'Input field "lead" is required.',
                ),
        })
        renderRoute(`/projects/${PID}/versions/2`)

        fireEvent.click(await screen.findByRole('button', { name: 'Run…' }))
        const dialog = screen.getByRole('dialog', { name: 'Run v2' })
        fireEvent.click(
            within(dialog).getByRole('button', { name: 'Start execution' }),
        )
        expect(
            await within(dialog).findByText(/Input field "lead" is required/),
        ).toBeInTheDocument()
    })
})

// -- navigation ---------------------------------------------------------------------

describe('Version navigation', () => {
    it('goes from the project to its versions and on to one version', async () => {
        stubBackend({
            [routes.project]: () => ok(project()),
            [routes.versions]: () => ok(page([summary(1), summary(2)])),
            [routes.version(1)]: () => ok(version(1)),
            [routes.version(2)]: () => ok(version(2)),
            [routes.executions]: () => ok(page([])),
        })
        const { router } = renderRoute(`/projects/${PID}`)

        expect(await screen.findByRole('link', { name: 'v2' })).toHaveAttribute(
            'href',
            `/projects/${PID}/versions/2`,
        )
        fireEvent.click(screen.getByRole('link', { name: 'Versions' }))
        fireEvent.click(await screen.findByRole('link', { name: 'v1' }))

        expect(
            await screen.findByRole('heading', {
                name: 'Lead scoring · v1',
                level: 1,
            }),
        ).toBeInTheDocument()
        expect(router.state.location.pathname).toBe(
            `/projects/${PID}/versions/1`,
        )
        expect(screen.getByText('v1 (latest is v2)')).toBeInTheDocument()
    })

    it('links an execution on the dashboard to the version it ran', async () => {
        stubBackend({
            'GET /executions': () => ok(page([execution('exec_1', 2)])),
            'GET /executions/status-counts': () => ok({ counts: {} }),
            'GET /projects': () => ok(page([])),
            'GET /system/status': pending,
        })
        renderRoute('/dashboard')

        const [link] = await screen.findAllByRole('link', { name: 'v2' })
        expect(link).toHaveAttribute('href', `/projects/${PID}/versions/2`)
    })
})
