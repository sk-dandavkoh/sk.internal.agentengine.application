import type { WorkflowStatus } from '../../api/workflows'
import { Badge, type Tone } from '../../components/ui'
import { workflowStatusLabels } from './status'

const tones: Record<WorkflowStatus, Tone> = {
    draft: 'neutral',
    published: 'info',
    active: 'success',
    disabled: 'warning',
    archived: 'neutral',
}

export function WorkflowStatusBadge({ status }: { status: WorkflowStatus }) {
    return (
        <Badge tone={tones[status]} dot>
            {workflowStatusLabels[status]}
        </Badge>
    )
}
