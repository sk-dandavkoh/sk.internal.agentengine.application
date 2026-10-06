import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useNavigate, useParams } from 'react-router'
import type { ApiClient } from '../../api/client'
import { useApiClient } from '../../api/context'
import { ApiError, errorMessage } from '../../api/errors'
import { useInvokeTrigger, useUpdateProject } from '../../api/mutations'
import type {
    Project,
    ProjectVersion,
    Trigger,
    ValidationIssue,
} from '../../api/projects'
import { projectQueries, queryKeys } from '../../api/queries'
import { QuerySection } from '../../components/QuerySection'
import {
    Alert,
    Badge,
    Button,
    Card,
    ConfirmDialog,
    ErrorState,
    PageHeader,
    Table,
    TextLink,
    useToast,
    type TableColumn,
} from '../../components/ui'
import { executionPath } from '../executions/format'
import { diffVersions } from '../versions/compare'
import { allows } from '../projects/access'
import { VersionLink } from '../versions/shared'
import { TriggerFormDialog } from './TriggerFormDialog'
import { triggerSummary, triggerTypeLabels } from './triggerTypes'

type Action =
    | { kind: 'create' }
    | { kind: 'edit'; trigger: Trigger }
    | { kind: 'delete'; trigger: Trigger }
    | null

const projectPath = (id: string) => `/projects/${encodeURIComponent(id)}`

/** Where the published state of draft trigger `id` stands. */
type PublishState = 'unpublished' | 'changed' | 'published'

function publishStates(project: Project, latest: ProjectVersion | undefined) {
    if (latest === undefined)
        return { states: new Map<string, PublishState>(), removed: [] }
    const diff = diffVersions(latest, {
        ...project.draft,
        files: project.files,
    }).triggers
    const states = new Map<string, PublishState>()
    for (const t of project.draft.triggers)
        states.set(
            t.id,
            diff.added.includes(t.id)
                ? 'unpublished'
                : diff.changed.includes(t.id)
                  ? 'changed'
                  : 'published',
        )
    return { states, removed: diff.removed }
}

function endpoint(client: ApiClient, path: string) {
    return new URL(`${client.baseUrl}${path}`, window.location.origin).href
}

function Endpoint({
    trigger,
    projectId,
}: {
    trigger: Trigger
    projectId: string
}) {
    const client = useApiClient()
    const ids = `${encodeURIComponent(projectId)}/${trigger.trigger_type === 'webhook' ? '' : 'triggers/'}${encodeURIComponent(trigger.id)}`
    switch (trigger.trigger_type) {
        case 'webhook':
            return (
                <div className="flex flex-col gap-1 text-xs">
                    <code className="font-mono break-all">
                        POST {endpoint(client, `/webhooks/${ids}`)}
                    </code>
                    <span className="text-muted">
                        Header{' '}
                        <code>X-Akasha-Signature: sha256=&lt;hex&gt;</code>:
                        HMAC-SHA256 of the raw body under the credential's
                        value. Unsigned requests are rejected.
                    </span>
                </div>
            )
        case 'manual':
        case 'api':
            return (
                <code className="font-mono text-xs break-all">
                    POST {endpoint(client, `/projects/${ids}/invoke`)}
                </code>
            )
        default:
            return null
    }
}

function Status({
    trigger,
    state,
}: {
    trigger: Trigger
    state: PublishState | undefined
}) {
    return (
        <div className="flex flex-wrap gap-1">
            <Badge tone={trigger.enabled ? 'success' : 'neutral'} dot>
                {trigger.enabled ? 'Enabled' : 'Disabled'}
            </Badge>
            {state === 'published' && <Badge tone="info">Published</Badge>}
            {state === 'changed' && (
                <Badge tone="warning">Unpublished changes</Badge>
            )}
            {(state === undefined || state === 'unpublished') && (
                <Badge tone="warning">Not published</Badge>
            )}
        </div>
    )
}

function Issues({
    issues,
    checking,
}: {
    issues: readonly ValidationIssue[] | undefined
    checking: boolean
}) {
    if (issues === undefined)
        return (
            <span className="text-muted">
                {checking ? 'Checking…' : 'Not checked'}
            </span>
        )
    if (issues.length === 0) return <Badge tone="success">Valid</Badge>
    return (
        <ul className="flex flex-col gap-1 text-danger-text">
            {issues.map((issue, i) => (
                <li key={i}>
                    <span className="sr-only">Error: </span>
                    {issue.message}
                </li>
            ))}
        </ul>
    )
}

function TriggersView({
    project,
    latest,
}: {
    project: Project
    latest: ProjectVersion | undefined
}) {
    const client = useApiClient()
    // Running and changing triggers needs edit access; the backend refuses
    // it regardless, this only stops offering it.
    const canEdit = allows(
        useQuery(projectQueries.sharing(client, project.id)).data?.access,
        'edit',
    )
    const [action, setAction] = useState<Action>(null)
    const save = useUpdateProject()
    const invoke = useInvokeTrigger()
    const navigate = useNavigate()
    const queryClient = useQueryClient()
    const toast = useToast()
    // The backend's verdict on the revision shown; refetched per save.
    const validation = useQuery({
        ...projectQueries.validation(client, project.id, project.revision),
        retry: false,
    })
    const { states, removed } = publishStates(project, latest)
    // What fires is the published copy, not the draft row.
    const live = new Map(latest?.triggers.map((t) => [t.id, t]))

    function run(trigger: Trigger) {
        invoke.mutate(
            { projectId: project.id, triggerId: trigger.id },
            {
                onSuccess: (execution) => {
                    toast.notify({ tone: 'success', title: 'Run started' })
                    void navigate(executionPath(execution.id))
                },
                onError: (error) => {
                    toast.notify({
                        tone: 'danger',
                        title: 'Could not start the run',
                        description: errorMessage(error),
                    })
                },
            },
        )
    }
    const triggers = project.draft.triggers

    const issuesFor = (id: string) =>
        validation.data?.issues.filter((i) => i.trigger_id === id)

    const close = () => {
        save.reset()
        setAction(null)
    }

    /** PUT the draft with `next` as its triggers; the revision keeps it
     * from overwriting newer work. */
    function saveTriggers(
        next: Trigger[],
        success: string,
        onSuccess: () => void = close,
    ) {
        save.mutate(
            {
                projectId: project.id,
                input: {
                    name: project.name,
                    description: project.description,
                    draft: { ...project.draft, triggers: next },
                    expected_revision: project.revision,
                },
            },
            {
                onSuccess: () => {
                    onSuccess()
                    toast.notify({ tone: 'success', title: success })
                },
                onError: (error) => {
                    // A stale copy: reload so a retry uses the current draft.
                    if (error instanceof ApiError && error.kind === 'conflict')
                        void queryClient.invalidateQueries({
                            queryKey: queryKeys.projects.detail(project.id),
                        })
                },
            },
        )
    }

    function toggle(trigger: Trigger) {
        const enabled = !trigger.enabled
        saveTriggers(
            triggers.map((t) => (t.id === trigger.id ? { ...t, enabled } : t)),
            `Trigger ${enabled ? 'enabled' : 'disabled'} in the draft`,
            () => undefined,
        )
    }

    const columns: TableColumn<Trigger>[] = [
        {
            key: 'type',
            header: 'Type',
            cell: (t) => (
                <div className="flex flex-col">
                    <span className="font-medium">
                        {triggerTypeLabels[t.trigger_type]}
                    </span>
                    <code className="font-mono text-xs text-muted" title={t.id}>
                        {t.id}
                    </code>
                </div>
            ),
        },
        {
            key: 'config',
            header: 'Configuration',
            cell: (t) => (
                <div className="flex flex-col gap-1">
                    <span>{triggerSummary(t)}</span>
                    <Endpoint trigger={t} projectId={project.id} />
                </div>
            ),
        },
        {
            key: 'status',
            header: 'Status',
            cell: (t) => <Status trigger={t} state={states.get(t.id)} />,
        },
        {
            key: 'issues',
            header: 'Validation',
            cell: (t) => (
                <Issues
                    issues={issuesFor(t.id)}
                    checking={validation.isFetching}
                />
            ),
        },
        {
            key: 'actions',
            header: <span className="sr-only">Actions</span>,
            align: 'end',
            cell: (t) =>
                canEdit && (
                    <div className="flex justify-end gap-2">
                        {t.trigger_type === 'manual' &&
                            live.get(t.id)?.enabled === true && (
                                <Button
                                    size="sm"
                                    variant="primary"
                                    loading={
                                        invoke.isPending &&
                                        invoke.variables.triggerId === t.id
                                    }
                                    disabled={invoke.isPending}
                                    aria-label={`Run trigger ${t.id}`}
                                    onClick={() => {
                                        run(t)
                                    }}
                                >
                                    Run
                                </Button>
                            )}
                        <Button
                            size="sm"
                            disabled={save.isPending}
                            aria-label={`${t.enabled ? 'Disable' : 'Enable'} trigger ${t.id}`}
                            onClick={() => {
                                toggle(t)
                            }}
                        >
                            {t.enabled ? 'Disable' : 'Enable'}
                        </Button>
                        <Button
                            size="sm"
                            aria-label={`Edit trigger ${t.id}`}
                            onClick={() => {
                                setAction({ kind: 'edit', trigger: t })
                            }}
                        >
                            Edit
                        </Button>
                        <Button
                            size="sm"
                            variant="danger"
                            aria-label={`Delete trigger ${t.id}`}
                            onClick={() => {
                                setAction({ kind: 'delete', trigger: t })
                            }}
                        >
                            Delete
                        </Button>
                    </div>
                ),
        },
    ]

    const editing = action?.kind === 'edit' ? action.trigger : undefined
    const deleting = action?.kind === 'delete' ? action.trigger : undefined

    return (
        <>
            <PageHeader
                title="Triggers"
                description={
                    <TextLink to={projectPath(project.id)}>
                        Back to {project.name}
                    </TextLink>
                }
                actions={
                    canEdit && (
                        <Button
                            variant="primary"
                            onClick={() => {
                                setAction({ kind: 'create' })
                            }}
                        >
                            New trigger
                        </Button>
                    )
                }
            />
            <div className="flex flex-col gap-4">
                {!canEdit && (
                    <Alert tone="info" title="View-only access">
                        You can see this project's triggers, but only its owner,
                        editors and administrators can change or run them.
                    </Alert>
                )}
                <Alert tone="info">
                    Triggers are saved to the draft (revision{' '}
                    {String(project.revision)}) and fire only from the latest
                    published version
                    {latest ? (
                        <>
                            {' '}
                            (
                            <VersionLink
                                projectId={project.id}
                                version={latest.version}
                            />
                            )
                        </>
                    ) : (
                        ', and this project has none yet'
                    )}
                    . Publish from the{' '}
                    <TextLink to={`${projectPath(project.id)}/editor`}>
                        editor
                    </TextLink>{' '}
                    to apply changes.
                </Alert>
                {save.error && action === null && (
                    <Alert tone="danger" title="Could not save the trigger">
                        {errorMessage(save.error)}
                    </Alert>
                )}
                {validation.isError && (
                    <Alert
                        tone="warning"
                        action={
                            <Button
                                size="sm"
                                loading={validation.isFetching}
                                onClick={() => void validation.refetch()}
                            >
                                Retry
                            </Button>
                        }
                    >
                        Could not check the triggers.{' '}
                        {errorMessage(validation.error)}
                    </Alert>
                )}
                {removed.length > 0 && (
                    <Alert tone="warning" title="Removed from the draft">
                        Still active in the published version until the next
                        publish:{' '}
                        {removed.map((id, i) => (
                            <span key={id}>
                                {i > 0 && ', '}
                                <code className="font-mono text-xs">{id}</code>
                            </span>
                        ))}
                    </Alert>
                )}
                <Card title="Draft triggers">
                    <Table
                        caption="Triggers"
                        hideCaption
                        columns={columns}
                        rows={triggers}
                        rowKey={(t) => t.id}
                        empty="No triggers. Without one, published versions run only when started manually."
                    />
                </Card>
            </div>

            <TriggerFormDialog
                project={project}
                open={action?.kind === 'create' || editing !== undefined}
                onClose={close}
                trigger={editing}
                pending={save.isPending}
                error={save.error}
                onSubmit={(trigger) => {
                    saveTriggers(
                        editing
                            ? triggers.map((t) =>
                                  t.id === trigger.id ? trigger : t,
                              )
                            : [...triggers, trigger],
                        editing
                            ? 'Trigger saved to the draft'
                            : 'Trigger created in the draft',
                    )
                }}
            />
            <ConfirmDialog
                open={deleting !== undefined}
                onCancel={close}
                title="Delete trigger?"
                confirmLabel="Delete trigger"
                pending={save.isPending}
                error={save.error ? errorMessage(save.error) : undefined}
                onConfirm={() => {
                    if (deleting)
                        saveTriggers(
                            triggers.filter((t) => t.id !== deleting.id),
                            'Trigger deleted from the draft',
                        )
                }}
            >
                The {deleting ? triggerTypeLabels[deleting.trigger_type] : ''}{' '}
                trigger{' '}
                <code className="font-mono text-xs">{deleting?.id}</code> is
                removed from the draft. A published copy keeps firing until the
                next publish; publishing a version that still has it restores
                it.
            </ConfirmDialog>
        </>
    )
}

export function TriggersPage() {
    const { projectId = '' } = useParams()
    const client = useApiClient()
    const query = useQuery(projectQueries.detail(client, projectId))
    const current = query.data?.current_version ?? 0
    const latest = useQuery({
        ...projectQueries.version(client, projectId, current),
        enabled: current > 0,
    })

    if (
        query.data === undefined &&
        query.error instanceof ApiError &&
        query.error.kind === 'not_found'
    )
        return (
            <ErrorState
                title="Project not found"
                titleAs="h1"
                description="It does not exist, was deleted, or you do not have access to it."
                action={
                    <TextLink to="/projects">Back to all projects</TextLink>
                }
            />
        )
    return (
        <>
            {query.data === undefined && <PageHeader title="Triggers" />}
            <QuerySection query={query} subject="the triggers">
                {(project) =>
                    current > 0 && latest.data === undefined ? (
                        <QuerySection
                            query={latest}
                            subject="the published triggers"
                        >
                            {(version) => (
                                <TriggersView
                                    project={project}
                                    latest={version}
                                />
                            )}
                        </QuerySection>
                    ) : (
                        <TriggersView project={project} latest={latest.data} />
                    )
                }
            </QuerySection>
        </>
    )
}
