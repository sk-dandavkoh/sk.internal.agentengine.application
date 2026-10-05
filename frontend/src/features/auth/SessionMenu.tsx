import { Button, useToast } from '../../components/ui'
import { errorMessage } from '../../api/errors'
import { useLogout, useSession } from './session'

/** Who is signed in, and the way out. Rendered inside RequireSession. */
export function SessionMenu() {
    const { data } = useSession()
    const logout = useLogout()
    const toast = useToast()
    if (data?.status !== 'active') return null
    const { user } = data.session

    return (
        <div className="flex items-center justify-between gap-2 border-t border-border px-4 py-3 md:flex-col md:items-start">
            <p className="min-w-0 text-sm">
                <span className="sr-only">Signed in as </span>
                <span className="block truncate font-medium">
                    {user.username}
                </span>
                <span className="text-muted">
                    {user.role === 'admin' ? 'Administrator' : 'Member'}
                </span>
            </p>
            <Button
                size="sm"
                loading={logout.isPending}
                onClick={() => {
                    logout.mutate(undefined, {
                        onError: (error) =>
                            toast.notify({
                                tone: 'danger',
                                title: 'Could not sign out',
                                description: errorMessage(error),
                            }),
                    })
                }}
            >
                Sign out
            </Button>
        </div>
    )
}
