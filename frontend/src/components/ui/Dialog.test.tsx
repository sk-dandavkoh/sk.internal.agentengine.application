import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { Button } from './Button'
import { ConfirmDialog } from './ConfirmDialog'
import { Dialog } from './Dialog'

function DialogHarness({ onClose = vi.fn() }: { onClose?: () => void }) {
    const [open, setOpen] = useState(false)
    return (
        <>
            <Button
                onClick={() => {
                    setOpen(true)
                }}
            >
                Open
            </Button>
            <Dialog
                open={open}
                onClose={() => {
                    onClose()
                    setOpen(false)
                }}
                title="Rename project"
                description="Shown in the project list."
            >
                <input aria-label="Name" data-autofocus />
            </Dialog>
        </>
    )
}

// Escape in a browser: `cancel`, then (unless prevented) `close`.
function pressNativeEscape(dialog: HTMLElement) {
    const cancel = new Event('cancel', { cancelable: true })
    dialog.dispatchEvent(cancel)
    if (!cancel.defaultPrevented) (dialog as HTMLDialogElement).close()
}

describe('Dialog', () => {
    it('renders nothing until opened, then a named, described modal', async () => {
        const user = userEvent.setup()
        render(<DialogHarness />)
        expect(screen.queryByRole('dialog')).not.toBeInTheDocument()

        await user.click(screen.getByRole('button', { name: 'Open' }))

        const dialog = screen.getByRole('dialog', { name: 'Rename project' })
        expect(dialog).toHaveAccessibleDescription('Shown in the project list.')
        expect(screen.getByRole('textbox', { name: 'Name' })).toHaveFocus()
    })

    it('closes via the close button', async () => {
        const user = userEvent.setup()
        const onClose = vi.fn()
        render(<DialogHarness onClose={onClose} />)
        await user.click(screen.getByRole('button', { name: 'Open' }))

        await user.click(screen.getByRole('button', { name: 'Close' }))

        expect(onClose).toHaveBeenCalledOnce()
        expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    })

    it('reports a browser-initiated close (Escape) to its owner', async () => {
        const user = userEvent.setup()
        const onClose = vi.fn()
        render(<DialogHarness onClose={onClose} />)
        await user.click(screen.getByRole('button', { name: 'Open' }))

        pressNativeEscape(screen.getByRole('dialog'))

        expect(onClose).toHaveBeenCalledOnce()
        expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    })

    it('blocks Escape and hides the close button when not dismissible', () => {
        const onClose = vi.fn()
        render(
            <Dialog
                open
                onClose={onClose}
                title="Saving"
                dismissible={false}
            />,
        )
        const dialog = screen.getByRole('dialog', { name: 'Saving' })

        pressNativeEscape(dialog)

        expect(onClose).not.toHaveBeenCalled()
        expect(dialog).toHaveAttribute('open')
        expect(
            screen.queryByRole('button', { name: 'Close' }),
        ).not.toBeInTheDocument()
    })
})

describe('ConfirmDialog', () => {
    function renderConfirm(
        props: Partial<Parameters<typeof ConfirmDialog>[0]> = {},
    ) {
        const handlers = { onCancel: vi.fn(), onConfirm: vi.fn() }
        render(
            <ConfirmDialog
                open
                title="Delete project?"
                confirmLabel="Delete project"
                {...handlers}
                {...props}
            >
                The project “Invoices” will be deleted.
            </ConfirmDialog>,
        )
        return handlers
    }

    it('names the consequence and focuses the safe choice', () => {
        renderConfirm({ irreversible: true })

        expect(
            screen.getByRole('dialog', { name: 'Delete project?' }),
        ).toHaveAccessibleDescription(
            'The project “Invoices” will be deleted. This cannot be undone.',
        )
        expect(screen.getByRole('button', { name: 'Cancel' })).toHaveFocus()
    })

    it('confirms and cancels', async () => {
        const user = userEvent.setup()
        const { onCancel, onConfirm } = renderConfirm()

        await user.click(screen.getByRole('button', { name: 'Delete project' }))
        await user.click(screen.getByRole('button', { name: 'Cancel' }))

        expect(onConfirm).toHaveBeenCalledOnce()
        expect(onCancel).toHaveBeenCalledOnce()
    })

    it('cannot be confirmed twice or dismissed while pending', async () => {
        const user = userEvent.setup()
        const { onCancel, onConfirm } = renderConfirm({ pending: true })
        const confirm = screen.getByRole('button', { name: 'Delete project' })

        await user.click(confirm)
        fireEvent(
            screen.getByRole('dialog'),
            new Event('cancel', { cancelable: true }),
        )

        expect(confirm).toHaveAttribute('aria-disabled', 'true')
        expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled()
        expect(onConfirm).not.toHaveBeenCalled()
        expect(onCancel).not.toHaveBeenCalled()
        expect(screen.getByRole('dialog')).toHaveAttribute('open')
    })

    it('shows a failure as an alert inside the dialog', () => {
        renderConfirm({ error: 'The project could not be deleted.' })

        expect(screen.getByRole('alert')).toHaveTextContent(
            'Error: The project could not be deleted.',
        )
    })
})
