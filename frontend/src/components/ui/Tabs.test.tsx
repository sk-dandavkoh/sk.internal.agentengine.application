import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { Tabs, type TabItem } from './Tabs'

const items: TabItem[] = [
    { id: 'overview', label: 'Overview', content: 'Overview content' },
    { id: 'runs', label: 'Runs', content: 'Runs content' },
    { id: 'settings', label: 'Settings', content: 'Settings content' },
]

describe('Tabs', () => {
    it('exposes tablist, selected tab and its labelled panel', () => {
        render(<Tabs label="Workflow sections" items={items} />)

        expect(
            screen.getByRole('tablist', { name: 'Workflow sections' }),
        ).toBeInTheDocument()
        expect(screen.getByRole('tab', { name: 'Overview' })).toHaveAttribute(
            'aria-selected',
            'true',
        )
        expect(
            screen.getByRole('tabpanel', { name: 'Overview' }),
        ).toHaveTextContent('Overview content')
        expect(screen.getAllByRole('tabpanel')).toHaveLength(1)
    })

    it('is a single tab stop with arrow-key selection that wraps', async () => {
        const user = userEvent.setup()
        render(<Tabs label="Sections" items={items} />)

        await user.tab()
        expect(screen.getByRole('tab', { name: 'Overview' })).toHaveFocus()
        await user.keyboard('{ArrowLeft}')
        expect(screen.getByRole('tab', { name: 'Settings' })).toHaveFocus()
        expect(screen.getByRole('tabpanel')).toHaveTextContent(
            'Settings content',
        )
        await user.keyboard('{ArrowRight}')
        expect(screen.getByRole('tab', { name: 'Overview' })).toHaveFocus()
        await user.keyboard('{End}')
        expect(screen.getByRole('tab', { name: 'Settings' })).toHaveFocus()
        await user.keyboard('{Home}')
        expect(screen.getByRole('tab', { name: 'Overview' })).toHaveFocus()

        // Tab leaves the tab list and lands on the panel.
        await user.tab()
        expect(screen.getByRole('tabpanel')).toHaveFocus()
    })

    it('selects on click', async () => {
        const user = userEvent.setup()
        render(<Tabs label="Sections" items={items} defaultValue="runs" />)
        expect(screen.getByRole('tabpanel')).toHaveTextContent('Runs content')

        await user.click(screen.getByRole('tab', { name: 'Settings' }))

        expect(screen.getByRole('tabpanel')).toHaveTextContent(
            'Settings content',
        )
    })

    it('follows a controlled value and reports changes', async () => {
        const user = userEvent.setup()
        const onValueChange = vi.fn()
        render(
            <Tabs
                label="Sections"
                items={items}
                value="runs"
                onValueChange={onValueChange}
            />,
        )

        await user.click(screen.getByRole('tab', { name: 'Settings' }))

        expect(onValueChange).toHaveBeenCalledWith('settings')
        // The owner did not change `value`, so the selection stays.
        expect(screen.getByRole('tabpanel')).toHaveTextContent('Runs content')
    })
})
