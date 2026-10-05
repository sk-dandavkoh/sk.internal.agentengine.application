import { expect, test, type Locator, type Page } from '@playwright/test'
import { stubSession } from './session'

// The draft editor in a real browser: layout-dependent behavior jsdom
// cannot show (drag geometry, edge paths, minimap, wheel zoom). The backend
// is an in-memory fake at the network boundary that enforces revisions.
const AT = '2026-10-02T08:00:00Z'
const A = `node_${'a'.repeat(32)}`
const B = `node_${'b'.repeat(32)}`

interface Position {
    x: number
    y: number
}
interface Draft {
    graph: {
        nodes: {
            id: string
            node_type: string
            config: object
            label: string
            position: Position | null
        }[]
        edges: object[]
    }
    triggers: object[]
    dependencies: object[]
}

function project(draft: Draft, revision = 1) {
    return {
        id: 'p1',
        name: 'Leads',
        description: '',
        status: 'published',
        owner_id: 'user_1',
        revision,
        current_version: 1,
        created_at: AT,
        updated_at: AT,
        draft,
        files: [],
    }
}

const port = (name: string) => ({
    name,
    type: 'json',
    required: false,
    nullable: true,
    multiple: false,
    description: '',
})

interface Catalog {
    items: object[]
    port_compatibility: Record<string, string[]>
}

async function fakeBackend(
    p: Page,
    start?: { draft: Draft; catalog: Catalog },
) {
    let revision = 1
    let draft: Draft = start?.draft ?? {
        graph: {
            nodes: [
                {
                    id: A,
                    node_type: 'core.input',
                    config: {},
                    label: 'Lead in',
                    position: { x: 0, y: 0 },
                },
                {
                    id: B,
                    node_type: 'core.log',
                    config: {},
                    label: 'Log it',
                    position: null, // never placed: the editor lays it out
                },
            ],
            edges: [
                {
                    source_node_id: A,
                    source_port: 'value',
                    target_node_id: B,
                    target_port: 'value',
                },
            ],
        },
        triggers: [],
        dependencies: [],
    }
    const saved: Draft[] = []
    await p.route('**/api/v1/**', async (route) => {
        const request = route.request()
        const path = new URL(request.url()).pathname
        const method = request.method()
        if (path === '/api/v1/nodes')
            return route.fulfill({
                json: start?.catalog ?? {
                    items: ['core.input', 'core.log'].map((node_type) => ({
                        node_type,
                        version: '1.0.0',
                        name: node_type,
                        description: '',
                        category: 'core',
                        documentation_url: null,
                        inputs: node_type === 'core.log' ? [port('value')] : [],
                        outputs: [port('value')],
                        config_schema: {},
                        capabilities: {},
                    })),
                    port_compatibility: { json: ['json', 'json[]'] },
                },
            })
        if (path === '/api/v1/projects/p1' && method === 'GET')
            return route.fulfill({ json: project(draft, revision) })
        if (path === '/api/v1/projects/p1' && method === 'PUT') {
            const body = request.postDataJSON() as {
                expected_revision: number
                draft: Draft
            }
            if (body.expected_revision !== revision)
                return route.fulfill({
                    status: 409,
                    json: {
                        code: 'CONCURRENT_MODIFICATION',
                        message: 'Reload and retry.',
                        details: {},
                        request_id: 'r',
                    },
                })
            revision += 1
            draft = body.draft
            saved.push(body.draft)
            return route.fulfill({ json: project(draft, revision) })
        }
        return route.fulfill({
            status: 404,
            json: {
                code: 'NOT_FOUND',
                message: 'Not found.',
                details: {},
                request_id: 'r',
            },
        })
    })
    await stubSession(p)
    return saved
}

const node = (p: Page, name: RegExp) =>
    p.getByRole('group', { name }).and(p.locator('.react-flow__node'))

test('renders, zooms, drags and saves a draft graph', async ({ page: p }) => {
    const saved = await fakeBackend(p)
    await p.goto('/projects/p1/editor')

    await expect(
        p.getByRole('heading', { level: 1, name: 'Draft editor: Leads' }),
    ).toBeVisible()
    await expect(p.getByText(/latest being v1, stay unchanged/)).toBeVisible()

    // Nodes, the edge between them (a real SVG path), grid and minimap.
    await expect(node(p, /Lead in/)).toBeVisible()
    await expect(node(p, /Log it/)).toBeVisible()
    await expect(p.locator('.react-flow__edge path').first()).toHaveAttribute(
        'd',
        /\S/,
    )
    await expect(p.locator('.react-flow__background')).toBeVisible()
    await expect(p.locator('.react-flow__minimap-node')).toHaveCount(2)
    // The never-placed node was laid out to the right of its source.
    const a0 = await node(p, /Lead in/).boundingBox()
    const b0 = await node(p, /Log it/).boundingBox()
    expect(b0?.x).toBeGreaterThan((a0?.x ?? 0) + (a0?.width ?? 0))

    // Zoom: controls and the wheel change the viewport.
    const zoom = p.getByText(/^Zoom \d+%$/)
    const before = await zoom.textContent()
    await p.getByRole('button', { name: 'Zoom In' }).click()
    await expect(zoom).not.toHaveText(before ?? '')
    await p.getByRole('button', { name: 'Fit View' }).click()

    // Pan: dragging the empty pane moves every node, but edits nothing.
    const pane = p.locator('.react-flow__pane')
    const box = await pane.boundingBox()
    if (!box) throw new Error('no pane')
    const a1 = await node(p, /Lead in/).boundingBox()
    await p.mouse.move(box.x + 10, box.y + box.height - 10)
    await p.mouse.down()
    await p.mouse.move(box.x + 60, box.y + box.height - 40, { steps: 5 })
    await p.mouse.up()
    const a2 = await node(p, /Lead in/).boundingBox()
    expect(a2?.x).toBeCloseTo((a1?.x ?? 0) + 50, 0)
    await expect(p.getByText('All changes saved to draft')).toBeVisible()

    // Drag a node: one undoable, unsaved change.
    const lead = node(p, /Lead in/)
    await lead.hover()
    await p.mouse.down()
    await p.mouse.move((a2?.x ?? 0) + 20, (a2?.y ?? 0) + 120, { steps: 8 })
    await p.mouse.up()
    await expect(p.getByText('Unsaved changes')).toBeVisible()
    await expect(lead).toHaveClass(/selected/)
    await expect(
        p.getByRole('button', { name: /Lead in/, pressed: true }),
    ).toBeVisible()

    // Explicit save with the shortcut; the saved draft carries the layout.
    await p.keyboard.press('ControlOrMeta+s')
    await expect(p.getByText('All changes saved to draft')).toBeVisible()
    expect(saved).toHaveLength(1)
    const positions = saved[0]?.graph.nodes.map((n) => n.position)
    expect(positions?.[0]?.y).toBeGreaterThan(0)
    expect(positions?.[1]).not.toBeNull()

    // Undo is still available after saving, and leaving now asks first.
    await p.getByRole('button', { name: 'Undo' }).click()
    await expect(p.getByText('Unsaved changes')).toBeVisible()
    await p.getByRole('link', { name: 'Back to project' }).click()
    const leave = p.getByRole('dialog', { name: 'Leave with unsaved changes?' })
    await expect(leave).toBeVisible()
    await leave.getByRole('button', { name: 'Stay in editor' }).click()
    await expect(p).toHaveURL(/\/projects\/p1\/editor$/)
})

test('drags a node from the library onto the canvas', async ({ page: p }) => {
    const saved = await fakeBackend(p)
    await p.goto('/projects/p1/editor')
    const library = p.getByRole('region', { name: 'Node library' })
    await expect(library.getByText('2 of 2 nodes')).toBeVisible()

    // Real HTML5 drag and drop, onto an empty spot of the pane.
    const pane = p.locator('.react-flow__pane')
    const box = await pane.boundingBox()
    if (!box) throw new Error('no pane')
    const target = { x: box.width - 120, y: 30 }
    await library
        .getByRole('listitem')
        .filter({ has: p.getByRole('heading', { name: 'core.log' }) })
        .dragTo(pane, { targetPosition: target })

    await expect(p.locator('.react-flow__node')).toHaveCount(3)
    const added = p.locator('.react-flow__node.selected')
    await expect(added).toContainText('core.log')
    // The node's top-left lands at the drop point (snapped to the grid).
    const at = await added.boundingBox()
    expect(Math.abs((at?.x ?? 0) - (box.x + target.x))).toBeLessThan(15)
    expect(Math.abs((at?.y ?? 0) - (box.y + target.y))).toBeLessThan(15)

    await p.keyboard.press('ControlOrMeta+s')
    await expect(p.getByText('All changes saved to draft')).toBeVisible()
    expect(saved[0]?.graph.nodes.map((n) => n.node_type)).toEqual([
        'core.input',
        'core.log',
        'core.log',
    ])
})

// -- connections ------------------------------------------------------------

const SRC = `node_${'c'.repeat(32)}`
const SINK = `node_${'d'.repeat(32)}`
const SINK2 = `node_${'e'.repeat(32)}`

const typed = (name: string, type: string, required = false) => ({
    ...port(name),
    type,
    required,
    nullable: false,
})
const definition = (
    node_type: string,
    inputs: object[],
    outputs: object[],
) => ({
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

function connectable(): { draft: Draft; catalog: Catalog } {
    const at = (
        id: string,
        label: string,
        node_type: string,
        x: number,
        y: number,
    ) => ({
        id,
        node_type,
        config: {},
        label,
        position: { x, y },
    })
    return {
        draft: {
            graph: {
                nodes: [
                    at(SRC, 'Source', 't.text', 0, 60),
                    at(SINK, 'Sink one', 't.sink', 380, 0),
                    at(SINK2, 'Sink two', 't.sink', 380, 220),
                ],
                edges: [],
            },
            triggers: [],
            dependencies: [],
        },
        catalog: {
            items: [
                definition('t.text', [], [typed('text', 'string')]),
                definition(
                    't.sink',
                    [typed('n', 'number', true), typed('s', 'string')],
                    [],
                ),
            ],
            // An excerpt of the backend's table.
            port_compatibility: { string: ['string', 'string[]', 'json'] },
        },
    }
}

const handle = (p: Page, nodeId: string, port: string) =>
    p.locator(
        `.react-flow__handle[data-nodeid="${nodeId}"][data-handleid="${port}"]`,
    )

async function center(locator: Locator) {
    const box = await locator.boundingBox()
    if (!box) throw new Error('not rendered')
    return { x: box.x + box.width / 2, y: box.y + box.height / 2 }
}

/** Presses on `from` and moves onto `to`; the caller releases. */
async function dragOnto(
    p: Page,
    from: { x: number; y: number },
    to: { x: number; y: number },
) {
    await p.mouse.move(from.x, from.y)
    await p.mouse.down()
    await p.mouse.move(to.x, to.y, { steps: 10 })
}

test('connects, refuses, reconnects and deletes edges by dragging; they persist', async ({
    page: p,
}) => {
    const saved = await fakeBackend(p, connectable())
    await p.goto('/projects/p1/editor')
    await expect(node(p, /^Sink one/)).toBeVisible()
    await p.getByRole('button', { name: 'Fit View' }).click()
    const edges = p.locator('.react-flow__edge')
    // A required input with nothing connected says so on the card.
    await expect(node(p, /^Sink one/)).toContainText(
        'Missing required input: n',
    )

    // While dragging from a string output, every port shows whether it fits.
    await dragOnto(
        p,
        await center(handle(p, SRC, 'text')),
        await center(handle(p, SINK, 'n')),
    )
    await expect(handle(p, SINK, 's')).toHaveClass(/port-compatible/)
    await expect(handle(p, SINK, 'n')).toHaveClass(/port-incompatible/)
    await expect(p.locator('.react-flow__connection')).toHaveClass(/invalid/)
    // Dropping on the number input is refused, with the reason.
    await p.mouse.up()
    await expect(p.getByText('Not connected')).toBeVisible()
    await expect(
        p.getByText('Output "text" (string) cannot feed input "n" (number).'),
    ).toBeVisible()
    await expect(edges).toHaveCount(0)
    await expect(handle(p, SINK, 's')).not.toHaveClass(/port-compatible/)

    // The string input takes it.
    await dragOnto(
        p,
        await center(handle(p, SRC, 'text')),
        await center(handle(p, SINK, 's')),
    )
    await expect(p.locator('.react-flow__connection')).toHaveClass(/valid/)
    await p.mouse.up()
    await expect(edges).toHaveCount(1)
    await expect(
        p.getByRole('group', {
            name: 'Connection from Source text to Sink one s',
        }),
    ).toBeVisible()
    await expect(p.getByText('Unsaved changes')).toBeVisible()

    // The same connection again would be a duplicate: refused.
    await dragOnto(
        p,
        await center(handle(p, SRC, 'text')),
        await center(handle(p, SINK, 's')),
    )
    await p.mouse.up()
    await expect(
        p.getByText('These ports are already connected.'),
    ).toBeVisible()
    await expect(edges).toHaveCount(1)

    // Reconnect: drag the edge's target end over to the other sink.
    await dragOnto(
        p,
        await center(p.locator('.react-flow__edgeupdater-target')),
        await center(handle(p, SINK2, 's')),
    )
    await p.mouse.up()
    await expect(
        p.getByRole('group', {
            name: 'Connection from Source text to Sink two s',
        }),
    ).toBeVisible()
    await expect(edges).toHaveCount(1)

    // Select the edge, delete it with the keyboard, undo it back.
    await p.getByRole('group', { name: /^Connection from Source/ }).click()
    await expect(edges.first()).toHaveClass(/selected/)
    await p.keyboard.press('Delete')
    await expect(edges).toHaveCount(0)
    await p.keyboard.press('ControlOrMeta+z')
    await expect(edges).toHaveCount(1)

    await p.keyboard.press('ControlOrMeta+s')
    await expect(p.getByText('All changes saved to draft')).toBeVisible()
    expect(saved.at(-1)?.graph.edges).toEqual([
        {
            source_node_id: SRC,
            source_port: 'text',
            target_node_id: SINK2,
            target_port: 's',
        },
    ])

    // Reload: the graph comes back from the (fake) server as saved.
    await p.reload()
    await expect(
        p.getByRole('group', {
            name: 'Connection from Source text to Sink two s',
        }),
    ).toBeVisible()
    await expect(p.locator('.react-flow__edge path').first()).toHaveAttribute(
        'd',
        /\S/,
    )
    await expect(p.getByText('All changes saved to draft')).toBeVisible()
})

test('box-selects several nodes, then duplicates and deletes them', async ({
    page: p,
}) => {
    const saved = await fakeBackend(p, connectable())
    await p.goto('/projects/p1/editor')
    await expect(node(p, /^Sink two/)).toBeVisible()
    await p.getByRole('button', { name: 'Fit View' }).click()

    // Shift-drag a box around both sinks.
    const one = await node(p, /^Sink one/).boundingBox()
    const two = await node(p, /^Sink two/).boundingBox()
    if (!one || !two) throw new Error('not rendered')
    await p.keyboard.down('Shift')
    await p.mouse.move(one.x - 15, one.y - 15)
    await p.mouse.down()
    await p.mouse.move(two.x + two.width + 15, two.y + two.height + 15, {
        steps: 10,
    })
    await p.mouse.up()
    await p.keyboard.up('Shift')
    await expect(p.locator('.react-flow__node.selected')).toHaveCount(2)

    await p.getByRole('button', { name: 'Duplicate' }).click()
    await expect(p.locator('.react-flow__node')).toHaveCount(5)
    await expect(p.locator('.react-flow__node.selected')).toHaveCount(2)
    await p.getByRole('button', { name: 'Delete' }).click()
    await expect(p.locator('.react-flow__node')).toHaveCount(3)

    await p.getByRole('button', { name: 'Undo' }).click()
    await expect(p.locator('.react-flow__node')).toHaveCount(5)
    await p.keyboard.press('ControlOrMeta+s')
    await expect(p.getByText('All changes saved to draft')).toBeVisible()
    expect(saved.at(-1)?.graph.nodes).toHaveLength(5)
})
