import {
    useIsFetching,
    useQueries,
    useQueryClient,
} from '@tanstack/react-query'
import { useApiClient } from '../../api/context'
import { errorMessage } from '../../api/errors'
import {
    executionQueries,
    workflowQueries,
    systemQueries,
} from '../../api/queries'
import { Button, ErrorState, PageHeader } from '../../components/ui'
import {
    activeExecutions,
    WORKFLOW_SAMPLE,
    recentExecutions,
    recentFailures,
} from './format'
import {
    ExecutionCountsCard,
    ExecutionListCard,
    WorkflowsCard,
    SystemStatusCard,
} from './sections'

const LIST = 'lg:col-span-2 lg:col-start-1'

/**
 * Every dashboard query failed with nothing to show: the backend is down or
 * the session is gone. One page-level error then replaces six identical
 * section errors. The queries are the sections' own (same keys, deduped).
 */
function useOutage() {
    const client = useApiClient()
    return useQueries({
        queries: [
            executionQueries.statusCounts(client),
            executionQueries.list(client, activeExecutions),
            executionQueries.list(client, recentFailures),
            executionQueries.list(client, recentExecutions),
            systemQueries.status(client),
            workflowQueries.list(client, WORKFLOW_SAMPLE),
        ],
        combine: (results) =>
            results.every((r) => r.isError && r.data === undefined)
                ? { error: results[0].error }
                : null,
    })
}

// Each section loads, fails and retries on its own: one unavailable
// endpoint must not blank the whole overview.
export function DashboardPage() {
    const queryClient = useQueryClient()
    const isFetching = useIsFetching() > 0
    const outage = useOutage()
    const refetch = () =>
        // Only what is on screen: the dashboard's queries.
        void queryClient.refetchQueries({ type: 'active' })

    return (
        <>
            <PageHeader
                title="Dashboard"
                description="Operational overview of the engine."
                actions={
                    <Button loading={isFetching} onClick={refetch}>
                        Refresh
                    </Button>
                }
            />
            {outage ? (
                <ErrorState
                    title="Could not load the dashboard"
                    description={errorMessage(outage.error)}
                    action={
                        <Button loading={isFetching} onClick={refetch}>
                            Retry
                        </Button>
                    }
                />
            ) : (
                <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-3">
                    {/* DOM order is reading order on every width: on narrow screens
                    the system status follows the outcomes instead of trailing
                    every list; wide screens place it and the workflows in a
                    side column. */}
                    <ExecutionCountsCard className="lg:col-span-2" />
                    <SystemStatusCard />
                    <ExecutionListCard
                        title="Active executions"
                        description="Created, queued, running, paused or waiting."
                        params={activeExecutions}
                        emptyTitle="No active executions"
                        emptyDescription="Nothing is running or waiting right now."
                        className={LIST}
                    />
                    <ExecutionListCard
                        title="Recent failures"
                        description="The newest failed or timed-out executions. Expand one to see its errors."
                        params={recentFailures}
                        emptyTitle="No failed executions"
                        errorDetails
                        className={LIST}
                    />
                    <ExecutionListCard
                        title="Recent executions"
                        description="The newest executions of every status."
                        params={recentExecutions}
                        emptyTitle="No executions yet"
                        emptyDescription="Runs of published workflows appear here."
                        className={LIST}
                    />
                    <WorkflowsCard className="lg:col-start-3 lg:row-span-3 lg:row-start-2" />
                </div>
            )}
        </>
    )
}
