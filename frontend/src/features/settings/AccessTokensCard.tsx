import { useQuery } from '@tanstack/react-query'
import { useState, type SubmitEvent } from 'react'
import type { AccessToken, CreatedAccessToken } from '../../api/auth'
import { useApiClient } from '../../api/context'
import { ApiError, errorMessage } from '../../api/errors'
import { useCreateAccessToken, useRevokeAccessToken } from '../../api/mutations'
import { accessTokenQueries } from '../../api/queries'
import { QuerySection } from '../../components/QuerySection'
import {
    Alert,
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
import { formText } from '../../lib/form'
import { formatDateTime } from '../../lib/format'

// Days until expiry; '' is "never" (the backend's null).
const EXPIRY_OPTIONS = [
    { value: '30', label: '30 days' },
    { value: '90', label: '90 days' },
    { value: '365', label: '1 year' },
    { value: '', label: 'Never (until revoked)' },
]

/** Personal access tokens: long-lived credentials for scripts and AI
 * agents (MCP, docs/mcp.md §3). The backend owns every rule; this card lists,
 * creates (showing the value exactly once) and revokes. */
export function AccessTokensCard() {
    const client = useApiClient()
    const query = useQuery(accessTokenQueries.list(client))
    const [creating, setCreating] = useState(false)
    const [revoking, setRevoking] = useState<AccessToken | null>(null)

    const columns: TableColumn<AccessToken>[] = [
        {
            key: 'name',
            header: 'Name',
            cell: (t) => (
                <div className="flex flex-col">
                    <span className="font-medium">{t.name}</span>
                    <code className="text-xs text-muted">{t.hint}…</code>
                </div>
            ),
        },
        {
            key: 'expires',
            header: 'Expires',
            cell: (t) =>
                t.expires_at ? formatDateTime(t.expires_at) : 'Never',
        },
        {
            key: 'used',
            header: 'Last used',
            cell: (t) =>
                t.last_used_at ? formatDateTime(t.last_used_at) : 'Never',
        },
        {
            key: 'actions',
            header: 'Actions',
            align: 'end',
            cell: (t) => (
                <Button
                    size="sm"
                    variant="danger"
                    aria-label={`Revoke ${t.name}`}
                    onClick={() => {
                        setRevoking(t)
                    }}
                >
                    Revoke
                </Button>
            ),
        },
    ]

    return (
        <Card
            className="lg:col-span-2"
            title="Access tokens"
            description="For scripts and AI agents (MCP). A token acts as you, with your role, until it expires or is revoked."
            actions={
                <Button
                    onClick={() => {
                        setCreating(true)
                    }}
                >
                    New token
                </Button>
            }
        >
            <QuerySection query={query} subject="access tokens">
                {(tokens) => (
                    <Table
                        caption="Access tokens"
                        hideCaption
                        columns={columns}
                        rows={tokens}
                        rowKey={(t) => t.id}
                        empty="No access tokens."
                    />
                )}
            </QuerySection>
            {creating && (
                <CreateTokenDialog
                    onClose={() => {
                        setCreating(false)
                    }}
                />
            )}
            {revoking && (
                <RevokeTokenDialog
                    token={revoking}
                    onClose={() => {
                        setRevoking(null)
                    }}
                />
            )}
        </Card>
    )
}

function CreateTokenDialog({ onClose }: { onClose: () => void }) {
    const create = useCreateAccessToken()
    // The value lives only here, only until the dialog closes.
    const [created, setCreated] = useState<CreatedAccessToken | null>(null)
    const close = () => {
        create.reset()
        setCreated(null)
        onClose()
    }
    const error = create.error
    const wrongPassword =
        error instanceof ApiError && error.code === 'INVALID_CREDENTIALS'

    function submit(event: SubmitEvent<HTMLFormElement>) {
        event.preventDefault()
        const data = new FormData(event.currentTarget)
        const days = formText(data, 'expires_in_days')
        create.mutate(
            {
                name: formText(data, 'name').trim(),
                password: formText(data, 'password'),
                expires_in_days: days === '' ? null : Number(days),
            },
            {
                onSuccess: (token) => {
                    setCreated(token)
                },
            },
        )
    }

    if (created) {
        return (
            <Dialog
                open
                onClose={close}
                title={`Token “${created.name}” created`}
                description="Copy it now. It is shown only once and cannot be retrieved later."
                footer={
                    <Button variant="primary" onClick={close}>
                        Done
                    </Button>
                }
            >
                <TokenValue value={created.token} />
            </Dialog>
        )
    }

    return (
        <Dialog
            open
            onClose={close}
            title="New access token"
            dismissible={!create.isPending}
            footer={
                <>
                    <Button onClick={close} disabled={create.isPending}>
                        Cancel
                    </Button>
                    <Button
                        type="submit"
                        form="new-access-token"
                        variant="primary"
                        loading={create.isPending}
                    >
                        Create token
                    </Button>
                </>
            }
        >
            <form
                id="new-access-token"
                className="flex flex-col gap-4"
                onSubmit={submit}
                aria-label="New access token"
            >
                {error && !wrongPassword && (
                    <Alert tone="danger">{errorMessage(error)}</Alert>
                )}
                <Input
                    label="Name"
                    name="name"
                    required
                    maxLength={100}
                    description="Where it is used, e.g. “claude-laptop”."
                    data-autofocus
                />
                <Select
                    label="Expires"
                    name="expires_in_days"
                    defaultValue="90"
                >
                    {EXPIRY_OPTIONS.map((o) => (
                        <option key={o.label} value={o.value}>
                            {o.label}
                        </option>
                    ))}
                </Select>
                <Input
                    label="Your password"
                    name="password"
                    type="password"
                    autoComplete="current-password"
                    required
                    maxLength={256}
                    description="Required to issue a token."
                    error={wrongPassword ? errorMessage(error) : undefined}
                />
            </form>
        </Dialog>
    )
}

function TokenValue({ value }: { value: string }) {
    const toast = useToast()
    return (
        <div className="flex items-end gap-2">
            <Input
                label="Token"
                value={value}
                readOnly
                className="font-mono"
                onFocus={(event) => {
                    event.currentTarget.select()
                }}
            />
            <Button
                onClick={() => {
                    navigator.clipboard.writeText(value).then(
                        () => {
                            toast.notify({
                                tone: 'success',
                                title: 'Token copied',
                            })
                        },
                        () => {
                            toast.notify({
                                tone: 'warning',
                                title: 'Copy failed; select the token and copy it.',
                            })
                        },
                    )
                }}
            >
                Copy
            </Button>
        </div>
    )
}

function RevokeTokenDialog({
    token,
    onClose,
}: {
    token: AccessToken
    onClose: () => void
}) {
    const revoke = useRevokeAccessToken()
    const toast = useToast()
    return (
        <ConfirmDialog
            open
            title={`Revoke “${token.name}”?`}
            confirmLabel="Revoke token"
            irreversible
            pending={revoke.isPending}
            error={revoke.error ? errorMessage(revoke.error) : undefined}
            onCancel={onClose}
            onConfirm={() => {
                revoke.mutate(token.id, {
                    onSuccess: () => {
                        toast.notify({
                            tone: 'success',
                            title: `Token “${token.name}” revoked`,
                        })
                        onClose()
                    },
                })
            }}
        >
            Scripts and agents using it are signed out immediately.
        </ConfirmDialog>
    )
}
