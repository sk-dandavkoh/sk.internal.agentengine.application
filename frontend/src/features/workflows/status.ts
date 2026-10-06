import type { WorkflowStatus } from '../../api/workflows'

// Display only: which status a workflow has is the backend's decision.
export const workflowStatusLabels: Record<WorkflowStatus, string> = {
    draft: 'Draft',
    published: 'Published',
    active: 'Active',
    disabled: 'Disabled',
    archived: 'Archived',
}
