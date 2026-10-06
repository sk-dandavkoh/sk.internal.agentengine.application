import type { UseQueryResult } from '@tanstack/react-query'
import type { Execution } from '../../../api/executions'
import type { ExecutionReport } from '../../../api/reports'
import { QuerySection } from '../../../components/QuerySection'
import { Button, Card, TextLink } from '../../../components/ui'
import { VersionLink } from '../../versions/shared'
import { duration } from '../format'
import { StatusBadge, Time } from '../shared'
import { Item, JsonBlock, Mono, None } from './display'

function Details({
    execution: e,
    workflowName,
}: {
    execution: Execution
    workflowName: string | null | undefined
}) {
    return (
        <Card title="Details">
            <dl className="grid gap-4 text-sm sm:grid-cols-2 lg:grid-cols-3">
                <Item term="Status">
                    <StatusBadge status={e.status} />
                </Item>
                <Item term="Workflow">
                    <TextLink
                        to={`/workflows/${encodeURIComponent(e.workflow_id)}`}
                    >
                        {workflowName ?? e.workflow_id}
                    </TextLink>
                </Item>
                <Item term="Version">
                    <VersionLink
                        workflowId={e.workflow_id}
                        version={e.version}
                    />
                </Item>
                <Item term="Trigger">
                    {e.trigger_type}
                    {e.trigger_id && (
                        <>
                            {' '}
                            <Mono>{e.trigger_id}</Mono>
                        </>
                    )}
                </Item>
                <Item term="Created">
                    <Time iso={e.created_at} />
                </Item>
                <Item term="Started">
                    <Time iso={e.started_at} />
                </Item>
                <Item term="Finished">
                    <Time iso={e.finished_at} />
                </Item>
                <Item term="Duration">{duration(e)}</Item>
                <Item term="Correlation id">
                    {e.correlation_id ? (
                        <Mono>{e.correlation_id}</Mono>
                    ) : (
                        <None />
                    )}
                </Item>
            </dl>
        </Card>
    )
}

const amount = (n: number) =>
    n.toLocaleString(undefined, { maximumFractionDigits: 6 })

export function Usage({ report }: { report: ExecutionReport }) {
    const { usage, costs, cost_complete, node_status_counts } = report.metrics
    const cancellation = report.execution.cancellation
    const counts = Object.entries(node_status_counts).filter(([, n]) => n > 0)
    return (
        <dl className="grid gap-4 text-sm sm:grid-cols-2 lg:grid-cols-3">
            <Item term="Tokens">
                {amount(usage.total_tokens)}{' '}
                <span className="text-muted">
                    ({amount(usage.input_tokens)} in,{' '}
                    {amount(usage.output_tokens)} out)
                </span>
            </Item>
            <Item term="Estimated cost">
                {costs.length === 0 ? (
                    <None />
                ) : (
                    costs
                        .map((c) => `${amount(c.amount)} ${c.currency}`)
                        .join(' + ')
                )}
                {!cost_complete && (
                    <span className="block text-muted">
                        Incomplete: some nodes reported tokens without a price.
                    </span>
                )}
            </Item>
            <Item term="Nodes">
                {String(report.nodes.length)}{' '}
                <span className="text-muted">
                    ({String(report.metrics.attempts)} attempts,{' '}
                    {String(report.metrics.retries)} retries,{' '}
                    {String(report.metrics.failed_attempts)} failed attempts)
                </span>
                {counts.length > 0 && (
                    <span className="block text-muted">
                        {counts.map(([s, n]) => `${String(n)} ${s}`).join(', ')}
                    </span>
                )}
            </Item>
            {cancellation && (
                <Item term="Cancellation">
                    Requested <Time iso={cancellation.requested_at} />
                    {cancellation.reason && `: ${cancellation.reason}`}
                </Item>
            )}
        </dl>
    )
}

/** What ran it: reproducibility metadata (AGENTS §73). */
export function RunSnapshot({ report }: { report: ExecutionReport }) {
    const versions = Object.entries(report.execution.node_versions)
    return (
        <div className="flex flex-col gap-4 text-sm">
            <dl className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                <Item term="Engine version">
                    <Mono>{report.execution.engine_version}</Mono>
                </Item>
                <Item term="Version published">
                    <Time iso={report.workflow.published_at} />
                </Item>
                <Item term="Version description">
                    {report.workflow.version_description || <None />}
                </Item>
                <Item term="Node versions">
                    {versions.length === 0 ? (
                        <None />
                    ) : (
                        versions.map(([type, v]) => (
                            <span key={type} className="block">
                                <Mono>{type}</Mono> {v}
                            </span>
                        ))
                    )}
                </Item>
            </dl>
            <div>
                <h3 className="mb-2 font-semibold">Input</h3>
                <JsonBlock value={report.execution.input} />
            </div>
        </div>
    )
}

export function OverviewPanel({
    execution,
    report,
}: {
    execution: Execution
    report: UseQueryResult<ExecutionReport>
}) {
    return (
        <div className="flex flex-col gap-6">
            <Details
                execution={execution}
                workflowName={report.data?.workflow.name}
            />
            <Card
                title="Usage"
                description="Token usage and a list-price cost estimate, never billing data."
                actions={
                    <Button
                        size="sm"
                        onClick={() => void report.refetch()}
                        loading={report.isFetching}
                    >
                        Refresh
                    </Button>
                }
            >
                <QuerySection query={report} subject="usage">
                    {(r) => <Usage report={r} />}
                </QuerySection>
            </Card>
            <Card
                title="Run snapshot"
                description="What this execution ran with. Inputs are redacted by the backend."
            >
                <QuerySection query={report} subject="the run snapshot">
                    {(r) => <RunSnapshot report={r} />}
                </QuerySection>
            </Card>
        </div>
    )
}
