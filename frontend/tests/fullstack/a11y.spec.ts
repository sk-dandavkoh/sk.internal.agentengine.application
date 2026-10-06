import { readFileSync } from 'node:fs'
import { randomUUID } from 'node:crypto'
import { request as http, type APIRequestContext } from '@playwright/test'
import { expectAccessible, expectNoHorizontalScroll } from '../e2e/a11y'
import { expect, test } from '../e2e/fixtures'

// Every screen, filled with real data from the real backend, at the
// supported widths and in both color schemes: no axe violation and no
// page-level horizontal scroll. Keyboard behavior is tested per workflow
// (tests/e2e); this sweep catches what a new screen or token breaks.

const UI_HEADERS = { 'X-Akasha-CSRF': '1' }
const admin = () => ({
    username: 'e2e-admin',
    password: process.env.AKASHA_E2E_ADMIN_PASSWORD ?? '',
})

let api: APIRequestContext
let routes: string[] = []

test.beforeAll(async ({ baseURL }) => {
    if (!baseURL) throw new Error('baseURL is not configured')
    const anonymous = await http.newContext({ baseURL })
    // 409: another spec of this run already created the admin.
    expect([201, 409]).toContain(
        (
            await anonymous.post('/api/v1/auth/setup', { data: admin() })
        ).status(),
    )
    const login = await anonymous.post('/api/v1/auth/login', { data: admin() })
    const token = String(((await login.json()) as { token: unknown }).token)
    await anonymous.dispose()
    api = await http.newContext({
        baseURL,
        extraHTTPHeaders: { Authorization: `Bearer ${token}` },
    })

    // A published, executed workflow, a credential and a trigger, so lists,
    // details, graphs and reports have content.
    const example = JSON.parse(
        readFileSync(
            new URL(
                '../../../backend/examples/workflows/hello-world.json',
                import.meta.url,
            ),
            'utf8',
        ),
    ) as { graph: unknown; triggers: unknown; dependencies: unknown }
    const created = await api.post('/api/v1/workflows', {
        data: { name: `A11y sweep ${randomUUID().slice(0, 8)}` },
    })
    const workflow = (await created.json()) as {
        id: string
        name: string
        revision: number
    }
    expect(
        (
            await api.put(`/api/v1/workflows/${workflow.id}`, {
                data: {
                    expected_revision: workflow.revision,
                    name: workflow.name,
                    draft: {
                        graph: example.graph,
                        triggers: example.triggers,
                        dependencies: example.dependencies,
                    },
                },
            })
        ).status(),
    ).toBe(200)
    expect(
        (await api.post(`/api/v1/workflows/${workflow.id}/publish`)).status(),
    ).toBe(201)
    const run = await api.post(
        `/api/v1/workflows/${workflow.id}/versions/1/execute`,
        { data: { name: 'World' } },
    )
    const execution = (await run.json()) as { id: string }
    await expect
        .poll(
            async () =>
                (
                    (await (
                        await api.get(`/api/v1/executions/${execution.id}`)
                    ).json()) as { status: string }
                ).status,
        )
        .toBe('completed')
    expect(
        (
            await api.post('/api/v1/credentials', {
                data: {
                    name: `a11y_${randomUUID().slice(0, 8)}`,
                    value: 'not-a-real-secret',
                },
            })
        ).status(),
    ).toBe(201)

    const p = `/workflows/${workflow.id}`
    const e = execution.id
    routes = [
        '/dashboard',
        '/workflows',
        p,
        `${p}/editor`,
        `${p}/versions`,
        `${p}/versions/1`,
        `${p}/triggers`,
        '/executions',
        `/executions/${e}`,
        '/monitoring',
        '/reports',
        `/reports/${e}`,
        '/credentials',
        '/plugins',
        '/settings',
        '/no-such-page',
    ]
})

test.afterAll(async () => {
    await api.dispose()
})

// Phone, tablet, small laptop, desktop.
const WIDTHS = [390, 768, 1024, 1440]

for (const colorScheme of ['light', 'dark'] as const)
    test(`every screen is accessible and fits, ${colorScheme}`, async ({
        page,
    }) => {
        test.setTimeout(240_000)
        await page.emulateMedia({ colorScheme })
        const login = await page.request.post('/api/v1/auth/login', {
            data: { ...admin(), cookie: true },
            headers: UI_HEADERS,
        })
        expect(login.status()).toBe(200)
        for (const width of WIDTHS) {
            await page.setViewportSize({ width, height: 900 })
            for (const route of routes)
                await test.step(`${route} at ${String(width)} px`, async () => {
                    await page.goto(route)
                    await expect(
                        page.getByRole('heading', { level: 1 }),
                    ).toBeVisible()
                    // Lazy sections (charts, catalogs) settle first.
                    await page.waitForLoadState('networkidle')
                    await expectNoHorizontalScroll(page)
                    await expectAccessible(page)
                })
        }
    })
