import { useQuery } from '@tanstack/react-query'
import { useId, useState, type SubmitEvent } from 'react'
import { useApiClient } from '../../api/context'
import { errorMessage } from '../../api/errors'
import type { Project, Trigger, TriggerType } from '../../api/projects'
import { projectQueries } from '../../api/queries'
import type { JsonObject } from '../../api/schemas'
import {
    Alert,
    Button,
    Checkbox,
    Dialog,
    Input,
    Select,
} from '../../components/ui'
import { CredentialInput } from '../credentials/CredentialInput'
import {
    creatableTypes,
    EVENT_SOURCE_FIELD,
    MAX_INTERVAL_SECONDS,
    newTriggerId,
    triggerTypeDescriptions,
    triggerableEvents,
    triggerTypeLabels,
    WEBHOOK_SECRET_FIELD,
} from './triggerTypes'

interface TriggerFormDialogProps {
    /** The project the trigger belongs to. */
    project: Project
    open: boolean
    onClose: () => void
    /** Absent: create. Present: edit (its type is fixed). */
    trigger?: Trigger | undefined
    pending: boolean
    error: unknown
    onSubmit: (trigger: Trigger) => void
}

export function TriggerFormDialog({
    project,
    open,
    onClose,
    trigger,
    pending,
    error,
    onSubmit,
}: TriggerFormDialogProps) {
    const formId = useId()
    const editing = trigger !== undefined
    return (
        <Dialog
            open={open}
            onClose={onClose}
            title={
                editing
                    ? `Edit ${triggerTypeLabels[trigger.trigger_type]} trigger`
                    : 'New trigger'
            }
            description="Saved to the draft. Triggers fire only after the draft is published."
            dismissible={!pending}
            footer={
                <>
                    <Button onClick={onClose} disabled={pending}>
                        Cancel
                    </Button>
                    <Button
                        type="submit"
                        form={formId}
                        variant="primary"
                        loading={pending}
                    >
                        {editing ? 'Save trigger' : 'Create trigger'}
                    </Button>
                </>
            }
        >
            {/* Remounted per open, so the form starts from `trigger`. */}
            <TriggerForm
                project={project}
                formId={formId}
                trigger={trigger}
                pending={pending}
                error={error}
                onSubmit={onSubmit}
            />
        </Dialog>
    )
}

const text = (config: JsonObject, key: string) => {
    const value = config[key]
    return typeof value === 'string' ? value : ''
}

function TriggerForm({
    project,
    formId,
    trigger,
    pending,
    error,
    onSubmit,
}: Omit<TriggerFormDialogProps, 'open' | 'onClose'> & { formId: string }) {
    const initial = trigger?.config ?? {}
    const [type, setType] = useState<TriggerType>(
        trigger?.trigger_type ?? 'manual',
    )
    const [enabled, setEnabled] = useState(trigger?.enabled ?? true)
    const [cron, setCron] = useState(text(initial, 'cron'))
    const [seconds, setSeconds] = useState(() => {
        const stored = initial['interval_seconds']
        return typeof stored === 'number' ? String(stored) : ''
    })
    const [event, setEvent] = useState(
        text(initial, 'event') || 'execution.completed',
    )
    // '' = this project.
    const [source, setSource] = useState(text(initial, EVENT_SOURCE_FIELD))
    const [secret, setSecret] = useState<unknown>(initial[WEBHOOK_SECRET_FIELD])
    const [secretMissing, setSecretMissing] = useState(false)

    function submit(e: SubmitEvent<HTMLFormElement>) {
        e.preventDefault()
        if (pending) return
        // Keys this form does not know are kept as they are.
        const config: JsonObject = { ...initial }
        switch (type) {
            case 'cron':
                config['cron'] = cron.trim()
                break
            case 'interval':
                config['interval_seconds'] = Number(seconds)
                break
            case 'event':
                config['event'] = event
                if (source === '') delete config['source_project_id']
                else config[EVENT_SOURCE_FIELD] = source
                break
            case 'webhook':
                // Publishing needs it; saving without one only defers the
                // error, so the form asks for it up front.
                if (typeof secret !== 'string' || secret === '') {
                    setSecretMissing(true)
                    return
                }
                config[WEBHOOK_SECRET_FIELD] = secret
                break
            case 'manual':
            case 'api':
                break
        }
        onSubmit({
            id: trigger?.id ?? newTriggerId(),
            trigger_type: type,
            config,
            enabled,
        })
    }

    return (
        <form id={formId} onSubmit={submit} className="flex flex-col gap-4">
            {error !== null && error !== undefined && (
                <Alert tone="danger">{errorMessage(error)}</Alert>
            )}
            {trigger === undefined ? (
                <Select
                    label="Type"
                    value={type}
                    description={triggerTypeDescriptions[type]}
                    onChange={(e) => {
                        const next = creatableTypes.find(
                            (t) => t === e.target.value,
                        )
                        if (next) setType(next)
                    }}
                    data-autofocus
                >
                    {creatableTypes.map((t) => (
                        <option key={t} value={t}>
                            {triggerTypeLabels[t]}
                        </option>
                    ))}
                </Select>
            ) : (
                <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
                    <dt className="text-muted">Type</dt>
                    <dd>
                        {triggerTypeLabels[type]}
                        <p className="text-muted">
                            {triggerTypeDescriptions[type]}
                        </p>
                    </dd>
                    <dt className="text-muted">Trigger ID</dt>
                    <dd>
                        <code className="font-mono text-xs break-all">
                            {trigger.id}
                        </code>
                    </dd>
                </dl>
            )}
            {type === 'cron' && (
                <Input
                    label="Cron expression"
                    required
                    value={cron}
                    onChange={(e) => {
                        setCron(e.target.value)
                    }}
                    placeholder="*/15 * * * *"
                    autoComplete="off"
                    spellCheck={false}
                    description="5 fields: minute hour day-of-month month day-of-week, in UTC. Checked by the server after saving."
                />
            )}
            {type === 'interval' && (
                <Input
                    label="Interval (seconds)"
                    type="number"
                    required
                    min={1}
                    max={MAX_INTERVAL_SECONDS}
                    step="any"
                    value={seconds}
                    onChange={(e) => {
                        setSeconds(e.target.value)
                    }}
                    description={`Between 1 second and ${String(MAX_INTERVAL_SECONDS)} seconds (one year).`}
                />
            )}
            {type === 'event' && (
                <EventFields
                    project={project}
                    event={event}
                    source={source}
                    onEvent={setEvent}
                    onSource={setSource}
                />
            )}
            {type === 'webhook' && (
                <CredentialInput
                    label="Signing secret"
                    required
                    value={secret}
                    error={
                        secretMissing
                            ? 'Choose the credential whose value signs webhook requests.'
                            : undefined
                    }
                    description="Callers sign the raw request body with HMAC-SHA256 under this credential's value."
                    onChange={(reference) => {
                        setSecret(reference)
                        setSecretMissing(false)
                    }}
                />
            )}
            <Checkbox
                switch
                label="Enabled"
                description="Disabled triggers stay configured but never fire."
                checked={enabled}
                onChange={(e) => {
                    setEnabled(e.target.checked)
                }}
            />
        </form>
    )
}

function EventFields({
    project,
    event,
    source,
    onEvent,
    onSource,
}: {
    project: Project
    event: string
    source: string
    onEvent: (event: string) => void
    onSource: (source: string) => void
}) {
    const client = useApiClient()
    const projects = useQuery(
        projectQueries.list(client, { limit: 100, sort: 'name' }),
    )
    // The projects the caller may access (all for an admin). The backend
    // fires only if the trigger project's owner may access the source too.
    const sources = (projects.data?.items ?? []).filter(
        (p) => p.id !== project.id,
    )
    const unknownSource = source !== '' && !sources.some((p) => p.id === source)
    return (
        <>
            <Select
                label="Event"
                required
                value={event}
                onChange={(e) => {
                    onEvent(e.target.value)
                }}
            >
                {Object.entries(triggerableEvents).map(([name, label]) => (
                    <option key={name} value={name}>
                        {label} ({name})
                    </option>
                ))}
                {!(event in triggerableEvents) && (
                    <option value={event}>Unsupported: {event}</option>
                )}
            </Select>
            <Select
                label="Source project"
                value={source}
                description={
                    projects.isError
                        ? `Could not load your projects: ${errorMessage(projects.error)}`
                        : 'Whose events start this trigger. It fires only if this project’s owner can access the source project: an admin any project, a member only their own.'
                }
                onChange={(e) => {
                    onSource(e.target.value)
                }}
            >
                <option value="">This project</option>
                {sources.map((p) => (
                    <option key={p.id} value={p.id}>
                        {p.name}
                    </option>
                ))}
                {unknownSource && (
                    <option value={source}>Unavailable project {source}</option>
                )}
            </Select>
        </>
    )
}
