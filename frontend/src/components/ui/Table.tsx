import type { ReactNode } from 'react'
import { cx } from '../../lib/cx'

export interface TableColumn<Row> {
    key: string
    header: ReactNode
    cell: (row: Row) => ReactNode
    align?: 'start' | 'end'
}

export interface TableProps<Row> {
    /** Names the table for assistive technology. */
    caption: string
    /** Keep the caption for screen readers only, e.g. under a Card title. */
    hideCaption?: boolean
    columns: readonly TableColumn<Row>[]
    rows: readonly Row[]
    rowKey: (row: Row) => string
    /** Shown in place of rows when there are none. */
    empty?: ReactNode
}

// ponytail: no sorting, selection or virtualization; lists are paginated
// server-side. Add sortable headers when a feature needs them.
export function Table<Row>({
    caption,
    hideCaption = false,
    columns,
    rows,
    rowKey,
    empty = 'No entries.',
}: TableProps<Row>) {
    const alignClass = (align: TableColumn<Row>['align']) =>
        align === 'end' ? 'text-right' : 'text-left'

    return (
        // Narrow screens scroll the table, not the page.
        <div className="overflow-x-auto rounded-lg border border-border bg-surface">
            <table className="w-full border-collapse text-sm">
                <caption
                    className={cx(
                        'px-4 py-3 text-left font-semibold',
                        hideCaption && 'sr-only',
                    )}
                >
                    {caption}
                </caption>
                <thead className="bg-surface-muted text-muted">
                    <tr>
                        {columns.map((column) => (
                            <th
                                key={column.key}
                                scope="col"
                                className={cx(
                                    'px-4 py-2 font-medium whitespace-nowrap',
                                    alignClass(column.align),
                                )}
                            >
                                {column.header}
                            </th>
                        ))}
                    </tr>
                </thead>
                <tbody>
                    {rows.length === 0 ? (
                        <tr>
                            <td
                                colSpan={columns.length}
                                className="px-4 py-8 text-center text-muted"
                            >
                                {empty}
                            </td>
                        </tr>
                    ) : (
                        rows.map((row) => (
                            <tr
                                key={rowKey(row)}
                                className="border-t border-border"
                            >
                                {columns.map((column) => (
                                    <td
                                        key={column.key}
                                        className={cx(
                                            'px-4 py-2.5',
                                            alignClass(column.align),
                                        )}
                                    >
                                        {column.cell(row)}
                                    </td>
                                ))}
                            </tr>
                        ))
                    )}
                </tbody>
            </table>
        </div>
    )
}
