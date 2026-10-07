import { useQuery } from '@tanstack/react-query'
import { useApiClient } from '../../api/context'
import type { NodeDefinition } from '../../api/nodes'
import { systemQueries } from '../../api/queries'
import type { JsonObject } from '../../api/schemas'
import type { Edge, Graph, NodeInstance } from '../../api/workflows'
import { Alert, Button, Dialog, Input } from '../../components/ui'
import type { ConnectionRules } from './connections'
import { ConnectionsPanel } from './ConnectionsPanel'
import { nodeTitle } from './graphView'
import { NodeInspector } from './NodeInspector'
import { NodeTestPanel } from './NodeTestPanel'
import { limitNotes } from './limitNotes'
import { capabilitiesOf } from './vocabulary'

/**
 * Everything about one node in one place, opened by clicking it: its name,
 * what it does, its settings and its connections. Edits apply to the draft
 * as they are made (autosaved, undoable), so there is nothing to confirm.
 */
export function NodeDialog({
    node,
    definition,
    graph,
    rules,
    onClose,
    onRename,
    onConfigure,
    onConnect,
    onRemoveEdge,
    onDuplicate,
    onDelete,
    test,
}: {
    /** What a test run needs; absent: no test (e.g. view-only). */
    test?: {
        workflowId: string
        revision: number
        saved: boolean
        onSave: () => void
    }
    node: NodeInstance
    definition: NodeDefinition | undefined
    graph: Graph
    rules: ConnectionRules
    onClose: () => void
    onRename: (label: string) => void
    onConfigure: (config: JsonObject, field: string) => void
    onConnect: (edge: Edge) => void
    onRemoveEdge: (edge: Edge) => void
    onDuplicate: () => void
    onDelete: () => void
}) {
    const limits = useQuery(systemQueries.limits(useApiClient())).data
    const notes = [
        ...(definition ? capabilitiesOf(definition) : []),
        ...(limits ? limitNotes(limits, definition) : []),
    ]
    return (
        <Dialog
            open
            onClose={onClose}
            size="xl"
            title={nodeTitle(node, definition)}
            description={
                definition?.description ??
                `"${node.node_type}" is not an installed node type.`
            }
            footer={
                <>
                    <Button
                        variant="danger"
                        onClick={onDelete}
                        className="mr-auto"
                    >
                        Delete node
                    </Button>
                    <Button onClick={onDuplicate}>Duplicate</Button>
                    <Button variant="primary" onClick={onClose}>
                        Done
                    </Button>
                </>
            }
        >
            <Input
                label="Name on the canvas"
                // No placeholder: a greyed default read as a typed value.
                description={`Optional. Shown instead of "${definition?.name ?? node.node_type}"; a name of your own helps tell nodes apart.`}
                value={node.label}
                data-autofocus
                onChange={(event) => {
                    onRename(event.target.value)
                }}
            />
            {notes.length > 0 && (
                <Alert tone="info" title="Good to know">
                    <ul className="list-inside list-disc">
                        {notes.map((c) => (
                            <li key={c.label}>
                                <span className="font-medium">{c.label}</span>
                                {c.description && `: ${c.description}`}
                            </li>
                        ))}
                    </ul>
                </Alert>
            )}
            <NodeInspector
                node={node}
                definition={definition}
                onConfigure={onConfigure}
            />
            {test && definition && (
                <>
                    <hr className="border-border" />
                    {definition.capabilities['waits_for_input'] ? (
                        <p className="text-sm text-muted">
                            This node waits for a person, so it cannot be tested
                            on its own; run the workflow instead.
                        </p>
                    ) : (
                        <NodeTestPanel
                            workflowId={test.workflowId}
                            nodeId={node.id}
                            definition={definition}
                            revision={test.revision}
                            saved={test.saved}
                            onSave={test.onSave}
                        />
                    )}
                </>
            )}
            <hr className="border-border" />
            <ConnectionsPanel
                node={node}
                graph={graph}
                rules={rules}
                onConnect={onConnect}
                onRemove={onRemoveEdge}
            />
            <details className="text-xs text-muted">
                <summary className="focus-ring cursor-pointer rounded">
                    Technical details
                </summary>
                <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-3 font-mono">
                    <dt>Type</dt>
                    <dd>{node.node_type}</dd>
                    {definition && (
                        <>
                            <dt>Version</dt>
                            <dd>{definition.version}</dd>
                        </>
                    )}
                    <dt>Node id</dt>
                    <dd className="break-all">{node.id}</dd>
                </dl>
            </details>
        </Dialog>
    )
}
