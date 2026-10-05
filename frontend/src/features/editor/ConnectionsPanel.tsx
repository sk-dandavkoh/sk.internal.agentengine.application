import { useState } from 'react'
import type { Edge, Graph, NodeInstance } from '../../api/projects'
import { Button, Select } from '../../components/ui'
import {
    connectionProblem,
    missingInputs,
    PROBLEM_LABELS,
    type ConnectionRules,
} from './connections'
import { edgeKey, edgesOf, graphIndex, nodeTitle, portsOf } from './graphView'

/**
 * The selected node's edges as lists with remove buttons, plus a form to
 * connect one of its outputs: graph editing without pointer or sight
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
    const own = edgesOf(graph, node.id)
    const { inputs, outputs } = portsOf(node, definition, own)
    const missing = missingInputs(node.id, definition, own)
    const title = (id: string) => {
        const n = graphIndex(graph).nodes.get(id)
        return n ? nodeTitle(n, rules.catalog?.get(n.node_type)) : id
    }
    const name = nodeTitle(node, definition)
    const [output, setOutput] = useState('')
    const [target, setTarget] = useState('')
    const source = outputs.some((p) => p.name === output)
        ? output
        : (outputs[0]?.name ?? '')
    const targets = graph.nodes
        .filter((n) => n.id !== node.id)
        .flatMap((n) =>
            portsOf(
                n,
                rules.catalog?.get(n.node_type),
                edgesOf(graph, n.id),
            ).inputs.map((p) => {
                const edge = {
                    source_node_id: node.id,
                    source_port: source,
                    target_node_id: n.id,
                    target_port: p.name,
                }
                return {
                    edge,
                    key: edgeKey(edge),
                    label: `${title(n.id)} › ${p.name}`,
                    problem: connectionProblem(graph, edge, rules),
                }
            }),
        )
    // A choice that a changed output (or graph) made invalid is dropped.
    const chosen = targets.find((t) => t.key === target && !t.problem)

    const edgeList = (edges: Edge[], other: (e: Edge) => string) =>
        edges.length === 0 ? null : (
            <ul className="flex flex-col gap-1">
                {edges.map((e) => (
                    <li
                        key={edgeKey(e)}
                        className="flex items-center justify-between gap-2"
                    >
                        <span>{other(e)}</span>
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
        )

    return (
        <section
            aria-labelledby="node-connections"
            className="flex flex-col gap-3 rounded-md border border-border bg-surface p-3 text-sm"
        >
            <h2 id="node-connections" className="font-semibold">
                Connections: {name}
            </h2>
            {missing.length > 0 && (
                <p className="font-medium text-warning-text">
                    Missing required input: {missing.join(', ')}. Publishing
                    would be rejected.
                </p>
            )}
            <div>
                <h3 className="font-medium">Inputs</h3>
                {inputs.length === 0 ? (
                    <p className="text-muted">No inputs.</p>
                ) : (
                    <ul className="flex flex-col gap-2">
                        {inputs.map((p) => {
                            const edges = graph.edges.filter(
                                (e) =>
                                    e.target_node_id === node.id &&
                                    e.target_port === p.name,
                            )
                            return (
                                <li key={p.name}>
                                    <span className="font-mono text-xs">
                                        {p.name}
                                        {p.type && ` (${p.type})`}
                                        {p.required && ', required'}
                                    </span>
                                    {edgeList(
                                        edges,
                                        (e) =>
                                            `from ${title(e.source_node_id)} › ${e.source_port}`,
                                    ) ?? (
                                        <p className="text-muted">
                                            Not connected
                                        </p>
                                    )}
                                </li>
                            )
                        })}
                    </ul>
                )}
            </div>
            <div>
                <h3 className="font-medium">Outputs</h3>
                {outputs.length === 0 ? (
                    <p className="text-muted">No outputs.</p>
                ) : (
                    <ul className="flex flex-col gap-2">
                        {outputs.map((p) => (
                            <li key={p.name}>
                                <span className="font-mono text-xs">
                                    {p.name}
                                    {p.type && ` (${p.type})`}
                                </span>
                                {edgeList(
                                    graph.edges.filter(
                                        (e) =>
                                            e.source_node_id === node.id &&
                                            e.source_port === p.name,
                                    ),
                                    (e) =>
                                        `to ${title(e.target_node_id)} › ${e.target_port}`,
                                ) ?? (
                                    <p className="text-muted">Not connected</p>
                                )}
                            </li>
                        ))}
                    </ul>
                )}
            </div>
            {outputs.length > 0 && targets.length > 0 && (
                <form
                    className="flex flex-col gap-2 border-t border-border pt-3"
                    onSubmit={(event) => {
                        event.preventDefault()
                        if (!chosen) return
                        onConnect(chosen.edge)
                        setTarget('')
                    }}
                >
                    <h3 className="font-medium">New connection</h3>
                    <Select
                        label="From output"
                        value={source}
                        onChange={(e) => {
                            setOutput(e.target.value)
                        }}
                    >
                        {outputs.map((p) => (
                            <option key={p.name} value={p.name}>
                                {p.name}
                                {p.type && ` (${p.type})`}
                            </option>
                        ))}
                    </Select>
                    <Select
                        label="To input"
                        description="Inputs that cannot take this output are listed with the reason."
                        value={chosen?.key ?? ''}
                        onChange={(e) => {
                            setTarget(e.target.value)
                        }}
                    >
                        <option value="">Choose an input…</option>
                        {targets.map((t) => (
                            <option
                                key={t.key}
                                value={t.key}
                                disabled={t.problem !== null}
                            >
                                {t.label}
                                {t.problem &&
                                    ` (${PROBLEM_LABELS[t.problem.code]})`}
                            </option>
                        ))}
                    </Select>
                    <Button type="submit" size="sm" disabled={!chosen}>
                        Connect
                    </Button>
                </form>
            )}
        </section>
    )
}
