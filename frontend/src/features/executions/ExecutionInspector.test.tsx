import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { apiError, ok, renderRoute, stubBackend } from '../../test/app'
import { MAX_RENDERED_CHARS } from './inspector/format'

// -- factories (valid by default) -------------------------------------------------

const AT = '2026-10-02T08:00:00Z'
const LATER = '2026-10-02T08:00:01Z'
const PID = 'proj_1'
const EID = 'exec_1'

function execution(status = 'completed') {
    return {
        id: EID,
        project_id: PID,
        version: 2,
        status,
        trigger_type: 'manual',
        trigger_id: null,
        correlation_id: 'corr_1',
        created_at: AT,
        started_at: AT,
        finished_at: LATER,
        cancellable: false,
    }
}

function error(code: string, message: string, overrides: object = {}) {
    return {
        code,
        message,
        kind: 'transient',
        node_id: 'fetch',
        details: {},
        retryable: true,
        ...overrides,
    }
}

function node(id: string, label: string, overrides: object = {}) {
    return {
        node_id: id,
        node_type: 'core.http',
        label,
        status: 'completed',
        attempts: 1,
        retries: 0,
        started_at: AT,
        finished_at: LATER,
        duration_ms: 850,
        error: null,
        failures: [],
        outputs: { body: 'ok' },
        usage: null,
        cost: null,
        ...overrides,
    }
}

function event(position: number, event_type: string, node_id: string | null) {
    return {
        position,
        event_type,
        occurred_at: AT,
        node_id,
        payload: node_id ? { attempt: 1 } : {},
    }
}

function report(overrides: object = {}, execution: object = {}) {
    return {
        schema_version: 1,
        execution_id: EID,
        project: {
            id: PID,
            name: 'Lead scoring',
            version: 2,
            version_description: '',
            published_at: AT,
        },
        execution: {
            status: 'completed',
            trigger_type: 'manual',
            trigger_id: null,
            correlation_id: 'corr_1',
            engine_version: '0.27.0',
            node_versions: { 'core.http': '1.2.0' },
            created_at: AT,
            started_at: AT,
            finished_at: LATER,
            duration_ms: 1000,
            input: { topic: 'leads' },
            error: null,
            cancellation: null,
            ...execution,
        },
        nodes: [node('fetch', 'Fetch leads'), node('score', 'Score')],
        metrics: {
            node_status_counts: { completed: 2 },
            attempts: 2,
            retries: 0,
            failed_attempts: 0,
            usage: { input_tokens: 0, output_tokens: 0, total_tokens: 0 },
            costs: [],
            cost_complete: true,
        },
        events: [
            event(1, 'execution.created', null),
            event(2, 'node.started', 'fetch'),
            event(3, 'node.completed', 'fetch'),
            event(4, 'execution.completed', null),
        ],
        events_truncated: false,
        ...overrides,
    }
}

function logLine(position: number, message: string, overrides: object = {}) {
    return {
        position,
        node_id: 'fetch',
        attempt: 1,
        level: 'INFO',
        message,
        logged_at: AT,
        fields: {},
        ...overrides,
    }
}

const logPage = (items: unknown[], has_more = false) => ({
    items,
    next_after:
        (items.at(-1) as { position: number } | undefined)?.position ?? 0,
    has_more,
})

const versionBody = {
    id: 'pver_2',
    version: 2,
    description: '',
    schema_version: 1,
    published_at: AT,
    published_by: null,
    graph: {
        nodes: [
            {
                id: 'fetch',
                node_type: 'core.http',
                config: {},
                label: 'Fetch leads',
                position: { x: 0, y: 0 },
            },
            {
                id: 'score',
                node_type: 'core.http',
                config: {},
                label: 'Score',
                position: { x: 300, y: 0 },
            },
        ],
        edges: [],
    },
    triggers: [],
    dependencies: [],
    files: [],
}

const routes = {
    detail: `GET /executions/${EID}`,
    report: `GET /reports/executions/${EID}`,
    logs: `GET /executions/${EID}/logs`,
    version: `GET /projects/${PID}/versions/2`,
}

function backend(
    r: object = report(),
    logs: (url: URL) => unknown = () => logPage([]),
    status = 'completed',
) {
    return stubBackend({
        [routes.detail]: () => ok(execution(status)),
        [routes.report]: () => ok(r),
        [routes.logs]: (url) => ok(logs(url)),
        [routes.version]: () => ok(versionBody),
    })
}

async function openTab(name: RegExp | string) {
    fireEvent.click(await screen.findByRole('tab', { name }))
    return screen.getByRole('tabpanel')
}

// -- tests ----------------------------------------------------------------------------

describe('Execution inspector', () => {
    it('shows a successful run: snapshot, nodes, timings, outputs and events', async () => {
        backend()
        const { router, ready } = renderRoute(`/executions/${EID}`)
        // The first test of the file pays the page's cold import (React
        // Flow), close to a second under full-suite load.
        await ready

        // Overview: reproducibility metadata and the (redacted) input.
        const snapshot = within(
            (
                await screen.findByRole('heading', { name: 'Run snapshot' })
            ).closest('section') ?? document.body,
        )
        expect(await snapshot.findByText('0.27.0')).toBeInTheDocument()
        expect(snapshot.getByText('1.2.0')).toBeInTheDocument()
        expect(snapshot.getByText(/"topic": "leads"/)).toBeInTheDocument()

        const nodes = await openTab('Nodes')
        const table = within(nodes).getByRole('region', { name: 'Node status' })
        expect(within(table).getAllByText('Completed')).toHaveLength(2)
        expect(within(table).getAllByText('850 ms')).toHaveLength(2)

        fireEvent.click(
            within(table).getByRole('button', { name: 'Inspect node Score' }),
        )
        expect(router.state.location.search).toBe('?tab=nodes&node=score')
        const inspector = within(
            screen
                .getByRole('heading', { name: 'Node: Score' })
                .closest('section') ?? document.body,
        )
        expect(inspector.getByText(/"body": "ok"/)).toBeInTheDocument()
        expect(inspector.getByText(/1\.2\.0/)).toBeInTheDocument()

        const events = await openTab('Events')
        const timeline = within(events).getByRole('list', {
            name: 'Event timeline',
        })
        // Nodes are named by their label, not their id.
        expect(
            within(timeline).getByText(
                'Node Fetch leads completed (attempt 1)',
            ),
        ).toBeInTheDocument()
        expect(
            within(timeline).getByText('Execution completed'),
        ).toBeInTheDocument()

        await openTab('Errors')
        expect(
            screen.getByText('No errors were recorded for this execution.'),
        ).toBeInTheDocument()
    })

    it('draws the executed graph with each node state and opens a node from it', async () => {
        backend(
            report({
                nodes: [
                    node('fetch', 'Fetch leads'),
                    node('score', 'Score', {
                        status: 'skipped',
                        duration_ms: null,
                        outputs: null,
                    }),
                ],
            }),
        )
        const { router } = renderRoute(`/executions/${EID}?tab=graph`)

        const graph = await screen.findByRole('figure', {
            name: 'Execution graph (read-only)',
        })
        await waitFor(() => {
            expect(within(graph).getByText('Skipped')).toBeInTheDocument()
        })
        expect(within(graph).getByText('Completed')).toBeInTheDocument()

        fireEvent.click(within(graph).getByText('Fetch leads'))
        await waitFor(() => {
            expect(router.state.location.search).toBe('?tab=nodes&node=fetch')
        })
    })

    it('lets a failed node be diagnosed: error, retried failures and its logs', async () => {
        const failing = node('fetch', 'Fetch leads', {
            status: 'failed',
            attempts: 3,
            retries: 2,
            outputs: null,
            error: error('HTTP_ERROR', 'upstream answered 503', {
                details: { status: '503' },
            }),
            failures: [
                error('HTTP_TIMEOUT', 'no answer within 10 s', { attempt: 1 }),
                error('HTTP_ERROR', 'upstream answered 502', { attempt: 2 }),
                error('HTTP_ERROR', 'upstream answered 503', {
                    details: { status: '503' },
                    attempt: 3,
                }),
            ],
        })
        const requests = backend(
            report(
                {
                    nodes: [
                        failing,
                        node('score', 'Score', {
                            status: 'cancelled',
                            outputs: null,
                        }),
                    ],
                },
                {
                    status: 'failed',
                    error: error('HTTP_ERROR', 'upstream answered 503'),
                },
            ),
            () =>
                logPage([
                    logLine(4, 'GET /leads → 503', {
                        attempt: 3,
                        level: 'ERROR',
                        fields: { status: 503 },
                    }),
                ]),
            'failed',
        )
        const { router } = renderRoute(`/executions/${EID}`)

        // The errors tab says how many failures there are, in text.
        const errors = await openTab('Errors (3)')
        const fetchErrors = within(
            within(errors).getByRole('region', {
                name: 'Errors of node Fetch leads',
            }),
        )
        expect(
            fetchErrors.getByText(/3 of 3 attempts failed; node failed/),
        ).toBeInTheDocument()
        expect(
            fetchErrors.getByText('no answer within 10 s'),
        ).toBeInTheDocument()

        fireEvent.click(
            fetchErrors.getByRole('button', {
                name: 'Inspect node Fetch leads',
            }),
        )
        expect(router.state.location.search).toBe('?tab=nodes&node=fetch')
        const inspector = within(
            screen
                .getByRole('heading', { name: 'Node: Fetch leads' })
                .closest('section') ?? document.body,
        )
        expect(
            inspector.getByText('2 retries', { exact: false }),
        ).toBeInTheDocument()
        const finalError = within(
            inspector.getByRole('region', { name: 'Error' }),
        )
        expect(
            finalError.getByText('upstream answered 503'),
        ).toBeInTheDocument()
        expect(finalError.getByText('status')).toBeInTheDocument()
        const retried = within(
            inspector.getByRole('region', { name: 'Retried failures' }),
        )
        expect(retried.getByText('no answer within 10 s')).toBeInTheDocument()
        expect(retried.getByText('Attempt 1')).toBeInTheDocument()
        expect(retried.getByText('Attempt 2')).toBeInTheDocument()
        expect(retried.getByText('upstream answered 502')).toBeInTheDocument()
        expect(retried.queryByText('upstream answered 503')).toBeNull()
        expect(
            inspector.getByText('Only a completed node has outputs.'),
        ).toBeInTheDocument()

        // The node's own logs, asked from the backend for that node only.
        expect(
            await inspector.findByText('GET /leads → 503'),
        ).toBeInTheDocument()
        expect(inspector.getByText('attempt 3')).toBeInTheDocument()
        const logRequest = requests.find((u) => u.pathname.endsWith('/logs'))
        expect(logRequest?.searchParams.get('node_id')).toBe('fetch')
    })

    it('filters logs through the backend and pages through large logs', async () => {
        const requests = backend(report(), (url) => {
            const after = Number(url.searchParams.get('after'))
            if (after === 0)
                return logPage(
                    Array.from({ length: 100 }, (_, i) =>
                        logLine(i + 1, `row ${String(i + 1)}`),
                    ),
                    true,
                )
            return logPage([
                logLine(101, 'last row', {
                    node_id: 'score',
                    level: 'WARNING',
                }),
            ])
        })
        renderRoute(`/executions/${EID}?tab=logs`)

        const list = await screen.findByRole('list', { name: 'Log lines' })
        expect(within(list).getAllByRole('listitem')).toHaveLength(100)
        fireEvent.click(screen.getByRole('button', { name: 'Load more lines' }))
        expect(await within(list).findByText('last row')).toBeInTheDocument()
        expect(within(list).getAllByRole('listitem')).toHaveLength(101)
        // Lines are named by their node's label.
        expect(within(list).getByText('Score')).toBeInTheDocument()
        expect(
            screen.queryByRole('button', { name: 'Load more lines' }),
        ).toBeNull()
        const pages = requests.filter((u) => u.pathname.endsWith('/logs'))
        expect(pages.map((u) => u.searchParams.get('after'))).toEqual([
            '0',
            '100',
        ])

        // Search applies on submit, not per keystroke.
        const before = requests.length
        fireEvent.change(screen.getByLabelText('Message contains'), {
            target: { value: '  last ' },
        })
        expect(requests.length).toBe(before)
        fireEvent.click(screen.getByRole('button', { name: 'Search' }))
        await waitFor(() => {
            const last = requests
                .filter((u) => u.pathname.endsWith('/logs'))
                .at(-1)
            expect(last?.searchParams.get('q')).toBe('last')
        })

        fireEvent.change(screen.getByLabelText('Minimum level'), {
            target: { value: 'WARNING' },
        })
        fireEvent.change(screen.getByLabelText('Node'), {
            target: { value: 'score' },
        })
        await waitFor(() => {
            const last = requests
                .filter((u) => u.pathname.endsWith('/logs'))
                .at(-1)
            expect(last?.searchParams.get('level')).toBe('WARNING')
            expect(last?.searchParams.get('node_id')).toBe('score')
        })
    })

    it('bounds what it renders of huge outputs, messages and event histories', async () => {
        const huge = 'x'.repeat(MAX_RENDERED_CHARS + 5_000)
        backend(
            report({
                nodes: [
                    node('fetch', 'Fetch leads', { outputs: { body: huge } }),
                ],
                events: Array.from({ length: 450 }, (_, i) =>
                    event(i + 1, 'node.started', 'fetch'),
                ),
                events_truncated: true,
            }),
            () => logPage([logLine(1, huge)]),
        )
        renderRoute(`/executions/${EID}?tab=nodes&node=fetch`)

        const inspector = within(
            (
                await screen.findByRole('heading', {
                    name: 'Node: Fetch leads',
                })
            ).closest('section') ?? document.body,
        )
        // The output and, once loaded, the log line.
        await waitFor(() => {
            expect(
                inspector.getAllByText(
                    /^Showing the first 20,000 of 2\d,\d{3} characters\.$/,
                ),
            ).toHaveLength(2)
        })
        const [showAll] = inspector.getAllByRole('button', { name: 'Show all' })
        if (showAll) fireEvent.click(showAll)
        expect(
            inspector.getByRole('button', { name: 'Show less' }),
        ).toBeInTheDocument()

        const events = await openTab('Events')
        expect(
            within(events).getByText(/more events than the report holds/),
        ).toBeInTheDocument()
        const timeline = within(events).getByRole('list', {
            name: 'Event timeline',
        })
        expect(within(timeline).getAllByRole('listitem')).toHaveLength(200)
        fireEvent.click(
            within(events).getByRole('button', { name: /Show more events/ }),
        )
        expect(within(timeline).getAllByRole('listitem')).toHaveLength(400)
    })

    it('copes with missing data', async () => {
        backend(
            report(
                {
                    nodes: [
                        node('fetch', '', {
                            status: 'pending',
                            attempts: 0,
                            started_at: null,
                            finished_at: null,
                            duration_ms: null,
                            outputs: null,
                        }),
                    ],
                    events: [],
                },
                { node_versions: {}, finished_at: null, duration_ms: null },
            ),
        )
        renderRoute(`/executions/${EID}?tab=nodes&node=fetch`)

        // No label: the node is named by its id.
        const inspector = within(
            (
                await screen.findByRole('heading', { name: 'Node: fetch' })
            ).closest('section') ?? document.body,
        )
        expect(inspector.getByText('Pending')).toBeInTheDocument()
        expect(inspector.getAllByText('None').length).toBeGreaterThan(0)
        expect(
            await inspector.findByText('No log lines were recorded.'),
        ).toBeInTheDocument()

        await openTab('Events')
        expect(screen.getByText('No events were recorded.')).toBeInTheDocument()
    })

    it('says when a linked node is not part of the execution', async () => {
        backend()
        renderRoute(`/executions/${EID}?tab=nodes&node=gone`)
        expect(
            await screen.findByText(/is not part of this execution/),
        ).toBeInTheDocument()
    })

    it('keeps the execution visible when the report or logs cannot load', async () => {
        stubBackend({
            [routes.detail]: () => ok(execution()),
            [routes.report]: () =>
                apiError(500, 'INTERNAL_ERROR', 'Report unavailable.'),
        })
        renderRoute(`/executions/${EID}?tab=nodes`)
        expect(
            await screen.findByRole('heading', {
                name: 'Could not load the nodes',
            }),
        ).toBeInTheDocument()
        expect(
            screen.getByRole('button', { name: 'Retry' }),
        ).toBeInTheDocument()

        stubBackend({
            [routes.detail]: () => ok(execution()),
            [routes.report]: () => ok(report()),
            [routes.logs]: () => apiError(500, 'INTERNAL_ERROR', 'Logs down.'),
        })
        renderRoute(`/executions/${EID}?tab=logs`)
        expect(
            await screen.findByRole('heading', {
                name: 'Could not load the logs',
            }),
        ).toBeInTheDocument()
    })

    it('renders redacted values as the backend sent them and stores nothing', async () => {
        localStorage.clear()
        sessionStorage.clear()
        backend(
            report({
                nodes: [
                    node('fetch', 'Fetch leads', {
                        outputs: {
                            api_key: '[REDACTED]',
                            note: 'key [REDACTED]',
                        },
                    }),
                ],
            }),
            () =>
                logPage([
                    logLine(1, 'calling with Bearer [REDACTED]', {
                        fields: { api_key: '[REDACTED]' },
                    }),
                ]),
        )
        const { router } = renderRoute(
            `/executions/${EID}?tab=nodes&node=fetch`,
        )

        const inspector = within(
            (
                await screen.findByRole('heading', {
                    name: 'Node: Fetch leads',
                })
            ).closest('section') ?? document.body,
        )
        expect(
            inspector.getByText(/"api_key": "\[REDACTED\]"/),
        ).toBeInTheDocument()
        expect(
            await inspector.findByText('calling with Bearer [REDACTED]'),
        ).toBeInTheDocument()
        // Execution data never lands in browser storage or the URL.
        expect(localStorage.length).toBe(0)
        expect(sessionStorage.length).toBe(0)
        expect(router.state.location.search).toBe('?tab=nodes&node=fetch')
    })

    it('shows untrusted log and output text as text, never as markup', async () => {
        const hostile =
            '<img src=x onerror="alert(1)"><script>alert(2)</script>'
        const link = '<a href="javascript:alert(3)">click</a>'
        backend(
            report({
                nodes: [
                    node('fetch', `<b>${hostile}</b>`, {
                        outputs: { html: hostile, md: '[click](javascript:x)' },
                        error: error('E', link),
                        failures: [error('E', link, { attempt: 1 })],
                        status: 'failed',
                    }),
                ],
            }),
            () => logPage([logLine(1, hostile, { fields: { html: link } })]),
        )
        renderRoute(`/executions/${EID}?tab=nodes&node=fetch`)

        expect(await screen.findByText(hostile)).toBeInTheDocument()
        // The output, as JSON text with the markup still in it.
        expect(document.body.textContent).toContain(
            `"html": ${JSON.stringify(hostile)}`,
        )
        expect(screen.getAllByText(link).length).toBeGreaterThan(0)
        // Nothing was interpreted: no element came out of the text.
        expect(document.querySelector('img')).toBeNull()
        expect(document.querySelector('script')).toBeNull()
        expect(document.querySelector('b')).toBeNull()
        expect(document.querySelector('a[href^="javascript"]')).toBeNull()
    })
})
