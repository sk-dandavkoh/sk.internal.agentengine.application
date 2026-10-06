import { describe, expect, it } from 'vitest'
import { describeEvent } from './useLiveExecution'

const at = (
    event_type: string,
    payload: Record<string, string | number> = {},
) => describeEvent({ event_type, node_id: 'review', payload })

describe('describeEvent for waiting executions', () => {
    it('names the wait and how it ended', () => {
        expect(at('execution.waiting')).toBe('Execution waiting for input')
        expect(at('node.waiting', { attempt: 1 })).toBe(
            'Node review waiting for input',
        )
        expect(at('node.resumed', { outcome: 'responded' })).toBe(
            'Node review answered',
        )
        expect(at('node.resumed', { outcome: 'timed_out' })).toBe(
            'Node review continued after its wait timed out',
        )
    })

    it('falls back to the raw type for events it does not know', () => {
        expect(at('node.something_new')).toBe('node.something_new')
    })
})
