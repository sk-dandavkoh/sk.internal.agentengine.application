import { Badge, LockIcon, TextLink } from '../../components/ui'
import type { WorkflowVersionSummary } from '../../api/workflows'
import { formatDateTime } from '../../lib/format'
import { versionPath } from './paths'

/** Published versions are read-only snapshots; said in text, not color. */
export function ImmutableBadge() {
    return (
        <Badge tone="neutral">
            <LockIcon />
            Immutable
        </Badge>
    )
}

/** The version new triggers run and the workflow shows as current. */
export function LatestBadge() {
    return <Badge tone="success">Latest</Badge>
}

export function Time({ iso }: { iso: string }) {
    return (
        <time dateTime={iso} className="whitespace-nowrap">
            {formatDateTime(iso)}
        </time>
    )
}

export function Author({
    author,
}: {
    author: WorkflowVersionSummary['published_by']
}) {
    if (author === null) return <span className="text-muted">Not recorded</span>
    // The id only when the account is gone; its title tells users apart.
    return author.username === null ? (
        <code className="font-mono text-xs">{author.id}</code>
    ) : (
        <span title={author.id}>{author.username}</span>
    )
}

/** `v3`, linking to that version of the workflow. */
export function VersionLink({
    workflowId,
    version,
}: {
    workflowId: string
    version: number
}) {
    return <TextLink to={versionPath(workflowId, version)}>v{version}</TextLink>
}
