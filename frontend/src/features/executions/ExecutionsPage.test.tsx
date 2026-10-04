import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { apiError, ok, pending, renderRoute, stubBackend } from '../../test/app'
import { parseInput } from './format'

// -- factories (valid by default) -------------------------------------------------

const AT = '2026-10-02T08:00:00Z'
const PID = 'proj_1'
const ACTIVE = ['created', 'queued', 'running', 'paused', 'waiting']

function execution(id: string, status = 'completed', overrides: object = {}) {
    const finished = !ACTIVE.includes(status)
    return {
        id,
        project_id: PID,
        project_name: 'Lead scoring',
        version: 2,
        status,
        trigger_type: 'manual',
        trigger_id: null,
        correlation_id: 'corr_1',
        created_at: AT,
        started_at: status === 'created' || status === 'queued' ? null : AT,
        finished_at: finished ? '2026-10-02T08:00:42Z' : null,
        // The backend's answer; the fixtures mirror its state machine.
        cancellable: !finished,
        ...overrides,
    }
}

const page = (items: unknown[], has_more = false) => ({
    items,
    page: { offset: 0, limit: 25, has_more },
})

const nodeError = {
    code: 'HTTP_ERROR',
    message: 'upstream answered 503',
    kind: 'transient',
    node_id: 'fetch',
    details: {},
    retryable: true,
}

function report(status = 'completed', overrides: object = {}) {
    return {
        schema_version: 1,
        execution_id: 'exec_1',
        project: {
            id: PID,
            name: 'Lead scoring',
            version: 2,
            version_description: '',
            published_at: AT,
        },
        execution: {
            status,
            trigger_type: 'manual',
            trigger_id: null,
            correlation_id: 'corr_1',
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
                label: 'Fetch leads',
                status: status === 'failed' ? 'failed' : 'completed',
                attempts: 1,
                retries: 0,
                started_at: AT,
                finished_at: AT,
                duration_ms: 40,
                error: status === 'failed' ? nodeError : null,
                failures: [],
                outputs: null,
                usage: null,
                cost: null,
            },
        ],
        metrics: {
            node_status_counts: {},
            attempts: 1,
            retries: 0,
            failed_attempts: 0,
            usage: {
                input_tokens: 1200,
                output_tokens: 300,
                total_tokens: 1500,
            },
            costs: [{ amount: 0.0125, currency: 'USD' }],
            cost_complete: true,
        },
        events: [],
        events_truncated: false,
        ...overrides,
    }
}

const routes = {
    list: 'GET /executions',
    detail: (id: string) => `GET /executions/${id}`,
    cancel: (id: string) => `POST /executions/${id}/cancel`,
    report: (id: string) => `GET /reports/executions/${id}`,
}

async function table() {
    return screen.findByRole('table', { name: 'Executions' })
}

// -- list -----------------------------------------------------------------------------

describe('Executions list', () => {
    it('shows a loading state while the list loads', async () => {
        stubBackend({ [routes.list]: pending })
        renderRoute('/executions')
        expect(
            await screen.findByText('Loading executions…'),
        ).toBeInTheDocument()
    })

    it('shows every backend status by its label, not only by color', async () => {
        const statuses = [
            ...ACTIVE,
            'completed',
            'failed',
            'cancelled',
            'timeout',
        ]
        const requests = stubBackend({
            [routes.list]: () =>
                ok(page(statuses.map((s) => execution(`exec_${s}`, s)))),
        })
        renderRoute('/executions')

        const rows = within(await table())
            .getAllByRole('row')
            .slice(1)
        const labels = [
            'Created',
            'Queued',
            'Running',
            'Paused',
            'Waiting',
            'Completed',
            'Failed',
            'Cancelled',
            'Timed out',
        ]
        rows.forEach((row, i) => {
            expect(within(row).getByText(labels[i] ?? '')).toBeInTheDocument()
        })
        // Project name, version link and execution link per row.
        const first = within(rows[0] ?? document.body)
        // The listing itself names the project: no per-project lookups.
        expect(first.getByText('Lead scoring')).toHaveAttribute('title', PID)
        expect(
            requests.filter((u) => u.pathname.endsWith('/projects')),
        ).toEqual([])
        expect(first.getByRole('link', { name: 'v2' })).toHaveAttribute(
            'href',
            `/projects/${PID}/versions/2`,
        )
        expect(
            first.getByRole('link', { name: 'exec_created' }),
        ).toHaveAttribute('href', '/executions/exec_created')
        expect(first.getByText('Not started')).toBeInTheDocument()
        expect(
            within(rows[2] ?? document.body).getByText('In progress'),
        ).toBeInTheDocument()
        expect(
            within(rows[5] ?? document.body).getByText('42 s'),
        ).toBeInTheDocument()
    })

    it('offers cancel only where the backend allows it', async () => {
        stubBackend({
            [routes.list]: () =>
                ok(
                    page([
                        execution('exec_r', 'running'),
                        execution('exec_c', 'completed'),
                        // The flag wins over any status-based guess.
                        execution('exec_x', 'waiting', { cancellable: false }),
                    ]),
                ),
        })
        renderRoute('/executions')

        await table()
        expect(
            screen.getByRole('button', { name: 'Cancel execution exec_r' }),
        ).toBeInTheDocument()
        expect(
            screen.queryByRole('button', { name: 'Cancel execution exec_c' }),
        ).toBeNull()
        expect(
            screen.queryByRole('button', { name: 'Cancel execution exec_x' }),
        ).toBeNull()
    })

    it('says when there are no executions yet', async () => {
        stubBackend({ [routes.list]: () => ok(page([])) })
        renderRoute('/executions')
        expect(
            await screen.findByRole('heading', { name: 'No executions yet' }),
        ).toBeInTheDocument()
        expect(
            screen.getByRole('link', { name: 'Go to projects' }),
        ).toBeInTheDocument()
    })

    it('filters by status through the backend', async () => {
        const requests = stubBackend({
            [routes.list]: (url) =>
                url.searchParams.get('status') === 'failed'
                    ? ok(page([]))
                    : ok(page([execution('exec_1')])),
        })
        const { router } = renderRoute('/executions')

        await table()
        fireEvent.change(screen.getByLabelText('Status'), {
            target: { value: 'failed' },
        })
        expect(
            await screen.findByRole('heading', {
                name: 'No matching executions',
            }),
        ).toBeInTheDocument()
        expect(router.state.location.search).toBe('?status=failed')
        const statuses = requests
            .filter((u) => u.pathname.endsWith('/executions'))
            .map((u) => u.searchParams.getAll('status'))
        expect(statuses).toEqual([[], ['failed']])
    })

    it('pages through executions', async () => {
        const requests = stubBackend({
            [routes.list]: (url) =>
                url.searchParams.get('offset') === '25'
                    ? ok(page([execution('exec_older')]))
                    : ok(page([execution('exec_newer')], true)),
        })
        renderRoute('/executions')

        fireEvent.click(await screen.findByRole('button', { name: 'Next' }))
        expect(
            await screen.findByRole('link', { name: 'exec_older' }),
        ).toBeInTheDocument()
        expect(
            requests
                .filter((u) => u.pathname.endsWith('/executions'))
                .map((u) => u.searchParams.get('offset')),
        ).toEqual(['0', '25'])
    })

    it('shows a load error with retry', async () => {
        let fail = true
        stubBackend({
            [routes.list]: () =>
                fail
                    ? apiError(500, 'INTERNAL_ERROR', 'Database unavailable.')
                    : ok(page([execution('exec_1')])),
        })
        renderRoute('/executions')

        expect(
            await screen.findByRole('heading', {
                name: 'Could not load executions',
            }),
        ).toBeInTheDocument()
        fail = false
        fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
        expect(await table()).toBeInTheDocument()
    })

    it('expands the recorded error of a failed execution', async () => {
        stubBackend({
            [routes.list]: () => ok(page([execution('exec_1', 'failed')])),
            [routes.report('exec_1')]: () => ok(report('failed')),
        })
        renderRoute('/executions')

        fireEvent.click(
            await screen.findByRole('button', {
                name: 'Show error of execution exec_1',
            }),
        )
        expect(
            await screen.findByText('upstream answered 503'),
        ).toBeInTheDocument()
    })
})

// -- detail ---------------------------------------------------------------------------

describe('Execution detail', () => {
    it('is reached from the list and shows state, timing and usage', async () => {
        stubBackend({
            [routes.list]: () => ok(page([execution('exec_1')])),
            [routes.detail('exec_1')]: () => ok(execution('exec_1')),
            [routes.report('exec_1')]: () => ok(report()),
        })
        const { router } = renderRoute('/executions')

        fireEvent.click(await screen.findByRole('link', { name: 'exec_1' }))
        expect(
            await screen.findByRole('heading', { name: 'Execution', level: 1 }),
        ).toBeInTheDocument()
        expect(router.state.location.pathname).toBe('/executions/exec_1')
        const details = within(
            (await screen.findByRole('heading', { name: 'Details' })).closest(
                'section',
            ) ?? document.body,
        )
        expect(details.getByText('Completed')).toBeInTheDocument()
        expect(details.getByText('42 s')).toBeInTheDocument()
        expect(details.getByText('corr_1')).toBeInTheDocument()
        expect(
            await details.findByRole('link', { name: 'Lead scoring' }),
        ).toHaveAttribute('href', `/projects/${PID}`)
        expect(await screen.findByText(/1,500|1500/)).toBeInTheDocument()
        expect(screen.getByText('0.0125 USD')).toBeInTheDocument()
        // Finished: nothing to cancel.
        expect(screen.queryByRole('button', { name: /^Cancel/ })).toBeNull()
    })

    it('shows the failure of a failed execution', async () => {
        stubBackend({
            [routes.detail('exec_1')]: () => ok(execution('exec_1', 'failed')),
            [routes.report('exec_1')]: () => ok(report('failed')),
        })
        renderRoute('/executions/exec_1')

        expect(
            await screen.findByRole('heading', { name: 'Failure' }),
        ).toBeInTheDocument()
        expect(
            await screen.findByText('upstream answered 503'),
        ).toBeInTheDocument()
        expect(screen.getByText('HTTP_ERROR')).toBeInTheDocument()
    })

    it('says when the execution does not exist', async () => {
        stubBackend({
            [routes.detail('exec_x')]: () =>
                apiError(404, 'EXECUTION_NOT_FOUND', 'Execution not found.'),
        })
        renderRoute('/executions/exec_x')
        expect(
            await screen.findByRole('heading', { name: 'Execution not found' }),
        ).toBeInTheDocument()
    })

    it('keeps the details when only the report fails', async () => {
        stubBackend({
            [routes.detail('exec_1')]: () => ok(execution('exec_1')),
            [routes.report('exec_1')]: () =>
                apiError(500, 'INTERNAL_ERROR', 'Report unavailable.'),
        })
        renderRoute('/executions/exec_1')
        expect(
            await screen.findByRole('heading', {
                name: 'Could not load usage',
            }),
        ).toBeInTheDocument()
        expect(
            screen.getByRole('heading', { name: 'Details' }),
        ).toBeInTheDocument()
    })
})

// -- cancel ---------------------------------------------------------------------------

describe('Cancel', () => {
    it('cancels a queued execution after confirmation', async () => {
        let state = execution('exec_1', 'queued')
        const requests = stubBackend({
            [routes.detail('exec_1')]: () => ok(state),
            [routes.report('exec_1')]: () => ok(report('queued')),
            [routes.cancel('exec_1')]: () => {
                state = execution('exec_1', 'cancelled', { started_at: null })
                return { status: 202, body: state }
            },
        })
        renderRoute('/executions/exec_1')

        fireEvent.click(
            await screen.findByRole('button', {
                name: 'Cancel execution exec_1',
            }),
        )
        const dialog = await screen.findByRole('dialog')
        fireEvent.click(
            within(dialog).getByRole('button', { name: 'Cancel execution' }),
        )
        expect(
            await screen.findByText('Execution cancelled'),
        ).toBeInTheDocument()
        // The refetched backend state replaces the old one.
        await waitFor(() => {
            expect(
                screen.queryByRole('button', {
                    name: 'Cancel execution exec_1',
                }),
            ).toBeNull()
        })
        expect(screen.getAllByText('Cancelled').length).toBeGreaterThan(0)
        expect(screen.getByText('Never started')).toBeInTheDocument()
        expect(
            requests.filter((u) => u.pathname.endsWith('/cancel')),
        ).toHaveLength(1)
    })

    it('says a running execution stops cooperatively', async () => {
        const running = execution('exec_1', 'running')
        stubBackend({
            [routes.list]: () => ok(page([running])),
            // Accepted; the owning worker still has to notice.
            [routes.cancel('exec_1')]: () => ({ status: 202, body: running }),
        })
        renderRoute('/executions')

        fireEvent.click(
            await screen.findByRole('button', {
                name: 'Cancel execution exec_1',
            }),
        )
        fireEvent.click(
            within(await screen.findByRole('dialog')).getByRole('button', {
                name: 'Cancel execution',
            }),
        )
        expect(
            await screen.findByText('Cancellation requested'),
        ).toBeInTheDocument()
    })

    it('shows the backend conflict when the run already finished', async () => {
        stubBackend({
            [routes.list]: () => ok(page([execution('exec_1', 'running')])),
            [routes.cancel('exec_1')]: () =>
                apiError(
                    409,
                    'INVALID_STATE_TRANSITION',
                    'Execution already completed.',
                ),
        })
        renderRoute('/executions')

        fireEvent.click(
            await screen.findByRole('button', {
                name: 'Cancel execution exec_1',
            }),
        )
        const dialog = await screen.findByRole('dialog')
        fireEvent.click(
            within(dialog).getByRole('button', { name: 'Cancel execution' }),
        )
        expect(
            await within(dialog).findByText(/Execution already completed/),
        ).toBeInTheDocument()
    })
})

// -- start ----------------------------------------------------------------------------

describe('parseInput', () => {
    it('accepts JSON objects only', () => {
        expect(parseInput('')).toEqual({})
        expect(parseInput('{"a": 1}')).toEqual({ a: 1 })
        expect(parseInput('[1]')).toMatch(/JSON object/)
        expect(parseInput('"x"')).toMatch(/JSON object/)
        expect(parseInput('{')).toBe('Not valid JSON.')
    })
})
