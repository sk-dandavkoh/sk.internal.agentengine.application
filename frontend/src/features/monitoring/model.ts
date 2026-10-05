import type { ExecutionStatus } from '../../api/executions'
import type { MonitoringSummary, MonitoringWindow } from '../../api/monitoring'
import type { Cost } from '../../api/reports'

// Display logic only. Every number comes from the backend summary; this
// file picks the window, groups statuses for display and formats units.

export const PRESETS = {
    '1h': { label: 'Last hour', ms: 3_600_000 },
    '24h': { label: 'Last 24 hours', ms: 86_400_000 },
    '7d': { label: 'Last 7 days', ms: 7 * 86_400_000 },
} as const
export type Preset = keyof typeof PRESETS
export type Range = Preset | 'custom'
export const DEFAULT_RANGE: Preset = '24h'
/** The backend's MAX_WINDOW. */
export const MAX_WINDOW_MS = 31 * 86_400_000

export function isRange(value: string | null): value is Range {
    return (
        value === 'custom' || (value !== null && Object.hasOwn(PRESETS, value))
    )
}

/** Ends at the end of the current minute, so the query key and the
 * buckets stay put until the user refreshes, and a run created this minute
 * is inside (the backend counts `created_at < until`). */
export function presetWindow(preset: Preset, now: number): MonitoringWindow {
    const until = (Math.floor(now / 60_000) + 1) * 60_000
    return {
        since: new Date(until - PRESETS[preset].ms).toISOString(),
        until: new Date(until).toISOString(),
    }
}

/** `<input type="datetime-local">` values (local time) → a window, or why
 * not. The backend checks the same rules; this only spares the round trip. */
export function customWindow(
    since: string,
    until: string,
): { window: MonitoringWindow } | { error: string } {
    const start = Date.parse(since)
    const end = Date.parse(until)
    if (Number.isNaN(start) || Number.isNaN(end))
        return { error: 'Enter a start and an end.' }
    if (end <= start) return { error: 'The end must be after the start.' }
    if (end - start > MAX_WINDOW_MS)
        return { error: 'The window may span at most 31 days.' }
    return {
        window: {
            since: new Date(start).toISOString(),
            until: new Date(end).toISOString(),
        },
    }
}

/** ISO timestamp → the local `datetime-local` value showing it. */
export function toLocalInput(iso: string): string {
    const date = new Date(iso)
    if (Number.isNaN(date.getTime())) return ''
    const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000)
    return local.toISOString().slice(0, 16)
}

// -- statuses, grouped for display -------------------------------------------

export const OUTCOMES = [
    { key: 'completed', label: 'Completed', statuses: ['completed'] },
    {
        key: 'failed',
        label: 'Failed or timed out',
        statuses: ['failed', 'timeout'],
    },
    { key: 'cancelled', label: 'Cancelled', statuses: ['cancelled'] },
    {
        key: 'unfinished',
        label: 'Not finished',
        statuses: ['created', 'queued', 'running', 'paused', 'waiting'],
    },
] as const satisfies readonly {
    key: string
    label: string
    statuses: readonly ExecutionStatus[]
}[]
export type Outcome = (typeof OUTCOMES)[number]['key']

export function outcomeCounts(
    counts: Record<ExecutionStatus, number>,
): Record<Outcome, number> {
    const sum = (statuses: readonly ExecutionStatus[]) =>
        statuses.reduce((total, s) => total + counts[s], 0)
    return {
        completed: sum(OUTCOMES[0].statuses),
        failed: sum(OUTCOMES[1].statuses),
        cancelled: sum(OUTCOMES[2].statuses),
        unfinished: sum(OUTCOMES[3].statuses),
    }
}

/**
 * Rates over *finished* runs only, with their denominator: a run still in
 * progress has no outcome yet and must not dilute either rate.
 */
export function rates(summary: MonitoringSummary) {
    const o = outcomeCounts(summary.status_counts)
    const finished = o.completed + o.failed + o.cancelled
    return { ...o, finished }
}

// -- units -------------------------------------------------------------------

const integer = new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 })

export function formatCount(n: number): string {
    return integer.format(n)
}

/** "92.3 %"; "—" without a denominator, never a fake 0 %. */
export function formatPercent(part: number, whole: number): string {
    if (whole <= 0) return '—'
    const share = (part / whole) * 100
    return `${share.toFixed(share === 0 || share === 100 ? 0 : 1)} %`
}

/** Estimated list price. Small LLM costs keep their digits instead of
 * rounding to a misleading 0.00. */
export function formatCost({ amount, currency }: Cost): string {
    try {
        return new Intl.NumberFormat(undefined, {
            style: 'currency',
            currency,
            minimumFractionDigits: 2,
            maximumFractionDigits: 6,
        }).format(amount)
    } catch {
        // Not an ISO 4217 code: the node reported its own unit; show it as is.
        return `${String(amount)} ${currency}`
    }
}

/** "1 min", "15 min", "6 h", "1 day": the timeline's bucket size. */
export function formatBucket(seconds: number): string {
    if (seconds % 86_400 === 0) {
        const days = seconds / 86_400
        return days === 1 ? '1 day' : `${String(days)} days`
    }
    if (seconds % 3_600 === 0) return `${String(seconds / 3_600)} h`
    if (seconds % 60 === 0) return `${String(seconds / 60)} min`
    return `${String(seconds)} s`
}
