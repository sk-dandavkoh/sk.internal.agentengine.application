import { randomUUID } from 'node:crypto'
import type { APIRequestContext, Browser, Page } from '@playwright/test'
import { expect, test } from '../e2e/fixtures'
import { admin, adminApi } from './session'

// User administration against the real backend: every refusal and every
// sign-in below is the backend's answer, not something the UI decided.

let api: APIRequestContext

test.beforeAll(async ({ baseURL }) => {
    if (!baseURL) throw new Error('baseURL is not configured')
    api = await adminApi(baseURL)
})

test.afterAll(async () => {
    await api.dispose()
})

async function signInThroughUi(page: Page, username: string, password: string) {
    await page.goto('/login')
    await page.getByLabel(/^Username/).fill(username)
    await page.getByLabel(/^Password/).fill(password)
    await page.getByRole('button', { name: 'Sign in' }).click()
}

/** A fresh, signed-out browser trying to sign in: the login's status. */
async function tryLogin(browser: Browser, username: string, password: string) {
    const context = await browser.newContext()
    const page = await context.newPage()
    const answer = page.waitForResponse((r) =>
        r.url().endsWith('/api/v1/auth/login'),
    )
    await signInThroughUi(page, username, password)
    const status = (await answer).status()
    if (status === 200)
        await expect(
            page.getByRole('heading', { level: 1, name: 'Dashboard' }),
        ).toBeVisible()
    else await expect(page.getByRole('alert')).toBeVisible()
    await context.close()
    return status
}

test('an admin creates, edits, disables, re-enables and resets a user', async ({
    page,
    browser,
}) => {
    const username = `user-${randomUUID().slice(0, 8)}`
    const initial = randomUUID()
    const name = `Pat ${username}`

    await signInThroughUi(page, admin().username, admin().password)
    await page
        .getByRole('navigation', { name: 'Main' })
        .getByRole('link', { name: 'Users' })
        .click()
    await expect(
        page.getByRole('heading', { level: 1, name: 'Users' }),
    ).toBeVisible()

    await test.step('create', async () => {
        await page.getByRole('button', { name: 'New user' }).first().click()
        const form = page.getByRole('form', { name: 'New user' })
        await form.getByLabel(/^Username/).fill(username)
        await form.getByLabel(/^Display name/).fill(name)
        await form.getByLabel(/^Initial password/).fill(initial)
        await form.getByLabel(/^Confirm initial password/).fill(initial)
        await page.getByRole('button', { name: 'Create user' }).click()
        await expect(page.getByText(`User “${username}” created`)).toBeVisible()
        await expect(
            page.getByRole('cell', { name, exact: true }),
        ).toBeVisible()
    })

    const row = page.getByRole('row').filter({ hasText: username })

    await test.step('edit and promote', async () => {
        await page.getByRole('button', { name: `Edit ${name}` }).click()
        const form = page.getByRole('form', { name: 'Edit user' })
        await form.getByLabel(/^Display name/).fill(`${name} II`)
        await form.getByLabel('Role').selectOption('admin')
        await expect(form.getByText('Change administrator role?')).toBeVisible()
        await page.getByRole('button', { name: 'Change role and save' }).click()
        await expect(page.getByText(`User “${username}” updated`)).toBeVisible()
        await expect(row.getByText('Administrator')).toBeVisible()
        await expect(row.getByText(`${name} II`)).toBeVisible()
    })
    const renamed = `${name} II`

    await test.step('disable: sign-in is refused', async () => {
        await page.getByRole('button', { name: `Disable ${renamed}` }).click()
        await page.getByRole('button', { name: 'Disable user' }).click()
        await expect(
            page.getByText(`User “${username}” disabled`),
        ).toBeVisible()
        await expect(row.getByText('Disabled')).toBeVisible()
        expect(await tryLogin(browser, username, initial)).toBe(401)
    })

    await test.step('re-enable: sign-in works again', async () => {
        await page.getByRole('button', { name: `Enable ${renamed}` }).click()
        await page.getByRole('button', { name: 'Enable user' }).click()
        await expect(page.getByText(`User “${username}” enabled`)).toBeVisible()
        await expect(row.getByText('Active')).toBeVisible()
        expect(await tryLogin(browser, username, initial)).toBe(200)
        await page.reload()
        await expect(row.getByRole('cell', { name: 'Never' })).toHaveCount(0)
    })

    await test.step('reset password: only the new one works', async () => {
        const next = randomUUID()
        await page
            .getByRole('button', { name: `Reset password of ${renamed}` })
            .click()
        const form = page.getByRole('form', { name: 'Reset password' })
        await form.getByLabel(/^New password/).fill(next)
        await form.getByLabel(/^Confirm new password/).fill(next)
        await page
            .getByRole('dialog')
            .getByRole('button', { name: 'Reset password', exact: true })
            .click()
        await expect(
            page.getByText(`Password of “${username}” reset`),
        ).toBeVisible()
        expect(await tryLogin(browser, username, initial)).toBe(401)
        expect(await tryLogin(browser, username, next)).toBe(200)
    })

    await test.step('the admin cannot disable themselves', async () => {
        const mine = page.getByRole('row').filter({ hasText: '(you)' })
        await expect(mine).toBeVisible()
        await expect(
            mine.getByRole('button', { name: /^Disable/ }),
        ).toHaveCount(0)
        // And the backend refuses it when asked directly.
        const me = (await (await api.get('/api/v1/auth/me')).json()) as {
            id: string
        }
        const refused = await api.patch(`/api/v1/users/${me.id}`, {
            data: { disabled: true },
        })
        expect(refused.status()).toBe(409)
    })
})

test('a member gets no Users area, from the UI or the API', async ({
    browser,
}) => {
    const member = {
        username: `member-${randomUUID().slice(0, 8)}`,
        password: randomUUID(),
    }
    expect(
        (
            await api.post('/api/v1/users', {
                data: { ...member, role: 'member' },
            })
        ).status(),
    ).toBe(201)
    const context = await browser.newContext()
    const page = await context.newPage()
    await signInThroughUi(page, member.username, member.password)
    await expect(
        page.getByRole('heading', { level: 1, name: 'Dashboard' }),
    ).toBeVisible()
    await expect(
        page
            .getByRole('navigation', { name: 'Main' })
            .getByRole('link', { name: 'Users' }),
    ).toHaveCount(0)

    await page.goto('/users')
    await expect(
        page.getByRole('heading', { name: 'Admin access required' }),
    ).toBeVisible()
    await expect(page.getByRole('table')).toHaveCount(0)

    // Hiding the page is not the control: the backend refuses the calls.
    const statuses = await page.evaluate<number[]>(`Promise.all([
        fetch('/api/v1/users', { headers: { 'X-Akasha-CSRF': '1' } }),
        fetch('/api/v1/users', {
            method: 'POST',
            headers: { 'X-Akasha-CSRF': '1', 'Content-Type': 'application/json' },
            body: JSON.stringify({ username: 'sneaky', password: 'sneaky-password-1', role: 'admin' }),
        }),
    ]).then((rs) => rs.map((r) => r.status))`)
    expect(statuses).toEqual([403, 403])
    await context.close()
})
