import { createContext, use } from 'react'
import type { FeedbackTone } from './shared'

export interface ToastOptions {
    tone?: FeedbackTone
    title: string
    description?: string
}

export interface ToastApi {
    /** Shows a toast and returns its id. */
    notify: (toast: ToastOptions) => number
    dismiss: (id: number) => void
}

export const ToastContext = createContext<ToastApi | null>(null)

export function useToast(): ToastApi {
    const api = use(ToastContext)
    if (!api) throw new Error('useToast must be used inside <ToastProvider>')
    return api
}
