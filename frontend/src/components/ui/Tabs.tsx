import {
    useId,
    useRef,
    useState,
    type KeyboardEvent,
    type ReactNode,
} from 'react'
import { cx } from '../../lib/cx'

export interface TabItem {
    id: string
    label: string
    content: ReactNode
}

export interface TabsProps {
    /** Accessible name of the tab list. */
    label: string
    items: readonly TabItem[]
    /** Controlled selection (e.g. from the URL); omit for internal state. */
    value?: string
    defaultValue?: string
    onValueChange?: (id: string) => void
}

/** WAI-ARIA tabs with roving focus; arrow keys select (automatic activation). */
export function Tabs({
    label,
    items,
    value,
    defaultValue,
    onValueChange,
}: TabsProps) {
    const [internal, setInternal] = useState(defaultValue ?? items[0]?.id)
    const selected = value ?? internal
    const baseId = useId()
    const tabRefs = useRef<(HTMLButtonElement | null)[]>([])

    function select(index: number) {
        const item = items[index]
        if (!item) return
        setInternal(item.id)
        onValueChange?.(item.id)
        tabRefs.current[index]?.focus()
    }

    function handleKeyDown(event: KeyboardEvent, index: number) {
        const last = items.length - 1
        const next = {
            ArrowRight: index === last ? 0 : index + 1,
            ArrowLeft: index === 0 ? last : index - 1,
            Home: 0,
            End: last,
        }[event.key]
        if (next === undefined) return
        event.preventDefault()
        select(next)
    }

    return (
        <div className="flex flex-col gap-4">
            <div
                role="tablist"
                aria-label={label}
                className="flex gap-1 overflow-x-auto border-b border-border"
            >
                {items.map((item, index) => {
                    const isSelected = item.id === selected
                    return (
                        <button
                            key={item.id}
                            ref={(el) => {
                                tabRefs.current[index] = el
                            }}
                            type="button"
                            role="tab"
                            id={`${baseId}-tab-${item.id}`}
                            aria-controls={
                                isSelected
                                    ? `${baseId}-panel-${item.id}`
                                    : undefined
                            }
                            aria-selected={isSelected}
                            tabIndex={isSelected ? 0 : -1}
                            onClick={() => {
                                select(index)
                            }}
                            onKeyDown={(event) => {
                                handleKeyDown(event, index)
                            }}
                            className={cx(
                                'focus-ring -mb-px border-b-2 px-3 py-2 text-sm font-medium whitespace-nowrap',
                                isSelected
                                    ? 'border-primary text-fg'
                                    : 'border-transparent text-muted hover:text-fg',
                            )}
                        >
                            {item.label}
                        </button>
                    )
                })}
            </div>
            {items
                .filter((item) => item.id === selected)
                .map((item) => (
                    <div
                        key={item.id}
                        role="tabpanel"
                        id={`${baseId}-panel-${item.id}`}
                        aria-labelledby={`${baseId}-tab-${item.id}`}
                        tabIndex={0}
                        className="focus-ring rounded-sm"
                    >
                        {item.content}
                    </div>
                ))}
        </div>
    )
}
