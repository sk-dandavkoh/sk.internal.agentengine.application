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
    useDeleteProject,
    useDuplicateProject,
    useExecuteProjectVersion,
    usePublishProject,
    useUpdateProject,
} from './mutations'
import {
    executionQueries,
    projectQueries,
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
const notFound = (): Reply => error(404, 'PROJECT_NOT_FOUND', 'Not found.')
function error(status: number, code: string, message: string): Reply {
    return { status, body: { code, message, details: {}, request_id: 'r' } }
}
const count = (key: string) => calls.filter((c) => c === key).length

// -- factories (valid by default) -----------------------------------------------

const AT = '2026-10-02T08:00:00Z'

function projectJson(overrides: Record<string, unknown> = {}) {
    return {
        id: 'proj_1',
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
    }
}

function executionJson(overrides: Record<string, unknown> = {}) {
    return {
        id: 'exec_1',
        project_id: 'proj_1',
        project_name: 'Lead scoring',
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
const useProject = (id = 'proj_1') =>
    useQuery(projectQueries.detail(useApiClient(), id))
const useProjects = () => useQuery(projectQueries.list(useApiClient()))

// -- queries ------------------------------------------------------------------------

describe('queries', () => {
    it('load, then expose the parsed response', async () => {
        routes['GET /projects/proj_1'] = () =>
            // The backend may omit fields that have defaults.
            ok(projectJson({ draft: {} }))

        const { result } = render(useProject)

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
        routes['GET /projects/proj_1'] = () => ok(projectJson())

        const a = render(useProject)
        const b = render(useProject)

        await waitFor(() => {
            expect(a.result.current.isSuccess).toBe(true)
            expect(b.result.current.isSuccess).toBe(true)
        })
        expect(count('GET /projects/proj_1')).toBe(1)
    })

    it('surface backend errors as ApiError, without retrying them', async () => {
        const { result } = render(useProject)

        await waitFor(() => {
            expect(result.current.isError).toBe(true)
        })
        expect(result.current.error).toBeInstanceOf(ApiError)
        expect(result.current.error).toMatchObject({
            kind: 'not_found',
            code: 'PROJECT_NOT_FOUND',
            message: 'Not found.',
        })
        expect(count('GET /projects/proj_1')).toBe(1)
    })

    it('reject responses that break the contract', async () => {
        routes['GET /projects/proj_1'] = () =>
            ok(projectJson({ status: 'deleted' }))

        const { result } = render(useProject)

        await waitFor(() => {
            expect(result.current.error).toMatchObject({
                kind: 'malformed_response',
            })
        })
        expect(result.current.data).toBeUndefined()
    })

    it('show stale data while refetching it on the next mount', async () => {
        let name = 'Old'
        routes['GET /projects/proj_1'] = () => ok(projectJson({ name }))
        const first = render(useProject)
        await waitFor(() => {
            expect(first.result.current.isSuccess).toBe(true)
        })
        first.unmount()
        name = 'New'

        const second = render(useProject)

        expect(second.result.current.data?.name).toBe('Old')
        expect(second.result.current.isFetching).toBe(true)
        await waitFor(() => {
            expect(second.result.current.data?.name).toBe('New')
        })
        expect(count('GET /projects/proj_1')).toBe(2)
    })

    it('never refetch an immutable published version', async () => {
        routes['GET /projects/proj_1/versions/1'] = () => ok(versionJson())
        const useVersion = () =>
            useQuery(projectQueries.version(useApiClient(), 'proj_1', 1))
        const first = render(useVersion)
        await waitFor(() => {
            expect(first.result.current.isSuccess).toBe(true)
        })
        first.unmount()

        const second = render(useVersion)

        expect(second.result.current.isStale).toBe(false)
        expect(second.result.current.isFetching).toBe(false)
        expect(count('GET /projects/proj_1/versions/1')).toBe(1)
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

    it('save a draft: cache the saved project, refetch the lists', async () => {
        routes['GET /projects/proj_1'] = () => ok(projectJson())
        routes['GET /projects'] = () => ok(page([projectJson()]))
        routes['PUT /projects/proj_1'] = () =>
            ok(projectJson({ name: 'Renamed', revision: 2 }))
        const project = await loaded(useProject)
        await loaded(useProjects)
        const update = render(useUpdateProject)

        await act(() =>
            update.result.current.mutateAsync({
                projectId: 'proj_1',
                input: {
                    name: 'Renamed',
                    description: '',
                    draft: projectJson().draft,
                    expected_revision: 1,
                },
            }),
        )

        await waitFor(() => {
            expect(project.result.current.data).toMatchObject({
                name: 'Renamed',
                revision: 2,
            })
        })
        expect(count('GET /projects/proj_1')).toBe(1) // set, not refetched
        expect(count('GET /projects')).toBe(2)
    })

    it('a failed write leaves the cache untouched and is not retried', async () => {
        routes['GET /projects/proj_1'] = () => ok(projectJson())
        routes['GET /projects'] = () => ok(page([projectJson()]))
        routes['PUT /projects/proj_1'] = () =>
            error(409, 'CONCURRENT_MODIFICATION', 'The draft changed.')
        const project = await loaded(useProject)
        await loaded(useProjects)
        const update = render(useUpdateProject)

        act(() => {
            update.result.current.mutate({
                projectId: 'proj_1',
                input: {
                    name: 'Renamed',
                    description: '',
                    draft: projectJson().draft,
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
        expect(project.result.current.data?.name).toBe('Digest')
        expect(count('PUT /projects/proj_1')).toBe(1)
        expect(count('GET /projects')).toBe(1)
    })

    it('publish refetches the project and its versions, not other projects', async () => {
        routes['GET /projects/proj_1'] = () => ok(projectJson())
        routes['GET /projects/proj_2'] = () => ok(projectJson({ id: 'proj_2' }))
        routes['GET /projects/proj_1/versions'] = () => ok(page([]))
        routes['POST /projects/proj_1/publish'] = () => ok(versionJson(), 201)
        await loaded(useProject)
        await loaded(() => useProject('proj_2'))
        const versions = await loaded(() =>
            useQuery(projectQueries.versions(useApiClient(), 'proj_1')),
        )
        routes['GET /projects/proj_1/versions'] = () =>
            ok(page([versionJson()]))
        const publish = render(usePublishProject)

        await act(() =>
            publish.result.current.mutateAsync({
                projectId: 'proj_1',
                expectedRevision: 1,
            }),
        )

        await waitFor(() => {
            expect(versions.result.current.data?.items).toHaveLength(1)
        })
        expect(count('GET /projects/proj_1')).toBe(2)
        expect(count('GET /projects/proj_2')).toBe(1)
    })

    it('delete drops every cached query of the project', async () => {
        routes['GET /projects/proj_1'] = () => ok(projectJson())
        routes['DELETE /projects/proj_1'] = () => ({ status: 204 })
        const project = await loaded(useProject)
        project.unmount()
        const remove = render(useDeleteProject)

        await act(() => remove.result.current.mutateAsync('proj_1'))

        expect(
            queryClient.getQueryData(queryKeys.projects.detail('proj_1')),
        ).toBeUndefined()
    })

    it('duplicate refetches the lists and leaves the source untouched', async () => {
        routes['GET /projects/proj_1'] = () => ok(projectJson())
        routes['GET /projects'] = () => ok(page([projectJson()]))
        routes['POST /projects/proj_1/duplicate'] = () =>
            ok(projectJson({ id: 'proj_2', name: 'Copy' }), 201)
        await loaded(useProject)
        await loaded(useProjects)
        const duplicate = render(useDuplicateProject)

        const copy = await act(() =>
            duplicate.result.current.mutateAsync({
                projectId: 'proj_1',
                name: 'Copy',
            }),
        )

        expect(copy.id).toBe('proj_2')
        expect(count('GET /projects')).toBe(2)
        expect(count('GET /projects/proj_1')).toBe(1)
    })

    it('starting and cancelling executions refetch what they change', async () => {
        routes['GET /projects/proj_1/executions'] = () => ok(page([]))
        routes['GET /executions/exec_1'] = () => ok(executionJson())
        routes['POST /projects/proj_1/versions/1/execute'] = () =>
            ok(executionJson(), 201)
        routes['POST /executions/exec_1/cancel'] = () =>
            ok(executionJson({ status: 'running' }), 202)
        // The dashboard's cross-project views.
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
            useQuery(projectQueries.executions(useApiClient(), 'proj_1')),
        )
        const execution = await loaded(() =>
            useQuery(executionQueries.detail(useApiClient(), 'exec_1')),
        )
        const execute = render(useExecuteProjectVersion)
        const cancel = render(useCancelExecution)

        await act(() =>
            execute.result.current.mutateAsync({
                projectId: 'proj_1',
                version: 1,
                input: { topic: 'news' },
            }),
        )
        expect(count('GET /projects/proj_1/executions')).toBe(2)
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
        expect(count('GET /projects/proj_1/executions')).toBe(3)
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
