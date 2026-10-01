import { fireEvent, render, screen } from '@testing-library/react'
import { createMemoryRouter, Link, RouterProvider } from 'react-router'
import { describe, expect, it } from 'vitest'
import { AppLayout } from './AppLayout'

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
        render(<RouterProvider router={router} />)

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
        render(<RouterProvider router={router} />)
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
