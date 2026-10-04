import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useSearchParams } from 'react-router'
import { useApiClient } from '../../api/context'
import type { MonitoringWindow } from '../../api/monitoring'
import { monitoringQueries, queryKeys } from '../../api/queries'
import { QuerySection } from '../../components/QuerySection'
import { Button, EmptyState, PageHeader } from '../../components/ui'
import { formatDateTime } from '../../lib/format'
import { SystemStatusCard } from '../dashboard/sections'
import {
    customWindow,
    DEFAULT_RANGE,
    isRange,
    presetWindow,
    type Range,
} from './model'
import { RangePicker } from './RangePicker'
import {
    ActiveNowCard,
    DurationsCard,
    ErrorsCard,
    NodePerformanceCard,
    StatsGrid,
    TimelineCard,
} from './sections'

/** The range lives in the URL (shareable, survives reload); a preset is
 * anchored at `now`, which only Refresh and a range change move. */
function useWindow() {
    const [params, setParams] = useSearchParams()
    const [now, setNow] = useState(() => Date.now())
    const param = params.get('range')
    const range: Range = isRange(param) ? param : DEFAULT_RANGE
    let window: MonitoringWindow | null
    if (range === 'custom') {
        const custom = customWindow(
            params.get('since') ?? '',
            params.get('until') ?? '',
        )
        window = 'window' in custom ? custom.window : null
    } else {
        window = presetWindow(range, now)
    }
    return {
        range,
        window,
        setRange: (next: Range) => {
            setNow(Date.now())
            setParams(
                next === 'custom' && window
                    ? { range: next, ...window }
                    : { range: next },
                { replace: true },
            )
        },
        setCustom: (next: MonitoringWindow) => {
            setParams({ range: 'custom', ...next }, { replace: true })
        },
        moveToNow: () => {
            setNow(Date.now())
        },
    }
}

export function MonitoringPage() {
    const { range, window, setRange, setCustom, moveToNow } = useWindow()
    const queryClient = useQueryClient()
    const refresh = () => {
        if (range === 'custom')
            void queryClient.refetchQueries({ queryKey: ['monitoring'] })
        else moveToNow() // a new window: fetched as a new query
        void queryClient.refetchQueries({
            queryKey: queryKeys.executions.statusCounts(),
        })
        void queryClient.refetchQueries({ queryKey: queryKeys.system() })
    }

    return (
        <>
            <PageHeader
                title="Monitoring"
                description="How executions performed, from the engine's persisted history."
                actions={<Button onClick={refresh}>Refresh</Button>}
            />
            <div className="mb-6">
                <RangePicker
                    range={range}
                    current={window}
                    onRange={setRange}
                    onCustom={setCustom}
                />
            </div>
            <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-3">
                <div className="flex flex-col gap-6 lg:col-span-2">
                    {window ? (
                        <WindowSummary window={window} />
                    ) : (
                        <EmptyState
                            title="Choose a time range"
                            description="Enter a start and an end, at most 31 days apart, and apply them."
                        />
                    )}
                </div>
                <div className="flex flex-col gap-6">
                    <ActiveNowCard />
                    <SystemStatusCard />
                </div>
            </div>
        </>
    )
}

function WindowSummary({ window }: { window: MonitoringWindow }) {
    const query = useQuery(monitoringQueries.summary(useApiClient(), window))
    return (
        <section
            aria-labelledby="window-heading"
            className="flex flex-col gap-6"
        >
            <h2 id="window-heading" className="text-sm text-muted">
                Executions created from {formatDateTime(window.since)} to{' '}
                {formatDateTime(window.until)}
            </h2>
            <QuerySection query={query} subject="monitoring data">
                {(summary) =>
                    summary.executions === 0 ? (
                        <EmptyState
                            title="No executions in this time range"
                            description="Runs created in this window will appear here. Try a longer range."
                        />
                    ) : (
                        <>
                            <StatsGrid summary={summary} />
                            <TimelineCard summary={summary} />
                            <DurationsCard summary={summary} />
                            <ErrorsCard summary={summary} />
                            <NodePerformanceCard summary={summary} />
                        </>
                    )
                }
            </QuerySection>
        </section>
    )
}
