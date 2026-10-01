import type { SVGProps } from 'react'

// Decorative only: every icon is aria-hidden, the meaning is carried by text.
function Icon({ children, ...props }: SVGProps<SVGSVGElement>) {
    return (
        <svg
            viewBox="0 0 20 20"
            fill="none"
            stroke="currentColor"
            strokeWidth={1.75}
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
            focusable="false"
            className="size-4 shrink-0"
            {...props}
        >
            {children}
        </svg>
    )
}

export function InfoIcon() {
    return (
        <Icon>
            <circle cx="10" cy="10" r="7.5" />
            <path d="M10 9v4.5M10 6.5v.01" />
        </Icon>
    )
}

export function SuccessIcon() {
    return (
        <Icon>
            <circle cx="10" cy="10" r="7.5" />
            <path d="m6.75 10.25 2.25 2.25 4.25-4.5" />
        </Icon>
    )
}

export function WarningIcon() {
    return (
        <Icon>
            <path d="M10 3 2.5 16.5h15L10 3Z" />
            <path d="M10 8.5v3.5M10 14.25v.01" />
        </Icon>
    )
}

export function DangerIcon() {
    return (
        <Icon>
            <circle cx="10" cy="10" r="7.5" />
            <path d="m7.5 7.5 5 5M12.5 7.5l-5 5" />
        </Icon>
    )
}

export function CloseIcon() {
    return (
        <Icon>
            <path d="m5 5 10 10M15 5 5 15" />
        </Icon>
    )
}

export function ChevronDownIcon() {
    return (
        <Icon>
            <path d="m5.5 8 4.5 4.5L14.5 8" />
        </Icon>
    )
}

export function Spinner() {
    return (
        <Icon className="size-4 shrink-0 animate-spin">
            <path d="M10 2.5a7.5 7.5 0 1 0 7.5 7.5" />
        </Icon>
    )
}
