import type { Execution, ExecutionStatus } from '../../api/executions'
import { jsonObject, type JsonObject } from '../../api/schemas'
import { FAILURE_STATUSES, formatDuration } from '../dashboard/format'

export const executionPath = (executionId: string) =>
    `/executions/${encodeURIComponent(executionId)}`

export function duration(e: Execution): string {
    if (e.started_at && e.finished_at)
        return formatDuration(e.started_at, e.finished_at)
    // Cancelled or failed before a worker picked it up.
    if (e.finished_at) return 'Never started'
    return e.started_at ? 'In progress' : 'Not started'
}

export const isFailure = (status: ExecutionStatus) =>
    (FAILURE_STATUSES as readonly ExecutionStatus[]).includes(status)

/** The run input must be a JSON object; the backend validates the rest. */
export function parseInput(text: string): JsonObject | string {
    let value: unknown
    try {
        value = JSON.parse(text.trim() === '' ? '{}' : text)
    } catch {
        return 'Not valid JSON.'
    }
    const parsed = jsonObject.safeParse(value)
    return parsed.success
        ? parsed.data
        : 'The input must be a JSON object, e.g. {"topic": "x"}.'
}
