import { z } from 'zod'

// What the route guard hands the login page in history state (never the
// URL: no ?next= parameter a crafted link could point elsewhere).
const loginStateSchema = z.object({
    from: z
        .object({ pathname: z.string(), search: z.string(), hash: z.string() })
        .optional(),
    expired: z.boolean().optional(),
})
export type LoginState = z.infer<typeof loginStateSchema>

/** History state is whatever the last page left there: validate it. */
export function readLoginState(state: unknown): {
    returnTo: string
    expired: boolean
} {
    const parsed = loginStateSchema.safeParse(state)
    const from = parsed.success ? parsed.data.from : undefined
    const inApp =
        from !== undefined &&
        from.pathname.startsWith('/') &&
        !from.pathname.startsWith('//') &&
        from.pathname !== '/login'
    return {
        returnTo: inApp ? `${from.pathname}${from.search}${from.hash}` : '/',
        expired: parsed.success && parsed.data.expired === true,
    }
}
