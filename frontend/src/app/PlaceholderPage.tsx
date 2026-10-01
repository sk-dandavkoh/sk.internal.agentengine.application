export function PlaceholderPage({ title }: { title: string }) {
    return (
        <section>
            <h1 className="text-2xl font-semibold">{title}</h1>
            <p className="mt-2 text-slate-600">
                This area is not available yet.
            </p>
        </section>
    )
}
