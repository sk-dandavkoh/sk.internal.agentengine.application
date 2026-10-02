import { Badge, LockIcon, TextLink } from '../../components/ui'
import type { ProjectVersionSummary } from '../../api/projects'
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

/** The version new triggers run and the project shows as current. */
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
    author: ProjectVersionSummary['published_by']
}) {
    if (author === null) return <span className="text-muted">Not recorded</span>
    // The id only when the account is gone; its title tells users apart.
    return author.username === null ? (
        <code className="font-mono text-xs">{author.id}</code>
    ) : (
        <span title={author.id}>{author.username}</span>
    )
}

/** `v3`, linking to that version of the project. */
export function VersionLink({
    projectId,
    version,
}: {
    projectId: string
    version: number
}) {
    return <TextLink to={versionPath(projectId, version)}>v{version}</TextLink>
}
