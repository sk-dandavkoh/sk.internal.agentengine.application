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
import { ToastProvider } from '../components/ui'
import { expireSession } from '../features/auth/session'

export interface Reply {
    status: number
    body?: unknown
}
/** Answers one request; a never-settling promise keeps it loading. */
export type Handler = (url: URL, init: RequestInit) => Reply | Promise<Reply>

export const ok = (body: unknown): Reply => ({ status: 200, body })
export function apiError(status: number, code: string, message: string) {
    return { status, body: { code, message, details: {}, request_id: 'r' } }
}
export const pending = () => new Promise<Reply>(() => undefined)

export const testUser = {
    id: 'u_admin',
    username: 'ada',
    display_name: null,
    role: 'admin',
    disabled: false,
    created_at: '2026-10-01T08:00:00Z',
    updated_at: '2026-10-01T08:00:00Z',
    last_login_at: null,
}
export const testSession = {
    expires_at: '2026-10-05T20:00:00Z',
    user: testUser,
}
export const noSession = () =>
    apiError(401, 'AUTHENTICATION_REQUIRED', 'Authentication required.')

/**
 * Stubs `fetch` with `handlers`, keyed `"GET /projects"` (path below
 * /api/v1). Unknown routes answer 404. Signed in as `testUser` unless
 * `GET /auth/session` is overridden. Returns the request log (without the
 * session checks).
 */
export function stubBackend(handlers: Record<string, Handler> = {}) {
    handlers = { 'GET /auth/session': () => ok(testSession), ...handlers }
    const requests: URL[] = []
    vi.stubGlobal('fetch', async (input: string, init: RequestInit) => {
        const url = new URL(input, 'http://app.test')
        const key = `${init.method ?? 'GET'} ${url.pathname.replace('/api/v1', '')}`
        if (key !== 'GET /auth/session') requests.push(url)
        const handler = handlers[key]
        const reply = handler
            ? await handler(url, init)
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
    const client = new ApiClient({
        onSessionEnded: () => {
            expireSession(queryClient)
        },
    })
    render(
        <ApiClientContext value={client}>
            <QueryClientProvider client={queryClient}>
                <ToastProvider>
                    <RouterProvider router={router} />
                </ToastProvider>
            </QueryClientProvider>
        </ApiClientContext>,
    )
    return { router, queryClient, ready: routerReady(router) }
}

/**
 * Settles once the initial navigation finished: lazy route modules are
 * loaded and loaders ran. Await it when a test must act before anything
 * is findable (e.g. advancing fake timers), or to time rendering alone.
 */
function routerReady(router: ReturnType<typeof createMemoryRouter>) {
    return new Promise<void>((resolve) => {
        if (router.state.initialized) {
            resolve()
            return
        }
        const unsubscribe = router.subscribe((state) => {
            if (!state.initialized) return
            unsubscribe()
            resolve()
        })
    })
}
