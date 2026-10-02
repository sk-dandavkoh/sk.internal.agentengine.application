import { fireEvent, screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import { renderRoute, stubBackend } from '../test/app'
import { navItems } from './navigation'

// Pages that load data get 404s from the stub backend; routing is the
// subject here, not their content.
beforeEach(() => {
    stubBackend()
})

function renderAt(path: string) {
    return renderRoute(path).router
}

function pageHeading(name: string) {
    return screen.findByRole('heading', { level: 1, name })
}

describe('routes', () => {
    it('shows the loading fallback until the first page chunk resolves', async () => {
        renderAt('/dashboard')

        expect(screen.getByRole('status')).toHaveTextContent('Loading…')
        expect(await pageHeading('Dashboard')).toBeInTheDocument()
        // The page's own section loaders may still run; the route fallback is gone.
        expect(screen.queryByText('Loading…')).not.toBeInTheDocument()
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

        // The stub backend knows no project p_1.
        expect(await pageHeading('Project not found')).toBeInTheDocument()
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
