import {
    act,
    fireEvent,
    renderHook,
    screen,
    within,
} from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useToast, type ToastApi } from './toastContext'
import { ToastProvider } from './ToastProvider'

let api: ToastApi

function renderToasts() {
    const { result } = renderHook(useToast, { wrapper: ToastProvider })
    api = result.current
    const region = screen.getByRole('region', { name: 'Notifications' })
    return region
}

function notify(options: Parameters<ToastApi['notify']>[0]) {
    // act() returns a thenable, not the callback's value.
    let id = 0
    act(() => {
        id = api.notify(options)
    })
    return id
}

describe('ToastProvider', () => {
    // fireEvent, not user-event: RTL's async act wrapper waits on a real
    // setTimeout that fake timers would freeze.
    beforeEach(() => {
        vi.useFakeTimers()
    })
    afterEach(() => {
        vi.useRealTimers()
    })

    it('announces toasts in a persistent polite region', () => {
        const region = renderToasts()
        expect(region).toHaveAttribute('aria-live', 'polite')

        notify({ tone: 'success', title: 'Project saved' })

        expect(within(region).getByRole('status')).toHaveTextContent(
            'Success: Project saved',
        )
    })

    it('auto-dismisses success after 5 s', () => {
        const region = renderToasts()
        notify({ tone: 'success', title: 'Project saved' })

        act(() => {
            vi.advanceTimersByTime(4999)
        })
        expect(within(region).getByText('Project saved')).toBeInTheDocument()
        act(() => {
            vi.advanceTimersByTime(1)
        })
        expect(
            within(region).queryByText('Project saved'),
        ).not.toBeInTheDocument()
    })

    it('keeps errors as alerts until dismissed by the user', () => {
        const region = renderToasts()
        notify({
            tone: 'danger',
            title: 'Publish failed',
            description: 'The graph has validation errors.',
        })

        act(() => {
            vi.advanceTimersByTime(60_000)
        })
        expect(within(region).getByRole('alert')).toHaveTextContent(
            'Error: Publish failedThe graph has validation errors.',
        )

        fireEvent.click(
            within(region).getByRole('button', {
                name: 'Dismiss notification',
            }),
        )
        expect(within(region).queryByRole('alert')).not.toBeInTheDocument()
    })

    it('pauses the timeout while hovered', () => {
        const region = renderToasts()
        notify({ title: 'Copied' })
        const toast = within(region).getByRole('status')

        fireEvent.mouseEnter(toast)
        act(() => {
            vi.advanceTimersByTime(10_000)
        })
        expect(toast).toBeInTheDocument()

        fireEvent.mouseLeave(toast)
        act(() => {
            vi.advanceTimersByTime(5000)
        })
        expect(toast).not.toBeInTheDocument()
    })

    it('caps the number of visible toasts, dropping the oldest', () => {
        const region = renderToasts()
        for (let i = 1; i <= 6; i++)
            notify({ tone: 'warning', title: `Warning ${String(i)}` })

        const shown = within(region)
            .getAllByRole('status')
            .map((toast) => toast.textContent)
        expect(shown).toEqual([
            'Warning: Warning 3',
            'Warning: Warning 4',
            'Warning: Warning 5',
            'Warning: Warning 6',
        ])
    })

    it('dismisses programmatically by id', () => {
        const region = renderToasts()
        const id = notify({ tone: 'warning', title: 'Unsaved changes' })

        act(() => {
            api.dismiss(id)
        })

        expect(within(region).queryByRole('status')).not.toBeInTheDocument()
    })
})

describe('useToast', () => {
    it('fails loudly outside the provider', () => {
        vi.spyOn(console, 'error').mockImplementation(() => undefined)
        expect(() => renderHook(() => useToast())).toThrow(
            'useToast must be used inside <ToastProvider>',
        )
    })
})
