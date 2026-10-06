import { describe, expect, it } from 'vitest'
import { projectVersionSchema } from '../../api/projects'
import { diffVersions } from './compare'

const version = (overrides: object) =>
    projectVersionSchema.parse({
        id: 'pver_1',
        version: 1,
        description: '',
        schema_version: 1,
        published_at: '2026-10-01T10:00:00Z',
        published_by: null,
        graph: { nodes: [], edges: [] },
        triggers: [],
        dependencies: [],
        files: [],
        plugin_versions: {},
        ...overrides,
    })

const edge = (source: string, target: string) => ({
    source_node_id: source,
    source_port: 'out',
    target_node_id: target,
    target_port: 'in',
})

describe('diffVersions', () => {
    it('reports added, removed and changed parts', () => {
        const from = version({
            graph: {
                nodes: [
                    { id: 'a', node_type: 'core.log', config: { level: 1 } },
                    { id: 'b', node_type: 'core.log' },
                    { id: 'gone', node_type: 'core.log' },
                ],
                edges: [edge('a', 'b')],
            },
            triggers: [{ id: 't', trigger_type: 'manual' }],
            dependencies: [{ kind: 'node', name: 'core.log' }],
            files: [{ path: 'a.md', size: 1 }],
        })
        const to = version({
            graph: {
                nodes: [
                    { id: 'a', node_type: 'core.log', config: { level: 2 } },
                    // Moved only: layout is not a change.
                    {
                        id: 'b',
                        node_type: 'core.log',
                        position: { x: 9, y: 9 },
                    },
                    { id: 'new', node_type: 'core.log' },
                ],
                edges: [edge('a', 'new')],
            },
            triggers: [{ id: 't', trigger_type: 'manual', enabled: false }],
            dependencies: [
                { kind: 'node', name: 'core.log', version_constraint: '>=1' },
            ],
            files: [{ path: 'a.md', size: 1 }],
        })

        const diff = diffVersions(from, to)

        expect(diff.identical).toBe(false)
        expect(diff.nodes).toEqual({
            added: ['new'],
            removed: ['gone'],
            changed: ['a'],
        })
        expect(diff.edges).toEqual({
            added: ['a.out → new.in'],
            removed: ['a.out → b.in'],
            changed: [],
        })
        expect(diff.triggers.changed).toEqual(['t'])
        expect(diff.dependencies.changed).toEqual(['node:core.log'])
        expect(diff.files).toEqual({ added: [], removed: [], changed: [] })
    })

    it('finds nothing between equal snapshots', () => {
        const snapshot = {
            graph: { nodes: [{ id: 'a', node_type: 'core.log' }], edges: [] },
        }
        expect(
            diffVersions(version(snapshot), version(snapshot)).identical,
        ).toBe(true)
    })
})
