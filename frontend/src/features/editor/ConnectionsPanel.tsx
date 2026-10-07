import type { Edge, Graph, NodeInstance } from '../../api/workflows'
import { Badge, Button, Select } from '../../components/ui'
import {
    connectionProblem,
    missingInputs,
    PROBLEM_LABELS,
    type ConnectionRules,
} from './connections'
import { edgeKey, edgesOf, graphIndex, nodeTitle, portsOf } from './graphView'
import type { CardPort } from './NodeCard'
import { describeType } from './vocabulary'

/**
 * The node's inputs and outputs, each explained, with its connections and
 * a picker to add one: graph editing without pointer or sight
 * (frontend/AGENTS §18), checked by the same rules as dragging.
 */
export function ConnectionsPanel({
    node,
    graph,
    rules,
    onConnect,
    onRemove,
}: {
    node: NodeInstance
    graph: Graph
    rules: ConnectionRules
    onConnect: (edge: Edge) => void
    onRemove: (edge: Edge) => void
}) {
    const definition = rules.catalog?.get(node.node_type)
    const { inputs, outputs } = portsOf(
        node,
        definition,
        edgesOf(graph, node.id),
    )
    const missing = missingInputs(node.id, definition, edgesOf(graph, node.id))
    const title = (id: string) => {
        const n = graphIndex(graph).nodes.get(id)
        return n ? nodeTitle(n, rules.catalog?.get(n.node_type)) : id
    }
    const others = graph.nodes
        .filter((n) => n.id !== node.id)
        .map((n) => ({
            node: n,
            ports: portsOf(
                n,
                rules.catalog?.get(n.node_type),
                edgesOf(graph, n.id),
            ),
        }))

    /** Every port of the other nodes this port could connect to. */
    function candidates(port: CardPort, side: 'input' | 'output') {
        return others.flatMap(({ node: n, ports }) =>
            (side === 'input' ? ports.outputs : ports.inputs).map((p) => {
                const edge =
                    side === 'input'
                        ? {
                              source_node_id: n.id,
                              source_port: p.name,
                              target_node_id: node.id,
                              target_port: port.name,
                          }
                        : {
                              source_node_id: node.id,
                              source_port: port.name,
                              target_node_id: n.id,
                              target_port: p.name,
                          }
                return {
                    edge,
                    label: `${title(n.id)} › ${p.name}`,
                    problem: connectionProblem(graph, edge, rules),
                }
            }),
        )
    }

    // A render function, not a component: one defined in here would
    // remount on every change and drop the focus of its picker.
    function portRow(port: CardPort, side: 'input' | 'output') {
        const edges = graph.edges.filter((e) =>
            side === 'input'
                ? e.target_node_id === node.id && e.target_port === port.name
                : e.source_node_id === node.id && e.source_port === port.name,
        )
        const type = port.type === undefined ? null : describeType(port.type)
        // By side: an input and an output may share a name (file_write's
        // `path`), and only an input can be missing.
        const needed = side === 'input' && missing.includes(port.name)
        const options = candidates(port, side)
        return (
            <li
                key={port.name}
                className="flex flex-col gap-1 rounded-md border border-border p-2"
            >
                <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium">{port.name}</span>
                    {type && (
                        <span title={type.hint}>
                            <Badge>{type.label}</Badge>
                        </span>
                    )}
                    {side === 'input' &&
                        (port.required ? (
                            <Badge tone="warning">Required</Badge>
                        ) : (
                            <span className="text-xs text-muted">Optional</span>
                        ))}
                </div>
                {port.description && (
                    <p className="text-muted">{port.description}</p>
                )}
                {edges.length > 0 ? (
                    <ul className="flex flex-col gap-1">
                        {edges.map((e) => (
                            <li
                                key={edgeKey(e)}
                                className="flex items-center justify-between gap-2"
                            >
                                <span>
                                    {side === 'input'
                                        ? `From ${title(e.source_node_id)} › ${e.source_port}`
                                        : `To ${title(e.target_node_id)} › ${e.target_port}`}
                                </span>
                                <Button
                                    size="sm"
                                    variant="ghost"
                                    onClick={() => {
                                        onRemove(e)
                                    }}
                                    aria-label={`Remove connection ${title(e.source_node_id)} ${e.source_port} to ${title(e.target_node_id)} ${e.target_port}`}
                                >
                                    Remove
                                </Button>
                            </li>
                        ))}
                    </ul>
                ) : (
                    <p
                        className={
                            needed
                                ? 'font-medium text-warning-text'
                                : 'text-muted'
                        }
                    >
                        {needed
                            ? 'Not connected yet. This node cannot run without it: connect the output of a node that provides this value.'
                            : side === 'input'
                              ? 'Not connected; optional.'
                              : 'Not sent anywhere yet.'}
                    </p>
                )}
                {/* Listed even when none fits: the reasons say why. */}
                {options.length > 0 && (
                    // Connects on choice; the list above announces the result.
                    <Select
                        label={
                            side === 'input'
                                ? `Connect ${port.name} from`
                                : `Send ${port.name} to`
                        }
                        value=""
                        onChange={(event) => {
                            const chosen = options.find(
                                (o) => edgeKey(o.edge) === event.target.value,
                            )
                            if (chosen && !chosen.problem)
                                onConnect(chosen.edge)
                        }}
                    >
                        <option value="">
                            {side === 'input'
                                ? 'Choose a node output…'
                                : 'Choose a node input…'}
                        </option>
                        {options.map((o) => (
                            <option
                                key={edgeKey(o.edge)}
                                value={edgeKey(o.edge)}
                                disabled={o.problem !== null}
                            >
                                {o.label}
                                {o.problem &&
                                    ` (${PROBLEM_LABELS[o.problem.code]})`}
                            </option>
                        ))}
                    </Select>
                )}
            </li>
        )
    }

    return (
        <section
            aria-labelledby="node-connections"
            className="flex flex-col gap-3 text-sm"
        >
            <div>
                <h3 id="node-connections" className="font-semibold">
                    Connections
                </h3>
                <p className="text-muted">
                    Data flows along connections, from one node&apos;s output
                    into another node&apos;s input. On the canvas, drag from a
                    dot on the right of a node to a dot on the left of another.
                </p>
            </div>
            <div className="flex flex-col gap-2">
                <h4 className="font-medium">Receives (inputs)</h4>
                {inputs.length === 0 ? (
                    <p className="text-muted">
                        Nothing: this node starts a flow.
                    </p>
                ) : (
                    <ul className="flex flex-col gap-2">
                        {inputs.map((p) => portRow(p, 'input'))}
                    </ul>
                )}
            </div>
            <div className="flex flex-col gap-2">
                <h4 className="font-medium">Sends (outputs)</h4>
                {outputs.length === 0 ? (
                    <p className="text-muted">
                        Nothing: this node ends a flow.
                    </p>
                ) : (
                    <ul className="flex flex-col gap-2">
                        {outputs.map((p) => portRow(p, 'output'))}
                    </ul>
                )}
            </div>
        </section>
    )
}
