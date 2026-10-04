import { z } from 'zod'
import type { ApiClient } from './client'
import { executionStatusSchema } from './executions'
import { executionMetricsSchema } from './reports'
import { timestamp } from './schemas'

// Contract of backend application/monitoring.py (GET /monitoring/summary):
// the executions *created* in [since, until), aggregated from persisted
// state. Every number here is the backend's; the UI only formats it.

/** Nearest-rank percentiles: each is a duration that was observed. */
export const durationStatsSchema = z.object({
    count: z.number().int(),
    mean_ms: z.number().int(),
    p50_ms: z.number().int(),
    p95_ms: z.number().int(),
    max_ms: z.number().int(),
})
export type DurationStats = z.infer<typeof durationStatsSchema>

export const monitoringSummarySchema = z.object({
    since: timestamp,
    until: timestamp,
    bucket_seconds: z.number().int(),
    executions: z.number().int(),
    status_counts: z.record(executionStatusSchema, z.number().int()),
    /** Terminal statuses with at least one finished run. */
    durations: z.partialRecord(executionStatusSchema, durationStatsSchema),
    /** `[start, end)`; the last bucket may be shorter than `bucket_seconds`. */
    timeline: z.array(
        z.object({
            start: timestamp,
            end: timestamp,
            counts: z.record(executionStatusSchema, z.number().int()),
        }),
    ),
    /** Most frequent first, capped; `errors_total` counts all. */
    errors: z.array(
        z.object({
            code: z.string(),
            kind: z.string(),
            count: z.number().int(),
        }),
    ),
    errors_total: z.number().int(),
    nodes: z.array(
        z.object({
            node_type: z.string(),
            metrics: executionMetricsSchema,
            /** Of the last attempt; null when none finished. */
            durations: durationStatsSchema.nullable(),
        }),
    ),
    totals: executionMetricsSchema,
})
export type MonitoringSummary = z.infer<typeof monitoringSummarySchema>

export interface MonitoringWindow {
    /** ISO timestamps with timezone; at most 31 days apart. */
    since: string
    until: string
}

export function getMonitoringSummary(
    client: ApiClient,
    window: MonitoringWindow,
    signal?: AbortSignal,
) {
    return client.request(
        'GET',
        '/monitoring/summary',
        monitoringSummarySchema,
        {
            query: { ...window },
            signal,
        },
    )
}
