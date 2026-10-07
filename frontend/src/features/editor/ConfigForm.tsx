import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useId, useState, type ReactNode } from 'react'
import { useApiClient } from '../../api/context'
import type { NodeDefinition } from '../../api/nodes'
import { nodeQueries, systemQueries } from '../../api/queries'
import { Button, Checkbox, Input, Select, Textarea } from '../../components/ui'
import {
    describePath,
    display,
    emptyValue,
    fits,
    humanize,
    isObject,
    issuesByPath,
    pathKey,
    properties,
    type ConfigPath,
    type FieldSpec,
    type JsonValue,
    type Property,
} from './configSchema'
import { useDebounced, VALIDATION_DELAY_MS } from './useDebounced'
import { CredentialInput } from '../credentials/CredentialInput'

// Renders any config schema the backend sends: one control per field kind,
// recursing into objects, lists and maps. No node has a form of its own
// (roadmap §3.2). Every control reports the whole new value of its field,
// `undefined` meaning "not set" (the node then uses its default).

type Change = (next: JsonValue | undefined) => void

interface FieldProps {
    spec: FieldSpec
    label: string
    path: ConfigPath
    value: JsonValue | undefined
    required: boolean
    /** Whether the field may be absent: object properties may, list items
     * and map values may not (clearing them keeps an empty value). */
    optional: boolean
    onChange: Change
    issues: ReadonlyMap<string, string[]>
    /** For a `nodeConfig` field: the node type picked next to it. */
    nodeType?: string | undefined
}

const errorFor = (issues: FieldProps['issues'], path: ConfigPath) =>
    issues.get(pathKey(path))?.join('; ')

function helpText(spec: FieldSpec, extra?: string) {
    const parts = [spec.description, extra]
    if (spec.defaultValue !== undefined)
        parts.push(`Default: ${display(spec.defaultValue)}.`)
    return parts.filter(Boolean).join(' ') || undefined
}

/**
 * The text a control shows for `value`: what the user typed while it is
 * what produced `value` (or does not parse yet), the formatted value once
 * `value` changes from outside (undo, another node's data, a schema change).
 */
function useDraftText(
    value: JsonValue | undefined,
    format: (value: JsonValue) => string,
) {
    const [draft, setDraft] = useState(() => ({
        text: value === undefined ? '' : format(value),
        source: value,
    }))
    let current = draft
    if (draft.source !== value) {
        current = {
            text: value === undefined ? '' : format(value),
            source: value,
        }
        setDraft(current)
    }
    function setText(
        text: string,
        committed?: { value: JsonValue | undefined },
    ) {
        setDraft({
            text,
            source: committed ? committed.value : current.source,
        })
    }
    return [current.text, setText] as const
}

const INVALID = Symbol('invalid')

/** A number, `undefined` for blank text, or INVALID. */
function parseNumber(text: string): number | undefined | typeof INVALID {
    const trimmed = text.trim()
    if (trimmed === '') return undefined
    const parsed = Number(trimmed)
    return Number.isFinite(parsed) ? parsed : INVALID
}

/** A JSON value, `undefined` for blank text, or INVALID. */
function parseJson(text: string): JsonValue | undefined | typeof INVALID {
    if (text.trim() === '') return undefined
    try {
        return JSON.parse(text) as JsonValue
    } catch {
        return INVALID
    }
}

/** One field: its control, plus null and default handling around it. */
export function ConfigField(props: FieldProps) {
    const { spec, value, optional, onChange } = props
    const nullId = useId()
    const isNull = spec.nullable && value === null
    const resettable =
        optional && value !== undefined && spec.defaultValue !== undefined
    return (
        <div className="flex flex-col gap-1.5">
            {isNull ? (
                <p className="text-sm font-medium text-fg">
                    {props.label}: <span className="text-muted">null</span>
                </p>
            ) : value !== undefined && !fits(spec, value) ? (
                <JsonField
                    {...props}
                    hint="This value does not match the field's type; edit it as JSON."
                />
            ) : (
                <Control {...props} />
            )}
            {(spec.nullable || resettable) && (
                <div className="flex flex-wrap items-center gap-3">
                    {spec.nullable && (
                        <Checkbox
                            id={nullId}
                            label={`${props.label}: no value (null)`}
                            checked={isNull}
                            onChange={(e) => {
                                onChange(
                                    e.target.checked
                                        ? null
                                        : optional
                                          ? undefined
                                          : emptyValue(spec),
                                )
                            }}
                        />
                    )}
                    {resettable && (
                        <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => {
                                onChange(undefined)
                            }}
                        >
                            Use default
                        </Button>
                    )}
                </div>
            )}
        </div>
    )
}

function Control(props: FieldProps) {
    switch (props.spec.kind) {
        case 'text':
            return <TextField {...props} multiline={props.spec.multiline} />
        case 'credential':
            return <CredentialField {...props} />
        case 'nodeType':
            return <NodeTypeField {...props} />
        case 'provider':
            return <ProviderField {...props} />
        case 'nodeConfig':
            return <NodeConfigField {...props} />
        case 'number':
            return <NumberField {...props} integer={props.spec.integer} />
        case 'boolean':
            return <BooleanField {...props} />
        case 'select':
            return <SelectField {...props} options={props.spec.options} />
        case 'object':
            return (
                <Group {...props}>
                    <PropertyFields
                        properties={props.spec.properties}
                        value={isObject(props.value) ? props.value : {}}
                        path={props.path}
                        issues={props.issues}
                        onChange={(next) => {
                            props.onChange(next)
                        }}
                    />
                </Group>
            )
        case 'map':
            return <MapField {...props} values={props.spec.values} />
        case 'list':
            return (
                <ListField
                    {...props}
                    items={props.spec.items}
                    maxItems={props.spec.maxItems}
                />
            )
        case 'json':
            return <JsonField {...props} />
    }
}

function TextField({
    spec,
    label,
    path,
    value,
    required,
    optional,
    onChange,
    issues,
    multiline,
}: FieldProps & { multiline: boolean }) {
    const shared = {
        label,
        required,
        description: helpText(spec),
        error: errorFor(issues, path),
        value: typeof value === 'string' ? value : '',
        placeholder:
            spec.defaultValue === undefined
                ? undefined
                : display(spec.defaultValue),
    }
    const change = (text: string) => {
        onChange(text === '' && optional ? undefined : text)
    }
    return multiline ? (
        <Textarea
            {...shared}
            className="font-mono"
            onChange={(e) => {
                change(e.target.value)
            }}
        />
    ) : (
        <Input
            {...shared}
            onChange={(e) => {
                change(e.target.value)
            }}
        />
    )
}

function CredentialField({
    spec,
    label,
    path,
    value,
    required,
    optional,
    onChange,
    issues,
}: FieldProps) {
    return (
        <CredentialInput
            label={label}
            required={required}
            description={helpText(spec)}
            error={errorFor(issues, path)}
            value={value}
            onChange={(reference) => {
                onChange(reference ?? (optional ? undefined : ''))
            }}
        />
    )
}

/** Node types offered as a tool. Mirrors the backend's rule for the UX only
 * (it rejects the rest when the agent starts): control-flow nodes answer
 * nothing, and a node that hosts tools itself would nest agents. */
const offeredAsTool = (d: NodeDefinition) =>
    d.capabilities['is_control_flow'] !== true &&
    !properties(d.config_schema).some((p) =>
        p.spec.kind === 'list' && p.spec.items.kind === 'object'
            ? p.spec.items.properties.some((q) => q.spec.kind === 'nodeType')
            : false,
    )

function useNodeCatalog() {
    return useQuery(nodeQueries.catalog(useApiClient()))
}

/** The LLM providers this installation configured; a typed name only
 * while the list is unavailable. */
function ProviderField(props: FieldProps) {
    const { spec, label, path, value, required, optional, onChange, issues } =
        props
    const providers = useQuery({
        ...systemQueries.llmProviders(useApiClient()),
        retry: false,
    })
    if (!providers.data) return <TextField {...props} multiline={false} />
    const selected = typeof value === 'string' ? value : ''
    const picked = providers.data.find((p) => p.name === selected)
    const note =
        picked === undefined
            ? undefined
            : picked.requires_credential
              ? 'Needs a credential with its API key.'
              : 'Needs no credential.'
    return (
        <Select
            label={label}
            required={required}
            description={[helpText(spec), note].filter(Boolean).join(' ')}
            error={
                errorFor(issues, path) ??
                (selected !== '' && !picked
                    ? `"${selected}" is not configured on this server.`
                    : providers.data.length === 0
                      ? 'This server has no AI provider configured; an operator sets one up.'
                      : undefined)
            }
            value={selected}
            onChange={(e) => {
                onChange(
                    e.target.value === '' && optional
                        ? undefined
                        : e.target.value,
                )
            }}
        >
            <option value="">Choose a provider…</option>
            {selected !== '' && !picked && (
                <option value={selected}>{selected} (not configured)</option>
            )}
            {providers.data.map((p) => (
                <option key={p.name} value={p.name}>
                    {p.name}
                </option>
            ))}
        </Select>
    )
}

function NodeTypeField(props: FieldProps) {
    const { spec, label, path, value, required, optional, onChange, issues } =
        props
    const catalog = useNodeCatalog()
    if (!catalog.data) return <TextField {...props} multiline={false} />
    const offered = catalog.data.items.filter(offeredAsTool)
    const selected = typeof value === 'string' ? value : ''
    const picked = offered.find((d) => d.node_type === selected)
    const categories = [...new Set(offered.map((d) => d.category))]
    return (
        <Select
            label={label}
            required={required}
            description={picked?.description ?? helpText(spec)}
            error={
                errorFor(issues, path) ??
                (selected !== '' && !picked
                    ? `"${selected}" is not an available tool node.`
                    : undefined)
            }
            value={selected}
            onChange={(e) => {
                onChange(
                    e.target.value === '' && optional
                        ? undefined
                        : e.target.value,
                )
            }}
        >
            <option value="">Choose a node…</option>
            {selected !== '' && !picked && (
                <option value={selected}>{selected}</option>
            )}
            {categories.map((category) => (
                <optgroup key={category} label={category}>
                    {offered
                        .filter((d) => d.category === category)
                        .map((d) => (
                            <option key={d.node_type} value={d.node_type}>
                                {d.name} ({d.node_type})
                            </option>
                        ))}
                </optgroup>
            ))}
        </Select>
    )
}

/** The picked node's own settings form, checked by the backend like a
 * node on the canvas: the agent's schema alone says nothing about them. */
function NodeConfigField(props: FieldProps) {
    const { path, value, nodeType, onChange } = props
    const client = useApiClient()
    const catalog = useNodeCatalog()
    const definition = catalog.data?.items.find((d) => d.node_type === nodeType)
    const config = isObject(value) ? value : {}
    const settled = useDebounced(config, VALIDATION_DELAY_MS)
    const check = useQuery({
        ...nodeQueries.configValidation(client, nodeType ?? '', settled),
        enabled: definition !== undefined,
        placeholderData: keepPreviousData,
    })
    if (!definition)
        return nodeType && catalog.data ? (
            <JsonField
                {...props}
                hint={`"${nodeType}" is not in the node catalog; edit its settings as JSON.`}
            />
        ) : (
            <Group {...props}>
                <p className="text-sm text-muted">
                    Choose a node to see its settings.
                </p>
            </Group>
        )
    const fields = properties(definition.config_schema)
    // Paths relative to the tool config, shown under the tool's fields.
    const nested = (check.data?.issues ?? []).map((i) => ({
        ...i,
        path: [...path, ...i.path],
    }))
    return (
        <Group {...props} label={`${props.label}: ${definition.name}`}>
            {fields.length === 0 ? (
                <p className="text-sm text-muted">This node has no settings.</p>
            ) : (
                <PropertyFields
                    properties={fields}
                    value={config}
                    path={path}
                    issues={new Map([...props.issues, ...issuesByPath(nested)])}
                    onChange={(next) => {
                        onChange(next)
                    }}
                />
            )}
            {nested.length > 0 && (
                <ul
                    aria-label={`${props.label} problems`}
                    className="list-inside list-disc text-sm text-danger-text"
                >
                    {nested.map((issue, i) => (
                        <li key={i}>
                            {describePath(issue.path)}: {issue.message}
                        </li>
                    ))}
                </ul>
            )}
        </Group>
    )
}

function NumberField({
    spec,
    label,
    path,
    value,
    required,
    optional,
    onChange,
    issues,
    integer,
}: FieldProps & { integer: boolean }) {
    const [text, setText] = useDraftText(value, display)
    // Derived, not stored: after an undo the text is the value's again.
    const invalid = parseNumber(text) === INVALID
    return (
        <Input
            label={label}
            required={required}
            type="text"
            inputMode={integer ? 'numeric' : 'decimal'}
            description={helpText(spec)}
            error={invalid ? 'Enter a number.' : errorFor(issues, path)}
            placeholder={
                spec.defaultValue === undefined
                    ? undefined
                    : display(spec.defaultValue)
            }
            value={text}
            onChange={(e) => {
                const raw = e.target.value
                const parsed = parseNumber(raw)
                if (parsed === INVALID) {
                    // Not committed: the config keeps its last valid number.
                    setText(raw)
                    return
                }
                const next = parsed ?? (optional ? undefined : 0)
                setText(raw, { value: next })
                onChange(next)
            }}
        />
    )
}

function BooleanField({
    spec,
    label,
    path,
    value,
    onChange,
    issues,
}: FieldProps) {
    const error = errorFor(issues, path)
    const id = useId()
    return (
        <div className="flex flex-col gap-1">
            <Checkbox
                id={id}
                label={label}
                description={helpText(spec)}
                aria-invalid={error ? true : undefined}
                aria-errormessage={error ? `${id}-error` : undefined}
                checked={
                    typeof value === 'boolean'
                        ? value
                        : spec.defaultValue === true
                }
                onChange={(e) => {
                    onChange(e.target.checked)
                }}
            />
            {error && (
                <p
                    id={`${id}-error`}
                    className="text-sm font-medium text-danger-text"
                >
                    {error}
                </p>
            )}
        </div>
    )
}

function SelectField({
    spec,
    label,
    path,
    value,
    required,
    optional,
    onChange,
    issues,
    options,
}: FieldProps & { options: JsonValue[] }) {
    // Options are matched by their JSON text: enums may hold any JSON value.
    const texts = options.map((o) => JSON.stringify(o))
    const selected = value === undefined ? '' : JSON.stringify(value)
    const unset =
        spec.defaultValue !== undefined
            ? `Default (${display(spec.defaultValue)})`
            : optional
              ? 'Not set'
              : 'Choose…'
    return (
        <Select
            label={label}
            required={required}
            description={helpText(spec)}
            error={
                errorFor(issues, path) ??
                (selected !== '' && !texts.includes(selected)
                    ? `The stored value ${display(value ?? null)} is not an option.`
                    : undefined)
            }
            value={selected}
            onChange={(e) => {
                const i = texts.indexOf(e.target.value)
                onChange(i < 0 ? undefined : options[i])
            }}
        >
            <option value="">{unset}</option>
            {selected !== '' && !texts.includes(selected) && (
                <option value={selected}>{selected}</option>
            )}
            {options.map((o, i) => (
                <option key={texts[i]} value={texts[i]}>
                    {display(o)}
                </option>
            ))}
        </Select>
    )
}

function JsonField({
    spec,
    label,
    path,
    value,
    required,
    optional,
    onChange,
    issues,
    hint,
}: FieldProps & { hint?: string }) {
    const [text, setText] = useDraftText(value, (v) =>
        JSON.stringify(v, null, 2),
    )
    const invalid = parseJson(text) === INVALID
    return (
        <Textarea
            label={label}
            required={required}
            description={helpText(spec, hint ?? 'Any JSON value.')}
            error={invalid ? 'Not valid JSON.' : errorFor(issues, path)}
            className="font-mono"
            spellCheck={false}
            value={text}
            onChange={(e) => {
                const raw = e.target.value
                const parsed = parseJson(raw)
                if (parsed === INVALID) {
                    // Half-typed JSON: kept as text, not committed.
                    setText(raw)
                    return
                }
                const next =
                    parsed === undefined
                        ? optional
                            ? undefined
                            : null
                        : parsed
                setText(raw, { value: next })
                onChange(next)
            }}
        />
    )
}

/** Fieldset around a nested object, list or map. */
function Group({
    spec,
    label,
    path,
    required,
    issues,
    children,
}: FieldProps & { children: ReactNode }) {
    const error = errorFor(issues, path)
    const description = helpText(spec)
    return (
        <fieldset className="flex flex-col gap-3 rounded-md border border-border p-3">
            <legend className="px-1 text-sm font-medium text-fg">
                {label}
                {required && (
                    <span aria-hidden="true" className="text-danger-text">
                        {' '}
                        *
                    </span>
                )}
            </legend>
            {description && <p className="text-sm text-muted">{description}</p>}
            {error && (
                <p className="text-sm font-medium text-danger-text">{error}</p>
            )}
            {children}
        </fieldset>
    )
}

function ListField(
    props: FieldProps & { items: FieldSpec; maxItems: number | undefined },
) {
    const { items, maxItems, path, issues, onChange } = props
    const list = Array.isArray(props.value) ? props.value : []
    const full = maxItems !== undefined && list.length >= maxItems
    return (
        <Group {...props}>
            {list.length === 0 && (
                <p className="text-sm text-muted">No items.</p>
            )}
            {list.map((item, i) => {
                const itemLabel = `${props.label} item ${String(i + 1)}`
                return (
                    // Index keys: items have no identity of their own, and
                    // removal re-renders the rest from their values.
                    <div key={i} className="flex flex-col gap-1">
                        <ConfigField
                            spec={items}
                            label={itemLabel}
                            path={[...path, i]}
                            value={item}
                            required={false}
                            optional={false}
                            issues={issues}
                            onChange={(next) => {
                                onChange(
                                    list.map((old, j) =>
                                        j === i
                                            ? (next ?? emptyValue(items))
                                            : old,
                                    ),
                                )
                            }}
                        />
                        <Button
                            size="sm"
                            variant="ghost"
                            className="self-start"
                            onClick={() => {
                                onChange(list.filter((_, j) => j !== i))
                            }}
                        >
                            Remove {itemLabel}
                        </Button>
                    </div>
                )
            })}
            <Button
                size="sm"
                className="self-start"
                disabled={full}
                onClick={() => {
                    onChange([...list, emptyValue(items)])
                }}
            >
                Add item
            </Button>
            {full && (
                <p className="text-sm text-muted">
                    At most {String(maxItems)} items.
                </p>
            )}
        </Group>
    )
}

function MapField(props: FieldProps & { values: FieldSpec }) {
    const { values, path, issues, onChange } = props
    const map = isObject(props.value) ? props.value : {}
    const [newKey, setNewKey] = useState('')
    const key = newKey.trim()
    const taken = key in map
    return (
        <Group {...props}>
            {Object.entries(map).map(([k, v]) => (
                <div key={k} className="flex flex-col gap-1">
                    <ConfigField
                        spec={values}
                        label={k}
                        path={[...path, k]}
                        value={v}
                        required={false}
                        optional={false}
                        issues={issues}
                        onChange={(next) => {
                            onChange({
                                ...map,
                                [k]: next ?? emptyValue(values),
                            })
                        }}
                    />
                    <Button
                        size="sm"
                        variant="ghost"
                        className="self-start"
                        onClick={() => {
                            onChange(withoutKey(map, k))
                        }}
                    >
                        Remove {k}
                    </Button>
                </div>
            ))}
            <div className="flex items-end gap-2">
                <Input
                    label={`New ${props.label} entry`}
                    value={newKey}
                    error={taken ? 'This entry exists already.' : undefined}
                    onChange={(e) => {
                        setNewKey(e.target.value)
                    }}
                />
                <Button
                    size="sm"
                    disabled={key === '' || taken}
                    onClick={() => {
                        onChange({ ...map, [key]: emptyValue(values) })
                        setNewKey('')
                    }}
                >
                    Add entry
                </Button>
            </div>
        </Group>
    )
}

function withoutKey(object: Record<string, JsonValue>, key: string) {
    return Object.fromEntries(Object.entries(object).filter(([k]) => k !== key))
}

/**
 * The fields of one object: every schema property, then the keys the
 * schema does not know (a removed setting, a newer node version), so they
 * can be seen and removed rather than lingering invisibly.
 */
export function PropertyFields({
    properties,
    value,
    path,
    issues,
    onChange,
}: {
    properties: Property[]
    value: Record<string, JsonValue>
    path: ConfigPath
    issues: ReadonlyMap<string, string[]>
    /** The object's new value, and the property that changed. */
    onChange: (next: Record<string, JsonValue>, key: string) => void
}) {
    const known = new Set(properties.map((p) => p.key))
    const unknown = Object.keys(value).filter((k) => !known.has(k))
    const typeKey = properties.find((p) => p.spec.kind === 'nodeType')?.key
    const nodeType =
        typeKey !== undefined && typeof value[typeKey] === 'string'
            ? value[typeKey]
            : undefined
    const set = (key: string, next: JsonValue | undefined) => {
        let object =
            next === undefined
                ? withoutKey(value, key)
                : { ...value, [key]: next }
        // Settings written for the previous node would not fit the new one.
        if (key === typeKey && next !== value[key])
            for (const p of properties)
                if (p.spec.kind === 'nodeConfig')
                    object = withoutKey(object, p.key)
        onChange(object, key)
    }
    return (
        <div className="flex flex-col gap-4">
            {properties.map(({ key, required, spec }) => (
                <ConfigField
                    key={key}
                    spec={spec}
                    label={spec.title ?? humanize(key)}
                    path={[...path, key]}
                    value={value[key]}
                    required={required}
                    optional
                    issues={issues}
                    nodeType={nodeType}
                    onChange={(next) => {
                        set(key, next)
                    }}
                />
            ))}
            {unknown.map((key) => (
                <div key={key} className="flex flex-col gap-1">
                    <JsonField
                        spec={{
                            kind: 'json',
                            title: undefined,
                            description: undefined,
                            defaultValue: undefined,
                            nullable: false,
                        }}
                        label={`${key} (not in this node's settings)`}
                        path={[...path, key]}
                        value={value[key]}
                        required={false}
                        optional
                        issues={issues}
                        onChange={(next) => {
                            set(key, next)
                        }}
                    />
                    <Button
                        size="sm"
                        variant="ghost"
                        className="self-start"
                        onClick={() => {
                            set(key, undefined)
                        }}
                    >
                        Remove {key}
                    </Button>
                </div>
            ))}
        </div>
    )
}
