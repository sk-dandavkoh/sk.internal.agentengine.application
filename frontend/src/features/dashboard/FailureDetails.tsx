import { useQuery } from '@tanstack/react-query'
import { useApiClient } from '../../api/context'
import { executionQueries } from '../../api/queries'
import type { ExecutionError } from '../../api/reports'
import { QuerySection } from './QuerySection'

function ErrorEntry({ error }: { error: ExecutionError }) {
    return (
        <>
            <p className="font-mono text-xs text-danger-text">{error.code}</p>
            {/* Redacted by the backend; rendered as text, never as HTML. */}
            <p className="break-words whitespace-pre-wrap">{error.message}</p>
        </>
    )
}

/**
 * The recorded errors of one execution, from its report. Mounted only when
 * the user expands the row, so the report is fetched on demand.
 */
export function FailureDetails({
    id,
    executionId,
}: {
    id: string
    executionId: string
}) {
    const query = useQuery(executionQueries.report(useApiClient(), executionId))
    return (
        <div
            id={id}
            className="rounded-md border border-border bg-surface-muted p-3 text-sm"
        >
            <QuerySection query={query} subject="error details">
                {(report) => {
                    const failedNodes = report.nodes.filter((n) => n.error)
                    const error = report.execution.error
                    if (!error && failedNodes.length === 0)
                        return (
                            <p className="text-muted">
                                No error details were recorded for this
                                execution.
                            </p>
                        )
                    return (
                        <dl className="flex flex-col gap-3">
                            {error && (
                                <div>
                                    <dt className="font-medium">Execution</dt>
                                    <dd>
                                        <ErrorEntry error={error} />
                                    </dd>
                                </div>
                            )}
                            {failedNodes.map((node) => (
                                <div key={node.node_id}>
                                    <dt className="font-medium">
                                        Node {node.label || node.node_id}{' '}
                                        <span className="font-normal text-muted">
                                            ({node.node_type}, attempt{' '}
                                            {String(node.attempts)})
                                        </span>
                                    </dt>
                                    <dd>
                                        {node.error && (
                                            <ErrorEntry error={node.error} />
                                        )}
                                    </dd>
                                </div>
                            ))}
                        </dl>
                    )
                }}
            </QuerySection>
        </div>
    )
}
