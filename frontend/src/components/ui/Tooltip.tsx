import {
    cloneElement,
    useEffect,
    useId,
    useState,
    type ReactElement,
} from 'react'

export interface TooltipProps {
    /** Supplementary text. Never the only label: icon buttons still need
     *  aria-label, the tooltip becomes their description. */
    content: string
    /** One focusable element. Disabled buttons fire no hover/focus events,
     *  so they cannot show a tooltip. */
    children: ReactElement<{ 'aria-describedby'?: string }>
}

/** Shows on hover and keyboard focus, hides on Escape (WCAG 1.4.13). */
export function Tooltip({ content, children }: TooltipProps) {
    const [open, setOpen] = useState(false)
    const id = useId()

    useEffect(() => {
        if (!open) return
        function onKeyDown(event: KeyboardEvent) {
            if (event.key === 'Escape') setOpen(false)
        }
        document.addEventListener('keydown', onKeyDown)
        return () => {
            document.removeEventListener('keydown', onKeyDown)
        }
    }, [open])

    const show = () => {
        setOpen(true)
    }
    const hide = () => {
        setOpen(false)
    }

    return (
        // Hover listeners sit on the wrapper so the pointer can move onto the
        // tooltip itself without it disappearing.
        <span
            className="relative inline-flex"
            onMouseEnter={show}
            onMouseLeave={hide}
            onFocus={show}
            onBlur={hide}
        >
            {cloneElement(children, { 'aria-describedby': id })}
            <span
                role="tooltip"
                id={id}
                hidden={!open}
                className="absolute bottom-full left-1/2 z-30 mb-2 w-max max-w-64 -translate-x-1/2 rounded bg-fg px-2 py-1 text-xs text-canvas shadow"
            >
                {content}
            </span>
        </span>
    )
}
