export interface NavItem {
    readonly label: string
    readonly path: string
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
    { label: 'Settings', path: '/settings' },
]
