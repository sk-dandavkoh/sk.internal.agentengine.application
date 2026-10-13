import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useState, type SubmitEvent } from 'react'
import { useNavigate, useSearchParams } from 'react-router'
import { useApiClient } from '../../api/context'
import { useCreateWorkflow } from '../../api/mutations'
import {
    workflowSortSchema,
    workflowStatusSchema,
    type WorkflowListParams,
    type WorkflowSummary,
} from '../../api/workflows'
import { workflowQueries } from '../../api/queries'
import { QuerySection } from '../../components/QuerySection'
import {
    Badge,
    Button,
    EmptyState,
    Input,
    PageHeader,
    Select,
    Table,
    TextLink,
    useToast,
    type TableColumn,
} from '../../components/ui'
import { formText } from '../../lib/form'
import { useSession } from '../auth/session'
import { formatDateTime } from '../../lib/format'
import { ImportWorkflowDialog } from './ImportWorkflowDialog'
import { WorkflowFormDialog } from './WorkflowFormDialog'
import { WorkflowStatusBadge } from './WorkflowStatusBadge'
import { workflowStatusLabels } from './status'

const PAGE_SIZE = 25

const sortLabels = {
    '-updated_at': 'Recently updated',
    updated_at: 'Least recently updated',
    '-created_at': 'Newest first',
    created_at: 'Oldest first',
    name: 'Name (A–Z)',
    '-name': 'Name (Z–A)',
} as const satisfies Record<(typeof workflowSortSchema.options)[number], string>

const DEFAULT_SORT = '-updated_at'

/** List state lives in the URL: shareable, and Back restores it. */
function useListParams() {
    const [search, setSearch] = useSearchParams()
    const status = workflowStatusSchema.safeParse(search.get('status'))
    const sort = workflowSortSchema.safeParse(search.get('sort'))
    const offset = Number(search.get('offset'))
    const params: WorkflowListParams = {
        q: search.get('q') ?? '',
        sort: sort.success ? sort.data : DEFAULT_SORT,
        limit: PAGE_SIZE,
        offset: Number.isInteger(offset) && offset > 0 ? offset : 0,
        ...(status.success && { status: [status.data] }),
    }
    const update = (changes: Record<string, string>) => {
        setSearch((current) => {
            const next = new URLSearchParams(current)
            // Any filter change starts again at the first page.
            if (!('offset' in changes)) next.delete('offset')
            for (const [key, value] of Object.entries(changes))
                if (value) next.set(key, value)
                else next.delete(key)
            return next
        })
    }
    return {
        params,
        update,
        filtered: Boolean(params.q) || params.status !== undefined,
    }
}

/** Marks workflows someone else owns that are shared with the caller. */
function SharedBadge({ workflow }: { workflow: WorkflowSummary }) {
    const { data } = useSession()
    const me = data?.status === 'active' ? data.session.user : undefined
    // Admins see every workflow; "shared" would mislabel the rest.
    if (!me || me.role === 'admin' || workflow.owner_id === me.id) return null
    return <Badge tone="info">Shared with you</Badge>
}

const columns: TableColumn<WorkflowSummary>[] = [
    {
        key: 'name',
        header: 'Name',
        cell: (p) => (
            <div className="flex flex-col">
                <span className="flex flex-wrap items-center gap-2">
                    <TextLink to={`/workflows/${encodeURIComponent(p.id)}`}>
                        {p.name}
                    </TextLink>
                    <SharedBadge workflow={p} />
                </span>
                {p.description && (
                    <span className="line-clamp-1 text-muted">
                        {p.description}
                    </span>
                )}
            </div>
        ),
    },
    {
        key: 'status',
        header: 'Status',
        cell: (p) => <WorkflowStatusBadge status={p.status} />,
    },
    {
        key: 'version',
        header: 'Latest version',
        align: 'end',
        cell: (p) =>
            p.current_version > 0 ? `v${String(p.current_version)}` : '—',
    },
    {
        key: 'updated',
        header: 'Updated',
        cell: (p) => (
            <time dateTime={p.updated_at} className="whitespace-nowrap">
                {formatDateTime(p.updated_at)}
            </time>
        ),
    },
]

function Filters({
    params,
    update,
}: Pick<ReturnType<typeof useListParams>, 'params' | 'update'>) {
    const submit = (event: SubmitEvent<HTMLFormElement>) => {
        event.preventDefault()
        update({ q: formText(new FormData(event.currentTarget), 'q') })
    }
    return (
        <div className="mb-4 flex flex-wrap items-end gap-3">
            <form
                role="search"
                onSubmit={submit}
                // Remount on a URL change (Back, Clear filters) so the
                // field shows the search that is actually applied.
                key={params.q}
                className="flex items-end gap-2"
            >
                <Input
                    label="Search by name"
                    name="q"
                    type="search"
                    defaultValue={params.q}
                    maxLength={200}
                />
                <Button type="submit">Search</Button>
            </form>
            <Select
                label="Status"
                value={params.status?.[0] ?? ''}
                onChange={(e) => {
                    update({ status: e.target.value })
                }}
            >
                <option value="">All except archived</option>
                {workflowStatusSchema.options.map((s) => (
                    <option key={s} value={s}>
                        {workflowStatusLabels[s]}
                    </option>
                ))}
            </Select>
            <Select
                label="Sort by"
                value={params.sort}
                onChange={(e) => {
                    update({ sort: e.target.value })
                }}
            >
                {Object.entries(sortLabels).map(([value, label]) => (
                    <option key={value} value={value}>
                        {label}
                    </option>
                ))}
            </Select>
        </div>
    )
}

function CreateWorkflowDialog({
    open,
    onClose,
}: {
    open: boolean
    onClose: () => void
}) {
    const create = useCreateWorkflow()
    const navigate = useNavigate()
    const toast = useToast()
    const close = () => {
        create.reset()
        onClose()
    }
    return (
        <WorkflowFormDialog
            open={open}
            onClose={close}
            title="New workflow"
            submitLabel="Create workflow"
            initial={{ name: '', description: '' }}
            pending={create.isPending}
            error={create.error}
            onSubmit={(values) => {
                create.mutate(values, {
                    onSuccess: (workflow) => {
                        toast.notify({
                            tone: 'success',
                            title: `Workflow “${workflow.name}” created`,
                        })
                        void navigate(
                            `/workflows/${encodeURIComponent(workflow.id)}`,
                        )
                    },
                })
            }}
        />
    )
}

export function WorkflowsPage() {
    const client = useApiClient()
    const { params, update, filtered } = useListParams()
    // Keep the current rows while the next page or filter loads, instead
    // of flashing a spinner on every change.
    const query = useQuery({
        ...workflowQueries.list(client, params),
        placeholderData: keepPreviousData,
    })
    const [creating, setCreating] = useState(false)
    const [importing, setImporting] = useState(false)
    const offset = params.offset ?? 0
    const newWorkflow = (
        <Button
            variant="primary"
            onClick={() => {
                setCreating(true)
            }}
        >
            New workflow
        </Button>
    )

    return (
        <>
            <PageHeader
                title="Workflows"
                description="Create, find and open the workflows you can access."
                actions={
                    <>
                        <Button
                            onClick={() => {
                                setImporting(true)
                            }}
                        >
                            Import workflow
                        </Button>
                        {newWorkflow}
                    </>
                }
            />
            <Filters params={params} update={update} />
            <QuerySection query={query} subject="workflows">
                {({ items, page }) =>
                    items.length === 0 && offset === 0 ? (
                        filtered ? (
                            <EmptyState
                                title="No matching workflows"
                                description="No workflow matches the search and status filter."
                                action={
                                    <Button
                                        onClick={() => {
                                            update({ q: '', status: '' })
                                        }}
                                    >
                                        Clear filters
                                    </Button>
                                }
                            />
                        ) : (
                            <EmptyState
                                title="No workflows yet"
                                description="Build, publish and run a workflow."
                                action={newWorkflow}
                            />
                        )
                    ) : (
                        <div aria-busy={query.isPlaceholderData || undefined}>
                            <Table
                                caption="Workflows"
                                hideCaption
                                columns={columns}
                                rows={items}
                                rowKey={(p) => p.id}
                                empty="No workflows on this page."
                            />
                            <nav
                                aria-label="Pagination"
                                className="mt-3 flex items-center justify-end gap-2"
                            >
                                <Button
                                    size="sm"
                                    disabled={offset === 0}
                                    onClick={() => {
                                        update({
                                            offset: String(
                                                Math.max(0, offset - PAGE_SIZE),
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
                                            offset: String(offset + PAGE_SIZE),
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
            <CreateWorkflowDialog
                open={creating}
                onClose={() => {
                    setCreating(false)
                }}
            />
            {importing && (
                <ImportWorkflowDialog
                    onClose={() => {
                        setImporting(false)
                    }}
                />
            )}
        </>
    )
}
