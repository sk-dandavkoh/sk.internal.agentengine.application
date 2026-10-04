// A WebSocket the test drives by hand: nothing connects anywhere, and every
// open, frame and close happens exactly when the test says so (no sleeps).
// Installed for every test in setup.ts, so no test reaches a real server.
import { act } from '@testing-library/react'

export class FakeWebSocket {
    static instances: FakeWebSocket[] = []

    readyState = 0 // CONNECTING
    readonly sent: string[] = []
    onopen: ((event: Event) => void) | null = null
    onmessage: ((event: MessageEvent) => void) | null = null
    onclose: ((event: CloseEvent) => void) | null = null
    onerror: ((event: Event) => void) | null = null

    readonly url: string

    constructor(url: string) {
        this.url = url
        FakeWebSocket.instances.push(this)
    }

    send(data: string) {
        if (this.readyState !== 1) throw new Error('WebSocket is not open')
        this.sent.push(data)
    }

    /** Client-side close: the close event follows, like a browser's. */
    close(code = 1000) {
        this.serverClose(code)
    }

    // -- the server's side ----------------------------------------------------

    serverOpen() {
        act(() => {
            this.readyState = 1
            this.onopen?.(new Event('open'))
        })
    }

    serverSend(frame: unknown) {
        const data = typeof frame === 'string' ? frame : JSON.stringify(frame)
        act(() => {
            this.onmessage?.(new MessageEvent('message', { data }))
        })
    }

    serverClose(code: number) {
        if (this.readyState === 3) return
        act(() => {
            this.readyState = 3
            this.onclose?.(new CloseEvent('close', { code }))
        })
    }

    /** The parsed frames the client sent. */
    frames(): unknown[] {
        return this.sent.map((s) => JSON.parse(s) as unknown)
    }
}

export function sockets() {
    return FakeWebSocket.instances
}

export function lastSocket(): FakeWebSocket {
    const socket = FakeWebSocket.instances.at(-1)
    if (!socket) throw new Error('No WebSocket was opened')
    return socket
}
