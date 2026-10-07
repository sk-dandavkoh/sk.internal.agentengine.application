import type { NodeDefinition } from '../../api/nodes'
import type { EngineLimits } from '../../api/system'
import { formatSeconds } from '../../lib/format'
import { properties } from './configSchema'
import type { Capability } from './vocabulary'

/**
 * The installation limits that apply to one node, as "Good to know" notes:
 * how long it may run, and for nodes that call an AI model the defaults a
 * call gets when the node leaves them empty.
 */
export function limitNotes(
    limits: EngineLimits,
    definition: NodeDefinition | undefined,
): Capability[] {
    const notes: Capability[] = [
        {
            label: `Runs up to ${formatSeconds(limits.node_timeout_seconds)}`,
            description: `per attempt, then it stops as timed out; a whole run may take up to ${formatSeconds(limits.execution_timeout_seconds)}.`,
        },
    ]
    const callsAModel =
        definition !== undefined &&
        properties(definition.config_schema).some(
            (p) => p.spec.kind === 'provider',
        )
    if (callsAModel)
        notes.push({
            label: 'Model defaults',
            description: `without a timeout, a model call waits up to ${formatSeconds(limits.llm_timeout_seconds)}; without a maximum, a reply stops at ${limits.llm_max_output_tokens.toLocaleString()} tokens, which is also the highest maximum allowed.`,
        })
    return notes
}
