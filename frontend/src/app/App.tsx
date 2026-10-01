import { createBrowserRouter, RouterProvider } from 'react-router'
import { ToastProvider } from '../components/ui'
import { routes } from './routes'

// State boundaries: the URL (router) is application/navigation state.
// Server state (TanStack Query) is provided here once the API client exists
// (Phase 03/04). Feature and editor state stay inside their feature subtrees.
const router = createBrowserRouter(routes)

export function App() {
    return (
        <ToastProvider>
            <RouterProvider router={router} />
        </ToastProvider>
    )
}
