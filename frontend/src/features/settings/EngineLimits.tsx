import { useQuery } from '@tanstack/react-query'
import { useApiClient } from '../../api/context'
import { systemQueries } from '../../api/queries'
import type { EngineLimits } from '../../api/system'
import { QuerySection } from '../../components/QuerySection'
import { Card } from '../../components/ui'
import { formatBytes, formatSeconds } from '../../lib/format'

// What this installation allows, in plain words, from GET /system/limits:
// the operator's settings, so nobody has to guess (or read the server's
// environment) how long a run may take.

interface Row {
    key: keyof EngineLimits
    term: string
    value: (limits: EngineLimits) => string
    explain: string
}

const ROWS: readonly Row[] = [
    {
        key: 'execution_timeout_seconds',
        term: 'A whole run',
        value: (l) => formatSeconds(l.execution_timeout_seconds),
        explain:
            'From its start, waiting for people included; then it stops as timed out.',
    },
    {
        key: 'node_timeout_seconds',
        term: 'One node',
        value: (l) => formatSeconds(l.node_timeout_seconds),
        explain:
            'Per attempt; a node setting can shorten it but not extend it.',
    },
    {
        key: 'node_test_timeout_seconds',
        term: 'A node test',
        value: (l) => formatSeconds(l.node_test_timeout_seconds),
        explain: 'When you test one node in the editor.',
    },
    {
        key: 'llm_timeout_seconds',
        term: 'An AI model call',
        value: (l) => formatSeconds(l.llm_timeout_seconds),
        explain: 'When the node sets no timeout of its own.',
    },
    {
        key: 'llm_max_output_tokens',
        term: 'An AI reply',
        value: (l) => `${l.llm_max_output_tokens.toLocaleString()} tokens`,
        explain:
            'When the node sets no maximum; also the highest maximum allowed (≈ ¾ word per token).',
    },
    {
        key: 'node_max_attempts',
        term: 'Attempts per node',
        value: (l) => String(l.node_max_attempts),
        explain: 'The first try plus retries.',
    },
    {
        key: 'graph_max_nodes',
        term: 'Nodes per workflow',
        value: (l) => l.graph_max_nodes.toLocaleString(),
        explain: 'Publishing a larger workflow is refused.',
    },
    {
        key: 'graph_max_depth',
        term: 'Nodes in a row',
        value: (l) => l.graph_max_depth.toLocaleString(),
        explain: 'The longest chain of nodes, one after another.',
    },
    {
        key: 'execution_concurrency',
        term: 'Nodes at the same time',
        value: (l) => String(l.execution_concurrency),
        explain: 'Within one run; more wait their turn.',
    },
    {
        key: 'active_executions_per_workflow',
        term: 'Unfinished runs per workflow',
        value: (l) => l.active_executions_per_workflow.toLocaleString(),
        explain: 'Starting more is refused until some finish.',
    },
    {
        key: 'node_output_max_bytes',
        term: 'Output of one node',
        value: (l) => formatBytes(l.node_output_max_bytes),
        explain: 'A larger result fails the node.',
    },
    {
        key: 'execution_output_max_bytes',
        term: 'Output of a whole run',
        value: (l) => formatBytes(l.execution_output_max_bytes),
        explain: 'All node results together.',
    },
    {
        key: 'event_trigger_max_depth',
        term: 'Workflows starting workflows',
        value: (l) => String(l.event_trigger_max_depth),
        explain: 'The longest chain of runs that start each other.',
    },
]

export function EngineLimitsList({
    limits,
    only,
}: {
    limits: EngineLimits
    only?: readonly (keyof EngineLimits)[]
}) {
    const rows = only ? ROWS.filter((r) => only.includes(r.key)) : ROWS
    return (
        <dl className="flex flex-col gap-2 text-sm">
            {rows.map((row) => (
                <div key={row.key}>
                    <dt className="flex justify-between gap-3">
                        <span>{row.term}</span>
                        <span className="font-semibold whitespace-nowrap">
                            {row.value(limits)}
                        </span>
                    </dt>
                    <dd className="text-xs text-muted">{row.explain}</dd>
                </div>
            ))}
        </dl>
    )
}

export function EngineLimitsCard() {
    const limits = useQuery(systemQueries.limits(useApiClient()))
    return (
        <Card
            title="Limits"
            description="What this installation allows. An administrator of the server sets them."
        >
            <QuerySection query={limits} subject="the limits">
                {(l) => <EngineLimitsList limits={l} />}
            </QuerySection>
        </Card>
    )
}
