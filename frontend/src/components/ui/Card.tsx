import type { ReactNode } from 'react'
import { cx } from '../../lib/cx'

export interface CardProps {
    title?: string
    titleAs?: 'h2' | 'h3'
    description?: ReactNode
    /** Header actions, end-aligned next to the title. */
    actions?: ReactNode
    children?: ReactNode
    className?: string
}

export function Card({
    title,
    titleAs: Title = 'h2',
    description,
    actions,
    children,
    className,
}: CardProps) {
    const hasHeader = Boolean(title ?? actions)

    return (
        <section
            className={cx(
                'rounded-lg border border-border bg-surface shadow-sm',
                className,
            )}
        >
            {hasHeader && (
                <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border px-4 py-3">
                    <div className="flex flex-col gap-0.5">
                        {title && (
                            <Title className="text-base font-semibold">
                                {title}
                            </Title>
                        )}
                        {description && (
                            <div className="text-sm text-muted">
                                {description}
                            </div>
                        )}
                    </div>
                    {actions && <div className="flex gap-2">{actions}</div>}
                </div>
            )}
            {children && <div className="p-4">{children}</div>}
        </section>
    )
}
