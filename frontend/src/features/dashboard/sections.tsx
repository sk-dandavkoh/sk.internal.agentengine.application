import { useQuery } from '@tanstack/react-query'
import { useApiClient } from '../../api/context'
import type { ExecutionListParams, ExecutionStatus } from '../../api/executions'
import { projectStatusSchema, type ProjectStatus } from '../../api/projects'
import {
    executionQueries,
    projectQueries,
    systemQueries,
} from '../../api/queries'
import { Badge, Card, EmptyState, TextLink } from '../../components/ui'
import { ACTIVE_STATUSES, FAILURE_STATUSES, PROJECT_SAMPLE } from './format'
import { projectStatusLabels } from '../projects/status'
import { ExecutionTable } from '../executions/shared'
import { QuerySection } from '../../components/QuerySection'

function useProjects() {
    return useQuery(projectQueries.list(useApiClient(), PROJECT_SAMPLE))
}

function ViewAll({ to, label }: { to: string; label: string }) {
    return (
        <TextLink to={to} className="text-sm">
            {label}
        </TextLink>
    )
}

interface ExecutionListCardProps {
    title: string
    description: string
    params: ExecutionListParams
    emptyTitle: string
    emptyDescription?: string
    errorDetails?: boolean
    className?: string
}

export function ExecutionListCard({
    title,
    description,
    params,
    emptyTitle,
    emptyDescription,
    errorDetails = false,
    className,
}: ExecutionListCardProps) {
    const query = useQuery(executionQueries.list(useApiClient(), params))
    return (
        <Card
            title={title}
            description={description}
            actions={<ViewAll to="/executions" label="All executions" />}
            className={className}
        >
            <QuerySection query={query} subject={title.toLowerCase()}>
                {(page) =>
                    page.items.length === 0 ? (
                        <EmptyState
                            title={emptyTitle}
                            titleAs="h3"
                            description={emptyDescription}
                        />
                    ) : (
                        <>
                            <ExecutionTable
                                caption={title}
                                executions={page.items}
                                errorDetails={errorDetails}
                            />
                            {page.page.has_more && (
                                <p className="mt-2 text-sm text-muted">
                                    Showing the newest{' '}
                                    {String(page.items.length)}.
                                </p>
                            )}
                        </>
                    )
                }
            </QuerySection>
        </Card>
    )
}

const groups: {
    label: string
    statuses: readonly ExecutionStatus[]
}[] = [
    { label: 'Active', statuses: ACTIVE_STATUSES },
    { label: 'Completed', statuses: ['completed'] },
    { label: 'Failed', statuses: FAILURE_STATUSES },
    { label: 'Cancelled', statuses: ['cancelled'] },
]

export function ExecutionCountsCard({ className }: { className?: string }) {
    const query = useQuery(executionQueries.statusCounts(useApiClient()))
    return (
        <Card
            title="Execution outcomes"
            description="All executions you can see, by status."
            className={className}
        >
            <QuerySection query={query} subject="execution counts">
                {({ counts }) => {
                    const total = Object.values(counts).reduce(
                        (a, b) => a + b,
                        0,
                    )
                    if (total === 0)
                        return (
                            <EmptyState
                                title="No executions yet"
                                titleAs="h3"
                                description="Runs of published projects appear here."
                            />
                        )
                    return (
                        <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                            {groups.map(({ label, statuses }) => (
                                <div
                                    key={label}
                                    className="rounded-md border border-border p-3"
                                >
                                    <dt className="text-sm text-muted">
                                        {label}
                                    </dt>
                                    <dd className="text-2xl font-semibold tabular-nums">
                                        {String(
                                            statuses.reduce(
                                                (sum, s) => sum + counts[s],
                                                0,
                                            ),
                                        )}
                                    </dd>
                                    {label === 'Failed' &&
                                        counts.timeout > 0 && (
                                            <dd className="text-xs text-muted">
                                                {String(counts.timeout)} timed
                                                out
                                            </dd>
                                        )}
                                </div>
                            ))}
                        </dl>
                    )
                }}
            </QuerySection>
        </Card>
    )
}

// -- system ------------------------------------------------------------------------

export function SystemStatusCard() {
    const query = useQuery(systemQueries.status(useApiClient()))
    return (
        <Card title="System" description="Engine readiness and version.">
            <QuerySection query={query} subject="system status">
                {(system) => (
                    <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
                        <dt className="text-muted">Status</dt>
                        <dd>
                            <Badge
                                tone={system.ready ? 'success' : 'danger'}
                                dot
                            >
                                {system.ready ? 'Ready' : 'Not ready'}
                            </Badge>
                        </dd>
                        <dt className="text-muted">Version</dt>
                        <dd className="font-mono">{system.version}</dd>
                        {Object.entries(system.failures)
                            .sort(([a], [b]) => a.localeCompare(b))
                            .map(([check, reason]) => (
                                <div key={check} className="contents">
                                    <dt className="text-muted">{check}</dt>
                                    <dd className="text-danger-text">
                                        {reason}
                                    </dd>
                                </div>
                            ))}
                    </dl>
                )}
            </QuerySection>
        </Card>
    )
}

// -- projects ----------------------------------------------------------------------

export function ProjectsCard({ className }: { className?: string }) {
    const query = useProjects()
    return (
        <Card
            title="Projects"
            description="Projects you can access, by status."
            actions={<ViewAll to="/projects" label="All projects" />}
            className={className}
        >
            <QuerySection query={query} subject="projects">
                {({ items, page }) => {
                    if (items.length === 0)
                        return (
                            <EmptyState
                                title="No projects yet"
                                titleAs="h3"
                                description="Create a project to build and run a workflow."
                                action={
                                    <ViewAll
                                        to="/projects"
                                        label="Go to Projects"
                                    />
                                }
                            />
                        )
                    const byStatus = new Map<ProjectStatus, number>()
                    for (const p of items)
                        byStatus.set(
                            p.status,
                            (byStatus.get(p.status) ?? 0) + 1,
                        )
                    return (
                        <>
                            <dl className="grid grid-cols-[1fr_auto] gap-y-2 text-sm">
                                <dt className="font-medium">Total</dt>
                                <dd className="text-right font-semibold tabular-nums">
                                    {String(items.length)}
                                    {page.has_more && '+'}
                                </dd>
                                {projectStatusSchema.options.map((status) => {
                                    const n = byStatus.get(status)
                                    return n === undefined ? null : (
                                        <div key={status} className="contents">
                                            <dt className="text-muted">
                                                {projectStatusLabels[status]}
                                            </dt>
                                            <dd className="text-right tabular-nums">
                                                {String(n)}
                                            </dd>
                                        </div>
                                    )
                                })}
                            </dl>
                            {page.has_more && (
                                <p className="mt-3 text-sm text-muted">
                                    Status counts cover the first{' '}
                                    {String(items.length)} projects.
                                </p>
                            )}
                        </>
                    )
                }}
            </QuerySection>
        </Card>
    )
}
