import type { z } from 'zod'
import { ApiError, fallbackMessage, httpError } from './errors'

export type HttpMethod = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'

export interface ApiClientOptions {
    /** Prefix for every path. Same-origin by default (see README). */
    baseUrl?: string
    /** Per-request default; a request may override it. */
    timeoutMs?: number
    /** Called when the backend says the session is gone (401 other than a
     * wrong password), whatever request noticed; the auth feature owns
     * what happens next. */
    onSessionEnded?: () => void
}

export interface RequestOptions {
    /** JSON-serialized request body. */
    body?: unknown
    /** Sent as-is instead of `body`, e.g. an uploaded package. */
    rawBody?: { data: Blob; contentType: string }
    /** An array value repeats the parameter (`?status=a&status=b`). */
    query?: Record<
        string,
        string | number | boolean | readonly string[] | undefined
    >
    /** Caller cancellation, e.g. the AbortSignal TanStack Query passes in. */
    signal?: AbortSignal | undefined
    timeoutMs?: number
}

export const DEFAULT_BASE_URL = '/api/v1'
export const DEFAULT_TIMEOUT_MS = 30_000
/** Makes the backend accept the session cookie (ADR 0002): a cross-origin
 * page cannot send it without a CORS preflight the backend never grants. */
export const CSRF_HEADER = 'X-Akasha-CSRF'

/**
 * The only place the frontend speaks HTTP to the backend. Every response is
 * validated against a schema; every failure becomes an ApiError.
 */
export class ApiClient {
    /** Also read by the live connection (live.ts): the same origin. */
    readonly baseUrl: string
    private readonly timeoutMs: number
    private readonly onSessionEnded: (() => void) | undefined

    constructor(options: ApiClientOptions = {}) {
        this.baseUrl = options.baseUrl ?? DEFAULT_BASE_URL
        this.timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS
        this.onSessionEnded = options.onSessionEnded
    }

    /**
     * Sends a request and returns the body parsed by `schema`. An empty body
     * (204) parses as `undefined`, so no-content endpoints pass `z.undefined()`.
     */
    async request<S extends z.ZodType>(
        method: HttpMethod,
        path: string,
        schema: S,
        options: RequestOptions = {},
    ): Promise<z.output<S>> {
        return (await this.requestWithBody(method, path, schema, options)).data
    }

    /**
     * Like `request`, plus the response body exactly as the server sent it:
     * for downloads that must keep the backend's bytes (e.g. a canonical
     * report export), still only after the body passed `schema`.
     */
    async requestWithBody<S extends z.ZodType>(
        method: HttpMethod,
        path: string,
        schema: S,
        options: RequestOptions = {},
    ): Promise<{ data: z.output<S>; body: string }> {
        const {
            status,
            requestId,
            value: text,
        } = await this.exchange(method, path, options, (response) =>
            response.text(),
        )
        let data: unknown
        try {
            data = text === '' ? undefined : JSON.parse(text)
        } catch (cause) {
            throw malformed(status, requestId, cause)
        }
        const parsed = schema.safeParse(data)
        if (!parsed.success) throw malformed(status, requestId, parsed.error)
        return { data: parsed.data, body: text }
    }

    /** A binary response (e.g. a project package), its bytes untouched;
     * failures are ApiErrors exactly as for `request`. */
    async download(
        method: HttpMethod,
        path: string,
        options: RequestOptions = {},
    ): Promise<Blob> {
        return (
            await this.exchange(method, path, options, (response) =>
                response.blob(),
            )
        ).value
    }

    /** Sends the request; a successful response is read by `read`, a
     * failed one becomes an ApiError. */
    private async exchange<T>(
        method: HttpMethod,
        path: string,
        options: RequestOptions,
        read: (response: Response) => Promise<T>,
    ): Promise<{ status: number; requestId: string; value: T }> {
        const requestId = crypto.randomUUID()
        // Ids in paths come from route params a crafted link controls, and
        // encodeURIComponent keeps '.': URL parsing would collapse a "." or
        // ".." segment and send the request to another endpoint.
        if (path.split('/').some((s) => s === '.' || s === '..'))
            throw new ApiError({
                kind: 'not_found',
                message: fallbackMessage('not_found'),
                requestId,
            })
        const timeout = AbortSignal.timeout(options.timeoutMs ?? this.timeoutMs)
        const signal = options.signal
            ? AbortSignal.any([options.signal, timeout])
            : timeout

        const headers: Record<string, string> = {
            Accept: 'application/json',
            'X-Request-ID': requestId,
            // The session itself is an HttpOnly cookie the browser attaches
            // (same-origin): page scripts never hold the token.
            [CSRF_HEADER]: '1',
        }
        let body: string | Blob | null = null
        if (options.rawBody) {
            headers['Content-Type'] = options.rawBody.contentType
            body = options.rawBody.data
        } else if (options.body !== undefined) {
            headers['Content-Type'] = 'application/json'
            body = JSON.stringify(options.body)
        }

        let response: Response
        let text: string
        try {
            response = await fetch(this.url(path, options.query), {
                method,
                headers,
                body,
                signal,
            })
            if (response.ok)
                return {
                    status: response.status,
                    requestId:
                        response.headers.get('X-Request-ID') ?? requestId,
                    value: await read(response),
                }
            text = await response.text()
        } catch (cause) {
            const kind = options.signal?.aborted
                ? 'cancelled'
                : timeout.aborted
                  ? 'timeout'
                  : 'network'
            throw new ApiError({
                kind,
                message: fallbackMessage(kind),
                requestId,
                cause,
            })
        }

        const error = httpError(
            response.status,
            text,
            response.headers.get('X-Request-ID') ?? requestId,
        )
        if (
            error.kind === 'authentication' &&
            error.code !== 'INVALID_CREDENTIALS'
        )
            this.onSessionEnded?.()
        throw error
    }

    private url(path: string, query: RequestOptions['query']): string {
        const params = new URLSearchParams()
        for (const [key, value] of Object.entries(query ?? {})) {
            if (value === undefined) continue
            for (const item of typeof value === 'object' ? value : [value])
                params.append(key, String(item))
        }
        const search = params.toString()
        return `${this.baseUrl}${path}${search ? `?${search}` : ''}`
    }
}

function malformed(
    status: number,
    requestId: string,
    cause: unknown,
): ApiError {
    // The cause (e.g. the ZodError) names the offending fields for debugging;
    // the message stays user-facing.
    return new ApiError({
        kind: 'malformed_response',
        status,
        message: fallbackMessage('malformed_response'),
        requestId,
        cause,
    })
}
