import type { ProjectStatus } from '../../api/projects'
import { Badge, type Tone } from '../../components/ui'
import { projectStatusLabels } from './status'

const tones: Record<ProjectStatus, Tone> = {
    draft: 'neutral',
    published: 'info',
    active: 'success',
    disabled: 'warning',
    archived: 'neutral',
}

export function ProjectStatusBadge({ status }: { status: ProjectStatus }) {
    return (
        <Badge tone={tones[status]} dot>
            {projectStatusLabels[status]}
        </Badge>
    )
}
