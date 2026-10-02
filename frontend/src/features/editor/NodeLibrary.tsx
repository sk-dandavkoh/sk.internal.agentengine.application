import type { UseQueryResult } from '@tanstack/react-query'
import { useState } from 'react'
import type { NodeCatalog, NodeDefinition, NodePort } from '../../api/nodes'
import { QuerySection } from '../../components/QuerySection'
import { Badge, Button, EmptyState, Input, Select } from '../../components/ui'
import { cardPort } from './graphView'
import { CategoryIcon, NodeCard } from './NodeCard'

// The node catalog as a browsable library (frontend/AGENTS §24). Everything
// shown comes from GET /nodes; new backend or plugin nodes appear without a
// frontend change. Filtering is local: the catalog is small and unpaginated.

/** dataTransfer type of a library item dragged onto the canvas. */
export const NODE_TYPE_MIME = 'application/x-akasha-node-type'

/** "control_flow" → "Control flow"; categories and capabilities are open sets. */
const humanize = (key: string) => {
    const text = key.replaceAll('_', ' ')
    return text.charAt(0).toUpperCase() + text.slice(1)
}

const CAPABILITY_LABELS: Readonly<Record<string, string>> = {
    supports_cancellation: 'Cancellable',
    has_side_effects: 'Side effects',
    is_control_flow: 'Control flow',
    streams_output: 'Streams output',
}
const capabilityLabel = (key: string) => CAPABILITY_LABELS[key] ?? humanize(key)

const enabledCapabilities = (d: NodeDefinition) =>
    Object.keys(d.capabilities)
        .filter((key) => d.capabilities[key])
        .sort()

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
        [d.name, d.node_type, d.description, d.category].some((s) =>
            s.toLowerCase().includes(q),
        )
    )
}

function Ports({ title, ports }: { title: string; ports: NodePort[] }) {
    return (
        <div>
            <h5 className="font-medium">{title}</h5>
            {ports.length === 0 ? (
                <p className="text-muted">None</p>
            ) : (
                <ul>
                    {ports.map((p) => (
                        <li key={p.name}>
                            <span className="font-mono">{p.name}</span>{' '}
                            <span className="text-muted">
                                ({p.type}
                                {p.required ? ', required' : ''}
                                {p.multiple ? ', multiple' : ''})
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
    const capabilities = enabledCapabilities(d)
    const docs = docsUrl(d)
    return (
        <li
            draggable
            onDragStart={(event) => {
                event.dataTransfer.setData(NODE_TYPE_MIME, d.node_type)
                event.dataTransfer.effectAllowed = 'copy'
            }}
            className="cursor-grab rounded-md border border-border p-2 active:cursor-grabbing"
        >
            <div className="flex items-start justify-between gap-2">
                <div className="flex min-w-0 gap-2">
                    <span className="mt-0.5 text-muted">
                        <CategoryIcon category={d.category} />
                    </span>
                    <div className="min-w-0">
                        <h4 className="font-semibold">{d.name}</h4>
                        <p className="truncate font-mono text-xs text-muted">
                            {d.node_type} · v{d.version}
                        </p>
                    </div>
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
            {d.description && <p className="mt-1 text-xs">{d.description}</p>}
            {capabilities.length > 0 && (
                <ul
                    aria-label="Capabilities"
                    className="mt-1 flex flex-wrap gap-1"
                >
                    {capabilities.map((key) => (
                        <li key={key}>
                            <Badge>{capabilityLabel(key)}</Badge>
                        </li>
                    ))}
                </ul>
            )}
            <details className="mt-1 text-xs">
                <summary className="focus-ring cursor-pointer rounded text-muted">
                    Preview and details
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
                    <Ports title="Inputs" ports={d.inputs} />
                    <Ports title="Outputs" ports={d.outputs} />
                    <div>
                        <h5 className="font-medium">Required settings</h5>
                        <p className={settings.length ? '' : 'text-muted'}>
                            {settings.length
                                ? settings.join(', ')
                                : 'None; works with defaults.'}
                        </p>
                    </div>
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
    const [capability, setCapability] = useState('')

    return (
        <section
            aria-labelledby="node-library"
            className="flex flex-col gap-2 rounded-md border border-border bg-surface p-3 lg:max-h-[40rem] lg:w-72 lg:overflow-y-auto"
        >
            <h2 id="node-library" className="font-semibold">
                Node library
            </h2>
            <QuerySection query={query} subject="the node library">
                {({ items }) => {
                    const categories = [
                        ...new Set(items.map((d) => d.category)),
                    ].sort()
                    const capabilities = [
                        ...new Set(items.flatMap(enabledCapabilities)),
                    ].sort()
                    const shown = items.filter(
                        (d) =>
                            matches(d, search) &&
                            (category === '' || d.category === category) &&
                            (capability === '' ||
                                d.capabilities[capability] === true),
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
                            <Select
                                label="Capability"
                                value={capability}
                                onChange={(e) => {
                                    setCapability(e.target.value)
                                }}
                            >
                                <option value="">Any capability</option>
                                {capabilities.map((c) => (
                                    <option key={c} value={c}>
                                        {capabilityLabel(c)}
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
                                    Add a node, or drag it onto the canvas.
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
