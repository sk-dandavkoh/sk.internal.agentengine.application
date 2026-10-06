import { useQuery } from '@tanstack/react-query'
import { useId, useState, type SubmitEvent } from 'react'
import { useNavigate } from 'react-router'
import { useApiClient } from '../../api/context'
import { ApiError, errorMessage } from '../../api/errors'
import {
    useLeaveProject,
    usePutProjectMember,
    useRemoveProjectMember,
    useTransferProjectOwnership,
} from '../../api/mutations'
import { projectQueries } from '../../api/queries'
import {
    projectRoleSchema,
    type Person,
    type ProjectAccess,
    type ProjectMember,
    type ProjectRole,
} from '../../api/sharing'
import { QuerySection } from '../../components/QuerySection'
import {
    Alert,
    Badge,
    Button,
    Card,
    ConfirmDialog,
    Dialog,
    Input,
    Select,
    Table,
    useToast,
    type TableColumn,
} from '../../components/ui'
import { controlClasses } from '../../components/ui/shared'
import { cx } from '../../lib/cx'
import { formText } from '../../lib/form'
import { formatDateTime } from '../../lib/format'
import { useSession } from '../auth/session'

const roleLabels: Record<ProjectRole, string> = {
    viewer: 'Viewer',
    editor: 'Editor',
}

const accessLabels: Record<ProjectAccess, string> = {
    manage: 'You manage this project',
    edit: 'You can edit this project',
    view: 'You can view this project',
}

const name = (p: Person) => p.display_name ?? p.username

/** Share by username. Errors that belong to the username land on it. */
function ShareForm({ projectId }: { projectId: string }) {
    const put = usePutProjectMember()
    const toast = useToast()
    const usernameId = useId()
    // Remount clears the field after a successful share.
    const [round, setRound] = useState(0)
    const error = put.error
    const fieldError =
        error instanceof ApiError &&
        ['USER_NOT_FOUND', 'PROJECT_OWNER'].includes(error.code)
            ? error.message
            : undefined

    function submit(event: SubmitEvent<HTMLFormElement>) {
        event.preventDefault()
        if (put.isPending) return
        const data = new FormData(event.currentTarget)
        const username = formText(data, 'username').trim().toLowerCase()
        const role = projectRoleSchema.catch('viewer').parse(data.get('role'))
        put.mutate(
            { projectId, username, role },
            {
                onSuccess: (member) => {
                    toast.notify({
                        tone: 'success',
                        title: `Shared with “${name(member)}” as ${roleLabels[member.role].toLowerCase()}`,
                    })
                    put.reset()
                    setRound((r) => r + 1)
                },
            },
        )
    }

    return (
        <form
            key={round}
            aria-label="Share project"
            onSubmit={submit}
            className="flex flex-wrap items-end gap-2"
        >
            <Input
                id={usernameId}
                label="Username"
                name="username"
                required
                maxLength={64}
                autoComplete="off"
                spellCheck={false}
                autoCapitalize="off"
                error={fieldError}
            />
            <Select label="Role" name="role" defaultValue="viewer">
                {projectRoleSchema.options.map((r) => (
                    <option key={r} value={r}>
                        {roleLabels[r]}
                    </option>
                ))}
            </Select>
            <Button type="submit" variant="primary" loading={put.isPending}>
                Share
            </Button>
            {error && !fieldError && (
                <Alert tone="danger" className="w-full">
                    {errorMessage(error)}
                </Alert>
            )}
        </form>
    )
}

function RoleSelect({
    projectId,
    member,
}: {
    projectId: string
    member: ProjectMember
}) {
    const put = usePutProjectMember()
    const toast = useToast()
    return (
        <select
            aria-label={`Role of ${name(member)}`}
            className={cx(controlClasses(false), 'h-8 w-auto')}
            value={member.role}
            disabled={put.isPending}
            onChange={(e) => {
                const role = projectRoleSchema
                    .catch(member.role)
                    .parse(e.target.value)
                put.mutate(
                    { projectId, username: member.username, role },
                    {
                        onSuccess: () => {
                            toast.notify({
                                tone: 'success',
                                title: `“${name(member)}” is now ${roleLabels[role].toLowerCase()}`,
                            })
                        },
                        onError: (error) => {
                            toast.notify({
                                tone: 'danger',
                                title: 'Could not change the role',
                                description: errorMessage(error),
                            })
                        },
                    },
                )
            }}
        >
            {projectRoleSchema.options.map((r) => (
                <option key={r} value={r}>
                    {roleLabels[r]}
                </option>
            ))}
        </select>
    )
}

function RemoveDialog({
    projectId,
    member,
    onClose,
}: {
    projectId: string
    member: ProjectMember
    onClose: () => void
}) {
    const remove = useRemoveProjectMember()
    const toast = useToast()
    return (
        <ConfirmDialog
            open
            title={`Stop sharing with “${name(member)}”?`}
            confirmLabel="Stop sharing"
            pending={remove.isPending}
            error={remove.error ? errorMessage(remove.error) : undefined}
            onCancel={onClose}
            onConfirm={() => {
                remove.mutate(
                    { projectId, username: member.username },
                    {
                        onSuccess: () => {
                            toast.notify({
                                tone: 'success',
                                title: `No longer shared with “${name(member)}”`,
                            })
                            onClose()
                        },
                    },
                )
            }}
        >
            They lose access with their next request: the project, its versions
            and its runs disappear for them. Copies they made stay theirs.
        </ConfirmDialog>
    )
}

function TransferDialog({
    projectId,
    onClose,
}: {
    projectId: string
    onClose: () => void
}) {
    const transfer = useTransferProjectOwnership()
    const toast = useToast()
    const formId = useId()
    const error = transfer.error
    const fieldError =
        error instanceof ApiError &&
        ['USER_NOT_FOUND', 'PROJECT_OWNER', 'USER_DISABLED'].includes(
            error.code,
        )
            ? error.message
            : undefined

    function submit(event: SubmitEvent<HTMLFormElement>) {
        event.preventDefault()
        if (transfer.isPending) return
        const username = formText(new FormData(event.currentTarget), 'username')
            .trim()
            .toLowerCase()
        transfer.mutate(
            { projectId, username },
            {
                onSuccess: (sharing) => {
                    const owner = sharing.owner ? name(sharing.owner) : username
                    toast.notify({
                        tone: 'success',
                        title: `“${owner}” now owns the project`,
                    })
                    onClose()
                },
            },
        )
    }

    return (
        <Dialog
            open
            onClose={onClose}
            title="Transfer ownership?"
            description="The new owner manages the project: they decide who keeps access and can delete it. The current owner stays on as an editor until removed or until they leave."
            dismissible={!transfer.isPending}
            footer={
                <>
                    <Button onClick={onClose} disabled={transfer.isPending}>
                        Cancel
                    </Button>
                    <Button
                        type="submit"
                        form={formId}
                        variant="danger"
                        loading={transfer.isPending}
                    >
                        Transfer ownership
                    </Button>
                </>
            }
        >
            <form
                id={formId}
                aria-label="Transfer ownership"
                onSubmit={submit}
                className="flex flex-col gap-4"
            >
                {error && !fieldError && (
                    <Alert tone="danger">{errorMessage(error)}</Alert>
                )}
                <Input
                    label="Username of the new owner"
                    name="username"
                    required
                    maxLength={64}
                    autoComplete="off"
                    spellCheck={false}
                    autoCapitalize="off"
                    description="Event triggers keep firing only for source projects the new owner can see."
                    error={fieldError}
                    data-autofocus
                />
            </form>
        </Dialog>
    )
}

function LeaveDialog({
    projectId,
    onClose,
}: {
    projectId: string
    onClose: () => void
}) {
    const leave = useLeaveProject()
    const toast = useToast()
    const navigate = useNavigate()
    return (
        <ConfirmDialog
            open
            title="Leave this project?"
            confirmLabel="Leave project"
            pending={leave.isPending}
            error={leave.error ? errorMessage(leave.error) : undefined}
            onCancel={onClose}
            onConfirm={() => {
                leave.mutate(projectId, {
                    onSuccess: () => {
                        toast.notify({
                            tone: 'success',
                            title: 'You left the project',
                        })
                        void navigate('/projects', { replace: true })
                    },
                })
            }}
        >
            You lose access at once: the project, its versions and its runs
            disappear for you. Only the owner or an administrator can share it
            with you again.
        </ConfirmDialog>
    )
}

/** Who can reach the project. Everyone with access sees it; only the
 * owner and admins change it (the backend enforces both). */
export function SharingCard({ projectId }: { projectId: string }) {
    const query = useQuery(projectQueries.sharing(useApiClient(), projectId))
    const [removing, setRemoving] = useState<ProjectMember | null>(null)
    const [dialog, setDialog] = useState<'transfer' | 'leave' | null>(null)
    const manage = query.data?.access === 'manage'
    const session = useSession().data
    const me = session?.status === 'active' ? session.session.user.id : null
    // Admins manage without being members; only members can leave.
    const member = query.data?.members.some((m) => m.user_id === me) ?? false
    const close = () => {
        setDialog(null)
    }

    const columns: TableColumn<ProjectMember>[] = [
        {
            key: 'name',
            header: 'Name',
            cell: (m) => (
                <div className="flex flex-col">
                    <span className="font-medium">{name(m)}</span>
                    {m.display_name && (
                        <span className="text-muted">{m.username}</span>
                    )}
                </div>
            ),
        },
        {
            key: 'role',
            header: 'Role',
            cell: (m) =>
                manage ? (
                    <RoleSelect projectId={projectId} member={m} />
                ) : (
                    <Badge>{roleLabels[m.role]}</Badge>
                ),
        },
        {
            key: 'added',
            header: 'Shared',
            cell: (m) => (
                <time dateTime={m.created_at} className="whitespace-nowrap">
                    {formatDateTime(m.created_at)}
                </time>
            ),
        },
    ]
    if (manage)
        columns.push({
            key: 'actions',
            header: <span className="sr-only">Actions</span>,
            align: 'end',
            cell: (m) => (
                <Button
                    size="sm"
                    variant="danger"
                    aria-label={`Stop sharing with ${name(m)}`}
                    onClick={() => {
                        setRemoving(m)
                    }}
                >
                    Remove
                </Button>
            ),
        })

    return (
        <Card
            title="Sharing"
            description="Viewers can read the project, its versions and runs. Editors can also change the draft, publish and run it. Only the owner and administrators share or delete it."
        >
            <div className="flex flex-col gap-4 p-4">
                <QuerySection query={query} subject="sharing">
                    {(sharing) => (
                        <>
                            <p className="text-sm">
                                <span className="font-medium">
                                    {accessLabels[sharing.access]}
                                </span>
                                {sharing.owner && (
                                    <>
                                        {' · '}Owner: {name(sharing.owner)}
                                    </>
                                )}
                            </p>
                            {sharing.members.length === 0 ? (
                                <p className="text-sm text-muted">
                                    Not shared with anyone.
                                </p>
                            ) : (
                                <Table
                                    caption="Shared with"
                                    hideCaption
                                    columns={columns}
                                    rows={sharing.members}
                                    rowKey={(m) => m.user_id}
                                    empty="Not shared with anyone."
                                />
                            )}
                            {sharing.access === 'manage' && (
                                <ShareForm projectId={projectId} />
                            )}
                            {(manage || member) && (
                                <div className="flex flex-wrap gap-2 border-t border-border pt-4">
                                    {manage && (
                                        <Button
                                            onClick={() => {
                                                setDialog('transfer')
                                            }}
                                        >
                                            Transfer ownership
                                        </Button>
                                    )}
                                    {member && (
                                        <Button
                                            variant="danger"
                                            onClick={() => {
                                                setDialog('leave')
                                            }}
                                        >
                                            Leave project
                                        </Button>
                                    )}
                                </div>
                            )}
                        </>
                    )}
                </QuerySection>
            </div>
            {dialog === 'transfer' && (
                <TransferDialog projectId={projectId} onClose={close} />
            )}
            {dialog === 'leave' && (
                <LeaveDialog projectId={projectId} onClose={close} />
            )}
            {removing && (
                <RemoveDialog
                    projectId={projectId}
                    member={removing}
                    onClose={() => {
                        setRemoving(null)
                    }}
                />
            )}
        </Card>
    )
}
