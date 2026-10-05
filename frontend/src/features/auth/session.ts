import {
    queryOptions,
    useMutation,
    useQuery,
    useQueryClient,
    type QueryClient,
} from '@tanstack/react-query'
import {
    fetchSession,
    fetchSetupStatus,
    login,
    logout,
    setup,
    type Credentials,
} from '../../api/auth'
import type { Session } from '../../api/auth'
import type { ApiClient } from '../../api/client'
import { useApiClient } from '../../api/context'
import { ApiError } from '../../api/errors'
import { queryKeys } from '../../api/queries'

// The one place the UI knows whether someone is signed in. The session is
// an HttpOnly cookie (ADR 0002): the UI holds who and until when, never the
// token, and asks the backend (GET /auth/session) after every reload.

export type SessionState =
    | { status: 'active'; session: Session }
    /** `expired`: a session this tab had ended (vs. never signed in). */
    | { status: 'anonymous'; expired: boolean }

// Re-checked on focus after this long; a request that meets a dead session
// ends it at once anyway (expireSession).
const SESSION_STALE_TIME_MS = 60_000

function sessionQuery(client: ApiClient, queryClient: QueryClient) {
    return queryOptions({
        queryKey: queryKeys.session(),
        queryFn: async ({ signal }): Promise<SessionState> => {
            const previous = queryClient.getQueryData<SessionState>(
                queryKeys.session(),
            )
            try {
                return {
                    status: 'active',
                    session: await fetchSession(client, signal),
                }
            } catch (error) {
                // Anything but "no session" (backend down, 5xx) is an error
                // to show, not a reason to send the user to the login page.
                if (
                    !(error instanceof ApiError) ||
                    error.kind !== 'authentication'
                )
                    throw error
                return {
                    status: 'anonymous',
                    expired:
                        previous !== undefined &&
                        (previous.status === 'active' || previous.expired),
                }
            }
        },
        staleTime: SESSION_STALE_TIME_MS,
    })
}

export function useSession() {
    return useQuery(sessionQuery(useApiClient(), useQueryClient()))
}

/** The backend refused a request for a dead session (expired, revoked,
 * logged out elsewhere): the route guard takes the user to the login page. */
export function expireSession(queryClient: QueryClient) {
    const current = queryClient.getQueryData<SessionState>(queryKeys.session())
    const ended: SessionState = { status: 'anonymous', expired: true }
    if (current?.status === 'active')
        queryClient.setQueryData(queryKeys.session(), ended)
}

function switchSession(queryClient: QueryClient, state: SessionState) {
    queryClient.setQueryData(queryKeys.session(), state)
}

/** Drops every user's server state but the session itself. Called by the
 * guard once a session ended and the pages are unmounted (mounted ones
 * would refetch right away), so the next user never sees it. */
export function forgetServerState(queryClient: QueryClient) {
    queryClient.removeQueries({
        predicate: (query) => query.queryKey[0] !== 'auth',
    })
}

export function useLogin() {
    const client = useApiClient()
    const queryClient = useQueryClient()
    return useMutation({
        mutationFn: (credentials: Credentials) => login(client, credentials),
        onSuccess: (session) => {
            switchSession(queryClient, { status: 'active', session })
        },
        // The variables hold the password: gone as soon as nobody watches.
        gcTime: 0,
    })
}

/** A fresh installation has no account yet: the login page offers setup. */
export function useSetupStatus() {
    const client = useApiClient()
    return useQuery({
        queryKey: queryKeys.setupStatus(),
        queryFn: ({ signal }) => fetchSetupStatus(client, signal),
    })
}

/** Creates the first admin, then signs in as it. A 409 means someone else
 * finished setup first: the status is refetched and sign-in is offered. */
export function useSetup() {
    const client = useApiClient()
    const queryClient = useQueryClient()
    return useMutation({
        mutationFn: async (credentials: Credentials) => {
            await setup(client, credentials)
            return login(client, credentials)
        },
        onSuccess: (session) => {
            queryClient.setQueryData(queryKeys.setupStatus(), {
                required: false,
            })
            switchSession(queryClient, { status: 'active', session })
        },
        onError: (error) => {
            if (error instanceof ApiError && error.kind === 'conflict')
                void queryClient.invalidateQueries({
                    queryKey: queryKeys.setupStatus(),
                })
        },
        // The variables hold the password: gone as soon as nobody watches.
        gcTime: 0,
    })
}

export function useLogout() {
    const client = useApiClient()
    const queryClient = useQueryClient()
    const signedOut = () => {
        switchSession(queryClient, { status: 'anonymous', expired: false })
    }
    return useMutation({
        mutationFn: () => logout(client),
        onSuccess: signedOut,
        onError: (error) => {
            // Already dead server-side: signed out either way. Any other
            // failure leaves the session (and its cookie) valid, so the UI
            // must not pretend otherwise.
            if (error instanceof ApiError && error.kind === 'authentication')
                signedOut()
        },
    })
}
