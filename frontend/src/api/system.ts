import { z } from 'zod'
import type { ApiClient } from './client'

// Contract of backend api/routes.py SystemStatus: /health and /ready as
// seen through /api/v1, the only prefix the UI reaches.

export const systemStatusSchema = z.object({
    version: z.string(),
    ready: z.boolean(),
    /** Failing readiness check → its reason. Empty when ready. */
    failures: z.record(z.string(), z.string()),
})
export type SystemStatus = z.infer<typeof systemStatusSchema>

export function getSystemStatus(client: ApiClient, signal?: AbortSignal) {
    return client.request('GET', '/system/status', systemStatusSchema, {
        signal,
    })
}
