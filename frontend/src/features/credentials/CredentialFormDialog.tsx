import { useId, type SubmitEvent } from 'react'
import {
    credentialKindSchema,
    type CredentialKind,
    type CredentialReference,
} from '../../api/credentials'
import { ApiError, errorMessage } from '../../api/errors'
import {
    Alert,
    Button,
    Dialog,
    Input,
    Select,
    Textarea,
} from '../../components/ui'
import { formText } from '../../lib/form'
import { kindLabels } from './kinds'

export interface CredentialFormValues {
    name: string
    kind: CredentialKind
    description: string
    /** '' on edit: keep the stored secret. */
    value: string
}

interface CredentialFormDialogProps {
    open: boolean
    onClose: () => void
    /** Absent: create. Present: edit its description or rotate its value. */
    credential?: CredentialReference
    pending: boolean
    error: unknown
    onSubmit: (values: CredentialFormValues) => void
}

// Mirrors of backend domain/credentials.py and api/credentials.py, so the
// browser stops obvious mistakes early; the backend still decides.
const NAME_PATTERN = '[a-z0-9][a-z0-9._\\-]{0,63}'
const DESCRIPTION_MAX = 2000
const VALUE_MAX = 64 * 1024

const FIELDS = ['name', 'kind', 'description', 'value'] as const

/** Backend field errors by form field; the rest goes above the form.
 * Messages come from the backend envelope, which never echoes the value. */
function submitErrors(error: unknown) {
    const fields: Partial<Record<(typeof FIELDS)[number], string>> = {}
    if (!error) return { form: undefined, fields }
    if (error instanceof ApiError && error.code === 'VALIDATION_ERROR') {
        for (const field of FIELDS) {
            const message = error.details[`body.${field}`]
            if (typeof message === 'string') fields[field] = message
        }
    }
    if (error instanceof ApiError && error.kind === 'conflict')
        fields.name = error.message
    const handled = Object.keys(fields).length > 0
    return { form: handled ? undefined : errorMessage(error), fields }
}

// The secret lives only in the uncontrolled password input until submit:
// never in React state, and gone from the DOM when the dialog unmounts its
// content on close.
export function CredentialFormDialog({
    open,
    onClose,
    credential,
    pending,
    error,
    onSubmit,
}: CredentialFormDialogProps) {
    const formId = useId()
    const { form, fields } = submitErrors(error)
    const editing = credential !== undefined

    function submit(event: SubmitEvent<HTMLFormElement>) {
        event.preventDefault()
        if (pending) return
        const data = new FormData(event.currentTarget)
        const kind = credentialKindSchema.safeParse(data.get('kind'))
        onSubmit({
            name: credential?.name ?? formText(data, 'name'),
            kind: credential?.kind ?? (kind.success ? kind.data : 'generic'),
            description: formText(data, 'description'),
            value: formText(data, 'value'),
        })
    }

    return (
        <Dialog
            open={open}
            onClose={onClose}
            title={editing ? `Edit “${credential.name}”` : 'New credential'}
            dismissible={!pending}
            footer={
                <>
                    <Button onClick={onClose} disabled={pending}>
                        Cancel
                    </Button>
                    <Button
                        type="submit"
                        form={formId}
                        variant="primary"
                        loading={pending}
                    >
                        {editing ? 'Save changes' : 'Create credential'}
                    </Button>
                </>
            }
        >
            <form id={formId} onSubmit={submit} className="flex flex-col gap-4">
                {form && <Alert tone="danger">{form}</Alert>}
                {editing ? (
                    <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
                        <dt className="text-muted">Reference</dt>
                        <dd>
                            <code>{credential.reference}</code>
                        </dd>
                        <dt className="text-muted">Type</dt>
                        <dd>{kindLabels[credential.kind]}</dd>
                    </dl>
                ) : (
                    <>
                        <Input
                            label="Name"
                            name="name"
                            required
                            pattern={NAME_PATTERN}
                            maxLength={64}
                            autoComplete="off"
                            spellCheck={false}
                            description="Lowercase letters, digits, '.', '_' or '-'. Projects reference it as credential:<name>; it cannot be changed later."
                            error={fields.name}
                            data-autofocus
                        />
                        <Select
                            label="Type"
                            name="kind"
                            defaultValue="api_key"
                            error={fields.kind}
                        >
                            {credentialKindSchema.options.map((k) => (
                                <option key={k} value={k}>
                                    {kindLabels[k]}
                                </option>
                            ))}
                        </Select>
                    </>
                )}
                <Textarea
                    label="Description"
                    name="description"
                    maxLength={DESCRIPTION_MAX}
                    defaultValue={credential?.description ?? ''}
                    error={fields.description}
                />
                <Input
                    label={editing ? 'New secret value' : 'Secret value'}
                    name="value"
                    // Masked; "new-password" keeps the browser from filling
                    // in the user's saved login, and spellcheck off keeps
                    // the value away from cloud spelling services.
                    type="password"
                    autoComplete="new-password"
                    spellCheck={false}
                    autoCapitalize="off"
                    autoCorrect="off"
                    required={!editing}
                    maxLength={VALUE_MAX}
                    description={
                        editing
                            ? 'Leave empty to keep the stored value. The stored value is never shown.'
                            : 'Write-only: it is encrypted on the server and never shown again.'
                    }
                    error={fields.value}
                    {...(editing && { 'data-autofocus': true })}
                />
            </form>
        </Dialog>
    )
}
