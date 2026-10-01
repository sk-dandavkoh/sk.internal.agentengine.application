import { useEffect, useId, useRef, type ReactNode } from 'react'
import { cx } from '../../lib/cx'
import { Button } from './Button'
import { CloseIcon } from './icons'

export interface DialogProps {
    open: boolean
    /** Called on Escape, the close button, or any other native close. */
    onClose: () => void
    title: string
    description?: ReactNode
    children?: ReactNode
    /** Action buttons, rendered end-aligned below the content. */
    footer?: ReactNode
    /** false blocks Escape and hides the close button, e.g. while saving. */
    dismissible?: boolean
    size?: 'sm' | 'md' | 'lg'
}

const widths = { sm: 'max-w-sm', md: 'max-w-lg', lg: 'max-w-2xl' }

/**
 * Modal dialog on the native <dialog> element: the browser provides the top
 * layer, the inert background, focus containment, Escape and focus return.
 * Content mounts only while open, so forms inside start fresh each time.
 * Put data-autofocus on the element that should receive initial focus.
 */
export function Dialog({
    open,
    onClose,
    title,
    description,
    children,
    footer,
    dismissible = true,
    size = 'md',
}: DialogProps) {
    const ref = useRef<HTMLDialogElement>(null)
    const titleId = useId()
    const descriptionId = useId()

    useEffect(() => {
        const dialog = ref.current
        if (!dialog) return
        if (open && !dialog.open) {
            dialog.showModal()
            dialog.querySelector<HTMLElement>('[data-autofocus]')?.focus()
        } else if (!open && dialog.open) {
            dialog.close()
        }
    }, [open])

    return (
        <dialog
            ref={ref}
            aria-labelledby={titleId}
            aria-describedby={description ? descriptionId : undefined}
            onCancel={(event) => {
                if (!dismissible) event.preventDefault()
            }}
            // The browser may close the dialog on its own (Escape); report it
            // so the open prop stays the source of truth.
            onClose={() => {
                if (open) onClose()
            }}
            className={cx(
                'm-auto w-[calc(100%-2rem)] rounded-lg border border-border bg-surface p-0 text-fg shadow-xl',
                widths[size],
            )}
        >
            {open && (
                <div className="flex flex-col gap-4 p-6">
                    <div className="flex items-start justify-between gap-4">
                        <div className="flex flex-col gap-1">
                            <h2 id={titleId} className="text-lg font-semibold">
                                {title}
                            </h2>
                            {description && (
                                <div
                                    id={descriptionId}
                                    className="text-sm text-muted"
                                >
                                    {description}
                                </div>
                            )}
                        </div>
                        {dismissible && (
                            <Button
                                variant="ghost"
                                size="sm"
                                aria-label="Close"
                                onClick={onClose}
                                className="-mt-1 -mr-2"
                            >
                                <CloseIcon />
                            </Button>
                        )}
                    </div>
                    {children}
                    {footer && (
                        <div className="flex flex-wrap justify-end gap-2">
                            {footer}
                        </div>
                    )}
                </div>
            )}
        </dialog>
    )
}
