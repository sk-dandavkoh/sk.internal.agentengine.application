import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type ReactNode } from 'react'
import { useNavigate, useParams } from 'react-router'
import { useApiClient } from '../../api/context'
import { ApiError, errorMessage } from '../../api/errors'
import {
    useDeleteWorkflow,
    useDuplicateWorkflow,
    useUpdateWorkflow,
} from '../../api/mutations'
import { exportWorkflow, type Workflow } from '../../api/workflows'
import { workflowQueries, queryKeys } from '../../api/queries'
import { QuerySection } from '../../components/QuerySection'
import {
    Button,
    Card,
    ConfirmDialog,
    ErrorState,
    PageHeader,
    TextLink,
    useToast,
} from '../../components/ui'
import { formatDateTime } from '../../lib/format'
import { saveFile } from '../../lib/saveFile'
import { cx } from '../../lib/cx'
import { versionsPath } from '../versions/paths'
import { VersionLink } from '../versions/shared'
import { WorkflowFormDialog } from './WorkflowFormDialog'
import { WorkflowStatusBadge } from './WorkflowStatusBadge'
import { SharingCard } from './SharingCard'
import { allows } from './access'

type Action = 'edit' | 'duplicate' | 'delete' | null

const workflowPath = (id: string) => `/workflows/${encodeURIComponent(id)}`

function Item({
    term,
    children,
    className,
}: {
    term: string
    children: ReactNode
    className?: string
}) {
    return (
        <div className={cx('flex flex-col gap-0.5', className)}>
            <dt className="text-muted">{term}</dt>
            <dd className="break-words">{children}</dd>
        </div>
    )
}

function Time({ iso }: { iso: string }) {
    return <time dateTime={iso}>{formatDateTime(iso)}</time>
}

function Metadata({ workflow }: { workflow: Workflow }) {
    const { graph, triggers } = workflow.draft
    return (
        <Card title="Details">
            <dl className="grid grid-cols-1 gap-4 p-4 text-sm sm:grid-cols-2 lg:grid-cols-3">
                <Item term="Status">
                    <WorkflowStatusBadge status={workflow.status} />
                </Item>
                <Item term="Latest published version">
                    {workflow.current_version > 0 ? (
                        <VersionLink
                            workflowId={workflow.id}
                            version={workflow.current_version}
                        />
                    ) : (
                        'Not published yet'
                    )}
                </Item>
                <Item term="Draft revision">{String(workflow.revision)}</Item>
                <Item term="Created">
                    <Time iso={workflow.created_at} />
                </Item>
                <Item term="Last updated">
                    <Time iso={workflow.updated_at} />
                </Item>
                <Item term="Workflow ID">
                    <code className="font-mono text-xs">{workflow.id}</code>
                </Item>
                <Item term="Draft contents">
                    {`${String(graph.nodes.length)} nodes, ${String(graph.edges.length)} connections, ${String(triggers.length)} triggers, ${String(workflow.files.length)} files`}
                </Item>
                <Item
                    term="Description"
                    className="sm:col-span-2 lg:col-span-3"
                >
                    {/* Plain text, never HTML. */}
                    <span className="whitespace-pre-wrap">
                        {workflow.description || 'No description.'}
                    </span>
                </Item>
            </dl>
        </Card>
    )
}

/** The draft's package, as the backend wrote it. */
function ExportWorkflow({ workflow }: { workflow: Workflow }) {
    const client = useApiClient()
    const toast = useToast()
    const [busy, setBusy] = useState(false)
    const download = async () => {
        setBusy(true)
        try {
            const data = await exportWorkflow(client, workflow.id)
            // The id, not the name: server data stays out of file names.
            const safe = workflow.id.replace(/[^\w-]/g, '_')
            saveFile(`${safe}-draft.akasha.zip`, data)
            toast.notify({
                tone: 'success',
                title: 'Workflow exported',
                description:
                    'Credential values are not part of the package, only their references.',
            })
        } catch (error) {
            toast.notify({
                tone: 'danger',
                title: 'Could not export the workflow',
                description: errorMessage(error),
            })
        } finally {
            setBusy(false)
        }
    }
    return (
        <Button loading={busy} onClick={() => void download()}>
            Export
        </Button>
    )
}

function WorkflowView({ workflow }: { workflow: Workflow }) {
    // Hides what the backend would refuse; until it is known, nothing
    // beyond reading is offered.
    const access = useQuery(
        workflowQueries.sharing(useApiClient(), workflow.id),
    ).data?.access
    const [action, setAction] = useState<Action>(null)
    const update = useUpdateWorkflow()
    const duplicate = useDuplicateWorkflow()
    const remove = useDeleteWorkflow()
    const queryClient = useQueryClient()
    const navigate = useNavigate()
    const toast = useToast()

    const close = () => {
        update.reset()
        duplicate.reset()
        remove.reset()
        setAction(null)
    }
    // A conflict means our copy is stale: reload it, so the user sees what
    // changed and a retry is based on the current revision.
    const onError = (error: unknown) => {
        if (error instanceof ApiError && error.kind === 'conflict')
            void queryClient.invalidateQueries({
                queryKey: queryKeys.workflows.detail(workflow.id),
            })
    }

    return (
        <>
            <PageHeader
                title={workflow.name}
                description={
                    <TextLink to="/workflows">Back to all workflows</TextLink>
                }
                actions={
                    <>
                        <TextLink
                            to={`${workflowPath(workflow.id)}/editor`}
                            className="self-center"
                        >
                            Open editor
                        </TextLink>
                        <TextLink
                            to={versionsPath(workflow.id)}
                            className="self-center"
                        >
                            Versions
                        </TextLink>
                        <TextLink
                            to={`${workflowPath(workflow.id)}/triggers`}
                            className="self-center"
                        >
                            Triggers
                        </TextLink>
                        {allows(access, 'edit') && (
                            <Button
                                onClick={() => {
                                    setAction('edit')
                                }}
                            >
                                Edit details
                            </Button>
                        )}
                        <ExportWorkflow workflow={workflow} />
                        <Button
                            onClick={() => {
                                setAction('duplicate')
                            }}
                        >
                            Duplicate
                        </Button>
                        {allows(access, 'manage') && (
                            <Button
                                variant="danger"
                                onClick={() => {
                                    setAction('delete')
                                }}
                            >
                                Delete
                            </Button>
                        )}
                    </>
                }
            />
            <Metadata workflow={workflow} />
            <div className="mt-6">
                <SharingCard workflowId={workflow.id} />
            </div>

            <WorkflowFormDialog
                open={action === 'edit'}
                onClose={close}
                title="Edit workflow details"
                submitLabel="Save"
                initial={workflow}
                pending={update.isPending}
                error={update.error}
                onSubmit={(values) => {
                    update.mutate(
                        {
                            workflowId: workflow.id,
                            // The draft is sent back unchanged; the revision
                            // makes the backend refuse to overwrite newer work.
                            input: {
                                ...values,
                                draft: workflow.draft,
                                expected_revision: workflow.revision,
                            },
                        },
                        {
                            onSuccess: () => {
                                close()
                                toast.notify({
                                    tone: 'success',
                                    title: 'Workflow details saved',
                                })
                            },
                            onError,
                        },
                    )
                }}
            />
            <WorkflowFormDialog
                open={action === 'duplicate'}
                onClose={close}
                title="Duplicate workflow"
                submitLabel="Duplicate"
                initial={{
                    name: `Copy of ${workflow.name}`.slice(0, 200),
                    description: '',
                }}
                withDescription={false}
                pending={duplicate.isPending}
                error={duplicate.error}
                onSubmit={({ name }) => {
                    duplicate.mutate(
                        { workflowId: workflow.id, name },
                        {
                            onSuccess: (copy) => {
                                close()
                                toast.notify({
                                    tone: 'success',
                                    title: `Workflow “${copy.name}” created`,
                                    description:
                                        'It holds a copy of the draft and has no published versions.',
                                })
                                void navigate(workflowPath(copy.id))
                            },
                        },
                    )
                }}
            />
            <ConfirmDialog
                open={action === 'delete'}
                onCancel={close}
                title="Delete workflow?"
                confirmLabel="Delete workflow"
                irreversible
                pending={remove.isPending}
                error={remove.error ? errorMessage(remove.error) : undefined}
                onConfirm={() => {
                    remove.mutate(workflow.id, {
                        onSuccess: () => {
                            toast.notify({
                                tone: 'success',
                                title: `Workflow “${workflow.name}” deleted`,
                            })
                            void navigate('/workflows', { replace: true })
                        },
                        onError,
                    })
                }}
            >
                “{workflow.name}” and its draft will be deleted. Workflows with
                published versions cannot be deleted.
            </ConfirmDialog>
        </>
    )
}

export function WorkflowDetailPage() {
    const { workflowId = '' } = useParams()
    const query = useQuery(workflowQueries.detail(useApiClient(), workflowId))

    if (
        query.data === undefined &&
        query.error instanceof ApiError &&
        query.error.kind === 'not_found'
    )
        return (
            <ErrorState
                title="Workflow not found"
                titleAs="h1"
                description="It does not exist, was deleted, or you do not have access to it."
                action={
                    <TextLink to="/workflows">Back to all workflows</TextLink>
                }
            />
        )
    return (
        <>
            {query.data === undefined && <PageHeader title="Workflow" />}
            <QuerySection query={query} subject="the workflow">
                {(workflow) => <WorkflowView workflow={workflow} />}
            </QuerySection>
        </>
    )
}
