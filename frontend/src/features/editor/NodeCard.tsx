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

export interface NodeCardProps {
    title: string
    nodeType: string
    /** undefined: the type is unknown, so no category is known either. */
    category: string | undefined
    inputs: readonly string[]
    outputs: readonly string[]
    selected?: boolean
    unknown?: boolean
    /** React Flow connection handles; only inside a ReactFlow canvas. */
    handles?: boolean
}

export function NodeCard({
    title,
    nodeType,
    category,
    inputs,
    outputs,
    selected = false,
    unknown = false,
    handles = false,
}: NodeCardProps) {
    return (
        <div
            className={cx(
                'min-w-44 rounded-md border bg-surface text-sm text-fg shadow-sm',
                selected
                    ? 'border-primary ring-2 ring-primary'
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
                        <li key={port} className="relative px-3 py-0.5">
                            {handles && (
                                <Handle
                                    type="target"
                                    id={port}
                                    position={Position.Left}
                                    isConnectable={false}
                                />
                            )}
                            {port}
                        </li>
                    ))}
                </ul>
                <ul aria-label="Outputs" className="text-right">
                    {outputs.map((port) => (
                        <li key={port} className="relative px-3 py-0.5">
                            {handles && (
                                <Handle
                                    type="source"
                                    id={port}
                                    position={Position.Right}
                                    isConnectable={false}
                                />
                            )}
                            {port}
                        </li>
                    ))}
                </ul>
            </div>
        </div>
    )
}
