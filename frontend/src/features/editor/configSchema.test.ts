import { describe, expect, it } from 'vitest'
import {
    describePath,
    emptyValue,
    fieldSpec,
    fits,
    humanize,
    issuesByPath,
    properties,
    type JsonValue,
} from './configSchema'

const kind = (schema: JsonValue) => fieldSpec(schema).kind

describe('fieldSpec', () => {
    it('maps each supported schema type to its control', () => {
        expect(kind({ type: 'string' })).toBe('text')
        expect(kind({ type: 'number' })).toBe('number')
        expect(kind({ type: 'integer' })).toBe('number')
        expect(kind({ type: 'boolean' })).toBe('boolean')
        expect(kind({ type: 'string', enum: ['a', 'b'] })).toBe('select')
        expect(kind({ enum: [1, true, null] })).toBe('select')
        expect(kind({ type: 'string', format: 'credential-reference' })).toBe(
            'credential',
        )
        expect(fieldSpec({ type: 'string', format: 'template' })).toMatchObject(
            { kind: 'text', multiline: true },
        )
        expect(fieldSpec({ type: 'integer' })).toMatchObject({ integer: true })
    })

    it('reads lists, objects and maps recursively', () => {
        const spec = fieldSpec({
            type: 'array',
            maxItems: 3,
            items: {
                type: 'object',
                properties: {
                    name: { type: 'string' },
                    headers: {
                        type: 'object',
                        additionalProperties: {
                            type: 'string',
                            format: 'credential-reference',
                        },
                    },
                },
                required: ['name'],
            },
        })
        expect(spec).toMatchObject({ kind: 'list', maxItems: 3 })
        if (spec.kind !== 'list' || spec.items.kind !== 'object')
            throw new Error('expected a list of objects')
        const [name, headers] = spec.items.properties
        expect(name).toMatchObject({ key: 'name', required: true })
        expect(headers?.spec).toMatchObject({
            kind: 'map',
            values: { kind: 'credential' },
        })
    })

    it('falls back to JSON instead of guessing', () => {
        expect(kind({})).toBe('json') // any JSON value
        expect(kind({ type: 'object' })).toBe('json') // free-form object
        expect(kind({ type: 'array' })).toBe('json') // items undeclared
        expect(kind({ type: ['string', 'number'] })).toBe('json')
        expect(kind({ type: 'date' })).toBe('json')
        expect(kind('not a schema')).toBe('json')
    })

    it('treats a null member of the type list as nullable', () => {
        expect(fieldSpec({ type: ['integer', 'null'] })).toMatchObject({
            kind: 'number',
            nullable: true,
        })
        expect(fieldSpec({ type: 'integer' }).nullable).toBe(false)
    })

    it('keeps annotations: title, description and default', () => {
        expect(
            fieldSpec({
                type: 'string',
                title: 'HTTP method',
                description: 'Verb to send.',
                default: 'GET',
            }),
        ).toMatchObject({
            title: 'HTTP method',
            description: 'Verb to send.',
            defaultValue: 'GET',
        })
        // A falsy default is still a default.
        expect(
            fieldSpec({ type: 'boolean', default: false }).defaultValue,
        ).toBe(false)
        expect(fieldSpec({ type: 'boolean' }).defaultValue).toBeUndefined()
    })
})

describe('properties', () => {
    it('keeps the schema order and marks required keys', () => {
        const props = properties({
            type: 'object',
            properties: { b: { type: 'string' }, a: { type: 'number' } },
            required: ['a'],
        })
        expect(props.map((p) => [p.key, p.required, p.spec.kind])).toEqual([
            ['b', false, 'text'],
            ['a', true, 'number'],
        ])
    })

    it('is empty for a schema without properties', () => {
        expect(properties({})).toEqual([])
        expect(properties({ type: 'object', properties: 'x' })).toEqual([])
    })
})

describe('helpers', () => {
    it('fits values to their controls', () => {
        expect(fits(fieldSpec({ type: 'string' }), 'x')).toBe(true)
        expect(fits(fieldSpec({ type: 'string' }), 3)).toBe(false)
        expect(fits(fieldSpec({ type: 'object', properties: {} }), [])).toBe(
            false,
        )
        expect(fits(fieldSpec({}), { any: ['thing'] })).toBe(true)
    })

    it('starts new items from the default or an empty value', () => {
        expect(emptyValue(fieldSpec({ type: 'string', default: 'x' }))).toBe(
            'x',
        )
        expect(
            emptyValue(fieldSpec({ type: 'object', properties: {} })),
        ).toEqual({})
        expect(emptyValue(fieldSpec({ enum: ['a', 'b'] }))).toBe('a')
        expect(emptyValue(fieldSpec({}))).toBeNull()
    })

    it('labels keys and paths for people', () => {
        expect(humanize('timeout_seconds')).toBe('Timeout seconds')
        expect(describePath(['tools', 0, 'name'])).toBe('tools › item 1 › name')
        expect(describePath([])).toBe('Configuration')
    })

    it('indexes issues by exact path', () => {
        const index = issuesByPath([
            { path: ['url'], message: 'a' },
            { path: ['url'], message: 'b' },
            { path: ['tools', 0], message: 'c' },
        ])
        expect(index.get('["url"]')).toEqual(['a', 'b'])
        expect(index.get('["tools",0]')).toEqual(['c'])
        expect(index.get('["tools"]')).toBeUndefined()
    })
})
