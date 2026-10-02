import { describe, expect, it } from 'vitest'
import { ApiError, type ApiErrorKind } from './errors'
import { createQueryClient, shouldRetryQuery } from './queryClient'

const error = (kind: ApiErrorKind, status?: number) =>
    new ApiError({ kind, status, message: 'm' })

describe('shouldRetryQuery', () => {
    it.each([
        ['network', undefined],
        ['timeout', undefined],
        ['server', 502],
        ['server', 503],
        ['server', 504],
    ] as const)('retries transient %s %s', (kind, status) => {
        expect(shouldRetryQuery(0, error(kind, status))).toBe(true)
        expect(shouldRetryQuery(1, error(kind, status))).toBe(true)
        expect(shouldRetryQuery(2, error(kind, status))).toBe(false)
    })

    it.each([
        ['server', 500],
        ['cancelled', undefined],
        ['malformed_response', 200],
        ['authentication', 401],
        ['authorization', 403],
        ['not_found', 404],
        ['conflict', 409],
        ['validation', 422],
        ['rate_limited', 429],
    ] as const)('does not retry %s %s', (kind, status) => {
        expect(shouldRetryQuery(0, error(kind, status))).toBe(false)
    })

    it('does not retry errors outside the API layer', () => {
        expect(shouldRetryQuery(0, new TypeError('bug'))).toBe(false)
    })

    it('is the query default; mutations never retry', () => {
        const options = createQueryClient().getDefaultOptions()
        expect(options.queries?.retry).toBe(shouldRetryQuery)
        expect(options.mutations?.retry).toBe(false)
    })
})
