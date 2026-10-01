import { NavLink, Outlet, useNavigation } from 'react-router'
import { navItems } from './navigation'

export function AppLayout() {
    const isNavigating = useNavigation().state === 'loading'

    return (
        <div className="flex min-h-screen flex-col bg-slate-50 text-slate-900 md:flex-row">
            <a
                href="#main-content"
                className="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:rounded focus:bg-white focus:px-3 focus:py-2"
            >
                Skip to content
            </a>
            <header className="border-b border-slate-200 bg-white md:w-56 md:shrink-0 md:border-r md:border-b-0">
                <p className="px-4 py-3 text-lg font-semibold">
                    Akasha Agent Engine
                </p>
                <nav aria-label="Main" className="overflow-x-auto">
                    <ul className="flex gap-1 px-2 pb-2 md:flex-col">
                        {navItems.map((item) => (
                            <li key={item.path}>
                                <NavLink
                                    to={item.path}
                                    className={({ isActive }) =>
                                        `block rounded px-3 py-2 whitespace-nowrap focus-visible:outline-2 focus-visible:outline-indigo-600 ${
                                            isActive
                                                ? 'bg-indigo-50 font-semibold text-indigo-800'
                                                : 'text-slate-700 hover:bg-slate-100'
                                        }`
                                    }
                                >
                                    {item.label}
                                </NavLink>
                            </li>
                        ))}
                    </ul>
                </nav>
            </header>
            <div className="relative flex-1">
                {isNavigating && (
                    <div
                        role="status"
                        className="absolute inset-x-0 top-0 h-1 animate-pulse bg-indigo-600 motion-reduce:animate-none"
                    >
                        <span className="sr-only">Loading page…</span>
                    </div>
                )}
                <main
                    id="main-content"
                    tabIndex={-1}
                    aria-busy={isNavigating}
                    className="p-6 focus:outline-none"
                >
                    <Outlet />
                </main>
            </div>
        </div>
    )
}
