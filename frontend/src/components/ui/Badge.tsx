import type { ReactNode } from 'react'
import { cx } from '../../lib/cx'
import { toneClasses, type Tone } from './shared'

export interface BadgeProps {
    tone?: Tone
    /** Leading dot, for status indicators. The text stays the label. */
    dot?: boolean
    children: ReactNode
}

export function Badge({ tone = 'neutral', dot = false, children }: BadgeProps) {
    return (
        <span
            className={cx(
                'inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap',
                toneClasses[tone],
            )}
        >
            {dot && (
                <span
                    aria-hidden="true"
                    className="size-1.5 rounded-full bg-current"
                />
            )}
            {children}
        </span>
    )
}
