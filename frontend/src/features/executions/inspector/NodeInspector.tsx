import { useState } from 'react'
import type { LogLevel } from '../../../api/executions'
import type { NodeExecution } from '../../../api/reports'
import { Card } from '../../../components/ui'
import { Time } from '../shared'
import {
    ErrorText,
    Item,
    JsonBlock,
    Mono,
    NodeStatusBadge,
    None,
} from './display'
import { ms } from './format'
import { LevelFilter, LogList } from './LogList'

const amount = (n: number) =>
    n.toLocaleString(undefined, { maximumFractionDigits: 6 })

/** Everything recorded about one node of one execution. */
export function NodeInspector({
    executionId,
    node,
    nodeVersion,
}: {
    executionId: string
    node: NodeExecution
    nodeVersion: string | undefined
}) {
    const [level, setLevel] = useState<LogLevel>()
    const title = node.label || node.node_id
    // `failures` holds every failed attempt; the last one is `error` when
    // the node ended failing, so earlier ones were retried.
    const retried = node.error ? node.failures.slice(0, -1) : node.failures
    return (
        <Card
            title={`Node: ${title}`}
            description={
                <>
                    <Mono>{node.node_type}</Mono>
                    {nodeVersion && ` ${nodeVersion}`} ·{' '}
                    <Mono>{node.node_id}</Mono>
                </>
            }
        >
            <div className="flex flex-col gap-6">
                <dl className="grid gap-4 text-sm sm:grid-cols-2 lg:grid-cols-3">
                    <Item term="Status">
                        <NodeStatusBadge status={node.status} />
                    </Item>
                    <Item term="Attempts">
                        {String(node.attempts)}{' '}
                        <span className="text-muted">
                            ({String(node.retries)}{' '}
                            {node.retries === 1 ? 'retry' : 'retries'})
                        </span>
                    </Item>
                    <Item term="Duration of the last attempt">
                        {ms(node.duration_ms)}
                    </Item>
                    <Item term="Last attempt started">
                        <Time iso={node.started_at} />
                    </Item>
                    <Item term="Finished">
                        <Time iso={node.finished_at} />
                    </Item>
                    <Item term="Tokens">
                        {node.usage ? (
                            <>
                                {amount(node.usage.total_tokens)}{' '}
                                <span className="text-muted">
                                    ({amount(node.usage.input_tokens)} in,{' '}
                                    {amount(node.usage.output_tokens)} out)
                                </span>
                            </>
                        ) : (
                            <None />
                        )}
                    </Item>
                    <Item term="Estimated cost">
                        {node.cost ? (
                            `${amount(node.cost.amount)} ${node.cost.currency}`
                        ) : (
                            <None />
                        )}
                    </Item>
                </dl>

                {node.error && (
                    <section aria-label="Error">
                        <h3 className="mb-2 text-sm font-semibold">Error</h3>
                        <ErrorText error={node.error} />
                    </section>
                )}
                {retried.length > 0 && (
                    <section aria-label="Retried failures">
                        <h3 className="mb-2 text-sm font-semibold">
                            Retried failures
                        </h3>
                        <ol className="flex flex-col gap-3 text-sm">
                            {retried.map((failure) => (
                                <li key={failure.attempt}>
                                    <p className="text-xs text-muted">
                                        Attempt {String(failure.attempt)}
                                    </p>
                                    <ErrorText error={failure} />
                                </li>
                            ))}
                        </ol>
                    </section>
                )}

                <section aria-label="Outputs">
                    <h3 className="mb-2 text-sm font-semibold">Outputs</h3>
                    {node.outputs === null ? (
                        <p className="text-sm text-muted">
                            {node.status === 'completed'
                                ? 'No outputs.'
                                : 'Only a completed node has outputs.'}
                        </p>
                    ) : (
                        <JsonBlock value={node.outputs} />
                    )}
                </section>

                <section aria-label="Node logs" className="flex flex-col gap-3">
                    <div className="flex flex-wrap items-end justify-between gap-3">
                        <h3 className="text-sm font-semibold">Logs</h3>
                        <LevelFilter value={level} onChange={setLevel} />
                    </div>
                    <LogList
                        executionId={executionId}
                        filter={{
                            node_id: node.node_id,
                            ...(level && { level }),
                        }}
                    />
                </section>
            </div>
        </Card>
    )
}
