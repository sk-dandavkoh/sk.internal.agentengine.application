import { describe, expect, it } from 'vitest'
import type { NodeDefinition } from '../../api/nodes'
import type { JsonObject } from '../../api/schemas'
import type { EngineLimits } from '../../api/system'
import { limitNotes } from './limitNotes'

const limits: EngineLimits = {
    execution_timeout_seconds: 3600,
    node_timeout_seconds: 900,
    node_test_timeout_seconds: 120,
    node_max_attempts: 5,
    graph_max_nodes: 500,
    graph_max_depth: 100,
    active_executions_per_workflow: 100,
    execution_concurrency: 8,
    node_output_max_bytes: 1,
    execution_output_max_bytes: 1,
    llm_timeout_seconds: 45,
    llm_max_output_tokens: 2048,
    event_trigger_max_depth: 8,
}
const node = (properties: JsonObject): NodeDefinition => ({
    node_type: 't.n',
    version: '1.0.0',
    name: 'N',
    description: '',
    category: 'ai',
    documentation_url: null,
    inputs: [],
    outputs: [],
    config_schema: { type: 'object', properties },
    capabilities: {},
})

describe('limitNotes', () => {
    it('says how long any node may run', () => {
        expect(limitNotes(limits, node({}))).toEqual([
            {
                label: 'Runs up to 15 min',
                description:
                    'per attempt, then it stops as timed out; a whole run may take up to 1 h.',
            },
        ])
    })

    it('adds the model defaults for a node that calls a model', () => {
        const notes = limitNotes(
            limits,
            node({ provider: { type: 'string', format: 'llm-provider' } }),
        )
        expect(notes[1]?.description).toBe(
            'without a timeout, a model call waits up to 45 s; without a maximum, a reply stops at 2,048 tokens, which is also the highest maximum allowed.',
        )
    })
})
