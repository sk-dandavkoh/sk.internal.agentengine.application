import {
    Background,
    BackgroundVariant,
    Controls,
    MiniMap,
    Panel,
    ReactFlow,
    useReactFlow,
    useViewport,
    type Edge as FlowEdge,
    type Node as FlowNode,
    type NodeChange,
    type NodeProps,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { useMemo, useState } from 'react'
import type { NodeDefinition } from '../../api/nodes'
import type { Graph, NodeInstance, NodePosition } from '../../api/projects'
import { edgeKey, nodeTitle, portsOf } from './graphView'
import { NodeCard } from './NodeCard'
import { NODE_TYPE_MIME } from './NodeLibrary'

export interface CanvasNodeData extends Record<string, unknown> {
    instance: NodeInstance
    /** undefined: the type is not in the catalog (or it did not load). */
    definition: NodeDefinition | undefined
    catalogLoaded: boolean
    inputs: string[]
    outputs: string[]
}
type CanvasNode = FlowNode<CanvasNodeData, 'draft'>

export const MIN_ZOOM = 0.2
export const MAX_ZOOM = 2
/** Snap step of moved and added nodes. */
export const GRID = 10

function DraftNode({ data, selected }: NodeProps<CanvasNode>) {
    const { instance, definition, catalogLoaded, inputs, outputs } = data
    return (
        <NodeCard
            title={nodeTitle(instance, definition)}
            nodeType={instance.node_type}
            category={definition?.category}
            inputs={inputs}
            outputs={outputs}
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
    catalog: ReadonlyMap<string, NodeDefinition> | undefined
    onMove: (positions: Record<string, NodePosition>, done: boolean) => void
    onSelect: (nodeIds: string[]) => void
    /** A library node dropped at a flow position. */
    onDropNode: (nodeType: string, position: NodePosition) => void
}

/**
 * Renders a draft graph. Controlled: positions and selection come from the
 * editor state; only measured node sizes (rendering detail) live here.
 * Connecting and deleting are Phase 10, so both are switched off. Library
 * nodes can be dropped in; the editor decides whether they are added.
 */
export function GraphCanvas({
    graph,
    selection,
    catalog,
    onMove,
    onSelect,
    onDropNode,
}: GraphCanvasProps) {
    const { screenToFlowPosition } = useReactFlow()
    const [measured, setMeasured] = useState<
        Record<string, { width: number; height: number }>
    >({})

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
                },
            }
        })
    }, [graph, selection, catalog, measured])

    const edges = useMemo(
        (): FlowEdge[] =>
            graph.edges.map((e) => ({
                id: edgeKey(e),
                source: e.source_node_id,
                sourceHandle: e.source_port,
                target: e.target_node_id,
                targetHandle: e.target_port,
                selectable: false,
            })),
        [graph.edges],
    )

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

    return (
        <ReactFlow<CanvasNode>
            nodes={nodes}
            edges={edges}
            nodeTypes={nodeTypes}
            onNodesChange={onNodesChange}
            onDragOver={(event) => {
                // Only library items are droppable; files and text are not.
                if (!event.dataTransfer.types.includes(NODE_TYPE_MIME)) return
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
            nodesConnectable={false}
            edgesFocusable={false}
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
    )
}
