import { useEffect, useRef } from 'react'
import { NavLink, Outlet, useLocation, useNavigation } from 'react-router'
import { SessionMenu } from '../features/auth/SessionMenu'
import { useSession } from '../features/auth/session'
import { cx } from '../lib/cx'
import { navItems } from './navigation'

export function AppLayout() {
    const isNavigating = useNavigation().state === 'loading'
    const { pathname } = useLocation()
    const mainRef = useRef<HTMLElement>(null)
    const { data: session } = useSession()
    const admin =
        session?.status === 'active' && session.session.user.role === 'admin'
    const items = navItems.filter((item) => admin || !item.adminOnly)
    const previousPath = useRef(pathname)

    // After a client-side navigation, move focus to the new content so
    // keyboard and screen-reader users land on the page, not the old link.
    useEffect(() => {
        if (previousPath.current === pathname) return
        previousPath.current = pathname
        mainRef.current?.focus()
    }, [pathname])

    return (
        <div className="flex min-h-screen flex-col md:flex-row">
            <a
                href="#main-content"
                className="focus-ring sr-only rounded-md bg-surface px-3 py-2 shadow focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-50"
            >
                Skip to content
            </a>
            <header className="border-b border-border bg-surface md:w-56 md:shrink-0 md:border-r md:border-b-0">
                <p className="px-4 py-3 text-lg font-semibold">
                    Akasha Agent Engine
                </p>
                <nav aria-label="Main" className="overflow-x-auto">
                    <ul className="flex gap-1 px-2 pb-2 md:flex-col">
                        {items.map((item) => (
                            <li key={item.path}>
                                <NavLink
                                    to={item.path}
                                    className={({ isActive }) =>
                                        cx(
                                            'focus-ring block rounded-md px-3 py-2 text-sm whitespace-nowrap',
                                            isActive
                                                ? 'bg-surface-muted font-semibold text-fg'
                                                : 'text-muted hover:bg-surface-muted hover:text-fg',
                                        )
                                    }
                                >
                                    {item.label}
                                </NavLink>
                            </li>
                        ))}
                    </ul>
                </nav>
                <SessionMenu />
            </header>
            <div className="relative min-w-0 flex-1">
                {isNavigating && (
                    <div
                        role="status"
                        className="absolute inset-x-0 top-0 h-1 animate-pulse bg-primary motion-reduce:animate-none"
                    >
                        <span className="sr-only">Loading page…</span>
                    </div>
                )}
                <main
                    ref={mainRef}
                    id="main-content"
                    tabIndex={-1}
                    aria-busy={isNavigating}
                    className="mx-auto max-w-6xl p-4 focus:outline-none sm:p-6"
                >
                    <Outlet />
                </main>
            </div>
        </div>
    )
}
