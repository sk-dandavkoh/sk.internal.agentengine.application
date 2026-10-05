import { useState, type ReactNode, type SubmitEvent } from 'react'
import { Navigate, useLocation } from 'react-router'
import { ApiError, errorMessage } from '../../api/errors'
import { Alert, Button, Card, Input, LoadingState } from '../../components/ui'
import { formText } from '../../lib/form'
import { readLoginState } from './loginState'
import { useLogin, useSession, useSetup, useSetupStatus } from './session'

export function LoginPage() {
    const { data } = useSession()
    const { returnTo, expired } = readLoginState(useLocation().state)
    const status = useSetupStatus()
    const setup = useSetup()

    // Signed in (just now, or already in another tab): back to where the
    // guard intercepted, else the start page.
    if (data?.status === 'active') return <Navigate to={returnTo} replace />

    // Setup status is a convenience: if it cannot be read, sign-in still
    // works (an installation without accounts then refuses every login).
    if (status.isPending)
        return (
            <Frame title="Akasha">
                <LoadingState />
            </Frame>
        )
    if (status.data?.required)
        return (
            <Frame title="Set up Akasha">
                <SetupForm mutation={setup} />
            </Frame>
        )
    const setupTaken =
        setup.error instanceof ApiError && setup.error.kind === 'conflict'
    return (
        <Frame title="Sign in to Akasha">
            {setupTaken && (
                <Alert tone="info">
                    Setup was already completed. Sign in with an existing
                    account.
                </Alert>
            )}
            <SignInForm expired={expired} />
        </Frame>
    )
}

function Frame({ title, children }: { title: string; children: ReactNode }) {
    return (
        <main className="flex min-h-screen items-center justify-center p-4">
            <div className="flex w-full max-w-sm flex-col gap-4">
                <h1 className="text-center text-2xl font-semibold tracking-tight">
                    {title}
                </h1>
                {children}
            </div>
        </main>
    )
}

function SignInForm({ expired }: { expired: boolean }) {
    const login = useLogin()

    function submit(event: SubmitEvent<HTMLFormElement>) {
        event.preventDefault()
        const form = new FormData(event.currentTarget)
        login.mutate({
            username: formText(form, 'username').trim(),
            password: formText(form, 'password'),
        })
    }

    return (
        <>
            {expired && !login.isError && (
                <Alert tone="warning">
                    Your session has ended. Sign in again to continue.
                </Alert>
            )}
            {login.isError && (
                <Alert tone="danger">{errorMessage(login.error)}</Alert>
            )}
            <Card>
                <form
                    className="flex flex-col gap-4"
                    onSubmit={submit}
                    aria-label="Sign in"
                >
                    <UsernameInput />
                    <Input
                        label="Password"
                        name="password"
                        type="password"
                        autoComplete="current-password"
                        required
                        maxLength={256}
                    />
                    <Button
                        type="submit"
                        variant="primary"
                        loading={login.isPending}
                    >
                        Sign in
                    </Button>
                </form>
            </Card>
        </>
    )
}

function SetupForm({ mutation }: { mutation: ReturnType<typeof useSetup> }) {
    const [mismatch, setMismatch] = useState(false)

    function submit(event: SubmitEvent<HTMLFormElement>) {
        event.preventDefault()
        const form = new FormData(event.currentTarget)
        const password = formText(form, 'password')
        // UX only: the backend validates username and password itself.
        const differs = password !== formText(form, 'confirm_password')
        setMismatch(differs)
        if (differs) return
        mutation.mutate({
            username: formText(form, 'username').trim(),
            password,
        })
    }

    return (
        <>
            <p className="text-center text-sm text-muted">
                This installation has no account yet. Create the first
                administrator; further accounts are created by administrators.
            </p>
            {mutation.isError && (
                <Alert tone="danger">{errorMessage(mutation.error)}</Alert>
            )}
            <Card>
                <form
                    className="flex flex-col gap-4"
                    onSubmit={submit}
                    aria-label="Create administrator"
                >
                    <UsernameInput />
                    <Input
                        label="Password"
                        name="password"
                        type="password"
                        autoComplete="new-password"
                        description="12 to 256 characters."
                        required
                        minLength={12}
                        maxLength={256}
                    />
                    <Input
                        label="Confirm password"
                        name="confirm_password"
                        type="password"
                        autoComplete="new-password"
                        required
                        maxLength={256}
                        error={
                            mismatch ? 'The passwords do not match.' : undefined
                        }
                    />
                    <Button
                        type="submit"
                        variant="primary"
                        loading={mutation.isPending}
                    >
                        Create administrator
                    </Button>
                </form>
            </Card>
        </>
    )
}

function UsernameInput() {
    return (
        <Input
            label="Username"
            name="username"
            autoComplete="username"
            autoCapitalize="none"
            spellCheck={false}
            required
            maxLength={64}
        />
    )
}
