import type { JsonObject } from '../../api/schemas'

// A node's config schema read as form fields (roadmap §3.2). Only the
// JSON-Schema subset the backend validates is interpreted (backend
// domain/node.py NodeConfigSchema); anything else becomes a JSON editor
// instead of a guess. Which values are valid is still the backend's call.

export type JsonValue = JsonObject[string]
/** Keys and list indexes from the config root, as the backend reports them. */
export type ConfigPath = readonly (string | number)[]

/** `format` of a string that holds `credential:<name>`, never a secret. */
export const CREDENTIAL_REFERENCE_FORMAT = 'credential-reference'
/** `format` of a prompt template string. */
export const TEMPLATE_FORMAT = 'template'
/** `format` of a string naming a configured LLM provider. */
export const LLM_PROVIDER_FORMAT = 'llm-provider'
/** `format` of a string naming a registered node type (agent tools). */
export const NODE_TYPE_FORMAT = 'node-type'
/** `format` of an object holding the config of the node type named by the
 * `node-type` field next to it. */
export const NODE_CONFIG_FORMAT = 'node-config'

interface Annotations {
    title: string | undefined
    description: string | undefined
    /** What the node does when the key is absent; never written for it. */
    defaultValue: JsonValue | undefined
    /** `type: [..., "null"]`: null is a value of its own. */
    nullable: boolean
}

export type FieldSpec = Annotations &
    (
        | { kind: 'text'; multiline: boolean }
        | { kind: 'credential' }
        | { kind: 'nodeType' }
        | { kind: 'provider' }
        | { kind: 'nodeConfig' }
        | { kind: 'number'; integer: boolean }
        | { kind: 'boolean' }
        | { kind: 'select'; options: JsonValue[] }
        | { kind: 'object'; properties: Property[] }
        | { kind: 'map'; values: FieldSpec }
        | { kind: 'list'; items: FieldSpec; maxItems: number | undefined }
        | { kind: 'json' }
    )

export interface Property {
    key: string
    required: boolean
    spec: FieldSpec
}

export const isObject = (v: JsonValue | undefined): v is JsonObject =>
    typeof v === 'object' && v !== null && !Array.isArray(v)

const text = (v: JsonValue | undefined) =>
    typeof v === 'string' && v.trim() !== '' ? v : undefined

/** The fields of an object schema, in the schema's order. */
export function properties(schema: JsonValue | undefined): Property[] {
    if (!isObject(schema) || !isObject(schema['properties'])) return []
    const required = Array.isArray(schema['required']) ? schema['required'] : []
    return Object.entries(schema['properties']).map(([key, prop]) => ({
        key,
        required: required.includes(key),
        spec: fieldSpec(prop),
    }))
}

export function fieldSpec(schema: JsonValue | undefined): FieldSpec {
    const s = isObject(schema) ? schema : {}
    const declared =
        typeof s['type'] === 'string'
            ? [s['type']]
            : Array.isArray(s['type'])
              ? s['type'].filter((t) => typeof t === 'string')
              : []
    const types = declared.filter((t) => t !== 'null')
    const base: Annotations = {
        title: text(s['title']),
        description: text(s['description']),
        defaultValue: 'default' in s ? s['default'] : undefined,
        nullable: types.length < declared.length,
    }
    if (Array.isArray(s['enum']) && s['enum'].length > 0)
        return { ...base, kind: 'select', options: s['enum'] }
    // No type, or a union of several: only raw JSON fits every value.
    if (types.length !== 1) return { ...base, kind: 'json' }
    switch (types[0]) {
        case 'string':
            if (s['format'] === CREDENTIAL_REFERENCE_FORMAT)
                return { ...base, kind: 'credential' }
            if (s['format'] === NODE_TYPE_FORMAT)
                return { ...base, kind: 'nodeType' }
            if (s['format'] === LLM_PROVIDER_FORMAT)
                return { ...base, kind: 'provider' }
            return {
                ...base,
                kind: 'text',
                multiline: s['format'] === TEMPLATE_FORMAT,
            }
        case 'number':
        case 'integer':
            return { ...base, kind: 'number', integer: types[0] === 'integer' }
        case 'boolean':
            return { ...base, kind: 'boolean' }
        case 'object':
            if (s['format'] === NODE_CONFIG_FORMAT)
                return { ...base, kind: 'nodeConfig' }
            if (isObject(s['properties']))
                return { ...base, kind: 'object', properties: properties(s) }
            if (isObject(s['additionalProperties']))
                return {
                    ...base,
                    kind: 'map',
                    values: fieldSpec(s['additionalProperties']),
                }
            return { ...base, kind: 'json' }
        case 'array':
            if (isObject(s['items']))
                return {
                    ...base,
                    kind: 'list',
                    items: fieldSpec(s['items']),
                    maxItems:
                        typeof s['maxItems'] === 'number'
                            ? s['maxItems']
                            : undefined,
                }
            return { ...base, kind: 'json' }
        default:
            return { ...base, kind: 'json' }
    }
}

/** Whether `value` can be shown by the field's own control. A value that
 * does not (an older config, a changed schema) is shown as JSON instead,
 * so it stays visible and fixable rather than silently dropped. */
export function fits(spec: FieldSpec, value: JsonValue): boolean {
    switch (spec.kind) {
        case 'text':
        case 'credential':
        case 'nodeType':
        case 'provider':
            return typeof value === 'string'
        case 'number':
            return typeof value === 'number'
        case 'boolean':
            return typeof value === 'boolean'
        case 'object':
        case 'map':
        case 'nodeConfig':
            return isObject(value)
        case 'list':
            return Array.isArray(value)
        case 'select':
        case 'json':
            return true
    }
}

/** The value a new list item or map entry starts with. */
export function emptyValue(spec: FieldSpec): JsonValue {
    if (spec.defaultValue !== undefined) return spec.defaultValue
    switch (spec.kind) {
        case 'object':
        case 'map':
        case 'nodeConfig':
            return {}
        case 'list':
            return []
        case 'boolean':
            return false
        case 'number':
            return 0
        case 'select':
            return spec.options[0] ?? null
        case 'json':
            return null
        default:
            return ''
    }
}

/** `timeout_seconds` → `Timeout seconds`. */
export function humanize(key: string): string {
    const words = key.replace(/[_-]+/g, ' ').trim()
    return words.charAt(0).toUpperCase() + words.slice(1)
}

export const display = (value: JsonValue) =>
    typeof value === 'string' ? value : JSON.stringify(value)

export const pathKey = (path: ConfigPath) => JSON.stringify(path)

/** `tools › item 1 › name`, for messages not tied to a visible field. */
export const describePath = (path: ConfigPath) =>
    path.length === 0
        ? 'Configuration'
        : path
              .map((p) => (typeof p === 'number' ? `item ${String(p + 1)}` : p))
              .join(' › ')

/** Messages per field path. */
export function issuesByPath(
    issues: readonly { path: ConfigPath; message: string }[],
): ReadonlyMap<string, string[]> {
    const index = new Map<string, string[]>()
    for (const { path, message } of issues) {
        const key = pathKey(path)
        index.set(key, [...(index.get(key) ?? []), message])
    }
    return index
}
