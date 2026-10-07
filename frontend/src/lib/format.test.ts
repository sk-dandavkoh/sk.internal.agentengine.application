import { describe, expect, it } from 'vitest'
import { formatBytes, formatSeconds } from './format'

describe('formatSeconds', () => {
    it.each([
        [3600, '1 h'],
        [900, '15 min'],
        [90, '1 min 30 s'],
        [45, '45 s'],
        [0, '0 s'],
        [5400, '1 h 30 min'],
    ])('%d s → %s', (seconds, text) => {
        expect(formatSeconds(seconds)).toBe(text)
    })
})

describe('formatBytes', () => {
    it.each([
        [512, '512 bytes'],
        [1536, '1.5 KB'],
        [1024 * 1024, '1 MB'],
        [16 * 1024 * 1024, '16 MB'],
    ])('%d → %s', (bytes, text) => {
        expect(formatBytes(bytes)).toBe(text)
    })
})
