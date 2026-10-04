import { z } from 'zod'
import type { ApiClient } from './client'
import {
    executionEventSchema,
    executionStatusSchema,
    type ExecutionEvent,
    type ExecutionStatus,
} from './executions'

// Contract of backend api/live.py: WS /executions/{id}/live. Events are
// notifications, never state: every handler below ends in a refetch of the
// execution resource, which stays authoritative (AGENTS §82, §41).

const serverFrameSchema = z.discriminatedUnion('type', [
    z.object({
        type: z.literal('subscribed'),
        execution_id: z.string(),
        after: z.number().int(),
    }),
    z.object({
        type: z.literal('events'),
        items: z.array(executionEventSchema),
    }),
    z.object({ type: z.literal('end'), status: executionStatusSchema }),
])

/** Close codes after which reconnecting cannot help. */
export const LIVE_CLOSE = {
    protocol: 4400,
    unauthenticated: 4401,
    notFound: 4404,
} as const

export type LiveState =
    | 'connecting'
    | 'live'
    /** Dropped; the next attempt waits for its backoff. */
    | 'reconnecting'
    /** The execution finished and every event was delivered. */
    | 'ended'
    | 'unauthenticated'
    /** Unknown execution or rejected subscription: retrying cannot help. */
    | 'unavailable'

export interface LiveHandlers {
    onState: (state: LiveState) => void
    /** (Re)subscribed: anything may have happened while disconnected, so
     * refetch the authoritative state. */
    onSubscribed: () => void
    /** Only events not seen before, ascending by position. */
    onEvents: (events: ExecutionEvent[]) => void
    onEnd: (status: ExecutionStatus) => void
}

export const RECONNECT_BASE_MS = 1_000
export const RECONNECT_MAX_MS = 30_000

// ponytail: no jitter; add it if many clients reconnect to one server at once.
export const reconnectDelay = (attempt: number) =>
    Math.min(RECONNECT_MAX_MS, RECONNECT_BASE_MS * 2 ** attempt)

function parseFrame(data: unknown) {
    if (typeof data !== 'string') return undefined
    try {
        const parsed = serverFrameSchema.safeParse(JSON.parse(data))
        return parsed.success ? parsed.data : undefined
    } catch {
        return undefined // not JSON: same as any malformed frame
    }
}

/**
 * Follows one execution's events until it ends, reconnecting with backoff
 * and resuming after the last seen position. Returns the unsubscribe.
 */
export function subscribeExecution(
    client: ApiClient,
    executionId: string,
    handlers: LiveHandlers,
): () => void {
    // Same origin as every request, ws(s) for http(s).
    const url = new URL(
        `${client.baseUrl}/executions/${encodeURIComponent(executionId)}/live`,
        window.location.href,
    )
    url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
    let after = 0 // highest position delivered
    let attempt = 0
    let socket: WebSocket | undefined
    let timer: number | undefined
    let stopped = false

    function stop(state?: LiveState) {
        stopped = true
        window.clearTimeout(timer)
        socket?.close()
        if (state) handlers.onState(state)
    }

    function connect() {
        const ws = new WebSocket(url.href)
        socket = ws
        ws.onopen = () => {
            // In the frame, never the URL: URLs end up in logs.
            const token = client.getToken?.() ?? ''
            ws.send(JSON.stringify({ type: 'subscribe', token, after }))
        }
        ws.onmessage = (message: MessageEvent) => {
            if (stopped) return
            const frame = parseFrame(message.data)
            if (!frame) {
                // Unreadable: drop the connection; the resubscribe
                // reconciles whatever the frame was about.
                ws.close()
                return
            }
            if (frame.type === 'subscribed') {
                attempt = 0
                handlers.onState('live')
                handlers.onSubscribed()
            } else if (frame.type === 'events') {
                // Duplicates and stale (older) events are dropped by position.
                const fresh = frame.items.filter((e) => {
                    if (e.position <= after) return false
                    after = e.position
                    return true
                })
                if (fresh.length) handlers.onEvents(fresh)
            } else {
                stop('ended')
                handlers.onEnd(frame.status)
            }
        }
        ws.onclose = (event: CloseEvent) => {
            if (stopped || socket !== ws) return
            socket = undefined
            if (event.code === LIVE_CLOSE.unauthenticated) {
                stop('unauthenticated')
            } else if (
                event.code === LIVE_CLOSE.notFound ||
                event.code === LIVE_CLOSE.protocol
            ) {
                stop('unavailable')
            } else {
                handlers.onState('reconnecting')
                timer = window.setTimeout(connect, reconnectDelay(attempt++))
            }
        }
    }

    handlers.onState('connecting')
    connect()
    return () => {
        stop()
    }
}
