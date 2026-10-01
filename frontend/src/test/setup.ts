import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

afterEach(() => {
    cleanup()
})

// jsdom has no modal dialog support. This stand-in only toggles `open` and
// fires `close`; top layer, focus containment and Escape are browser
// behavior and are covered by the Playwright tests (tests/e2e/ui.spec.ts).
HTMLDialogElement.prototype.showModal = function (this: HTMLDialogElement) {
    this.open = true
}
HTMLDialogElement.prototype.close = function (this: HTMLDialogElement) {
    if (!this.open) return
    this.open = false
    this.dispatchEvent(new Event('close'))
}
