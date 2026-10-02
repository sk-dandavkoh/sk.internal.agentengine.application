import type { NodeDefinition } from '../../api/nodes'
import type { Edge, NodeInstance } from '../../api/projects'

// Read-only views of a draft graph shared by the canvas and the node list.

export const edgeKey = (e: Edge) =>
    `${e.source_node_id}:${e.source_port}->${e.target_node_id}:${e.target_port}`

export const nodeTitle = (
    node: NodeInstance,
    definition: NodeDefinition | undefined,
) => (node.label !== '' ? node.label : (definition?.name ?? node.node_type))

/**
 * Port names to draw: the definition's, plus any an edge uses that the
 * definition lacks (unknown type, or a port the node no longer has), so
 * every stored edge stays visible. Which edges are valid is the backend's
 * call (/validate), not the canvas's.
 */
export function portsOf(
    node: NodeInstance,
    definition: NodeDefinition | undefined,
    edges: readonly Edge[],
) {
    const inputs = new Set(definition?.inputs.map((p) => p.name))
    const outputs = new Set(definition?.outputs.map((p) => p.name))
    for (const e of edges) {
        if (e.target_node_id === node.id) inputs.add(e.target_port)
        if (e.source_node_id === node.id) outputs.add(e.source_port)
    }
    return { inputs: [...inputs], outputs: [...outputs] }
}
