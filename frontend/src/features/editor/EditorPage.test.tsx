import {
    act,
    cleanup,
    fireEvent,
    screen,
    waitFor,
    within,
} from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import {
    apiError,
    ok,
    renderRoute,
    stubBackend,
    type Handler,
    type Reply,
} from '../../test/app'
import { AUTOSAVE_DELAY_MS } from './useDraftEditor'

// -- factories (valid by default, wire-shaped like the backend's) ------------

const AT = '2026-10-02T08:00:00Z'
const ID_A = `node_${'a'.repeat(32)}`
const ID_B = `node_${'b'.repeat(32)}`

function project(overrides: object = {}) {
    return {
        id: 'p1',
        name: 'Leads',
        description: 'Qualifies leads',
        status: 'published',
        owner_id: 'user_1',
        revision: 3,
        current_version: 2,
        created_at: AT,
        updated_at: AT,
        draft: {
            graph: {
                nodes: [
                    {
                        id: ID_A,
                        node_type: 'core.input',
                        config: {},
                        label: 'Lead in',
                        position: { x: 0, y: 0 },
                    },
                    {
                        id: ID_B,
                        node_type: 'core.log',
                        config: { level: 'info' },
                        label: '',
                        position: null,
                    },
                ],
                edges: [
                    {
                        source_node_id: ID_A,
                        source_port: 'value',
                        target_node_id: ID_B,
                        target_port: 'value',
                    },
                ],
            },
            triggers: [
                { id: 't1', trigger_type: 'manual', config: {}, enabled: true },
            ],
            dependencies: [],
        },
        files: [],
        ...overrides,
    }
}

const port = (name: string) => ({
    name,
    type: 'json',
    required: false,
    nullable: true,
    multiple: false,
    description: '',
})

function definition(node_type: string, name: string) {
    return {
        node_type,
        version: '1.0.0',
        name,
        description: '',
        category: 'core',
        documentation_url: null,
        inputs: node_type === 'core.input' ? [] : [port('value')],
        outputs: [port('value')],
        config_schema: {},
        capabilities: {},
    }
}

const catalog = ok({
    items: [definition('core.input', 'Input')],
    port_compatibility: { json: ['json', 'json[]'] },
})

interface PutBody {
    name: string
    description: string
    expected_revision: number
    draft: {
        graph: {
            nodes: { id: string; position: { x: number; y: number } | null }[]
            edges: unknown[]
        }
        triggers: unknown[]
    }
}

/** A backend whose PUT enforces revisions like the real one. */
function backend(overrides: Record<string, Handler> = {}, initial = project()) {
    let current = initial
    const puts: PutBody[] = []
    const requests = stubBackend({
        'GET /projects/p1': () => ok(current),
        'GET /nodes': () => catalog,
        'PUT /projects/p1': (_url, init) => {
            const body = JSON.parse(init.body as string) as PutBody
            puts.push(body)
            if (body.expected_revision !== current.revision)
                return apiError(409, 'CONCURRENT_MODIFICATION', 'Changed.')
            current = {
                ...current,
                revision: current.revision + 1,
                draft: body.draft as typeof current.draft,
            }
            return ok(current)
        },
        ...overrides,
    })
    return {
        puts,
        requests,
        bump: () => {
            current = { ...current, revision: current.revision + 1 }
        },
        current: () => current,
        /** Another stored change: like every save, it advances the revision. */
        patch: (fields: Partial<typeof current>) => {
            current = { ...current, ...fields, revision: current.revision + 1 }
        },
    }
}

async function openEditor() {
    const view = renderRoute('/projects/p1/editor')
    await screen.findByRole('heading', {
        level: 1,
        name: 'Draft editor: Leads',
    })
    return view
}

const nodeList = () => within(screen.getByRole('region', { name: /^Nodes/ }))
const canvas = () =>
    within(screen.getByRole('region', { name: 'Graph canvas' }))
const status = () =>
    screen.getByText(
        /^(All changes saved to draft|Saving draft…|Unsaved changes|Changes not saved)$/,
    )
const button = (name: string) => screen.getByRole('button', { name })

/** Moves node A one step right with the keyboard, as React Flow allows. */
async function moveNodeA() {
    const node = await canvas().findByRole('group', { name: /^Lead in/ })
    fireEvent.click(node)
    fireEvent.keyDown(node, { key: 'ArrowRight' })
    await waitFor(() => {
        expect(status()).toHaveTextContent('Unsaved changes')
    })
}

afterEach(() => {
    vi.useRealTimers()
})

// The editor route is code-split: its first render imports React Flow and
// the editor (~0.6 s cold, over 1 s under parallel workers). Load it here so
// that cost is not charged to the first test's 1 s findBy window.
beforeAll(() => import('./EditorPage'))

describe('EditorPage', () => {
    it('loads the draft graph and says it is the draft, not a version', async () => {
        backend()
        await openEditor()

        expect(screen.getByText('Draft', { selector: 'span' })).toBeVisible()
        expect(
            screen.getByText(
                /published versions, the latest being v2, stay unchanged/,
            ),
        ).toBeInTheDocument()
        expect(
            screen.getByText(/Publishing is a separate step/),
        ).toBeInTheDocument()
        expect(status()).toHaveTextContent('All changes saved to draft')

        // Label, catalog name or type, in that order; connections counted.
        const list = nodeList()
        expect(
            list.getByRole('button', { name: /^Lead in/ }),
        ).toHaveTextContent('core.input · 0 in, 1 out')
        expect(
            list.getByRole('button', { name: /^core\.log/ }),
        ).toHaveTextContent('core.log · 1 in, 0 out')
        // Nodes render on the canvas with their ports.
        const lead = await canvas().findByRole('group', { name: /^Lead in/ })
        expect(
            within(lead).getByRole('list', { name: 'Outputs' }),
        ).toHaveTextContent('value')
        // core.log is not in the catalog: said in text, not only by style.
        const log = canvas().getByRole('group', { name: /^core\.log/ })
        expect(within(log).getByText('Unknown node type')).toBeInTheDocument()
        // The edge's ports render even though the type is unknown.
        expect(
            within(log).getByRole('list', { name: 'Inputs' }),
        ).toHaveTextContent('value')
    })

    it('an unpublished project says nothing has been published', async () => {
        backend({
            'GET /projects/p1': () =>
                ok(project({ status: 'draft', current_version: 0 })),
        })
        await openEditor()
        expect(
            screen.getByText(/nothing has been published yet/),
        ).toBeInTheDocument()
    })

    it('selects nodes from the canvas and from the node list', async () => {
        backend()
        await openEditor()
        const inList = nodeList().getByRole('button', { name: /^Lead in/ })
        expect(inList).toHaveAttribute('aria-pressed', 'false')

        fireEvent.click(
            await canvas().findByRole('group', { name: /^Lead in/ }),
        )
        await waitFor(() => {
            expect(inList).toHaveAttribute('aria-pressed', 'true')
        })
        expect(button('Copy')).toBeEnabled()

        fireEvent.click(nodeList().getByRole('button', { name: /^core\.log/ }))
        expect(inList).toHaveAttribute('aria-pressed', 'false')
        expect(
            nodeList().getByRole('button', { name: /^core\.log/ }),
        ).toHaveAttribute('aria-pressed', 'true')
        await waitFor(() => {
            expect(
                canvas().getByRole('group', { name: /^core\.log/ }),
            ).toHaveClass('selected')
        })
        // Selecting is not an edit.
        expect(status()).toHaveTextContent('All changes saved to draft')
    })

    it('zooms with the viewport controls', async () => {
        backend()
        await openEditor()
        const zoom = await screen.findByText(/^Zoom \d+%$/)
        const before = zoom.textContent

        fireEvent.click(screen.getByRole('button', { name: 'Zoom In' }))
        await waitFor(() => {
            expect(zoom.textContent).not.toBe(before)
        })
        expect(screen.getByRole('button', { name: 'Fit View' })).toBeEnabled()
    })

    it('moving a node is an unsaved draft change that undo and redo reverse', async () => {
        backend()
        await openEditor()
        expect(button('Undo')).toBeDisabled()

        await moveNodeA()
        expect(button('Undo')).toBeEnabled()
        expect(button('Save draft')).toBeEnabled()

        fireEvent.click(button('Undo'))
        expect(status()).toHaveTextContent('All changes saved to draft')
        expect(button('Redo')).toBeEnabled()

        fireEvent.click(button('Redo'))
        expect(status()).toHaveTextContent('Unsaved changes')
    })

    it('keyboard shortcuts undo and redo, but not inside text fields', async () => {
        backend()
        await openEditor()
        await moveNodeA()
        const region = screen.getByRole('toolbar', { name: 'Editor' })

        const input = document.createElement('input')
        region.append(input)
        fireEvent.keyDown(input, { key: 'z', ctrlKey: true })
        expect(status()).toHaveTextContent('Unsaved changes')
        input.remove()

        fireEvent.keyDown(region, { key: 'z', ctrlKey: true })
        expect(status()).toHaveTextContent('All changes saved to draft')
        fireEvent.keyDown(region, { key: 'z', metaKey: true, shiftKey: true })
        expect(status()).toHaveTextContent('Unsaved changes')
    })

    it('copy shortcut leaves selected page text to the browser', async () => {
        backend()
        await openEditor()
        fireEvent.click(nodeList().getByRole('button', { name: /^Lead in/ }))
        const toolbar = screen.getByRole('toolbar', { name: 'Editor' })

        window
            .getSelection()
            ?.selectAllChildren(screen.getByRole('heading', { level: 1 }))
        fireEvent.keyDown(toolbar, { key: 'c', ctrlKey: true })
        expect(button('Paste')).toBeDisabled()

        window.getSelection()?.removeAllRanges()
        fireEvent.keyDown(toolbar, { key: 'c', ctrlKey: true })
        expect(button('Paste')).toBeEnabled()
    })

    it('saves the whole draft with positions, pinned to the revision', async () => {
        const server = backend()
        await openEditor()
        await moveNodeA()

        fireEvent.click(button('Save draft'))
        await waitFor(() => {
            expect(status()).toHaveTextContent('All changes saved to draft')
        })
        const [put] = server.puts
        expect(put?.expected_revision).toBe(3)
        expect(put?.name).toBe('Leads')
        expect(put?.description).toBe('Qualifies leads')
        // Untouched parts of the draft go back as loaded.
        expect(put?.draft.triggers).toHaveLength(1)
        // Every node is saved placed, including the never-placed one.
        const positions = put?.draft.graph.nodes.map((n) => n.position)
        expect(positions?.every((p) => p !== null)).toBe(true)
        expect(positions?.[0]?.x).toBeGreaterThan(0)
        // The undo stack survives the save.
        expect(button('Undo')).toBeEnabled()
        expect(button('Save draft')).toBeDisabled()

        // The next save uses the revision the first one returned.
        fireEvent.click(button('Undo'))
        fireEvent.keyDown(screen.getByRole('toolbar', { name: 'Editor' }), {
            key: 's',
            ctrlKey: true,
        })
        await waitFor(() => {
            expect(server.puts).toHaveLength(2)
        })
        expect(server.puts[1]?.expected_revision).toBe(4)
    })

    it('autosaves once after a quiet period', async () => {
        const server = backend()
        await openEditor()
        vi.useFakeTimers({ shouldAdvanceTime: true })
        await moveNodeA()
        expect(server.puts).toHaveLength(0)

        await act(() => vi.advanceTimersByTimeAsync(AUTOSAVE_DELAY_MS))
        await waitFor(() => {
            expect(status()).toHaveTextContent('All changes saved to draft')
        })
        await act(() => vi.advanceTimersByTimeAsync(AUTOSAVE_DELAY_MS * 3))
        expect(server.puts).toHaveLength(1)
    })

    it('a failed save keeps the changes, pauses autosave and can be retried', async () => {
        let fail = true
        const put = vi.fn()
        backend({
            'PUT /projects/p1': () => {
                put()
                return fail
                    ? apiError(500, 'INTERNAL_ERROR', 'Database unavailable.')
                    : ok(project({ revision: 4 }))
            },
        })
        await openEditor()
        vi.useFakeTimers({ shouldAdvanceTime: true })
        await moveNodeA()

        fireEvent.click(button('Save draft'))
        const alert = await screen.findByRole('alert')
        expect(alert).toHaveTextContent('Could not save the draft')
        expect(alert).toHaveTextContent('Autosave is paused')
        expect(status()).toHaveTextContent('Changes not saved')

        await act(() => vi.advanceTimersByTimeAsync(AUTOSAVE_DELAY_MS * 3))
        expect(put).toHaveBeenCalledTimes(1)

        fail = false
        fireEvent.click(within(alert).getByRole('button', { name: 'Retry' }))
        await waitFor(() => {
            expect(status()).toHaveTextContent('All changes saved to draft')
        })
        expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    })

    it('never overwrites a draft changed elsewhere; reload discards local edits', async () => {
        const server = backend()
        await openEditor()
        server.bump() // someone else saved revision 4
        await moveNodeA()

        fireEvent.click(button('Save draft'))
        const alert = await screen.findByRole('alert')
        expect(alert).toHaveTextContent('The draft was changed elsewhere')
        expect(server.puts).toHaveLength(1)

        fireEvent.click(
            within(alert).getByRole('button', {
                name: 'Discard my changes and reload',
            }),
        )
        await waitFor(() => {
            expect(status()).toHaveTextContent('All changes saved to draft')
        })
        expect(screen.queryByRole('alert')).not.toBeInTheDocument()
        expect(button('Undo')).toBeDisabled()
        expect(
            screen.getByText(/editing the draft \(revision 4\)/),
        ).toBeInTheDocument()
    })

    it('asks before leaving with unsaved changes', async () => {
        backend()
        const { router } = await openEditor()
        await moveNodeA()

        fireEvent.click(screen.getByRole('link', { name: 'Back to project' }))
        const dialog = await screen.findByRole('dialog', {
            name: 'Leave with unsaved changes?',
        })
        fireEvent.click(
            within(dialog).getByRole('button', { name: 'Stay in editor' }),
        )
        expect(router.state.location.pathname).toBe('/projects/p1/editor')

        fireEvent.click(screen.getByRole('link', { name: 'Back to project' }))
        fireEvent.click(
            within(
                await screen.findByRole('dialog', {
                    name: 'Leave with unsaved changes?',
                }),
            ).getByRole('button', { name: 'Leave without saving' }),
        )
        await waitFor(() => {
            expect(router.state.location.pathname).toBe('/projects/p1')
        })
    })

    it('leaves without asking when everything is saved', async () => {
        backend()
        const { router } = await openEditor()
        fireEvent.click(screen.getByRole('link', { name: 'Back to project' }))
        await waitFor(() => {
            expect(router.state.location.pathname).toBe('/projects/p1')
        })
    })

    it('copies and pastes nodes as an unsaved change with fresh ids', async () => {
        const server = backend()
        await openEditor()
        fireEvent.click(nodeList().getByRole('button', { name: /^Lead in/ }))
        fireEvent.click(button('Copy'))
        fireEvent.click(button('Paste'))

        expect(
            screen.getByRole('heading', { name: 'Nodes (3)' }),
        ).toBeInTheDocument()
        expect(status()).toHaveTextContent('Unsaved changes')
        fireEvent.click(button('Save draft'))
        await waitFor(() => {
            expect(server.puts).toHaveLength(1)
        })
        const ids = server.puts[0]?.draft.graph.nodes.map((n) => n.id) ?? []
        expect(ids).toHaveLength(3)
        expect(new Set(ids).size).toBe(3)
        for (const id of ids) expect(id).toMatch(/^node_[0-9a-f]{32}$/)
    })

    it('shows the load error with a retry', async () => {
        let reply: Reply = apiError(
            500,
            'INTERNAL_ERROR',
            'Database unavailable.',
        )
        backend({ 'GET /projects/p1': () => reply })
        renderRoute('/projects/p1/editor')

        expect(
            await screen.findByRole('heading', {
                name: 'Could not load the project draft',
            }),
        ).toBeInTheDocument()
        reply = ok(project())
        fireEvent.click(button('Retry'))
        expect(
            await screen.findByRole('heading', { name: 'Draft editor: Leads' }),
        ).toBeInTheDocument()
    })

    it('shows not found for an unknown project', async () => {
        backend({
            'GET /projects/p1': () =>
                apiError(404, 'PROJECT_NOT_FOUND', 'Nope.'),
        })
        renderRoute('/projects/p1/editor')
        expect(
            await screen.findByRole('heading', { name: 'Project not found' }),
        ).toBeInTheDocument()
    })

    it('still edits when the node catalog fails to load', async () => {
        backend({
            // 500: not retried, unlike a transient 503.
            'GET /nodes': () =>
                apiError(500, 'INTERNAL_ERROR', 'Registry down.'),
        })
        await openEditor()
        expect(
            await screen.findByText(/node catalog could not be loaded/),
        ).toBeInTheDocument()
        // Without a catalog nothing is called unknown.
        expect(screen.queryByText('Unknown node type')).not.toBeInTheDocument()
        await moveNodeA()
    })
})

describe('Node library', () => {
    const library = () =>
        within(screen.getByRole('region', { name: 'Node library' }))

    const llm = {
        ...definition('ai.llm', 'LLM'),
        version: '2.1.0',
        description: 'Calls a language model.',
        category: 'ai',
        documentation_url: 'https://docs.example/llm',
        inputs: [{ ...port('prompt'), type: 'string', required: true }],
        outputs: [{ ...port('text'), type: 'string' }],
        config_schema: { type: 'object', required: ['model', 'credential'] },
        capabilities: {
            supports_cancellation: true,
            has_side_effects: false,
            streams_output: true,
        },
    }
    const http = {
        ...definition('web.http', 'HTTP Request'),
        category: 'web',
        description: 'Sends an HTTP request.',
        documentation_url: 'javascript:alert(1)',
        capabilities: { has_side_effects: true },
    }
    const items = [definition('core.input', 'Input'), llm, http]

    async function openLibrary(
        nodes: Handler = () => ok({ items, port_compatibility: {} }),
    ) {
        const server = backend({ 'GET /nodes': nodes })
        await openEditor()
        return server
    }

    it('shows loading, then the registry grouped by category', async () => {
        let release: (r: Reply) => void = () => undefined
        await openLibrary(
            () =>
                new Promise<Reply>((resolve) => {
                    release = resolve
                }),
        )
        expect(
            library().getByText('Loading the node library…'),
        ).toBeInTheDocument()
        act(() => {
            release(ok({ items, port_compatibility: {} }))
        })
        expect(await library().findByText('3 of 3 nodes')).toBeInTheDocument()
        expect(
            library()
                .getAllByRole('heading', { level: 3 })
                .map((h) => h.textContent),
        ).toEqual(['Ai', 'Core', 'Web'])
    })

    it('shows what a node does, its ports, settings, capabilities and version', async () => {
        await openLibrary()
        const item = within(
            (await library().findByRole('heading', { name: 'LLM' })).closest(
                'li',
            ) as HTMLElement,
        )
        expect(item.getByText('ai.llm · v2.1.0')).toBeInTheDocument()
        expect(item.getByText('Calls a language model.')).toBeInTheDocument()
        const caps = within(item.getByRole('list', { name: 'Capabilities' }))
        expect(
            caps.getAllByRole('listitem').map((li) => li.textContent),
        ).toEqual(['Streams output', 'Cancellable'])
        // The preview is the canvas card, hidden from assistive tech
        // because the text lists below say the same.
        const preview = item.getByRole('list', {
            name: 'Outputs',
            hidden: true,
        })
        expect(preview).toHaveTextContent('text')
        expect(preview.closest('[aria-hidden="true"]')).not.toBeNull()
        expect(item.queryByRole('list', { name: 'Outputs' })).toBeNull()
        expect(item.getAllByText('prompt')).toHaveLength(2)
        expect(item.getByText('(string, required)')).toBeInTheDocument()
        expect(item.getByText('model, credential')).toBeInTheDocument()
        expect(
            item.getByRole('link', { name: 'Documentation for LLM' }),
        ).toHaveAttribute('href', 'https://docs.example/llm')
        // A non-web documentation URL from plugin metadata is not linked.
        expect(
            library().queryByRole('link', {
                name: 'Documentation for HTTP Request',
            }),
        ).not.toBeInTheDocument()
    })

    it('searches and filters by category and capability', async () => {
        await openLibrary()
        const search = await library().findByRole('searchbox', {
            name: 'Search nodes',
        })
        fireEvent.change(search, { target: { value: 'language' } })
        expect(library().getByText('1 of 3 nodes')).toBeInTheDocument()
        expect(library().getByRole('heading', { name: 'LLM' })).toBeVisible()

        fireEvent.change(search, { target: { value: '' } })
        fireEvent.change(
            library().getByRole('combobox', { name: 'Category' }),
            {
                target: { value: 'web' },
            },
        )
        expect(library().getByText('1 of 3 nodes')).toBeInTheDocument()
        expect(
            library().getByRole('heading', { name: 'HTTP Request' }),
        ).toBeInTheDocument()

        fireEvent.change(
            library().getByRole('combobox', { name: 'Capability' }),
            { target: { value: 'streams_output' } },
        )
        expect(library().getByText('0 of 3 nodes')).toBeInTheDocument()
        expect(
            library().getByRole('heading', { name: 'No matching nodes' }),
        ).toBeInTheDocument()
    })

    it('says when the registry has no nodes', async () => {
        await openLibrary(() => ok({ items: [], port_compatibility: {} }))
        expect(
            await library().findByRole('heading', {
                name: 'No nodes registered',
            }),
        ).toBeInTheDocument()
    })

    it('shows an API error with retry', async () => {
        let fail = true
        await openLibrary(() =>
            fail
                ? apiError(500, 'INTERNAL_ERROR', 'Registry down.')
                : ok({ items, port_compatibility: {} }),
        )
        expect(
            await library().findByRole('heading', {
                name: 'Could not load the node library',
            }),
        ).toBeInTheDocument()
        expect(library().getByText('Registry down.')).toBeInTheDocument()
        fail = false
        fireEvent.click(library().getByRole('button', { name: 'Retry' }))
        expect(await library().findByText('3 of 3 nodes')).toBeInTheDocument()
    })

    it('adds nodes to the draft with fresh ids, selected, without stacking', async () => {
        vi.useFakeTimers({ shouldAdvanceTime: true })
        const server = await openLibrary()
        const add = await library().findByRole('button', {
            name: 'Add LLM to graph',
        })
        fireEvent.click(add)
        fireEvent.click(add)
        expect(status()).toHaveTextContent('Unsaved changes')
        const listed = nodeList().getAllByRole('button', { name: /^LLM/ })
        expect(listed).toHaveLength(2)
        // The last added node is the selection.
        expect(listed[1]).toHaveAttribute('aria-pressed', 'true')

        await act(async () => {
            await vi.advanceTimersByTimeAsync(AUTOSAVE_DELAY_MS)
        })
        await waitFor(() => {
            expect(server.puts).toHaveLength(1)
        })
        const nodes = server.puts[0]?.draft.graph.nodes ?? []
        const added = nodes.slice(2)
        expect(new Set(nodes.map((n) => n.id)).size).toBe(4)
        expect(added.map((n) => n.id)).toEqual([
            expect.stringMatching(/^node_[0-9a-f]{32}$/),
            expect.stringMatching(/^node_[0-9a-f]{32}$/),
        ])
        expect(added[0]?.position).not.toEqual(added[1]?.position)
    })

    it('adds a node dropped on the canvas and refuses unregistered types', async () => {
        await openLibrary()
        await library().findByText('3 of 3 nodes')
        const pane = screen.getByTestId('rf__wrapper')
        const drop = (nodeType: string) => {
            const dataTransfer = {
                types: ['application/x-akasha-node-type'],
                getData: () => nodeType,
                dropEffect: 'none',
            }
            fireEvent.dragOver(pane, { dataTransfer })
            fireEvent.drop(pane, { dataTransfer, clientX: 50, clientY: 60 })
        }
        drop('web.http')
        expect(
            nodeList().getByRole('button', { name: /^HTTP Request/ }),
        ).toBeInTheDocument()
        drop('plugin.removed')
        expect(await screen.findByText('Node not added')).toBeInTheDocument()
        expect(nodeList().getAllByRole('listitem')).toHaveLength(3)
    })
})

describe('Graph editing', () => {
    const SRC = `node_${'c'.repeat(32)}`
    const SINK = `node_${'d'.repeat(32)}`
    const typed = (
        name: string,
        type: string,
        extra: Partial<ReturnType<typeof port>> = {},
    ) => ({ ...port(name), type, nullable: false, ...extra })
    const nodes = ok({
        items: [
            {
                ...definition('t.text', 'Text'),
                inputs: [],
                outputs: [
                    typed('text', 'string'),
                    typed('lines', 'string', { multiple: true }),
                ],
            },
            {
                ...definition('t.sink', 'Sink'),
                inputs: [
                    typed('n', 'number', { required: true }),
                    typed('s', 'string'),
                    typed('all', 'string', { multiple: true }),
                ],
                outputs: [],
            },
        ],
        // An excerpt of the backend's table.
        port_compatibility: {
            string: ['string', 'string[]', 'json'],
            'string[]': ['string[]', 'json'],
        },
    })
    const wire = (source_port: string, target_port: string) => ({
        source_node_id: SRC,
        source_port,
        target_node_id: SINK,
        target_port,
    })

    function graphBackend(edges: ReturnType<typeof wire>[] = []) {
        const base = project()
        return backend(
            { 'GET /nodes': () => nodes },
            {
                ...base,
                draft: {
                    ...base.draft,
                    graph: {
                        nodes: [
                            {
                                id: SRC,
                                node_type: 't.text',
                                config: {},
                                label: 'Source',
                                position: { x: 0, y: 0 },
                            },
                            {
                                id: SINK,
                                node_type: 't.sink',
                                config: {},
                                label: 'Target',
                                position: { x: 400, y: 0 },
                            },
                        ],
                        edges,
                    },
                },
            },
        )
    }

    const nodeOnCanvas = (name: RegExp) =>
        canvas().findByRole('group', { name })
    const connections = () =>
        within(screen.getByRole('region', { name: /^Connections:/ }))
    const edgeOnCanvas = (name: RegExp) =>
        canvas().findByRole('group', { name })
    const save = async () => {
        fireEvent.click(button('Save draft'))
        await waitFor(() => {
            expect(status()).toHaveTextContent('All changes saved to draft')
        })
    }

    it('connects from the panel, offering inputs with their fit, and saves the edge', async () => {
        const server = graphBackend()
        await openEditor()
        fireEvent.click(await nodeOnCanvas(/^Source/))

        const panel = connections()
        const to = panel.getByRole('combobox', { name: 'To input' })
        const option = (name: RegExp) =>
            within(to).getByRole<HTMLOptionElement>('option', { name })
        expect(option(/^Target › n \(incompatible type\)$/).disabled).toBe(true)
        expect(option(/^Target › s$/).disabled).toBe(false)
        expect(panel.getByRole('button', { name: 'Connect' })).toBeDisabled()

        fireEvent.change(to, {
            target: { value: option(/^Target › s$/).value },
        })
        fireEvent.click(panel.getByRole('button', { name: 'Connect' }))

        expect(
            await edgeOnCanvas(/^Connection from Source text to Target s$/),
        ).toBeInTheDocument()
        // The same edge again is offered, but not choosable: a duplicate.
        expect(option(/^Target › s \(already connected\)$/).disabled).toBe(true)
        expect(nodeList().getByText(/0 in, 1 out/)).toBeInTheDocument()

        await save()
        expect(server.puts[0]?.draft.graph.edges).toEqual([wire('text', 's')])
    })

    it('switching nodes shows one settings and one connections panel', async () => {
        graphBackend()
        await openEditor()
        fireEvent.click(await nodeOnCanvas(/^Source/))
        fireEvent.click(await nodeOnCanvas(/^Target/))
        fireEvent.click(await nodeOnCanvas(/^Source/))
        expect(
            screen.getAllByRole('region', { name: /^(Settings|Connections):/ }),
        ).toHaveLength(2)
        expect(
            connections().getByRole('heading', { level: 2 }),
        ).toHaveTextContent('Connections: Source')
    })

    it('a list output only fits list inputs', async () => {
        graphBackend()
        await openEditor()
        fireEvent.click(await nodeOnCanvas(/^Source/))
        const panel = connections()
        fireEvent.change(panel.getByRole('combobox', { name: 'From output' }), {
            target: { value: 'lines' },
        })
        const to = panel.getByRole('combobox', { name: 'To input' })
        expect(
            within(to).getByRole<HTMLOptionElement>('option', {
                name: 'Target › s (incompatible type)',
            }).disabled,
        ).toBe(true)
        expect(
            within(to).getByRole<HTMLOptionElement>('option', {
                name: 'Target › all',
            }).disabled,
        ).toBe(false)
    })

    it('shows required inputs that are not connected, on the card and in the panel', async () => {
        graphBackend()
        await openEditor()
        const sink = await nodeOnCanvas(/^Target/)
        expect(
            within(sink).getByText('Missing required input: n'),
        ).toBeVisible()
        fireEvent.click(sink)
        expect(
            connections().getByText(
                'Missing required input: n. Publishing would be rejected.',
            ),
        ).toBeInTheDocument()
    })

    it('labels a stored edge the contract does not allow, in text', async () => {
        graphBackend([wire('text', 'n')])
        await openEditor()
        expect(
            await edgeOnCanvas(
                /^Connection from Source text to Target n, invalid: Output "text" \(string\) cannot feed input "n" \(number\)\.$/,
            ),
        ).toBeInTheDocument()
        expect(canvas().getByText('incompatible type')).toBeInTheDocument()
    })

    it('removes a connection from the panel; undo and redo replay it', async () => {
        graphBackend([wire('text', 's')])
        await openEditor()
        fireEvent.click(await nodeOnCanvas(/^Source/))
        fireEvent.click(
            connections().getByRole('button', {
                name: 'Remove connection Source text to Target s',
            }),
        )
        await waitFor(() => {
            expect(
                canvas().queryByRole('group', { name: /^Connection/ }),
            ).not.toBeInTheDocument()
        })
        expect(status()).toHaveTextContent('Unsaved changes')
        fireEvent.click(button('Undo'))
        expect(
            await edgeOnCanvas(/^Connection from Source/),
        ).toBeInTheDocument()
        expect(status()).toHaveTextContent('All changes saved to draft')
        fireEvent.click(button('Redo'))
        await waitFor(() => {
            expect(
                canvas().queryByRole('group', { name: /^Connection/ }),
            ).not.toBeInTheDocument()
        })
    })

    it('Delete on the canvas removes the selected node with its edges; undo restores both', async () => {
        const server = graphBackend([wire('text', 's'), wire('text', 'all')])
        await openEditor()
        const source = await nodeOnCanvas(/^Source/)
        fireEvent.click(source)
        // Outside the canvas, Delete and Backspace keep their usual meaning.
        fireEvent.keyDown(button('Undo'), { key: 'Delete' })
        expect(nodeList().getAllByRole('button')).toHaveLength(2)

        fireEvent.keyDown(source, { key: 'Delete' })
        await waitFor(() => {
            expect(nodeList().getAllByRole('button')).toHaveLength(1)
        })
        expect(
            canvas().queryByRole('group', { name: /^Connection/ }),
        ).not.toBeInTheDocument()
        expect(
            screen.queryByRole('region', { name: /^Connections:/ }),
        ).toBeNull()

        fireEvent.click(button('Undo'))
        expect(
            await canvas().findAllByRole('group', { name: /^Connection/ }),
        ).toHaveLength(2)

        fireEvent.click(button('Redo'))
        await save()
        expect(server.puts[0]?.draft.graph).toMatchObject({
            nodes: [{ id: SINK }],
            edges: [],
        })
    })

    it('selects an edge on the canvas and deletes it with the toolbar', async () => {
        graphBackend([wire('text', 's')])
        await openEditor()
        expect(button('Delete')).toBeDisabled()
        fireEvent.click(await edgeOnCanvas(/^Connection from Source/))
        await waitFor(() => {
            expect(button('Delete')).toBeEnabled()
        })
        fireEvent.click(button('Delete'))
        await waitFor(() => {
            expect(
                canvas().queryByRole('group', { name: /^Connection/ }),
            ).not.toBeInTheDocument()
        })
        // Both nodes stay.
        expect(nodeList().getAllByRole('button')).toHaveLength(2)
    })

    it('select all, duplicate and escape from the keyboard', async () => {
        const server = graphBackend([wire('text', 's')])
        await openEditor()
        const source = await nodeOnCanvas(/^Source/)
        fireEvent.keyDown(source, { key: 'a', ctrlKey: true })
        await waitFor(() => {
            expect(
                nodeList()
                    .getAllByRole('button')
                    .every((b) => b.getAttribute('aria-pressed') === 'true'),
            ).toBe(true)
        })

        fireEvent.keyDown(source, { key: 'd', metaKey: true })
        await waitFor(() => {
            expect(nodeList().getAllByRole('button')).toHaveLength(4)
        })
        // The copies are the selection, with their own edge.
        expect(
            nodeList()
                .getAllByRole('button', { pressed: true })
                .map((b) => b.textContent),
        ).toEqual(['Sourcet.text · 0 in, 1 out', 'Targett.sink · 1 in, 0 out'])

        fireEvent.keyDown(source, { key: 'Escape' })
        await waitFor(() => {
            expect(
                nodeList().queryAllByRole('button', { pressed: true }),
            ).toHaveLength(0)
        })
        expect(button('Duplicate')).toBeDisabled()

        await save()
        const saved = server.puts[0]?.draft.graph
        expect(saved?.nodes).toHaveLength(4)
        expect(saved?.edges).toHaveLength(2)
    })

    it('a saved graph comes back the same after reopening the editor', async () => {
        graphBackend()
        await openEditor()
        fireEvent.click(await nodeOnCanvas(/^Source/))
        const to = connections().getByRole('combobox', { name: 'To input' })
        fireEvent.change(to, {
            target: {
                value: within(to).getByRole<HTMLOptionElement>('option', {
                    name: 'Target › all',
                }).value,
            },
        })
        fireEvent.click(connections().getByRole('button', { name: 'Connect' }))
        await save()

        cleanup()
        await openEditor()
        expect(
            await edgeOnCanvas(/^Connection from Source text to Target all$/),
        ).toBeInTheDocument()
        expect(status()).toHaveTextContent('All changes saved to draft')
        expect(
            screen.getByText(/editing the draft \(revision 4\)/),
        ).toBeInTheDocument()
    })
})

// -- validate and publish ------------------------------------------------------

interface PinBody {
    expected_revision?: number
}

const issue = (overrides: object) => ({
    code: 'invalid_config',
    message: 'invalid configuration',
    node_id: null,
    port: null,
    edge: null,
    trigger_id: null,
    ...overrides,
})

/** One error of each kind the backend reports. */
const ISSUES = [
    issue({
        code: 'missing_required_input',
        message: 'required input "value" is not connected',
        node_id: ID_B,
        port: 'value',
    }),
    issue({ node_id: ID_B, message: 'invalid configuration: level' }),
    issue({
        code: 'duplicate_edge',
        message: 'duplicate edge',
        edge: {
            source_node_id: ID_A,
            source_port: 'value',
            target_node_id: ID_B,
            target_port: 'value',
        },
    }),
    issue({
        code: 'invalid_trigger_schedule',
        message: 'bad schedule',
        trigger_id: 't1',
    }),
    issue({ code: 'missing_dependency', message: 'plugin x is missing' }),
]

const VERSION = {
    id: 'v3',
    version: 3,
    schema_version: 1,
    description: 'Qualifies leads',
    published_at: AT,
    published_by: null,
    graph: project().draft.graph,
    triggers: [],
    dependencies: [],
    files: [],
    plugin_versions: {},
}

/** A backend whose validate/publish enforce the pinned revision like the
 * real one; `publish` and `validate` replace their success answers. */
function publishBackend({
    validate = () => ok({ valid: true, issues: [] }),
    publish,
}: { validate?: () => Reply; publish?: () => Reply } = {}) {
    const pins: { path: string; body: PinBody }[] = []
    const pinned =
        (path: string, answer: () => Reply): Handler =>
        (_url, init) => {
            const body = JSON.parse(
                (init.body as string | undefined) ?? '{}',
            ) as PinBody
            pins.push({ path, body })
            if (body.expected_revision !== server.current().revision)
                return apiError(409, 'CONCURRENT_MODIFICATION', 'Changed.')
            return answer()
        }
    const server = backend({
        'POST /projects/p1/validate': pinned('validate', validate),
        'POST /projects/p1/publish': pinned(
            'publish',
            publish ??
                (() => {
                    // Like the backend: saving the project advances the revision.
                    server.patch({
                        current_version: server.current().current_version + 1,
                    })
                    return { status: 201, body: VERSION }
                }),
        ),
    })
    return { ...server, pins }
}

const panel = () =>
    within(screen.getByRole('region', { name: 'Validate and publish' }))

async function validate() {
    fireEvent.click(panel().getByRole('button', { name: 'Validate' }))
    await waitFor(() => {
        expect(
            panel().getByRole('button', { name: 'Validate' }),
        ).not.toHaveAttribute('aria-busy', 'true')
    })
}

describe('Validate and publish', () => {
    it('publishes a valid draft only after validation and confirmation', async () => {
        const server = publishBackend()
        await openEditor()

        expect(
            panel().getByText('Revision 3 has not been validated yet.'),
        ).toBeInTheDocument()
        expect(panel().getByRole('button', { name: 'Publish…' })).toBeDisabled()

        await validate()
        expect(
            await panel().findByText(
                'The backend accepted revision 3. It can be published as version 3.',
            ),
        ).toBeInTheDocument()
        expect(panel().getByText('Valid')).toBeInTheDocument()

        fireEvent.click(panel().getByRole('button', { name: 'Publish…' }))
        const dialog = await screen.findByRole('dialog', {
            name: 'Publish version 3?',
        })
        expect(dialog).toHaveTextContent('immutable')
        expect(dialog).toHaveTextContent('This cannot be undone.')
        // Nothing is published until the user confirms.
        expect(server.pins.map((p) => p.path)).toEqual(['validate'])

        fireEvent.click(
            within(dialog).getByRole('button', { name: 'Publish version 3' }),
        )
        expect(
            await screen.findByText('Version 3 is published'),
        ).toBeInTheDocument()
        expect(server.pins).toEqual([
            { path: 'validate', body: { expected_revision: 3 } },
            { path: 'publish', body: { expected_revision: 3 } },
        ])
        // The editor continues from the server's draft, whose revision the
        // publish advanced, so the next save is not a conflict.
        await waitFor(() => {
            expect(
                screen.getByText(/editing the draft \(revision 4\)/),
            ).toBeInTheDocument()
        })
        expect(
            panel().getByText('Revision 4 has not been validated yet.'),
        ).toBeInTheDocument()
        await moveNodeA()
        fireEvent.click(button('Save draft'))
        await waitFor(() => {
            expect(status()).toHaveTextContent('All changes saved to draft')
        })
        expect(server.puts.at(-1)?.expected_revision).toBe(4)
    })

    it('shows every backend error, grouped, on its node, and navigates to it', async () => {
        publishBackend({ validate: () => ok({ valid: false, issues: ISSUES }) })
        await openEditor()
        await validate()

        expect(await panel().findByText('5 errors')).toBeInTheDocument()
        expect(
            panel().getByText(
                'Nodes: 2 · Connections: 1 · Triggers: 1 · Project: 1',
            ),
        ).toBeInTheDocument()
        expect(panel().getByRole('button', { name: 'Publish…' })).toBeDisabled()
        for (const { message } of ISSUES)
            expect(panel().getByText(message)).toBeInTheDocument()

        // Node and port are named; the node carries a marker on the canvas
        // and in the node list.
        const nodeErrors = panel().getByRole('list', { name: 'Nodes errors' })
        expect(nodeErrors).toHaveTextContent('Node "core.log" → port "value"')
        expect(
            canvas().getByRole('group', { name: /^core\.log/ }),
        ).toHaveTextContent('2 validation errors')
        expect(
            nodeList().getByRole('button', { name: /^core\.log/ }),
        ).toHaveTextContent('2 validation errors')

        fireEvent.click(
            within(nodeErrors).getByRole('button', { name: /port "value"/ }),
        )
        expect(
            nodeList().getByRole('button', { name: /^core\.log/ }),
        ).toHaveAttribute('aria-pressed', 'true')

        // An issue located only at an edge selects that edge.
        const connectionErrors = panel().getByRole('list', {
            name: 'Connections errors',
        })
        fireEvent.click(within(connectionErrors).getByRole('button'))
        await waitFor(() => {
            expect(
                canvas().getByRole('group', {
                    name: /^Connection from Lead in/,
                }),
            ).toHaveClass('selected')
        })
        expect(
            nodeList().getByRole('button', { name: /^core\.log/ }),
        ).toHaveAttribute('aria-pressed', 'false')

        // Not in the editor: shown, but not a navigation target.
        const triggerErrors = panel().getByRole('list', {
            name: 'Triggers errors',
        })
        expect(triggerErrors).toHaveTextContent('trigger "t1"')
        expect(within(triggerErrors).queryByRole('button')).toBeNull()
    })

    it('marks results outdated by unsaved changes and validates the saved draft only', async () => {
        vi.useFakeTimers({ shouldAdvanceTime: true })
        const server = publishBackend()
        await openEditor()
        await validate()
        await panel().findByText('Valid')

        await moveNodeA()
        expect(panel().getByText('Outdated')).toBeInTheDocument()
        expect(panel().getByText(/^Save the draft first/)).toBeInTheDocument()
        expect(panel().getByRole('button', { name: 'Validate' })).toBeDisabled()
        expect(panel().getByRole('button', { name: 'Publish…' })).toBeDisabled()

        // Autosave stores revision 4; the old result stays outdated.
        await act(() => vi.advanceTimersByTimeAsync(AUTOSAVE_DELAY_MS))
        await waitFor(() => {
            expect(status()).toHaveTextContent('All changes saved to draft')
        })
        expect(
            panel().getByText(
                'These results are for revision 3; the draft changed since. Validate again.',
            ),
        ).toBeInTheDocument()
        expect(panel().getByRole('button', { name: 'Publish…' })).toBeDisabled()
        expect(server.pins).toHaveLength(1)
    })

    it('refuses to validate or publish a draft changed elsewhere', async () => {
        const server = publishBackend()
        await openEditor()
        server.bump()

        await validate()
        const alert = await screen.findByRole('alert')
        expect(alert).toHaveTextContent('The draft was changed elsewhere')
        expect(panel().getByRole('button', { name: 'Validate' })).toBeDisabled()
        expect(panel().getByRole('button', { name: 'Publish…' })).toBeDisabled()

        fireEvent.click(
            within(alert).getByRole('button', { name: 'Reload the draft' }),
        )
        await waitFor(() => {
            expect(
                screen.getByText(/editing the draft \(revision 4\)/),
            ).toBeInTheDocument()
        })
        expect(screen.queryByRole('alert')).not.toBeInTheDocument()
        expect(panel().getByRole('button', { name: 'Validate' })).toBeEnabled()
    })

    it('a draft changed between validate and publish is not published', async () => {
        const server = publishBackend()
        await openEditor()
        await validate()
        await panel().findByText('Valid')
        server.bump()

        fireEvent.click(panel().getByRole('button', { name: 'Publish…' }))
        const dialog = await screen.findByRole('dialog')
        fireEvent.click(
            within(dialog).getByRole('button', { name: 'Publish version 3' }),
        )
        expect(await screen.findByRole('alert')).toHaveTextContent(
            'The draft was changed elsewhere',
        )
        expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
        expect(screen.queryByText(/is published/)).not.toBeInTheDocument()
    })

    it('shows the issues of a publish the backend refused', async () => {
        publishBackend({
            publish: () => ({
                status: 422,
                body: {
                    code: 'PROJECT_VALIDATION_FAILED',
                    message: 'The project draft contains validation errors.',
                    details: { issues: [ISSUES[0]] },
                    request_id: 'r',
                },
            }),
        })
        await openEditor()
        await validate()
        await panel().findByText('Valid')

        fireEvent.click(panel().getByRole('button', { name: 'Publish…' }))
        const dialog = await screen.findByRole('dialog')
        fireEvent.click(
            within(dialog).getByRole('button', { name: 'Publish version 3' }),
        )
        expect(await panel().findByText('1 error')).toBeInTheDocument()
        expect(panel().getByText(ISSUES[0]?.message ?? '')).toBeInTheDocument()
        expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
        expect(panel().getByRole('button', { name: 'Publish…' })).toBeDisabled()
    })

    it('keeps the dialog open with the reason when publishing fails otherwise', async () => {
        publishBackend({
            publish: () =>
                apiError(500, 'INTERNAL_ERROR', 'An internal error occurred.'),
        })
        await openEditor()
        await validate()
        await panel().findByText('Valid')

        fireEvent.click(panel().getByRole('button', { name: 'Publish…' }))
        const dialog = await screen.findByRole('dialog')
        fireEvent.click(
            within(dialog).getByRole('button', { name: 'Publish version 3' }),
        )
        expect(
            await within(dialog).findByText('An internal error occurred.'),
        ).toBeInTheDocument()
        expect(screen.queryByText(/is published/)).not.toBeInTheDocument()
        expect(
            screen.getByText(/editing the draft \(revision 3\)/),
        ).toBeInTheDocument()
    })
})
