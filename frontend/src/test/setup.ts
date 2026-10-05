import '@testing-library/jest-dom/vitest'
import { cleanup, configure } from '@testing-library/react'
import { afterEach, beforeEach, vi } from 'vitest'
import { FakeWebSocket } from './websocket'

// The app formats in the user's locale; tests assert en-US output, so the
// machine's locale must not leak in (AGENTS §16). Node takes the default
// from the OS (on Windows LANG is ignored), hence pinned here: a missing
// locale argument means en-US. Before any module creates a formatter.
const TEST_LOCALE = 'en-US'
const { NumberFormat, DateTimeFormat } = Intl
type Locales = Intl.LocalesArgument
// Functions, not arrows: the app calls them with `new`.
Intl.NumberFormat = function (
    locales?: Locales,
    options?: Intl.NumberFormatOptions,
) {
    return new NumberFormat(locales ?? TEST_LOCALE, options)
} as unknown as typeof Intl.NumberFormat
Intl.DateTimeFormat = function (
    locales?: Locales,
    options?: Intl.DateTimeFormatOptions,
) {
    return new DateTimeFormat(locales ?? TEST_LOCALE, options)
} as unknown as typeof Intl.DateTimeFormat
// Specified as Intl.NumberFormat. Date#toLocale* is not used by the app.
Number.prototype.toLocaleString = function (
    this: number,
    locales?: Locales,
    options?: Intl.NumberFormatOptions,
) {
    return new NumberFormat(locales ?? TEST_LOCALE, options).format(this)
}

// A screen test's first findBy* also waits for its lazy route module, and a
// cold import of a feature chunk can exceed the 1 s default on a busy
// machine (a VersionsPage test failed that way). findBy* resolves as soon
// as the element appears, so the budget costs time only when a test fails.
configure({ asyncUtilTimeout: 5000 })

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
