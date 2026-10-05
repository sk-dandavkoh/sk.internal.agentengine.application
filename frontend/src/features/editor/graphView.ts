import type { NodeDefinition, NodePort } from '../../api/nodes'
import type { Edge, Graph, NodeInstance } from '../../api/projects'
import type { CardPort } from './NodeCard'

// Read-only views of a draft graph shared by the canvas and the node list.

export const edgeKey = (e: Edge) =>
    `${e.source_node_id}:${e.source_port}->${e.target_node_id}:${e.target_port}`

/** Backend `port_signature`: the type, `[]`-suffixed for a list port.
 * The key format of the catalog's `port_compatibility`. */
export const portSignature = (port: NodePort) =>
    port.type + (port.multiple ? '[]' : '')

// Field-wise, not via edgeKey: called per edge pair while connecting, and
// building two strings each time made large graphs lag.
export const sameEdge = (a: Edge, b: Edge) =>
    a.source_node_id === b.source_node_id &&
    a.source_port === b.source_port &&
    a.target_node_id === b.target_node_id &&
    a.target_port === b.target_port

export const nodeTitle = (
    node: NodeInstance,
    definition: NodeDefinition | undefined,
) => (node.label !== '' ? node.label : (definition?.name ?? node.node_type))

export interface GraphIndex {
    nodes: ReadonlyMap<string, NodeInstance>
    /** Edges into a node. */
    incoming: ReadonlyMap<string, readonly Edge[]>
    /** Edges into or out of a node. */
    touching: ReadonlyMap<string, readonly Edge[]>
}

const indexes = new WeakMap<Graph, GraphIndex>()
const none: readonly Edge[] = []

/**
 * Lookups by node id, so per-node and per-edge checks need not scan the
 * whole graph (quadratic: ~0.4 s per drag frame at the backend's 500 node
 * limit). Graphs are never mutated (the editor reducer makes a new one per
 * change), so each is indexed once.
 */
export function graphIndex(graph: Graph): GraphIndex {
    let index = indexes.get(graph)
    if (index) return index
    const nodes = new Map(graph.nodes.map((n) => [n.id, n]))
    const incoming = new Map<string, Edge[]>()
    const touching = new Map<string, Edge[]>()
    const add = (map: Map<string, Edge[]>, id: string, e: Edge) => {
        const list = map.get(id)
        if (list) list.push(e)
        else map.set(id, [e])
    }
    for (const e of graph.edges) {
        add(incoming, e.target_node_id, e)
        add(touching, e.target_node_id, e)
        if (e.source_node_id !== e.target_node_id)
            add(touching, e.source_node_id, e)
    }
    index = { nodes, incoming, touching }
    indexes.set(graph, index)
    return index
}

/** `graph`'s edges into or out of `nodeId`. */
export const edgesOf = (graph: Graph, nodeId: string) =>
    graphIndex(graph).touching.get(nodeId) ?? none

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
