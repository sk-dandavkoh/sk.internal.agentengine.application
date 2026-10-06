import { randomUUID } from 'node:crypto'
import type { APIRequestContext } from '@playwright/test'
import { expect, test } from '../e2e/fixtures'
import { adminApi, signIn } from './session'

// What a member sees against the real backend: admin-only areas and other
// users' workflows are refused by the backend, and the UI shows that answer.
// Settings changes the member's password on the backend, which ends the
// member's other sessions. Plugins is checked from the admin's side.

let api: APIRequestContext

test.beforeAll(async ({ baseURL }) => {
    if (!baseURL) throw new Error('baseURL is not configured')
    api = await adminApi(baseURL)
})

test.afterAll(async () => {
    await api.dispose()
})

test('a member is refused admin areas and other workflows by the backend; Settings changes the password', async ({
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
    const adminWorkflow = (await (
        await api.post('/api/v1/workflows', {
            data: { name: `Admin only ${randomUUID().slice(0, 8)}` },
        })
    ).json()) as { id: string }

    // A second session of the member, to see it revoked later.
    const otherContext = await browser.newContext()
    const anonymous = otherContext.request
    const login = await anonymous.post('/api/v1/auth/login', { data: member })
    expect(login.status()).toBe(200)
    const otherToken = String(
        ((await login.json()) as { token: unknown }).token,
    )

    const context = await browser.newContext()
    const p = await context.newPage()
    await p.goto('/login')
    await p.getByLabel(/^Username/).fill(member.username)
    await p.getByLabel(/^Password/).fill(member.password)
    await p.getByRole('button', { name: 'Sign in' }).click()
    await expect(
        p.getByRole('heading', { level: 1, name: 'Dashboard' }),
    ).toBeVisible()

    for (const area of ['Credentials', 'Plugins']) {
        await test.step(`${area}: admin only`, async () => {
            const refused = p.waitForResponse(
                (r) =>
                    r.url().includes(`/api/v1/${area.toLowerCase()}`) &&
                    r.request().method() === 'GET',
            )
            await p.goto(`/${area.toLowerCase()}`)
            expect((await refused).status()).toBe(403)
            await expect(
                p.getByRole('heading', { name: 'Admin access required' }),
            ).toBeVisible()
        })
    }
    // The hidden form is not the control: the backend refuses the write,
    // sent from the page with its session cookie, as a crafted script would.
    const write = await p.evaluate<number>(
        `fetch('/api/v1/credentials', {
            method: 'POST',
            headers: { 'X-Akasha-CSRF': '1', 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: 'sneaky', value: 'not-a-real-secret' }),
        }).then((r) => r.status)`,
    )
    expect(write).toBe(403)

    await test.step("another user's workflow is not served", async () => {
        const detail = p.waitForResponse((r) =>
            r.url().endsWith(`/api/v1/workflows/${adminWorkflow.id}`),
        )
        await p.goto(`/workflows/${adminWorkflow.id}`)
        expect((await detail).status()).toBe(404)
        await expect(
            p.getByRole('heading', { level: 1, name: 'Workflow not found' }),
        ).toBeVisible()
    })

    await test.step('Settings: wrong current password, then a change', async () => {
        await p.goto('/settings')
        await expect(p.getByText(member.username).first()).toBeVisible()
        await expect(
            p.getByRole('main').getByText('Member', { exact: true }),
        ).toBeVisible()
        const form = p.getByRole('form', { name: 'Change password' })
        const next = randomUUID()

        await form.getByLabel(/^Current password/).fill('not the password')
        await form.getByLabel(/^New password/).fill(next)
        await form.getByLabel(/^Confirm new password/).fill(next)
        await form.getByRole('button', { name: 'Change password' }).click()
        // A field error, not a sign-out.
        await expect(form.getByLabel(/^Current password/)).toHaveAttribute(
            'aria-invalid',
            'true',
        )
        await expect(
            p.getByRole('heading', { level: 1, name: 'Settings' }),
        ).toBeVisible()

        await form.getByLabel(/^Current password/).fill(member.password)
        await form.getByRole('button', { name: 'Change password' }).click()
        await expect(p.getByText('Password changed')).toBeVisible()

        // This session continues; the other one is revoked by the backend.
        await p.reload()
        await expect(
            p.getByRole('heading', { level: 1, name: 'Settings' }),
        ).toBeVisible()
        const revoked = await anonymous.get('/api/v1/auth/session', {
            headers: { Authorization: `Bearer ${otherToken}` },
        })
        expect(revoked.status()).toBe(401)
        // The new password is the one the backend accepts.
        const relogin = await anonymous.post('/api/v1/auth/login', {
            data: { username: member.username, password: next },
        })
        expect(relogin.status()).toBe(200)
        await otherContext.close()
    })
    await context.close()
})

test('Plugins shows the backend plugin registry to an admin', async ({
    page: p,
}) => {
    await signIn(p)
    const listed = p.waitForResponse(
        (r) => r.url().endsWith('/api/v1/plugins') && r.ok(),
    )
    await p.goto('/plugins')
    const body = (await (await listed).json()) as {
        items: unknown[]
        install_enabled: boolean
    }
    await expect(
        p.getByRole('heading', { level: 1, name: 'Plugins' }),
    ).toBeVisible()
    await expect(p.getByText('Plugins run with full access')).toBeVisible()
    // What the page shows is what the backend answered.
    await expect(p.getByText('Uploads are off')).toBeVisible({
        visible: !body.install_enabled,
    })
    await expect(p.getByText('No plugins installed')).toBeVisible({
        visible: body.items.length === 0,
    })
})
