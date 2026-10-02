import { expect, test, type Page } from '@playwright/test'

// Layout needs a real browser: jsdom has no CSS. The backend is stubbed at
// the network boundary so the bundle runs its real API client.
const AT = '2026-10-02T08:00:00Z'
const execution = (id: string, status: string) => ({
    id,
    project_id: 'proj_1',
    version: 2,
    status,
    trigger_type: 'manual',
    trigger_id: null,
    correlation_id: null,
    created_at: AT,
    started_at: AT,
    finished_at: status === 'running' ? null : '2026-10-02T08:01:05Z',
})
const page = (items: unknown[]) => ({
    items,
    page: { offset: 0, limit: 10, has_more: false },
})
const STATUSES = ['created', 'queued', 'running', 'paused', 'waiting']
const replies: Record<string, unknown> = {
    '/api/v1/executions/status-counts': {
        counts: {
            ...Object.fromEntries(STATUSES.map((s) => [s, 0])),
            running: 1,
            completed: 12,
            failed: 2,
            cancelled: 1,
            timeout: 0,
        },
    },
    '/api/v1/system/status': { version: '0.27.0', ready: true, failures: {} },
    '/api/v1/projects': page([
        {
            id: 'proj_1',
            name: 'Invoice triage with a rather long project name',
            description: '',
            status: 'published',
            owner_id: 'user_1',
            revision: 3,
            current_version: 2,
            created_at: AT,
            updated_at: AT,
        },
    ]),
}

const failureError = {
    code: 'HTTP_STATUS',
    message:
        'upstream returned 502 Bad Gateway after three attempts; the long message must wrap inside the visible area',
    kind: 'transient',
    node_id: 'fetch',
    details: {},
    retryable: true,
}
const failureReport = {
    schema_version: 1,
    execution_id: 'exec_f',
    project: {
        id: 'proj_1',
        name: 'Invoice triage',
        version: 2,
        version_description: '',
        published_at: AT,
    },
    execution: {
        status: 'failed',
        trigger_type: 'manual',
        trigger_id: null,
        correlation_id: null,
        engine_version: '0.27.0',
        node_versions: {},
        created_at: AT,
        started_at: AT,
        finished_at: AT,
        duration_ms: 42,
        input: {},
        error: null,
        cancellation: null,
    },
    nodes: [
        {
            node_id: 'fetch',
            node_type: 'core.http',
            label: 'Fetch invoices',
            status: 'failed',
            attempts: 3,
            retries: 2,
            started_at: AT,
            finished_at: AT,
            duration_ms: 40,
            error: failureError,
            failures: [],
            outputs: null,
            usage: null,
            cost: null,
        },
    ],
    metrics: {
        node_status_counts: { failed: 1 },
        attempts: 3,
        retries: 2,
        failed_attempts: 3,
        usage: { input_tokens: 0, output_tokens: 0, total_tokens: 0 },
        costs: [],
        cost_complete: true,
    },
    events: [],
    events_truncated: false,
}

async function stubBackend(p: Page) {
    await p.route('**/api/v1/**', (route) => {
        const url = new URL(route.request().url())
        const statuses = url.searchParams.getAll('status')
        const body =
            url.pathname === '/api/v1/executions'
                ? page(
                      statuses.includes('running')
                          ? [execution('exec_a', 'running')]
                          : statuses.includes('failed')
                            ? [execution('exec_f', 'failed')]
                            : [
                                  execution('exec_c', 'completed'),
                                  execution('exec_f', 'failed'),
                              ],
                  )
                : url.pathname.startsWith('/api/v1/reports/')
                  ? failureReport
                  : replies[url.pathname]
        return body === undefined
            ? route.fulfill({ status: 404 })
            : route.fulfill({ json: body })
    })
}

async function openDashboard(p: Page) {
    await stubBackend(p)
    await p.goto('/dashboard')
    await expect(p.getByText('Ready', { exact: true })).toBeVisible()
    await expect(
        p.getByRole('table', { name: 'Recent executions' }),
    ).toBeVisible()
}

const box = async (p: Page, heading: string) => {
    const b = await p
        .getByRole('heading', { level: 2, name: heading })
        .boundingBox()
    if (!b) throw new Error(`${heading} not rendered`)
    return b
}

test('desktop: overview beside system and projects', async ({ page: p }) => {
    await p.setViewportSize({ width: 1440, height: 900 })
    await openDashboard(p)

    const outcomes = await box(p, 'Execution outcomes')
    const system = await box(p, 'System')
    expect(system.x).toBeGreaterThan(outcomes.x + outcomes.width)
    expect(Math.abs(system.y - outcomes.y)).toBeLessThan(5)
})

test('mobile: sections stack and the page never scrolls sideways', async ({
    page: p,
}) => {
    await p.setViewportSize({ width: 375, height: 812 })
    await openDashboard(p)

    const outcomes = await box(p, 'Execution outcomes')
    const system = await box(p, 'System')
    expect(Math.abs(system.x - outcomes.x)).toBeLessThan(5)
    expect(system.y).toBeGreaterThan(outcomes.y)
    // A string: this tsconfig has no DOM types; it runs in the page.
    const overflow = await p.evaluate<number>(
        'document.documentElement.scrollWidth - window.innerWidth',
    )
    expect(overflow).toBeLessThanOrEqual(0)
})

test('every interactive element is reachable by keyboard', async ({
    page: p,
}) => {
    await openDashboard(p)

    const reached = new Set<string>()
    for (let i = 0; i < 30; i++) {
        await p.keyboard.press('Tab')
        reached.add(
            await p.evaluate<string>(
                'document.activeElement?.textContent ?? ""',
            ),
        )
    }
    for (const name of ['Refresh', 'All executions', 'All projects'])
        expect(reached).toContain(name)
})

test('an unreachable backend shows one error, not one per section', async ({
    page: p,
}) => {
    await p.route('**/api/v1/**', (route) => route.abort('connectionrefused'))
    await p.goto('/dashboard')

    // Network errors are retried first (queryClient.ts), then collapse.
    const alert = p.getByRole('alert')
    await expect(alert).toHaveText(/Could not load the dashboard/, {
        timeout: 15_000,
    })
    await expect(alert).toHaveCount(1)
    await expect(alert).toContainText('The server could not be reached.')
})

test('mobile: expanded error details stay within the visible area', async ({
    page: p,
}) => {
    await p.setViewportSize({ width: 375, height: 812 })
    await openDashboard(p)

    const toggle = p.getByRole('button', {
        name: 'Show error of execution exec_f',
    })
    await toggle.click()
    const details = p.locator('#error-exec_f')
    await expect(details).toContainText('upstream returned 502')
    await expect(
        p.getByRole('button', { name: 'Hide error of execution exec_f' }),
    ).toHaveAttribute('aria-expanded', 'true')

    const box = await details.boundingBox()
    if (!box) throw new Error('details not rendered')
    expect(box.x).toBeGreaterThanOrEqual(0)
    expect(box.x + box.width).toBeLessThanOrEqual(375)
})
