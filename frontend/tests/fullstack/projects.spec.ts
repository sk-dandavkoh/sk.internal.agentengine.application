import { readFileSync } from 'node:fs'
import { randomUUID } from 'node:crypto'
import {
    expect,
    request as http,
    test,
    type APIRequestContext,
    type WebSocketRoute,
} from '@playwright/test'

// The project lifecycle through the production bundle against the real
// backend (playwright.fullstack.config.ts): nothing is stubbed.
//
// The database is empty at the start of every run, so the first admin is
// created here. Until the login UI exists (Phase 21) the browser gets the
// session token as a request header; everything else is the real app.

let api: APIRequestContext
let token = ''

test.beforeAll(async ({ baseURL }) => {
    if (!baseURL) throw new Error('baseURL is not configured')
    const credentials = {
        username: 'e2e-admin',
        password: process.env.AKASHA_E2E_ADMIN_PASSWORD,
    }
    const anonymous = await http.newContext({ baseURL })
    // 409: an earlier worker of this run already created the admin.
    expect([201, 409]).toContain(
        (
            await anonymous.post('/api/v1/auth/setup', { data: credentials })
        ).status(),
    )
    const login = await anonymous.post('/api/v1/auth/login', {
        data: credentials,
    })
    expect(login.status()).toBe(200)
    token = String(((await login.json()) as { token: unknown }).token)
    await anonymous.dispose()
    api = await http.newContext({
        baseURL,
        extraHTTPHeaders: { Authorization: `Bearer ${token}` },
    })
})

test.afterAll(async () => {
    await api.dispose()
})

test.beforeEach(async ({ page }) => {
    await page.setExtraHTTPHeaders({ Authorization: `Bearer ${token}` })
})

/** A name unique to this test: tests share one database. */
const unique = (name: string) => `${name} ${randomUUID().slice(0, 8)}`

interface ProjectJson {
    id: string
    name: string
    revision: number
}

async function createViaApi(name: string): Promise<ProjectJson> {
    const response = await api.post('/api/v1/projects', { data: { name } })
    expect(response.status()).toBe(201)
    return (await response.json()) as ProjectJson
}

async function namesOnServer(q: string): Promise<string[]> {
    const response = await api.get('/api/v1/projects', { params: { q } })
    const body = (await response.json()) as { items: ProjectJson[] }
    return body.items.map((p) => p.name).sort()
}

test('create, search, rename, duplicate and delete', async ({ page: p }) => {
    const name = unique('Invoice triage')
    await p.goto('/projects')

    // Create, with the real backend refusing a blank name at the field.
    await p.getByRole('button', { name: 'New project' }).first().click()
    const create = p.getByRole('dialog', { name: 'New project' })
    await create.getByLabel('Name').fill('   ')
    await create.getByRole('button', { name: 'Create project' }).click()
    await expect(create.getByLabel('Name')).toHaveAttribute(
        'aria-invalid',
        'true',
    )
    await create.getByLabel('Name').fill(name)
    await create.getByRole('button', { name: 'Create project' }).click()
    await expect(p.getByRole('heading', { level: 1, name })).toBeVisible()
    const id = /\/projects\/([^/]+)$/.exec(p.url())?.[1] ?? ''

    // Search finds it through the backend.
    await p.goto('/projects')
    await p.getByRole('searchbox').fill(name.toUpperCase())
    await p.getByRole('button', { name: 'Search' }).click()
    const table = p.getByRole('table', { name: 'Projects' })
    await expect(table.getByRole('link')).toHaveText([name])

    // Rename.
    await table.getByRole('link', { name }).click()
    await p.getByRole('button', { name: 'Edit details' }).click()
    const edit = p.getByRole('dialog', { name: 'Edit project details' })
    await edit.getByLabel('Name').fill(`${name} v2`)
    await edit.getByLabel('Description').fill('Sorts invoices.')
    await edit.getByRole('button', { name: 'Save' }).click()
    await expect(
        p.getByRole('heading', { level: 1, name: `${name} v2` }),
    ).toBeVisible()

    // Duplicate, then delete the copy.
    await p.getByRole('button', { name: 'Duplicate' }).click()
    await p
        .getByRole('dialog', { name: 'Duplicate project' })
        .getByRole('button', { name: 'Duplicate' })
        .click()
    await expect(
        p.getByRole('heading', { level: 1, name: `Copy of ${name} v2` }),
    ).toBeVisible()
    await p.getByRole('button', { name: 'Delete', exact: true }).click()
    await p
        .getByRole('dialog', { name: 'Delete project?' })
        .getByRole('button', { name: 'Delete project' })
        .click()
    await expect(p).toHaveURL(/\/projects$/)

    // The backend holds exactly what the UI showed.
    expect(await namesOnServer(name)).toEqual([`${name} v2`])
    const saved = await api.get(`/api/v1/projects/${id}`)
    expect(await saved.json()).toMatchObject({
        description: 'Sorts invoices.',
        revision: 2,
    })
})

interface HelloWorld {
    graph: { nodes: { label?: string }[] }
    triggers: unknown
    dependencies: unknown
}

/** Stores the hello-world example as the draft, optionally edited, and
 * publishes it; returns the next draft revision. */
async function publishHelloWorld(
    project: ProjectJson,
    revision = project.revision,
    edit: (draft: HelloWorld) => void = () => undefined,
): Promise<number> {
    const example = readFileSync(
        new URL(
            '../../../backend/examples/projects/hello-world.json',
            import.meta.url,
        ),
        'utf8',
    )
    const { graph, triggers, dependencies } = JSON.parse(example) as HelloWorld
    const draft = { graph, triggers, dependencies }
    edit(draft)
    const put = await api.put(`/api/v1/projects/${project.id}`, {
        data: { expected_revision: revision, name: project.name, draft },
    })
    expect(put.status()).toBe(200)
    expect(
        (await api.post(`/api/v1/projects/${project.id}/publish`)).status(),
    ).toBe(201)
    return revision + 2
}

test('a published project cannot be deleted', async ({ page: p }) => {
    const project = await createViaApi(unique('Published'))
    await publishHelloWorld(project)

    await p.goto(`/projects/${project.id}`)
    await expect(p.getByText('v1', { exact: true })).toBeVisible()
    await p.getByRole('button', { name: 'Delete', exact: true }).click()
    const dialog = p.getByRole('dialog', { name: 'Delete project?' })
    await dialog.getByRole('button', { name: 'Delete project' }).click()

    await expect(
        dialog.getByText(
            'A project with published versions cannot be deleted.',
        ),
    ).toBeVisible()
    expect((await api.get(`/api/v1/projects/${project.id}`)).status()).toBe(200)
})

test('an edit based on a stale revision is refused, then reloaded', async ({
    page: p,
}) => {
    const project = await createViaApi(unique('Contended'))
    await p.goto(`/projects/${project.id}`)
    await expect(
        p.getByRole('heading', { level: 1, name: project.name }),
    ).toBeVisible()

    // Someone else saves while the page shows revision 1.
    const elsewhere = `${project.name} (elsewhere)`
    const put = await api.put(`/api/v1/projects/${project.id}`, {
        data: { expected_revision: 1, name: elsewhere, draft: {} },
    })
    expect(put.status()).toBe(200)

    await p.getByRole('button', { name: 'Edit details' }).click()
    const edit = p.getByRole('dialog', { name: 'Edit project details' })
    await edit.getByLabel('Name').fill('Mine')
    await edit.getByRole('button', { name: 'Save' }).click()

    await expect(edit.getByRole('alert')).toContainText('reload and retry')
    await expect(
        p.getByRole('heading', { level: 1, name: elsewhere }),
    ).toBeVisible()
    expect(await namesOnServer(project.name)).toEqual([elsewhere])
})

test('the node library lists the backend registry and adds to the draft', async ({
    page: p,
}) => {
    const project = await createViaApi(unique('Library'))
    await p.goto(`/projects/${project.id}/editor`)
    const library = p.getByRole('region', { name: 'Node library' })
    // Built-in nodes come from the real registry, not a frontend list.
    await expect(library.getByRole('heading', { name: 'Log' })).toBeVisible()
    await library
        .getByRole('searchbox', { name: 'Search nodes' })
        .fill('core.log')
    await library.getByRole('button', { name: 'Add Log to graph' }).click()
    await p.keyboard.press('ControlOrMeta+s')
    await expect(p.getByText('All changes saved to draft')).toBeVisible()

    const saved = await api.get(`/api/v1/projects/${project.id}`)
    const body = (await saved.json()) as {
        draft: { graph: { nodes: { node_type: string }[] } }
    }
    expect(body.draft.graph.nodes.map((n) => n.node_type)).toEqual(['core.log'])
})

test('the inspector configures a built-in node from its backend schema', async ({
    page: p,
}) => {
    const project = await createViaApi(unique('Inspector'))
    await p.goto(`/projects/${project.id}/editor`)
    const library = p.getByRole('region', { name: 'Node library' })
    await library
        .getByRole('searchbox', { name: 'Search nodes' })
        .fill('core.http')
    await library
        .getByRole('button', { name: 'Add HTTP Request to graph' })
        .click()
    const inspector = p.getByRole('region', { name: /^Settings: HTTP Request/ })

    // The real backend judges the config, field by field.
    await expect(
        inspector.getByText(/publishing would be rejected/),
    ).toBeVisible()
    const url = inspector.getByRole('textbox', { name: /^Url/ })
    await url.fill('ftp://example.com')
    await expect(url).toHaveAccessibleDescription(
        /does not match the required format/,
    )
    await url.fill('https://example.com/hook')
    await inspector
        .getByRole('combobox', { name: /^Method/ })
        .selectOption('POST')
    const secrets = inspector.getByRole('group', { name: 'Header credentials' })
    await secrets
        .getByRole('textbox', { name: 'New Header credentials entry' })
        .fill('Authorization')
    await secrets.getByRole('button', { name: 'Add entry' }).click()
    await secrets
        .getByRole('combobox', { name: /^Authorization/ })
        .fill('crm-api')
    await expect(inspector.getByText('Configuration valid')).toBeVisible()
    await p.keyboard.press('ControlOrMeta+s')
    await expect(p.getByText('All changes saved to draft')).toBeVisible()

    const saved = await api.get(`/api/v1/projects/${project.id}`)
    const body = (await saved.json()) as {
        draft: { graph: { nodes: { config: unknown }[] } }
    }
    expect(body.draft.graph.nodes[0]?.config).toEqual({
        url: 'https://example.com/hook',
        method: 'POST',
        header_credentials: { Authorization: 'credential:crm-api' },
    })
})

test('a graph connected by dragging is stored, validates and reloads the same', async ({
    page: p,
}) => {
    // Three unconnected built-ins, placed apart: the connecting is the UI's.
    const project = await createViaApi(unique('Pipeline'))
    const nodes = ['core.input', 'core.log', 'core.output'].map((type, i) => ({
        id: `node_${String(i + 1).repeat(32)}`,
        node_type: type,
        config: type === 'core.log' ? { message: 'lead seen' } : {},
        label: '',
        position: { x: i * 60, y: i * 170 },
    }))
    const seeded = await api.put(`/api/v1/projects/${project.id}`, {
        data: {
            name: project.name,
            description: '',
            draft: {
                graph: { nodes, edges: [] },
                triggers: [],
                dependencies: [],
            },
            expected_revision: project.revision,
        },
    })
    expect(seeded.status()).toBe(200)
    await p.goto(`/projects/${project.id}/editor`)
    const card = (name: string) =>
        p.locator('.react-flow__node').filter({ hasText: name })
    await expect(p.locator('.react-flow__node')).toHaveCount(3)

    const port = (name: string, side: 'source' | 'target') =>
        card(name).locator(`.react-flow__handle.${side}[data-handleid="value"]`)
    async function connect(from: string, to: string) {
        const a = await port(from, 'source').boundingBox()
        const b = await port(to, 'target').boundingBox()
        if (!a || !b) throw new Error('handle not rendered')
        await p.mouse.move(a.x + a.width / 2, a.y + a.height / 2)
        await p.mouse.down()
        await p.mouse.move(b.x + b.width / 2, b.y + b.height / 2, { steps: 10 })
        await p.mouse.up()
    }
    await expect(card('Log')).toContainText('Missing required input: value')
    await connect('core.input', 'core.log')
    await connect('core.log', 'core.output')
    await expect(p.locator('.react-flow__edge')).toHaveCount(2)
    await expect(card('Log')).not.toContainText('Missing required input')
    await p.keyboard.press('ControlOrMeta+s')
    await expect(p.getByText('All changes saved to draft')).toBeVisible()

    // The backend stored the edges and judges the graph publishable.
    const stored = (await (
        await api.get(`/api/v1/projects/${project.id}`)
    ).json()) as {
        draft: {
            graph: {
                nodes: { id: string; node_type: string }[]
                edges: { source_node_id: string; target_node_id: string }[]
            }
        }
    }
    const typeOf = (id: string) =>
        stored.draft.graph.nodes.find((n) => n.id === id)?.node_type
    expect(
        stored.draft.graph.edges.map(
            (e) =>
                `${String(typeOf(e.source_node_id))}>${String(typeOf(e.target_node_id))}`,
        ),
    ).toEqual(['core.input>core.log', 'core.log>core.output'])
    const validation = await api.post(`/api/v1/projects/${project.id}/validate`)
    expect(await validation.json()).toMatchObject({ valid: true, issues: [] })

    // A reload shows exactly what was stored.
    await p.reload()
    await expect(p.locator('.react-flow__edge')).toHaveCount(2)
    await expect(
        p.getByRole('group', {
            name: 'Connection from Input value to Log value',
        }),
    ).toBeVisible()
    await expect(p.getByText('All changes saved to draft')).toBeVisible()
})

test('the backend port rule decides which inputs an output may feed', async ({
    page: p,
}) => {
    const project = await createViaApi(unique('Ports'))
    await p.goto(`/projects/${project.id}/editor`)
    const library = p.getByRole('region', { name: 'Node library' })
    const added: [string, string][] = [
        ['core.http', 'HTTP Request'],
        ['core.web_search', 'Web Search'],
    ]
    for (const [type, name] of added) {
        await library
            .getByRole('searchbox', { name: 'Search nodes' })
            .fill(type)
        await library
            .getByRole('button', { name: `Add ${name} to graph` })
            .click()
    }
    await p
        .getByRole('region', { name: /^Nodes/ })
        .getByRole('button', { name: /^HTTP Request/ })
        .click()
    const panel = p.getByRole('region', { name: /^Connections: HTTP Request/ })
    const to = panel.getByRole('combobox', { name: 'To input' })
    // number → string: refused by the real compatibility table.
    await panel
        .getByRole('combobox', { name: 'From output' })
        .selectOption('status')
    await expect(
        to.getByRole('option', {
            name: 'Web Search › query (incompatible type)',
        }),
    ).toBeDisabled()
    // json → string: the generic type connects anywhere.
    await panel
        .getByRole('combobox', { name: 'From output' })
        .selectOption('body')
    await to.selectOption({ label: 'Web Search › query' })
    await panel.getByRole('button', { name: 'Connect' }).click()
    await expect(p.locator('.react-flow__edge')).toHaveCount(1)
})

test('validate, fix and publish a draft as an immutable version', async ({
    page: p,
}) => {
    // Log's required input is not connected: the backend must refuse it.
    const project = await createViaApi(unique('Publishing'))
    const id = (n: number) => `node_${String(n).repeat(32)}`
    const seeded = await api.put(`/api/v1/projects/${project.id}`, {
        data: {
            name: project.name,
            description: '',
            draft: {
                graph: {
                    nodes: ['core.input', 'core.log', 'core.output'].map(
                        (type, i) => ({
                            id: id(i + 1),
                            node_type: type,
                            config:
                                type === 'core.log' ? { message: 'seen' } : {},
                            label: '',
                            position: { x: i * 220, y: 0 },
                        }),
                    ),
                    edges: [
                        {
                            source_node_id: id(2),
                            source_port: 'value',
                            target_node_id: id(3),
                            target_port: 'value',
                        },
                    ],
                },
                triggers: [],
                dependencies: [],
            },
            expected_revision: project.revision,
        },
    })
    expect(seeded.status()).toBe(200)
    await p.goto(`/projects/${project.id}/editor`)
    const panel = p.getByRole('region', { name: 'Validate and publish' })

    await panel.getByRole('button', { name: 'Validate' }).click()
    const errors = panel.getByRole('list', { name: 'Nodes errors' })
    await expect(errors).toContainText('Node "Log" → port "value"')
    await expect(panel.getByRole('button', { name: 'Publish…' })).toBeDisabled()
    await errors
        .getByRole('button', { name: /port "value"/ })
        .first()
        .click()
    await expect(
        p
            .getByRole('region', { name: /^Nodes/ })
            .getByRole('button', { name: /^Log/ }),
    ).toHaveAttribute('aria-pressed', 'true')

    // Fix: connect Input → Log, save, validate again.
    await p
        .getByRole('region', { name: /^Nodes/ })
        .getByRole('button', { name: /^Input/ })
        .click()
    const connections = p.getByRole('region', { name: /^Connections: Input/ })
    await connections
        .getByRole('combobox', { name: 'To input' })
        .selectOption({ label: 'Log › value' })
    await connections.getByRole('button', { name: 'Connect' }).click()
    await p.keyboard.press('ControlOrMeta+s')
    await expect(p.getByText('All changes saved to draft')).toBeVisible()
    await panel.getByRole('button', { name: 'Validate' }).click()
    await expect(panel.getByText('Valid', { exact: true })).toBeVisible()

    await panel.getByRole('button', { name: 'Publish…' }).click()
    await p
        .getByRole('dialog', { name: 'Publish version 1?' })
        .getByRole('button', { name: 'Publish version 1' })
        .click()
    await expect(p.getByText('Version 1 is published')).toBeVisible()

    // The backend froze exactly the fixed draft.
    const version = (await (
        await api.get(`/api/v1/projects/${project.id}/versions/1`)
    ).json()) as { graph: { edges: unknown[] } }
    expect(version.graph.edges).toHaveLength(2)
    // The editor continues on the draft the publish advanced.
    await expect(
        p.getByText(/Revision \d+ has not been validated yet/),
    ).toBeVisible()
})

test('without a session the backend refuses and the UI says so', async ({
    browser,
}) => {
    const page = await browser.newPage() // no Authorization header
    await page.goto('/projects')
    await expect(page.getByRole('alert')).toContainText(
        'Could not load projects',
    )
    await page.close()
})

test('versions are listed, inspected, compared and show their runs', async ({
    page: p,
}) => {
    const project = await createViaApi(unique('Versioned'))
    const next = await publishHelloWorld(project)
    await publishHelloWorld(project, next, (draft) => {
        const [first] = draft.graph.nodes
        if (first) first.label = 'Renamed in v2'
    })
    const run = await api.post(
        `/api/v1/projects/${project.id}/versions/1/execute`,
        { data: {} },
    )
    expect(run.status()).toBe(201)
    const { id: executionId } = (await run.json()) as { id: string }
    await p.goto(`/projects/${project.id}`)
    await p.getByRole('link', { name: 'Versions', exact: true }).click()
    const versions = p.getByRole('table', { name: 'Published versions' })
    await expect(versions.getByRole('row')).toHaveCount(3)
    const latest = versions.getByRole('row').nth(2)
    await expect(latest.getByText('Latest')).toBeVisible()
    await expect(latest.getByText('e2e-admin')).toBeVisible()

    await versions.getByRole('link', { name: 'v1', exact: true }).click()
    await expect(
        p.getByRole('heading', { name: `${project.name} · v1`, level: 1 }),
    ).toBeVisible()
    await expect(p.getByText('Published versions are read-only')).toBeVisible()
    await expect(p.getByText(executionId)).toBeVisible()
    const graph = p.getByRole('figure', { name: 'Published graph (read-only)' })
    // One card per node of the example: Input, Set, Output.
    await expect(
        graph.getByRole('group', { name: /\(core\.\w+\)$/ }),
    ).toHaveCount(3)

    await p.getByRole('combobox', { name: 'Compare with' }).selectOption('2')
    const result = p.getByRole('region', { name: 'Comparison result' })
    await expect(result.getByText('Changes from v1 to v2:')).toBeVisible()
    await expect(result.getByText('Changed', { exact: true })).toHaveCount(1)

    // v2 never ran: the runs list is per version.
    await p.goto(`/projects/${project.id}/versions/2`)
    await expect(p.getByRole('heading', { name: 'Not run yet' })).toBeVisible()

    // Clone v1 into a new project: its draft is v1, not the current draft.
    await p.goto(`/projects/${project.id}/versions/1`)
    await p.getByRole('button', { name: 'Clone as new project…' }).click()
    await p.getByRole('button', { name: 'Create project' }).click()
    await expect(
        p.getByRole('heading', { name: `${project.name} (v1)`, level: 1 }),
    ).toBeVisible()
    const cloneId = p.url().split('/').pop() ?? ''
    const clone = (await (
        await api.get(`/api/v1/projects/${cloneId}`)
    ).json()) as { current_version: number; draft: HelloWorld }
    expect(clone.current_version).toBe(0)
    expect(clone.draft.graph.nodes.map((n) => n.label)).not.toContain(
        'Renamed in v2',
    )

    // Restore v1 into this project's draft; v2 is the draft until then.
    await p.goto(`/projects/${project.id}/versions/1`)
    await p
        .getByRole('combobox', { name: 'Compare with' })
        .selectOption('draft')
    await expect(
        result.getByText('Changes from v1 to the current draft:'),
    ).toBeVisible()
    await p.getByRole('button', { name: 'Restore to draft…' }).click()
    await p.getByRole('button', { name: 'Replace draft' }).click()
    await expect(p).toHaveURL(new RegExp(`/projects/${project.id}/editor$`))
    const restored = (await (
        await api.get(`/api/v1/projects/${project.id}`)
    ).json()) as { current_version: number; draft: HelloWorld }
    expect(restored.current_version).toBe(2)
    expect(restored.draft.graph.nodes.map((n) => n.label)).not.toContain(
        'Renamed in v2',
    )
})

test('a run started in the UI is listed, opened and cancelled', async ({
    page: p,
}) => {
    // The suite runs no worker, so the run stays QUEUED until cancelled:
    // every state shown below is the backend's, not a client guess.
    const project = await createViaApi(unique('Runnable'))
    await publishHelloWorld(project)

    await p.goto(`/projects/${project.id}/versions/1`)
    await p.getByRole('button', { name: 'Run…' }).click()
    await p.getByRole('button', { name: 'Start execution' }).click()
    await expect(p).toHaveURL(/\/executions\/exec_\w+$/)
    const executionId = p.url().split('/').pop() ?? ''
    await expect(
        p.getByRole('heading', { name: 'Execution', level: 1 }),
    ).toBeVisible()
    await expect(p.getByText('Queued').first()).toBeVisible()

    await p.goto('/executions')
    const row = p
        .getByRole('table', { name: 'Executions' })
        .getByRole('row')
        .filter({ hasText: executionId })
    await expect(row.getByText('Queued')).toBeVisible()
    await expect(row.getByText(project.name)).toBeVisible()
    await row.getByRole('link', { name: executionId }).click()

    await p
        .getByRole('button', { name: `Cancel execution ${executionId}` })
        .click()
    await p
        .getByRole('button', { name: 'Cancel execution', exact: true })
        .click()
    await expect(p.getByText('Execution cancelled')).toBeVisible()
    await expect(
        p.getByRole('button', { name: `Cancel execution ${executionId}` }),
    ).toHaveCount(0)
    const stored = (await (
        await api.get(`/api/v1/executions/${executionId}`)
    ).json()) as { status: string; cancellable: boolean }
    expect(stored).toMatchObject({ status: 'cancelled', cancellable: false })
})

test('a run is followed live, across a dropped connection, to its end', async ({
    page: p,
}) => {
    const project = await createViaApi(unique('Live'))
    await publishHelloWorld(project)
    const run = await api.post(
        `/api/v1/projects/${project.id}/versions/1/execute`,
        { data: {} },
    )
    const { id: executionId } = (await run.json()) as { id: string }

    // The real backend stream. Until the login UI exists (Phase 21) the app
    // has no token to put in its subscribe frame; this adds it, like the
    // request header above. Everything else passes through unchanged.
    const subscribes: { after: number }[] = []
    let connection: WebSocketRoute | undefined
    await p.routeWebSocket(/\/live$/, (ws) => {
        connection = ws
        const server = ws.connectToServer()
        ws.onMessage((message) => {
            const frame = JSON.parse(String(message)) as { after: number }
            subscribes.push(frame)
            server.send(JSON.stringify({ ...frame, token }))
        })
    })

    await p.goto(`/executions/${executionId}`)
    await expect(p.getByText('Live', { exact: true })).toBeVisible()
    const activity = p.getByRole('list', { name: 'Live activity' })
    await expect(activity.getByText('Execution created')).toBeVisible()

    // Dropped (e.g. a proxy restart); the run is cancelled meanwhile, so
    // its events are missed by this connection.
    await connection?.close({ code: 1012, reason: 'test: dropped' })
    await expect(p.getByText('Reconnecting')).toBeVisible()
    const cancelled = await api.post(
        `/api/v1/executions/${executionId}/cancel`,
        { data: {} },
    )
    expect(cancelled.status()).toBe(202)

    // Reconnected after the backoff: resumed after the last seen event, the
    // missed ones arrive, and the page shows the persisted state.
    await expect(p.getByText('Live updates finished')).toBeVisible()
    await expect(activity.getByText('Execution cancelled')).toBeVisible()
    await expect(p.getByText('Cancelled').first()).toBeVisible()
    expect(subscribes).toHaveLength(2)
    expect(subscribes[1]?.after).toBeGreaterThan(0)
    // Each stored event exactly once: none lost, none doubled by the resume.
    const stored = (await (
        await api.get(`/api/v1/executions/${executionId}/events`)
    ).json()) as { items: unknown[] }
    await expect(activity.getByRole('listitem')).toHaveCount(
        stored.items.length,
    )
})
