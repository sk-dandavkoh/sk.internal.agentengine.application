import { useQuery } from '@tanstack/react-query'
import { useCallback, useMemo } from 'react'
import { useApiClient } from '../../../api/context'
import type { Execution } from '../../../api/executions'
import { workflowQueries } from '../../../api/queries'
import type { ExecutionReport, NodeExecution } from '../../../api/reports'
import { QuerySection } from '../../../components/QuerySection'
import { Button, Card, Table, type TableColumn } from '../../../components/ui'
import { SnapshotCanvas } from '../../versions/SnapshotCanvas'
import { Time } from '../shared'
import { NodeStatusBadge } from './display'
import { ms } from './format'
import { NodeInspector } from './NodeInspector'

/**
 * The pinned version's graph with each node's state in this execution.
 * Clicking a node opens it in the Nodes tab.
 */
export function GraphPanel({
    execution,
    report,
    onInspect,
}: {
    execution: Execution
    report: ExecutionReport
    onInspect: (nodeId: string) => void
}) {
    const version = useQuery(
        workflowQueries.version(
            useApiClient(),
            execution.workflow_id,
            execution.version,
        ),
    )
    const byId = useMemo(
        () => new Map(report.nodes.map((n) => [n.node_id, n])),
        [report],
    )
    const footer = useCallback(
        (nodeId: string) => {
            const node = byId.get(nodeId)
            if (!node) return undefined
            return (
                <span className="flex flex-wrap items-center gap-2">
                    <NodeStatusBadge status={node.status} />
                    {node.retries > 0 && (
                        <span className="text-muted">
                            {String(node.retries)}{' '}
                            {node.retries === 1 ? 'retry' : 'retries'}
                        </span>
                    )}
                    {node.duration_ms !== null && (
                        <span className="text-muted">
                            {ms(node.duration_ms)}
                        </span>
                    )}
                </span>
            )
        },
        [byId],
    )
    return (
        <Card
            title="Execution graph"
            description="The published version this execution ran, with each node's state. Select a node to inspect it."
        >
            <QuerySection query={version} subject="the graph">
                {(v) => (
                    <SnapshotCanvas
                        graph={v.graph}
                        label="Execution graph (read-only)"
                        footer={footer}
                        onNodeClick={onInspect}
                    />
                )}
            </QuerySection>
        </Card>
    )
}

export function NodesPanel({
    executionId,
    report,
    selected,
    onInspect,
}: {
    executionId: string
    report: ExecutionReport
    selected: string | undefined
    onInspect: (nodeId: string) => void
}) {
    const columns: TableColumn<NodeExecution>[] = [
        {
            key: 'node',
            header: 'Node',
            cell: (n) => (
                <>
                    {n.label || n.node_id}{' '}
                    <span className="text-muted">{n.node_type}</span>
                </>
            ),
        },
        {
            key: 'status',
            header: 'Status',
            cell: (n) => <NodeStatusBadge status={n.status} />,
        },
        {
            key: 'attempts',
            header: 'Attempts',
            align: 'end',
            cell: (n) => String(n.attempts),
        },
        {
            key: 'started',
            header: 'Started',
            cell: (n) => <Time iso={n.started_at} />,
        },
        {
            key: 'finished',
            header: 'Finished',
            cell: (n) => <Time iso={n.finished_at} />,
        },
        {
            key: 'duration',
            header: 'Duration',
            align: 'end',
            cell: (n) => (
                <span className="whitespace-nowrap">{ms(n.duration_ms)}</span>
            ),
        },
        {
            key: 'inspect',
            header: <span className="sr-only">Inspect</span>,
            align: 'end',
            cell: (n) => (
                <Button
                    size="sm"
                    variant={n.node_id === selected ? 'primary' : 'ghost'}
                    aria-pressed={n.node_id === selected}
                    aria-label={`Inspect node ${n.label || n.node_id}`}
                    onClick={() => {
                        onInspect(n.node_id)
                    }}
                >
                    Inspect
                </Button>
            ),
        },
    ]
    const node = report.nodes.find((n) => n.node_id === selected)
    return (
        <div className="flex flex-col gap-6">
            <Card title="Nodes">
                <Table
                    caption="Node status"
                    hideCaption
                    columns={columns}
                    rows={report.nodes}
                    rowKey={(n) => n.node_id}
                    empty="This execution has no node executions."
                />
            </Card>
            {node ? (
                <NodeInspector
                    // Keyed: filters belong to one node.
                    key={node.node_id}
                    executionId={executionId}
                    node={node}
                    nodeVersion={report.execution.node_versions[node.node_type]}
                />
            ) : (
                selected && (
                    <p className="text-sm text-muted">
                        Node <code className="font-mono">{selected}</code> is
                        not part of this execution.
                    </p>
                )
            )}
        </div>
    )
}
