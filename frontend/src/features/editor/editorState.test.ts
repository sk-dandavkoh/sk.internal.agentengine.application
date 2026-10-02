import { describe, expect, it } from 'vitest'
import type { Graph, NodeInstance } from '../../api/projects'
import {
    editorReducer,
    HISTORY_LIMIT,
    initEditor,
    isDirty,
    newNodeId,
    PASTE_OFFSET,
    placeUnpositioned,
    type EditorAction,
    type EditorState,
} from './editorState'

const node = (
    id: string,
    position: NodeInstance['position'] = { x: 0, y: 0 },
): NodeInstance => ({
    id,
    node_type: 'core.log',
    config: {},
    label: '',
    position,
})

const edge = (source: string, target: string) => ({
    source_node_id: source,
    source_port: 'out',
    target_node_id: target,
    target_port: 'in',
})

function graph(): Graph {
    return {
        nodes: [node('a', { x: 0, y: 0 }), node('b', { x: 300, y: 0 })],
        edges: [edge('a', 'b')],
    }
}

const run = (state: EditorState, ...actions: EditorAction[]) =>
    actions.reduce(editorReducer, state)

const drag = (id: string, x: number, y: number, done = false) =>
    ({ type: 'move', positions: { [id]: { x, y } }, done }) as const

const at = (state: EditorState, id: string) =>
    state.graph.nodes.find((n) => n.id === id)?.position

describe('initEditor', () => {
    it('starts clean, with empty history and selection', () => {
        const state = initEditor(graph(), 7)
        expect(isDirty(state)).toBe(false)
        expect(state.revision).toBe(7)
        expect(state).toMatchObject({ past: [], future: [], selection: [] })
    })

    it('places unpositioned nodes without marking the draft dirty', () => {
        const g = {
            nodes: [node('a', null), node('b', null), node('c', null)],
            edges: [edge('a', 'b')],
        }
        const state = initEditor(g, 1)
        expect(state.graph.nodes.every((n) => n.position !== null)).toBe(true)
        expect(isDirty(state)).toBe(false)
        // b depends on a: a later column. a and c are both sources.
        expect(at(state, 'b')?.x).toBeGreaterThan(at(state, 'a')?.x ?? 0)
        expect(at(state, 'c')?.x).toBe(at(state, 'a')?.x)
        expect(at(state, 'c')?.y).not.toBe(at(state, 'a')?.y)
    })
})

describe('placeUnpositioned', () => {
    it('keeps placed nodes and the graph identity when nothing is missing', () => {
        const g = graph()
        expect(placeUnpositioned(g)).toBe(g)
    })

    it('terminates on cycles', () => {
        const g = {
            nodes: [node('a', null), node('b', null)],
            edges: [edge('a', 'b'), edge('b', 'a')],
        }
        const placed = placeUnpositioned(g)
        for (const n of placed.nodes) expect(n.position).not.toBeNull()
    })

    it('puts new nodes below placed ones', () => {
        const g = {
            nodes: [node('a', { x: 0, y: 500 }), node('b', null)],
            edges: [],
        }
        const b = placeUnpositioned(g).nodes[1]
        expect(b?.position?.y).toBeGreaterThan(500)
    })
})

describe('move', () => {
    it('one drag is one undo step, committed when it ends', () => {
        const start = initEditor(graph(), 1)
        const dragging = run(start, drag('a', 10, 0), drag('a', 20, 0))
        expect(at(dragging, 'a')).toEqual({ x: 20, y: 0 })
        expect(dragging.past).toEqual([])
        expect(isDirty(dragging)).toBe(true)

        const dropped = run(dragging, drag('a', 30, 5, true))
        expect(at(dropped, 'a')).toEqual({ x: 30, y: 5 })
        expect(dropped.past).toEqual([start.graph])
        expect(dropped.dragOrigin).toBeNull()
    })

    it('a drag back to the start is no edit', () => {
        const start = initEditor(graph(), 1)
        const state = run(start, drag('a', 10, 0), drag('a', 0, 0, true))
        expect(state.graph).toBe(start.graph)
        expect(state.past).toEqual([])
        expect(isDirty(state)).toBe(false)
    })

    it('ignores unknown node ids', () => {
        const start = initEditor(graph(), 1)
        expect(run(start, drag('zzz', 1, 1, true))).toEqual(start)
    })

    it('a move without drag (keyboard) commits immediately', () => {
        const state = run(initEditor(graph(), 1), drag('b', 300, 10, true))
        expect(state.past).toHaveLength(1)
    })
})

describe('undo / redo', () => {
    it('restores graphs and the saved (clean) state', () => {
        const start = initEditor(graph(), 1)
        const moved = run(start, drag('a', 50, 50, true))
        const undone = run(moved, { type: 'undo' })
        expect(undone.graph).toBe(start.graph)
        expect(isDirty(undone)).toBe(false)
        expect(undone.future).toEqual([moved.graph])

        const redone = run(undone, { type: 'redo' })
        expect(redone.graph).toBe(moved.graph)
        expect(isDirty(redone)).toBe(true)
    })

    it('a new edit clears redo', () => {
        const state = run(
            initEditor(graph(), 1),
            drag('a', 1, 1, true),
            { type: 'undo' },
            drag('b', 1, 1, true),
        )
        expect(state.future).toEqual([])
    })

    it('is a no-op on empty history and during a drag', () => {
        const start = initEditor(graph(), 1)
        expect(run(start, { type: 'undo' })).toBe(start)
        expect(run(start, { type: 'redo' })).toBe(start)
        const dragging = run(start, drag('a', 1, 1, true), drag('b', 5, 5))
        expect(run(dragging, { type: 'undo' })).toBe(dragging)
    })

    it('keeps at most HISTORY_LIMIT steps', () => {
        let state = initEditor(graph(), 1)
        for (let i = 1; i <= HISTORY_LIMIT + 5; i++)
            state = run(state, drag('a', i, 0, true))
        expect(state.past).toHaveLength(HISTORY_LIMIT)
    })

    it('drops undone nodes from the selection', () => {
        const state = run(
            initEditor(graph(), 1),
            { type: 'select', nodeIds: ['a'] },
            { type: 'copy' },
            { type: 'paste', ids: { a: 'a2' } },
            { type: 'undo' },
        )
        expect(state.selection).toEqual([])
    })
})

describe('select', () => {
    it('is not an edit', () => {
        const start = initEditor(graph(), 1)
        const state = run(start, { type: 'select', nodeIds: ['b'] })
        expect(state.selection).toEqual(['b'])
        expect(isDirty(state)).toBe(false)
        expect(state.past).toEqual([])
    })
})

describe('copy / paste', () => {
    it('copies selected nodes and only the edges between them', () => {
        const state = run(
            initEditor(graph(), 1),
            { type: 'select', nodeIds: ['a'] },
            { type: 'copy' },
        )
        expect(state.clipboard?.nodes.map((n) => n.id)).toEqual(['a'])
        expect(state.clipboard?.edges).toEqual([])
    })

    it('copy without selection keeps the clipboard', () => {
        const start = initEditor(graph(), 1)
        expect(run(start, { type: 'copy' })).toBe(start)
    })

    it('pastes with fresh ids, offset, remapped edges, as one undo step', () => {
        const start = initEditor(graph(), 1)
        const state = run(
            start,
            { type: 'select', nodeIds: ['a', 'b'] },
            { type: 'copy' },
            { type: 'paste', ids: { a: 'a2', b: 'b2' } },
        )
        expect(state.graph.nodes.map((n) => n.id)).toEqual([
            'a',
            'b',
            'a2',
            'b2',
        ])
        expect(at(state, 'a2')).toEqual({ x: PASTE_OFFSET, y: PASTE_OFFSET })
        expect(state.graph.edges).toContainEqual(edge('a2', 'b2'))
        expect(state.selection).toEqual(['a2', 'b2'])
        expect(state.past).toEqual([start.graph])
        expect(isDirty(state)).toBe(true)
        // The copy is independent of the original.
        expect(state.graph.nodes[2]?.config).toEqual(
            state.graph.nodes[0]?.config,
        )

        const again = run(state, { type: 'paste', ids: { a2: 'a3', b2: 'b3' } })
        expect(at(again, 'a3')).toEqual({
            x: 2 * PASTE_OFFSET,
            y: 2 * PASTE_OFFSET,
        })
    })

    it('paste with an empty clipboard is a no-op', () => {
        const start = initEditor(graph(), 1)
        expect(run(start, { type: 'paste', ids: {} })).toBe(start)
    })
})

describe('add', () => {
    const add = (id: string, x = 0, y = 0) =>
        ({ type: 'add', node: node(id, { x, y }) }) as const

    it('adds and selects the node as one undo step', () => {
        const added = run(initEditor(graph(), 1), add('c', 600, 0))
        expect(added.graph.nodes.map((n) => n.id)).toEqual(['a', 'b', 'c'])
        expect(added.selection).toEqual(['c'])
        expect(isDirty(added)).toBe(true)
        expect(run(added, { type: 'undo' }).graph.nodes).toHaveLength(2)
    })

    it('refuses a duplicate id, and adds during a drag', () => {
        const start = initEditor(graph(), 1)
        expect(run(start, add('a', 900, 900))).toBe(start)
        const dragging = run(start, drag('a', 5, 5))
        expect(run(dragging, add('c'))).toBe(dragging)
    })

    it('offsets nodes added onto an occupied spot', () => {
        const state = run(initEditor(graph(), 1), add('c'), add('d'))
        expect(at(state, 'c')).toEqual({ x: PASTE_OFFSET, y: PASTE_OFFSET })
        expect(at(state, 'd')).toEqual({
            x: 2 * PASTE_OFFSET,
            y: 2 * PASTE_OFFSET,
        })
    })
})

describe('saved', () => {
    it('marks the saved graph clean and keeps the undo stack', () => {
        const moved = run(initEditor(graph(), 1), drag('a', 9, 9, true))
        const saved = run(moved, {
            type: 'saved',
            graph: moved.graph,
            revision: 2,
        })
        expect(isDirty(saved)).toBe(false)
        expect(saved.revision).toBe(2)
        expect(saved.past).toHaveLength(1)
        // Undo after save: dirty again, relative to what the server holds.
        expect(isDirty(run(saved, { type: 'undo' }))).toBe(true)
    })

    it('edits made while a save was in flight stay dirty', () => {
        const first = run(initEditor(graph(), 1), drag('a', 9, 9, true))
        const sent = first.graph
        const later = run(first, drag('b', 1, 1, true))
        const saved = run(later, { type: 'saved', graph: sent, revision: 2 })
        expect(isDirty(saved)).toBe(true)
        expect(saved.graph).toBe(later.graph)
    })
})

it('newNodeId has the backend id shape', () => {
    expect(newNodeId()).toMatch(/^node_[0-9a-f]{32}$/)
})

describe('configure', () => {
    const configure = (
        nodeId: string,
        config: Record<string, string>,
        field: string,
    ): EditorAction => ({ type: 'configure', nodeId, config, field })
    const configOf = (state: EditorState, id: string) =>
        state.graph.nodes.find((n) => n.id === id)?.config

    it('replaces only the node config and is an unsaved, undoable edit', () => {
        const start = initEditor(graph(), 1)
        const state = run(start, configure('a', { message: 'hi' }, 'message'))
        expect(configOf(state, 'a')).toEqual({ message: 'hi' })
        expect(state.graph.nodes[1]).toBe(start.graph.nodes[1])
        expect(state.graph.edges).toBe(start.graph.edges)
        expect(isDirty(state)).toBe(true)
        const undone = run(state, { type: 'undo' })
        expect(undone.graph).toBe(start.graph)
        expect(isDirty(undone)).toBe(false)
    })

    it('typing on in one field is one undo step; another field is a new one', () => {
        const start = initEditor(graph(), 1)
        const typed = run(
            start,
            configure('a', { message: 'h' }, 'message'),
            configure('a', { message: 'hi' }, 'message'),
            configure('a', { message: 'hi', level: 'info' }, 'level'),
        )
        expect(typed.past).toHaveLength(2)
        const once = run(typed, { type: 'undo' })
        expect(configOf(once, 'a')).toEqual({ message: 'hi' })
        expect(run(once, { type: 'undo' }).graph).toBe(start.graph)
    })

    it('selecting or undoing ends the step', () => {
        const start = initEditor(graph(), 1)
        const state = run(
            start,
            configure('a', { message: 'h' }, 'message'),
            { type: 'select', nodeIds: ['a'] },
            configure('a', { message: 'hi' }, 'message'),
        )
        expect(state.past).toHaveLength(2)
    })

    it('ignores unknown nodes and edits during a drag', () => {
        const start = initEditor(graph(), 1)
        expect(run(start, configure('zz', { x: 'y' }, 'x'))).toBe(start)
        const dragging = run(start, drag('a', 5, 5, false))
        expect(run(dragging, configure('a', { x: 'y' }, 'x'))).toBe(dragging)
    })
})
