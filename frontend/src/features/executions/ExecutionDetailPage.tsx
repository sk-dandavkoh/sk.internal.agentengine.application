import { useQuery } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { useParams } from 'react-router'
import { useApiClient } from '../../api/context'
import { ApiError } from '../../api/errors'
import type { Execution } from '../../api/executions'
import { executionQueries } from '../../api/queries'
import type { ExecutionReport, NodeExecution } from '../../api/reports'
import { QuerySection } from '../../components/QuerySection'
import {
    Button,
    Card,
    ErrorState,
    PageHeader,
    Table,
    TextLink,
    type TableColumn,
} from '../../components/ui'
import { FailureDetails } from '../dashboard/FailureDetails'
import { VersionLink } from '../versions/shared'
import { CancelExecution } from './CancelExecution'
import { duration, isFailure } from './format'
import { LiveActivity, LiveIndicator } from './live'
import { useLiveExecution } from './useLiveExecution'
import { StatusBadge, Time } from './shared'

function Item({ term, children }: { term: string; children: ReactNode }) {
    return (
        <div className="flex flex-col gap-0.5">
            <dt className="text-muted">{term}</dt>
            <dd className="break-words">{children}</dd>
        </div>
    )
}

const Mono = ({ children }: { children: ReactNode }) => (
    <code className="font-mono text-xs break-all">{children}</code>
)

const none = <span className="text-muted">None</span>

function Details({
    execution,
    projectName,
}: {
    execution: Execution
    projectName: string | null | undefined
}) {
    const e = execution
    return (
        <Card title="Details">
            <dl className="grid gap-4 text-sm sm:grid-cols-2 lg:grid-cols-3">
                <Item term="Status">
                    <StatusBadge status={e.status} />
                </Item>
                <Item term="Project">
                    <TextLink
                        to={`/projects/${encodeURIComponent(e.project_id)}`}
                    >
                        {projectName ?? e.project_id}
                    </TextLink>
                </Item>
                <Item term="Version">
                    <VersionLink projectId={e.project_id} version={e.version} />
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
                    {e.correlation_id ? <Mono>{e.correlation_id}</Mono> : none}
                </Item>
            </dl>
        </Card>
    )
}

const amount = (n: number) =>
    n.toLocaleString(undefined, { maximumFractionDigits: 6 })

function Usage({ report }: { report: ExecutionReport }) {
    const { usage, costs, cost_complete } = report.metrics
    const cancellation = report.execution.cancellation
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
                {costs.length === 0
                    ? none
                    : costs
                          .map((c) => `${amount(c.amount)} ${c.currency}`)
                          .join(' + ')}
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
                    {String(report.metrics.retries)} retries)
                </span>
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

const nodeColumns: readonly TableColumn<NodeExecution>[] = [
    {
        key: 'node',
        header: 'Node',
        cell: (n) => (
            <>
                {n.label || n.node_id}{' '}
                <span className="text-muted">{n.node_type}</span>
            </>
        ),
    },
    // The backend's node status, verbatim: no client-side state machine.
    { key: 'status', header: 'Status', cell: (n) => n.status },
    {
        key: 'attempts',
        header: 'Attempts',
        align: 'end',
        cell: (n) => String(n.attempts),
    },
    {
        key: 'started',
        header: 'Started',
        cell: (n) => <Time iso={n.started_at} />,
    },
    {
        key: 'finished',
        header: 'Finished',
        cell: (n) => <Time iso={n.finished_at} />,
    },
]

function ExecutionView({ execution }: { execution: Execution }) {
    const report = useQuery(
        executionQueries.report(useApiClient(), execution.id),
    )
    // Only unfinished runs stream; a finished one has nothing left to say.
    const live = useLiveExecution(execution.id, execution.finished_at === null)
    const refetch = () => {
        void report.refetch()
    }
    return (
        <div className="flex flex-col gap-6">
            <LiveIndicator state={live.state} />
            {isFailure(execution.status) && (
                <Card title="Failure">
                    <FailureDetails
                        id={`error-${execution.id}`}
                        executionId={execution.id}
                    />
                </Card>
            )}
            <Details
                execution={execution}
                projectName={report.data?.project.name}
            />
            <Card
                title="Usage"
                description="Token usage and a list-price cost estimate, never billing data."
                actions={
                    <Button
                        size="sm"
                        onClick={refetch}
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
            <Card title="Nodes">
                <QuerySection query={report} subject="the nodes">
                    {(r) => (
                        <Table
                            caption="Node status"
                            hideCaption
                            columns={nodeColumns}
                            rows={r.nodes}
                            rowKey={(n) => n.node_id}
                        />
                    )}
                </QuerySection>
            </Card>
            {live.state !== 'idle' && (
                <Card
                    title="Live activity"
                    description="Events received while this page is open."
                >
                    <LiveActivity events={live.events} />
                </Card>
            )}
        </div>
    )
}

function ExecutionNotFound() {
    return (
        <ErrorState
            title="Execution not found"
            titleAs="h1"
            description="It does not exist or you do not have access to it."
            action={<TextLink to="/executions">All executions</TextLink>}
        />
    )
}

export function ExecutionDetailPage() {
    const { executionId = '' } = useParams()
    const query = useQuery(executionQueries.detail(useApiClient(), executionId))
    if (
        query.data === undefined &&
        query.error instanceof ApiError &&
        query.error.kind === 'not_found'
    )
        return <ExecutionNotFound />
    const execution = query.data
    return (
        <>
            <PageHeader
                title="Execution"
                description={
                    <>
                        <Mono>{executionId}</Mono> ·{' '}
                        <TextLink to="/executions">All executions</TextLink>
                    </>
                }
                actions={
                    <>
                        <Button
                            loading={query.isFetching}
                            onClick={() => void query.refetch()}
                        >
                            Refresh
                        </Button>
                        {execution && <CancelExecution execution={execution} />}
                    </>
                }
            />
            <QuerySection query={query} subject="the execution">
                {/* Keyed: live state belongs to one execution. */}
                {(e) => <ExecutionView key={e.id} execution={e} />}
            </QuerySection>
        </>
    )
}
