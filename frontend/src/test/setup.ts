import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, beforeEach, vi } from 'vitest'
import { FakeWebSocket } from './websocket'

beforeEach(() => {
    // Live updates never reach a real server in unit/component tests.
    FakeWebSocket.instances = []
    vi.stubGlobal('WebSocket', FakeWebSocket)
})

afterEach(() => {
    cleanup()
})

// jsdom has no modal dialog support. This stand-in only toggles `open` and
// fires `close`; top layer, focus containment and Escape are browser
// behavior and are covered by the Playwright tests (tests/e2e/ui.spec.ts).
// Absent in `@vitest-environment node` files (the API client tests).
if (typeof HTMLDialogElement !== 'undefined') {
    HTMLDialogElement.prototype.showModal = function (this: HTMLDialogElement) {
        this.open = true
    }
    HTMLDialogElement.prototype.close = function (this: HTMLDialogElement) {
        if (!this.open) return
        this.open = false
        this.dispatchEvent(new Event('close'))
    }
}

// jsdom has no layout. React Flow needs these to mount; sizes are fixed
// stand-ins, so geometry (drag, edge paths, minimap) is covered by
// Playwright (tests/e2e/editor.spec.ts), not here.
if (typeof window !== 'undefined') {
    // Reports each observed element once, as a browser does on observe,
    // so React Flow measures (and shows) its nodes.
    window.ResizeObserver = class {
        readonly callback: ResizeObserverCallback
        constructor(callback: ResizeObserverCallback) {
            this.callback = callback
        }
        observe = (target: Element) => {
            setTimeout(() => {
                const contentRect = { width: 200, height: 200 }
                this.callback(
                    [{ target, contentRect } as ResizeObserverEntry],
                    this,
                )
            })
        }
        unobserve = () => undefined
        disconnect = () => undefined
    }
    // React Flow reads the viewport transform through this constructor.
    window.DOMMatrixReadOnly = class {
        m22: number
        constructor(transform?: string) {
            const scale = /scale\(([\d.]+)\)/.exec(transform ?? '')?.[1]
            this.m22 = scale ? Number(scale) : 1
        }
    } as unknown as typeof DOMMatrixReadOnly
    // Edge labels measure their text.
    Object.defineProperty(SVGElement.prototype, 'getBBox', {
        configurable: true,
        value: () => new DOMRect(0, 0, 40, 12),
    })
    for (const property of ['offsetWidth', 'offsetHeight'] as const)
        Object.defineProperty(HTMLElement.prototype, property, {
            configurable: true,
            get() {
                return 200
            },
        })
}
