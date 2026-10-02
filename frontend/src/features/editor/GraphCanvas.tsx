import {
    Background,
    BackgroundVariant,
    Controls,
    MiniMap,
    Panel,
    ReactFlow,
    useConnection,
    useReactFlow,
    useViewport,
    type Connection,
    type EdgeChange,
    type Edge as FlowEdge,
    type FinalConnectionState,
    type Node as FlowNode,
    type NodeChange,
    type NodeProps,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { createContext, use, useMemo, useRef, useState } from 'react'
import type { NodeDefinition } from '../../api/nodes'
import type {
    Edge,
    Graph,
    NodeInstance,
    NodePosition,
} from '../../api/projects'
import {
    connectionProblem,
    missingInputs,
    PROBLEM_LABELS,
    type ConnectionProblem,
    type ConnectionRules,
} from './connections'
import { edgeKey, nodeTitle, portsOf } from './graphView'
import { NodeCard, type CardPort } from './NodeCard'
import { NODE_TYPE_MIME } from './NodeLibrary'

export interface CanvasNodeData extends Record<string, unknown> {
    instance: NodeInstance
    /** undefined: the type is not in the catalog (or it did not load). */
    definition: NodeDefinition | undefined
    catalogLoaded: boolean
    inputs: CardPort[]
    outputs: CardPort[]
    missing: string[]
}
type CanvasNode = FlowNode<CanvasNodeData, 'draft'>

export const MIN_ZOOM = 0.2
export const MAX_ZOOM = 2
/** Snap step of moved and added nodes. */
export const GRID = 10

/** The problem of a would-be edge in the current graph. */
const CheckContext = createContext<(edge: Edge) => ConnectionProblem | null>(
    () => null,
)

/** Handle end of a connection, as a draft edge; null while incomplete. */
function toEdge(c: Connection | FlowEdge): Edge | null {
    if (!c.sourceHandle || !c.targetHandle) return null
    return {
        source_node_id: c.source,
        source_port: c.sourceHandle,
        target_node_id: c.target,
        target_port: c.targetHandle,
    }
}

function DraftNode({ id, data, selected }: NodeProps<CanvasNode>) {
    const { instance, definition, catalogLoaded, inputs, outputs, missing } =
        data
    const check = use(CheckContext)
    // A string, so nodes re-render when a drag starts or ends, not per move.
    const from = useConnection((c) =>
        c.inProgress && c.fromHandle.id
            ? [c.fromHandle.type, c.fromNode.id, c.fromHandle.id].join('\n')
            : null,
    )
    const [fromType = '', fromNode = '', fromPort = ''] =
        from?.split('\n') ?? []
    // While a drag runs, mark which of this node's ports would take it.
    const fit = (port: CardPort, side: 'source' | 'target'): CardPort => {
        if (!from || fromType === side) return port
        const edge =
            side === 'target'
                ? {
                      source_node_id: fromNode,
                      source_port: fromPort,
                      target_node_id: id,
                      target_port: port.name,
                  }
                : {
                      source_node_id: id,
                      source_port: port.name,
                      target_node_id: fromNode,
                      target_port: fromPort,
                  }
        return {
            ...port,
            fit: check(edge) ? 'incompatible' : 'compatible',
        }
    }
    return (
        <NodeCard
            title={nodeTitle(instance, definition)}
            nodeType={instance.node_type}
            category={definition?.category}
            inputs={inputs.map((p) => fit(p, 'target'))}
            outputs={outputs.map((p) => fit(p, 'source'))}
            missing={missing}
            selected={selected}
            unknown={catalogLoaded && !definition}
            handles
        />
    )
}

const nodeTypes = { draft: DraftNode }

function ZoomLevel() {
    const { zoom } = useViewport()
    return (
        <Panel
            position="bottom-right"
            className="rounded bg-surface px-2 py-1 text-xs text-muted shadow-sm"
        >
            <span aria-live="polite">Zoom {Math.round(zoom * 100)}%</span>
        </Panel>
    )
}

export interface GraphCanvasProps {
    graph: Graph
    selection: readonly string[]
    edgeSelection: readonly string[]
    rules: ConnectionRules
    onMove: (positions: Record<string, NodePosition>, done: boolean) => void
    onSelect: (nodeIds: string[]) => void
    onSelectEdges: (edgeKeys: string[]) => void
    /** A library node dropped at a flow position. */
    onDropNode: (nodeType: string, position: NodePosition) => void
    /** A connection that passed `connectionProblem`. */
    onConnect: (edge: Edge) => void
    onReconnect: (from: Edge, edge: Edge) => void
    /** A connection dropped on a port that cannot take it. */
    onRefuse: (problem: ConnectionProblem) => void
}

/**
 * Renders a draft graph. Controlled: positions, selection and edges come
 * from the editor state; only measured node sizes (rendering detail) live
 * here. Library nodes can be dropped in; the editor decides whether they
 * are added. Deleting is the editor's (keyboard and toolbar), so React
 * Flow's own delete key is off.
 */
export function GraphCanvas({
    graph,
    selection,
    edgeSelection,
    rules,
    onMove,
    onSelect,
    onSelectEdges,
    onDropNode,
    onConnect,
    onReconnect,
    onRefuse,
}: GraphCanvasProps) {
    const { screenToFlowPosition } = useReactFlow()
    const [measured, setMeasured] = useState<
        Record<string, { width: number; height: number }>
    >({})
    // The edge being dragged to other ports: it does not block its own move.
    const reconnecting = useRef<Edge | undefined>(undefined)
    const check = (edge: Edge) =>
        connectionProblem(graph, edge, rules, reconnecting.current)
    const { catalog } = rules

    /** Reports a drop on a port that refused the connection. */
    function refused(state: FinalConnectionState) {
        if (!state.toHandle || state.isValid !== false) return
        const [source, target] =
            state.fromHandle.type === 'source'
                ? [state.fromHandle, state.toHandle]
                : [state.toHandle, state.fromHandle]
        const edge = toEdge({
            source: source.nodeId,
            sourceHandle: source.id ?? null,
            target: target.nodeId,
            targetHandle: target.id ?? null,
        })
        const problem = edge && check(edge)
        if (problem) onRefuse(problem)
    }

    // ponytail: rebuilds every node object on each change; cache per node
    // when large graphs measure slow (frontend roadmap Phase 24).
    const nodes = useMemo((): CanvasNode[] => {
        const selected = new Set(selection)
        return graph.nodes.map((instance) => {
            const definition = catalog?.get(instance.node_type)
            return {
                id: instance.id,
                type: 'draft',
                // Every node has a position once loaded (placeUnpositioned).
                position: instance.position ?? { x: 0, y: 0 },
                selected: selected.has(instance.id),
                measured: measured[instance.id] ?? {},
                ariaLabel: `${nodeTitle(instance, definition)} (${instance.node_type})`,
                data: {
                    instance,
                    definition,
                    catalogLoaded: catalog !== undefined,
                    ...portsOf(instance, definition, graph.edges),
                    missing: missingInputs(
                        instance.id,
                        definition,
                        graph.edges,
                    ),
                },
            }
        })
    }, [graph, selection, catalog, measured])

    const edges = useMemo((): FlowEdge[] => {
        const selected = new Set(edgeSelection)
        const title = (id: string, port: string) => {
            const node = graph.nodes.find((n) => n.id === id)
            const name = node
                ? nodeTitle(node, catalog?.get(node.node_type))
                : id
            return `${name} ${port}`
        }
        return graph.edges.map((e) => {
            // Stored edges can predate a contract change or come from an
            // import; say so on the edge, in text (never color alone).
            const problem = connectionProblem(graph, e, rules, e)
            // A node of unknown type already says so on its card.
            const shown =
                problem &&
                !['unchecked', 'unknown_type'].includes(problem.code) &&
                problem
            return {
                id: edgeKey(e),
                source: e.source_node_id,
                sourceHandle: e.source_port,
                target: e.target_node_id,
                targetHandle: e.target_port,
                selected: selected.has(edgeKey(e)),
                ariaLabel: `Connection from ${title(e.source_node_id, e.source_port)} to ${title(e.target_node_id, e.target_port)}${shown ? `, invalid: ${shown.message}` : ''}`,
                ...(shown && {
                    className: 'edge-invalid',
                    label: PROBLEM_LABELS[shown.code],
                }),
            }
        })
    }, [graph, edgeSelection, rules, catalog])

    const onNodesChange = (changes: NodeChange<CanvasNode>[]) => {
        const positions: Record<string, NodePosition> = {}
        let moved = false
        let dragging = false
        let selected: Set<string> | null = null
        const sizes: typeof measured = {}
        for (const change of changes) {
            if (change.type === 'position' && change.position) {
                positions[change.id] = change.position
                moved = true
                dragging ||= change.dragging === true
            } else if (change.type === 'select') {
                selected ??= new Set(selection)
                if (change.selected) selected.add(change.id)
                else selected.delete(change.id)
            } else if (change.type === 'dimensions' && change.dimensions) {
                sizes[change.id] = change.dimensions
            }
        }
        if (Object.keys(sizes).length)
            setMeasured((current) => ({ ...current, ...sizes }))
        if (moved) onMove(positions, !dragging)
        if (selected) onSelect([...selected])
    }

    const onEdgesChange = (changes: EdgeChange[]) => {
        let selected: Set<string> | null = null
        for (const change of changes) {
            if (change.type !== 'select') continue
            selected ??= new Set(edgeSelection)
            if (change.selected) selected.add(change.id)
            else selected.delete(change.id)
        }
        if (selected) onSelectEdges([...selected])
    }

    return (
        <CheckContext value={check}>
            <ReactFlow<CanvasNode>
                nodes={nodes}
                edges={edges}
                nodeTypes={nodeTypes}
                onNodesChange={onNodesChange}
                onEdgesChange={onEdgesChange}
                isValidConnection={(c) => {
                    const edge = toEdge(c)
                    return edge !== null && check(edge) === null
                }}
                onConnect={(c) => {
                    const edge = toEdge(c)
                    if (edge) onConnect(edge)
                }}
                onConnectEnd={(_event, state) => {
                    refused(state)
                }}
                edgesReconnectable
                onReconnectStart={(_event, edge) => {
                    reconnecting.current = toEdge(edge) ?? undefined
                }}
                onReconnect={(old, c) => {
                    const from = toEdge(old)
                    const edge = toEdge(c)
                    if (from && edge) onReconnect(from, edge)
                }}
                onReconnectEnd={(_event, _edge, _type, state) => {
                    refused(state)
                    reconnecting.current = undefined
                }}
                onDragOver={(event) => {
                    // Only library items are droppable; files and text are not.
                    if (!event.dataTransfer.types.includes(NODE_TYPE_MIME))
                        return
                    event.preventDefault()
                    event.dataTransfer.dropEffect = 'copy'
                }}
                onDrop={(event) => {
                    const nodeType = event.dataTransfer.getData(NODE_TYPE_MIME)
                    if (!nodeType) return
                    event.preventDefault()
                    onDropNode(
                        nodeType,
                        screenToFlowPosition({
                            x: event.clientX,
                            y: event.clientY,
                        }),
                    )
                }}
                deleteKeyCode={null}
                minZoom={MIN_ZOOM}
                maxZoom={MAX_ZOOM}
                fitView
                fitViewOptions={{ maxZoom: 1 }}
                snapToGrid
                snapGrid={[GRID, GRID]}
                className="draft-canvas"
            >
                <Background variant={BackgroundVariant.Dots} gap={20} />
                <Controls showInteractive={false} />
                <MiniMap pannable zoomable ariaLabel="Minimap" />
                <ZoomLevel />
            </ReactFlow>
        </CheckContext>
    )
}
