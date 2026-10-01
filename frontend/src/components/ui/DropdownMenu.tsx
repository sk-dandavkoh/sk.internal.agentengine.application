import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react'
import { cx } from '../../lib/cx'
import { Button, type ButtonVariant } from './Button'
import { ChevronDownIcon } from './icons'

export interface MenuItem {
    label: string
    onSelect: () => void
    disabled?: boolean
    /** Destructive items should still open a ConfirmDialog. */
    tone?: 'danger'
}

export interface DropdownMenuProps {
    /** Trigger text; also the menu's accessible name. */
    label: string
    items: readonly MenuItem[]
    variant?: ButtonVariant
    align?: 'start' | 'end'
}

// ponytail: absolutely positioned, so an overflow container (e.g. a scrolling
// Table) can clip it. Upgrade: Popover API + CSS anchor positioning.
/** WAI-ARIA menu button: arrows move, Escape closes and returns focus. */
export function DropdownMenu({
    label,
    items,
    variant = 'secondary',
    align = 'start',
}: DropdownMenuProps) {
    const [open, setOpen] = useState(false)
    const rootRef = useRef<HTMLDivElement>(null)
    const triggerRef = useRef<HTMLButtonElement>(null)
    const menuRef = useRef<HTMLDivElement>(null)
    const menuId = useId()
    // Which item to focus once the menu has rendered.
    const pendingFocus = useRef<'first' | 'last'>('first')

    const enabledItems = () =>
        Array.from(
            menuRef.current?.querySelectorAll<HTMLButtonElement>(
                '[role="menuitem"]:not(:disabled)',
            ) ?? [],
        )

    useEffect(() => {
        if (!open) return
        const targets = enabledItems()
        ;(pendingFocus.current === 'first'
            ? targets[0]
            : targets.at(-1)
        )?.focus()

        function onPointerDown(event: PointerEvent) {
            if (!rootRef.current?.contains(event.target as Node)) setOpen(false)
        }
        document.addEventListener('pointerdown', onPointerDown)
        return () => {
            document.removeEventListener('pointerdown', onPointerDown)
        }
    }, [open])

    function openMenu(focus: 'first' | 'last') {
        pendingFocus.current = focus
        setOpen(true)
    }

    function close() {
        setOpen(false)
        triggerRef.current?.focus()
    }

    function onTriggerKeyDown(event: KeyboardEvent) {
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            event.preventDefault()
            openMenu(event.key === 'ArrowDown' ? 'first' : 'last')
        }
    }

    function onMenuKeyDown(event: KeyboardEvent) {
        const targets = enabledItems()
        const current = targets.indexOf(
            document.activeElement as HTMLButtonElement,
        )
        const last = targets.length - 1
        const next = {
            ArrowDown: current >= last ? 0 : current + 1,
            ArrowUp: current <= 0 ? last : current - 1,
            Home: 0,
            End: last,
        }[event.key]

        if (next !== undefined) {
            event.preventDefault()
            targets[next]?.focus()
        } else if (event.key === 'Escape') {
            event.preventDefault()
            close()
        } else if (event.key === 'Tab') {
            // Let focus move on naturally; just drop the menu.
            setOpen(false)
        }
    }

    return (
        <div ref={rootRef} className="relative inline-block">
            <Button
                ref={triggerRef}
                variant={variant}
                aria-haspopup="menu"
                aria-expanded={open}
                aria-controls={open ? menuId : undefined}
                onClick={() => {
                    if (open) setOpen(false)
                    else openMenu('first')
                }}
                onKeyDown={onTriggerKeyDown}
            >
                {label}
                <ChevronDownIcon />
            </Button>
            {open && (
                <div
                    ref={menuRef}
                    id={menuId}
                    role="menu"
                    aria-label={label}
                    onKeyDown={onMenuKeyDown}
                    className={cx(
                        'absolute z-20 mt-1 flex min-w-44 flex-col rounded-md border border-border bg-surface p-1 shadow-lg',
                        align === 'end' ? 'right-0' : 'left-0',
                    )}
                >
                    {items.map((item) => (
                        <button
                            key={item.label}
                            type="button"
                            role="menuitem"
                            tabIndex={-1}
                            disabled={item.disabled}
                            onClick={() => {
                                close()
                                item.onSelect()
                            }}
                            className={cx(
                                'rounded px-3 py-2 text-left text-sm outline-none hover:bg-surface-muted focus:bg-surface-muted disabled:cursor-not-allowed disabled:opacity-50',
                                item.tone === 'danger'
                                    ? 'text-danger-text'
                                    : 'text-fg',
                            )}
                        >
                            {item.label}
                        </button>
                    ))}
                </div>
            )}
        </div>
    )
}
