import { QueryClientProvider } from '@tanstack/react-query'
import { createBrowserRouter, RouterProvider } from 'react-router'
import { ApiClient } from '../api/client'
import { ApiClientContext } from '../api/context'
import { createQueryClient } from '../api/queryClient'
import { ToastProvider } from '../components/ui'
import { routes } from './routes'

// State boundaries: the URL (router) is application/navigation state.
// Server state lives in TanStack Query, fetched through the one ApiClient.
// Feature and editor state stay inside their feature subtrees.
const router = createBrowserRouter(routes)
// The session token source is wired in by the auth feature (Phase 21).
const apiClient = new ApiClient()
const queryClient = createQueryClient()

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
