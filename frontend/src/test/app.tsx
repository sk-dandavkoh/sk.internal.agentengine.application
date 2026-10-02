// Test harness for screens: the real router, ApiClient and QueryClient, with
// only the network replaced by a stub backend (no hidden network calls).
import { QueryClientProvider } from '@tanstack/react-query'
import { render } from '@testing-library/react'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { vi } from 'vitest'
import { ApiClient } from '../api/client'
import { ApiClientContext } from '../api/context'
import { createQueryClient } from '../api/queryClient'
import { routes } from '../app/routes'

export interface Reply {
    status: number
    body?: unknown
}
/** Answers one request; a never-settling promise keeps it loading. */
export type Handler = (url: URL) => Reply | Promise<Reply>

export const ok = (body: unknown): Reply => ({ status: 200, body })
export function apiError(status: number, code: string, message: string) {
    return { status, body: { code, message, details: {}, request_id: 'r' } }
}
export const pending = () => new Promise<Reply>(() => undefined)

/**
 * Stubs `fetch` with `handlers`, keyed `"GET /projects"` (path below
 * /api/v1). Unknown routes answer 404. Returns the request log.
 */
export function stubBackend(handlers: Record<string, Handler> = {}) {
    const requests: URL[] = []
    vi.stubGlobal('fetch', async (input: string, init: RequestInit) => {
        const url = new URL(input, 'http://app.test')
        requests.push(url)
        const key = `${init.method ?? 'GET'} ${url.pathname.replace('/api/v1', '')}`
        const handler = handlers[key]
        const reply = handler
            ? await handler(url)
            : apiError(404, 'NOT_FOUND', 'Not found.')
        return new Response(
            reply.body === undefined ? null : JSON.stringify(reply.body),
            {
                status: reply.status,
                headers: { 'Content-Type': 'application/json' },
            },
        )
    })
    return requests
}

export function renderRoute(path: string) {
    const router = createMemoryRouter(routes, { initialEntries: [path] })
    const queryClient = createQueryClient()
    render(
        <ApiClientContext value={new ApiClient()}>
            <QueryClientProvider client={queryClient}>
                <RouterProvider router={router} />
            </QueryClientProvider>
        </ApiClientContext>,
    )
    return { router, queryClient }
}
