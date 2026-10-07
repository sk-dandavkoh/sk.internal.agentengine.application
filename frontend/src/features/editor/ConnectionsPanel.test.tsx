import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { NodeDefinition, NodePort } from '../../api/nodes'
import type { Graph } from '../../api/workflows'
import { ConnectionsPanel } from './ConnectionsPanel'

const port = (name: string, extra: Partial<NodePort> = {}): NodePort => ({
    name,
    type: 'string',
    required: true,
    nullable: false,
    multiple: false,
    description: '',
    ...extra,
})
// Like core.file_write: an input and an output share the name `path`.
const writer: NodeDefinition = {
    node_type: 't.write',
    version: '1.0.0',
    name: 'Write',
    description: '',
    category: 'file',
    documentation_url: null,
    inputs: [port('path', { description: 'Where to save the file.' })],
    outputs: [port('path'), port('size', { type: 'number' })],
    config_schema: {},
    capabilities: {},
}
const graph: Graph = {
    nodes: [
        {
            id: 'w',
            node_type: 't.write',
            config: {},
            label: '',
            position: { x: 0, y: 0 },
        },
    ],
    edges: [],
}

describe('ConnectionsPanel', () => {
    it('explains each port and flags only the missing input, not an output of the same name', () => {
        const [node] = graph.nodes
        if (!node) throw new Error('no node')
        render(
            <ConnectionsPanel
                node={node}
                graph={graph}
                rules={{
                    catalog: new Map([[writer.node_type, writer]]),
                    compatibility: {},
                }}
                onConnect={() => undefined}
                onRemove={() => undefined}
            />,
        )
        const [inputs, outputs] = screen.getAllByRole('list')
        if (!inputs || !outputs) throw new Error('no port lists')
        const input = within(inputs)
        expect(input.getByText('Where to save the file.')).toBeInTheDocument()
        expect(input.getByText('Text')).toBeInTheDocument()
        expect(input.getByText('Required')).toBeInTheDocument()
        expect(input.getByText(/^Not connected yet/)).toBeInTheDocument()

        const output = within(outputs)
        expect(output.queryByText(/^Not connected yet/)).toBeNull()
        expect(output.getAllByText('Not sent anywhere yet.')).toHaveLength(2)
        expect(output.getByText('Number')).toBeInTheDocument()
    })
})
