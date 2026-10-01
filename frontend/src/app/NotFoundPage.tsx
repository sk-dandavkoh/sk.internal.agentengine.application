import { Link } from 'react-router'

export function NotFoundPage() {
    return (
        <section>
            <h1 className="text-2xl font-semibold">Page not found</h1>
            <p className="mt-2 text-slate-600">
                The page you requested does not exist.
            </p>
            <Link
                to="/dashboard"
                className="mt-4 inline-block text-indigo-700 underline focus-visible:outline-2 focus-visible:outline-indigo-600"
            >
                Go to Dashboard
            </Link>
        </section>
    )
}
