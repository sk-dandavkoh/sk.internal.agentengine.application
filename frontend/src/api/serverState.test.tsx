// Server-state behavior: queries, cache invalidation and mutations through
// the real ApiClient and QueryClient. Only the network is replaced: a stub
// `fetch` answers like the backend, so response parsing and error mapping
// are the production code paths.
import {
    QueryClientProvider,
    useQuery,
    type QueryClient,
} from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiClient } from './client'
import { ApiClientContext, useApiClient } from './context'
import { ApiError } from './errors'
import {
    useCancelExecution,
    useCreateCredential,
    useDeleteWorkflow,
    useDuplicateWorkflow,
    useExecuteWorkflowVersion,
    usePublishWorkflow,
    useUpdateWorkflow,
} from './mutations'
import {
    executionQueries,
    workflowQueries,
    queryKeys,
    credentialQueries,
} from './queries'
import { executionStatusSchema } from './executions'
import { createQueryClient } from './queryClient'

// -- fake backend ----------------------------------------------------------------

interface Reply {
    status: number
    body?: unknown
}
let routes: Record<string, () => Reply>
let calls: string[]

beforeEach(() => {
    routes = {}
    calls = []
    vi.stubGlobal('fetch', (url: string, init: RequestInit) => {
        const key = `${init.method ?? 'GET'} ${new URL(url).pathname.replace('/api/v1', '')}`
        calls.push(key)
        const route = routes[key]
        const reply = route ? route() : notFound()
        return Promise.resolve(
            new Response(
                reply.body === undefined ? null : JSON.stringify(reply.body),
                {
                    status: reply.status,
                    headers: { 'Content-Type': 'application/json' },
                },
            ),
        )
    })
})
afterEach(() => {
    vi.unstubAllGlobals()
})

const ok = (body: unknown, status = 200): Reply => ({ status, body })
const notFound = (): Reply => error(404, 'WORKFLOW_NOT_FOUND', 'Not found.')
function error(status: number, code: string, message: string): Reply {
    return { status, body: { code, message, details: {}, request_id: 'r' } }
}
const count = (key: string) => calls.filter((c) => c === key).length

// -- factories (valid by default) -----------------------------------------------

const AT = '2026-10-02T08:00:00Z'

function workflowJson(overrides: Record<string, unknown> = {}) {
    return {
        id: 'wf_1',
        name: 'Digest',
        description: '',
        status: 'draft',
        owner_id: 'user_1',
        revision: 1,
        current_version: 0,
        created_at: AT,
        updated_at: AT,
        draft: {
            graph: { nodes: [], edges: [] },
            triggers: [],
            dependencies: [],
        },
        files: [],
        ...overrides,
    }
}

function versionJson(version = 1) {
    return {
        id: `ver_${String(version)}`,
        version,
        description: '',
        schema_version: 1,
        published_at: AT,
        published_by: null,
        graph: { nodes: [], edges: [] },
        triggers: [],
        dependencies: [],
        files: [],
        plugin_versions: {},
    }
}

function executionJson(overrides: Record<string, unknown> = {}) {
    return {
        id: 'exec_1',
        workflow_id: 'wf_1',
        workflow_name: 'Lead scoring',
        version: 1,
        status: 'queued',
        trigger_type: 'manual',
        trigger_id: null,
        correlation_id: null,
        created_at: AT,
        started_at: null,
        finished_at: null,
        cancellable: true,
        ...overrides,
    }
}

const page = (items: unknown[]) => ({
    items,
    page: { offset: 0, limit: 50, has_more: false },
})

// -- harness ----------------------------------------------------------------------

let queryClient: QueryClient

function wrapper({ children }: { children: ReactNode }) {
    return (
        <ApiClientContext
            value={new ApiClient({ baseUrl: 'http://backend.test/api/v1' })}
        >
            <QueryClientProvider client={queryClient}>
                {children}
            </QueryClientProvider>
        </ApiClientContext>
    )
}

beforeEach(() => {
    queryClient = createQueryClient()
})
afterEach(() => {
    queryClient.clear()
})

const render = <T,>(hook: () => T) => renderHook(hook, { wrapper })
const useWorkflow = (id = 'wf_1') =>
    useQuery(workflowQueries.detail(useApiClient(), id))
const useWorkflows = () => useQuery(workflowQueries.list(useApiClient()))

// -- queries ------------------------------------------------------------------------

describe('queries', () => {
    it('load, then expose the parsed response', async () => {
        routes['GET /workflows/wf_1'] = () =>
            // The backend may omit fields that have defaults.
            ok(workflowJson({ draft: {} }))

        const { result } = render(useWorkflow)

        expect(result.current.isPending).toBe(true)
        await waitFor(() => {
            expect(result.current.isSuccess).toBe(true)
        })
        expect(result.current.data?.draft).toEqual({
            graph: { nodes: [], edges: [] },
            triggers: [],
            dependencies: [],
        })
    })

    it('share one request between observers of the same key', async () => {
        routes['GET /workflows/wf_1'] = () => ok(workflowJson())

        const a = render(useWorkflow)
        const b = render(useWorkflow)

        await waitFor(() => {
            expect(a.result.current.isSuccess).toBe(true)
            expect(b.result.current.isSuccess).toBe(true)
        })
        expect(count('GET /workflows/wf_1')).toBe(1)
    })

    it('surface backend errors as ApiError, without retrying them', async () => {
        const { result } = render(useWorkflow)

        await waitFor(() => {
            expect(result.current.isError).toBe(true)
        })
        expect(result.current.error).toBeInstanceOf(ApiError)
        expect(result.current.error).toMatchObject({
            kind: 'not_found',
            code: 'WORKFLOW_NOT_FOUND',
            message: 'Not found.',
        })
        expect(count('GET /workflows/wf_1')).toBe(1)
    })

    it('reject responses that break the contract', async () => {
        routes['GET /workflows/wf_1'] = () =>
            ok(workflowJson({ status: 'deleted' }))

        const { result } = render(useWorkflow)

        await waitFor(() => {
            expect(result.current.error).toMatchObject({
                kind: 'malformed_response',
            })
        })
        expect(result.current.data).toBeUndefined()
    })

    it('show stale data while refetching it on the next mount', async () => {
        let name = 'Old'
        routes['GET /workflows/wf_1'] = () => ok(workflowJson({ name }))
        const first = render(useWorkflow)
        await waitFor(() => {
            expect(first.result.current.isSuccess).toBe(true)
        })
        first.unmount()
        name = 'New'

        const second = render(useWorkflow)

        expect(second.result.current.data?.name).toBe('Old')
        expect(second.result.current.isFetching).toBe(true)
        await waitFor(() => {
            expect(second.result.current.data?.name).toBe('New')
        })
        expect(count('GET /workflows/wf_1')).toBe(2)
    })

    it('never refetch an immutable published version', async () => {
        routes['GET /workflows/wf_1/versions/1'] = () => ok(versionJson())
        const useVersion = () =>
            useQuery(workflowQueries.version(useApiClient(), 'wf_1', 1))
        const first = render(useVersion)
        await waitFor(() => {
            expect(first.result.current.isSuccess).toBe(true)
        })
        first.unmount()

        const second = render(useVersion)

        expect(second.result.current.isStale).toBe(false)
        expect(second.result.current.isFetching).toBe(false)
        expect(count('GET /workflows/wf_1/versions/1')).toBe(1)
    })
})

// -- mutations ------------------------------------------------------------------------

describe('mutations', () => {
    async function loaded<R extends { data: unknown }>(hook: () => R) {
        const rendered = render(hook)
        // Reading `data` subscribes to it, as a component rendering it
        // would: query results only notify about properties that were read.
        await waitFor(() => {
            expect(rendered.result.current.data).toBeDefined()
        })
        return rendered
    }

    it('save a draft: cache the saved workflow, refetch the lists', async () => {
        routes['GET /workflows/wf_1'] = () => ok(workflowJson())
        routes['GET /workflows'] = () => ok(page([workflowJson()]))
        routes['PUT /workflows/wf_1'] = () =>
            ok(workflowJson({ name: 'Renamed', revision: 2 }))
        const workflow = await loaded(useWorkflow)
        await loaded(useWorkflows)
        const update = render(useUpdateWorkflow)

        await act(() =>
            update.result.current.mutateAsync({
                workflowId: 'wf_1',
                input: {
                    name: 'Renamed',
                    description: '',
                    draft: workflowJson().draft,
                    expected_revision: 1,
                },
            }),
        )

        await waitFor(() => {
            expect(workflow.result.current.data).toMatchObject({
                name: 'Renamed',
                revision: 2,
            })
        })
        expect(count('GET /workflows/wf_1')).toBe(1) // set, not refetched
        expect(count('GET /workflows')).toBe(2)
    })

    it('a failed write leaves the cache untouched and is not retried', async () => {
        routes['GET /workflows/wf_1'] = () => ok(workflowJson())
        routes['GET /workflows'] = () => ok(page([workflowJson()]))
        routes['PUT /workflows/wf_1'] = () =>
            error(409, 'CONCURRENT_MODIFICATION', 'The draft changed.')
        const workflow = await loaded(useWorkflow)
        await loaded(useWorkflows)
        const update = render(useUpdateWorkflow)

        act(() => {
            update.result.current.mutate({
                workflowId: 'wf_1',
                input: {
                    name: 'Renamed',
                    description: '',
                    draft: workflowJson().draft,
                    expected_revision: 0,
                },
            })
        })

        await waitFor(() => {
            expect(update.result.current.isError).toBe(true)
        })
        expect(update.result.current.error).toMatchObject({
            kind: 'conflict',
            code: 'CONCURRENT_MODIFICATION',
        })
        expect(workflow.result.current.data?.name).toBe('Digest')
        expect(count('PUT /workflows/wf_1')).toBe(1)
        expect(count('GET /workflows')).toBe(1)
    })

    it('publish refetches the workflow and its versions, not other workflows', async () => {
        routes['GET /workflows/wf_1'] = () => ok(workflowJson())
        routes['GET /workflows/wf_2'] = () => ok(workflowJson({ id: 'wf_2' }))
        routes['GET /workflows/wf_1/versions'] = () => ok(page([]))
        routes['POST /workflows/wf_1/publish'] = () => ok(versionJson(), 201)
        await loaded(useWorkflow)
        await loaded(() => useWorkflow('wf_2'))
        const versions = await loaded(() =>
            useQuery(workflowQueries.versions(useApiClient(), 'wf_1')),
        )
        routes['GET /workflows/wf_1/versions'] = () => ok(page([versionJson()]))
        const publish = render(usePublishWorkflow)

        await act(() =>
            publish.result.current.mutateAsync({
                workflowId: 'wf_1',
                expectedRevision: 1,
            }),
        )

        await waitFor(() => {
            expect(versions.result.current.data?.items).toHaveLength(1)
        })
        expect(count('GET /workflows/wf_1')).toBe(2)
        expect(count('GET /workflows/wf_2')).toBe(1)
    })

    it('delete drops every cached query of the workflow', async () => {
        routes['GET /workflows/wf_1'] = () => ok(workflowJson())
        routes['DELETE /workflows/wf_1'] = () => ({ status: 204 })
        const workflow = await loaded(useWorkflow)
        workflow.unmount()
        const remove = render(useDeleteWorkflow)

        await act(() => remove.result.current.mutateAsync('wf_1'))

        expect(
            queryClient.getQueryData(queryKeys.workflows.detail('wf_1')),
        ).toBeUndefined()
    })

    it('duplicate refetches the lists and leaves the source untouched', async () => {
        routes['GET /workflows/wf_1'] = () => ok(workflowJson())
        routes['GET /workflows'] = () => ok(page([workflowJson()]))
        routes['POST /workflows/wf_1/duplicate'] = () =>
            ok(workflowJson({ id: 'wf_2', name: 'Copy' }), 201)
        await loaded(useWorkflow)
        await loaded(useWorkflows)
        const duplicate = render(useDuplicateWorkflow)

        const copy = await act(() =>
            duplicate.result.current.mutateAsync({
                workflowId: 'wf_1',
                name: 'Copy',
            }),
        )

        expect(copy.id).toBe('wf_2')
        expect(count('GET /workflows')).toBe(2)
        expect(count('GET /workflows/wf_1')).toBe(1)
    })

    it('starting and cancelling executions refetch what they change', async () => {
        routes['GET /workflows/wf_1/executions'] = () => ok(page([]))
        routes['GET /executions/exec_1'] = () => ok(executionJson())
        routes['POST /workflows/wf_1/versions/1/execute'] = () =>
            ok(executionJson(), 201)
        routes['POST /executions/exec_1/cancel'] = () =>
            ok(executionJson({ status: 'running' }), 202)
        // The dashboard's cross-workflow views.
        routes['GET /executions'] = () => ok(page([]))
        routes['GET /executions/status-counts'] = () =>
            ok({
                counts: Object.fromEntries(
                    executionStatusSchema.options.map((s) => [s, 0]),
                ),
            })
        await loaded(() =>
            useQuery(executionQueries.list(useApiClient(), { limit: 5 })),
        )
        await loaded(() =>
            useQuery(executionQueries.statusCounts(useApiClient())),
        )
        await loaded(() =>
            useQuery(workflowQueries.executions(useApiClient(), 'wf_1')),
        )
        const execution = await loaded(() =>
            useQuery(executionQueries.detail(useApiClient(), 'exec_1')),
        )
        const execute = render(useExecuteWorkflowVersion)
        const cancel = render(useCancelExecution)

        await act(() =>
            execute.result.current.mutateAsync({
                workflowId: 'wf_1',
                version: 1,
                input: { topic: 'news' },
            }),
        )
        expect(count('GET /workflows/wf_1/executions')).toBe(2)
        expect(count('GET /executions')).toBe(2)
        expect(count('GET /executions/status-counts')).toBe(2)

        routes['GET /executions/exec_1'] = () =>
            ok(executionJson({ status: 'cancelled' }))
        await act(() =>
            cancel.result.current.mutateAsync({ executionId: 'exec_1' }),
        )
        await waitFor(() => {
            expect(execution.result.current.data?.status).toBe('cancelled')
        })
        expect(count('GET /workflows/wf_1/executions')).toBe(3)
        expect(count('GET /executions')).toBe(3)
        expect(count('GET /executions/status-counts')).toBe(3)
    })

    it('credential secrets do not outlive the mutation in the cache', async () => {
        const secret = 'sk-live-do-not-cache'
        routes['GET /credentials'] = () => ok(page([]))
        routes['POST /credentials'] = () =>
            ok(
                {
                    name: 'openai',
                    kind: 'api_key',
                    description: '',
                    reference: 'cred:openai',
                    created_at: AT,
                    updated_at: AT,
                },
                201,
            )
        await loaded(() => useQuery(credentialQueries.list(useApiClient())))
        const create = render(useCreateCredential)

        await act(() =>
            create.result.current.mutateAsync({
                name: 'openai',
                kind: 'api_key',
                value: secret,
            }),
        )
        expect(count('GET /credentials')).toBe(2)
        create.unmount()

        await waitFor(() => {
            expect(queryClient.getMutationCache().getAll()).toEqual([])
        })
        const cached = queryClient
            .getQueryCache()
            .getAll()
            .map((q) => JSON.stringify(q.state.data))
        expect(cached.join()).not.toContain(secret)
    })
})
