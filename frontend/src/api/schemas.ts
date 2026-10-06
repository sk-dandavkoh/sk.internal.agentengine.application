import { z } from 'zod'

// Building blocks shared by the service modules. Each schema mirrors a
// backend wire format; contract.node.test.ts checks them against OpenAPI.

export const timestamp = z.iso.datetime({ offset: true })

/** An arbitrary JSON object (node config, outputs, event payloads). */
export const jsonObject = z.record(z.string(), z.json())
export type JsonObject = z.infer<typeof jsonObject>

// backend api/workflow_schemas.py PageInfo
export const pageInfoSchema = z.object({
    offset: z.number().int(),
    limit: z.number().int(),
    has_more: z.boolean(),
})

export function pageSchema<T extends z.ZodType>(item: T) {
    return z.object({ items: z.array(item), page: pageInfoSchema })
}

/** Offset pagination; the backend defaults to offset 0, limit 50 (max 100). */
export interface PageParams {
    offset?: number
    limit?: number
}
