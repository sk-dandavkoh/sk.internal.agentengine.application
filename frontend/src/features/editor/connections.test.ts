import { describe, expect, it } from 'vitest'
import type { NodeDefinition, NodePort } from '../../api/nodes'
import type { Edge, Graph } from '../../api/workflows'
import {
    connectionProblem,
    missingInputs,
    type ConnectionRules,
} from './connections'

const port = (name: string, type: string, extra: Partial<NodePort> = {}) => ({
    name,
    type,
    required: false,
    nullable: false,
    multiple: false,
    description: '',
    ...extra,
})

const def = (
    node_type: string,
    inputs: NodePort[],
    outputs: NodePort[],
): NodeDefinition => ({
    node_type,
    version: '1.0.0',
    name: node_type,
    description: '',
    category: 'core',
    documentation_url: null,
    inputs,
    outputs,
    config_schema: {},
    capabilities: {},
})

// An excerpt of the backend's table (domain port_compatibility).
const rules: ConnectionRules = {
    catalog: new Map(
        [
            def(
                't.text',
                [],
                [
                    port('text', 'string'),
                    port('lines', 'string', { multiple: true }),
                ],
            ),
            def(
                't.sink',
                [
                    port('n', 'number', { required: true }),
                    port('s', 'string'),
                    port('all', 'string', { multiple: true }),
                ],
                [],
            ),
        ].map((d) => [d.node_type, d]),
    ),
    compatibility: {
        string: ['string', 'string[]', 'json'],
        'string[]': ['string[]', 'json'],
    },
}

const node = (id: string, node_type: string) => ({
    id,
    node_type,
    config: {},
    label: '',
    position: null,
})
const edge = (sp: string, tp: string, s = 'a', t = 'b'): Edge => ({
    source_node_id: s,
    source_port: sp,
    target_node_id: t,
    target_port: tp,
})
const graph = (edges: Edge[] = []): Graph => ({
    nodes: [node('a', 't.text'), node('b', 't.sink'), node('x', 'gone.type')],
    edges,
})
const code = (g: Graph, e: Edge, r = rules, replacing?: Edge) =>
    connectionProblem(g, e, r, replacing)?.code ?? null

describe('connectionProblem', () => {
    it('accepts compatible ports, including a scalar into a list input', () => {
        expect(code(graph(), edge('text', 's'))).toBeNull()
        expect(code(graph(), edge('text', 'all'))).toBeNull()
        expect(code(graph(), edge('lines', 'all'))).toBeNull()
    })

    it('refuses incompatible types and lists into scalars, saying which', () => {
        expect(code(graph(), edge('text', 'n'))).toBe('incompatible')
        expect(code(graph(), edge('lines', 's'))).toBe('incompatible')
        expect(
            connectionProblem(graph(), edge('text', 'n'), rules)?.message,
        ).toBe('Output "text" (string) cannot feed input "n" (number).')
    })

    it('refuses self-edges, duplicates and dangling ends', () => {
        expect(code(graph(), edge('text', 's', 'a', 'a'))).toBe('self')
        expect(code(graph([edge('text', 's')]), edge('text', 's'))).toBe(
            'duplicate',
        )
        expect(code(graph(), edge('text', 's', 'a', 'gone'))).toBe(
            'unknown_node',
        )
    })

    it('a single input takes one edge; a multiple input takes many', () => {
        const fed = graph([edge('text', 's'), edge('text', 'all')])
        const other = { ...fed, nodes: [...fed.nodes, node('c', 't.text')] }
        expect(code(other, edge('text', 's', 'c'))).toBe('occupied')
        expect(code(other, edge('text', 'all', 'c'))).toBeNull()
    })

    it('ignores the edge being reconnected', () => {
        const fed = graph([edge('text', 's')])
        // Moving the edge feeding `s` to `all` is fine; so is putting it back.
        expect(
            code(fed, edge('text', 'all'), rules, edge('text', 's')),
        ).toBeNull()
        expect(
            code(fed, edge('text', 's'), rules, edge('text', 's')),
        ).toBeNull()
    })

    it('refuses ports the contract lacks and nodes of unknown type', () => {
        expect(code(graph(), edge('nope', 's'))).toBe('unknown_port')
        expect(code(graph(), edge('text', 'nope'))).toBe('unknown_port')
        expect(code(graph(), edge('text', 's', 'a', 'x'))).toBe('unknown_type')
    })

    it('checks nothing it cannot: no catalog, no connection', () => {
        const none = { catalog: undefined, compatibility: undefined }
        expect(code(graph(), edge('text', 's'), none)).toBe('unchecked')
        // A signature missing from the table is not compatible.
        expect(
            code(graph(), edge('text', 's'), { ...rules, compatibility: {} }),
        ).toBe('incompatible')
    })
})

describe('missingInputs', () => {
    it('lists required inputs without an incoming edge', () => {
        const sink = rules.catalog?.get('t.sink')
        expect(missingInputs('b', sink, [])).toEqual(['n'])
        expect(missingInputs('b', sink, [edge('text', 'n')])).toEqual([])
        expect(missingInputs('b', undefined, [])).toEqual([])
    })
})
