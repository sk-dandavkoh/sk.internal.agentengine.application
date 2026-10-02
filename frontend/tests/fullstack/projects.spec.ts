import { readFileSync } from 'node:fs'
import { randomUUID } from 'node:crypto'
import {
    expect,
    request as http,
    test,
    type APIRequestContext,
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

test('a published project cannot be deleted', async ({ page: p }) => {
    const project = await createViaApi(unique('Published'))
    const example = readFileSync(
        new URL(
            '../../../backend/examples/projects/hello-world.json',
            import.meta.url,
        ),
        'utf8',
    )
    const { graph, triggers, dependencies } = JSON.parse(example) as Record<
        string,
        unknown
    >
    const put = await api.put(`/api/v1/projects/${project.id}`, {
        data: {
            expected_revision: project.revision,
            name: project.name,
            draft: { graph, triggers, dependencies },
        },
    })
    expect(put.status()).toBe(200)
    expect(
        (await api.post(`/api/v1/projects/${project.id}/publish`)).status(),
    ).toBe(201)

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
