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
                    {
                        path: '/dashboard',
                        lazy: async () => {
                            const { DashboardPage } =
                                await import('../features/dashboard/DashboardPage')
                            return { Component: DashboardPage }
                        },
                    },
                    {
                        path: '/projects',
                        lazy: async () => {
                            const { ProjectsPage } =
                                await import('../features/projects/ProjectsPage')
                            return { Component: ProjectsPage }
                        },
                    },
                    {
                        path: '/projects/:projectId',
                        lazy: async () => {
                            const { ProjectDetailPage } =
                                await import('../features/projects/ProjectDetailPage')
                            return { Component: ProjectDetailPage }
                        },
                    },
                    {
                        path: '/executions',
                        lazy: async () => {
                            const { ExecutionsPage } =
                                await import('../features/executions/ExecutionsPage')
                            return { Component: ExecutionsPage }
                        },
                    },
                    {
                        path: '/executions/:executionId',
                        lazy: async () => {
                            const { ExecutionDetailPage } =
                                await import('../features/executions/ExecutionDetailPage')
                            return { Component: ExecutionDetailPage }
                        },
                    },
                    ...navItems
                        .filter(
                            (item) =>
                                ![
                                    '/dashboard',
                                    '/projects',
                                    '/executions',
                                ].includes(item.path),
                        )
                        .map((item): RouteObject => ({
                            path: item.path,
                            lazy: placeholder(item.label),
                        })),
                    {
                        path: '/projects/:projectId/versions',
                        lazy: async () => {
                            const { VersionsPage } =
                                await import('../features/versions/VersionsPage')
                            return { Component: VersionsPage }
                        },
                    },
                    {
                        path: '/projects/:projectId/versions/:version',
                        lazy: async () => {
                            const { VersionDetailPage } =
                                await import('../features/versions/VersionDetailPage')
                            return { Component: VersionDetailPage }
                        },
                    },
                    {
                        path: '/projects/:projectId/editor',
                        lazy: async () => {
                            const { EditorPage } =
                                await import('../features/editor/EditorPage')
                            return { Component: EditorPage }
                        },
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
