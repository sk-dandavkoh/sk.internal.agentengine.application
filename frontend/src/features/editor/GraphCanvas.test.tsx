import { render } from '@testing-library/react'
import { ReactFlowProvider } from '@xyflow/react'
import { describe, expect, it, vi } from 'vitest'
import type { NodeDefinition, NodePort } from '../../api/nodes'
import type { Edge, Graph } from '../../api/projects'
import type { ConnectionRules } from './connections'
import { GraphCanvas } from './GraphCanvas'

// Large graphs stay smooth only if a canvas change re-renders the cards it
// touches, not every card (frontend/AGENTS §60). Cards are counted.
const cards = vi.hoisted(() => ({ rendered: [] as string[] }))
vi.mock('./NodeCard', async (load) => {
    const actual = await load<typeof import('./NodeCard')>()
    return {
        ...actual,
        NodeCard: (props: Parameters<typeof actual.NodeCard>[0]) => {
            cards.rendered.push(props.title)
            return actual.NodeCard(props)
        },
    }
})

const port = (name: string, extra: Partial<NodePort> = {}): NodePort => ({
    name,
    type: 'string',
    required: false,
    nullable: false,
    multiple: false,
    description: '',
    ...extra,
})
const definition: NodeDefinition = {
    node_type: 't.step',
    version: '1.0.0',
    name: 'Step',
    description: '',
    category: 'core',
    documentation_url: null,
    inputs: [port('in', { multiple: true })],
    outputs: [port('out')],
    config_schema: {},
    capabilities: {},
}
const rules: ConnectionRules = {
    catalog: new Map([[definition.node_type, definition]]),
    compatibility: { string: ['string', 'string[]'] },
}

/** A chain of `count` nodes, each feeding the next. */
function chain(count: number): Graph {
    const nodes = Array.from({ length: count }, (_, i) => ({
        id: `n${String(i)}`,
        node_type: 't.step',
        label: `Step ${String(i)}`,
        config: {},
        position: { x: i * 200, y: 0 },
    }))
    const edges: Edge[] = nodes.slice(1).map((n, i) => ({
        source_node_id: `n${String(i)}`,
        source_port: 'out',
        target_node_id: n.id,
        target_port: 'in',
    }))
    return { nodes, edges }
}

function canvas(graph: Graph, selection: string[] = []) {
    const noop = vi.fn()
    return (
        <ReactFlowProvider>
            <GraphCanvas
                graph={graph}
                selection={selection}
                edgeSelection={[]}
                rules={rules}
                onMove={noop}
                onSelect={noop}
                onSelectEdges={noop}
                onDropNode={noop}
                onConnect={noop}
                onReconnect={noop}
                onRefuse={noop}
            />
        </ReactFlowProvider>
    )
}

const settle = () => new Promise((resolve) => setTimeout(resolve, 0))

describe('GraphCanvas rendering', () => {
    it('re-renders only the moved card on each drag frame', async () => {
        let graph = chain(30)
        const view = render(canvas(graph))
        await settle()
        for (const y of [10, 20, 30]) {
            cards.rendered = []
            graph = {
                ...graph,
                nodes: graph.nodes.map((n, i) =>
                    i === 5 ? { ...n, position: { x: 1000, y } } : n,
                ),
            }
            view.rerender(canvas(graph))
            await settle()
            expect(new Set(cards.rendered)).toEqual(new Set(['Step 5']))
        }
    })

    it('re-renders the cards whose selection or edges changed', async () => {
        let graph = chain(30)
        const view = render(canvas(graph))
        await settle()

        cards.rendered = []
        view.rerender(canvas(graph, ['n7']))
        await settle()
        expect(new Set(cards.rendered)).toEqual(new Set(['Step 7']))

        // A removed edge changes both its ends' ports.
        cards.rendered = []
        graph = {
            ...graph,
            edges: graph.edges.filter((e) => e.target_node_id !== 'n12'),
        }
        view.rerender(canvas(graph, ['n7']))
        await settle()
        expect(new Set(cards.rendered)).toEqual(new Set(['Step 11', 'Step 12']))
    })
})
