import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useState, type SubmitEvent } from 'react'
import { useNavigate, useSearchParams } from 'react-router'
import { useApiClient } from '../../api/context'
import { useCreateProject } from '../../api/mutations'
import {
    projectSortSchema,
    projectStatusSchema,
    type ProjectListParams,
    type ProjectSummary,
} from '../../api/projects'
import { projectQueries } from '../../api/queries'
import { QuerySection } from '../../components/QuerySection'
import {
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
import { formatDateTime } from '../../lib/format'
import { ProjectFormDialog } from './ProjectFormDialog'
import { ProjectStatusBadge } from './ProjectStatusBadge'
import { projectStatusLabels } from './status'

const PAGE_SIZE = 25

const sortLabels = {
    '-updated_at': 'Recently updated',
    updated_at: 'Least recently updated',
    '-created_at': 'Newest first',
    created_at: 'Oldest first',
    name: 'Name (A–Z)',
    '-name': 'Name (Z–A)',
} as const satisfies Record<(typeof projectSortSchema.options)[number], string>

const DEFAULT_SORT = '-updated_at'

/** List state lives in the URL: shareable, and Back restores it. */
function useListParams() {
    const [search, setSearch] = useSearchParams()
    const status = projectStatusSchema.safeParse(search.get('status'))
    const sort = projectSortSchema.safeParse(search.get('sort'))
    const offset = Number(search.get('offset'))
    const params: ProjectListParams = {
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

const columns: TableColumn<ProjectSummary>[] = [
    {
        key: 'name',
        header: 'Name',
        cell: (p) => (
            <div className="flex flex-col">
                <TextLink to={`/projects/${encodeURIComponent(p.id)}`}>
                    {p.name}
                </TextLink>
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
        cell: (p) => <ProjectStatusBadge status={p.status} />,
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
                <option value="">All statuses</option>
                {projectStatusSchema.options.map((s) => (
                    <option key={s} value={s}>
                        {projectStatusLabels[s]}
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

function CreateProjectDialog({
    open,
    onClose,
}: {
    open: boolean
    onClose: () => void
}) {
    const create = useCreateProject()
    const navigate = useNavigate()
    const toast = useToast()
    const close = () => {
        create.reset()
        onClose()
    }
    return (
        <ProjectFormDialog
            open={open}
            onClose={close}
            title="New project"
            submitLabel="Create project"
            initial={{ name: '', description: '' }}
            pending={create.isPending}
            error={create.error}
            onSubmit={(values) => {
                create.mutate(values, {
                    onSuccess: (project) => {
                        toast.notify({
                            tone: 'success',
                            title: `Project “${project.name}” created`,
                        })
                        void navigate(
                            `/projects/${encodeURIComponent(project.id)}`,
                        )
                    },
                })
            }}
        />
    )
}

export function ProjectsPage() {
    const client = useApiClient()
    const { params, update, filtered } = useListParams()
    // Keep the current rows while the next page or filter loads, instead
    // of flashing a spinner on every change.
    const query = useQuery({
        ...projectQueries.list(client, params),
        placeholderData: keepPreviousData,
    })
    const [creating, setCreating] = useState(false)
    const offset = params.offset ?? 0
    const newProject = (
        <Button
            variant="primary"
            onClick={() => {
                setCreating(true)
            }}
        >
            New project
        </Button>
    )

    return (
        <>
            <PageHeader
                title="Projects"
                description="Create, find and open the projects you can access."
                actions={newProject}
            />
            <Filters params={params} update={update} />
            <QuerySection query={query} subject="projects">
                {({ items, page }) =>
                    items.length === 0 && offset === 0 ? (
                        filtered ? (
                            <EmptyState
                                title="No matching projects"
                                description="No project matches the search and status filter."
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
                                title="No projects yet"
                                description="A project holds a workflow you build, publish and run."
                                action={newProject}
                            />
                        )
                    ) : (
                        <div aria-busy={query.isPlaceholderData || undefined}>
                            <Table
                                caption="Projects"
                                hideCaption
                                columns={columns}
                                rows={items}
                                rowKey={(p) => p.id}
                                empty="No projects on this page."
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
            <CreateProjectDialog
                open={creating}
                onClose={() => {
                    setCreating(false)
                }}
            />
        </>
    )
}
