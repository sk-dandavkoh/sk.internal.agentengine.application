import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { apiError, ok, renderRoute, stubBackend } from '../../test/app'

const AT = '2026-10-06T08:00:00Z'
const EID = 'exec_1'
const NODE = 'node_review'

function execution(status: string) {
    return {
        id: EID,
        workflow_id: 'wf_1',
        version: 1,
        status,
        trigger_type: 'manual',
        trigger_id: null,
        correlation_id: null,
        created_at: AT,
        started_at: AT,
        finished_at: status === 'completed' ? AT : null,
        cancellable: status !== 'completed',
    }
}

const report = {
    schema_version: 1,
    execution_id: EID,
    workflow: {
        id: 'wf_1',
        name: 'Lead review',
        version: 1,
        version_description: '',
        published_at: AT,
    },
    execution: {
        status: 'waiting',
        trigger_type: 'manual',
        trigger_id: null,
        correlation_id: null,
        engine_version: '0.1.0',
        node_versions: { 'core.human': '1.0.0' },
        created_at: AT,
        started_at: AT,
        finished_at: null,
        duration_ms: null,
        input: {},
        error: null,
        cancellation: null,
    },
    nodes: [
        {
            node_id: NODE,
            node_type: 'core.human',
            label: 'Review',
            // A node status the report schema must accept.
            status: 'waiting',
            attempts: 1,
            retries: 0,
            started_at: AT,
            finished_at: null,
            duration_ms: null,
            error: null,
            failures: [],
            outputs: null,
            usage: null,
            cost: null,
        },
    ],
    metrics: {
        node_status_counts: { waiting: 1 },
        attempts: 1,
        retries: 0,
        failed_attempts: 0,
        usage: { input_tokens: 0, output_tokens: 0, total_tokens: 0 },
        costs: [],
        cost_complete: true,
    },
    events: [],
    events_truncated: false,
}

const pending = {
    items: [
        {
            node_id: NODE,
            prompt: 'Contact <b>Ada</b> (score 91)?',
            response_schema: {
                type: 'object',
                properties: {
                    decision: { type: 'string', enum: ['approve', 'reject'] },
                    comment: { type: 'string' },
                },
                required: ['decision'],
                additionalProperties: false,
            },
            deadline: '2026-10-07T08:00:00Z',
        },
    ],
}

function backend(
    status: string,
    respond: (init: RequestInit) => ReturnType<typeof ok> = () =>
        ok(execution('queued')),
) {
    const answers: unknown[] = []
    const requests = stubBackend({
        [`GET /executions/${EID}`]: () => ok(execution(status)),
        [`GET /reports/executions/${EID}`]: () => ok(report),
        [`GET /executions/${EID}/inputs`]: () => ok(pending),
        [`POST /executions/${EID}/inputs/${NODE}`]: (_url, init) => {
            answers.push(JSON.parse(init.body as string))
            return respond(init)
        },
    })
    return { requests, answers }
}

/** The card is a <section> under its heading. */
async function inputCard() {
    const heading = await screen.findByRole('heading', {
        name: 'Waiting for input',
    })
    const card = heading.closest('section')
    if (!card) throw new Error('no card around the heading')
    return card
}

describe('Pending inputs of a waiting execution', () => {
    it('shows the request as text and sends the answer the form built', async () => {
        const { answers } = backend('waiting')
        renderRoute(`/executions/${EID}`)

        const card = await inputCard()
        // The prompt is data: markup in it is shown, never interpreted.
        expect(
            await within(card).findByText('Contact <b>Ada</b> (score 91)?'),
        ).toBeInTheDocument()
        expect(within(card).getByText(/Answer by/)).toBeInTheDocument()

        fireEvent.change(within(card).getByLabelText(/Decision/), {
            target: { value: '"approve"' },
        })
        fireEvent.change(within(card).getByLabelText(/Comment/), {
            target: { value: 'go ahead' },
        })
        fireEvent.click(
            within(card).getByRole('button', { name: 'Send answer' }),
        )

        await waitFor(() => {
            expect(answers).toEqual([
                { response: { decision: 'approve', comment: 'go ahead' } },
            ])
        })
        expect(await screen.findByText('Answer sent')).toBeInTheDocument()
    })

    it('shows why the server refused an answer and keeps the form', async () => {
        backend('waiting', () =>
            apiError(
                409,
                'INVALID_STATE_TRANSITION',
                'The node is not waiting for a response.',
            ),
        )
        renderRoute(`/executions/${EID}`)
        const card = await inputCard()
        fireEvent.change(await within(card).findByLabelText(/Decision/), {
            target: { value: '"reject"' },
        })
        fireEvent.click(
            within(card).getByRole('button', { name: 'Send answer' }),
        )
        expect(await within(card).findByRole('alert')).toHaveTextContent(
            /not waiting for a response/,
        )
        expect(
            within(card).getByRole('button', { name: 'Send answer' }),
        ).toBeInTheDocument()
    })

    it('confirms the answer even when the run finished meanwhile', async () => {
        let status = 'waiting'
        let release: () => void = () => undefined
        stubBackend({
            [`GET /executions/${EID}`]: () => ok(execution(status)),
            [`GET /reports/executions/${EID}`]: () => ok(report),
            [`GET /executions/${EID}/inputs`]: () => ok(pending),
            [`POST /executions/${EID}/inputs/${NODE}`]: () =>
                new Promise((resolve) => {
                    release = () => {
                        resolve(ok(execution('queued')))
                    }
                }),
        })
        renderRoute(`/executions/${EID}`)
        const card = await inputCard()
        fireEvent.change(await within(card).findByLabelText(/Decision/), {
            target: { value: '"approve"' },
        })
        fireEvent.click(
            within(card).getByRole('button', { name: 'Send answer' }),
        )
        // The worker finishes the run; the page learns it before the answer's
        // own response arrives, and the form goes away.
        status = 'completed'
        for (const refresh of screen.getAllByRole('button', {
            name: 'Refresh',
        }))
            fireEvent.click(refresh)
        await waitFor(() => {
            expect(
                screen.queryByRole('heading', { name: 'Waiting for input' }),
            ).not.toBeInTheDocument()
        })
        release()
        expect(await screen.findByText('Answer sent')).toBeInTheDocument()
    })

    it('asks nothing when the execution is not waiting', async () => {
        const { requests } = backend('completed')
        renderRoute(`/executions/${EID}`)
        expect(
            await screen.findByRole('heading', { name: /Execution/ }),
        ).toBeInTheDocument()
        expect(
            screen.queryByRole('heading', { name: 'Waiting for input' }),
        ).not.toBeInTheDocument()
        expect(requests.some((u) => u.pathname.endsWith('/inputs'))).toBe(false)
    })
})
