import { isRouteErrorResponse, useRouteError } from 'react-router'
import { ApiError } from '../api/errors'
import { Button, ErrorState } from '../components/ui'
import { NotFoundPage } from './NotFoundPage'

// Users get a stable message; the technical error stays in the console
// (React Router logs it in development) and never becomes the UI text.
// ApiError messages are user-facing by construction (api/errors.ts).
export function RouteErrorBoundary() {
    const error = useRouteError()

    if (isRouteErrorResponse(error) && error.status === 404) {
        return <NotFoundPage />
    }

    return (
        <div className="p-6">
            <ErrorState
                titleAs="h1"
                description={
                    error instanceof ApiError
                        ? error.message
                        : 'This page could not be displayed. Try reloading it.'
                }
                action={
                    <Button
                        variant="primary"
                        onClick={() => {
                            window.location.reload()
                        }}
                    >
                        Reload
                    </Button>
                }
            />
        </div>
    )
}
