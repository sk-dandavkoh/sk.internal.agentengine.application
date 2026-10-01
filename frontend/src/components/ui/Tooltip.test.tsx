import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { Button } from './Button'
import { Tooltip } from './Tooltip'

describe('Tooltip', () => {
    it('describes its trigger even while hidden', () => {
        render(
            <Tooltip content="Runs the published version">
                <Button>Run</Button>
            </Tooltip>,
        )

        expect(
            screen.getByRole('button', { name: 'Run' }),
        ).toHaveAccessibleDescription('Runs the published version')
        expect(screen.queryByRole('tooltip')).not.toBeInTheDocument()
    })

    it('shows on keyboard focus and hides on Escape and blur', async () => {
        const user = userEvent.setup()
        render(
            <Tooltip content="Runs the published version">
                <Button>Run</Button>
            </Tooltip>,
        )

        await user.tab()
        expect(screen.getByRole('tooltip')).toBeVisible()
        await user.keyboard('{Escape}')
        expect(screen.queryByRole('tooltip')).not.toBeInTheDocument()

        await user.tab({ shift: true })
        await user.tab()
        expect(screen.getByRole('tooltip')).toBeVisible()
        await user.tab()
        expect(screen.queryByRole('tooltip')).not.toBeInTheDocument()
    })

    it('shows on hover and hides when the pointer leaves', async () => {
        const user = userEvent.setup()
        render(
            <Tooltip content="Runs the published version">
                <Button>Run</Button>
            </Tooltip>,
        )

        await user.hover(screen.getByRole('button', { name: 'Run' }))
        expect(screen.getByRole('tooltip')).toHaveTextContent(
            'Runs the published version',
        )
        await user.unhover(screen.getByRole('button', { name: 'Run' }))
        expect(screen.queryByRole('tooltip')).not.toBeInTheDocument()
    })
})
