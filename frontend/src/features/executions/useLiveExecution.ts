import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { useApiClient } from '../../api/context'
import type { ExecutionEvent } from '../../api/executions'
import { subscribeExecution, type LiveState } from '../../api/live'
import { queryKeys } from '../../api/queries'

/** Activity kept in memory; the full history is the events endpoint. */
export const MAX_ACTIVITY = 200

/** At most one refetch of the execution's queries per this many ms. */
export const RECONCILE_INTERVAL_MS = 1_000

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
        const refetch = () =>
            void queryClient.invalidateQueries({
                queryKey: queryKeys.executions.detail(executionId),
            })
        // A busy run sends events every poll tick, and each refetch reloads
        // the report and the loaded log pages. At most one refetch per
        // window: the first event refetches at once, later ones in the
        // window are folded into one refetch at its end.
        let timer: ReturnType<typeof setTimeout> | undefined
        let pending = false
        const reconcile = () => {
            if (timer !== undefined) {
                pending = true
                return
            }
            refetch()
            timer = setTimeout(() => {
                timer = undefined
                if (pending) {
                    pending = false
                    reconcile()
                }
            }, RECONCILE_INTERVAL_MS)
        }
        const unsubscribe = subscribeExecution(client, executionId, {
            onState: setState,
            onSubscribed: reconcile,
            onEvents: (fresh) => {
                setEvents((seen) => [...seen, ...fresh].slice(-MAX_ACTIVITY))
                reconcile()
            },
            onEnd: () => {
                // The final state: never deferred.
                clearTimeout(timer)
                timer = undefined
                pending = false
                refetch()
                void queryClient.invalidateQueries({
                    queryKey: queryKeys.executions.overview(),
                })
            },
        })
        return () => {
            clearTimeout(timer)
            unsubscribe()
        }
    }, [client, queryClient, executionId, follow])

    return { state, events }
}

const num = (v: unknown) => (typeof v === 'number' ? String(v) : undefined)
const str = (v: unknown) => (typeof v === 'string' ? v : undefined)

/** One line per backend event type; unknown types are shown, not guessed. */
export function describeEvent(
    {
        event_type,
        node_id,
        payload,
    }: Pick<ExecutionEvent, 'event_type' | 'node_id' | 'payload'>,
    nodeName: (nodeId: string) => string = (id) => id,
) {
    const node = `Node ${node_id === null ? '?' : nodeName(node_id)}`
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
        case 'execution.waiting':
            return 'Execution waiting for input'
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
        case 'node.waiting':
            return `${node} waiting for input`
        case 'node.resumed':
            return str(payload['outcome']) === 'timed_out'
                ? `${node} continued after its wait timed out`
                : `${node} answered`
        case 'node.emitted':
            return `${node} emitted ${str(payload['name']) ?? 'an event'}`
        default:
            return event_type
    }
}
