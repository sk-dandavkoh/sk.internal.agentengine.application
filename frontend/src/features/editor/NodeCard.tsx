import { Handle, Position } from '@xyflow/react'
import type { ReactNode } from 'react'
import { Icon } from '../../components/ui'
import { cx } from '../../lib/cx'

// The node as drawn on the canvas, shared with the library's preview so
// the preview is exactly what an added node looks like.

// Glyphs for the backend's built-in categories. Categories are an open set
// (plugins add their own): anything else gets the generic glyph.
const CATEGORY_GLYPHS: Readonly<Record<string, ReactNode>> = {
    ai: (
        <path d="M10 3v3M10 14v3M3 10h3M14 10h3M5 5l2 2M13 13l2 2M15 5l-2 2M7 13l-2 2" />
    ),
    web: (
        <>
            <circle cx="10" cy="10" r="7.5" />
            <path d="M2.5 10h15M10 2.5c2 2.2 3 4.7 3 7.5s-1 5.3-3 7.5c-2-2.2-3-4.7-3-7.5s1-5.3 3-7.5Z" />
        </>
    ),
    control_flow: <path d="M4 10h4l6-5M8 10l6 5M12 5h2v2M12 15h2v-2" />,
    input: <path d="M3 10h9M9 7l3 3-3 3M12 4h4v12h-4" />,
    output: <path d="M8 10h9M14 7l3 3-3 3M8 4H4v12h4" />,
    observability: (
        <>
            <path d="M2.5 10S5.5 4.5 10 4.5 17.5 10 17.5 10 14.5 15.5 10 15.5 2.5 10 2.5 10Z" />
            <circle cx="10" cy="10" r="2.5" />
        </>
    ),
    transform: <path d="M4 7h12l-3-3M16 13H4l3 3" />,
    communication: <path d="M3 5h14v10H3zM3 5l7 6 7-6" />,
}
const GENERIC_GLYPH = (
    <path d="M10 2.5 17 6v8l-7 3.5L3 14V6l7-3.5ZM3 6l7 3.5L17 6M10 9.5v8" />
)

/** Decorative; the category is always also given as text. */
export function CategoryIcon({ category }: { category: string }) {
    return <Icon>{CATEGORY_GLYPHS[category] ?? GENERIC_GLYPH}</Icon>
}

export interface CardPort {
    name: string
    /** Port signature (`portSignature`); undefined if the contract lacks it. */
    type?: string | undefined
    required?: boolean
    /** While a connection is being drawn: whether this port can take it. */
    fit?: 'compatible' | 'incompatible' | undefined
}

export interface NodeCardProps {
    title: string
    nodeType: string
    /** undefined: the type is unknown, so no category is known either. */
    category: string | undefined
    inputs: readonly CardPort[]
    outputs: readonly CardPort[]
    /** Required inputs without a connection. */
    missing?: readonly string[]
    /** Errors the backend's last validation reported on this node. */
    issues?: number
    selected?: boolean
    unknown?: boolean
    /** React Flow connection handles; only inside a ReactFlow canvas. */
    handles?: boolean
    /** Extra state under the ports, e.g. an execution's node status. */
    footer?: ReactNode
}

export function NodeCard({
    title,
    nodeType,
    category,
    inputs,
    outputs,
    missing = [],
    issues = 0,
    selected = false,
    unknown = false,
    handles = false,
    footer,
}: NodeCardProps) {
    return (
        <div
            className={cx(
                'min-w-44 rounded-md border bg-surface text-sm text-fg shadow-sm',
                selected
                    ? 'border-primary ring-2 ring-primary'
                    : issues > 0
                      ? 'border-danger'
                      : 'border-border-strong',
                unknown && 'border-dashed',
            )}
        >
            <div className="flex gap-2 border-b border-border px-3 py-2">
                {category !== undefined && (
                    <span className="mt-0.5 text-muted">
                        <CategoryIcon category={category} />
                    </span>
                )}
                <div className="min-w-0">
                    <p className="font-semibold">{title}</p>
                    <p className="font-mono text-xs text-muted">{nodeType}</p>
                    {/* Text, not just the dashed border: state is never color-only. */}
                    {unknown && (
                        <p className="text-xs font-medium text-warning-text">
                            Unknown node type
                        </p>
                    )}
                </div>
            </div>
            <div className="flex justify-between gap-4 py-1.5 text-xs">
                <ul aria-label="Inputs">
                    {inputs.map((port) => (
                        <PortItem
                            key={port.name}
                            port={port}
                            side="target"
                            handles={handles}
                            missing={missing.includes(port.name)}
                        />
                    ))}
                </ul>
                <ul aria-label="Outputs" className="text-right">
                    {outputs.map((port) => (
                        <PortItem
                            key={port.name}
                            port={port}
                            side="source"
                            handles={handles}
                            missing={false}
                        />
                    ))}
                </ul>
            </div>
            {issues > 0 && (
                <p className="border-t border-border px-3 py-1 text-xs font-medium text-danger-text">
                    {issues} validation {issues === 1 ? 'error' : 'errors'}
                </p>
            )}
            {missing.length > 0 && (
                <p className="border-t border-border px-3 py-1 text-xs font-medium text-warning-text">
                    Missing required input: {missing.join(', ')}
                </p>
            )}
            {footer !== undefined && (
                <div className="border-t border-border px-3 py-1.5 text-xs">
                    {footer}
                </div>
            )}
        </div>
    )
}

function PortItem({
    port,
    side,
    handles,
    missing,
}: {
    port: CardPort
    side: 'source' | 'target'
    handles: boolean
    missing: boolean
}) {
    return (
        <li className="relative px-3 py-0.5">
            {handles && (
                <Handle
                    type={side}
                    id={port.name}
                    position={
                        side === 'target' ? Position.Left : Position.Right
                    }
                    className={port.fit && `port-${port.fit}`}
                />
            )}
            <span className={cx(missing && 'text-warning-text')}>
                {port.name}
                {port.required && (
                    <>
                        <span aria-hidden="true">*</span>
                        <span className="sr-only"> (required)</span>
                    </>
                )}
            </span>
            {port.type !== undefined && (
                <span className="ml-1 font-mono text-[10px] text-muted">
                    {port.type}
                </span>
            )}
        </li>
    )
}
