import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useState, type SubmitEvent } from 'react'
import { useSearchParams } from 'react-router'
import { userRoleSchema, type User } from '../../api/auth'
import { useApiClient } from '../../api/context'
import { ApiError } from '../../api/errors'
import { userQueries } from '../../api/queries'
import { userStatusSchema, type UserListParams } from '../../api/users'
import { QuerySection } from '../../components/QuerySection'
import {
    Badge,
    Button,
    EmptyState,
    ErrorState,
    Input,
    PageHeader,
    Select,
    Table,
    type TableColumn,
} from '../../components/ui'
import { formText } from '../../lib/form'
import { formatDateTime } from '../../lib/format'
import { useSession } from '../auth/session'
import {
    CreateUserDialog,
    EditUserDialog,
    ResetPasswordDialog,
    StatusDialog,
} from './UserDialogs'
import { displayName, roleLabels } from './labels'

const PAGE_SIZE = 50

type Action =
    { kind: 'create' } | { kind: 'edit' | 'status' | 'password'; user: User }

/** List state lives in the URL: shareable, and Back restores it. */
function useListParams() {
    const [search, setSearch] = useSearchParams()
    const role = userRoleSchema.safeParse(search.get('role'))
    const status = userStatusSchema.safeParse(search.get('status'))
    const offset = Number(search.get('offset'))
    const params: UserListParams = {
        q: search.get('q') ?? '',
        limit: PAGE_SIZE,
        offset: Number.isInteger(offset) && offset > 0 ? offset : 0,
        ...(role.success && { role: role.data }),
        ...(status.success && { status: status.data }),
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
    const filtered = Boolean(params.q) || Boolean(params.role ?? params.status)
    return { params, update, filtered }
}

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
                // Remount on a URL change so the field shows the applied search.
                key={params.q}
                className="flex items-end gap-2"
            >
                <Input
                    label="Search users"
                    name="q"
                    type="search"
                    defaultValue={params.q}
                    maxLength={64}
                />
                <Button type="submit">Search</Button>
            </form>
            <Select
                label="Role"
                value={params.role ?? ''}
                onChange={(e) => {
                    update({ role: e.target.value })
                }}
            >
                <option value="">All roles</option>
                {userRoleSchema.options.map((r) => (
                    <option key={r} value={r}>
                        {roleLabels[r]}
                    </option>
                ))}
            </Select>
            <Select
                label="Status"
                value={params.status ?? ''}
                onChange={(e) => {
                    update({ status: e.target.value })
                }}
            >
                <option value="">All statuses</option>
                <option value="active">Active</option>
                <option value="disabled">Disabled</option>
            </Select>
        </div>
    )
}

function DateCell({ at }: { at: string | null }) {
    if (at === null) return <span className="text-muted">Never</span>
    return (
        <time dateTime={at} className="whitespace-nowrap">
            {formatDateTime(at)}
        </time>
    )
}

const adminRequired = (
    <ErrorState
        title="Admin access required"
        description="Only administrators can manage users. You can change your own password in Settings."
    />
)

export function UsersPage() {
    const { data: session } = useSession()
    const me = session?.status === 'active' ? session.session.user : undefined
    // The backend refuses non-admins anyway; this only avoids asking.
    return me?.role === 'admin' ? (
        <UserAdministration selfId={me.id} />
    ) : (
        <>
            <PageHeader title="Users" />
            {adminRequired}
        </>
    )
}

function UserAdministration({ selfId }: { selfId: string }) {
    const client = useApiClient()
    const { params, update, filtered } = useListParams()
    const query = useQuery({
        ...userQueries.list(client, params),
        placeholderData: keepPreviousData,
    })
    const [action, setAction] = useState<Action | null>(null)
    const close = () => {
        setAction(null)
    }
    // A role changed since the session was read: the backend's answer wins.
    const forbidden =
        query.error instanceof ApiError && query.error.kind === 'authorization'

    const columns: TableColumn<User>[] = [
        {
            key: 'name',
            header: 'Name',
            cell: (u) => (
                <span className="font-medium">
                    {displayName(u)}
                    {u.id === selfId && (
                        <span className="font-normal text-muted"> (you)</span>
                    )}
                </span>
            ),
        },
        { key: 'username', header: 'Username', cell: (u) => u.username },
        {
            key: 'role',
            header: 'Role',
            cell: (u) => (
                <Badge tone={u.role === 'admin' ? 'info' : 'neutral'}>
                    {roleLabels[u.role]}
                </Badge>
            ),
        },
        {
            key: 'status',
            header: 'Status',
            cell: (u) =>
                u.disabled ? (
                    <Badge tone="danger" dot>
                        Disabled
                    </Badge>
                ) : (
                    <Badge tone="success" dot>
                        Active
                    </Badge>
                ),
        },
        {
            key: 'created',
            header: 'Created',
            cell: (u) => <DateCell at={u.created_at} />,
        },
        {
            key: 'last_login',
            header: 'Last login',
            cell: (u) => <DateCell at={u.last_login_at} />,
        },
        {
            key: 'actions',
            header: <span className="sr-only">Actions</span>,
            align: 'end',
            cell: (u) => {
                // Mirrors the backend's self-lockout rule (409 OWN_ACCOUNT).
                const self = u.id === selfId
                const name = displayName(u)
                return (
                    <div className="flex justify-end gap-2">
                        <Button
                            size="sm"
                            aria-label={`Edit ${name}`}
                            onClick={() => {
                                setAction({ kind: 'edit', user: u })
                            }}
                        >
                            Edit
                        </Button>
                        {!self && (
                            <>
                                <Button
                                    size="sm"
                                    aria-label={`Reset password of ${name}`}
                                    onClick={() => {
                                        setAction({ kind: 'password', user: u })
                                    }}
                                >
                                    Reset password
                                </Button>
                                <Button
                                    size="sm"
                                    variant={
                                        u.disabled ? 'secondary' : 'danger'
                                    }
                                    aria-label={`${u.disabled ? 'Enable' : 'Disable'} ${name}`}
                                    onClick={() => {
                                        setAction({ kind: 'status', user: u })
                                    }}
                                >
                                    {u.disabled ? 'Enable' : 'Disable'}
                                </Button>
                            </>
                        )}
                    </div>
                )
            },
        },
    ]

    const newUser = (
        <Button
            variant="primary"
            disabled={forbidden}
            onClick={() => {
                setAction({ kind: 'create' })
            }}
        >
            New user
        </Button>
    )
    const offset = params.offset ?? 0

    return (
        <>
            <PageHeader
                title="Users"
                description="Accounts of this installation. Passwords are never shown; disabled users cannot sign in."
                actions={newUser}
            />
            {forbidden ? (
                adminRequired
            ) : (
                <>
                    <Filters params={params} update={update} />
                    <QuerySection query={query} subject="users">
                        {({ items, page }) =>
                            items.length === 0 && offset === 0 ? (
                                <EmptyState
                                    title={
                                        filtered
                                            ? 'No matching users'
                                            : 'No users yet'
                                    }
                                    description={
                                        filtered
                                            ? 'No user matches the search and filters.'
                                            : 'Create an account for each person who works with the engine.'
                                    }
                                    action={
                                        filtered ? (
                                            <Button
                                                onClick={() => {
                                                    update({
                                                        q: '',
                                                        role: '',
                                                        status: '',
                                                    })
                                                }}
                                            >
                                                Clear filters
                                            </Button>
                                        ) : (
                                            newUser
                                        )
                                    }
                                />
                            ) : (
                                <div
                                    aria-busy={
                                        query.isPlaceholderData || undefined
                                    }
                                >
                                    <Table
                                        caption="Users"
                                        hideCaption
                                        columns={columns}
                                        rows={items}
                                        rowKey={(u) => u.id}
                                        empty="No users on this page."
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
                                                        Math.max(
                                                            0,
                                                            offset - PAGE_SIZE,
                                                        ),
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
                                                    offset: String(
                                                        offset + PAGE_SIZE,
                                                    ),
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
                </>
            )}
            {action?.kind === 'create' && <CreateUserDialog onClose={close} />}
            {action?.kind === 'edit' && (
                <EditUserDialog
                    user={action.user}
                    self={action.user.id === selfId}
                    onClose={close}
                />
            )}
            {action?.kind === 'password' && (
                <ResetPasswordDialog user={action.user} onClose={close} />
            )}
            {action?.kind === 'status' && (
                <StatusDialog user={action.user} onClose={close} />
            )}
        </>
    )
}
