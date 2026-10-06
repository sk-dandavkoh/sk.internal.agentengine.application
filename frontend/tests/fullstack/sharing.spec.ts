import { randomUUID } from 'node:crypto'
import type { APIRequestContext, Browser, Page } from '@playwright/test'
import { expect, test } from '../e2e/fixtures'
import { adminApi } from './session'

// Project sharing against the real backend: every refusal below is the
// backend's answer; the UI only stops offering what it would refuse.

let api: APIRequestContext

test.beforeAll(async ({ baseURL }) => {
    if (!baseURL) throw new Error('baseURL is not configured')
    api = await adminApi(baseURL)
})

test.afterAll(async () => {
    await api.dispose()
})

async function member(prefix: string) {
    const user = {
        username: `${prefix}-${randomUUID().slice(0, 8)}`,
        password: randomUUID(),
    }
    const created = await api.post('/api/v1/users', {
        data: { ...user, role: 'member' },
    })
    expect(created.status()).toBe(201)
    return user
}

async function signedIn(
    browser: Browser,
    user: { username: string; password: string },
): Promise<Page> {
    const page = await (await browser.newContext()).newPage()
    await page.goto('/login')
    await page.getByLabel(/^Username/).fill(user.username)
    await page.getByLabel(/^Password/).fill(user.password)
    await page.getByRole('button', { name: 'Sign in' }).click()
    await expect(
        page.getByRole('heading', { level: 1, name: 'Dashboard' }),
    ).toBeVisible()
    return page
}

test('an owner shares a project as viewer, then editor, then stops sharing', async ({
    browser,
}) => {
    const owner = await member('owner')
    const guest = await member('guest')
    const ownerPage = await signedIn(browser, owner)
    const name = `Shared ${randomUUID().slice(0, 8)}`

    // The owner creates the project as themselves (their session cookie).
    const id = await ownerPage.evaluate<string>(
        `fetch('/api/v1/projects', {
            method: 'POST',
            headers: { 'X-Akasha-CSRF': '1', 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: ${JSON.stringify(name)} }),
        }).then((r) => r.json()).then((p) => p.id)`,
    )
    const projectUrl = `/projects/${id}`

    await test.step('share as viewer from the project page', async () => {
        await ownerPage.goto(projectUrl)
        const form = ownerPage.getByRole('form', { name: 'Share project' })
        await form.getByLabel(/^Username/).fill(guest.username)
        await form.getByRole('button', { name: 'Share' }).click()
        await expect(
            ownerPage.getByText(`Shared with “${guest.username}” as viewer`),
        ).toBeVisible()
    })

    const guestPage = await signedIn(browser, guest)

    await test.step('the viewer finds it, reads it, cannot change it', async () => {
        await guestPage.goto('/projects')
        const row = guestPage.getByRole('row').filter({ hasText: name })
        await expect(row.getByText('Shared with you')).toBeVisible()
        await row.getByRole('link', { name }).click()
        await expect(
            guestPage.getByRole('heading', { level: 1, name }),
        ).toBeVisible()
        await expect(
            guestPage.getByText('You can view this project'),
        ).toBeVisible()
        await expect(
            guestPage.getByRole('button', { name: 'Edit details' }),
        ).toHaveCount(0)
        await expect(
            guestPage.getByRole('button', { name: 'Delete' }),
        ).toHaveCount(0)
        await guestPage.goto(`${projectUrl}/editor`)
        await expect(guestPage.getByText('View-only access')).toBeVisible()
        // Hidden is not the control: the backend refuses the write itself.
        const status = await guestPage.evaluate<number>(
            `fetch('/api/v1${projectUrl}/publish', {
                method: 'POST',
                headers: { 'X-Akasha-CSRF': '1' },
            }).then((r) => r.status)`,
        )
        expect(status).toBe(403)
    })

    await test.step('promoted to editor, the editor opens', async () => {
        await ownerPage.reload()
        await ownerPage
            .getByRole('combobox', { name: `Role of ${guest.username}` })
            .selectOption('editor')
        await expect(
            ownerPage.getByText(`“${guest.username}” is now editor`),
        ).toBeVisible()
        await guestPage.goto(`${projectUrl}/editor`)
        await expect(
            guestPage.getByRole('heading', {
                level: 1,
                name: `Draft editor: ${name}`,
            }),
        ).toBeVisible()
        await expect(
            guestPage.getByRole('region', { name: /^Nodes/ }),
        ).toBeVisible()
        await expect(guestPage.getByText('View-only access')).toHaveCount(0)
    })

    await test.step('after the share ends, the project is gone for them', async () => {
        await ownerPage
            .getByRole('button', {
                name: `Stop sharing with ${guest.username}`,
            })
            .click()
        await ownerPage
            .getByRole('dialog')
            .getByRole('button', { name: 'Stop sharing' })
            .click()
        await expect(
            ownerPage.getByText('Not shared with anyone.'),
        ).toBeVisible()
        await guestPage.goto(projectUrl)
        await expect(
            guestPage.getByRole('heading', {
                level: 1,
                name: 'Project not found',
            }),
        ).toBeVisible()
    })
})

test('an owner hands a project over and then leaves it', async ({
    browser,
}) => {
    const owner = await member('owner')
    const heir = await member('heir')
    const ownerPage = await signedIn(browser, owner)
    const name = `Handover ${randomUUID().slice(0, 8)}`
    const id = await ownerPage.evaluate<string>(
        `fetch('/api/v1/projects', {
            method: 'POST',
            headers: { 'X-Akasha-CSRF': '1', 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: ${JSON.stringify(name)} }),
        }).then((r) => r.json()).then((p) => p.id)`,
    )
    const projectUrl = `/projects/${id}`

    await test.step('transfer ownership', async () => {
        await ownerPage.goto(projectUrl)
        await ownerPage
            .getByRole('button', { name: 'Transfer ownership' })
            .click()
        const form = ownerPage.getByRole('form', { name: 'Transfer ownership' })
        await form.getByLabel(/^Username/).fill(heir.username)
        await ownerPage
            .getByRole('dialog')
            .getByRole('button', { name: 'Transfer ownership' })
            .click()
        await expect(
            ownerPage.getByText(`“${heir.username}” now owns the project`),
        ).toBeVisible()
        // The former owner stays on as an editor, so no longer manages it.
        await expect(
            ownerPage.getByText('You can edit this project'),
        ).toBeVisible()
        await expect(
            ownerPage.getByRole('button', { name: 'Delete' }),
        ).toHaveCount(0)
    })

    await test.step('the new owner manages it', async () => {
        const heirPage = await signedIn(browser, heir)
        await heirPage.goto(projectUrl)
        await expect(
            heirPage.getByText('You manage this project'),
        ).toBeVisible()
        await expect(
            heirPage.getByRole('button', { name: 'Delete' }),
        ).toBeVisible()
    })

    await test.step('the former owner leaves', async () => {
        await ownerPage.getByRole('button', { name: 'Leave project' }).click()
        await ownerPage
            .getByRole('dialog')
            .getByRole('button', { name: 'Leave project' })
            .click()
        await expect(ownerPage.getByText('You left the project')).toBeVisible()
        await ownerPage.goto(projectUrl)
        await expect(
            ownerPage.getByRole('heading', {
                level: 1,
                name: 'Project not found',
            }),
        ).toBeVisible()
    })
})
