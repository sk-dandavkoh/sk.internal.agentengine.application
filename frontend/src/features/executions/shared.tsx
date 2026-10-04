import { useState } from 'react'
import type { ExecutionListItem, ExecutionStatus } from '../../api/executions'
import {
    Badge,
    Button,
    Table,
    TextLink,
    type TableColumn,
} from '../../components/ui'
import { formatDateTime } from '../../lib/format'
import { FailureDetails } from '../dashboard/FailureDetails'
import { executionStatusDisplay } from '../dashboard/format'
import { duration, executionPath, isFailure } from './format'
import { VersionLink } from '../versions/shared'

/** Label and tone together: color is never the only signal (AGENTS §63). */
export function StatusBadge({ status }: { status: ExecutionStatus }) {
    const { label, tone } = executionStatusDisplay[status]
    return (
        <Badge tone={tone} dot>
            {label}
        </Badge>
    )
}

export function Time({ iso }: { iso: string | null }) {
    if (iso === null) return <span className="text-muted">—</span>
    return (
        <time dateTime={iso} className="whitespace-nowrap">
            {formatDateTime(iso)}
        </time>
    )
}

/** A short id that still links to the full one. */
export function ExecutionLink({ id }: { id: string }) {
    return (
        <TextLink to={executionPath(id)} className="font-mono text-xs">
            <span
                title={id}
                className="inline-block max-w-32 truncate align-bottom"
            >
                {id}
            </span>
        </TextLink>
    )
}

interface ExecutionTableProps {
    caption: string
    executions: readonly ExecutionListItem[]
    /** Failed rows can expand to show their recorded errors (fetched on demand). */
    errorDetails?: boolean
    /** Appended after the standard columns, e.g. timestamps or actions. */
    extraColumns?: readonly TableColumn<ExecutionListItem>[]
}

export function ExecutionTable({
    caption,
    executions,
    errorDetails = false,
    extraColumns = [],
}: ExecutionTableProps) {
    const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set())
    const toggle = (id: string) => {
        setExpanded((current) => {
            const next = new Set(current)
            if (!next.delete(id)) next.add(id)
            return next
        })
    }
    const columns: TableColumn<ExecutionListItem>[] = [
        {
            key: 'id',
            header: 'Execution',
            cell: (e) => <ExecutionLink id={e.id} />,
        },
        {
            key: 'project',
            header: 'Project',
            cell: (e) => <span title={e.project_id}>{e.project_name}</span>,
        },
        {
            key: 'version',
            header: 'Version',
            cell: (e) => (
                <VersionLink projectId={e.project_id} version={e.version} />
            ),
        },
        {
            key: 'status',
            header: 'Status',
            cell: (e) => <StatusBadge status={e.status} />,
        },
        { key: 'trigger', header: 'Trigger', cell: (e) => e.trigger_type },
        {
            key: 'created',
            header: 'Created',
            cell: (e) => <Time iso={e.created_at} />,
        },
        {
            key: 'duration',
            header: 'Duration',
            align: 'end',
            cell: (e) => (
                <span className="whitespace-nowrap">{duration(e)}</span>
            ),
        },
        ...extraColumns,
    ]
    if (errorDetails)
        columns.push({
            key: 'error',
            header: <span className="sr-only">Error details</span>,
            align: 'end',
            cell: (e) => {
                if (!isFailure(e.status)) return null
                const open = expanded.has(e.id)
                return (
                    <Button
                        size="sm"
                        variant="ghost"
                        // Starts with the visible text (WCAG 2.5.3); the
                        // id tells the rows' buttons apart.
                        aria-label={`${open ? 'Hide' : 'Show'} error of execution ${e.id}`}
                        aria-expanded={open}
                        aria-controls={`error-${e.id}`}
                        onClick={() => {
                            toggle(e.id)
                        }}
                    >
                        {open ? 'Hide error' : 'Show error'}
                    </Button>
                )
            },
        })
    return (
        <Table
            caption={caption}
            hideCaption
            columns={columns}
            rows={executions}
            rowKey={(e) => e.id}
            details={(e) =>
                expanded.has(e.id) ? (
                    <FailureDetails id={`error-${e.id}`} executionId={e.id} />
                ) : null
            }
        />
    )
}
