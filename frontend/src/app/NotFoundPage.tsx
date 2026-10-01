import { PageHeader, TextLink } from '../components/ui'

export function NotFoundPage() {
    return (
        <>
            <PageHeader
                title="Page not found"
                description="The page you requested does not exist."
            />
            <TextLink to="/dashboard">Go to Dashboard</TextLink>
        </>
    )
}
