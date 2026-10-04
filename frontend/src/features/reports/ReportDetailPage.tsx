import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { useNavigate, useParams } from 'react-router'
import { useApiClient } from '../../api/context'
import { ApiError, errorMessage } from '../../api/errors'
import { executionQueries } from '../../api/queries'
import {
    exportExecutionReport,
    type ExecutionReport,
    type NodeExecution,
} from '../../api/reports'
import { QuerySection } from '../../components/QuerySection'
import {
    Button,
    Card,
    ErrorState,
    PageHeader,
    Table,
    TextLink,
    useToast,
    type TableColumn,
} from '../../components/ui'
import { executionPath } from '../executions/format'
import { ErrorsPanel, EventsPanel } from '../executions/inspector/HistoryPanels'
import {
    Item,
    JsonBlock,
    Mono,
    NodeStatusBadge,
    None,
} from '../executions/inspector/display'
import { ms } from '../executions/inspector/format'
import { RunSnapshot, Usage } from '../executions/inspector/OverviewPanel'
import { StatusBadge, Time } from '../executions/shared'
import { VersionLink } from '../versions/shared'

// Long enough for the browser to start reading the blob; a revoke right
// after click() can cancel the download in some browsers.
const REVOKE_AFTER_MS = 40_000

function saveFile(name: string, body: string) {
    const url = URL.createObjectURL(
        new Blob([body], { type: 'application/json' }),
    )
    const link = document.createElement('a')
    link.href = url
    link.download = name
    link.click()
    setTimeout(() => {
        URL.revokeObjectURL(url)
    }, REVOKE_AFTER_MS)
}

/** The backend's canonical export, saved unchanged (never re-serialized). */
function DownloadReport({ executionId }: { executionId: string }) {
    const client = useApiClient()
    const toast = useToast()
    const [busy, setBusy] = useState(false)
    const download = async () => {
        setBusy(true)
        try {
            const body = await exportExecutionReport(client, executionId)
            // The id is server data: keep the file name to safe characters.
            const safe = executionId.replace(/[^\w-]/g, '_')
            saveFile(`execution-report-${safe}.json`, body)
        } catch (error) {
            toast.notify({
                tone: 'danger',
                title: 'Could not download the report',
                description: errorMessage(error),
            })
        } finally {
            setBusy(false)
        }
    }
    return (
        <Button loading={busy} onClick={() => void download()}>
            Download JSON
        </Button>
    )
}

function Summary({ report: r }: { report: ExecutionReport }) {
    const e = r.execution
    return (
        <Card title="Summary">
            <dl className="grid gap-4 text-sm sm:grid-cols-2 lg:grid-cols-3">
                <Item term="Status">
                    <StatusBadge status={e.status} />
                </Item>
                <Item term="Project">
                    <TextLink
                        to={`/projects/${encodeURIComponent(r.project.id)}`}
                    >
                        {r.project.name ?? r.project.id}
                    </TextLink>
                    {r.project.name === null && (
                        <span className="block text-muted">
                            The project no longer exists.
                        </span>
                    )}
                </Item>
                <Item term="Version">
                    <VersionLink
                        projectId={r.project.id}
                        version={r.project.version}
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
                {/* The backend's duration: none while a run is unfinished. */}
                <Item term="Duration">{ms(e.duration_ms)}</Item>
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

const nodeColumns: TableColumn<NodeExecution>[] = [
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
    {
        key: 'status',
        header: 'Status',
        cell: (n) => <NodeStatusBadge status={n.status} />,
    },
    {
        key: 'attempts',
        header: 'Attempts',
        align: 'end',
        cell: (n) => String(n.attempts),
    },
    {
        key: 'duration',
        header: 'Duration',
        align: 'end',
        cell: (n) => (
            <span className="whitespace-nowrap">{ms(n.duration_ms)}</span>
        ),
    },
    {
        key: 'tokens',
        header: 'Tokens',
        align: 'end',
        cell: (n) => (n.usage ? n.usage.total_tokens.toLocaleString() : '—'),
    },
    {
        key: 'cost',
        header: 'Cost',
        align: 'end',
        cell: (n) =>
            n.cost
                ? `${n.cost.amount.toLocaleString(undefined, { maximumFractionDigits: 6 })} ${n.cost.currency}`
                : '—',
    },
]

function Nodes({ report }: { report: ExecutionReport }) {
    const withOutputs = report.nodes.filter((n) => n.outputs !== null)
    return (
        <Card title="Nodes" description="Ordered by node id.">
            <div className="flex flex-col gap-4 text-sm">
                <Table
                    caption="Nodes of this execution"
                    hideCaption
                    columns={nodeColumns}
                    rows={report.nodes}
                    rowKey={(n) => n.node_id}
                    empty="This execution has no node executions."
                />
                {withOutputs.length > 0 && (
                    <section aria-label="Node outputs">
                        <h3 className="mb-2 font-semibold">Outputs</h3>
                        {withOutputs.map((n) => (
                            // Native disclosure: large outputs render only
                            // on demand and stay keyboard-operable.
                            <details key={n.node_id} className="mb-2">
                                <summary className="focus-ring cursor-pointer rounded-sm">
                                    {n.label || n.node_id}
                                </summary>
                                <div className="mt-2">
                                    <JsonBlock value={n.outputs} />
                                </div>
                            </details>
                        ))}
                    </section>
                )}
            </div>
        </Card>
    )
}

function ReportView({ report }: { report: ExecutionReport }) {
    const navigate = useNavigate()
    const inspect = (nodeId: string) => {
        void navigate(
            `${executionPath(report.execution_id)}?tab=nodes&node=${encodeURIComponent(nodeId)}`,
        )
    }
    return (
        <div className="flex flex-col gap-6">
            <Summary report={report} />
            <Card
                title="Usage"
                description="Token usage and a list-price cost estimate, never billing data."
            >
                <Usage report={report} />
            </Card>
            <Nodes report={report} />
            <ErrorsPanel report={report} onInspect={inspect} />
            <Card
                title="Run snapshot"
                description="What this execution ran with. Inputs are redacted by the backend."
            >
                <RunSnapshot report={report} />
            </Card>
            <EventsPanel report={report} />
            <p className="text-sm text-muted">
                Node logs are not part of the report:{' '}
                <TextLink to={`${executionPath(report.execution_id)}?tab=logs`}>
                    view them in the execution inspector
                </TextLink>
                .
            </p>
        </div>
    )
}

export function ReportDetailPage() {
    const { executionId = '' } = useParams()
    const query = useQuery(executionQueries.report(useApiClient(), executionId))
    if (
        query.data === undefined &&
        query.error instanceof ApiError &&
        query.error.kind === 'not_found'
    )
        return (
            <ErrorState
                title="Report not found"
                titleAs="h1"
                description="The execution does not exist or you do not have access to it."
                action={<TextLink to="/reports">All reports</TextLink>}
            />
        )
    return (
        <>
            <PageHeader
                title="Execution report"
                description={
                    <>
                        <Mono>{executionId}</Mono> ·{' '}
                        <TextLink to={executionPath(executionId)}>
                            Open in inspector
                        </TextLink>{' '}
                        · <TextLink to="/reports">All reports</TextLink>
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
                        <DownloadReport executionId={executionId} />
                    </>
                }
            />
            <QuerySection query={query} subject="the report">
                {(r) => <ReportView report={r} />}
            </QuerySection>
        </>
    )
}
