import type { ComponentType } from 'react'
import type { FieldMessages } from './Field'
import { DangerIcon, InfoIcon, SuccessIcon, WarningIcon } from './icons'

export type Tone = 'neutral' | 'info' | 'success' | 'warning' | 'danger'
export type FeedbackTone = Exclude<Tone, 'neutral'>

export const toneClasses: Record<Tone, string> = {
    neutral: 'bg-neutral-soft text-neutral-text',
    info: 'bg-info-soft text-info-text',
    success: 'bg-success-soft text-success-text',
    warning: 'bg-warning-soft text-warning-text',
    danger: 'bg-danger-soft text-danger-text',
}

// Feedback is never conveyed by color alone: each tone has an icon and a
// screen-reader prefix.
export const feedbackTones: Record<
    FeedbackTone,
    { icon: ComponentType; srLabel: string }
> = {
    info: { icon: InfoIcon, srLabel: 'Info:' },
    success: { icon: SuccessIcon, srLabel: 'Success:' },
    warning: { icon: WarningIcon, srLabel: 'Warning:' },
    danger: { icon: DangerIcon, srLabel: 'Error:' },
}

export const controlClasses = (invalid: boolean) =>
    `focus-ring block w-full rounded-md border bg-surface px-3 text-sm text-fg placeholder:text-muted disabled:cursor-not-allowed disabled:bg-surface-muted disabled:opacity-70 ${
        invalid ? 'border-danger' : 'border-border-strong'
    }`

export function fieldDescribedBy(
    id: string,
    { description, error }: FieldMessages,
) {
    const ids = [description && `${id}-description`, error && `${id}-error`]
    return ids.filter(Boolean).join(' ') || undefined
}
