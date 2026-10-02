import { Fragment, type ReactNode } from 'react'
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
    /**
     * Extra content shown in a full-width row under `row` (e.g. an expanded
     * disclosure); null/undefined for none. The disclosure control and its
     * state belong to the caller; give the content `id` for aria-controls.
     */
    details?: (row: Row) => ReactNode
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
    details,
}: TableProps<Row>) {
    const alignClass = (align: TableColumn<Row>['align']) =>
        align === 'end' ? 'text-right' : 'text-left'

    return (
        // Narrow screens scroll the table, not the page. Focusable, so the
        // keyboard can scroll it too (WCAG 2.1.1). `relative` makes it the
        // containing block of absolutely positioned content (sr-only text),
        // which it otherwise would not clip, widening the page.
        <div
            role="region"
            aria-label={caption}
            tabIndex={0}
            className="focus-ring @container relative overflow-x-auto rounded-lg border border-border bg-surface"
        >
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
                        rows.map((row) => {
                            const extra = details?.(row)
                            return (
                                <Fragment key={rowKey(row)}>
                                    <tr className="border-t border-border">
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
                                    {extra != null && (
                                        <tr>
                                            <td
                                                colSpan={columns.length}
                                                className="px-4 pb-3"
                                            >
                                                {/* Pinned to the visible width of the scroll
                                                    container, so a wide table scrolled sideways
                                                    never hides it. */}
                                                <div className="sticky left-4 w-[calc(100cqw-2rem)]">
                                                    {extra}
                                                </div>
                                            </td>
                                        </tr>
                                    )}
                                </Fragment>
                            )
                        })
                    )}
                </tbody>
            </table>
        </div>
    )
}
