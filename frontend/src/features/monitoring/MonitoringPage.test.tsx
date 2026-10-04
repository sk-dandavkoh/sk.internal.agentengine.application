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
type Status = (typeof STATUSES)[number]

const counts = (overrides: Partial<Record<Status, number>> = {}) =>
    Object.fromEntries(STATUSES.map((s) => [s, overrides[s] ?? 0]))

function metrics(overrides: Record<string, unknown> = {}) {
    return {
        node_status_counts: { completed: 0, failed: 0 },
        attempts: 0,
        retries: 0,
        failed_attempts: 0,
        usage: { input_tokens: 0, output_tokens: 0, total_tokens: 0 },
        costs: [],
        cost_complete: true,
        ...overrides,
    }
}

const stats = (ms: number, count = 1) => ({
    count,
    mean_ms: ms,
    p50_ms: ms,
    p95_ms: ms,
    max_ms: ms,
})

function bucket(i: number, c: Partial<Record<Status, number>> = {}) {
    const start = Date.parse('2026-10-04T10:00:00Z') + i * 900_000
    return {
        start: new Date(start).toISOString(),
        end: new Date(start + 900_000).toISOString(),
        counts: counts(c),
    }
}

function summary(overrides: Record<string, unknown> = {}) {
    return {
        since: '2026-10-03T10:00:00Z',
        until: '2026-10-04T10:00:00Z',
        bucket_seconds: 900,
        executions: 0,
        status_counts: counts(),
        durations: {},
        timeline: [bucket(0)],
        errors: [],
        errors_total: 0,
        nodes: [],
        totals: metrics(),
        ...overrides,
    }
}

function populated() {
    return summary({
        executions: 50,
        status_counts: counts({
            completed: 40,
            failed: 4,
            timeout: 2,
            cancelled: 1,
            running: 3,
        }),
        durations: { completed: stats(1_500, 40), failed: stats(42_000, 4) },
        timeline: [
            bucket(0, { completed: 30, failed: 4 }),
            bucket(1, { completed: 10, timeout: 2, cancelled: 1, running: 3 }),
        ],
        errors: [
            { code: 'HTTP_STATUS', kind: 'permanent', count: 3 },
            { code: 'NODE_TIMEOUT', kind: 'timeout', count: 2 },
        ],
        errors_total: 6,
        nodes: [
            {
                node_type: 'core.http',
                metrics: metrics({
                    node_status_counts: { completed: 45, failed: 5 },
                    attempts: 57,
                    retries: 7,
                    failed_attempts: 12,
                }),
                durations: stats(850, 50),
            },
            {
                node_type: 'core.llm',
                metrics: metrics({
                    node_status_counts: { completed: 40 },
                    attempts: 40,
                    usage: {
                        input_tokens: 9_000,
                        output_tokens: 3_000,
                        total_tokens: 12_000,
                    },
                    costs: [{ amount: 0.0042, currency: 'USD' }],
                }),
                durations: stats(2_300, 40),
            },
        ],
        totals: metrics({
            attempts: 97,
            retries: 7,
            failed_attempts: 12,
            usage: {
                input_tokens: 9_000,
                output_tokens: 3_000,
                total_tokens: 12_000,
            },
            costs: [{ amount: 0.0042, currency: 'USD' }],
        }),
    })
}

const now = () => ok({ counts: counts({ running: 3, queued: 2 }) })
const system = () => ok({ version: '0.27.0', ready: true, failures: {} })

async function renderMonitoring(
    handlers: Record<string, Handler>,
    path = '/monitoring',
) {
    const requests = stubBackend({
        'GET /executions/status-counts': now,
        'GET /system/status': system,
        ...handlers,
    })
    const rendered = renderRoute(path)
    await screen.findByRole('heading', { level: 1, name: 'Monitoring' })
    return { ...rendered, requests }
}

function card(name: string) {
    const heading = screen.getByRole('heading', { level: 2, name })
    const section = heading.closest('section')
    if (!section) throw new Error(`no card ${name}`)
    return within(section)
}

const stat = (label: string) => {
    const term = screen.getByText(label, { selector: 'dt' })
    return term.parentElement?.textContent ?? ''
}

const summaryRequests = (requests: URL[]) =>
    requests.filter((u) => u.pathname === '/api/v1/monitoring/summary')

// -- tests -------------------------------------------------------------------------

describe('Monitoring', () => {
    it('shows backend metrics with their units and denominators', async () => {
        const { requests } = await renderMonitoring({
            'GET /monitoring/summary': () => ok(populated()),
        })

        await screen.findByText('Success rate')
        // Rates are over finished runs (47), not all 50 created.
        expect(stat('Success rate')).toContain('85.1 %')
        expect(stat('Success rate')).toContain('40 completed of 47 finished')
        expect(stat('Failure rate')).toContain('12.8 %')
        expect(stat('Failure rate')).toContain('6 failed or timed out')
        expect(stat('Executions created')).toContain('3 not finished yet')
        expect(stat('Node retries')).toContain('12 failed of 97 attempts')
        expect(stat('Tokens')).toMatch(/12.000/)
        expect(stat('Estimated cost')).toContain('0.0042')
        expect(stat('Estimated cost')).toContain('List-price estimate')

        const timeline = card('Executions over time')
        expect(timeline.getByText(/Peak: 34 per 15 min/)).toBeInTheDocument()
        const table = timeline.getByRole('table', {
            name: 'Executions created per 15 min',
        })
        const [, first, second] = within(table).getAllByRole('row')
        expect(first).toHaveTextContent(/30\s*4\s*0\s*0\s*34/)
        expect(second).toHaveTextContent(/10\s*2\s*1\s*3\s*16/)

        const durations = card('Execution durations')
        const [, completed, failed] = durations.getAllByRole('row')
        expect(completed).toHaveTextContent('Completed')
        expect(completed).toHaveTextContent('1 s')
        expect(failed).toHaveTextContent('42 s')

        const errors = card('Errors')
        expect(errors.getByText('HTTP_STATUS')).toBeInTheDocument()
        expect(errors.getByText('1 more with other codes.')).toBeInTheDocument()

        const nodes = card('Node performance')
        const [, http, llm] = nodes.getAllByRole('row')
        expect(http).toHaveTextContent('core.http')
        expect(http).toHaveTextContent(/45\s*5\s*7\s*850 ms/)
        expect(llm).toHaveTextContent('core.llm')
        expect(llm).toHaveTextContent('0.0042')

        // Current state comes from its own endpoint, not the window.
        const right = card('Right now')
        expect(right.getByText('Running').nextElementSibling).toHaveTextContent(
            '3',
        )
        expect(await card('System').findByText('Ready')).toBeInTheDocument()

        const [request] = summaryRequests(requests)
        const since = Date.parse(request?.searchParams.get('since') ?? '')
        const until = Date.parse(request?.searchParams.get('until') ?? '')
        expect(until - since).toBe(86_400_000) // the default: last 24 hours
    })

    it('shows an empty state when the window has no executions', async () => {
        await renderMonitoring({
            'GET /monitoring/summary': () => ok(summary()),
        })
        expect(
            await screen.findByText('No executions in this time range'),
        ).toBeInTheDocument()
        expect(screen.queryByText('Success rate')).not.toBeInTheDocument()
        // The current state still shows.
        expect(card('Right now').getByText('Queued')).toBeInTheDocument()
    })

    it('handles partial data: unfinished runs, no nodes, no cost', async () => {
        await renderMonitoring({
            'GET /monitoring/summary': () =>
                ok(
                    summary({
                        executions: 2,
                        status_counts: counts({ queued: 2 }),
                        timeline: [bucket(0, { queued: 2 })],
                        totals: metrics({
                            usage: {
                                input_tokens: 5,
                                output_tokens: 5,
                                total_tokens: 10,
                            },
                            cost_complete: false,
                        }),
                    }),
                ),
        })

        await screen.findByText('Success rate')
        // Nothing finished: no rate rather than a misleading 0 %.
        expect(stat('Success rate')).toContain('—')
        expect(stat('Failure rate')).toContain('—')
        expect(stat('Estimated cost')).toContain(
            'Incomplete: some nodes reported tokens without a price.',
        )
        expect(
            card('Execution durations').getByText(
                'No finished executions in this window.',
            ),
        ).toBeInTheDocument()
        expect(
            card('Errors').getByText('No failed executions in this window.'),
        ).toBeInTheDocument()
        expect(
            card('Node performance').getByText(
                'No node executions recorded in this window.',
            ),
        ).toBeInTheDocument()
    })

    it('shows loading, then a recoverable error per section', async () => {
        let fail = true
        await renderMonitoring({
            'GET /monitoring/summary': () =>
                fail
                    ? apiError(500, 'INTERNAL_ERROR', 'The server failed.')
                    : ok(populated()),
            'GET /system/status': pending,
        })
        expect(screen.getByText('Loading system status…')).toBeInTheDocument()

        const alert = await screen.findByRole('alert')
        expect(alert).toHaveTextContent('Could not load monitoring data')
        // Other sections keep working.
        expect(card('Right now').getByText('Running')).toBeInTheDocument()

        fail = false
        fireEvent.click(within(alert).getByRole('button', { name: 'Retry' }))
        expect(await screen.findByText('Success rate')).toBeInTheDocument()
    })

    it('rejects malformed backend data instead of rendering it', async () => {
        await renderMonitoring({
            'GET /monitoring/summary': () =>
                ok({ ...populated(), executions: 'many' }),
        })
        expect(await screen.findByRole('alert')).toHaveTextContent(
            'Could not load monitoring data',
        )
    })

    it('renders a large window and many node types', async () => {
        const nodes = Array.from({ length: 200 }, (_, i) => ({
            node_type: `plugin.node_${String(i).padStart(3, '0')}`,
            metrics: metrics({ node_status_counts: { completed: i } }),
            durations: i % 2 ? stats(i) : null,
        }))
        await renderMonitoring(
            {
                'GET /monitoring/summary': () =>
                    ok(
                        summary({
                            bucket_seconds: 86_400,
                            executions: 100_000,
                            status_counts: counts({ completed: 100_000 }),
                            timeline: Array.from({ length: 100 }, (_, i) =>
                                bucket(i, { completed: 1_000 }),
                            ),
                            nodes,
                        }),
                    ),
            },
            '/monitoring?range=7d',
        )
        await screen.findByText('Success rate')
        expect(stat('Executions created')).toMatch(/100.000/)
        const timeline = card('Executions over time')
        expect(
            within(
                timeline.getByRole('table', {
                    name: 'Executions created per 1 day',
                }),
            ).getAllByRole('row'),
        ).toHaveLength(101)
        expect(card('Node performance').getAllByRole('row')).toHaveLength(201)
        expect(screen.getByLabelText('Time range')).toHaveValue('7d')
    })

    it('asks for each preset window and validates a custom one', async () => {
        const { requests, router } = await renderMonitoring({
            'GET /monitoring/summary': () => ok(summary()),
        })
        await screen.findByText('No executions in this time range')

        fireEvent.change(screen.getByLabelText('Time range'), {
            target: { value: '1h' },
        })
        await waitFor(() => {
            const last = summaryRequests(requests).at(-1)
            const span =
                Date.parse(last?.searchParams.get('until') ?? '') -
                Date.parse(last?.searchParams.get('since') ?? '')
            expect(span).toBe(3_600_000)
        })
        expect(router.state.location.search).toBe('?range=1h')

        fireEvent.change(screen.getByLabelText('Time range'), {
            target: { value: 'custom' },
        })
        const from = await screen.findByLabelText(/^From/)
        const to = screen.getByLabelText(/^To\b/)
        fireEvent.change(from, { target: { value: '2026-10-02T00:00' } })
        fireEvent.change(to, { target: { value: '2026-10-01T00:00' } })
        fireEvent.click(screen.getByRole('button', { name: 'Apply' }))
        expect(
            await screen.findByText('The end must be after the start.'),
        ).toBeInTheDocument()

        const before = summaryRequests(requests).length
        fireEvent.change(to, { target: { value: '2026-10-03T00:00' } })
        fireEvent.click(screen.getByRole('button', { name: 'Apply' }))
        await waitFor(() => {
            expect(summaryRequests(requests).length).toBeGreaterThan(before)
        })
        const custom = summaryRequests(requests).at(-1)
        expect(
            Date.parse(custom?.searchParams.get('until') ?? '') -
                Date.parse(custom?.searchParams.get('since') ?? ''),
        ).toBe(86_400_000)
        expect(router.state.location.search).toContain('range=custom')
    })

    it('asks to choose a range for an invalid custom link', async () => {
        const { requests } = await renderMonitoring(
            {},
            '/monitoring?range=custom&since=nonsense',
        )
        expect(
            await screen.findByText('Choose a time range'),
        ).toBeInTheDocument()
        expect(summaryRequests(requests)).toHaveLength(0)
    })

    it('falls back to the default range for an unknown one', async () => {
        await renderMonitoring(
            { 'GET /monitoring/summary': () => ok(summary()) },
            '/monitoring?range=toString',
        )
        expect(screen.getByLabelText('Time range')).toHaveValue('24h')
    })

    it('refresh moves a preset window to now and refetches the current state', async () => {
        const { requests } = await renderMonitoring({
            'GET /monitoring/summary': () => ok(summary()),
        })
        await screen.findByText('No executions in this time range')
        const counted = requests.filter((u) =>
            u.pathname.endsWith('/status-counts'),
        ).length
        fireEvent.click(screen.getByRole('button', { name: 'Refresh' }))
        await waitFor(() => {
            expect(
                requests.filter((u) => u.pathname.endsWith('/status-counts'))
                    .length,
            ).toBeGreaterThan(counted)
        })
    })
})
