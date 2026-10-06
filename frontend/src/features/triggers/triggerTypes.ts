import type { Trigger, TriggerType } from '../../api/workflows'
import { randomHex } from '../../lib/randomHex'

// Presentation of the backend's trigger types (domain/trigger.py). Which
// configs are valid is the backend's call: PUT rejects structural errors,
// /validate reports schedule and secret problems.

export const triggerTypeLabels: Record<TriggerType, string> = {
    manual: 'Manual',
    api: 'API',
    cron: 'Cron',
    interval: 'Interval',
    webhook: 'Webhook',
    event: 'Event',
}

export const triggerTypeDescriptions: Record<TriggerType, string> = {
    manual: 'Started by a signed-in user through the invoke endpoint.',
    api: 'Started by an authenticated API call to the invoke endpoint.',
    cron: 'Runs on a 5-field cron schedule, evaluated in UTC.',
    interval: 'Runs every N seconds.',
    webhook: 'Started by an external system with an HMAC-signed HTTP request.',
    event: 'Runs when an execution of this workflow, or of another workflow its owner can access, ends, or when it is published. Chains stop after a server-side depth limit.',
}

/** Types offered for new triggers: every type the engine fires. */
export const creatableTypes = [
    'manual',
    'api',
    'cron',
    'interval',
    'webhook',
    'event',
] as const satisfies readonly TriggerType[]

/**
 * backend domain/trigger.py TRIGGERABLE_EVENTS: the events an event trigger
 * may name. Mirrored for the picker only; /validate decides.
 */
export const triggerableEvents: Record<string, string> = {
    'execution.completed': 'An execution completed',
    'execution.failed': 'An execution failed',
    'execution.cancelled': 'An execution was cancelled',
    'execution.timed_out': 'An execution timed out',
    'workflow.published': 'A version was published',
}

/** backend domain/trigger.py EVENT_SOURCE_FIELD. */
export const EVENT_SOURCE_FIELD = 'source_workflow_id'

/** backend domain/trigger.py MAX_INTERVAL_SECONDS. */
export const MAX_INTERVAL_SECONDS = 365 * 24 * 60 * 60

/** backend domain/trigger.py WEBHOOK_SIGNING_FIELD. */
export const WEBHOOK_SECRET_FIELD = 'secret_credential'

/** Same shape as backend `new_id("trig")`. */
export const newTriggerId = () => `trig_${randomHex()}`

/** One line describing what a trigger is configured to do. */
export function triggerSummary(trigger: Trigger): string {
    const { config } = trigger
    const text = (key: string) => {
        const value = config[key]
        return typeof value === 'string' ? value : undefined
    }
    switch (trigger.trigger_type) {
        case 'cron':
            return `Schedule: ${text('cron') ?? '(none)'} (UTC)`
        case 'interval': {
            const seconds = config['interval_seconds']
            return typeof seconds === 'number'
                ? `Every ${String(seconds)} s`
                : 'Every (not set)'
        }
        case 'event': {
            const event = text('event') ?? '(none)'
            const source = text(EVENT_SOURCE_FIELD)
            return `On ${event} in ${source ? `workflow ${source}` : 'this workflow'}`
        }
        case 'webhook': {
            // A reference names a credential; anything else may be a
            // secret pasted by mistake and is never shown.
            const ref = text(WEBHOOK_SECRET_FIELD)
            if (ref === undefined) return 'No signing secret'
            return ref.startsWith('credential:')
                ? `Signed with ${ref}`
                : 'Signing secret is not a credential reference'
        }
        case 'manual':
        case 'api':
            return 'No configuration'
    }
}
