import {
    keepPreviousData,
    useQuery,
    useQueryClient,
} from '@tanstack/react-query'
import { useState, type ReactNode } from 'react'
import { useNavigate, useParams } from 'react-router'
import { useApiClient } from '../../api/context'
import { ApiError, errorMessage } from '../../api/errors'
import {
    useDuplicateProject,
    useRestoreProjectVersion,
} from '../../api/mutations'
import type { Execution } from '../../api/executions'
import type {
    Dependency,
    Edge,
    NodeInstance,
    Project,
    ProjectFile,
    ProjectVersion,
    Trigger,
} from '../../api/projects'
import { projectQueries, queryKeys } from '../../api/queries'
import { QuerySection } from '../../components/QuerySection'
import {
    Alert,
    Badge,
    Button,
    Card,
    ConfirmDialog,
    EmptyState,
    ErrorState,
    PageHeader,
    Select,
    Table,
    TextLink,
    useToast,
    type TableColumn,
} from '../../components/ui'
import { executionStatusDisplay } from '../dashboard/format'
import { ProjectFormDialog } from '../projects/ProjectFormDialog'
import { diffVersions, type Changes, type VersionDiff } from './compare'
import { Author, ImmutableBadge, LatestBadge, Time } from './shared'
import { versionsPath } from './paths'
import { SnapshotCanvas } from './SnapshotCanvas'
import { ProjectNotFound } from './VersionsPage'

const EXECUTION_PAGE_SIZE = 20

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

function Metadata({
    version,
    latest,
}: {
    version: ProjectVersion
    latest: number
}) {
    return (
        <Card title="Details">
            <dl className="grid grid-cols-1 gap-4 text-sm sm:grid-cols-2 lg:grid-cols-3">
                <Item term="Version">
                    {`v${String(version.version)}`}
                    {version.version === latest
                        ? ' (latest)'
                        : ` (latest is v${String(latest)})`}
                </Item>
                <Item term="Published">
                    <Time iso={version.published_at} />
                </Item>
                <Item term="Published by">
                    <Author author={version.published_by} />
                </Item>
                <Item term="Project schema version">
                    {version.schema_version}
                </Item>
                <Item term="Version ID">
                    <Mono>{version.id}</Mono>
                </Item>
                <Item term="Contents">
                    {`${String(version.graph.nodes.length)} nodes, ${String(version.graph.edges.length)} connections, ${String(version.triggers.length)} triggers, ${String(version.files.length)} files`}
                </Item>
                <div className="sm:col-span-2 lg:col-span-3">
                    <Item term="Description">
                        {/* Plain text, never HTML. */}
                        <span className="whitespace-pre-wrap">
                            {version.description || 'No description.'}
                        </span>
                    </Item>
                </div>
            </dl>
        </Card>
    )
}

const nodeColumns: TableColumn<NodeInstance>[] = [
    { key: 'id', header: 'Node', cell: (n) => <Mono>{n.id}</Mono> },
    { key: 'label', header: 'Label', cell: (n) => n.label || '—' },
    { key: 'type', header: 'Type', cell: (n) => <Mono>{n.node_type}</Mono> },
    {
        key: 'config',
        header: 'Configured fields',
        cell: (n) => Object.keys(n.config).sort().join(', ') || '—',
    },
]
const edgeColumns: TableColumn<Edge>[] = [
    {
        key: 'from',
        header: 'From',
        cell: (e) => <Mono>{`${e.source_node_id}.${e.source_port}`}</Mono>,
    },
    {
        key: 'to',
        header: 'To',
        cell: (e) => <Mono>{`${e.target_node_id}.${e.target_port}`}</Mono>,
    },
]
const triggerColumns: TableColumn<Trigger>[] = [
    { key: 'id', header: 'Trigger', cell: (t) => <Mono>{t.id}</Mono> },
    { key: 'type', header: 'Type', cell: (t) => t.trigger_type },
    {
        key: 'enabled',
        header: 'Enabled',
        cell: (t) => (t.enabled ? 'Yes' : 'No'),
    },
]
const dependencyColumns: TableColumn<Dependency>[] = [
    { key: 'kind', header: 'Kind', cell: (d) => d.kind },
    { key: 'name', header: 'Name', cell: (d) => <Mono>{d.name}</Mono> },
    {
        key: 'constraint',
        header: 'Version constraint',
        cell: (d) => <Mono>{d.version_constraint}</Mono>,
    },
]
const fileColumns: TableColumn<ProjectFile>[] = [
    { key: 'path', header: 'Path', cell: (f) => <Mono>{f.path}</Mono> },
    {
        key: 'size',
        header: 'Size',
        align: 'end',
        cell: (f) => `${String(f.size)} B`,
    },
]

// The canvas shows the graph; the tables say the same in text (node ids,
// configured fields, ports) for screen readers and copying.
function Snapshot({ version }: { version: ProjectVersion }) {
    const edgeKey = (e: Edge) =>
        `${e.source_node_id}.${e.source_port}>${e.target_node_id}.${e.target_port}`
    return (
        <Card
            title="Snapshot"
            description="The graph, triggers, dependencies and files frozen at publish time. Config values are not shown here."
        >
            <div className="flex flex-col gap-4">
                <SnapshotCanvas graph={version.graph} />
                <Table
                    caption="Nodes"
                    columns={nodeColumns}
                    rows={version.graph.nodes}
                    rowKey={(n) => n.id}
                    empty="No nodes."
                />
                <Table
                    caption="Connections"
                    columns={edgeColumns}
                    rows={version.graph.edges}
                    rowKey={edgeKey}
                    empty="No connections."
                />
                <Table
                    caption="Triggers"
                    columns={triggerColumns}
                    rows={version.triggers}
                    rowKey={(t) => t.id}
                    empty="No triggers."
                />
                <Table
                    caption="Dependencies"
                    columns={dependencyColumns}
                    rows={version.dependencies}
                    rowKey={(d) => `${d.kind}:${d.name}`}
                    empty="No declared dependencies."
                />
                <Table
                    caption="Files"
                    columns={fileColumns}
                    rows={version.files}
                    rowKey={(f) => f.path}
                    empty="No files."
                />
            </div>
        </Card>
    )
}

function ChangeList({ title, changes }: { title: string; changes: Changes }) {
    const rows = [
        ...changes.added.map((k) => ['Added', k] as const),
        ...changes.removed.map((k) => ['Removed', k] as const),
        ...changes.changed.map((k) => ['Changed', k] as const),
    ]
    if (rows.length === 0) return null
    return (
        <div>
            <h4 className="mb-1 font-medium">{title}</h4>
            <ul className="flex flex-col gap-1">
                {rows.map(([kind, key]) => (
                    <li key={`${kind}:${key}`} className="flex gap-2">
                        <Badge
                            tone={
                                kind === 'Added'
                                    ? 'success'
                                    : kind === 'Removed'
                                      ? 'danger'
                                      : 'warning'
                            }
                        >
                            {kind}
                        </Badge>
                        <Mono>{key}</Mono>
                    </li>
                ))}
            </ul>
        </div>
    )
}

function DiffResult({
    from,
    to,
    diff,
}: {
    from: string
    to: string
    diff: VersionDiff
}) {
    return (
        <section aria-label="Comparison result" className="flex flex-col gap-3">
            <p>
                Changes from {from} to {to}:
            </p>
            {diff.identical ? (
                <p className="text-muted">
                    No differences in graph, triggers, dependencies or files.
                </p>
            ) : (
                <>
                    <ChangeList title="Nodes" changes={diff.nodes} />
                    <ChangeList title="Connections" changes={diff.edges} />
                    <ChangeList title="Triggers" changes={diff.triggers} />
                    <ChangeList
                        title="Dependencies"
                        changes={diff.dependencies}
                    />
                    <ChangeList title="Files" changes={diff.files} />
                </>
            )}
        </section>
    )
}

type Target = number | 'draft' | null

function Comparison({
    project,
    version,
}: {
    project: Project
    version: ProjectVersion
}) {
    // Versions are numbered 1..current_version without gaps.
    const others = Array.from(
        { length: project.current_version },
        (_, i) => i + 1,
    ).filter((n) => n !== version.version)
    const [other, setOther] = useState<Target>(
        version.version > 1 ? version.version - 1 : 'draft',
    )
    const query = useQuery({
        ...projectQueries.version(
            useApiClient(),
            project.id,
            typeof other === 'number' ? other : 0,
        ),
        enabled: typeof other === 'number',
    })
    const label = (v: ProjectVersion) => `v${String(v.version)}`
    return (
        <Card
            title="Compare"
            description="Differences to another published version or to the current draft. Node positions are layout and not compared; files compare by path and size."
        >
            <div className="flex flex-col gap-4 text-sm">
                <Select
                    label="Compare with"
                    className="max-w-xs"
                    value={other === null ? '' : String(other)}
                    onChange={(e) => {
                        const { value } = e.target
                        setOther(
                            value === ''
                                ? null
                                : value === 'draft'
                                  ? 'draft'
                                  : Number(value),
                        )
                    }}
                >
                    <option value="">Choose what to compare with</option>
                    <option value="draft">
                        Current draft (revision {project.revision})
                    </option>
                    {others.map((n) => (
                        <option key={n} value={n}>
                            v{n}
                        </option>
                    ))}
                </Select>
                {other === 'draft' && (
                    <DiffResult
                        from={label(version)}
                        to="the current draft"
                        diff={diffVersions(version, {
                            ...project.draft,
                            files: project.files,
                        })}
                    />
                )}
                {typeof other === 'number' && (
                    <QuerySection
                        query={query}
                        subject={`version ${String(other)}`}
                    >
                        {(otherVersion) => {
                            const [from, to] =
                                otherVersion.version < version.version
                                    ? [otherVersion, version]
                                    : [version, otherVersion]
                            return (
                                <DiffResult
                                    from={label(from)}
                                    to={label(to)}
                                    diff={diffVersions(from, to)}
                                />
                            )
                        }}
                    </QuerySection>
                )}
            </div>
        </Card>
    )
}

const executionColumns: TableColumn<Execution>[] = [
    { key: 'id', header: 'Execution', cell: (e) => <Mono>{e.id}</Mono> },
    {
        key: 'status',
        header: 'Status',
        cell: (e) => {
            const { label, tone } = executionStatusDisplay[e.status]
            return (
                <Badge tone={tone} dot>
                    {label}
                </Badge>
            )
        },
    },
    { key: 'trigger', header: 'Trigger', cell: (e) => e.trigger_type },
    {
        key: 'created',
        header: 'Created',
        cell: (e) => <Time iso={e.created_at} />,
    },
]

function Executions({
    projectId,
    version,
}: {
    projectId: string
    version: number
}) {
    const [offset, setOffset] = useState(0)
    const query = useQuery({
        ...projectQueries.executions(useApiClient(), projectId, {
            version,
            offset,
            limit: EXECUTION_PAGE_SIZE,
        }),
        placeholderData: keepPreviousData,
    })
    return (
        <Card
            title="Executions of this version"
            description="Runs pinned to this snapshot, newest first. Later drafts and versions never change them."
        >
            <div>
                <QuerySection query={query} subject="executions">
                    {({ items, page }) =>
                        items.length === 0 && offset === 0 ? (
                            <EmptyState
                                title="Not run yet"
                                titleAs="h3"
                                description="No execution has used this version."
                            />
                        ) : (
                            <div
                                aria-busy={query.isPlaceholderData || undefined}
                            >
                                <Table
                                    caption="Executions of this version"
                                    hideCaption
                                    columns={executionColumns}
                                    rows={items}
                                    rowKey={(e) => e.id}
                                    empty="No executions on this page."
                                />
                                <nav
                                    aria-label="Execution pages"
                                    className="mt-3 flex justify-end gap-2"
                                >
                                    <Button
                                        size="sm"
                                        disabled={offset === 0}
                                        onClick={() => {
                                            setOffset(
                                                Math.max(
                                                    0,
                                                    offset -
                                                        EXECUTION_PAGE_SIZE,
                                                ),
                                            )
                                        }}
                                    >
                                        Previous
                                    </Button>
                                    <Button
                                        size="sm"
                                        disabled={!page.has_more}
                                        onClick={() => {
                                            setOffset(
                                                offset + EXECUTION_PAGE_SIZE,
                                            )
                                        }}
                                    >
                                        Next
                                    </Button>
                                </nav>
                            </div>
                        )
                    }
                </QuerySection>
            </div>
        </Card>
    )
}

type Action = 'restore' | 'clone' | null

/** Copy a version into the draft, or into a new project. Neither changes
 * the version: the backend copies it. */
function VersionActions({
    project,
    version,
}: {
    project: Project
    version: ProjectVersion
}) {
    const [action, setAction] = useState<Action>(null)
    const restore = useRestoreProjectVersion()
    const clone = useDuplicateProject()
    const queryClient = useQueryClient()
    const navigate = useNavigate()
    const toast = useToast()
    const v = `v${String(version.version)}`
    const close = () => {
        restore.reset()
        clone.reset()
        setAction(null)
    }
    return (
        <>
            <Button
                onClick={() => {
                    setAction('restore')
                }}
            >
                Restore to draft…
            </Button>
            <Button
                onClick={() => {
                    setAction('clone')
                }}
            >
                Clone as new project…
            </Button>
            <ConfirmDialog
                open={action === 'restore'}
                onCancel={close}
                title={`Restore ${v} to the draft?`}
                confirmLabel="Replace draft"
                pending={restore.isPending}
                error={restore.error ? errorMessage(restore.error) : undefined}
                onConfirm={() => {
                    restore.mutate(
                        {
                            projectId: project.id,
                            version: version.version,
                            expectedRevision: project.revision,
                        },
                        {
                            onSuccess: () => {
                                close()
                                toast.notify({
                                    tone: 'success',
                                    title: `Draft restored from ${v}`,
                                    description:
                                        'Publish it to make it a new version.',
                                })
                                void navigate(
                                    `/projects/${encodeURIComponent(project.id)}/editor`,
                                )
                            },
                            // A conflict means the draft changed: reload it,
                            // so a retry replaces the revision the user sees.
                            onError: (error) => {
                                if (
                                    error instanceof ApiError &&
                                    error.kind === 'conflict'
                                )
                                    void queryClient.invalidateQueries({
                                        queryKey: queryKeys.projects.detail(
                                            project.id,
                                        ),
                                    })
                            },
                        },
                    )
                }}
            >
                The current draft (revision {project.revision}) is replaced by a
                copy of {v}. Draft changes since then are lost. Published
                versions, {v} included, stay as they are.
            </ConfirmDialog>
            <ProjectFormDialog
                open={action === 'clone'}
                onClose={close}
                title={`Clone ${v} as a new project`}
                submitLabel="Create project"
                initial={{
                    name: `${project.name} (${v})`.slice(0, 200),
                    description: '',
                }}
                withDescription={false}
                pending={clone.isPending}
                error={clone.error}
                onSubmit={({ name }) => {
                    clone.mutate(
                        {
                            projectId: project.id,
                            name,
                            version: version.version,
                        },
                        {
                            onSuccess: (copy) => {
                                close()
                                toast.notify({
                                    tone: 'success',
                                    title: `Project “${copy.name}” created`,
                                    description: `Its draft is a copy of ${v}; it has no published versions.`,
                                })
                                void navigate(
                                    `/projects/${encodeURIComponent(copy.id)}`,
                                )
                            },
                        },
                    )
                }}
            />
        </>
    )
}

function VersionView({
    project,
    version,
}: {
    project: Project
    version: ProjectVersion
}) {
    const latest = version.version === project.current_version
    return (
        <>
            <PageHeader
                title={`${project.name} · v${String(version.version)}`}
                description={
                    <TextLink to={versionsPath(project.id)}>
                        All versions
                    </TextLink>
                }
                actions={
                    <>
                        <span className="flex items-center gap-2">
                            <ImmutableBadge />
                            {latest && <LatestBadge />}
                        </span>
                        <VersionActions project={project} version={version} />
                    </>
                }
            />
            <div className="flex flex-col gap-6">
                <Alert tone="info" title="Published versions are read-only">
                    v{version.version} cannot be edited. Changes go into the
                    draft and become a new version when it is published.{' '}
                    <TextLink
                        to={`/projects/${encodeURIComponent(project.id)}/editor`}
                    >
                        Open draft in editor
                    </TextLink>
                </Alert>
                <Metadata version={version} latest={project.current_version} />
                <Snapshot version={version} />
                <Comparison
                    // Reset the selection when navigating between versions.
                    key={version.version}
                    project={project}
                    version={version}
                />
                <Executions projectId={project.id} version={version.version} />
            </div>
        </>
    )
}

function VersionNotFound({ projectId }: { projectId: string }) {
    return (
        <ErrorState
            title="Version not found"
            titleAs="h1"
            description="This project has no published version with that number."
            action={
                <TextLink to={versionsPath(projectId)}>All versions</TextLink>
            }
        />
    )
}

const notFound = (error: unknown) =>
    error instanceof ApiError && error.kind === 'not_found'

function VersionDetail({
    projectId,
    version,
}: {
    projectId: string
    version: number
}) {
    const client = useApiClient()
    const project = useQuery(projectQueries.detail(client, projectId))
    const snapshot = useQuery(
        projectQueries.version(client, projectId, version),
    )

    if (project.data === undefined && notFound(project.error))
        return <ProjectNotFound />
    if (snapshot.data === undefined && notFound(snapshot.error))
        return <VersionNotFound projectId={projectId} />
    return (
        <>
            {(project.data === undefined || snapshot.data === undefined) && (
                <PageHeader title={`Version ${String(version)}`} />
            )}
            <QuerySection query={project} subject="the project">
                {(p) => (
                    <QuerySection
                        query={snapshot}
                        subject={`version ${String(version)}`}
                    >
                        {(v) => <VersionView project={p} version={v} />}
                    </QuerySection>
                )}
            </QuerySection>
        </>
    )
}

export function VersionDetailPage() {
    const { projectId = '', version = '' } = useParams()
    const number = Number(version)
    // Only canonical positive integers ("3", not "03" or "3.0") are versions.
    if (!Number.isInteger(number) || number < 1 || String(number) !== version)
        return <VersionNotFound projectId={projectId} />
    return (
        <VersionDetail
            // Fresh local state (pages, comparison) per version.
            key={`${projectId}/${version}`}
            projectId={projectId}
            version={number}
        />
    )
}
