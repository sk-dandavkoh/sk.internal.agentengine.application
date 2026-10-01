import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { DropdownMenu } from './DropdownMenu'

function renderMenu() {
    const handlers = {
        duplicate: vi.fn(),
        archive: vi.fn(),
        remove: vi.fn(),
    }
    render(
        <>
            <DropdownMenu
                label="Actions"
                items={[
                    { label: 'Duplicate', onSelect: handlers.duplicate },
                    {
                        label: 'Archive',
                        onSelect: handlers.archive,
                        disabled: true,
                    },
                    {
                        label: 'Delete',
                        onSelect: handlers.remove,
                        tone: 'danger',
                    },
                ]}
            />
            <button type="button">Outside</button>
        </>,
    )
    const trigger = screen.getByRole('button', { name: 'Actions' })
    return { handlers, trigger }
}

const focusedItem = () => document.activeElement?.textContent

describe('DropdownMenu', () => {
    it('opens on click with focus on the first item', async () => {
        const user = userEvent.setup()
        const { trigger } = renderMenu()
        expect(trigger).toHaveAttribute('aria-haspopup', 'menu')
        expect(trigger).toHaveAttribute('aria-expanded', 'false')

        await user.click(trigger)

        expect(trigger).toHaveAttribute('aria-expanded', 'true')
        expect(
            screen.getByRole('menu', { name: 'Actions' }),
        ).toBeInTheDocument()
        expect(screen.getAllByRole('menuitem')).toHaveLength(3)
        expect(focusedItem()).toBe('Duplicate')
    })

    it('moves with arrows, wraps, skips disabled items, and supports Home/End', async () => {
        const user = userEvent.setup()
        const { trigger } = renderMenu()
        trigger.focus()

        await user.keyboard('{ArrowDown}')
        expect(focusedItem()).toBe('Duplicate')
        await user.keyboard('{ArrowDown}')
        expect(focusedItem()).toBe('Delete')
        await user.keyboard('{ArrowDown}')
        expect(focusedItem()).toBe('Duplicate')
        await user.keyboard('{ArrowUp}')
        expect(focusedItem()).toBe('Delete')
        await user.keyboard('{Home}')
        expect(focusedItem()).toBe('Duplicate')
        await user.keyboard('{End}')
        expect(focusedItem()).toBe('Delete')
    })

    it('opens on ArrowUp with focus on the last item', async () => {
        const user = userEvent.setup()
        const { trigger } = renderMenu()
        trigger.focus()

        await user.keyboard('{ArrowUp}')

        expect(focusedItem()).toBe('Delete')
    })

    it('selects with Enter, closes, and returns focus to the trigger', async () => {
        const user = userEvent.setup()
        const { handlers, trigger } = renderMenu()
        trigger.focus()

        await user.keyboard('{ArrowDown}{Enter}')

        expect(handlers.duplicate).toHaveBeenCalledOnce()
        expect(screen.queryByRole('menu')).not.toBeInTheDocument()
        expect(trigger).toHaveFocus()
    })

    it('closes on Escape and returns focus to the trigger', async () => {
        const user = userEvent.setup()
        const { handlers, trigger } = renderMenu()
        await user.click(trigger)

        await user.keyboard('{Escape}')

        expect(screen.queryByRole('menu')).not.toBeInTheDocument()
        expect(trigger).toHaveFocus()
        expect(handlers.duplicate).not.toHaveBeenCalled()
    })

    it('does not run disabled items', async () => {
        const user = userEvent.setup()
        const { handlers, trigger } = renderMenu()
        await user.click(trigger)

        await user.click(screen.getByRole('menuitem', { name: 'Archive' }))

        expect(screen.getByRole('menuitem', { name: 'Archive' })).toBeDisabled()
        expect(handlers.archive).not.toHaveBeenCalled()
    })

    it('closes on outside click and on Tab', async () => {
        const user = userEvent.setup()
        const { trigger } = renderMenu()

        await user.click(trigger)
        await user.click(screen.getByRole('button', { name: 'Outside' }))
        expect(screen.queryByRole('menu')).not.toBeInTheDocument()

        await user.click(trigger)
        await user.tab()
        expect(screen.queryByRole('menu')).not.toBeInTheDocument()
    })
})
