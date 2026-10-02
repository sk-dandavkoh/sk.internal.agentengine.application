import { QueryClient } from '@tanstack/react-query'
import { ApiError } from './errors'

const MAX_QUERY_RETRIES = 2
const TRANSIENT_STATUSES = new Set([502, 503, 504])

/**
 * Reads retry only transient failures (backend unreachable or restarting).
 * Client errors, 500s and malformed responses fail the same way on retry,
 * and a cancelled request must stay cancelled.
 */
export function shouldRetryQuery(
    failureCount: number,
    error: unknown,
): boolean {
    if (failureCount >= MAX_QUERY_RETRIES || !(error instanceof ApiError))
        return false
    return (
        error.kind === 'network' ||
        error.kind === 'timeout' ||
        (error.status !== undefined && TRANSIENT_STATUSES.has(error.status))
    )
}

export function createQueryClient(): QueryClient {
    return new QueryClient({
        defaultOptions: {
            queries: { retry: shouldRetryQuery },
            // Writes are never retried automatically: a timed-out POST may
            // already have taken effect (at-least-once side effects).
            mutations: { retry: false },
        },
    })
}
