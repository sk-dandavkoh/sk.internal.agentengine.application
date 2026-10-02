import { z } from 'zod'

// Every failure the API layer raises is an ApiError; `kind` is the
// discriminant callers branch on, `code` the backend's stable error code.
export type ApiErrorKind =
    | 'network' // backend unreachable (no HTTP response)
    | 'timeout'
    | 'cancelled' // the caller aborted the request
    | 'malformed_response' // response does not match the expected contract
    | 'validation' // 422
    | 'authentication' // 401
    | 'authorization' // 403
    | 'not_found' // 404
    | 'conflict' // 409
    | 'rate_limited' // 429
    | 'server' // 5xx
    | 'http' // any other 4xx

interface ApiErrorInit {
    kind: ApiErrorKind
    message: string
    status?: number | undefined
    code?: string | undefined
    details?: Record<string, unknown> | undefined
    requestId?: string | undefined
    cause?: unknown
}

export class ApiError extends Error {
    override readonly name = 'ApiError'
    readonly kind: ApiErrorKind
    /** HTTP status; undefined when no response arrived. */
    readonly status: number | undefined
    readonly code: string
    readonly details: Readonly<Record<string, unknown>>
    /** Correlates with the backend logs (X-Request-ID). */
    readonly requestId: string | undefined

    constructor(init: ApiErrorInit) {
        super(init.message, { cause: init.cause })
        this.kind = init.kind
        this.status = init.status
        this.code = init.code ?? init.kind.toUpperCase()
        this.details = init.details ?? {}
        this.requestId = init.requestId
    }
}

// The backend's error envelope (backend api/schemas.py ErrorResponse).
export const errorEnvelopeSchema = z.object({
    code: z.string(),
    message: z.string(),
    // Optional in the contract (the backend defaults it to {}).
    details: z.record(z.string(), z.unknown()).optional(),
    request_id: z.string(),
})

// User-facing fallbacks when the backend sent no envelope (e.g. a proxy's
// 502 page). Envelope messages are written for users and redacted server-side.
const FALLBACK_MESSAGES: Record<ApiErrorKind, string> = {
    network:
        'The server could not be reached. Check your connection and try again.',
    timeout: 'The server took too long to respond. Try again.',
    cancelled: 'The request was cancelled.',
    malformed_response: 'The server sent an unexpected response.',
    validation: 'The request was invalid.',
    authentication: 'Your session has expired. Sign in again.',
    authorization: 'You do not have permission to do this.',
    not_found: 'The requested resource was not found.',
    conflict:
        'The request conflicts with the current state. Reload and try again.',
    rate_limited: 'Too many requests. Wait a moment and try again.',
    server: 'The server is unavailable. Try again later.',
    http: 'The request failed.',
}

export function fallbackMessage(kind: ApiErrorKind): string {
    return FALLBACK_MESSAGES[kind]
}

function kindForStatus(status: number): ApiErrorKind {
    switch (status) {
        case 401:
            return 'authentication'
        case 403:
            return 'authorization'
        case 404:
            return 'not_found'
        case 409:
            return 'conflict'
        case 422:
            return 'validation'
        case 429:
            return 'rate_limited'
        default:
            return status >= 500 ? 'server' : 'http'
    }
}

export function httpError(
    status: number,
    body: string,
    requestId: string,
): ApiError {
    const kind = kindForStatus(status)
    const envelope = errorEnvelopeSchema.safeParse(parseJson(body))
    if (!envelope.success) {
        return new ApiError({
            kind,
            status,
            message: fallbackMessage(kind),
            requestId,
        })
    }
    const { code, message, details, request_id } = envelope.data
    return new ApiError({
        kind,
        status,
        code,
        message,
        details,
        requestId: request_id,
    })
}

function parseJson(text: string): unknown {
    try {
        return JSON.parse(text)
    } catch {
        // Not JSON is a valid outcome here; callers validate the result.
        return undefined
    }
}

/** A message fit for users: ApiError messages are written for them;
 * anything else is a bug and its message is not. */
export function errorMessage(error: unknown): string {
    return error instanceof ApiError
        ? error.message
        : 'An unexpected error occurred.'
}
