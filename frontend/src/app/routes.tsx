import { redirect, type RouteObject } from 'react-router'
import { AppLayout } from './AppLayout'
import { AppLoading } from './AppLoading'
import { navItems } from './navigation'
import { NotFoundPage } from './NotFoundPage'
import { RouteErrorBoundary } from './RouteErrorBoundary'

// Each area is code-split. Until its phase lands, an area points at the shared
// placeholder chunk; the feature then swaps in its own module here.
function placeholder(title: string): NonNullable<RouteObject['lazy']> {
    return async () => {
        const { PlaceholderPage } = await import('./PlaceholderPage')
        return { element: <PlaceholderPage title={title} /> }
    }
}

export const routes: RouteObject[] = [
    {
        // Root boundary: catches failures of the layout itself.
        path: '/',
        element: <AppLayout />,
        errorElement: <RouteErrorBoundary />,
        HydrateFallback: AppLoading,
        children: [
            {
                // Content boundary: a failing page keeps the shell usable.
                errorElement: <RouteErrorBoundary />,
                children: [
                    { index: true, loader: () => redirect('/dashboard') },
                    ...navItems.map((item): RouteObject => ({
                        path: item.path,
                        lazy: placeholder(item.label),
                    })),
                    {
                        path: '/projects/:projectId/editor',
                        lazy: placeholder('Editor'),
                    },
                    {
                        // Internal design-system reference; not in the nav.
                        path: '/dev/ui',
                        lazy: async () => {
                            const { UiCatalogPage } =
                                await import('./UiCatalogPage')
                            return { Component: UiCatalogPage }
                        },
                    },
                    { path: '*', element: <NotFoundPage /> },
                ],
            },
        ],
    },
]
