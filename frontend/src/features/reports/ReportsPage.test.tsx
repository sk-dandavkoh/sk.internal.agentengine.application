import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { apiError, ok, renderRoute, stubBackend } from '../../test/app'

// -- factories (valid by default) -------------------------------------------------

const AT = '2026-10-02T08:00:00Z'
const LATER = '2026-10-02T08:00:01Z'
const PID = 'wf_1'
const EID = 'exec_1'
const REPORT = `GET /reports/executions/${EID}`

function listItem(id: string, status = 'completed') {
    return {
        id,
        workflow_id: PID,
        workflow_name: 'Lead scoring',
        version: 2,
        status,
        trigger_type: 'manual',
        trigger_id: null,
        correlation_id: null,
        created_at: AT,
        started_at: AT,
        finished_at: LATER,
        cancellable: false,
    }
}

const page = (items: unknown[], has_more = false) => ({
    items,
    page: { offset: 0, limit: 25, has_more },
})

function error(code: string, message: string, node_id: string | null) {
    return {
        code,
        message,
        kind: 'permanent',
        node_id,
        details: {},
        retryable: false,
    }
}

function node(id: string, label: string, overrides: object = {}) {
    return {
        node_id: id,
        node_type: 'core.llm',
        label,
        status: 'completed',
        attempts: 1,
        retries: 0,
        started_at: AT,
        finished_at: LATER,
        duration_ms: 850,
        error: null,
        failures: [],
        outputs: { text: 'hello' },
        usage: { input_tokens: 12, output_tokens: 30, total_tokens: 42 },
        cost: { amount: 0.0021, currency: 'USD' },
        ...overrides,
    }
}

function report(overrides: object = {}, execution: object = {}) {
    return {
        schema_version: 1,
        execution_id: EID,
        workflow: {
            id: PID,
            name: 'Lead scoring',
            version: 2,
            version_description: 'first release',
            published_at: AT,
        },
        execution: {
            status: 'completed',
            trigger_type: 'manual',
            trigger_id: null,
            correlation_id: 'corr_1',
            engine_version: '0.27.0',
            node_versions: { 'core.llm': '1.0.0' },
            created_at: AT,
            started_at: AT,
            finished_at: LATER,
            duration_ms: 1000,
            input: { topic: 'leads' },
            error: null,
            cancellation: null,
            ...execution,
        },
        nodes: [node('model', 'Model')],
        metrics: {
            node_status_counts: { completed: 1 },
            attempts: 1,
            retries: 0,
            failed_attempts: 0,
            usage: { input_tokens: 12, output_tokens: 30, total_tokens: 42 },
            costs: [{ amount: 0.0021, currency: 'USD' }],
            cost_complete: true,
        },
        events: [
            {
                position: 1,
                event_type: 'execution.created',
                occurred_at: AT,
                node_id: null,
                payload: {},
            },
        ],
        events_truncated: false,
        ...overrides,
    }
}

const failedReport = () => {
    const failure = error('HTTP_500', 'Upstream failed', 'model')
    return report(
        {
            nodes: [
                node('model', 'Model', {
                    status: 'failed',
                    error: failure,
                    failures: [{ ...failure, attempt: 1 }],
                    outputs: null,
                    usage: null,
                    cost: null,
                }),
            ],
            metrics: {
                node_status_counts: { failed: 1 },
                attempts: 1,
                retries: 0,
                failed_attempts: 1,
                usage: { input_tokens: 0, output_tokens: 0, total_tokens: 0 },
                costs: [],
                cost_complete: true,
            },
        },
        {
            status: 'failed',
            error: error('NODE_FAILED', 'Node model failed', 'model'),
        },
    )
}

async function openReport(body: unknown) {
    stubBackend({ [REPORT]: () => ok(body) })
    const { ready } = renderRoute(`/reports/${EID}`)
    await ready
    return screen.findByRole('heading', { name: 'Summary' })
}

// -- list -------------------------------------------------------------------------------

describe('Reports list', () => {
    it('lists finished executions only, each linking to its report', async () => {
        const requests = stubBackend({
            'GET /executions': () =>
                ok(page([listItem(EID), listItem('exec_2', 'failed')])),
        })
        renderRoute('/reports')
        const link = await screen.findByRole('link', {
            name: `View report of execution ${EID}`,
        })
        expect(link).toHaveAttribute('href', `/reports/${EID}`)
        // Execution association: the id still opens the inspector.
        expect(screen.getAllByRole('link', { name: EID })[0]).toHaveAttribute(
            'href',
            `/executions/${EID}`,
        )
        const list = requests.find((u) => u.pathname.endsWith('/executions'))
        expect(list?.searchParams.getAll('status')).toEqual([
            'completed',
            'failed',
            'timeout',
            'cancelled',
        ])
    })

    it('filters by one finished status and pages through the backend', async () => {
        const requests = stubBackend({
            'GET /executions': (url) =>
                ok(
                    page(
                        [listItem(EID)],
                        url.searchParams.get('offset') === '0',
                    ),
                ),
        })
        renderRoute('/reports')
        fireEvent.change(await screen.findByLabelText('Status'), {
            target: { value: 'failed' },
        })
        await waitFor(() => {
            expect(requests.at(-1)?.searchParams.getAll('status')).toEqual([
                'failed',
            ])
        })
        fireEvent.click(await screen.findByRole('button', { name: 'Next' }))
        await waitFor(() => {
            expect(requests.at(-1)?.searchParams.get('offset')).toBe('25')
        })
    })

    it('shows an empty state when nothing has finished', async () => {
        stubBackend({ 'GET /executions': () => ok(page([])) })
        renderRoute('/reports')
        expect(
            await screen.findByRole('heading', { name: 'No reports yet' }),
        ).toBeInTheDocument()
    })
})

// -- detail -----------------------------------------------------------------------------

describe('Report detail', () => {
    it('shows a successful report: summary, usage, nodes, outputs, events', async () => {
        await openReport(report())
        expect(
            screen.getByRole('link', { name: 'Lead scoring' }),
        ).toHaveAttribute('href', `/workflows/${PID}`)
        expect(screen.getByText('1 s')).toBeInTheDocument() // backend duration
        expect(screen.getAllByText(/0\.0021 USD/).length).toBeGreaterThan(0)
        const nodes = screen.getByRole('table', {
            name: 'Nodes of this execution',
        })
        expect(within(nodes).getByText('Model')).toBeInTheDocument()
        expect(within(nodes).getByText('42')).toBeInTheDocument()
        expect(
            screen.getByText('No errors were recorded for this execution.'),
        ).toBeInTheDocument()
        expect(
            screen.getByRole('list', { name: 'Event timeline' }),
        ).toBeInTheDocument()
        expect(
            screen.getByRole('link', { name: 'Open in inspector' }),
        ).toHaveAttribute('href', `/executions/${EID}`)
        expect(
            screen.getByRole('link', {
                name: 'view them in the execution inspector',
            }),
        ).toHaveAttribute('href', `/executions/${EID}?tab=logs`)
    })

    it('shows a failed execution report with every recorded error', async () => {
        await openReport(failedReport())
        const errors = screen.getByRole('region', { name: 'Execution error' })
        expect(within(errors).getByText('NODE_FAILED')).toBeInTheDocument()
        const nodeErrors = screen.getByRole('region', {
            name: 'Errors of node Model',
        })
        expect(
            within(nodeErrors).getByText('Upstream failed'),
        ).toBeInTheDocument()
        expect(within(nodeErrors).getByText('Attempt 1')).toBeInTheDocument()
        expect(
            screen.queryByRole('region', { name: 'Node outputs' }),
        ).toBeNull()
    })

    it('rejects a malformed report instead of rendering it', async () => {
        const broken: Record<string, unknown> = report()
        delete broken['nodes']
        stubBackend({ [REPORT]: () => ok(broken) })
        renderRoute(`/reports/${EID}`)
        expect(
            await screen.findByRole('heading', {
                name: 'Could not load the report',
            }),
        ).toBeInTheDocument()
        expect(screen.queryByRole('heading', { name: 'Summary' })).toBeNull()
    })

    it('shows a not-found state for an unknown or inaccessible execution', async () => {
        stubBackend({
            [REPORT]: () => apiError(404, 'EXECUTION_NOT_FOUND', 'Not found.'),
        })
        renderRoute(`/reports/${EID}`)
        expect(
            await screen.findByRole('heading', { name: 'Report not found' }),
        ).toBeInTheDocument()
    })

    it('renders untrusted text as text and keeps redaction as sent', async () => {
        const html = '<img src=x onerror="window.pwned=1">'
        const script = '<script>window.pwned=1</script>'
        const body = failedReport()
        body.nodes[0] = node('model', html, {
            status: 'failed',
            error: error('X', script, 'model'),
            failures: [{ ...error('X', script, 'model'), attempt: 1 }],
            outputs: { text: html, api_key: '[REDACTED]' },
        })
        Object.assign(body.execution.input, { token: '[REDACTED]' })
        await openReport(body)
        expect(screen.getAllByText(html).length).toBeGreaterThan(0)
        expect(screen.getAllByText(script).length).toBeGreaterThan(0)
        expect(document.body.querySelector('img, script, iframe')).toBeNull()
        expect('pwned' in window).toBe(false)
        // Redacted values are shown exactly as the backend sent them.
        fireEvent.click(screen.getByText(html, { selector: 'summary' }))
        expect(screen.getAllByText(/\[REDACTED\]/).length).toBeGreaterThan(0)
    })

    it('renders a large report in bounded steps', async () => {
        const nodes = Array.from({ length: 300 }, (_, i) =>
            node(`n${String(i).padStart(3, '0')}`, `Node ${String(i)}`),
        )
        nodes[0] = node('n000', 'Huge', {
            outputs: { text: 'x'.repeat(100_000) },
        })
        const events = Array.from({ length: 10_000 }, (_, i) => ({
            position: i + 1,
            event_type: 'node.started',
            occurred_at: AT,
            node_id: 'n001',
            payload: { attempt: 1 },
        }))
        await openReport(report({ nodes, events, events_truncated: true }))
        const table = screen.getByRole('table', {
            name: 'Nodes of this execution',
        })
        expect(within(table).getAllByRole('row')).toHaveLength(301)
        expect(
            screen.getByText(/more events than the report holds/),
        ).toBeInTheDocument()
        expect(
            screen.getByRole('button', {
                name: /Show more events \(9800 more\)/,
            }),
        ).toBeInTheDocument()
        fireEvent.click(screen.getByText('Huge', { selector: 'summary' }))
        expect(
            screen.getByText(/Showing the first 20,000 of/),
        ).toBeInTheDocument()
    })
})

// -- export -----------------------------------------------------------------------------

describe('Report export', () => {
    let saved: Blob[]
    let clicks: HTMLAnchorElement[]

    beforeEach(() => {
        saved = []
        clicks = []
        URL.createObjectURL = vi.fn((blob: Blob) => {
            saved.push(blob)
            return 'blob:report'
        })
        URL.revokeObjectURL = vi.fn()
        vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(
            function (this: HTMLAnchorElement) {
                clicks.push(this)
            },
        )
    })
    afterEach(() => {
        vi.restoreAllMocks()
    })

    it('downloads the backend bytes unchanged, unknown fields included', async () => {
        // An additive backend field the frontend schema does not know:
        // a re-serialized export would silently drop it.
        const body = { ...report(), future_field: 'kept' }
        await openReport(body)
        fireEvent.click(screen.getByRole('button', { name: 'Download JSON' }))
        await waitFor(() => {
            expect(clicks).toHaveLength(1)
        })
        expect(clicks[0]?.download).toBe(`execution-report-${EID}.json`)
        expect(saved[0]?.type).toBe('application/json')
        expect(await saved[0]?.text()).toBe(JSON.stringify(body))
    })

    it('saves nothing when the export is malformed', async () => {
        let calls = 0
        stubBackend({
            [REPORT]: () => {
                calls += 1
                // The page loads; the export request then gets a broken body.
                return ok(calls === 1 ? report() : { schema_version: 1 })
            },
        })
        const { ready } = renderRoute(`/reports/${EID}`)
        await ready
        fireEvent.click(
            await screen.findByRole('button', { name: 'Download JSON' }),
        )
        expect(
            await screen.findByText('Could not download the report'),
        ).toBeInTheDocument()
        expect(saved).toHaveLength(0)
        expect(clicks).toHaveLength(0)
    })

    it('keeps the file name safe for an unusual execution id', async () => {
        const odd = 'exec/../x y'
        stubBackend({
            [`GET /reports/executions/${encodeURIComponent(odd)}`]: () =>
                ok(report({ execution_id: odd })),
        })
        const { ready } = renderRoute(`/reports/${encodeURIComponent(odd)}`)
        await ready
        await screen.findByRole('heading', { name: 'Summary' })
        fireEvent.click(screen.getByRole('button', { name: 'Download JSON' }))
        await waitFor(() => {
            expect(clicks).toHaveLength(1)
        })
        expect(clicks[0]?.download).toBe('execution-report-exec____x_y.json')
    })
})
