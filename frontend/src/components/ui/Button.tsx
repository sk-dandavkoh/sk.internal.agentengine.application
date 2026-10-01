import type { ComponentProps, MouseEvent } from 'react'
import { cx } from '../../lib/cx'
import { Spinner } from './icons'

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger'

const variants: Record<ButtonVariant, string> = {
    primary: 'bg-primary text-on-primary hover:bg-primary-hover',
    secondary:
        'border border-border-strong bg-surface text-fg hover:bg-surface-muted',
    ghost: 'hover:bg-surface-muted',
    danger: 'bg-danger text-on-danger hover:bg-danger-hover',
}

const sizes = {
    sm: 'h-8 gap-1.5 px-2.5',
    md: 'h-10 gap-2 px-4',
}

export interface ButtonProps extends ComponentProps<'button'> {
    variant?: ButtonVariant
    size?: keyof typeof sizes
    /** Blocks activation and shows a spinner while an action is in flight. */
    loading?: boolean
}

export function Button({
    variant = 'secondary',
    size = 'md',
    loading = false,
    type = 'button',
    className,
    onClick,
    children,
    ...props
}: ButtonProps) {
    // While loading the button stays focusable (aria-disabled, not disabled),
    // so keyboard focus is not dropped to <body> mid-action. Activation,
    // including form submission, is blocked here instead.
    function handleClick(event: MouseEvent<HTMLButtonElement>) {
        if (loading) {
            event.preventDefault()
            return
        }
        onClick?.(event)
    }

    return (
        <button
            type={type}
            aria-disabled={loading || undefined}
            aria-busy={loading || undefined}
            onClick={handleClick}
            className={cx(
                'focus-ring inline-flex shrink-0 items-center justify-center rounded-md text-sm font-medium whitespace-nowrap transition-colors disabled:cursor-not-allowed disabled:opacity-50 aria-disabled:cursor-wait aria-disabled:opacity-70',
                variants[variant],
                sizes[size],
                className,
            )}
            {...props}
        >
            {loading && <Spinner />}
            {children}
        </button>
    )
}
