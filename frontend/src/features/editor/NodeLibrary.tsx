import type { UseQueryResult } from '@tanstack/react-query'
import { useState, type DragEvent } from 'react'
import type { NodeCatalog, NodeDefinition, NodePort } from '../../api/nodes'
import { QuerySection } from '../../components/QuerySection'
import { Badge, Button, EmptyState, Input, Select } from '../../components/ui'
import { cardPort, portSignature } from './graphView'
import { CategoryIcon, NodeCard } from './NodeCard'
import { capabilitiesOf, describeType, humanize } from './vocabulary'

// The node catalog as a browsable library (frontend/AGENTS §24). Everything
// shown comes from GET /nodes; new backend or plugin nodes appear without a
// frontend change. Filtering is local: the catalog is small and unpaginated.

/** dataTransfer type of a library item dragged onto the canvas. */
export const NODE_TYPE_MIME = 'application/x-akasha-node-type'

/** Capabilities worth a badge in the list; the rest are in the details. */
const NOTABLE = new Set(['Changes things outside', 'Waits for a person'])

/** The browser's drag image of a list item also captured the items below
 * it; an off-screen chip with just the node's name is unambiguous. */
function setDragChip(event: DragEvent<HTMLElement>, name: string) {
    const chip = document.createElement('div')
    chip.textContent = name
    chip.className =
        'fixed -top-96 left-0 rounded-md border border-primary bg-surface px-3 py-2 text-sm font-semibold text-fg shadow'
    document.body.append(chip)
    event.dataTransfer.setDragImage(chip, 16, 16)
    // The browser snapshots the chip synchronously; it can go right after.
    setTimeout(() => {
        chip.remove()
    })
}

/** Required config keys from the JSON Schema; the inspector is Phase 09. */
function requiredSettings(d: NodeDefinition): string[] {
    const required = d.config_schema['required']
    return Array.isArray(required)
        ? required.filter((k): k is string => typeof k === 'string')
        : []
}

/** Only web links: the URL is plugin metadata, so no `javascript:` hrefs. */
function docsUrl(d: NodeDefinition) {
    if (d.documentation_url === null) return null
    try {
        const url = new URL(d.documentation_url)
        return url.protocol === 'https:' || url.protocol === 'http:'
            ? url.href
            : null
    } catch {
        return null
    }
}

function matches(d: NodeDefinition, query: string) {
    const q = query.trim().toLowerCase()
    return (
        q === '' ||
        [d.name, d.node_type, d.description, humanize(d.category)].some((s) =>
            s.toLowerCase().includes(q),
        )
    )
}

function Ports({
    title,
    ports,
    inputs = false,
}: {
    title: string
    ports: NodePort[]
    inputs?: boolean
}) {
    return (
        <div>
            <h5 className="font-medium">{title}</h5>
            {ports.length === 0 ? (
                <p className="text-muted">None</p>
            ) : (
                <ul>
                    {ports.map((p) => (
                        <li key={p.name}>
                            <span className="font-medium">{p.name}</span>{' '}
                            <span className="text-muted">
                                ({describeType(portSignature(p)).label}
                                {inputs &&
                                    (p.required ? ', required' : ', optional')}
                                )
                            </span>
                            {p.description && (
                                <span className="block text-muted">
                                    {p.description}
                                </span>
                            )}
                        </li>
                    ))}
                </ul>
            )}
        </div>
    )
}

function LibraryItem({
    definition: d,
    onAdd,
}: {
    definition: NodeDefinition
    onAdd: (d: NodeDefinition) => void
}) {
    const settings = requiredSettings(d)
    const capabilities = capabilitiesOf(d)
    const docs = docsUrl(d)
    return (
        <li
            draggable
            onDragStart={(event) => {
                event.dataTransfer.setData(NODE_TYPE_MIME, d.node_type)
                event.dataTransfer.effectAllowed = 'copy'
                setDragChip(event, d.name)
            }}
            className="cursor-grab rounded-md border border-border p-2 hover:border-border-strong hover:bg-surface-muted active:cursor-grabbing"
        >
            <div className="flex items-start gap-2">
                <span className="mt-0.5 text-muted">
                    <CategoryIcon category={d.category} />
                </span>
                <div className="min-w-0 flex-1">
                    <h4 className="font-semibold">{d.name}</h4>
                    {d.description && (
                        <p className="text-xs text-muted">{d.description}</p>
                    )}
                </div>
                <Button
                    size="sm"
                    onClick={() => {
                        onAdd(d)
                    }}
                    aria-label={`Add ${d.name} to graph`}
                >
                    Add
                </Button>
            </div>
            {capabilities.some((c) => NOTABLE.has(c.label)) && (
                <ul
                    aria-label="Good to know"
                    className="mt-1 ml-7 flex flex-wrap gap-1"
                >
                    {capabilities
                        .filter((c) => NOTABLE.has(c.label))
                        .map((c) => (
                            <li key={c.label} title={c.description}>
                                <Badge tone="warning">{c.label}</Badge>
                            </li>
                        ))}
                </ul>
            )}
            <details className="mt-1 ml-7 text-xs">
                <summary className="focus-ring cursor-pointer rounded text-muted">
                    Details
                    {/* Thirteen identical summaries are ambiguous out of
                        context, e.g. in a screen reader's control list. */}
                    <span className="sr-only"> of {d.name}</span>
                </summary>
                <div className="mt-1 flex flex-col gap-2">
                    {/* As it will look on the canvas. Hidden from assistive
                        tech: the lists below say the same in text. */}
                    <div aria-hidden="true" className="pointer-events-none">
                        <NodeCard
                            title={d.name}
                            nodeType={d.node_type}
                            category={d.category}
                            inputs={d.inputs.map((p) => cardPort(p))}
                            outputs={d.outputs.map((p) => cardPort(p, false))}
                        />
                    </div>
                    <Ports title="Receives (inputs)" ports={d.inputs} inputs />
                    <Ports title="Sends (outputs)" ports={d.outputs} />
                    <div>
                        <h5 className="font-medium">
                            Settings you must fill in
                        </h5>
                        <p className={settings.length ? '' : 'text-muted'}>
                            {settings.length
                                ? settings.map(humanize).join(', ')
                                : 'None; works with its defaults.'}
                        </p>
                    </div>
                    {capabilities.length > 0 && (
                        <div>
                            <h5 className="font-medium">Good to know</h5>
                            <ul aria-label="Capabilities">
                                {capabilities.map((c) => (
                                    <li key={c.label}>
                                        <span className="font-medium">
                                            {c.label}
                                        </span>
                                        {c.description && (
                                            <span className="text-muted">
                                                : {c.description}
                                            </span>
                                        )}
                                    </li>
                                ))}
                            </ul>
                        </div>
                    )}
                    <p className="font-mono text-muted">
                        {d.node_type} · v{d.version}
                    </p>
                    {docs && (
                        <a
                            href={docs}
                            target="_blank"
                            rel="noreferrer noopener"
                            className="focus-ring rounded text-primary underline"
                        >
                            Documentation for {d.name}
                        </a>
                    )}
                </div>
            </details>
        </li>
    )
}

export function NodeLibrary({
    query,
    onAdd,
}: {
    query: UseQueryResult<NodeCatalog>
    onAdd: (d: NodeDefinition) => void
}) {
    const [search, setSearch] = useState('')
    const [category, setCategory] = useState('')

    return (
        <section
            aria-labelledby="node-library"
            // Scrolls on its own; beside the canvas it is exactly as tall.
            className="flex max-h-[24rem] min-h-0 flex-col gap-2 overflow-y-auto rounded-md border border-border bg-surface p-3 xl:max-h-none"
        >
            <h2 id="node-library" className="font-semibold">
                Node library
            </h2>
            <QuerySection query={query} subject="the node library">
                {({ items }) => {
                    const categories = [
                        ...new Set(items.map((d) => d.category)),
                    ].sort()
                    const shown = items.filter(
                        (d) =>
                            matches(d, search) &&
                            (category === '' || d.category === category),
                    )
                    const groups = categories
                        .map((c) => ({
                            category: c,
                            nodes: shown.filter((d) => d.category === c),
                        }))
                        .filter((g) => g.nodes.length > 0)
                    return (
                        <div className="flex flex-col gap-2">
                            <Input
                                type="search"
                                label="Search nodes"
                                placeholder="e.g. e-mail, file, AI"
                                value={search}
                                onChange={(e) => {
                                    setSearch(e.target.value)
                                }}
                            />
                            <Select
                                label="Category"
                                value={category}
                                onChange={(e) => {
                                    setCategory(e.target.value)
                                }}
                            >
                                <option value="">All categories</option>
                                {categories.map((c) => (
                                    <option key={c} value={c}>
                                        {humanize(c)}
                                    </option>
                                ))}
                            </Select>
                            <p role="status" className="text-xs text-muted">
                                {shown.length} of {items.length} nodes
                            </p>
                            {items.length === 0 ? (
                                <EmptyState
                                    title="No nodes registered"
                                    titleAs="h3"
                                    description="The backend registry has no node types. Install a plugin to add some."
                                />
                            ) : shown.length === 0 ? (
                                <EmptyState
                                    title="No matching nodes"
                                    titleAs="h3"
                                    description="Change the search or filters."
                                />
                            ) : (
                                <p className="text-xs text-muted">
                                    Drag a node onto the canvas, or click Add.
                                </p>
                            )}
                            {groups.map((g) => (
                                <section
                                    key={g.category}
                                    aria-label={humanize(g.category)}
                                >
                                    <h3 className="mb-1 text-xs font-semibold uppercase text-muted">
                                        {humanize(g.category)}
                                    </h3>
                                    <ul className="flex flex-col gap-2 text-sm">
                                        {g.nodes.map((d) => (
                                            <LibraryItem
                                                key={d.node_type}
                                                definition={d}
                                                onAdd={onAdd}
                                            />
                                        ))}
                                    </ul>
                                </section>
                            ))}
                        </div>
                    )
                }}
            </QuerySection>
        </section>
    )
}
