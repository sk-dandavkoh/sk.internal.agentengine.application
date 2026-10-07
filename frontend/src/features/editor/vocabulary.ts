import type { NodeDefinition } from '../../api/nodes'

// Plain-language names for the catalog's technical vocabulary, so the
// editor reads for people who never saw a JSON Schema. Categories, port
// types and capabilities are open sets: unknown keys are humanized.

/** "control_flow" → "Control flow". */
export const humanize = (key: string) => {
    const text = key.replaceAll('_', ' ')
    return text.charAt(0).toUpperCase() + text.slice(1)
}

const TYPES: Readonly<Record<string, { label: string; hint: string }>> = {
    string: { label: 'Text', hint: 'Text, e.g. a name, a sentence or a URL.' },
    number: { label: 'Number', hint: 'A number, e.g. 42 or 3.5.' },
    boolean: { label: 'Yes/no', hint: 'true or false.' },
    json: {
        label: 'Any',
        hint: 'Any kind of data: text, a number, a list or an object.',
    },
    object: {
        label: 'Object',
        hint: 'Named values together, e.g. { "name": "Ada", "age": 36 }.',
    },
    list: { label: 'List', hint: 'Several values in order.' },
    binary: { label: 'Binary', hint: 'Raw bytes, e.g. an image.' },
    file: { label: 'File', hint: 'A file.' },
    artifact: { label: 'Artifact', hint: 'A stored result file.' },
    secret_reference: {
        label: 'Credential',
        hint: 'A reference to a stored credential, never the secret itself.',
    },
}

/** A port signature (`string`, `json[]`) as a short label and a hint. */
export function describeType(signature: string) {
    const several = signature.endsWith('[]')
    const base = several ? signature.slice(0, -2) : signature
    const known = TYPES[base]
    const label = known?.label ?? humanize(base)
    return several
        ? {
              label: `${label} (several)`,
              hint: `Takes one value per connection. ${known?.hint ?? ''}`.trim(),
          }
        : { label, hint: known?.hint ?? label }
}

export interface Capability {
    label: string
    description: string
}

const CAPABILITIES: Readonly<Record<string, Capability>> = {
    has_side_effects: {
        label: 'Changes things outside',
        description:
            'Affects the world outside this workflow, e.g. writes files, sends messages or calls other services. If a run is retried, this can happen again.',
    },
    waits_for_input: {
        label: 'Waits for a person',
        description: 'Pauses the run until someone responds.',
    },
    is_control_flow: {
        label: 'Chooses the path',
        description:
            'Decides which of its outputs the workflow continues on; the other outputs stay idle.',
    },
    supports_cancellation: {
        label: 'Can be stopped',
        description:
            'Stops right away when a run is cancelled or times out, instead of finishing its current work first.',
    },
    streams_output: {
        label: 'Streams output',
        description: 'Produces its result piece by piece while it runs.',
    },
}

/** The node's enabled capabilities, most consequential first. */
export function capabilitiesOf(d: NodeDefinition): Capability[] {
    const order = Object.keys(CAPABILITIES)
    return Object.keys(d.capabilities)
        .filter((key) => d.capabilities[key])
        .sort((a, b) => {
            const rank = (k: string) =>
                order.includes(k) ? order.indexOf(k) : order.length
            return rank(a) - rank(b) || a.localeCompare(b)
        })
        .map(
            (key) =>
                CAPABILITIES[key] ?? { label: humanize(key), description: '' },
        )
}
