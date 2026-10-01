import { PageHeader } from '../components/ui'

export function PlaceholderPage({ title }: { title: string }) {
    return (
        <PageHeader
            title={title}
            description="This area is not available yet."
        />
    )
}
