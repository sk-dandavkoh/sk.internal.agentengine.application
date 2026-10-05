import type { SubmitEvent } from 'react'
import { Navigate, useLocation } from 'react-router'
import { errorMessage } from '../../api/errors'
import { Alert, Button, Card, Input } from '../../components/ui'
import { formText } from '../../lib/form'
import { readLoginState } from './loginState'
import { useLogin, useSession } from './session'

export function LoginPage() {
    const { data } = useSession()
    const { returnTo, expired } = readLoginState(useLocation().state)
    const login = useLogin()

    // Signed in (just now, or already in another tab): back to where the
    // guard intercepted, else the start page.
    if (data?.status === 'active') return <Navigate to={returnTo} replace />

    function submit(event: SubmitEvent<HTMLFormElement>) {
        event.preventDefault()
        const form = new FormData(event.currentTarget)
        login.mutate({
            username: formText(form, 'username').trim(),
            password: formText(form, 'password'),
        })
    }

    return (
        <main className="flex min-h-screen items-center justify-center p-4">
            <div className="flex w-full max-w-sm flex-col gap-4">
                <h1 className="text-center text-2xl font-semibold tracking-tight">
                    Sign in to Akasha
                </h1>
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
                        <Input
                            label="Username"
                            name="username"
                            autoComplete="username"
                            autoCapitalize="none"
                            spellCheck={false}
                            required
                            maxLength={64}
                        />
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
            </div>
        </main>
    )
}
