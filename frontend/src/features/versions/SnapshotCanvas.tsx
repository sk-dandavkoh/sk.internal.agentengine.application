import {
    Background,
    BackgroundVariant,
    Controls,
    MiniMap,
    ReactFlow,
    type Edge as FlowEdge,
    type Node as FlowNode,
    type NodeProps,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { useQuery } from '@tanstack/react-query'
import { useMemo, type ReactNode } from 'react'
import { useApiClient } from '../../api/context'
import type { NodeDefinition } from '../../api/nodes'
import type { Graph, NodeInstance } from '../../api/projects'
import { nodeQueries } from '../../api/queries'
import { placeUnpositioned } from '../editor/editorState'
import { MAX_ZOOM, MIN_ZOOM } from '../editor/GraphCanvas'
import { edgeKey, edgesOf, nodeTitle, portsOf } from '../editor/graphView'
import { NodeCard, type CardPort } from '../editor/NodeCard'

interface SnapshotNodeData extends Record<string, unknown> {
    instance: NodeInstance
    definition: NodeDefinition | undefined
    catalogLoaded: boolean
    inputs: CardPort[]
    outputs: CardPort[]
    footer: ReactNode
}
type SnapshotNode = FlowNode<SnapshotNodeData, 'snapshot'>

function SnapshotNodeView({ data }: NodeProps<SnapshotNode>) {
    const { instance, definition, catalogLoaded, inputs, outputs, footer } =
        data
    return (
        <NodeCard
            title={nodeTitle(instance, definition)}
            nodeType={instance.node_type}
            category={definition?.category}
            inputs={inputs}
            outputs={outputs}
            unknown={catalogLoaded && !definition}
            // Edges attach to handles; nothing can be connected here.
            handles
            footer={footer}
        />
    )
}

const nodeTypes = { snapshot: SnapshotNodeView }

/**
 * A published graph drawn like the editor draws drafts, but frozen: no
 * dragging, connecting, selecting or deleting, only pan and zoom. Node
 * cards use today's catalog for names and ports; the snapshot decides
 * which nodes and edges exist.
 */
export function SnapshotCanvas({
    graph,
    label = 'Published graph (read-only)',
    footer,
    onNodeClick,
}: {
    graph: Graph
    label?: string
    /** Per-node extra state, e.g. its status within an execution. */
    footer?: (nodeId: string) => ReactNode
    onNodeClick?: (nodeId: string) => void
}) {
    const catalog = useQuery(nodeQueries.catalog(useApiClient())).data
    const definitions = useMemo(
        () => new Map(catalog?.items.map((d) => [d.node_type, d])),
        [catalog],
    )
    const nodes = useMemo((): SnapshotNode[] => {
        // Versions published via API or import may have no layout.
        const placed = placeUnpositioned(graph)
        return placed.nodes.map((instance) => {
            const definition = definitions.get(instance.node_type)
            return {
                id: instance.id,
                type: 'snapshot',
                position: instance.position ?? { x: 0, y: 0 },
                ariaLabel: `${nodeTitle(instance, definition)} (${instance.node_type})`,
                data: {
                    instance,
                    definition,
                    catalogLoaded: catalog !== undefined,
                    footer: footer?.(instance.id),
                    ...portsOf(
                        instance,
                        definition,
                        edgesOf(graph, instance.id),
                    ),
                },
            }
        })
    }, [graph, definitions, catalog, footer])
    const edges = useMemo(
        (): FlowEdge[] =>
            graph.edges.map((e) => ({
                id: edgeKey(e),
                source: e.source_node_id,
                sourceHandle: e.source_port,
                target: e.target_node_id,
                targetHandle: e.target_port,
            })),
        [graph],
    )

    return (
        <div
            role="figure"
            aria-label={label}
            className="h-96 overflow-hidden rounded-md border border-border bg-canvas"
        >
            <ReactFlow<SnapshotNode>
                nodes={nodes}
                edges={edges}
                nodeTypes={nodeTypes}
                nodesDraggable={false}
                nodesConnectable={false}
                elementsSelectable={false}
                edgesReconnectable={false}
                // Read-only edges have no keyboard action; as Tab stops
                // they only read out raw node ids.
                edgesFocusable={false}
                deleteKeyCode={null}
                onNodeClick={(_, node) => {
                    onNodeClick?.(node.id)
                }}
                minZoom={MIN_ZOOM}
                maxZoom={MAX_ZOOM}
                fitView
                fitViewOptions={{ maxZoom: 1 }}
                className="draft-canvas"
            >
                <Background variant={BackgroundVariant.Dots} gap={20} />
                <Controls showInteractive={false} />
                <MiniMap pannable zoomable ariaLabel="Minimap" />
            </ReactFlow>
        </div>
    )
}
