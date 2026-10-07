import { useState, type ReactNode } from 'react'
import type { NodeExecution } from '../../../api/reports'
import { Button } from '../../../components/ui'
import { JsonBlock, UntrustedText } from './display'

// What a node got and what it produced, side by side (like n8n's input and
// output panes). Values come redacted from the report; nothing here could
// reveal more, and node-written text is never interpreted (UntrustedText).

/** Rows shown in table view; a table of thousands helps nobody. */
const MAX_TABLE_ROWS = 100

type Row = Record<string, unknown>
const isRow = (v: unknown): v is Row =>
    typeof v === 'object' && v !== null && !Array.isArray(v)

/** A list of objects reads best as a table; anything else as JSON. */
const asRows = (value: unknown): Row[] | null =>
    Array.isArray(value) && value.length > 0 && value.every(isRow)
        ? value
        : null

const cellText = (v: unknown) =>
    typeof v === 'string' ? v : v === undefined ? '' : JSON.stringify(v)

function RowsTable({ rows }: { rows: Row[] }) {
    const shown = rows.slice(0, MAX_TABLE_ROWS)
    // Columns in first-seen order: deterministic for the same data.
    const columns = [...new Set(shown.flatMap((r) => Object.keys(r)))]
    return (
        <div className="max-h-96 overflow-auto rounded-md border border-border">
            <table className="w-full text-left text-xs">
                <thead className="sticky top-0 bg-surface-muted">
                    <tr>
                        {columns.map((c) => (
                            <th
                                key={c}
                                scope="col"
                                className="px-2 py-1 font-semibold"
                            >
                                {c}
                            </th>
                        ))}
                    </tr>
                </thead>
                <tbody>
                    {shown.map((row, i) => (
                        <tr
                            key={i}
                            className="border-t border-border align-top"
                        >
                            {columns.map((c) => (
                                <td
                                    key={c}
                                    className="max-w-xs px-2 py-1 break-words"
                                >
                                    <UntrustedText text={cellText(row[c])} />
                                </td>
                            ))}
                        </tr>
                    ))}
                </tbody>
            </table>
            {rows.length > shown.length && (
                <p className="px-2 py-1 text-xs text-muted">
                    Showing the first {MAX_TABLE_ROWS} of{' '}
                    {rows.length.toLocaleString()} rows; the JSON view has all.
                </p>
            )}
        </div>
    )
}

/** One value: plain text for text, a table or JSON for structured data. */
export function DataView({ value, label }: { value: unknown; label: string }) {
    const rows = asRows(value)
    const [view, setView] = useState<'table' | 'json'>('table')
    if (typeof value === 'string')
        return value === '' ? (
            <p className="text-xs text-muted">Empty text.</p>
        ) : (
            <UntrustedText block text={value} />
        )
    if (!rows) return <JsonBlock value={value} />
    return (
        <div className="flex flex-col gap-1">
            <div
                role="group"
                aria-label={`View of ${label}`}
                className="flex gap-1"
            >
                {(['table', 'json'] as const).map((v) => (
                    <Button
                        key={v}
                        size="sm"
                        variant={view === v ? 'primary' : 'ghost'}
                        aria-pressed={view === v}
                        onClick={() => {
                            setView(v)
                        }}
                    >
                        {v === 'table'
                            ? `Table (${String(rows.length)})`
                            : 'JSON'}
                    </Button>
                ))}
            </div>
            {view === 'table' ? (
                <RowsTable rows={rows} />
            ) : (
                <JsonBlock value={value} />
            )}
        </div>
    )
}

function Pane({ title, children }: { title: string; children: ReactNode }) {
    return (
        <section
            aria-label={title}
            className="flex min-w-0 flex-col gap-3 rounded-md border border-border bg-surface p-3"
        >
            <h3 className="text-sm font-semibold">{title}</h3>
            {children}
        </section>
    )
}

function inputsNote(node: NodeExecution) {
    if (node.inputs === undefined)
        return 'This server does not report node inputs.'
    if (node.inputs === null)
        return 'The node never started, so nothing reached it.'
    return 'Nothing reached it over a connection: it starts the flow, or it ran on its defaults.'
}

/**
 * Input | Output of one node in one execution. `title` names a node id
 * (its label, else its type) for "from …" lines.
 */
export function NodeData({
    node,
    title,
}: {
    node: NodeExecution
    title: (nodeId: string) => string
}) {
    const inputs = node.inputs ?? []
    const outputs = node.outputs === null ? [] : Object.entries(node.outputs)
    return (
        <div className="grid gap-3 lg:grid-cols-2">
            <Pane title="Input">
                {inputs.length === 0 ? (
                    <p className="text-sm text-muted">{inputsNote(node)}</p>
                ) : (
                    inputs.map((input, i) => (
                        <div key={i} className="flex flex-col gap-1">
                            <p className="text-xs">
                                <span className="font-semibold">
                                    {input.port}
                                </span>{' '}
                                <span className="text-muted">
                                    from {title(input.source_node_id)} ›{' '}
                                    {input.source_port}
                                </span>
                            </p>
                            <DataView value={input.value} label={input.port} />
                        </div>
                    ))
                )}
            </Pane>
            <Pane title="Output">
                {node.outputs === null ? (
                    <p className="text-sm text-muted">
                        {node.status === 'completed'
                            ? 'No outputs.'
                            : 'Only a completed node has outputs.'}
                    </p>
                ) : outputs.length === 0 ? (
                    <p className="text-sm text-muted">No outputs.</p>
                ) : (
                    outputs.map(([port, value]) => (
                        <div key={port} className="flex flex-col gap-1">
                            <p className="text-xs font-semibold">{port}</p>
                            <DataView value={value} label={port} />
                        </div>
                    ))
                )}
            </Pane>
        </div>
    )
}
