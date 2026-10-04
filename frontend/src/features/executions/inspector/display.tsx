import { useState, type ReactNode } from 'react'
import type { ExecutionError, NodeExecutionStatus } from '../../../api/reports'
import { Badge, Button } from '../../../components/ui'
import { MAX_RENDERED_CHARS, nodeStatusDisplay } from './format'

/** Label and tone together: color is never the only signal (AGENTS §63). */
export function NodeStatusBadge({ status }: { status: NodeExecutionStatus }) {
    const { label, tone } = nodeStatusDisplay[status]
    return (
        <Badge tone={tone} dot>
            {label}
        </Badge>
    )
}

export function Item({
    term,
    children,
}: {
    term: string
    children: ReactNode
}) {
    return (
        <div className="flex flex-col gap-0.5">
            <dt className="text-muted">{term}</dt>
            <dd className="break-words">{children}</dd>
        </div>
    )
}

export const Mono = ({ children }: { children: ReactNode }) => (
    <code className="font-mono text-xs break-all">{children}</code>
)

export const None = () => <span className="text-muted">None</span>

/**
 * Node-, log- or LLM-written text. Untrusted: React renders it as a text
 * node, so markup in it is shown, never interpreted (no HTML, no Markdown,
 * no auto-links). Redaction is the backend's; nothing here reconstructs it.
 */
export function UntrustedText({
    text,
    block = false,
}: {
    text: string
    /** Preformatted (JSON, multi-line output) instead of wrapped prose. */
    block?: boolean
}) {
    const [all, setAll] = useState(false)
    const long = text.length > MAX_RENDERED_CHARS
    const shown = long && !all ? text.slice(0, MAX_RENDERED_CHARS) : text
    const body = block ? (
        <pre className="max-h-96 overflow-auto rounded-md bg-surface-muted p-3 font-mono text-xs break-words whitespace-pre-wrap">
            {shown}
        </pre>
    ) : (
        <span className="break-words whitespace-pre-wrap">{shown}</span>
    )
    if (!long) return body
    return (
        <>
            {body}
            <span className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted">
                {all
                    ? `${text.length.toLocaleString()} characters.`
                    : `Showing the first ${MAX_RENDERED_CHARS.toLocaleString()} of ${text.length.toLocaleString()} characters.`}
                <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => {
                        setAll(!all)
                    }}
                >
                    {all ? 'Show less' : 'Show all'}
                </Button>
            </span>
        </>
    )
}

export function JsonBlock({ value }: { value: unknown }) {
    return <UntrustedText block text={JSON.stringify(value, null, 2)} />
}

export function ErrorText({ error }: { error: ExecutionError }) {
    const details = Object.entries(error.details)
    return (
        <div className="flex flex-col gap-1">
            <p className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-xs text-danger-text">
                    {error.code}
                </span>
                <span className="text-xs text-muted">
                    {error.kind}
                    {error.retryable ? ', retryable' : ''}
                </span>
            </p>
            <UntrustedText text={error.message} />
            {details.length > 0 && (
                <dl className="grid grid-cols-[auto_1fr] gap-x-3 text-xs">
                    {details.map(([key, value]) => (
                        <div key={key} className="contents">
                            <dt className="font-mono text-muted">{key}</dt>
                            <dd className="font-mono break-all">
                                <UntrustedText text={value} />
                            </dd>
                        </div>
                    ))}
                </dl>
            )}
        </div>
    )
}
