import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { lastSocket, sockets } from '../test/websocket'
import { ApiClient } from './client'
import type { ExecutionEvent } from './executions'
import {
    reconnectDelay,
    RECONNECT_MAX_MS,
    subscribeExecution,
    type LiveState,
} from './live'

const ID = 'exec_1'

function event(position: number, event_type = 'node.started') {
    return {
        event_id: `evt_${String(position)}`,
        event_type,
        schema_version: 1,
        position,
        occurred_at: '2026-10-04T08:00:00Z',
        project_id: 'proj_1',
        project_version: 1,
        execution_id: ID,
        node_id: 'a',
        correlation_id: null,
        payload: { attempt: 1 },
    }
}

/** `null`: no session token at all. */
function follow(token: string | null = 'session-token') {
    const states: LiveState[] = []
    const delivered: number[] = []
    const ends: string[] = []
    const handlers = {
        onState: (s: LiveState) => states.push(s),
        onSubscribed: vi.fn(),
        onEvents: (events: ExecutionEvent[]) =>
            delivered.push(...events.map((e) => e.position)),
        onEnd: (status: string) => ends.push(status),
    }
    const client = new ApiClient({ getToken: () => token ?? undefined })
    const unsubscribe = subscribeExecution(client, ID, handlers)
    return { states, delivered, ends, handlers, unsubscribe }
}

/** Open the latest socket and confirm its subscription, as the server does. */
function accept(after = 0) {
    const ws = lastSocket()
    ws.serverOpen()
    ws.serverSend({ type: 'subscribed', execution_id: ID, after })
    return ws
}

beforeEach(() => {
    vi.useFakeTimers()
})
afterEach(() => {
    vi.useRealTimers()
})

describe('subscribeExecution', () => {
    it('subscribes on the same origin with the token in the frame, not the URL', () => {
        const live = follow()
        const ws = lastSocket()
        expect(ws.url).toBe(`ws://localhost:3000/api/v1/executions/${ID}/live`)
        expect(live.states).toEqual(['connecting'])

        ws.serverOpen()
        expect(ws.frames()).toEqual([
            { type: 'subscribe', token: 'session-token', after: 0 },
        ])
        ws.serverSend({ type: 'subscribed', execution_id: ID, after: 0 })
        expect(live.states).toEqual(['connecting', 'live'])
        expect(live.handlers.onSubscribed).toHaveBeenCalledOnce()
    })

    it('delivers each event once, in order, dropping duplicates and stale ones', () => {
        const live = follow()
        const ws = accept()
        ws.serverSend({ type: 'events', items: [event(3), event(5)] })
        // A duplicate (5), a stale one (4) and a new one (7).
        ws.serverSend({ type: 'events', items: [event(5), event(4), event(7)] })
        ws.serverSend({ type: 'events', items: [event(7)] })
        expect(live.delivered).toEqual([3, 5, 7])
    })

    it('stops at the end of the execution without reconnecting', () => {
        const live = follow()
        const ws = accept()
        ws.serverSend({ type: 'end', status: 'completed' })
        ws.serverClose(1000)
        vi.runAllTimers()

        expect(live.ends).toEqual(['completed'])
        expect(live.states.at(-1)).toBe('ended')
        expect(sockets()).toHaveLength(1)
    })

    it('reconnects with backoff, resumes after the last seen event and reconciles', () => {
        const live = follow()
        const first = accept()
        first.serverSend({ type: 'events', items: [event(3), event(4)] })

        first.serverClose(1006) // dropped, e.g. network loss or server restart
        expect(live.states.at(-1)).toBe('reconnecting')
        vi.advanceTimersByTime(reconnectDelay(0) - 1)
        expect(sockets()).toHaveLength(1)
        vi.advanceTimersByTime(1)
        expect(sockets()).toHaveLength(2)

        const second = lastSocket()
        second.serverOpen()
        // Missed events come from the server's log, after position 4.
        expect(second.frames()).toEqual([
            { type: 'subscribe', token: 'session-token', after: 4 },
        ])
        second.serverSend({ type: 'subscribed', execution_id: ID, after: 4 })
        expect(live.states.at(-1)).toBe('live')
        // Every (re)subscribe refetches the authoritative state.
        expect(live.handlers.onSubscribed).toHaveBeenCalledTimes(2)
        second.serverSend({ type: 'events', items: [event(4), event(6)] })
        expect(live.delivered).toEqual([3, 4, 6])
    })

    it('backs off exponentially while the server stays away, then resets', () => {
        expect([0, 1, 2, 3].map(reconnectDelay)).toEqual([
            1000, 2000, 4000, 8000,
        ])
        expect(reconnectDelay(20)).toBe(RECONNECT_MAX_MS)

        follow()
        lastSocket().serverClose(1006)
        vi.advanceTimersByTime(1000)
        lastSocket().serverClose(1006) // never opened: still unreachable
        vi.advanceTimersByTime(1999)
        expect(sockets()).toHaveLength(2)
        vi.advanceTimersByTime(1)
        expect(sockets()).toHaveLength(3)

        accept() // connected again: the next drop starts from the base delay
        lastSocket().serverClose(1012)
        vi.advanceTimersByTime(1000)
        expect(sockets()).toHaveLength(4)
    })

    it('does not retry a rejected session or an unknown execution', () => {
        const unauthenticated = follow(null)
        lastSocket().serverOpen()
        expect(lastSocket().frames()).toEqual([
            { type: 'subscribe', token: '', after: 0 },
        ])
        lastSocket().serverClose(4401)
        const notFound = follow()
        lastSocket().serverClose(4404)
        vi.runAllTimers()

        expect(unauthenticated.states.at(-1)).toBe('unauthenticated')
        expect(notFound.states.at(-1)).toBe('unavailable')
        expect(sockets()).toHaveLength(2)
    })

    it('drops a connection that sends a malformed frame and resubscribes', () => {
        const live = follow()
        const ws = accept()
        ws.serverSend('not json')
        expect(ws.readyState).toBe(3)
        ws.serverSend({ type: 'events', items: [{ position: 'x' }] })
        expect(live.delivered).toEqual([])

        vi.advanceTimersByTime(reconnectDelay(0))
        accept()
        expect(live.handlers.onSubscribed).toHaveBeenCalledTimes(2)
    })

    it('stays closed after unsubscribing', () => {
        const live = follow()
        const ws = accept()
        live.unsubscribe()
        expect(ws.readyState).toBe(3)
        ws.serverSend({ type: 'events', items: [event(1)] })
        vi.runAllTimers()
        expect(live.delivered).toEqual([])
        expect(sockets()).toHaveLength(1)
    })
})
