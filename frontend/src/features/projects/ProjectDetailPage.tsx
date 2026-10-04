import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type ReactNode } from 'react'
import { useNavigate, useParams } from 'react-router'
import { useApiClient } from '../../api/context'
import { ApiError, errorMessage } from '../../api/errors'
import {
    useDeleteProject,
    useDuplicateProject,
    useUpdateProject,
} from '../../api/mutations'
import type { Project } from '../../api/projects'
import { projectQueries, queryKeys } from '../../api/queries'
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
import { versionsPath } from '../versions/paths'
import { VersionLink } from '../versions/shared'
import { ProjectFormDialog } from './ProjectFormDialog'
import { ProjectStatusBadge } from './ProjectStatusBadge'

type Action = 'edit' | 'duplicate' | 'delete' | null

const projectPath = (id: string) => `/projects/${encodeURIComponent(id)}`

function Item({ term, children }: { term: string; children: ReactNode }) {
    return (
        <div className="flex flex-col gap-0.5">
            <dt className="text-muted">{term}</dt>
            <dd className="break-words">{children}</dd>
        </div>
    )
}

function Time({ iso }: { iso: string }) {
    return <time dateTime={iso}>{formatDateTime(iso)}</time>
}

function Metadata({ project }: { project: Project }) {
    const { graph, triggers } = project.draft
    return (
        <Card title="Details">
            <dl className="grid grid-cols-1 gap-4 p-4 text-sm sm:grid-cols-2 lg:grid-cols-3">
                <Item term="Status">
                    <ProjectStatusBadge status={project.status} />
                </Item>
                <Item term="Latest published version">
                    {project.current_version > 0 ? (
                        <VersionLink
                            projectId={project.id}
                            version={project.current_version}
                        />
                    ) : (
                        'Not published yet'
                    )}
                </Item>
                <Item term="Draft revision">{String(project.revision)}</Item>
                <Item term="Created">
                    <Time iso={project.created_at} />
                </Item>
                <Item term="Last updated">
                    <Time iso={project.updated_at} />
                </Item>
                <Item term="Project ID">
                    <code className="font-mono text-xs">{project.id}</code>
                </Item>
                <Item term="Draft contents">
                    {`${String(graph.nodes.length)} nodes, ${String(graph.edges.length)} connections, ${String(triggers.length)} triggers, ${String(project.files.length)} files`}
                </Item>
                <div className="sm:col-span-2 lg:col-span-3">
                    <Item term="Description">
                        {/* Plain text, never HTML. */}
                        <span className="whitespace-pre-wrap">
                            {project.description || 'No description.'}
                        </span>
                    </Item>
                </div>
            </dl>
        </Card>
    )
}

function ProjectView({ project }: { project: Project }) {
    const [action, setAction] = useState<Action>(null)
    const update = useUpdateProject()
    const duplicate = useDuplicateProject()
    const remove = useDeleteProject()
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
                queryKey: queryKeys.projects.detail(project.id),
            })
    }

    return (
        <>
            <PageHeader
                title={project.name}
                description={
                    <TextLink to="/projects">Back to all projects</TextLink>
                }
                actions={
                    <>
                        <TextLink
                            to={`${projectPath(project.id)}/editor`}
                            className="self-center"
                        >
                            Open editor
                        </TextLink>
                        <TextLink
                            to={versionsPath(project.id)}
                            className="self-center"
                        >
                            Versions
                        </TextLink>
                        <TextLink
                            to={`${projectPath(project.id)}/triggers`}
                            className="self-center"
                        >
                            Triggers
                        </TextLink>
                        <Button
                            onClick={() => {
                                setAction('edit')
                            }}
                        >
                            Edit details
                        </Button>
                        <Button
                            onClick={() => {
                                setAction('duplicate')
                            }}
                        >
                            Duplicate
                        </Button>
                        <Button
                            variant="danger"
                            onClick={() => {
                                setAction('delete')
                            }}
                        >
                            Delete
                        </Button>
                    </>
                }
            />
            <Metadata project={project} />

            <ProjectFormDialog
                open={action === 'edit'}
                onClose={close}
                title="Edit project details"
                submitLabel="Save"
                initial={project}
                pending={update.isPending}
                error={update.error}
                onSubmit={(values) => {
                    update.mutate(
                        {
                            projectId: project.id,
                            // The draft is sent back unchanged; the revision
                            // makes the backend refuse to overwrite newer work.
                            input: {
                                ...values,
                                draft: project.draft,
                                expected_revision: project.revision,
                            },
                        },
                        {
                            onSuccess: () => {
                                close()
                                toast.notify({
                                    tone: 'success',
                                    title: 'Project details saved',
                                })
                            },
                            onError,
                        },
                    )
                }}
            />
            <ProjectFormDialog
                open={action === 'duplicate'}
                onClose={close}
                title="Duplicate project"
                submitLabel="Duplicate"
                initial={{
                    name: `Copy of ${project.name}`.slice(0, 200),
                    description: '',
                }}
                withDescription={false}
                pending={duplicate.isPending}
                error={duplicate.error}
                onSubmit={({ name }) => {
                    duplicate.mutate(
                        { projectId: project.id, name },
                        {
                            onSuccess: (copy) => {
                                close()
                                toast.notify({
                                    tone: 'success',
                                    title: `Project “${copy.name}” created`,
                                    description:
                                        'It holds a copy of the draft and has no published versions.',
                                })
                                void navigate(projectPath(copy.id))
                            },
                        },
                    )
                }}
            />
            <ConfirmDialog
                open={action === 'delete'}
                onCancel={close}
                title="Delete project?"
                confirmLabel="Delete project"
                irreversible
                pending={remove.isPending}
                error={remove.error ? errorMessage(remove.error) : undefined}
                onConfirm={() => {
                    remove.mutate(project.id, {
                        onSuccess: () => {
                            toast.notify({
                                tone: 'success',
                                title: `Project “${project.name}” deleted`,
                            })
                            void navigate('/projects', { replace: true })
                        },
                        onError,
                    })
                }}
            >
                “{project.name}” and its draft will be deleted. Projects with
                published versions cannot be deleted.
            </ConfirmDialog>
        </>
    )
}

export function ProjectDetailPage() {
    const { projectId = '' } = useParams()
    const query = useQuery(projectQueries.detail(useApiClient(), projectId))

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
            {query.data === undefined && <PageHeader title="Project" />}
            <QuerySection query={query} subject="the project">
                {(project) => <ProjectView project={project} />}
            </QuerySection>
        </>
    )
}
