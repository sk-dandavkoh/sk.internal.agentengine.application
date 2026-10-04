import { useQuery } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { useParams, useSearchParams } from 'react-router'
import { useApiClient } from '../../api/context'
import { ApiError } from '../../api/errors'
import type { Execution } from '../../api/executions'
import { executionQueries } from '../../api/queries'
import type { ExecutionReport } from '../../api/reports'
import { QuerySection } from '../../components/QuerySection'
import {
    Button,
    Card,
    ErrorState,
    PageHeader,
    Tabs,
    TextLink,
    type TabItem,
} from '../../components/ui'
import { FailureDetails } from '../dashboard/FailureDetails'
import { CancelExecution } from './CancelExecution'
import { isFailure, reportPath } from './format'
import { Mono } from './inspector/display'
import { GraphPanel, NodesPanel } from './inspector/FlowPanels'
import { ErrorsPanel, EventsPanel, LogsPanel } from './inspector/HistoryPanels'
import { OverviewPanel } from './inspector/OverviewPanel'
import { LiveActivity, LiveIndicator } from './live'
import { useLiveExecution } from './useLiveExecution'

const TABS = ['overview', 'graph', 'nodes', 'logs', 'errors', 'events'] as const
type Tab = (typeof TABS)[number]
const isTab = (v: string | null): v is Tab =>
    (TABS as readonly (string | null)[]).includes(v)

function ExecutionView({ execution }: { execution: Execution }) {
    const report = useQuery(
        executionQueries.report(useApiClient(), execution.id),
    )
    // Only unfinished runs stream; a finished one has nothing left to say.
    const live = useLiveExecution(execution.id, execution.finished_at === null)
    // Tab and node live in the URL: an inspected node is a shareable link.
    const [params, setParams] = useSearchParams()
    const tabParam = params.get('tab')
    const tab: Tab = isTab(tabParam) ? tabParam : 'overview'
    const node = params.get('node') ?? undefined
    const show = (next: Tab, nodeId = node) => {
        const p = new URLSearchParams()
        if (next !== 'overview') p.set('tab', next)
        if (nodeId) p.set('node', nodeId)
        setParams(p, { replace: true })
    }
    const inspect = (nodeId: string) => {
        show('nodes', nodeId)
    }

    const withReport = (
        subject: string,
        render: (r: ExecutionReport) => ReactNode,
    ) => (
        <QuerySection query={report} subject={subject}>
            {render}
        </QuerySection>
    )
    const failures =
        report.data?.nodes.reduce((n, x) => n + x.failures.length, 0) ?? 0
    const items: TabItem[] = [
        {
            id: 'overview',
            label: 'Overview',
            content: (
                <div className="flex flex-col gap-6">
                    <OverviewPanel execution={execution} report={report} />
                    {live.state !== 'idle' && (
                        <Card
                            title="Live activity"
                            description="Events received while this page is open."
                        >
                            <LiveActivity events={live.events} />
                        </Card>
                    )}
                </div>
            ),
        },
        {
            id: 'graph',
            label: 'Graph',
            content: withReport('the graph', (r) => (
                <GraphPanel
                    execution={execution}
                    report={r}
                    onInspect={inspect}
                />
            )),
        },
        {
            id: 'nodes',
            label: 'Nodes',
            content: withReport('the nodes', (r) => (
                <NodesPanel
                    executionId={execution.id}
                    report={r}
                    selected={node}
                    onInspect={inspect}
                />
            )),
        },
        {
            id: 'logs',
            label: 'Logs',
            content: withReport('the logs', (r) => (
                <LogsPanel executionId={execution.id} report={r} />
            )),
        },
        {
            id: 'errors',
            // The count is text: no color-only signal.
            label: failures > 0 ? `Errors (${String(failures)})` : 'Errors',
            content: withReport('the errors', (r) => (
                <ErrorsPanel report={r} onInspect={inspect} />
            )),
        },
        {
            id: 'events',
            label: 'Events',
            content: withReport('the events', (r) => (
                <EventsPanel report={r} />
            )),
        },
    ]
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
            <Tabs
                label="Execution inspector"
                items={items}
                value={tab}
                onValueChange={(id) => {
                    if (isTab(id)) show(id)
                }}
            />
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
                        <TextLink to={reportPath(executionId)}>Report</TextLink>{' '}
                        · <TextLink to="/executions">All executions</TextLink>
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
