import { QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import { createMemoryRouter, Link, RouterProvider } from 'react-router'
import { beforeEach, describe, expect, it } from 'vitest'
import { ApiClient } from '../api/client'
import { ApiClientContext } from '../api/context'
import { createQueryClient } from '../api/queryClient'
import { ToastProvider } from '../components/ui'
import { stubBackend } from '../test/app'
import { AppLayout } from './AppLayout'

beforeEach(() => {
    stubBackend()
})

// The layout alone: its session menu needs the API and query providers.
function renderLayout(router: ReturnType<typeof createMemoryRouter>) {
    render(
        <ApiClientContext value={new ApiClient()}>
            <QueryClientProvider client={createQueryClient()}>
                <ToastProvider>
                    <RouterProvider router={router} />
                </ToastProvider>
            </QueryClientProvider>
        </ApiClientContext>,
    )
}

describe('AppLayout', () => {
    it('signals a pending navigation until the next page is ready', async () => {
        let resolve: (value: null) => void = () => undefined
        const promise = new Promise<null>((r) => {
            resolve = r
        })
        const router = createMemoryRouter(
            [
                {
                    element: <AppLayout />,
                    children: [
                        { path: '/', element: <Link to="/slow">Go slow</Link> },
                        {
                            path: '/slow',
                            loader: () => promise,
                            element: <h1>Slow page</h1>,
                        },
                    ],
                },
            ],
            { initialEntries: ['/'] },
        )
        renderLayout(router)

        fireEvent.click(await screen.findByRole('link', { name: 'Go slow' }))

        expect(await screen.findByRole('status')).toHaveTextContent(
            'Loading page…',
        )
        expect(screen.getByRole('main')).toHaveAttribute('aria-busy', 'true')

        resolve(null)

        expect(
            await screen.findByRole('heading', { name: 'Slow page' }),
        ).toBeInTheDocument()
        expect(screen.queryByRole('status')).not.toBeInTheDocument()
        expect(screen.getByRole('main')).toHaveAttribute('aria-busy', 'false')
    })

    it('moves focus to the main content after navigation, not on first load', async () => {
        const router = createMemoryRouter(
            [
                {
                    element: <AppLayout />,
                    children: [
                        { path: '/', element: <Link to="/next">Next</Link> },
                        { path: '/next', element: <h1>Next page</h1> },
                    ],
                },
            ],
            { initialEntries: ['/'] },
        )
        renderLayout(router)
        const link = await screen.findByRole('link', { name: 'Next' })
        expect(screen.getByRole('main')).not.toHaveFocus()

        link.focus()
        fireEvent.click(link)

        expect(
            await screen.findByRole('heading', { name: 'Next page' }),
        ).toBeInTheDocument()
        expect(screen.getByRole('main')).toHaveFocus()
    })
})
