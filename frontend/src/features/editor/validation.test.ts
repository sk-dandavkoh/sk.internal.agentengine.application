import { describe, expect, it } from 'vitest'
import { ApiError } from '../../api/errors'
import { issueScope, publishIssues } from './validation'

const issue = {
    code: 'empty_graph',
    message: 'graph has no nodes',
    node_id: null,
    port: null,
    edge: null,
    trigger_id: null,
} as const

const refused = (details: Record<string, unknown>) =>
    new ApiError({
        kind: 'validation',
        status: 422,
        code: 'WORKFLOW_VALIDATION_FAILED',
        message: 'The workflow draft contains validation errors.',
        details,
    })

describe('publishIssues', () => {
    it('reads the issues of a refused publish', () => {
        expect(publishIssues(refused({ issues: [issue] }))).toEqual([issue])
    })

    it('is null for other failures and for details off the contract', () => {
        expect(
            publishIssues(refused({ issues: [{ code: 'nope' }] })),
        ).toBeNull()
        expect(publishIssues(refused({}))).toBeNull()
        expect(
            publishIssues(
                new ApiError({
                    kind: 'validation',
                    code: 'VALIDATION_ERROR',
                    message: 'x',
                }),
            ),
        ).toBeNull()
        expect(publishIssues(new Error('x'))).toBeNull()
    })
})

describe('issueScope', () => {
    it('prefers the most specific location', () => {
        const edge = {
            source_node_id: 'a',
            source_port: 'o',
            target_node_id: 'b',
            target_port: 'i',
        }
        expect(issueScope({ ...issue, node_id: 'b', edge })).toBe('node')
        expect(issueScope({ ...issue, edge })).toBe('connection')
        expect(issueScope({ ...issue, trigger_id: 't' })).toBe('trigger')
        expect(issueScope(issue)).toBe('workflow')
    })
})
