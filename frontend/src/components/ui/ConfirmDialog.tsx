import type { ReactNode } from 'react'
import { Alert } from './Alert'
import { Button } from './Button'
import { Dialog } from './Dialog'

export interface ConfirmDialogProps {
    open: boolean
    onCancel: () => void
    onConfirm: () => void
    title: string
    /** What happens and to which resource. */
    children: ReactNode
    confirmLabel: string
    cancelLabel?: string
    tone?: 'danger' | 'primary'
    /** Adds an explicit "cannot be undone" notice. */
    irreversible?: boolean
    /** The confirmed action is in flight: confirm shows a spinner and the
     *  dialog cannot be dismissed. The caller closes it on success. */
    pending?: boolean
    /** User-facing failure message for the last attempt. */
    error?: string | undefined
}

export function ConfirmDialog({
    open,
    onCancel,
    onConfirm,
    title,
    children,
    confirmLabel,
    cancelLabel = 'Cancel',
    tone = 'danger',
    irreversible = false,
    pending = false,
    error,
}: ConfirmDialogProps) {
    return (
        <Dialog
            open={open}
            onClose={onCancel}
            title={title}
            size="sm"
            dismissible={!pending}
            description={
                <>
                    {children}
                    {irreversible && (
                        <p className="mt-2 font-medium text-fg">
                            This cannot be undone.
                        </p>
                    )}
                </>
            }
            footer={
                <>
                    {/* Initial focus on the safe choice. */}
                    <Button
                        data-autofocus
                        onClick={onCancel}
                        disabled={pending}
                    >
                        {cancelLabel}
                    </Button>
                    <Button
                        variant={tone}
                        loading={pending}
                        onClick={onConfirm}
                    >
                        {confirmLabel}
                    </Button>
                </>
            }
        >
            {error && <Alert tone="danger">{error}</Alert>}
        </Dialog>
    )
}
