import type { NodeCatalog, NodeDefinition } from '../../api/nodes'
import type { Edge, Graph } from '../../api/projects'
import { graphIndex, nodeTitle, portSignature, sameEdge } from './graphView'

// Whether an edge may be drawn, from the backend's node contracts and its
// published port rule (`port_compatibility`). A UX pre-check only: the
// backend re-validates the graph at validate/publish (frontend/AGENTS §32).

export interface ConnectionRules {
    /** undefined: the catalog did not load, so no port can be checked. */
    catalog: ReadonlyMap<string, NodeDefinition> | undefined
    compatibility: NodeCatalog['port_compatibility'] | undefined
}

export type ConnectionProblemCode =
    | 'self'
    | 'unknown_node'
    | 'unchecked'
    | 'unknown_type'
    | 'unknown_port'
    | 'duplicate'
    | 'occupied'
    | 'incompatible'

export interface ConnectionProblem {
    code: ConnectionProblemCode
    message: string
}

/** Short reason, for places with room for a few words only. */
export const PROBLEM_LABELS: Readonly<Record<ConnectionProblemCode, string>> = {
    self: 'same node',
    unknown_node: 'unknown node',
    unchecked: 'catalog not loaded',
    unknown_type: 'unknown node type',
    unknown_port: 'unknown port',
    duplicate: 'already connected',
    occupied: 'input already connected',
    incompatible: 'incompatible type',
}

/**
 * Why `edge` cannot be added to `graph`, or null if it can. `replacing` is
 * an edge being reconnected: it is left out, as it will be gone.
 */
export function connectionProblem(
    graph: Graph,
    edge: Edge,
    { catalog, compatibility }: ConnectionRules,
    replacing?: Edge,
): ConnectionProblem | null {
    const problem = (code: ConnectionProblemCode, message: string) => ({
        code,
        message,
    })
    // The backend refuses both outright, so they would make the draft
    // unsavable, not just invalid.
    if (edge.source_node_id === edge.target_node_id)
        return problem('self', 'A node cannot be connected to itself.')
    const index = graphIndex(graph)
    const source = index.nodes.get(edge.source_node_id)
    const target = index.nodes.get(edge.target_node_id)
    if (!source || !target)
        return problem('unknown_node', 'The node is no longer in the draft.')
    // Both checks below concern edges into the target only.
    const into = (index.incoming.get(edge.target_node_id) ?? []).filter(
        (e) => !replacing || !sameEdge(e, replacing),
    )
    if (into.some((e) => sameEdge(e, edge)))
        return problem('duplicate', 'These ports are already connected.')
    if (!catalog || !compatibility)
        return problem(
            'unchecked',
            'The node catalog is not loaded, so ports cannot be checked.',
        )
    const sourceDef = catalog.get(source.node_type)
    const targetDef = catalog.get(target.node_type)
    const unknown = !sourceDef ? source : !targetDef ? target : null
    if (unknown)
        return problem(
            'unknown_type',
            `"${unknown.node_type}" is not a registered node type, so its ports are unknown.`,
        )
    const output = sourceDef?.outputs.find((p) => p.name === edge.source_port)
    const input = targetDef?.inputs.find((p) => p.name === edge.target_port)
    if (!output)
        return problem(
            'unknown_port',
            `${nodeTitle(source, sourceDef)} has no output "${edge.source_port}".`,
        )
    if (!input)
        return problem(
            'unknown_port',
            `${nodeTitle(target, targetDef)} has no input "${edge.target_port}".`,
        )
    if (!input.multiple && into.some((e) => e.target_port === edge.target_port))
        return problem(
            'occupied',
            `Input "${input.name}" of ${nodeTitle(target, targetDef)} takes one connection and already has it. Delete or reconnect that one first.`,
        )
    if (!compatibility[portSignature(output)]?.includes(portSignature(input)))
        return problem(
            'incompatible',
            `Output "${output.name}" (${portSignature(output)}) cannot feed input "${input.name}" (${portSignature(input)}).`,
        )
    return null
}

/** Required inputs of `node` without an incoming edge. */
export function missingInputs(
    nodeId: string,
    definition: NodeDefinition | undefined,
    edges: readonly Edge[],
) {
    return (definition?.inputs ?? [])
        .filter(
            (p) =>
                p.required &&
                !edges.some(
                    (e) =>
                        e.target_node_id === nodeId && e.target_port === p.name,
                ),
        )
        .map((p) => p.name)
}
