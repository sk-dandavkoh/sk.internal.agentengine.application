import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { useApiClient } from '../../api/context'
import type { ExecutionEvent } from '../../api/executions'
import { subscribeExecution, type LiveState } from '../../api/live'
import { queryKeys } from '../../api/queries'

/** Activity kept in memory; the full history is the events endpoint. */
export const MAX_ACTIVITY = 200

/**
 * Live updates for a running execution. Events only trigger refetches of
 * the execution's queries (detail, report, events): what the page shows is
 * always the persisted state, the stream just says when to look again.
 */
export function useLiveExecution(executionId: string, active: boolean) {
    const client = useApiClient()
    const queryClient = useQueryClient()
    // Decided once: a stream, once started, runs until the server ends it
    // (after the last event), not until a refetch happens to see the end.
    const [follow] = useState(active)
    const [state, setState] = useState<LiveState | 'idle'>('idle')
    const [events, setEvents] = useState<ExecutionEvent[]>([])

    useEffect(() => {
        if (!follow) return
        const reconcile = () =>
            void queryClient.invalidateQueries({
                queryKey: queryKeys.executions.detail(executionId),
            })
        return subscribeExecution(client, executionId, {
            onState: setState,
            onSubscribed: reconcile,
            onEvents: (fresh) => {
                setEvents((seen) => [...seen, ...fresh].slice(-MAX_ACTIVITY))
                reconcile()
            },
            onEnd: () => {
                reconcile()
                void queryClient.invalidateQueries({
                    queryKey: queryKeys.executions.overview(),
                })
            },
        })
    }, [client, queryClient, executionId, follow])

    return { state, events }
}

const num = (v: unknown) => (typeof v === 'number' ? String(v) : undefined)
const str = (v: unknown) => (typeof v === 'string' ? v : undefined)

/** One line per backend event type; unknown types are shown, not guessed. */
export function describeEvent({
    event_type,
    node_id,
    payload,
}: ExecutionEvent) {
    const node = `Node ${node_id ?? '?'}`
    const attempt = num(payload['attempt'])
    const ofAttempt = attempt ? ` (attempt ${attempt})` : ''
    const code = str(payload['error_code'])
    const because = code ? `: ${code}` : ''
    switch (event_type) {
        case 'execution.created':
            return 'Execution created'
        case 'execution.started':
            return 'Execution started'
        case 'execution.completed':
            return 'Execution completed'
        case 'execution.failed':
            return `Execution failed${because}`
        case 'execution.cancelled':
            return 'Execution cancelled'
        case 'execution.timed_out':
            return `Execution timed out${because}`
        case 'execution.recovered':
            return 'Execution recovered after its worker stopped'
        case 'execution.resumed':
            return 'Execution resumed'
        case 'node.started':
            return `${node} started${ofAttempt}`
        case 'node.completed':
            return `${node} completed${ofAttempt}`
        case 'node.failed':
            return `${node} failed${ofAttempt}${because}`
        case 'node.retry_scheduled':
            return `${node} failed${ofAttempt}${because}; retry in ${num(payload['backoff_ms']) ?? '?'} ms`
        case 'node.skipped':
            return `${node} skipped`
        case 'node.cancelled':
            return `${node} cancelled`
        case 'node.emitted':
            return `${node} emitted ${str(payload['name']) ?? 'an event'}`
        default:
            return event_type
    }
}
