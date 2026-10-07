import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { beforeAll, describe, expect, it } from 'vitest'
import type { JsonObject } from '../../api/schemas'
import {
    apiError,
    ok,
    renderRoute,
    stubBackend,
    type Handler,
} from '../../test/app'

// The inspector through the real editor route: catalog, validation and
// save go through the ApiClient to a stub backend (AGENTS §20).

const AT = '2026-10-02T08:00:00Z'
const NODE = `node_${'a'.repeat(32)}`

/** A plugin-style node no form was written for: every supported kind. */
const RICH_SCHEMA = {
    type: 'object',
    properties: {
        url: { type: 'string', description: 'Where requests go.' },
        method: { type: 'string', enum: ['GET', 'POST'], default: 'GET' },
        retries: { type: ['integer', 'null'], minimum: 0 },
        verbose: { type: 'boolean', default: true },
        prompt: { type: 'string', format: 'template' },
        credential: { type: 'string', format: 'credential-reference' },
        options: {
            type: 'object',
            properties: { depth: { type: 'integer' } },
        },
        tags: { type: 'array', items: { type: 'string' }, maxItems: 2 },
        headers: { type: 'object', additionalProperties: { type: 'string' } },
        payload: {},
    },
    required: ['url'],
    additionalProperties: false,
}

function definition(nodeType: string, schema: object) {
    return {
        node_type: nodeType,
        version: '1.2.0',
        name: 'Rich',
        description: 'Does many things.',
        category: 'test',
        documentation_url: null,
        inputs: [],
        outputs: [],
        config_schema: schema,
        capabilities: {},
    }
}

function workflow(config: JsonObject, nodeType = 'acme.rich') {
    return {
        id: 'p1',
        name: 'Leads',
        description: '',
        status: 'draft',
        owner_id: 'user_1',
        revision: 1,
        current_version: 0,
        created_at: AT,
        updated_at: AT,
        draft: {
            graph: {
                nodes: [
                    {
                        id: NODE,
                        node_type: nodeType,
                        config,
                        label: 'Rich one',
                        position: { x: 0, y: 0 },
                    },
                ],
                edges: [],
            },
            triggers: [],
            dependencies: [],
        },
        files: [],
    }
}

interface Saved {
    draft: { graph: { nodes: { config: JsonObject }[] } }
}

function backend({
    config = {},
    nodeType = 'acme.rich',
    schema = RICH_SCHEMA,
    handlers = {},
}: {
    config?: JsonObject
    nodeType?: string
    schema?: object
    /** Replace single routes of the default backend. */
    handlers?: Record<string, Handler>
} = {}) {
    const saved: JsonObject[] = []
    const validated: JsonObject[] = []
    const requests = stubBackend({
        'GET /workflows/p1': () => ok(workflow(config, nodeType)),
        'GET /nodes': () =>
            ok({
                items: [definition('acme.rich', schema)],
                port_compatibility: {},
            }),
        'GET /credential-names': () =>
            ok({ items: [{ name: 'crm-api', kind: 'api_key' }], page: page() }),
        'POST /nodes/acme.rich/config/validate': (_url, init) => {
            const body = JSON.parse(init.body as string) as {
                config: JsonObject
            }
            validated.push(body.config)
            return ok({ valid: true, issues: [] })
        },
        'PUT /workflows/p1': (_url, init) => {
            const body = JSON.parse(init.body as string) as Saved
            saved.push(body.draft.graph.nodes[0]?.config ?? {})
            return ok({ ...workflow(config, nodeType), revision: 2, ...body })
        },
        ...handlers,
    })
    return { saved, validated, requests }
}

const page = () => ({ offset: 0, limit: 100, has_more: false })

async function openInspector() {
    const view = renderRoute('/workflows/p1/editor')
    const list = await screen.findByRole('region', { name: /^Nodes/ })
    fireEvent.click(within(list).getByRole('button', { name: /Rich one/ }))
    const dialog = await screen.findByRole('dialog', { name: 'Rich one' })
    const inspector = within(dialog).getByRole('region', { name: 'Settings' })
    return { ...view, dialog: within(dialog), inspector: within(inspector) }
}

const type = (field: HTMLElement, value: string) => {
    fireEvent.change(field, { target: { value } })
}

async function saveDraft() {
    fireEvent.click(screen.getByRole('button', { name: 'Save draft' }))
    await waitFor(() => {
        expect(
            screen.getByText('All changes saved to draft'),
        ).toBeInTheDocument()
    })
}

// The editor route is code-split: its first render imports React Flow and
// the editor (~0.6 s cold, over 1 s under parallel workers). Load it here so
// that cost is not charged to the first test's 1 s findBy window.
beforeAll(() => import('./EditorPage'))

describe('NodeInspector', () => {
    it('renders every field kind from the node schema alone', async () => {
        backend()
        const { dialog, inspector } = await openInspector()

        expect(dialog.getByText('acme.rich')).toBeInTheDocument()
        expect(dialog.getByText('1.2.0')).toBeInTheDocument()
        expect(dialog.getByText('Does many things.')).toBeInTheDocument()
        const url = inspector.getByRole('textbox', { name: /^Url/ })
        expect(url).toBeRequired()
        expect(url).toHaveAccessibleDescription('Where requests go.')
        expect(
            inspector.getByRole('combobox', { name: /^Method/ }),
        ).toHaveDisplayValue('Default (GET)')
        expect(
            inspector.getByRole('textbox', { name: /^Retries$/ }),
        ).toHaveAttribute('inputmode', 'numeric')
        expect(
            inspector.getByRole('checkbox', {
                name: 'Retries: no value (null)',
            }),
        ).not.toBeChecked()
        expect(
            inspector.getByRole('checkbox', { name: 'Verbose' }),
        ).toBeChecked()
        expect(
            inspector.getByRole('textbox', { name: /^Prompt/ }).tagName,
        ).toBe('TEXTAREA')
        expect(
            await inspector.findByRole('option', { name: 'crm-api (API key)' }),
        ).toBeInTheDocument()
        expect(
            inspector.getByRole('combobox', { name: /^Credential/ }),
        ).toHaveAccessibleDescription(/secret is never shown here/)
        const options = inspector.getByRole('group', { name: 'Options' })
        expect(
            within(options).getByRole('textbox', { name: /^Depth/ }),
        ).toBeInTheDocument()
        const tags = inspector.getByRole('group', { name: 'Tags' })
        expect(within(tags).getByText('No items.')).toBeInTheDocument()
        expect(
            inspector.getByRole('group', { name: 'Headers' }),
        ).toBeInTheDocument()
        expect(
            inspector.getByRole('textbox', { name: /^Payload/ }),
        ).toHaveAccessibleDescription(/Any JSON value/)
        // Required: marked visibly (aria-hidden *) and for assistive tech.
        expect(
            inspector.getByRole('textbox', { name: /^Prompt/ }),
        ).not.toBeRequired()
    })

    it('edits nested settings into the saved draft config', async () => {
        const { saved } = backend()
        const { inspector } = await openInspector()

        type(inspector.getByRole('textbox', { name: /^Url/ }), 'https://x.test')
        fireEvent.change(inspector.getByRole('combobox', { name: /^Method/ }), {
            target: { value: '"POST"' },
        })
        type(inspector.getByRole('textbox', { name: /^Retries$/ }), '3')
        fireEvent.click(inspector.getByRole('checkbox', { name: 'Verbose' }))
        type(inspector.getByRole('textbox', { name: /^Depth/ }), '2')
        const tags = within(inspector.getByRole('group', { name: 'Tags' }))
        fireEvent.click(tags.getByRole('button', { name: 'Add item' }))
        type(tags.getByRole('textbox', { name: 'Tags item 1' }), 'vip')
        fireEvent.click(tags.getByRole('button', { name: 'Add item' }))
        // maxItems 2: the schema's limit is offered, the backend enforces it.
        expect(tags.getByRole('button', { name: 'Add item' })).toBeDisabled()
        fireEvent.click(
            tags.getByRole('button', { name: 'Remove Tags item 2' }),
        )
        const headers = within(
            inspector.getByRole('group', { name: 'Headers' }),
        )
        type(
            headers.getByRole('textbox', { name: 'New Headers entry' }),
            'Accept',
        )
        fireEvent.click(headers.getByRole('button', { name: 'Add entry' }))
        type(headers.getByRole('textbox', { name: 'Accept' }), 'text/plain')
        type(
            inspector.getByRole('textbox', { name: /^Payload/ }),
            '{"a": [1, null]}',
        )
        await saveDraft()

        expect(saved.at(-1)).toEqual({
            url: 'https://x.test',
            method: 'POST',
            retries: 3,
            verbose: false,
            options: { depth: 2 },
            tags: ['vip'],
            headers: { Accept: 'text/plain' },
            payload: { a: [1, null] },
        })
    })

    it('shows defaults without writing them, and resets to them', async () => {
        const { saved } = backend({ config: { url: 'https://x.test' } })
        const { inspector } = await openInspector()

        const method = inspector.getByRole('combobox', { name: /^Method/ })
        expect(method).toHaveAccessibleDescription('Default: GET.')
        fireEvent.change(method, { target: { value: '"POST"' } })
        expect(method).toHaveDisplayValue('POST')
        fireEvent.click(inspector.getByRole('button', { name: 'Use default' }))
        expect(method).toHaveDisplayValue('Default (GET)')
        expect(
            inspector.queryByRole('button', { name: 'Use default' }),
        ).not.toBeInTheDocument()
        // Cleared optional fields are removed, not stored empty.
        const prompt = inspector.getByRole('textbox', { name: /^Prompt/ })
        type(prompt, 'Hi')
        type(prompt, '')
        fireEvent.click(inspector.getByRole('checkbox', { name: 'Verbose' }))
        await saveDraft()
        expect(saved.at(-1)).toEqual({ url: 'https://x.test', verbose: false })
    })

    it('sets and clears null for nullable settings', async () => {
        const { saved } = backend({ config: { url: 'u', retries: 2 } })
        const { inspector } = await openInspector()
        const nullBox = inspector.getByRole('checkbox', {
            name: 'Retries: no value (null)',
        })

        fireEvent.click(nullBox)
        expect(
            inspector.queryByRole('textbox', { name: /^Retries$/ }),
        ).not.toBeInTheDocument()
        await saveDraft()
        expect(saved.at(-1)).toEqual({ url: 'u', retries: null })

        fireEvent.click(nullBox)
        expect(
            inspector.getByRole('textbox', { name: /^Retries$/ }),
        ).toHaveValue('')
    })

    it('shows the backend verdict at each field and as a list', async () => {
        backend({
            config: { options: { depth: 1.5 }, typo: 1 },
            handlers: {
                'POST /nodes/acme.rich/config/validate': () =>
                    ok({
                        valid: false,
                        issues: [
                            { path: ['url'], message: 'is required' },
                            {
                                path: ['options', 'depth'],
                                message: 'must be integer',
                            },
                            { path: ['typo'], message: 'is not allowed' },
                        ],
                    }),
            },
        })
        const { inspector } = await openInspector()

        expect(
            await inspector.findByText(
                '3 settings need attention before the workflow can be published.',
            ),
        ).toBeInTheDocument()
        const url = inspector.getByRole('textbox', { name: /^Url/ })
        expect(url).toBeInvalid()
        expect(url).toHaveAccessibleDescription(/is required/)
        expect(
            inspector.getByRole('textbox', { name: /^Depth/ }),
        ).toHaveAccessibleDescription(/must be integer/)
        // Keys the schema does not know stay visible and removable.
        expect(
            inspector.getByRole('textbox', {
                name: "typo (not in this node's settings)",
            }),
        ).toHaveAccessibleDescription(/is not allowed/)
        const problems = inspector.getByRole('list', {
            name: 'Configuration problems',
        })
        expect(problems).toHaveTextContent('options › depth: must be integer')
    })

    it('re-checks the config once typing settles', async () => {
        const { validated } = backend({
            handlers: {
                'POST /nodes/acme.rich/config/validate': (_url, init) => {
                    const { config } = JSON.parse(init.body as string) as {
                        config: JsonObject
                    }
                    validated.push(config)
                    return ok(
                        'url' in config
                            ? { valid: true, issues: [] }
                            : {
                                  valid: false,
                                  issues: [
                                      { path: ['url'], message: 'is required' },
                                  ],
                              },
                    )
                },
            },
        })
        const { inspector } = await openInspector()
        expect(
            await inspector.findByText(
                '1 setting needs attention before the workflow can be published.',
            ),
        ).toBeInTheDocument()

        const url = inspector.getByRole('textbox', { name: /^Url/ })
        type(url, 'h')
        type(url, 'https://x.test')

        expect(
            await inspector.findByText('Configuration valid'),
        ).toBeInTheDocument()
        expect(url).toBeValid()
        // Debounced: the intermediate "h" was never sent.
        expect(validated).toEqual([{}, { url: 'https://x.test' }])
    })

    it('says so when the config cannot be checked, and keeps editing', async () => {
        backend({
            handlers: {
                'POST /nodes/acme.rich/config/validate': () =>
                    apiError(500, 'INTERNAL_ERROR', 'Boom.'),
            },
        })
        const { inspector } = await openInspector()
        expect(
            await inspector.findByText(/^Could not check the configuration/),
        ).toBeInTheDocument()
        type(inspector.getByRole('textbox', { name: /^Url/ }), 'https://x')
        expect(inspector.getByRole('textbox', { name: /^Url/ })).toHaveValue(
            'https://x',
        )
    })

    it('keeps invalid local input out of the config', async () => {
        const { saved } = backend({ config: { url: 'u', retries: 2 } })
        const { inspector } = await openInspector()

        const retries = inspector.getByRole('textbox', { name: /^Retries$/ })
        type(retries, '2x')
        expect(retries).toHaveValue('2x')
        expect(retries).toHaveAccessibleDescription('Enter a number.')
        const payload = inspector.getByRole('textbox', { name: /^Payload/ })
        type(payload, '{"a": ')
        expect(payload).toHaveAccessibleDescription(/Not valid JSON/)
        type(inspector.getByRole('textbox', { name: /^Url/ }), 'v')
        await saveDraft()

        // The last valid values are what the draft holds.
        expect(saved.at(-1)).toEqual({ url: 'v', retries: 2 })
        expect(retries).toHaveValue('2x')
    })

    it('stores credential references, never secrets', async () => {
        const { saved, requests } = backend({ config: { url: 'u' } })
        const { inspector } = await openInspector()
        const credential = inspector.getByRole('combobox', {
            name: /^Credential/,
        })

        // Picked from the names list, stored as a reference.
        await inspector.findByRole('option', { name: 'crm-api (API key)' })
        type(credential, 'crm-api')
        expect(credential).toHaveValue('crm-api')
        await saveDraft()
        expect(saved.at(-1)).toEqual({
            url: 'u',
            credential: 'credential:crm-api',
        })
        // Only names were read: never the admin API, never a credential.
        expect(
            requests.filter((u) =>
                u.pathname.startsWith('/api/v1/credentials'),
            ),
        ).toEqual([])
    })

    it('offers the configured LLM providers to choose from', async () => {
        const { saved } = backend({
            schema: {
                type: 'object',
                properties: {
                    provider: { type: 'string', format: 'llm-provider' },
                },
            },
            handlers: {
                'GET /system/llm-providers': () =>
                    ok([
                        { name: 'openai', requires_credential: true },
                        { name: 'vllm', requires_credential: false },
                    ]),
            },
        })
        const { inspector } = await openInspector()
        await inspector.findByRole('option', { name: 'vllm' })
        const provider = inspector.getByRole('combobox', { name: /^Provider/ })
        fireEvent.change(provider, { target: { value: 'vllm' } })
        expect(provider).toHaveAccessibleDescription(/Needs no credential/)
        await saveDraft()
        expect(saved.at(-1)).toEqual({ provider: 'vllm' })
    })

    it('flags a reference to a credential that does not exist', async () => {
        backend({ config: { url: 'u', credential: 'credential:gone' } })
        const { inspector } = await openInspector()
        await inspector.findByRole('option', { name: 'gone (not found)' })
        expect(
            inspector.getByRole('combobox', { name: /^Credential/ }),
        ).toHaveAccessibleDescription(/no credential named "gone"/)
    })

    it('falls back to typing a name when the list is unavailable', async () => {
        const { saved } = backend({
            config: { url: 'u' },
            handlers: {
                'GET /credential-names': () =>
                    apiError(500, 'INTERNAL_ERROR', 'Down.'),
            },
        })
        const { inspector } = await openInspector()
        const credential = await inspector.findByRole('textbox', {
            name: /^Credential/,
        })
        type(credential, 'credential:smtp-main')
        expect(credential).toHaveValue('smtp-main')
        await saveDraft()
        expect(saved.at(-1)).toMatchObject({
            credential: 'credential:smtp-main',
        })
    })

    it('hides a stored value that is not a credential reference', async () => {
        backend({
            config: { url: 'u', credential: 'sk-live-plaintext-123456' },
        })
        const { inspector } = await openInspector()

        await inspector.findByRole('option', { name: 'crm-api (API key)' })
        const credential = inspector.getByRole('combobox', {
            name: /^Credential/,
        })
        expect(credential).toHaveValue('')
        expect(credential).toHaveAccessibleDescription(
            /not a credential reference and is hidden/,
        )
        expect(
            screen.queryByDisplayValue(/sk-live-plaintext/),
        ).not.toBeInTheDocument()
        expect(document.body.textContent).not.toContain('sk-live-plaintext')
        type(credential, 'crm-api')
        expect(credential).toHaveValue('crm-api')
    })

    it('keeps the edits and says so when saving fails', async () => {
        backend({
            handlers: {
                'PUT /workflows/p1': () =>
                    apiError(500, 'INTERNAL_ERROR', 'Boom.'),
            },
        })
        const { inspector } = await openInspector()
        type(inspector.getByRole('textbox', { name: /^Url/ }), 'https://x')

        fireEvent.click(screen.getByRole('button', { name: 'Save draft' }))

        expect(
            await screen.findByText('Could not save the draft'),
        ).toBeInTheDocument()
        expect(screen.getByText('Changes not saved')).toBeInTheDocument()
        expect(inspector.getByRole('textbox', { name: /^Url/ })).toHaveValue(
            'https://x',
        )
    })

    it('follows schema changes without losing unknown values', async () => {
        let schema: object = RICH_SCHEMA
        const { saved } = backend({
            config: { url: 'https://old.test' },
            handlers: {
                'GET /nodes': () =>
                    ok({
                        items: [definition('acme.rich', schema)],
                        port_compatibility: {},
                    }),
            },
        })
        const { inspector, queryClient } = await openInspector()
        expect(inspector.getByRole('textbox', { name: /^Url/ })).toHaveValue(
            'https://old.test',
        )

        // A new node version renames url to endpoint.
        schema = {
            type: 'object',
            properties: { endpoint: { type: 'string' } },
            required: ['endpoint'],
            additionalProperties: false,
        }
        await act(() => queryClient.invalidateQueries({ queryKey: ['nodes'] }))

        const endpoint = await inspector.findByRole('textbox', {
            name: /^Endpoint/,
        })
        expect(endpoint).toBeRequired()
        const stale = inspector.getByRole('textbox', {
            name: "url (not in this node's settings)",
        })
        expect(stale).toHaveValue('"https://old.test"')
        type(endpoint, 'https://new.test')
        fireEvent.click(inspector.getByRole('button', { name: 'Remove url' }))
        await saveDraft()
        expect(saved.at(-1)).toEqual({ endpoint: 'https://new.test' })
    })

    it('shows a value of the wrong type as JSON so it can be fixed', async () => {
        backend({ config: { url: 42 } })
        const { inspector } = await openInspector()
        const url = inspector.getByRole('textbox', { name: /^Url/ })
        expect(url).toHaveValue('42')
        expect(url).toHaveAccessibleDescription(
            /does not match the field's type/,
        )
    })

    it('undo reverts a config edit in the form', async () => {
        backend({ config: { url: 'a' } })
        const { inspector } = await openInspector()
        const retries = inspector.getByRole('textbox', { name: /^Retries$/ })
        type(retries, '5')
        type(retries, '5x')
        expect(retries).toHaveAccessibleDescription('Enter a number.')
        fireEvent.click(screen.getByRole('button', { name: 'Undo' }))
        expect(retries).toHaveValue('')
        expect(retries).toBeValid()
    })

    it('says when a node has no settings', async () => {
        backend({ schema: { type: 'object', properties: {} } })
        const { inspector } = await openInspector()
        expect(
            inspector.getByText('Nothing to set up: this node works as it is.'),
        ).toBeInTheDocument()
    })

    it('picks tool nodes from the catalog and edits their own settings', async () => {
        const tools = {
            type: 'object',
            properties: {
                tools: {
                    type: 'array',
                    items: {
                        type: 'object',
                        properties: {
                            name: { type: 'string' },
                            node_type: { type: 'string', format: 'node-type' },
                            config: { type: 'object', format: 'node-config' },
                        },
                    },
                },
            },
        }
        const search = {
            ...definition('acme.search', {
                type: 'object',
                properties: { max_results: { type: 'integer', default: 5 } },
            }),
            name: 'Search',
            description: 'Searches the web.',
        }
        const branch = {
            ...definition('acme.branch', {}),
            name: 'Branch',
            capabilities: { is_control_flow: true },
        }
        const checked: JsonObject[] = []
        const { saved } = backend({
            schema: tools,
            handlers: {
                'GET /nodes': () =>
                    ok({
                        items: [definition('acme.rich', tools), search, branch],
                        port_compatibility: {},
                    }),
                'POST /nodes/acme.search/config/validate': (_url, init) => {
                    const body = JSON.parse(init.body as string) as {
                        config: JsonObject
                    }
                    checked.push(body.config)
                    return ok({
                        valid: false,
                        issues: [
                            { path: ['max_results'], message: 'must be <= 10' },
                        ],
                    })
                },
            },
        })
        const { inspector } = await openInspector()

        const list = within(inspector.getByRole('group', { name: 'Tools' }))
        fireEvent.click(list.getByRole('button', { name: 'Add item' }))
        const picker = list.getByRole('combobox', { name: /^Node type/ })
        // Control-flow nodes and tool hosts (no nested agents) are not offered.
        expect(
            within(picker)
                .getAllByRole('option')
                .map((o) => o.textContent),
        ).toEqual(['Choose a node…', 'Search (acme.search)'])
        expect(
            list.getByText('Choose a node to see its settings.'),
        ).toBeInTheDocument()

        fireEvent.change(picker, { target: { value: 'acme.search' } })
        expect(picker).toHaveAccessibleDescription('Searches the web.')
        const settings = within(
            list.getByRole('group', { name: 'Config: Search' }),
        )
        const max = settings.getByRole('textbox', { name: /^Max results/ })
        expect(max).toHaveAttribute('placeholder', '5')
        type(max, '50')
        // The tool's settings are checked as that node's, not the agent's.
        await waitFor(() => {
            expect(checked.at(-1)).toEqual({ max_results: 50 })
        })
        expect(await settings.findAllByText(/must be <= 10/)).not.toHaveLength(
            0,
        )
        await saveDraft()
        expect(saved.at(-1)).toEqual({
            tools: [{ node_type: 'acme.search', config: { max_results: 50 } }],
        })

        // A changed pick drops the old node's settings, never carries them over.
        fireEvent.change(picker, { target: { value: '' } })
        await saveDraft()
        expect(saved.at(-1)).toEqual({ tools: [{}] })
    })

    it('does not offer a form for a node type missing from the catalog', async () => {
        const { requests } = backend({
            nodeType: 'acme.gone',
            config: { kept: true },
        })
        const { inspector } = await openInspector()
        expect(inspector.getByText('Settings unavailable')).toBeInTheDocument()
        expect(
            requests.some((u) => u.pathname.endsWith('/config/validate')),
        ).toBe(false)
    })
})
