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
