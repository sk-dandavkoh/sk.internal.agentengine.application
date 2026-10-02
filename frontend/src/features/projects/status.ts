import type { ProjectStatus } from '../../api/projects'

// Display only: which status a project has is the backend's decision.
export const projectStatusLabels: Record<ProjectStatus, string> = {
    draft: 'Draft',
    published: 'Published',
    active: 'Active',
    disabled: 'Disabled',
    archived: 'Archived',
}
