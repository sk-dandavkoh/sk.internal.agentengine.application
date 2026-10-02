import type { NodeDefinition, NodePort } from '../../api/nodes'
import type { Edge, NodeInstance } from '../../api/projects'
import type { CardPort } from './NodeCard'

// Read-only views of a draft graph shared by the canvas and the node list.

export const edgeKey = (e: Edge) =>
    `${e.source_node_id}:${e.source_port}->${e.target_node_id}:${e.target_port}`

/** Backend `port_signature`: the type, `[]`-suffixed for a list port.
 * The key format of the catalog's `port_compatibility`. */
export const portSignature = (port: NodePort) =>
    port.type + (port.multiple ? '[]' : '')

export const sameEdge = (a: Edge, b: Edge) => edgeKey(a) === edgeKey(b)

export const nodeTitle = (
    node: NodeInstance,
    definition: NodeDefinition | undefined,
) => (node.label !== '' ? node.label : (definition?.name ?? node.node_type))

/** A contract port as drawn on a card. Outputs are always produced, so
 * only inputs are ever "required". */
export const cardPort = (port: NodePort, input = true): CardPort => ({
    name: port.name,
    type: portSignature(port),
    required: input && port.required,
})

/**
 * Ports to draw: the definition's, plus any an edge uses that the
 * definition lacks (unknown type, or a port the node no longer has), so
 * every stored edge stays visible. Which edges are valid is the backend's
 * call (/validate); the canvas only flags what connections.ts finds.
 */
export function portsOf(
    node: NodeInstance,
    definition: NodeDefinition | undefined,
    edges: readonly Edge[],
) {
    const inputs = (definition?.inputs ?? []).map((p) => cardPort(p))
    const outputs = (definition?.outputs ?? []).map((p) => cardPort(p, false))
    const add = (ports: CardPort[], name: string) => {
        if (!ports.some((p) => p.name === name)) ports.push({ name })
    }
    for (const e of edges) {
        if (e.target_node_id === node.id) add(inputs, e.target_port)
        if (e.source_node_id === node.id) add(outputs, e.source_port)
    }
    return { inputs, outputs }
}
