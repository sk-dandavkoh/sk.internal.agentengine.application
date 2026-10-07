import { readFileSync } from 'node:fs'
import { randomUUID } from 'node:crypto'
import type { APIRequestContext } from '@playwright/test'
import { expect, test } from '../e2e/fixtures'
import { adminApi, downloaded, signIn, unzip } from './session'

// The credential workflow against the real backend: a secret is stored
// once, referenced by a node by name, and never comes back, not on the
// page, not through the API, not in an export. An import elsewhere names
// the credentials it lacks.

let api: APIRequestContext

test.beforeAll(async ({ baseURL }) => {
    if (!baseURL) throw new Error('baseURL is not configured')
    api = await adminApi(baseURL)
})

test.afterAll(async () => {
    await api.dispose()
})

test.beforeEach(async ({ page }) => {
    await signIn(page)
})

test('a credential is created, referenced, exported without its secret and reported missing on import', async ({
    page: p,
}) => {
    const name = `crm_${randomUUID().slice(0, 8)}`
    const secret = `sk-e2e-${randomUUID()}`
    const reference = `credential:${name}`
    // Every response the page receives; none may carry the secret.
    const bodies: Promise<string>[] = []
    p.on('response', (r) => {
        if (r.url().includes('/api/v1/')) bodies.push(r.text().catch(() => ''))
    })

    await test.step('create the credential', async () => {
        await p.goto('/credentials')
        await p.getByRole('button', { name: 'New credential' }).first().click()
        const dialog = p.getByRole('dialog', { name: 'New credential' })
        await dialog.getByLabel('Name').fill(name)
        await dialog.getByLabel('Secret value').fill(secret)
        await dialog.getByRole('button', { name: 'Create credential' }).click()
        await expect(dialog).toBeHidden()
        const row = p
            .getByRole('table', { name: 'Credentials' })
            .getByRole('row')
            .filter({ hasText: name })
        await expect(row.getByText(reference)).toBeVisible()
        await expect(row.getByLabel('Hidden')).toBeVisible()
    })

    await test.step('the secret stays hidden, also after a reload', async () => {
        await p.reload()
        await expect(p.getByText(reference)).toBeVisible()
        expect(await p.content()).not.toContain(secret)
        const stored = await api.get(
            `/api/v1/credentials/${encodeURIComponent(name)}`,
        )
        expect(stored.status()).toBe(200)
        expect(await stored.text()).not.toContain(secret)
        expect(
            await p.evaluate<string>(
                'JSON.stringify([localStorage, sessionStorage])',
            ),
        ).not.toContain(secret)
    })

    let workflowId = ''
    await test.step('a node references it by name', async () => {
        const created = await api.post('/api/v1/workflows', {
            data: { name: `Uses ${name}` },
        })
        workflowId = ((await created.json()) as { id: string }).id
        await p.goto(`/workflows/${workflowId}/editor`)
        const library = p.getByRole('region', { name: 'Node library' })
        await library
            .getByRole('searchbox', { name: 'Search nodes' })
            .fill('core.http')
        await library
            .getByRole('button', { name: 'Add HTTP Request to graph' })
            .click()
        const inspector = p
            .getByRole('dialog', { name: 'HTTP Request' })
            .getByRole('region', { name: 'Settings' })
        await inspector
            .getByRole('textbox', { name: /^Url/ })
            .fill('https://example.com/hook')
        const secrets = inspector.getByRole('group', {
            name: 'Header credentials',
        })
        await secrets
            .getByRole('textbox', { name: 'New Header credentials entry' })
            .fill('Authorization')
        await secrets.getByRole('button', { name: 'Add entry' }).click()
        const field = secrets.getByRole('combobox', { name: /^Authorization/ })
        // Suggested from the backend's credential list: names, no values.
        const listId = (await field.getAttribute('list')) ?? ''
        await expect(
            p.locator(`datalist[id="${listId}"] option[value="${name}"]`),
        ).toHaveCount(1)
        await field.fill(name)
        await expect(inspector.getByText('Configuration valid')).toBeVisible()
        await p.keyboard.press('ControlOrMeta+s')
        await expect(p.getByText('All changes saved to draft')).toBeVisible()
        const draft = await (
            await api.get(`/api/v1/workflows/${workflowId}`)
        ).text()
        expect(draft).toContain(`"Authorization":"${reference}"`)
        expect(draft).not.toContain(secret)
    })

    let packagePath = ''
    await test.step('the export holds the reference, never the secret', async () => {
        await p.goto(`/workflows/${workflowId}`)
        const { path } = await downloaded(p, () =>
            p.getByRole('button', { name: 'Export', exact: true }).click(),
        )
        const content = [...unzip(readFileSync(path)).values()].join('\n')
        expect(content).toContain(reference)
        expect(content).not.toContain(secret)
        packagePath = path
    })

    await test.step('deleted, the import names it as missing', async () => {
        await p.goto('/credentials')
        await p.getByRole('button', { name: `Delete ${name}` }).click()
        await p
            .getByRole('dialog', { name: `Delete “${name}”?` })
            .getByRole('button', { name: 'Delete credential' })
            .click()
        await expect(p.getByText(reference)).toHaveCount(0)
        expect(
            (
                await api.get(`/api/v1/credentials/${encodeURIComponent(name)}`)
            ).status(),
        ).toBe(404)

        await p.goto('/workflows')
        await p.getByRole('button', { name: 'Import workflow' }).click()
        const dialog = p.getByRole('dialog', { name: 'Import workflow' })
        await dialog
            .getByLabel('Workflow package (.zip)')
            .setInputFiles(packagePath)
        await dialog
            .getByRole('button', { name: 'Import', exact: true })
            .click()
        const missing = dialog.getByRole('status').filter({
            hasText: 'Credentials to create',
        })
        await expect(missing.getByRole('listitem')).toHaveText([name])
    })

    expect((await Promise.all(bodies)).join('\n')).not.toContain(secret)
})

test('a broken package is refused with the backend reason', async ({
    page: p,
}) => {
    await p.goto('/workflows')
    await p.getByRole('button', { name: 'Import workflow' }).click()
    const dialog = p.getByRole('dialog', { name: 'Import workflow' })
    await dialog.getByLabel('Workflow package (.zip)').setInputFiles({
        name: 'broken.akasha.zip',
        mimeType: 'application/zip',
        buffer: Buffer.from('not a zip archive'),
    })
    const response = p.waitForResponse((r) =>
        r.url().endsWith('/api/v1/workflows/import'),
    )
    await dialog.getByRole('button', { name: 'Import', exact: true }).click()
    const refused = await response
    expect(refused.status()).toBe(422)
    const { message } = (await refused.json()) as { message: string }
    await expect(dialog.getByRole('alert')).toContainText(message)
    // Still the upload form: nothing was imported.
    await expect(dialog.getByLabel('Workflow package (.zip)')).toBeVisible()
})
