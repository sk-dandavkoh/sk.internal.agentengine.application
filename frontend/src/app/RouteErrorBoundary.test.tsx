import { render, screen } from '@testing-library/react'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { describe, expect, it, vi } from 'vitest'
import { ApiError } from '../api/errors'
import { routes } from './routes'

// Make one area crash while rendering, so the real route tree's
// boundaries are exercised rather than a test-only tree.
vi.mock('./PlaceholderPage', () => ({
    PlaceholderPage: ({ title }: { title: string }) => {
        if (title === 'Reports') {
            throw new Error('TypeError: secret internal detail')
        }
        if (title === 'Plugins') {
            throw new ApiError({
                kind: 'network',
                message: 'Server unreachable.',
            })
        }
        return <h1>{title}</h1>
    },
}))

describe('RouteErrorBoundary', () => {
    it('contains a page crash inside the shell without leaking details', async () => {
        vi.spyOn(console, 'error').mockImplementation(() => undefined)
        vi.spyOn(console, 'warn').mockImplementation(() => undefined)
        const router = createMemoryRouter(routes, {
            initialEntries: ['/reports'],
        })
        render(<RouterProvider router={router} />)

        expect(await screen.findByRole('alert')).toHaveTextContent(
            'Something went wrong',
        )
        expect(
            screen.getByRole('navigation', { name: 'Main' }),
        ).toBeInTheDocument()
        expect(
            screen.queryByText(/secret internal detail/),
        ).not.toBeInTheDocument()
        expect(
            screen.getByRole('button', { name: 'Reload' }),
        ).toBeInTheDocument()
    })

    it('shows the user-facing message of an API failure', async () => {
        vi.spyOn(console, 'error').mockImplementation(() => undefined)
        vi.spyOn(console, 'warn').mockImplementation(() => undefined)
        const router = createMemoryRouter(routes, {
            initialEntries: ['/plugins'],
        })
        render(<RouterProvider router={router} />)

        expect(await screen.findByRole('alert')).toHaveTextContent(
            'Server unreachable.',
        )
    })
})
