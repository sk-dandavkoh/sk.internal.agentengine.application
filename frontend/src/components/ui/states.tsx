import type { ReactNode } from 'react'
import { cx } from '../../lib/cx'
import { Spinner } from './icons'

interface StateProps {
    title: string
    /** Heading level in the page outline. */
    titleAs?: 'h1' | 'h2' | 'h3'
    description?: ReactNode
    /** Next step for the user, e.g. a create or retry button. */
    action?: ReactNode
}

function StateLayout({
    title,
    titleAs: Title = 'h2',
    description,
    action,
    role,
}: StateProps & { role?: 'alert' }) {
    return (
        <div
            role={role}
            className="flex flex-col items-center gap-2 rounded-lg border border-dashed border-border-strong px-6 py-10 text-center"
        >
            <Title className="text-base font-semibold text-fg">{title}</Title>
            {description && (
                <div className="max-w-prose text-sm text-muted">
                    {description}
                </div>
            )}
            {action && <div className="mt-2">{action}</div>}
        </div>
    )
}

/** A loaded collection that has no items. */
export function EmptyState(props: StateProps) {
    return <StateLayout {...props} />
}

/**
 * A region that failed to load. The message must be user-facing; technical
 * details belong in logs or an inspector, never here.
 */
export function ErrorState({
    title = 'Something went wrong',
    ...props
}: Partial<StateProps>) {
    return <StateLayout title={title} {...props} role="alert" />
}

export function LoadingState({
    label = 'Loading…',
    className,
}: {
    label?: string
    className?: string
}) {
    return (
        <div
            role="status"
            className={cx(
                'flex items-center justify-center gap-2 py-10 text-sm text-muted',
                className,
            )}
        >
            <Spinner />
            {label}
        </div>
    )
}

/**
 * Placeholder block for progressive loading. Decorative: the surrounding
 * region must announce loading itself (aria-busy or a LoadingState label).
 */
export function Skeleton({ className }: { className?: string }) {
    return (
        <div
            aria-hidden="true"
            className={cx(
                'h-4 animate-pulse rounded bg-surface-muted motion-reduce:animate-none',
                className,
            )}
        />
    )
}
