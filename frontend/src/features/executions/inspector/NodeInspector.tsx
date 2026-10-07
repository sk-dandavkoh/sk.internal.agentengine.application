import { useState } from 'react'
import type { LogLevel } from '../../../api/executions'
import type { NodeExecution } from '../../../api/reports'
import { Card } from '../../../components/ui'
import { Time } from '../shared'
import { ErrorText, Item, Mono, NodeStatusBadge, None } from './display'
import { NodeData } from './NodeData'
import { ms } from './format'
import { LevelFilter, LogList } from './LogList'

const amount = (n: number) =>
    n.toLocaleString(undefined, { maximumFractionDigits: 6 })

interface NodeProps {
    executionId: string
    node: NodeExecution
    nodeVersion: string | undefined
    /** Names a node id of this execution, for "from …" lines. */
    title: (nodeId: string) => string
}

/** Type, version and id of the node: technical, so small and mono. */
export function NodeIdentity({
    node,
    nodeVersion,
}: Pick<NodeProps, 'node' | 'nodeVersion'>) {
    return (
        <>
            <Mono>{node.node_type}</Mono>
            {nodeVersion && ` ${nodeVersion}`} · <Mono>{node.node_id}</Mono>
        </>
    )
}

/** Everything recorded about one node of one execution, as a card. */
export function NodeInspector(props: NodeProps) {
    return (
        <Card
            title={`Node: ${props.title(props.node.node_id)}`}
            description={<NodeIdentity {...props} />}
        >
            <NodeRunDetails {...props} />
        </Card>
    )
}

/** The facts, input | output, errors and logs of one node run. */
export function NodeRunDetails({ executionId, node, title }: NodeProps) {
    const [level, setLevel] = useState<LogLevel>()
    // `failures` holds every failed attempt; the last one is `error` when
    // the node ended failing, so earlier ones were retried.
    const retried = node.error ? node.failures.slice(0, -1) : node.failures
    return (
        <div className="flex flex-col gap-6">
            {node.error && (
                <section aria-label="Error">
                    <h3 className="mb-2 text-sm font-semibold">Error</h3>
                    <ErrorText error={node.error} />
                </section>
            )}
            {/* The data first: it is what a reader came for. */}
            <NodeData node={node} title={title} />
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
    )
}
