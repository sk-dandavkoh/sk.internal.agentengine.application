import type { ExecutionEvent } from '../../api/executions'
import type { LiveState } from '../../api/live'
import { Badge, type Tone } from '../../components/ui'
import { Time } from './shared'
import { describeEvent } from './useLiveExecution'

const liveDisplay: Record<
    LiveState,
    { label: string; tone: Tone; hint?: string }
> = {
    connecting: { label: 'Connecting', tone: 'info' },
    live: { label: 'Live', tone: 'success' },
    reconnecting: {
        label: 'Reconnecting',
        tone: 'warning',
        hint: 'Live updates are paused; the page may be out of date.',
    },
    ended: { label: 'Live updates finished', tone: 'neutral' },
    unauthenticated: {
        label: 'Live updates unavailable',
        tone: 'warning',
        hint: 'Your session is not valid for live updates. Use Refresh.',
    },
    unavailable: {
        label: 'Live updates unavailable',
        tone: 'warning',
        hint: 'Use Refresh to update the page.',
    },
}

/** Says whether the page is live; never "Live" without a subscription. */
export function LiveIndicator({ state }: { state: LiveState | 'idle' }) {
    if (state === 'idle') return null
    const { label, tone, hint } = liveDisplay[state]
    return (
        <p role="status" className="flex flex-wrap items-center gap-2 text-sm">
            <Badge tone={tone} dot>
                {label}
            </Badge>
            {hint && <span className="text-muted">{hint}</span>}
        </p>
    )
}

export function LiveActivity({ events }: { events: ExecutionEvent[] }) {
    if (events.length === 0)
        return <p className="text-sm text-muted">No events received yet.</p>
    return (
        <ol aria-label="Live activity" className="flex flex-col gap-1 text-sm">
            {events.map((e) => (
                <li key={e.event_id} className="flex gap-3">
                    <span className="text-muted">
                        <Time iso={e.occurred_at} />
                    </span>
                    <span>{describeEvent(e)}</span>
                </li>
            ))}
        </ol>
    )
}
