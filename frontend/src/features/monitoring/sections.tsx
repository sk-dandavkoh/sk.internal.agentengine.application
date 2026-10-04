import { useQuery } from '@tanstack/react-query'
import { useApiClient } from '../../api/context'
import { executionStatusSchema } from '../../api/executions'
import type { DurationStats, MonitoringSummary } from '../../api/monitoring'
import { executionQueries } from '../../api/queries'
import type { ExecutionMetrics } from '../../api/reports'
import { QuerySection } from '../../components/QuerySection'
import { Badge, Card, Table, type TableColumn } from '../../components/ui'
import { cx } from '../../lib/cx'
import { formatDateTime } from '../../lib/format'
import {
    ACTIVE_STATUSES,
    executionStatusDisplay,
    formatMilliseconds,
} from '../dashboard/format'
import {
    formatBucket,
    formatCost,
    formatCount,
    formatPercent,
    OUTCOMES,
    outcomeCounts,
    rates,
    type Outcome,
} from './model'

// -- now (current state, not the window) -----------------------------------------

export function ActiveNowCard() {
    const query = useQuery(executionQueries.statusCounts(useApiClient()))
    return (
        <Card
            title="Right now"
            description="Executions not finished yet, whenever they were created."
        >
            <QuerySection query={query} subject="current executions">
                {({ counts }) => (
                    <dl className="grid grid-cols-[1fr_auto] gap-x-4 gap-y-2 text-sm">
                        {ACTIVE_STATUSES.map((status) => (
                            <div key={status} className="contents">
                                <dt className="text-muted">
                                    {executionStatusDisplay[status].label}
                                </dt>
                                <dd className="text-right tabular-nums">
                                    {formatCount(counts[status])}
                                </dd>
                            </div>
                        ))}
                    </dl>
                )}
            </QuerySection>
        </Card>
    )
}

// -- headline numbers ---------------------------------------------------------------

function Stat({
    label,
    value,
    detail,
}: {
    label: string
    value: string
    detail?: string | undefined
}) {
    return (
        <div className="flex flex-col gap-1 rounded-lg border border-border bg-surface p-4">
            <dt className="text-sm text-muted">{label}</dt>
            <dd className="text-2xl font-semibold tabular-nums">{value}</dd>
            {detail && <dd className="text-xs text-muted">{detail}</dd>}
        </div>
    )
}

export function StatsGrid({ summary }: { summary: MonitoringSummary }) {
    const r = rates(summary)
    const { totals } = summary
    return (
        <dl className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <Stat
                label="Executions created"
                value={formatCount(summary.executions)}
                detail={`${formatCount(r.unfinished)} not finished yet`}
            />
            <Stat
                label="Success rate"
                value={formatPercent(r.completed, r.finished)}
                detail={`${formatCount(r.completed)} completed of ${formatCount(r.finished)} finished`}
            />
            <Stat
                label="Failure rate"
                value={formatPercent(r.failed, r.finished)}
                detail={`${formatCount(r.failed)} failed or timed out, ${formatCount(r.cancelled)} cancelled`}
            />
            <Stat
                label="Node retries"
                value={formatCount(totals.retries)}
                detail={`${formatCount(totals.failed_attempts)} failed of ${formatCount(totals.attempts)} attempts`}
            />
            <Stat
                label="Tokens"
                value={formatCount(totals.usage.total_tokens)}
                detail={`${formatCount(totals.usage.input_tokens)} in, ${formatCount(totals.usage.output_tokens)} out`}
            />
            <Stat
                label="Estimated cost"
                value={costText(totals)}
                detail={costNote(totals)}
            />
        </dl>
    )
}

/** Per currency, never summed across currencies. */
function costText(metrics: ExecutionMetrics): string {
    if (metrics.costs.length === 0) return '—'
    return metrics.costs.map(formatCost).join(' + ')
}

function costNote(metrics: ExecutionMetrics): string | undefined {
    if (!metrics.cost_complete)
        return 'Incomplete: some nodes reported tokens without a price.'
    if (metrics.costs.length === 0) return 'No node reported a cost.'
    return 'List-price estimate, not billing data.'
}

// -- timeline ---------------------------------------------------------------------

const OUTCOME_COLOR: Record<Outcome, string> = {
    completed: 'bg-emerald-600',
    failed: 'bg-red-600',
    cancelled: 'bg-slate-400',
    unfinished: 'bg-sky-500',
}

/**
 * Executions created per bucket, stacked by outcome. The chart is a visual
 * summary only (hidden from assistive technology); the table below it holds
 * the same numbers.
 */
export function TimelineCard({ summary }: { summary: MonitoringSummary }) {
    const buckets = summary.timeline.map((b) => ({
        ...b,
        outcomes: outcomeCounts(b.counts),
        total: Object.values(b.counts).reduce((a, n) => a + n, 0),
    }))
    const peak = Math.max(0, ...buckets.map((b) => b.total))
    const unit = formatBucket(summary.bucket_seconds)
    const columns: TableColumn<(typeof buckets)[number]>[] = [
        {
            key: 'start',
            header: 'From',
            cell: (b) => formatDateTime(b.start),
        },
        ...OUTCOMES.map((o) => ({
            key: o.key,
            header: o.label,
            align: 'end' as const,
            cell: (b: (typeof buckets)[number]) =>
                formatCount(b.outcomes[o.key]),
        })),
        {
            key: 'total',
            header: 'Total',
            align: 'end',
            cell: (b) => formatCount(b.total),
        },
    ]
    return (
        <Card
            title="Executions over time"
            description={`Executions created per ${unit}, by outcome. Peak: ${formatCount(peak)} per ${unit}.`}
        >
            <Legend />
            <div
                aria-hidden="true"
                className="mt-3 flex h-40 items-end gap-px border-b border-border"
            >
                {buckets.map((b) => (
                    <div
                        key={b.start}
                        title={`${formatDateTime(b.start)}: ${formatCount(b.total)}`}
                        className="flex h-full min-w-0 flex-1 flex-col-reverse"
                    >
                        {OUTCOMES.map((o) =>
                            b.outcomes[o.key] > 0 ? (
                                <div
                                    key={o.key}
                                    className={OUTCOME_COLOR[o.key]}
                                    style={{
                                        // Data-driven height: no Tailwind class can express it.
                                        height: `${String((b.outcomes[o.key] / peak) * 100)}%`,
                                    }}
                                />
                            ) : null,
                        )}
                    </div>
                ))}
            </div>
            <div
                aria-hidden="true"
                className="mt-1 flex justify-between text-xs text-muted"
            >
                <span>{formatDateTime(summary.since)}</span>
                <span>{formatDateTime(summary.until)}</span>
            </div>
            <details className="mt-4">
                <summary className="focus-ring cursor-pointer rounded text-sm font-medium">
                    Show as table
                </summary>
                <div className="mt-3">
                    <Table
                        caption={`Executions created per ${unit}`}
                        columns={columns}
                        rows={buckets}
                        rowKey={(b) => b.start}
                    />
                </div>
            </details>
        </Card>
    )
}

function Legend() {
    return (
        <ul className="flex flex-wrap gap-4 text-xs text-muted">
            {OUTCOMES.map((o) => (
                <li key={o.key} className="flex items-center gap-1.5">
                    <span
                        aria-hidden="true"
                        className={cx(
                            'size-2.5 rounded-sm',
                            OUTCOME_COLOR[o.key],
                        )}
                    />
                    {o.label}
                </li>
            ))}
        </ul>
    )
}

// -- durations ---------------------------------------------------------------------

const durationColumns = <Row,>(
    stats: (row: Row) => DurationStats | null,
): TableColumn<Row>[] =>
    (
        [
            ['p50', 'Median', (s: DurationStats) => s.p50_ms],
            ['p95', 'p95', (s: DurationStats) => s.p95_ms],
            ['max', 'Max', (s: DurationStats) => s.max_ms],
        ] as const
    ).map(([key, header, pick]) => ({
        key,
        header,
        align: 'end',
        cell: (row: Row) => {
            const s = stats(row)
            return s ? formatMilliseconds(pick(s)) : '—'
        },
    }))

export function DurationsCard({ summary }: { summary: MonitoringSummary }) {
    const rows = executionStatusSchema.options.flatMap((status) => {
        const stats = summary.durations[status]
        return stats ? [{ status, stats }] : []
    })
    return (
        <Card
            title="Execution durations"
            description="From start to finish, per outcome. Unfinished runs have no duration yet."
        >
            <Table
                caption="Execution durations by outcome"
                hideCaption
                rows={rows}
                rowKey={(r) => r.status}
                empty="No finished executions in this window."
                columns={[
                    {
                        key: 'status',
                        header: 'Outcome',
                        cell: (r) => (
                            <Badge tone={executionStatusDisplay[r.status].tone}>
                                {executionStatusDisplay[r.status].label}
                            </Badge>
                        ),
                    },
                    {
                        key: 'count',
                        header: 'Runs',
                        align: 'end',
                        cell: (r) => formatCount(r.stats.count),
                    },
                    {
                        key: 'mean',
                        header: 'Mean',
                        align: 'end',
                        cell: (r) => formatMilliseconds(r.stats.mean_ms),
                    },
                    ...durationColumns(
                        (r: { stats: DurationStats }) => r.stats,
                    ),
                ]}
            />
        </Card>
    )
}

// -- errors ------------------------------------------------------------------------

export function ErrorsCard({ summary }: { summary: MonitoringSummary }) {
    const shown = summary.errors.reduce((a, e) => a + e.count, 0)
    const rest = summary.errors_total - shown
    return (
        <Card
            title="Errors"
            description="Error codes of failed and timed-out executions, most frequent first."
        >
            <Table
                caption="Execution errors by code"
                hideCaption
                rows={summary.errors}
                rowKey={(e) => `${e.code}/${e.kind}`}
                empty="No failed executions in this window."
                columns={[
                    {
                        key: 'code',
                        header: 'Code',
                        cell: (e) => (
                            <span className="font-mono">{e.code}</span>
                        ),
                    },
                    { key: 'kind', header: 'Kind', cell: (e) => e.kind },
                    {
                        key: 'count',
                        header: 'Executions',
                        align: 'end',
                        cell: (e) => formatCount(e.count),
                    },
                ]}
            />
            {rest > 0 && (
                <p className="mt-3 text-sm text-muted">
                    {formatCount(rest)} more with other codes.
                </p>
            )}
        </Card>
    )
}

// -- nodes -------------------------------------------------------------------------

type NodeRow = MonitoringSummary['nodes'][number]

const count = (row: NodeRow, status: string) =>
    row.metrics.node_status_counts[status] ?? 0

export function NodePerformanceCard({
    summary,
}: {
    summary: MonitoringSummary
}) {
    return (
        <Card
            title="Node performance"
            description="Per node type, over the node executions of these runs. Durations are of each node's last attempt."
        >
            <Table
                caption="Node performance by node type"
                hideCaption
                rows={summary.nodes}
                rowKey={(n) => n.node_type}
                empty="No node executions recorded in this window."
                columns={[
                    {
                        key: 'type',
                        header: 'Node type',
                        cell: (n) => (
                            <span className="font-mono">{n.node_type}</span>
                        ),
                    },
                    {
                        key: 'completed',
                        header: 'Completed',
                        align: 'end',
                        cell: (n) => formatCount(count(n, 'completed')),
                    },
                    {
                        key: 'failed',
                        header: 'Failed',
                        align: 'end',
                        cell: (n) => formatCount(count(n, 'failed')),
                    },
                    {
                        key: 'retries',
                        header: 'Retries',
                        align: 'end',
                        cell: (n) => formatCount(n.metrics.retries),
                    },
                    ...durationColumns((n: NodeRow) => n.durations),
                    {
                        key: 'tokens',
                        header: 'Tokens',
                        align: 'end',
                        cell: (n) => formatCount(n.metrics.usage.total_tokens),
                    },
                    {
                        key: 'cost',
                        header: 'Est. cost',
                        align: 'end',
                        cell: (n) =>
                            costText(n.metrics) +
                            (n.metrics.cost_complete ? '' : ' (incomplete)'),
                    },
                ]}
            />
        </Card>
    )
}
