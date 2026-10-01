import type { ReactNode } from 'react'

export interface PageHeaderProps {
    title: string
    description?: ReactNode
    /** Page-level actions, end-aligned (wrapping below on narrow screens). */
    actions?: ReactNode
}

/** The one h1 of a page. */
export function PageHeader({ title, description, actions }: PageHeaderProps) {
    return (
        <header className="mb-6 flex flex-wrap items-start justify-between gap-4">
            <div className="flex flex-col gap-1">
                <h1 className="text-2xl font-semibold tracking-tight">
                    {title}
                </h1>
                {description && (
                    <div className="text-sm text-muted">{description}</div>
                )}
            </div>
            {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
        </header>
    )
}
