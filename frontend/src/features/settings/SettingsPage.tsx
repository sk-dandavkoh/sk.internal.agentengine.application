import { useMutation } from '@tanstack/react-query'
import { useState, type SubmitEvent } from 'react'
import { changePassword } from '../../api/auth'
import { useApiClient } from '../../api/context'
import { ApiError, errorMessage } from '../../api/errors'
import {
    Alert,
    Button,
    Card,
    Input,
    PageHeader,
    Select,
    useToast,
} from '../../components/ui'
import { formText } from '../../lib/form'
import { formatDateTime } from '../../lib/format'
import { SystemStatusCard } from '../dashboard/sections'
import { useSession } from '../auth/session'
import { AccessTokensCard } from './AccessTokensCard'
import { EngineLimitsCard } from './EngineLimits'
import { readTheme, saveTheme, THEMES, type Theme } from './theme'

export function SettingsPage() {
    return (
        <>
            <PageHeader
                title="Settings"
                description="Your account, this browser's preferences and the engine."
            />
            <div className="grid gap-6 lg:grid-cols-2">
                <AccountCard />
                <PasswordCard />
                <AppearanceCard />
                <SystemStatusCard />
                <EngineLimitsCard />
                <AccessTokensCard />
            </div>
        </>
    )
}

function AccountCard() {
    const { data } = useSession()
    // Rendered behind RequireSession: always active here.
    if (data?.status !== 'active') return null
    const { user, expires_at } = data.session
    return (
        <Card title="Account">
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
                <dt className="text-muted">Username</dt>
                <dd className="font-medium">{user.username}</dd>
                <dt className="text-muted">Role</dt>
                <dd>{user.role === 'admin' ? 'Administrator' : 'Member'}</dd>
                <dt className="text-muted">Account created</dt>
                <dd>{formatDateTime(user.created_at)}</dd>
                <dt className="text-muted">Session expires</dt>
                <dd>{expires_at ? formatDateTime(expires_at) : 'Never'}</dd>
            </dl>
            <p className="mt-3 text-sm text-muted">
                Sessions end at a fixed time after sign-in; activity does not
                extend them.
            </p>
        </Card>
    )
}

const LABELS: Record<Theme, string> = {
    system: 'Match the operating system',
    light: 'Light',
    dark: 'Dark',
}

function AppearanceCard() {
    const [theme, setTheme] = useState(readTheme)
    return (
        <Card title="Appearance" description="Stored in this browser only.">
            <Select
                label="Theme"
                value={theme}
                onChange={(event) => {
                    const next = THEMES.find((t) => t === event.target.value)
                    if (!next) return
                    saveTheme(next)
                    setTheme(next)
                }}
            >
                {THEMES.map((t) => (
                    <option key={t} value={t}>
                        {LABELS[t]}
                    </option>
                ))}
            </Select>
        </Card>
    )
}

interface PasswordInput {
    current_password: string
    new_password: string
}

function PasswordCard() {
    const client = useApiClient()
    const toast = useToast()
    const [mismatch, setMismatch] = useState(false)
    const mutation = useMutation({
        mutationFn: (body: PasswordInput) => changePassword(client, body),
        // The variables hold both passwords: dropped once settled.
        gcTime: 0,
    })
    const error = mutation.error
    const wrongCurrent =
        error instanceof ApiError && error.code === 'INVALID_CREDENTIALS'

    function submit(event: SubmitEvent<HTMLFormElement>) {
        event.preventDefault()
        const form = event.currentTarget
        const data = new FormData(form)
        const next = formText(data, 'new_password')
        // UX only: the backend validates the new password itself.
        const differs = next !== formText(data, 'confirm_password')
        setMismatch(differs)
        if (differs) return
        mutation.mutate(
            {
                current_password: formText(data, 'current_password'),
                new_password: next,
            },
            {
                onSuccess: () => {
                    form.reset()
                    toast.notify({
                        tone: 'success',
                        title: 'Password changed',
                        description:
                            'Your other sessions were signed out; this one continues.',
                    })
                },
            },
        )
    }

    return (
        <Card
            title="Change password"
            description="12 to 256 characters. Signs out your other sessions."
        >
            <form
                className="flex flex-col gap-4"
                onSubmit={submit}
                aria-label="Change password"
            >
                {error && !wrongCurrent && (
                    <Alert tone="danger">{errorMessage(error)}</Alert>
                )}
                <Input
                    label="Current password"
                    name="current_password"
                    type="password"
                    autoComplete="current-password"
                    required
                    maxLength={256}
                    error={wrongCurrent ? errorMessage(error) : undefined}
                />
                <Input
                    label="New password"
                    name="new_password"
                    type="password"
                    autoComplete="new-password"
                    required
                    minLength={12}
                    maxLength={256}
                />
                <Input
                    label="Confirm new password"
                    name="confirm_password"
                    type="password"
                    autoComplete="new-password"
                    required
                    maxLength={256}
                    error={mismatch ? 'The passwords do not match.' : undefined}
                />
                <div>
                    <Button
                        type="submit"
                        variant="primary"
                        loading={mutation.isPending}
                    >
                        Change password
                    </Button>
                </div>
            </form>
        </Card>
    )
}
