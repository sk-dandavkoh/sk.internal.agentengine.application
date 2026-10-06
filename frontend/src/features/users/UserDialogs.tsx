import { useId, useState, type SubmitEvent } from 'react'
import { userRoleSchema, type User, type UserRole } from '../../api/auth'
import { ApiError, errorMessage } from '../../api/errors'
import { useCreateUser, useUpdateUser } from '../../api/mutations'
import {
    Alert,
    Button,
    ConfirmDialog,
    Dialog,
    Input,
    Select,
    useToast,
} from '../../components/ui'
import { formText } from '../../lib/form'
import { displayName, roleLabels } from './labels'

// Mirrors of backend domain/identity.py, so the browser stops obvious
// mistakes early; the backend still decides.
const USERNAME_PATTERN = '[a-z0-9][a-z0-9._\\-]{2,63}'
const PASSWORD_MIN = 12
const PASSWORD_MAX = 256
const DISPLAY_NAME_MAX = 100

const FIELDS = ['username', 'display_name', 'role', 'password'] as const
type FieldErrors = Partial<Record<(typeof FIELDS)[number] | 'confirm', string>>

/** Backend field errors by form field; the rest goes above the form. The
 * backend envelope never echoes a password. */
function submitErrors(error: unknown): { form?: string; fields: FieldErrors } {
    const fields: FieldErrors = {}
    if (!error) return { fields }
    if (error instanceof ApiError && error.code === 'VALIDATION_ERROR')
        for (const field of FIELDS) {
            const message = error.details[`body.${field}`]
            if (typeof message === 'string') fields[field] = message
        }
    if (error instanceof ApiError && error.code === 'USERNAME_TAKEN')
        fields.username = error.message
    const handled = Object.keys(fields).length > 0
    return handled ? { fields } : { form: errorMessage(error), fields }
}

/** Two masked fields that must match. Uncontrolled: the password lives in
 * the DOM only until submit, never in React state. */
function PasswordFields({
    label,
    errors,
}: {
    label: string
    errors: FieldErrors
}) {
    const common = {
        type: 'password',
        // "new-password": the browser must not fill in the admin's own login.
        autoComplete: 'new-password',
        spellCheck: false,
        required: true,
        minLength: PASSWORD_MIN,
        maxLength: PASSWORD_MAX,
    } as const
    return (
        <>
            <Input
                label={label}
                name="password"
                description={`At least ${String(PASSWORD_MIN)} characters. Share it with the user over a separate channel; it is never shown again.`}
                error={errors.password}
                {...common}
            />
            <Input
                label={`Confirm ${label.toLowerCase()}`}
                name="confirm"
                error={errors.confirm}
                {...common}
            />
        </>
    )
}

function readPassword(data: FormData): string | undefined {
    const password = formText(data, 'password')
    return password === formText(data, 'confirm') ? password : undefined
}

const MISMATCH = 'The passwords do not match.'

export function CreateUserDialog({ onClose }: { onClose: () => void }) {
    const create = useCreateUser()
    const toast = useToast()
    const formId = useId()
    const [mismatch, setMismatch] = useState(false)
    const { form, fields } = submitErrors(create.error)
    if (mismatch) fields.confirm = MISMATCH
    // reset() drops the mutation's variables, the only copy of the
    // password outside the form; gcTime 0 then evicts it.
    const close = () => {
        create.reset()
        onClose()
    }

    function submit(event: SubmitEvent<HTMLFormElement>) {
        event.preventDefault()
        if (create.isPending) return
        const data = new FormData(event.currentTarget)
        const password = readPassword(data)
        setMismatch(password === undefined)
        if (password === undefined) return
        const role = userRoleSchema.catch('member').parse(data.get('role'))
        const display = formText(data, 'display_name').trim()
        create.mutate(
            {
                username: formText(data, 'username'),
                password,
                role,
                ...(display && { display_name: display }),
            },
            {
                onSuccess: (user) => {
                    toast.notify({
                        tone: 'success',
                        title: `User “${user.username}” created`,
                    })
                    close()
                },
            },
        )
    }

    return (
        <Dialog
            open
            onClose={close}
            title="New user"
            dismissible={!create.isPending}
            footer={
                <>
                    <Button onClick={close} disabled={create.isPending}>
                        Cancel
                    </Button>
                    <Button
                        type="submit"
                        form={formId}
                        variant="primary"
                        loading={create.isPending}
                    >
                        Create user
                    </Button>
                </>
            }
        >
            <form
                id={formId}
                aria-label="New user"
                onSubmit={submit}
                className="flex flex-col gap-4"
            >
                {form && <Alert tone="danger">{form}</Alert>}
                <Input
                    label="Username"
                    name="username"
                    required
                    pattern={USERNAME_PATTERN}
                    maxLength={64}
                    autoComplete="off"
                    spellCheck={false}
                    autoCapitalize="off"
                    description="3–64 lowercase letters, digits, '.', '_' or '-'. Used to sign in; it cannot be changed later."
                    error={fields.username}
                    data-autofocus
                />
                <Input
                    label="Display name"
                    name="display_name"
                    maxLength={DISPLAY_NAME_MAX}
                    autoComplete="off"
                    description="Optional."
                    error={fields.display_name}
                />
                <Select
                    label="Role"
                    name="role"
                    defaultValue="member"
                    error={fields.role}
                >
                    {userRoleSchema.options.map((r) => (
                        <option key={r} value={r}>
                            {roleLabels[r]}
                        </option>
                    ))}
                </Select>
                <PasswordFields label="Initial password" errors={fields} />
            </form>
        </Dialog>
    )
}

/** Display name and role. A role change is called out before saving: it
 * takes effect on the user's very next request. */
export function EditUserDialog({
    user,
    self,
    onClose,
}: {
    user: User
    /** The signed-in admin: their own role is not offered for change. */
    self: boolean
    onClose: () => void
}) {
    const update = useUpdateUser()
    const toast = useToast()
    const formId = useId()
    const [role, setRole] = useState<UserRole>(user.role)
    const { form, fields } = submitErrors(update.error)
    const roleChanged = role !== user.role
    const close = () => {
        update.reset()
        onClose()
    }

    function submit(event: SubmitEvent<HTMLFormElement>) {
        event.preventDefault()
        if (update.isPending) return
        const data = new FormData(event.currentTarget)
        update.mutate(
            {
                id: user.id,
                input: {
                    // '' clears it on the backend.
                    display_name: formText(data, 'display_name').trim(),
                    ...(roleChanged && { role }),
                },
            },
            {
                onSuccess: () => {
                    toast.notify({
                        tone: 'success',
                        title: `User “${user.username}” updated`,
                    })
                    close()
                },
            },
        )
    }

    return (
        <Dialog
            open
            onClose={close}
            title={`Edit “${displayName(user)}”`}
            dismissible={!update.isPending}
            footer={
                <>
                    <Button onClick={close} disabled={update.isPending}>
                        Cancel
                    </Button>
                    <Button
                        type="submit"
                        form={formId}
                        variant={roleChanged ? 'danger' : 'primary'}
                        loading={update.isPending}
                    >
                        {roleChanged ? 'Change role and save' : 'Save changes'}
                    </Button>
                </>
            }
        >
            <form
                id={formId}
                aria-label="Edit user"
                onSubmit={submit}
                className="flex flex-col gap-4"
            >
                {form && <Alert tone="danger">{form}</Alert>}
                <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
                    <dt className="text-muted">Username</dt>
                    <dd>{user.username}</dd>
                </dl>
                <Input
                    label="Display name"
                    name="display_name"
                    maxLength={DISPLAY_NAME_MAX}
                    autoComplete="off"
                    defaultValue={user.display_name ?? ''}
                    description="Leave empty to show the username."
                    error={fields.display_name}
                    data-autofocus
                />
                <Select
                    label="Role"
                    name="role"
                    value={role}
                    disabled={self}
                    description={
                        self
                            ? 'You cannot change your own role; another administrator has to.'
                            : undefined
                    }
                    error={fields.role}
                    onChange={(e) => {
                        setRole(
                            userRoleSchema
                                .catch(user.role)
                                .parse(e.target.value),
                        )
                    }}
                >
                    {userRoleSchema.options.map((r) => (
                        <option key={r} value={r}>
                            {roleLabels[r]}
                        </option>
                    ))}
                </Select>
                {roleChanged && (
                    <Alert tone="warning" title="Change administrator role?">
                        {role === 'admin'
                            ? 'The user can manage users, credentials and plugins and see every workflow, starting with their next request.'
                            : 'The user loses access to administration and to workflows they do not own, starting with their next request.'}
                    </Alert>
                )}
            </form>
        </Dialog>
    )
}

export function ResetPasswordDialog({
    user,
    onClose,
}: {
    user: User
    onClose: () => void
}) {
    const update = useUpdateUser()
    const toast = useToast()
    const formId = useId()
    const [mismatch, setMismatch] = useState(false)
    const { form, fields } = submitErrors(update.error)
    if (mismatch) fields.confirm = MISMATCH
    const close = () => {
        update.reset()
        onClose()
    }

    function submit(event: SubmitEvent<HTMLFormElement>) {
        event.preventDefault()
        if (update.isPending) return
        const password = readPassword(new FormData(event.currentTarget))
        setMismatch(password === undefined)
        if (password === undefined) return
        update.mutate(
            { id: user.id, input: { password } },
            {
                onSuccess: () => {
                    toast.notify({
                        tone: 'success',
                        title: `Password of “${user.username}” reset`,
                    })
                    close()
                },
            },
        )
    }

    return (
        <Dialog
            open
            onClose={close}
            title={`Reset password of “${displayName(user)}”?`}
            description="The user's existing password is invalidated and every session and access token of theirs ends at once."
            dismissible={!update.isPending}
            footer={
                <>
                    <Button onClick={close} disabled={update.isPending}>
                        Cancel
                    </Button>
                    <Button
                        type="submit"
                        form={formId}
                        variant="danger"
                        loading={update.isPending}
                    >
                        Reset password
                    </Button>
                </>
            }
        >
            <form
                id={formId}
                aria-label="Reset password"
                onSubmit={submit}
                className="flex flex-col gap-4"
            >
                {form && <Alert tone="danger">{form}</Alert>}
                <PasswordFields label="New password" errors={fields} />
            </form>
        </Dialog>
    )
}

/** Enable or disable: the opposite of the user's current status. */
export function StatusDialog({
    user,
    onClose,
}: {
    user: User
    onClose: () => void
}) {
    const update = useUpdateUser()
    const toast = useToast()
    const disable = !user.disabled
    return (
        <ConfirmDialog
            open
            title={`${disable ? 'Disable' : 'Enable'} “${displayName(user)}”?`}
            confirmLabel={disable ? 'Disable user' : 'Enable user'}
            tone={disable ? 'danger' : 'primary'}
            pending={update.isPending}
            error={update.error ? errorMessage(update.error) : undefined}
            onCancel={onClose}
            onConfirm={() => {
                update.mutate(
                    { id: user.id, input: { disabled: disable } },
                    {
                        onSuccess: () => {
                            toast.notify({
                                tone: 'success',
                                title: `User “${user.username}” ${disable ? 'disabled' : 'enabled'}`,
                            })
                            onClose()
                        },
                    },
                )
            }}
        >
            {disable
                ? 'The user will no longer be able to sign in. Their sessions and access tokens end at once; their workflows stay.'
                : 'The user can sign in again with their password. Access tokens revoked when they were disabled stay revoked.'}
        </ConfirmDialog>
    )
}
