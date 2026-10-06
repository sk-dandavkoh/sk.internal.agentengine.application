export interface NavItem {
    readonly label: string
    readonly path: string
    /** Hidden from members. Navigation only: the route and the backend
     * refuse them regardless. */
    readonly adminOnly?: boolean
}

// Top-level product areas. The Editor is reached per project
// (/projects/:projectId/editor), so it has no entry of its own here.
export const navItems: readonly NavItem[] = [
    { label: 'Dashboard', path: '/dashboard' },
    { label: 'Projects', path: '/projects' },
    { label: 'Executions', path: '/executions' },
    { label: 'Monitoring', path: '/monitoring' },
    { label: 'Reports', path: '/reports' },
    { label: 'Credentials', path: '/credentials' },
    { label: 'Plugins', path: '/plugins' },
    { label: 'Users', path: '/users', adminOnly: true },
    { label: 'Settings', path: '/settings' },
]
