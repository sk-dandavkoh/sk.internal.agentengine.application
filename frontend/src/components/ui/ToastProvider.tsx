import {
    useCallback,
    useEffect,
    useMemo,
    useRef,
    useState,
    type ReactNode,
} from 'react'
import { cx } from '../../lib/cx'
import { Button } from './Button'
import { CloseIcon } from './icons'
import { feedbackTones, toneClasses } from './shared'
import { ToastContext, type ToastOptions } from './toastContext'

interface Toast extends ToastOptions {
    id: number
}

const TOAST_DURATION_MS = 5000
// Older toasts are dropped beyond this, so bursts cannot flood the screen.
const MAX_TOASTS = 4

export function ToastProvider({ children }: { children: ReactNode }) {
    const [toasts, setToasts] = useState<Toast[]>([])
    const nextId = useRef(0)

    const dismiss = useCallback((id: number) => {
        setToasts((current) => current.filter((toast) => toast.id !== id))
    }, [])

    const notify = useCallback((options: ToastOptions) => {
        nextId.current += 1
        const id = nextId.current
        setToasts((current) =>
            [...current, { ...options, id }].slice(-MAX_TOASTS),
        )
        return id
    }, [])

    const api = useMemo(() => ({ notify, dismiss }), [notify, dismiss])

    return (
        <ToastContext value={api}>
            {children}
            {/* Always mounted, so screen readers pick up toasts added later. */}
            <section
                aria-label="Notifications"
                aria-live="polite"
                className="pointer-events-none fixed inset-x-4 bottom-4 z-50 flex flex-col items-end gap-2 sm:left-auto"
            >
                {toasts.map((toast) => (
                    <ToastItem
                        key={toast.id}
                        toast={toast}
                        onDismiss={dismiss}
                    />
                ))}
            </section>
        </ToastContext>
    )
}

function ToastItem({
    toast,
    onDismiss,
}: {
    toast: Toast
    onDismiss: (id: number) => void
}) {
    const { id, tone = 'info', title, description } = toast
    const { icon: Icon, srLabel } = feedbackTones[tone]
    // Errors and warnings stay until dismissed; the rest time out, paused
    // while the pointer or focus is on the toast (WCAG 2.2.1).
    const autoDismiss = tone === 'info' || tone === 'success'
    const [paused, setPaused] = useState(false)

    useEffect(() => {
        if (!autoDismiss || paused) return
        const timer = setTimeout(() => {
            onDismiss(id)
        }, TOAST_DURATION_MS)
        return () => {
            clearTimeout(timer)
        }
    }, [autoDismiss, paused, id, onDismiss])

    return (
        <div
            role={tone === 'danger' ? 'alert' : 'status'}
            onMouseEnter={() => {
                setPaused(true)
            }}
            onMouseLeave={() => {
                setPaused(false)
            }}
            onFocus={() => {
                setPaused(true)
            }}
            onBlur={() => {
                setPaused(false)
            }}
            className={cx(
                'pointer-events-auto flex w-full max-w-sm items-start gap-3 rounded-lg border border-border p-3 text-sm shadow-lg',
                toneClasses[tone],
            )}
        >
            <span className="mt-0.5">
                <Icon />
            </span>
            <div className="flex flex-1 flex-col gap-0.5">
                <p className="font-semibold">
                    <span className="sr-only">{srLabel} </span>
                    {title}
                </p>
                {description && <p>{description}</p>}
            </div>
            <Button
                variant="ghost"
                size="sm"
                aria-label="Dismiss notification"
                onClick={() => {
                    onDismiss(id)
                }}
                className="-my-1 -mr-1"
            >
                <CloseIcon />
            </Button>
        </div>
    )
}
