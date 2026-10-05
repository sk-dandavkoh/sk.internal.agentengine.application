import { QueryClientProvider } from '@tanstack/react-query'
import { createBrowserRouter, RouterProvider } from 'react-router'
import { ApiClient } from '../api/client'
import { ApiClientContext } from '../api/context'
import { createQueryClient } from '../api/queryClient'
import { ToastProvider } from '../components/ui'
import { expireSession } from '../features/auth/session'
import { routes } from './routes'

// State boundaries: the URL (router) is application/navigation state.
// Server state lives in TanStack Query, fetched through the one ApiClient.
// Feature and editor state stay inside their feature subtrees.
const router = createBrowserRouter(routes)
const queryClient = createQueryClient()
// The session is an HttpOnly cookie; a request that finds it dead ends
// the UI session, whichever feature made it.
const apiClient = new ApiClient({
    onSessionEnded: () => {
        expireSession(queryClient)
    },
})

export function App() {
    return (
        <ApiClientContext value={apiClient}>
            <QueryClientProvider client={queryClient}>
                <ToastProvider>
                    <RouterProvider router={router} />
                </ToastProvider>
            </QueryClientProvider>
        </ApiClientContext>
    )
}
