import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useSearchParams } from 'react-router'
import { useApiClient } from '../../api/context'
import {
    executionStatusSchema,
    type ExecutionListItem,
    type ExecutionStatus,
} from '../../api/executions'
import { executionQueries } from '../../api/queries'
import { QuerySection } from '../../components/QuerySection'
import {
    Button,
    Card,
    EmptyState,
    PageHeader,
    Select,
    TextLink,
    type TableColumn,
} from '../../components/ui'
import { executionStatusDisplay } from '../dashboard/format'
import { CancelExecution } from './CancelExecution'
import { ExecutionTable, Time } from './shared'

const PAGE_SIZE = 25

const extraColumns: TableColumn<ExecutionListItem>[] = [
    {
        key: 'started',
        header: 'Started',
        cell: (e) => <Time iso={e.started_at} />,
    },
    {
        key: 'finished',
        header: 'Finished',
        cell: (e) => <Time iso={e.finished_at} />,
    },
    {
        key: 'actions',
        header: <span className="sr-only">Actions</span>,
        align: 'end',
        cell: (e) => <CancelExecution execution={e} size="sm" />,
    },
]

/** Filter and page live in the URL, so back from a detail page restores them. */
function useListParams() {
    const [search, setSearch] = useSearchParams()
    const parsed = executionStatusSchema.safeParse(search.get('status'))
    const status: ExecutionStatus | null = parsed.success ? parsed.data : null
    const offset = Math.max(0, Math.trunc(Number(search.get('offset'))) || 0)
    const update = (next: {
        status?: ExecutionStatus | null
        offset: number
    }) => {
        const params = new URLSearchParams()
        const s = next.status === undefined ? status : next.status
        if (s) params.set('status', s)
        if (next.offset > 0) params.set('offset', String(next.offset))
        setSearch(params)
    }
    return { status, offset, update }
}

export function ExecutionsPage() {
    const { status, offset, update } = useListParams()
    const query = useQuery({
        ...executionQueries.list(useApiClient(), {
            ...(status ? { status: [status] } : {}),
            offset,
            limit: PAGE_SIZE,
        }),
        placeholderData: keepPreviousData,
    })
    return (
        <>
            <PageHeader
                title="Executions"
                description="Runs across your projects, newest first. Each one is pinned to a published version."
                actions={
                    <Button
                        loading={query.isFetching}
                        onClick={() => void query.refetch()}
                    >
                        Refresh
                    </Button>
                }
            />
            <Card>
                <div className="mb-4 max-w-xs">
                    <Select
                        label="Status"
                        value={status ?? ''}
                        onChange={(event) => {
                            const parsed = executionStatusSchema.safeParse(
                                event.target.value,
                            )
                            update({
                                status: parsed.success ? parsed.data : null,
                                offset: 0,
                            })
                        }}
                    >
                        <option value="">All statuses</option>
                        {executionStatusSchema.options.map((s) => (
                            <option key={s} value={s}>
                                {executionStatusDisplay[s].label}
                            </option>
                        ))}
                    </Select>
                </div>
                <QuerySection query={query} subject="executions">
                    {({ items, page }) =>
                        items.length === 0 && offset === 0 ? (
                            status ? (
                                <EmptyState
                                    title="No matching executions"
                                    titleAs="h2"
                                    description={`No execution is ${executionStatusDisplay[status].label.toLowerCase()}.`}
                                />
                            ) : (
                                <EmptyState
                                    title="No executions yet"
                                    titleAs="h2"
                                    description="Run a published version of a project to start one."
                                    action={
                                        <TextLink to="/projects">
                                            Go to projects
                                        </TextLink>
                                    }
                                />
                            )
                        ) : (
                            <div
                                aria-busy={query.isPlaceholderData || undefined}
                            >
                                <ExecutionTable
                                    caption="Executions"
                                    executions={items}
                                    errorDetails
                                    extraColumns={extraColumns}
                                />
                                <nav
                                    aria-label="Execution pages"
                                    className="mt-3 flex justify-end gap-2"
                                >
                                    <Button
                                        size="sm"
                                        disabled={offset === 0}
                                        onClick={() => {
                                            update({
                                                offset: Math.max(
                                                    0,
                                                    offset - PAGE_SIZE,
                                                ),
                                            })
                                        }}
                                    >
                                        Previous
                                    </Button>
                                    <Button
                                        size="sm"
                                        disabled={!page.has_more}
                                        onClick={() => {
                                            update({
                                                offset: offset + PAGE_SIZE,
                                            })
                                        }}
                                    >
                                        Next
                                    </Button>
                                </nav>
                            </div>
                        )
                    }
                </QuerySection>
            </Card>
        </>
    )
}
