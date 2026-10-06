import { redirect, type RouteObject } from 'react-router'
import { RequireSession } from '../features/auth/RequireSession'
import { AppLayout } from './AppLayout'
import { AppLoading } from './AppLoading'
import { NotFoundPage } from './NotFoundPage'
import { RouteErrorBoundary } from './RouteErrorBoundary'

export const routes: RouteObject[] = [
    {
        path: '/login',
        errorElement: <RouteErrorBoundary />,
        HydrateFallback: AppLoading,
        lazy: async () => {
            const { LoginPage } = await import('../features/auth/LoginPage')
            return { Component: LoginPage }
        },
    },
    {
        // Root boundary: catches failures of the layout itself. Every area
        // below needs a session (UX; the backend enforces it).
        path: '/',
        element: (
            <RequireSession>
                <AppLayout />
            </RequireSession>
        ),
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
                    {
                        path: '/monitoring',
                        lazy: async () => {
                            const { MonitoringPage } =
                                await import('../features/monitoring/MonitoringPage')
                            return { Component: MonitoringPage }
                        },
                    },
                    {
                        path: '/credentials',
                        lazy: async () => {
                            const { CredentialsPage } =
                                await import('../features/credentials/CredentialsPage')
                            return { Component: CredentialsPage }
                        },
                    },
                    {
                        path: '/plugins',
                        lazy: async () => {
                            const { PluginsPage } =
                                await import('../features/plugins/PluginsPage')
                            return { Component: PluginsPage }
                        },
                    },
                    {
                        path: '/users',
                        lazy: async () => {
                            const { UsersPage } =
                                await import('../features/users/UsersPage')
                            return { Component: UsersPage }
                        },
                    },
                    {
                        path: '/reports',
                        lazy: async () => {
                            const { ReportsPage } =
                                await import('../features/reports/ReportsPage')
                            return { Component: ReportsPage }
                        },
                    },
                    {
                        path: '/settings',
                        lazy: async () => {
                            const { SettingsPage } =
                                await import('../features/settings/SettingsPage')
                            return { Component: SettingsPage }
                        },
                    },
                    {
                        path: '/reports/:executionId',
                        lazy: async () => {
                            const { ReportDetailPage } =
                                await import('../features/reports/ReportDetailPage')
                            return { Component: ReportDetailPage }
                        },
                    },
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
                        path: '/projects/:projectId/triggers',
                        lazy: async () => {
                            const { TriggersPage } =
                                await import('../features/triggers/TriggersPage')
                            return { Component: TriggersPage }
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
