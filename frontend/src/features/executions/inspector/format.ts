import type { ExecutionReport, NodeExecutionStatus } from '../../../api/reports'
import type { Tone } from '../../../components/ui'
import { formatMilliseconds } from '../../dashboard/format'

// Display only: node states and their transitions are the backend's.
export const nodeStatusDisplay: Record<
    NodeExecutionStatus,
    { label: string; tone: Tone }
> = {
    pending: { label: 'Pending', tone: 'neutral' },
    ready: { label: 'Ready', tone: 'info' },
    running: { label: 'Running', tone: 'info' },
    waiting: { label: 'Waiting for input', tone: 'warning' },
    completed: { label: 'Completed', tone: 'success' },
    failed: { label: 'Failed', tone: 'danger' },
    skipped: { label: 'Skipped', tone: 'neutral' },
    cancelled: { label: 'Cancelled', tone: 'neutral' },
}

export const ms = (value: number | null) =>
    value === null ? '—' : formatMilliseconds(value)

// Rendering megabytes of text into the DOM freezes the tab; the rest is
// one click away (and in the downloadable report).
export const MAX_RENDERED_CHARS = 20_000

// The backend's limit for a log search (`q`).
export const MAX_SEARCH_CHARS = 200

// The report carries up to 10,000 events; render them in steps so a long
// history cannot stall the page.
export const EVENT_STEP = 200

/** "Fetch leads" for a node of this execution; the id when unknown. */
export function nodeNamer(report: ExecutionReport) {
    const labels = new Map(
        report.nodes.map((n) => [n.node_id, n.label || n.node_id]),
    )
    return (nodeId: string) => labels.get(nodeId) ?? nodeId
}
