import type { ReactNode } from 'react'
import { cx } from '../../lib/cx'
import { feedbackTones, toneClasses, type FeedbackTone } from './shared'

export interface AlertProps {
    tone?: FeedbackTone
    title?: string
    children?: ReactNode
    /** Optional action, e.g. a retry button. */
    action?: ReactNode
    className?: string
}

/** Inline, persistent message. Errors are announced as alerts. */
export function Alert({
    tone = 'info',
    title,
    children,
    action,
    className,
}: AlertProps) {
    const { icon: Icon, srLabel } = feedbackTones[tone]

    return (
        <div
            role={tone === 'danger' ? 'alert' : 'status'}
            className={cx(
                'flex items-start gap-3 rounded-md p-3 text-sm',
                toneClasses[tone],
                className,
            )}
        >
            <span className="mt-0.5">
                <Icon />
            </span>
            <div className="flex flex-1 flex-col gap-1">
                {title && (
                    <p className="font-semibold">
                        <span className="sr-only">{srLabel} </span>
                        {title}
                    </p>
                )}
                {children && (
                    <div>
                        {!title && <span className="sr-only">{srLabel} </span>}
                        {children}
                    </div>
                )}
            </div>
            {action}
        </div>
    )
}
