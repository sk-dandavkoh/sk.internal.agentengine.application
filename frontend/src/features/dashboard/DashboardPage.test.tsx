import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import {
    apiError,
    ok,
    pending,
    renderRoute,
    stubBackend,
    type Handler,
} from '../../test/app'

// -- factories (valid by default) -------------------------------------------------

const AT = '2026-10-02T08:00:00Z'
const STATUSES = [
    'created',
    'queued',
    'running',
    'paused',
    'waiting',
    'completed',
    'failed',
    'cancelled',
    'timeout',
] as const

function counts(overrides: Partial<Record<(typeof STATUSES)[number], number>>) {
    return {
        counts: Object.fromEntries(STATUSES.map((s) => [s, overrides[s] ?? 0])),
    }
}

function execution(id: string, overrides: Record<string, unknown> = {}) {
    return {
        id,
        project_id: 'proj_1',
        project_name: 'Invoice triage',
        version: 2,
        status: 'completed',
        trigger_type: 'manual',
        trigger_id: null,
        correlation_id: null,
        created_at: AT,
        started_at: '2026-10-02T08:00:01Z',
        finished_at: '2026-10-02T08:00:43Z',
        cancellable: false,
        ...overrides,
    }
}

function project(id: string, name: string, status = 'published') {
    return {
        id,
        name,
        description: '',
        status,
        owner_id: 'user_1',
        revision: 3,
        current_version: status === 'draft' ? 0 : 2,
        created_at: AT,
        updated_at: AT,
    }
}

const page = (items: unknown[], has_more = false) => ({
    items,
    page: { offset: 0, limit: 10, has_more },
})

function errorJson(code: string, message: string, node_id: string | null) {
    return {
        code,
        message,
        kind: 'permanent',
        node_id,
        details: {},
        retryable: false,
    }
}

function report(
    executionId: string,
    {
        error = errorJson('NODE_FAILED', 'Node "fetch" failed.', 'fetch'),
        nodeError = errorJson('HTTP_STATUS', 'upstream returned 502', 'fetch'),
    }: { error?: unknown; nodeError?: unknown } = {},
) {
    const usage = { input_tokens: 0, output_tokens: 0, total_tokens: 0 }
    return {
        schema_version: 1,
        execution_id: executionId,
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
            error,
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
                error: nodeError,
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
            usage,
            costs: [],
            cost_complete: true,
        },
        events: [],
        events_truncated: false,
    }
}

const system = (overrides: Record<string, unknown> = {}) => ({
    version: '0.27.0',
    ready: true,
    failures: {},
    ...overrides,
})

/** The executions listing, answered per `status` filter like the backend. */
function executionsBy(lists: {
    active?: unknown[]
    failures?: unknown[]
    recent?: unknown[]
}): Handler {
    return (url) => {
        const status = url.searchParams.getAll('status')
        if (status.includes('running')) return ok(page(lists.active ?? []))
        if (status.includes('failed')) return ok(page(lists.failures ?? []))
        return ok(page(lists.recent ?? []))
    }
}

function populated(): Record<string, Handler> {
    return {
        'GET /executions/status-counts': () =>
            ok(
                counts({
                    running: 1,
                    queued: 1,
                    completed: 5,
                    failed: 2,
                    timeout: 1,
                }),
            ),
        'GET /executions': executionsBy({
            active: [
                execution('exec_run', {
                    status: 'running',
                    finished_at: null,
                }),
                execution('exec_q', {
                    status: 'queued',
                    project_id: 'proj_unknown',
                    project_name: 'Beyond the project sample',
                    started_at: null,
                    finished_at: null,
                }),
            ],
            failures: [execution('exec_fail', { status: 'failed' })],
            recent: [execution('exec_ok')],
        }),
        'GET /system/status': () => ok(system()),
        'GET /projects': () =>
            ok(
                page([
                    project('proj_1', 'Invoice triage'),
                    project('proj_2', 'Weekly digest', 'draft'),
                    project('proj_3', 'Lead enrichment'),
                ]),
            ),
    }
}

function card(name: string) {
    const heading = screen.getByRole('heading', { level: 2, name })
    const section = heading.closest('section')
    if (!section) throw new Error(`no card ${name}`)
    return within(section)
}

async function renderDashboard(handlers: Record<string, Handler>) {
    const requests = stubBackend(handlers)
    const rendered = renderRoute('/dashboard')
    await screen.findByRole('heading', { level: 1, name: 'Dashboard' })
    return { ...rendered, requests }
}

// -- tests -------------------------------------------------------------------------

describe('Dashboard', () => {
    it('shows every section from real backend data', async () => {
        const { requests } = await renderDashboard(populated())

        const outcomes = card('Execution outcomes')
        expect(await outcomes.findByText('Active')).toBeInTheDocument()
        const value = (label: string) =>
            outcomes.getByText(label).nextElementSibling?.textContent
        expect(value('Active')).toBe('2')
        expect(value('Completed')).toBe('5')
        expect(value('Failed')).toBe('3')
        expect(outcomes.getByText('1 timed out')).toBeInTheDocument()
        expect(value('Cancelled')).toBe('0')

        const active = card('Active executions')
        const activeTable = await active.findByRole('table', {
            name: 'Active executions',
        })
        const [, running, queued] = within(activeTable).getAllByRole('row')
        expect(running).toHaveTextContent('Invoice triage')
        expect(running).toHaveTextContent('Running')
        expect(running).toHaveTextContent('In progress')
        // The listing names the project itself, even one outside the
        // dashboard's project sample; unstarted runs say so.
        expect(queued).toHaveTextContent('Beyond the project sample')
        expect(queued).toHaveTextContent('Queued')
        expect(queued).toHaveTextContent('Not started')

        const failures = card('Recent failures')
        expect(await failures.findByText('Failed')).toBeInTheDocument()

        const recent = card('Recent executions')
        const [, row] = await recent.findAllByRole('row')
        if (!row) throw new Error('no execution row')
        expect(row).toHaveTextContent('v2')
        expect(row).toHaveTextContent('manual')
        expect(row).toHaveTextContent('42 s')
        expect(within(row).getByText(/2026/)).toHaveAttribute('datetime', AT)

        const systemCard = card('System')
        expect(await systemCard.findByText('Ready')).toBeInTheDocument()
        expect(systemCard.getByText('0.27.0')).toBeInTheDocument()

        const projects = card('Projects')
        expect(await projects.findByText('Total')).toBeInTheDocument()
        expect(
            projects.getByText('Total').nextElementSibling,
        ).toHaveTextContent('3')
        expect(
            projects.getByText('Published').nextElementSibling,
        ).toHaveTextContent('2')
        expect(
            projects.getByText('Draft').nextElementSibling,
        ).toHaveTextContent('1')

        // The lists ask the backend for exactly what they show.
        const lists = requests
            .filter((u) => u.pathname === '/api/v1/executions')
            .map((u) => u.search)
        expect(lists).toEqual(
            expect.arrayContaining([
                '?status=created&status=queued&status=running&status=paused&status=waiting&limit=10',
                '?status=failed&status=timeout&limit=5',
                '?limit=10',
            ]),
        )
    })

    it('shows a loading state per section while requests are in flight', async () => {
        await renderDashboard({
            'GET /executions/status-counts': pending,
            'GET /executions': pending,
            'GET /system/status': pending,
            'GET /projects': pending,
        })

        for (const label of [
            'Loading execution counts…',
            'Loading active executions…',
            'Loading recent failures…',
            'Loading recent executions…',
            'Loading system status…',
            'Loading projects…',
        ])
            expect(await screen.findByText(label)).toHaveAttribute(
                'role',
                'status',
            )
        expect(screen.getByRole('button', { name: 'Refresh' })).toHaveAttribute(
            'aria-busy',
            'true',
        )
    })

    it('shows empty states when there is nothing yet', async () => {
        await renderDashboard({
            'GET /executions/status-counts': () => ok(counts({})),
            'GET /executions': executionsBy({}),
            'GET /system/status': () => ok(system()),
            'GET /projects': () => ok(page([])),
        })

        expect(
            await card('Execution outcomes').findByRole('heading', {
                name: 'No executions yet',
            }),
        ).toBeInTheDocument()
        expect(
            await card('Active executions').findByRole('heading', {
                name: 'No active executions',
            }),
        ).toBeInTheDocument()
        expect(
            await card('Recent failures').findByRole('heading', {
                name: 'No failed executions',
            }),
        ).toBeInTheDocument()
        expect(
            await card('Recent executions').findByRole('heading', {
                name: 'No executions yet',
            }),
        ).toBeInTheDocument()
        expect(
            await card('Projects').findByRole('heading', {
                name: 'No projects yet',
            }),
        ).toBeInTheDocument()
        expect(screen.queryByRole('table')).not.toBeInTheDocument()
    })

    it('isolates a failing section and recovers it on retry', async () => {
        let systemDown = true
        await renderDashboard({
            ...populated(),
            'GET /system/status': () =>
                systemDown
                    ? apiError(500, 'INTERNAL_ERROR', 'The engine failed.')
                    : ok(system()),
        })

        const systemCard = card('System')
        const alert = await systemCard.findByRole('alert')
        expect(alert).toHaveTextContent('Could not load system status')
        expect(alert).toHaveTextContent('The engine failed.')
        // The other sections still render their data.
        expect(await card('Projects').findByText('Total')).toBeInTheDocument()

        systemDown = false
        fireEvent.click(systemCard.getByRole('button', { name: 'Retry' }))

        expect(await systemCard.findByText('Ready')).toBeInTheDocument()
        expect(systemCard.queryByRole('alert')).not.toBeInTheDocument()
    })

    it('shows one page-level error when every section fails, and recovers', async () => {
        let down = true
        const all = populated()
        const handlers = Object.fromEntries(
            Object.entries(all).map(([key, handler]) => [
                key,
                ((url, init) =>
                    down
                        ? apiError(500, 'INTERNAL_ERROR', 'Engine failure.')
                        : handler(url, init)) satisfies Handler,
            ]),
        )
        await renderDashboard(handlers)

        // Sections may fail one by one first; once all have, one error remains.
        const alert = (
            await screen.findByText('Could not load the dashboard')
        ).closest('[role="alert"]')
        if (!(alert instanceof HTMLElement)) throw new Error('no alert')
        expect(alert).toHaveTextContent('Engine failure.')
        expect(screen.getAllByRole('alert')).toHaveLength(1)
        // The sections give way to it.
        expect(screen.getAllByRole('heading', { level: 2 })).toHaveLength(1)

        down = false
        fireEvent.click(within(alert).getByRole('button', { name: 'Retry' }))

        // The sections return once data arrives.
        await screen.findByText('Ready')
        expect(card('System').getByText('0.27.0')).toBeInTheDocument()
        expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    })

    it('shows the errors of a failure only when its row is expanded', async () => {
        const { requests } = await renderDashboard({
            ...populated(),
            'GET /reports/executions/exec_fail': () => ok(report('exec_fail')),
        })
        const failures = card('Recent failures')
        const toggle = await failures.findByRole('button', {
            name: 'Show error of execution exec_fail',
        })
        expect(toggle).toHaveAttribute('aria-expanded', 'false')
        // Nothing is fetched until asked for.
        expect(requests.some((u) => u.pathname.includes('/reports/'))).toBe(
            false,
        )

        fireEvent.click(toggle)

        expect(toggle).toHaveAttribute('aria-expanded', 'true')
        const details = document.getElementById(
            toggle.getAttribute('aria-controls') ?? '',
        )
        if (!details) throw new Error('no details region')
        const region = within(details)
        expect(
            await region.findByText('Node "fetch" failed.'),
        ).toBeInTheDocument()
        expect(region.getByText('NODE_FAILED')).toBeInTheDocument()
        expect(region.getByText('upstream returned 502')).toBeInTheDocument()
        expect(region.getByText(/Node Fetch invoices/)).toHaveTextContent(
            '(core.http, attempt 3)',
        )

        fireEvent.click(
            failures.getByRole('button', {
                name: 'Hide error of execution exec_fail',
            }),
        )
        expect(
            failures.queryByText('upstream returned 502'),
        ).not.toBeInTheDocument()
        expect(toggle).toHaveAttribute('aria-expanded', 'false')
    })

    it('reports a failed or empty error lookup inside the row', async () => {
        let available = false
        await renderDashboard({
            ...populated(),
            'GET /reports/executions/exec_fail': () =>
                available
                    ? ok(report('exec_fail', { error: null, nodeError: null }))
                    : apiError(500, 'INTERNAL_ERROR', 'The engine failed.'),
        })
        const failures = card('Recent failures')
        fireEvent.click(
            await failures.findByRole('button', { name: /Show error/ }),
        )

        const alert = await failures.findByRole('alert')
        expect(alert).toHaveTextContent('Could not load error details')
        // The rest of the dashboard is unaffected.
        expect(card('System').getByText('Ready')).toBeInTheDocument()

        available = true
        fireEvent.click(within(alert).getByRole('button', { name: 'Retry' }))
        expect(
            await failures.findByText(
                'No error details were recorded for this execution.',
            ),
        ).toBeInTheDocument()
    })

    it('reports a system that is not ready with its failing checks', async () => {
        await renderDashboard({
            ...populated(),
            'GET /system/status': () =>
                ok(
                    system({
                        ready: false,
                        failures: { redis: 'unreachable' },
                    }),
                ),
        })

        const systemCard = card('System')
        expect(await systemCard.findByText('Not ready')).toBeInTheDocument()
        expect(
            systemCard.getByText('redis').nextElementSibling,
        ).toHaveTextContent('unreachable')
    })

    it('refreshes all sections and keeps earlier data when a refresh fails', async () => {
        let failing = false
        let countCalls = 0
        await renderDashboard({
            ...populated(),
            'GET /executions/status-counts': () => {
                countCalls++
                return failing
                    ? apiError(500, 'INTERNAL_ERROR', 'The engine failed.')
                    : ok(counts({ completed: 5 }))
            },
        })
        const outcomes = card('Execution outcomes')
        expect(await outcomes.findByText('Completed')).toBeInTheDocument()

        failing = true
        const refresh = screen.getByRole('button', { name: 'Refresh' })
        await waitFor(() =>
            expect(refresh).not.toHaveAttribute('aria-busy', 'true'),
        )
        fireEvent.click(refresh)

        // 500 is not retried (queryClient.ts); the earlier data stays visible.
        const warning = await outcomes.findByRole('status')
        expect(warning).toHaveTextContent(
            'Could not refresh execution counts; showing earlier data.',
        )
        expect(
            outcomes.getByText('Completed').nextElementSibling,
        ).toHaveTextContent('5')
        expect(countCalls).toBeGreaterThan(1)
    })

    it('notes when a list is truncated or project counts are partial', async () => {
        await renderDashboard({
            ...populated(),
            'GET /executions': () => ok(page([execution('exec_1')], true)),
            'GET /projects': () => ok(page([project('proj_1', 'A')], true)),
        })

        expect(
            await card('Recent executions').findByText('Showing the newest 1.'),
        ).toBeInTheDocument()
        const projects = card('Projects')
        expect(await projects.findByText('1+')).toBeInTheDocument()
        expect(
            projects.getByText('Status counts cover the first 1 projects.'),
        ).toBeInTheDocument()
    })

    it.each([
        ['Recent executions', 'All executions', '/executions', 'Executions'],
        ['Projects', 'All projects', '/projects', 'Projects'],
    ])('%s links to %s', async (cardName, linkName, path, heading) => {
        const { router } = await renderDashboard(populated())

        fireEvent.click(card(cardName).getByRole('link', { name: linkName }))

        expect(
            await screen.findByRole('heading', { level: 1, name: heading }),
        ).toBeInTheDocument()
        expect(router.state.location.pathname).toBe(path)
    })

    it('offers the way to create a first project', async () => {
        const { router } = await renderDashboard({
            ...populated(),
            'GET /projects': () => ok(page([])),
        })

        fireEvent.click(
            await card('Projects').findByRole('link', {
                name: 'Go to Projects',
            }),
        )

        await screen.findByRole('heading', { level: 1, name: 'Projects' })
        expect(router.state.location.pathname).toBe('/projects')
    })
})
