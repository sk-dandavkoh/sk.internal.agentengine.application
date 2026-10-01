import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { Checkbox, Switch } from './Checkbox'
import { Input } from './Input'
import { Select } from './Select'
import { Textarea } from './Textarea'

describe('Input', () => {
    it('labels the control and links help text and error', () => {
        render(
            <Input
                label="Name"
                description="Shown in lists."
                error="Name is required."
                required
            />,
        )
        const input = screen.getByRole('textbox', { name: /Name/ })

        expect(input).toBeRequired()
        expect(input).toBeInvalid()
        expect(input).toHaveAccessibleDescription(
            'Shown in lists. Name is required.',
        )
    })

    it('is valid and undescribed without messages, and accepts typing', async () => {
        const user = userEvent.setup()
        render(<Input label="Name" />)
        const input = screen.getByRole('textbox', { name: 'Name' })

        await user.type(input, 'Akasha')

        expect(input).toHaveValue('Akasha')
        expect(input).not.toHaveAttribute('aria-invalid')
        expect(input).not.toHaveAttribute('aria-describedby')
    })

    it('cannot be edited when disabled', async () => {
        const user = userEvent.setup()
        render(<Input label="Name" disabled />)

        await user.type(screen.getByRole('textbox', { name: 'Name' }), 'x')

        expect(screen.getByRole('textbox')).toHaveValue('')
    })
})

describe('Textarea', () => {
    it('shares the field wiring', () => {
        render(<Textarea label="Notes" error="Too long." />)

        expect(screen.getByRole('textbox', { name: 'Notes' })).toBeInvalid()
        expect(screen.getByRole('textbox')).toHaveAccessibleDescription(
            'Too long.',
        )
    })
})

describe('Select', () => {
    it('is a labelled native select operable by the user', async () => {
        const user = userEvent.setup()
        render(
            <Select label="Provider" defaultValue="a">
                <option value="a">Provider A</option>
                <option value="b">Provider B</option>
            </Select>,
        )
        const select = screen.getByRole('combobox', { name: 'Provider' })

        await user.selectOptions(select, 'Provider B')

        expect(select).toHaveValue('b')
    })
})

describe('Checkbox and Switch', () => {
    it('toggles via label click and Space, with its description', async () => {
        const user = userEvent.setup()
        render(<Checkbox label="Notify" description="On failure only." />)
        const checkbox = screen.getByRole('checkbox', { name: 'Notify' })

        expect(checkbox).toHaveAccessibleDescription('On failure only.')
        await user.click(screen.getByText('Notify'))
        expect(checkbox).toBeChecked()
        await user.keyboard(' ')
        expect(checkbox).not.toBeChecked()
    })

    it('exposes the switch role', async () => {
        const user = userEvent.setup()
        render(<Switch label="Enabled" />)
        const toggle = screen.getByRole('switch', { name: 'Enabled' })

        await user.click(toggle)

        expect(toggle).toBeChecked()
    })

    it('does not toggle when disabled', async () => {
        const user = userEvent.setup()
        render(<Switch label="Enabled" disabled />)

        await user.click(screen.getByText('Enabled'))

        expect(screen.getByRole('switch')).not.toBeChecked()
    })
})
