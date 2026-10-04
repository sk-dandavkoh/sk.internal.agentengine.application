import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { useApiClient } from '../../api/context'
import type { CredentialReference } from '../../api/credentials'
import { ApiError, errorMessage } from '../../api/errors'
import {
    useCreateCredential,
    useDeleteCredential,
    useUpdateCredential,
} from '../../api/mutations'
import { credentialQueries } from '../../api/queries'
import { QuerySection } from '../../components/QuerySection'
import {
    Badge,
    Button,
    ConfirmDialog,
    EmptyState,
    ErrorState,
    PageHeader,
    Table,
    useToast,
    type TableColumn,
} from '../../components/ui'
import { formatDateTime } from '../../lib/format'
import {
    CredentialFormDialog,
    type CredentialFormValues,
} from './CredentialFormDialog'
import { kindLabels } from './kinds'

const PAGE_SIZE = 50

type Editing =
    { mode: 'create' } | { mode: 'edit'; credential: CredentialReference }

function CreateDialog({ onClose }: { onClose: () => void }) {
    const create = useCreateCredential()
    const toast = useToast()
    // reset() drops the mutation's variables, the only copy of the secret
    // outside the form; gcTime 0 then evicts it from the cache.
    const close = () => {
        create.reset()
        onClose()
    }
    return (
        <CredentialFormDialog
            open
            onClose={close}
            pending={create.isPending}
            error={create.error}
            onSubmit={({ name, kind, description, value }) => {
                create.mutate(
                    { name, kind, description, value },
                    {
                        onSuccess: (credential) => {
                            toast.notify({
                                tone: 'success',
                                title: `Credential “${credential.name}” created`,
                            })
                            close()
                        },
                    },
                )
            }}
        />
    )
}

function EditDialog({
    credential,
    onClose,
}: {
    credential: CredentialReference
    onClose: () => void
}) {
    const update = useUpdateCredential()
    const toast = useToast()
    const close = () => {
        update.reset()
        onClose()
    }
    const submit = ({ description, value }: CredentialFormValues) => {
        update.mutate(
            // An empty value field means "keep": it is never sent.
            {
                name: credential.name,
                input: { description, ...(value && { value }) },
            },
            {
                onSuccess: () => {
                    toast.notify({
                        tone: 'success',
                        title: value
                            ? `Credential “${credential.name}” rotated`
                            : `Credential “${credential.name}” updated`,
                    })
                    close()
                },
            },
        )
    }
    return (
        <CredentialFormDialog
            open
            onClose={close}
            credential={credential}
            pending={update.isPending}
            error={update.error}
            onSubmit={submit}
        />
    )
}

function DeleteDialog({
    credential,
    onClose,
}: {
    credential: CredentialReference
    onClose: () => void
}) {
    const remove = useDeleteCredential()
    const toast = useToast()
    return (
        <ConfirmDialog
            open
            title={`Delete “${credential.name}”?`}
            confirmLabel="Delete credential"
            irreversible
            pending={remove.isPending}
            error={remove.error ? errorMessage(remove.error) : undefined}
            onCancel={onClose}
            onConfirm={() => {
                remove.mutate(credential.name, {
                    onSuccess: () => {
                        toast.notify({
                            tone: 'success',
                            title: `Credential “${credential.name}” deleted`,
                        })
                        onClose()
                    },
                })
            }}
        >
            The secret is destroyed. Nodes that reference{' '}
            <code>{credential.reference}</code> fail when they run, until a
            credential with the same name is created again.
        </ConfirmDialog>
    )
}

export function CredentialsPage() {
    const client = useApiClient()
    const [offset, setOffset] = useState(0)
    const query = useQuery({
        ...credentialQueries.list(client, { offset, limit: PAGE_SIZE }),
        placeholderData: keepPreviousData,
    })
    const [editing, setEditing] = useState<Editing | null>(null)
    const [deleting, setDeleting] = useState<CredentialReference | null>(null)
    const forbidden =
        query.error instanceof ApiError && query.error.kind === 'authorization'

    const newCredential = (
        <Button
            variant="primary"
            disabled={forbidden}
            onClick={() => {
                setEditing({ mode: 'create' })
            }}
        >
            New credential
        </Button>
    )

    const columns: TableColumn<CredentialReference>[] = [
        {
            key: 'name',
            header: 'Name',
            cell: (c) => (
                <div className="flex flex-col">
                    <span className="font-medium">{c.name}</span>
                    {c.description && (
                        <span className="line-clamp-1 text-muted">
                            {c.description}
                        </span>
                    )}
                </div>
            ),
        },
        {
            key: 'kind',
            header: 'Type',
            cell: (c) => <Badge>{kindLabels[c.kind]}</Badge>,
        },
        {
            key: 'reference',
            header: 'Reference',
            cell: (c) => <code className="text-sm">{c.reference}</code>,
        },
        {
            key: 'value',
            header: 'Value',
            // Nothing to show: the API never returns it.
            cell: () => (
                <span className="text-muted" aria-label="Hidden">
                    ••••••••
                </span>
            ),
        },
        {
            key: 'updated',
            header: 'Updated',
            cell: (c) => (
                <time dateTime={c.updated_at} className="whitespace-nowrap">
                    {formatDateTime(c.updated_at)}
                </time>
            ),
        },
        {
            key: 'actions',
            header: <span className="sr-only">Actions</span>,
            align: 'end',
            cell: (c) => (
                <div className="flex justify-end gap-2">
                    <Button
                        size="sm"
                        aria-label={`Edit ${c.name}`}
                        onClick={() => {
                            setEditing({ mode: 'edit', credential: c })
                        }}
                    >
                        Edit
                    </Button>
                    <Button
                        size="sm"
                        variant="danger"
                        aria-label={`Delete ${c.name}`}
                        onClick={() => {
                            setDeleting(c)
                        }}
                    >
                        Delete
                    </Button>
                </div>
            ),
        },
    ]

    return (
        <>
            <PageHeader
                title="Credentials"
                description="Secrets that nodes use by reference. Values are write-only and never shown again."
                actions={newCredential}
            />
            {forbidden ? (
                <ErrorState
                    title="Admin access required"
                    description="Only admins can manage credentials. Your projects can still use a credential by its reference, e.g. credential:<name>."
                />
            ) : (
                <QuerySection query={query} subject="credentials">
                    {({ items, page }) =>
                        items.length === 0 && offset === 0 ? (
                            <EmptyState
                                title="No credentials yet"
                                description="Store an API key or password once; projects reference it by name."
                                action={newCredential}
                            />
                        ) : (
                            <div
                                aria-busy={query.isPlaceholderData || undefined}
                            >
                                <Table
                                    caption="Credentials"
                                    hideCaption
                                    columns={columns}
                                    rows={items}
                                    rowKey={(c) => c.name}
                                    empty="No credentials on this page."
                                />
                                <nav
                                    aria-label="Pagination"
                                    className="mt-3 flex items-center justify-end gap-2"
                                >
                                    <Button
                                        size="sm"
                                        disabled={offset === 0}
                                        onClick={() => {
                                            setOffset(
                                                Math.max(0, offset - PAGE_SIZE),
                                            )
                                        }}
                                    >
                                        Previous
                                    </Button>
                                    <Button
                                        size="sm"
                                        disabled={!page.has_more}
                                        onClick={() => {
                                            setOffset(offset + PAGE_SIZE)
                                        }}
                                    >
                                        Next
                                    </Button>
                                </nav>
                            </div>
                        )
                    }
                </QuerySection>
            )}
            {editing?.mode === 'create' && (
                <CreateDialog
                    onClose={() => {
                        setEditing(null)
                    }}
                />
            )}
            {editing?.mode === 'edit' && (
                <EditDialog
                    credential={editing.credential}
                    onClose={() => {
                        setEditing(null)
                    }}
                />
            )}
            {deleting && (
                <DeleteDialog
                    credential={deleting}
                    onClose={() => {
                        setDeleting(null)
                    }}
                />
            )}
        </>
    )
}
