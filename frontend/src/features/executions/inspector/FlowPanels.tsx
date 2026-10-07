import { useQuery } from '@tanstack/react-query'
import { useCallback, useMemo } from 'react'
import { useApiClient } from '../../../api/context'
import type { Execution } from '../../../api/executions'
import { nodeQueries, workflowQueries } from '../../../api/queries'
import type { ExecutionReport, NodeExecution } from '../../../api/reports'
import { QuerySection } from '../../../components/QuerySection'
import {
    Button,
    Card,
    Dialog,
    Table,
    type TableColumn,
} from '../../../components/ui'
import { SnapshotCanvas } from '../../versions/SnapshotCanvas'
import { Time } from '../shared'
import { NodeStatusBadge } from './display'
import { ms } from './format'

/** Names a node of `report` like the editor does: its label, else its
 * type's name in the catalog, else its type. */
function useNodeName(report: ExecutionReport) {
    const catalog = useQuery(nodeQueries.catalog(useApiClient())).data
    return useMemo(() => {
        const names = new Map(catalog?.items.map((d) => [d.node_type, d.name]))
        const byId = new Map(report.nodes.map((n) => [n.node_id, n]))
        return (nodeId: string) => {
            const node = byId.get(nodeId)
            return node
                ? node.label || (names.get(node.node_type) ?? node.node_type)
                : nodeId
        }
    }, [catalog, report])
}
import { NodeIdentity, NodeInspector, NodeRunDetails } from './NodeInspector'

/**
 * The pinned version's graph with each node's state in this execution.
 * Clicking a node opens what it got and produced in a dialog.
 */
export function GraphPanel({
    execution,
    report,
    selected,
    onInspect,
    onClose,
}: {
    execution: Execution
    report: ExecutionReport
    selected: string | undefined
    onInspect: (nodeId: string) => void
    onClose: () => void
}) {
    const name = useNodeName(report)
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
    const node = selected === undefined ? undefined : byId.get(selected)
    return (
        <Card
            title="Execution graph"
            description="The published version this execution ran, with each node's state. Click a node to see what it got and what it produced."
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
            {node && (
                <Dialog
                    open
                    onClose={onClose}
                    size="full"
                    title={name(node.node_id)}
                    description={
                        <NodeIdentity
                            node={node}
                            nodeVersion={
                                report.execution.node_versions[node.node_type]
                            }
                        />
                    }
                >
                    <NodeRunDetails
                        // Keyed: log filters belong to one node.
                        key={node.node_id}
                        executionId={execution.id}
                        node={node}
                        nodeVersion={undefined}
                        title={name}
                    />
                </Dialog>
            )}
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
    const name = useNodeName(report)
    const columns: TableColumn<NodeExecution>[] = [
        {
            key: 'node',
            header: 'Node',
            cell: (n) => (
                <>
                    {name(n.node_id)}{' '}
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
                    aria-label={`Inspect node ${name(n.node_id)}`}
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
                    title={name}
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
