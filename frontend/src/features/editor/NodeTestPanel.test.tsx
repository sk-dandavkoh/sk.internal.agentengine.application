import { QueryClientProvider } from '@tanstack/react-query'
import {
    fireEvent,
    render,
    screen,
    waitFor,
    within,
} from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../api/client'
import { ApiClientContext } from '../../api/context'
import type { NodeDefinition, NodePort } from '../../api/nodes'
import { createQueryClient } from '../../api/queryClient'
import { apiError, ok, stubBackend, type Handler } from '../../test/app'
import { NodeTestPanel } from './NodeTestPanel'

const AT = '2026-10-07T08:00:00Z'
const NODE = `node_${'a'.repeat(32)}`

const port = (name: string, extra: Partial<NodePort> = {}): NodePort => ({
    name,
    type: 'string',
    required: true,
    nullable: false,
    multiple: false,
    description: '',
    ...extra,
})
const writer: NodeDefinition = {
    node_type: 'core.file_write',
    version: '1.0.0',
    name: 'Write File',
    description: '',
    category: 'file',
    documentation_url: null,
    inputs: [port('path'), port('meta', { type: 'json', required: false })],
    outputs: [port('path')],
    config_schema: {},
    capabilities: {},
}

const result = (overrides: object = {}) =>
    ok({
        status: 'completed',
        duration_ms: 12,
        outputs: { path: 'out/report.txt' },
        error: null,
        logs: [
            {
                level: 'INFO',
                message: 'wrote 5 bytes',
                logged_at: AT,
                fields: {},
            },
        ],
        ...overrides,
    })

const noRuns = () =>
    ok({ items: [], page: { offset: 0, limit: 1, has_more: false } })

function setup(
    handlers: Record<string, Handler>,
    props: Partial<Parameters<typeof NodeTestPanel>[0]> = {},
) {
    const requests = stubBackend({
        'GET /workflows/w1/executions': noRuns,
        ...handlers,
    })
    const onSave = vi.fn()
    render(
        <ApiClientContext value={new ApiClient()}>
            <QueryClientProvider client={createQueryClient()}>
                <NodeTestPanel
                    workflowId="w1"
                    nodeId={NODE}
                    definition={writer}
                    revision={7}
                    saved
                    onSave={onSave}
                    {...props}
                />
            </QueryClientProvider>
        </ApiClientContext>,
    )
    return { requests, onSave }
}

const testUrl = `POST /workflows/w1/nodes/${NODE}/test`
const field = (name: string) =>
    screen.getByRole('textbox', { name: new RegExp(`^${name}`) })
const runButton = () => screen.getByRole('button', { name: 'Run test' })

describe('NodeTestPanel', () => {
    it('sends text as text and JSON as data, then shows outputs and logs', async () => {
        let body: unknown
        setup({
            [testUrl]: (_url, init) => {
                body = JSON.parse(init.body as string)
                return result()
            },
        })
        fireEvent.change(field('path'), { target: { value: 'out/report.txt' } })
        fireEvent.change(field('meta'), { target: { value: '{"a": ' } })
        // Invalid JSON is caught here; the test cannot run.
        expect(screen.getByText('Not valid JSON.')).toBeInTheDocument()
        expect(runButton()).toBeDisabled()
        fireEvent.change(field('meta'), { target: { value: '{"a": 1}' } })
        fireEvent.click(runButton())

        const shown = within(
            await screen.findByRole('region', { name: 'Test result' }),
        )
        expect(body).toEqual({
            expected_revision: 7,
            inputs: { path: 'out/report.txt', meta: { a: 1 } },
            execution_input: {},
        })
        expect(shown.getByText('Succeeded')).toBeInTheDocument()
        expect(shown.getByText('out/report.txt')).toBeInTheDocument()
        expect(shown.getByText('wrote 5 bytes')).toBeInTheDocument()
    })

    it('shows a node failure as a result', async () => {
        setup({
            [testUrl]: () =>
                result({
                    status: 'failed',
                    outputs: null,
                    logs: [],
                    error: {
                        code: 'FILE_EXISTS',
                        message: 'the file already exists',
                        kind: 'permanent',
                        retryable: false,
                        details: {},
                    },
                }),
        })
        fireEvent.change(field('path'), { target: { value: 'a.txt' } })
        fireEvent.click(runButton())
        const shown = within(
            await screen.findByRole('region', { name: 'Test result' }),
        )
        expect(shown.getByText('Failed')).toBeInTheDocument()
        expect(shown.getByText('the file already exists')).toBeInTheDocument()
    })

    it('tests only the saved draft', () => {
        const { onSave } = setup({}, { saved: false })
        expect(runButton()).toBeDisabled()
        fireEvent.click(screen.getByRole('button', { name: 'Save changes' }))
        expect(onSave).toHaveBeenCalledOnce()
    })

    it('asks before a test with real effects', async () => {
        const { requests } = setup(
            { [testUrl]: () => result() },
            {
                definition: {
                    ...writer,
                    capabilities: { has_side_effects: true },
                },
            },
        )
        expect(screen.getByText(/Its effects are real/)).toBeInTheDocument()
        fireEvent.change(field('path'), { target: { value: 'a.txt' } })
        fireEvent.click(runButton())
        const confirm = within(
            screen.getByRole('dialog', { name: 'Run with real effects?' }),
        )
        expect(requests.some((u) => u.pathname.endsWith('/test'))).toBe(false)
        fireEvent.click(confirm.getByRole('button', { name: 'Run test' }))
        expect(
            await screen.findByRole('region', { name: 'Test result' }),
        ).toBeInTheDocument()
    })

    it('says so when the draft changed elsewhere', async () => {
        setup({
            [testUrl]: () =>
                apiError(409, 'CONCURRENT_MODIFICATION', 'changed'),
        })
        fireEvent.change(field('path'), { target: { value: 'a.txt' } })
        fireEvent.click(runButton())
        expect(
            await screen.findByText(/The draft was changed elsewhere/),
        ).toBeInTheDocument()
    })

    it('fills in what the node received in the last run', async () => {
        setup({
            'GET /workflows/w1/executions': () =>
                ok({
                    items: [
                        {
                            id: 'e1',
                            workflow_id: 'w1',
                            workflow_name: 'Report',
                            version: 3,
                            status: 'completed',
                            trigger_type: 'manual',
                            trigger_id: null,
                            correlation_id: null,
                            created_at: AT,
                            started_at: AT,
                            finished_at: AT,
                            cancellable: false,
                        },
                    ],
                    page: { offset: 0, limit: 1, has_more: false },
                }),
            'GET /reports/executions/e1': () =>
                ok({
                    schema_version: 1,
                    execution_id: 'e1',
                    workflow: {
                        id: 'w1',
                        name: 'Report',
                        version: 3,
                        version_description: '',
                        published_at: AT,
                    },
                    execution: {
                        status: 'completed',
                        trigger_type: 'manual',
                        trigger_id: null,
                        correlation_id: null,
                        engine_version: '1',
                        node_versions: {},
                        created_at: AT,
                        started_at: AT,
                        finished_at: AT,
                        duration_ms: 1,
                        input: {},
                        error: null,
                        cancellation: null,
                    },
                    nodes: [
                        {
                            node_id: NODE,
                            node_type: 'core.file_write',
                            label: '',
                            status: 'completed',
                            attempts: 1,
                            retries: 0,
                            started_at: AT,
                            finished_at: AT,
                            duration_ms: 1,
                            error: null,
                            failures: [],
                            inputs: [
                                {
                                    port: 'path',
                                    source_node_id: 'n0',
                                    source_port: 'value',
                                    value: 'daily.txt',
                                },
                                {
                                    port: 'meta',
                                    source_node_id: 'n0',
                                    source_port: 'value',
                                    value: { day: 1 },
                                },
                            ],
                            outputs: {},
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
                            input_tokens: 0,
                            output_tokens: 0,
                            total_tokens: 0,
                        },
                        costs: [],
                        cost_complete: true,
                    },
                    events: [],
                    events_truncated: false,
                }),
        })
        fireEvent.click(
            await screen.findByRole('button', {
                name: 'Use the input of the last run',
            }),
        )
        await waitFor(() => {
            expect(field('path')).toHaveValue('daily.txt')
        })
        expect(
            JSON.parse((field('meta') as HTMLTextAreaElement).value),
        ).toEqual({
            day: 1,
        })
    })
})
