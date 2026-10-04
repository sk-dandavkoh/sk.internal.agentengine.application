import { useState, type SubmitEvent } from 'react'
import type { MonitoringWindow } from '../../api/monitoring'
import { Button, Input, Select } from '../../components/ui'
import {
    customWindow,
    isRange,
    PRESETS,
    toLocalInput,
    type Range,
} from './model'

interface RangePickerProps {
    range: Range
    /** The window shown now; prefills the custom inputs. */
    current: MonitoringWindow | null
    onRange: (range: Range) => void
    onCustom: (window: MonitoringWindow) => void
}

export function RangePicker({
    range,
    current,
    onRange,
    onCustom,
}: RangePickerProps) {
    return (
        <div className="flex flex-wrap items-end gap-3">
            <Select
                label="Time range"
                value={range}
                onChange={(e) => {
                    const value = e.target.value
                    if (isRange(value)) onRange(value)
                }}
            >
                {Object.entries(PRESETS).map(([key, { label }]) => (
                    <option key={key} value={key}>
                        {label}
                    </option>
                ))}
                <option value="custom">Custom</option>
            </Select>
            {range === 'custom' && (
                // Keyed by the window: applying a new one resets the inputs.
                <CustomRange
                    key={current ? `${current.since}/${current.until}` : ''}
                    current={current}
                    onApply={onCustom}
                />
            )}
        </div>
    )
}

function CustomRange({
    current,
    onApply,
}: {
    current: MonitoringWindow | null
    onApply: (window: MonitoringWindow) => void
}) {
    const [since, setSince] = useState(
        current ? toLocalInput(current.since) : '',
    )
    const [until, setUntil] = useState(
        current ? toLocalInput(current.until) : '',
    )
    const [error, setError] = useState<string>()

    const submit = (e: SubmitEvent<HTMLFormElement>) => {
        e.preventDefault()
        const result = customWindow(since, until)
        if ('error' in result) setError(result.error)
        else onApply(result.window)
    }

    return (
        <form
            className="flex flex-wrap items-end gap-3"
            onSubmit={submit}
            noValidate
            aria-label="Custom time range"
        >
            <Input
                label="From"
                type="datetime-local"
                value={since}
                onChange={(e) => {
                    setSince(e.target.value)
                    setError(undefined)
                }}
                required
            />
            <Input
                label="To"
                type="datetime-local"
                value={until}
                onChange={(e) => {
                    setUntil(e.target.value)
                    setError(undefined)
                }}
                error={error}
                required
            />
            <Button type="submit">Apply</Button>
        </form>
    )
}
