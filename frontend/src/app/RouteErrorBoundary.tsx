import { isRouteErrorResponse, useRouteError } from 'react-router'
import { NotFoundPage } from './NotFoundPage'

// Users get a stable message; the technical error stays in the console
// (React Router logs it in development) and never becomes the UI text.
export function RouteErrorBoundary() {
    const error = useRouteError()

    if (isRouteErrorResponse(error) && error.status === 404) {
        return <NotFoundPage />
    }

    return (
        <section role="alert" className="p-6">
            <h1 className="text-2xl font-semibold">Something went wrong</h1>
            <p className="mt-2 text-slate-600">
                This page could not be displayed. Try reloading it.
            </p>
            <button
                type="button"
                onClick={() => {
                    window.location.reload()
                }}
                className="mt-4 rounded bg-indigo-700 px-3 py-1.5 text-white hover:bg-indigo-800 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600"
            >
                Reload
            </button>
        </section>
    )
}
