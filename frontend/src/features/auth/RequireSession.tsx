import { useQueryClient } from '@tanstack/react-query'
import { useEffect, type ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router'
import { errorMessage } from '../../api/errors'
import { AppLoading } from '../../app/AppLoading'
import { Button, ErrorState } from '../../components/ui'
import type { LoginState } from './loginState'
import { forgetServerState, useSession } from './session'

/**
 * Guards every application area (routes.tsx). This is UX only: the backend
 * refuses every request without a session regardless of what renders here.
 */
export function RequireSession({ children }: { children: ReactNode }) {
    const { data, error, refetch, isFetching } = useSession()
    const location = useLocation()
    const queryClient = useQueryClient()
    const anonymous = data?.status === 'anonymous'
    useEffect(() => {
        // After the commit that unmounted the pages: nothing refetches.
        if (anonymous) forgetServerState(queryClient)
    }, [anonymous, queryClient])

    if (data?.status === 'active') return children
    if (data?.status === 'anonymous') {
        const state: LoginState = { from: location, expired: data.expired }
        return <Navigate to="/login" replace state={state} />
    }
    if (error)
        return (
            <main className="mx-auto max-w-md p-6">
                <ErrorState
                    title="Could not check your session"
                    titleAs="h1"
                    description={errorMessage(error)}
                    action={
                        <Button
                            loading={isFetching}
                            onClick={() => void refetch()}
                        >
                            Try again
                        </Button>
                    }
                />
            </main>
        )
    return <AppLoading />
}
