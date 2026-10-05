import { describe, expect, it } from 'vitest'
import {
    customWindow,
    formatBucket,
    formatCost,
    formatCount,
    formatPercent,
    isRange,
    outcomeCounts,
    presetWindow,
    toLocalInput,
} from './model'

const NOW = Date.parse('2026-10-04T12:34:56.789Z')

describe('time windows', () => {
    it('anchors presets at the end of the current minute', () => {
        expect(presetWindow('1h', NOW)).toEqual({
            since: '2026-10-04T11:35:00.000Z',
            until: '2026-10-04T12:35:00.000Z',
        })
        expect(presetWindow('7d', NOW).since).toBe('2026-09-27T12:35:00.000Z')
        // A run created just now is inside, also on a minute boundary
        // (regression: the window used to end at the start of the minute).
        for (const now of [NOW, Date.parse('2026-10-04T12:34:00.000Z')])
            expect(Date.parse(presetWindow('1h', now).until)).toBeGreaterThan(
                now,
            )
        // Same minute, same window: the query key does not churn.
        expect(presetWindow('24h', NOW + 3_000)).toEqual(
            presetWindow('24h', NOW),
        )
    })

    it('accepts a valid custom window and rejects invalid ones', () => {
        expect(
            customWindow('2026-10-01T00:00:00Z', '2026-10-02T00:00:00Z'),
        ).toEqual({
            window: {
                since: '2026-10-01T00:00:00.000Z',
                until: '2026-10-02T00:00:00.000Z',
            },
        })
        expect(customWindow('', '2026-10-02T00:00')).toEqual({
            error: 'Enter a start and an end.',
        })
        expect(customWindow('2026-10-02T00:00', '2026-10-01T00:00')).toEqual({
            error: 'The end must be after the start.',
        })
        expect(customWindow('2026-10-02T00:00', '2026-10-02T00:00')).toEqual({
            error: 'The end must be after the start.',
        })
        expect(customWindow('2026-09-01T00:00', '2026-10-02T00:01')).toEqual({
            error: 'The window may span at most 31 days.',
        })
    })

    it('round-trips through the datetime-local value', () => {
        const iso = '2026-10-04T12:34:00.000Z'
        const local = toLocalInput(iso)
        expect(local).toMatch(/^\d{4}-\d\d-\d\dT\d\d:\d\d$/)
        expect(new Date(local).toISOString()).toBe(iso)
        expect(toLocalInput('garbage')).toBe('')
    })

    it('recognizes only known ranges', () => {
        expect(['1h', '24h', '7d', 'custom'].every(isRange)).toBe(true)
        expect(isRange('30d')).toBe(false)
        expect(isRange('toString')).toBe(false)
        expect(isRange(null)).toBe(false)
    })
})

describe('outcomes', () => {
    it('groups every status exactly once', () => {
        const counts = {
            created: 1,
            queued: 2,
            running: 3,
            paused: 4,
            waiting: 5,
            completed: 6,
            failed: 7,
            cancelled: 8,
            timeout: 9,
        }
        const o = outcomeCounts(counts)
        expect(o).toEqual({
            completed: 6,
            failed: 16,
            cancelled: 8,
            unfinished: 15,
        })
        expect(Object.values(o).reduce((a, n) => a + n)).toBe(45)
    })
})

describe('formatting', () => {
    it('formats percentages without inventing a denominator', () => {
        expect(formatPercent(0, 0)).toBe('—')
        expect(formatPercent(0, 5)).toBe('0 %')
        expect(formatPercent(5, 5)).toBe('100 %')
        expect(formatPercent(46, 50)).toBe('92.0 %')
        expect(formatPercent(1, 3)).toBe('33.3 %')
    })

    it('keeps small costs visible and tolerates unknown currencies', () => {
        expect(formatCost({ amount: 0.000123, currency: 'USD' })).toContain(
            '0.000123',
        )
        expect(formatCost({ amount: 12.5, currency: 'USD' })).toContain('12.50')
        expect(formatCost({ amount: 3, currency: 'credits' })).toBe('3 credits')
    })

    it('names bucket sizes in readable units', () => {
        expect(formatBucket(60)).toBe('1 min')
        expect(formatBucket(900)).toBe('15 min')
        expect(formatBucket(21_600)).toBe('6 h')
        expect(formatBucket(86_400)).toBe('1 day')
        expect(formatBucket(172_800)).toBe('2 days')
        expect(formatBucket(45)).toBe('45 s')
    })

    it('formats large counts with grouping', () => {
        expect(formatCount(1_234_567)).toMatch(/1.234.567/)
    })
})
