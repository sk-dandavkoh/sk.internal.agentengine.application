import type { z } from 'zod'
import { ApiError, fallbackMessage, httpError } from './errors'

export type HttpMethod = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'

export interface ApiClientOptions {
    /** Prefix for every path. Same-origin by default (see README). */
    baseUrl?: string
    /** Per-request default; a request may override it. */
    timeoutMs?: number
    /** Session token source. The auth feature owns where the token lives. */
    getToken?: () => string | undefined
}

export interface RequestOptions {
    /** JSON-serialized request body. */
    body?: unknown
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

/**
 * The only place the frontend speaks HTTP to the backend. Every response is
 * validated against a schema; every failure becomes an ApiError.
 */
export class ApiClient {
    /** Also read by the live connection (live.ts): the same origin. */
    readonly baseUrl: string
    private readonly timeoutMs: number
    /** Also read by the live connection (live.ts): one token source. */
    readonly getToken: (() => string | undefined) | undefined

    constructor(options: ApiClientOptions = {}) {
        this.baseUrl = options.baseUrl ?? DEFAULT_BASE_URL
        this.timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS
        this.getToken = options.getToken
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
        const requestId = crypto.randomUUID()
        const timeout = AbortSignal.timeout(options.timeoutMs ?? this.timeoutMs)
        const signal = options.signal
            ? AbortSignal.any([options.signal, timeout])
            : timeout

        const headers: Record<string, string> = {
            Accept: 'application/json',
            'X-Request-ID': requestId,
        }
        const token = this.getToken?.()
        if (token) headers['Authorization'] = `Bearer ${token}`
        let body: string | null = null
        if (options.body !== undefined) {
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

        const responseId = response.headers.get('X-Request-ID') ?? requestId
        if (!response.ok) throw httpError(response.status, text, responseId)

        let data: unknown
        try {
            data = text === '' ? undefined : JSON.parse(text)
        } catch (cause) {
            throw malformed(response.status, responseId, cause)
        }
        const parsed = schema.safeParse(data)
        if (!parsed.success)
            throw malformed(response.status, responseId, parsed.error)
        return { data: parsed.data, body: text }
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
