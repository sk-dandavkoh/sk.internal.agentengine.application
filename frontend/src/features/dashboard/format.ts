import type { ExecutionListParams, ExecutionStatus } from '../../api/executions'
import type { Tone } from '../../components/ui'

// Display only: which statuses exist and how they move is the backend's
// state machine (AGENTS §23).
export const executionStatusDisplay: Record<
    ExecutionStatus,
    { label: string; tone: Tone }
> = {
    created: { label: 'Created', tone: 'info' },
    queued: { label: 'Queued', tone: 'info' },
    running: { label: 'Running', tone: 'info' },
    paused: { label: 'Paused', tone: 'warning' },
    waiting: { label: 'Waiting', tone: 'warning' },
    completed: { label: 'Completed', tone: 'success' },
    failed: { label: 'Failed', tone: 'danger' },
    cancelled: { label: 'Cancelled', tone: 'neutral' },
    timeout: { label: 'Timed out', tone: 'danger' },
}

/** Not finished yet: what the dashboard lists as active. */
export const ACTIVE_STATUSES = [
    'created',
    'queued',
    'running',
    'paused',
    'waiting',
] as const satisfies readonly ExecutionStatus[]

export const FAILURE_STATUSES = [
    'failed',
    'timeout',
] as const satisfies readonly ExecutionStatus[]

// The dashboard's execution lists.
export const activeExecutions: ExecutionListParams = {
    status: ACTIVE_STATUSES,
    limit: 10,
}
export const recentFailures: ExecutionListParams = {
    status: FAILURE_STATUSES,
    limit: 5,
}
export const recentExecutions: ExecutionListParams = { limit: 10 }

/** "850 ms", "42 s", "3 min 5 s", "2 h 4 min". */
export function formatDuration(startIso: string, endIso: string): string {
    return formatMilliseconds(Date.parse(endIso) - Date.parse(startIso))
}

export function formatMilliseconds(duration: number): string {
    const ms = Math.max(0, duration)
    if (ms < 1000) return `${String(ms)} ms`
    const s = Math.floor(ms / 1000)
    if (s < 60) return `${String(s)} s`
    const min = Math.floor(s / 60)
    if (min < 60) return `${String(min)} min ${String(s % 60)} s`
    return `${String(Math.floor(min / 60))} h ${String(min % 60)} min`
}

// The project page size the backend allows at most. Dashboard project
// counts cover this many projects; beyond it the counts say so.
export const PROJECT_SAMPLE = { limit: 100 }
