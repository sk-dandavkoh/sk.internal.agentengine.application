import { fireEvent, render, screen, within } from '@testing-library/react'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { describe, expect, it } from 'vitest'
import { navItems } from './navigation'
import { routes } from './routes'

function renderAt(path: string) {
    const router = createMemoryRouter(routes, { initialEntries: [path] })
    render(<RouterProvider router={router} />)
    return router
}

function pageHeading(name: string) {
    return screen.findByRole('heading', { level: 1, name })
}

describe('routes', () => {
    it('shows the loading fallback until the first page chunk resolves', async () => {
        renderAt('/dashboard')

        expect(screen.getByRole('status')).toHaveTextContent('Loading…')
        expect(await pageHeading('Dashboard')).toBeInTheDocument()
        expect(screen.queryByRole('status')).not.toBeInTheDocument()
    })

    it('redirects the root path to the dashboard', async () => {
        const router = renderAt('/')

        expect(await pageHeading('Dashboard')).toBeInTheDocument()
        expect(router.state.location.pathname).toBe('/dashboard')
    })

    it.each(navItems)('serves $path directly', async ({ path, label }) => {
        renderAt(path)

        expect(await pageHeading(label)).toBeInTheDocument()
    })

    it('navigates through every main area via the sidebar', async () => {
        const router = renderAt('/dashboard')
        await pageHeading('Dashboard')
        const nav = screen.getByRole('navigation', { name: 'Main' })

        for (const { label, path } of navItems) {
            const link = within(nav).getByRole('link', { name: label })
            fireEvent.click(link)

            expect(await pageHeading(label)).toBeInTheDocument()
            expect(router.state.location.pathname).toBe(path)
            expect(link).toHaveAttribute('aria-current', 'page')
        }
    })

    it('serves the per-project editor route', async () => {
        renderAt('/projects/p_1/editor')

        expect(await pageHeading('Editor')).toBeInTheDocument()
    })

    it('shows a not-found page inside the shell for unknown routes', async () => {
        const router = renderAt('/does/not/exist')

        expect(await pageHeading('Page not found')).toBeInTheDocument()
        expect(
            screen.getByRole('navigation', { name: 'Main' }),
        ).toBeInTheDocument()

        fireEvent.click(screen.getByRole('link', { name: 'Go to Dashboard' }))
        expect(await pageHeading('Dashboard')).toBeInTheDocument()
        expect(router.state.location.pathname).toBe('/dashboard')
    })
})
