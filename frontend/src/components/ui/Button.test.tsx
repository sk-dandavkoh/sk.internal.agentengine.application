import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { Button } from './Button'

describe('Button', () => {
    it('is a real button activated by click, Enter and Space', async () => {
        const user = userEvent.setup()
        const onClick = vi.fn()
        render(<Button onClick={onClick}>Save</Button>)
        const button = screen.getByRole('button', { name: 'Save' })

        expect(button).toHaveAttribute('type', 'button')
        await user.click(button)
        await user.keyboard('{Enter}')
        await user.keyboard(' ')

        expect(onClick).toHaveBeenCalledTimes(3)
    })

    it('ignores activation when disabled', async () => {
        const user = userEvent.setup()
        const onClick = vi.fn()
        render(
            <Button disabled onClick={onClick}>
                Save
            </Button>,
        )

        await user.click(screen.getByRole('button', { name: 'Save' }))

        expect(screen.getByRole('button')).toBeDisabled()
        expect(onClick).not.toHaveBeenCalled()
    })

    it('stays focusable but blocks clicks and form submission while loading', async () => {
        const user = userEvent.setup()
        const onClick = vi.fn()
        const onSubmit = vi.fn((event: SubmitEvent) => {
            event.preventDefault()
        })
        render(
            <form
                ref={(form) => {
                    form?.addEventListener('submit', onSubmit)
                }}
            >
                <Button type="submit" loading onClick={onClick}>
                    Save
                </Button>
            </form>,
        )
        const button = screen.getByRole('button', { name: 'Save' })

        await user.tab()
        expect(button).toHaveFocus()
        expect(button).toHaveAttribute('aria-disabled', 'true')
        expect(button).toHaveAttribute('aria-busy', 'true')

        await user.click(button)
        await user.keyboard('{Enter}')

        expect(onClick).not.toHaveBeenCalled()
        expect(onSubmit).not.toHaveBeenCalled()
    })
})
