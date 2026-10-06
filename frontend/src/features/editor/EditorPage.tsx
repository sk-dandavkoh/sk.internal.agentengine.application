import { useQuery, type UseQueryResult } from '@tanstack/react-query'
import { ReactFlowProvider, useReactFlow } from '@xyflow/react'
import { Fragment, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import { useParams } from 'react-router'
import { useApiClient } from '../../api/context'
import { ApiError, errorMessage } from '../../api/errors'
import type { NodeCatalog } from '../../api/nodes'
import type {
    Edge,
    NodePosition,
    Workflow,
    WorkflowVersion,
    ValidationIssue,
} from '../../api/workflows'
import { nodeQueries, workflowQueries } from '../../api/queries'
import { formatDateTime } from '../../lib/format'
import { QuerySection } from '../../components/QuerySection'
import {
    Alert,
    Badge,
    Button,
    ConfirmDialog,
    ErrorState,
    PageHeader,
    TextLink,
    useToast,
} from '../../components/ui'
import type { ConnectionRules } from './connections'
import { ConnectionsPanel } from './ConnectionsPanel'
import { newNodeId } from './editorState'
import { GRID, GraphCanvas } from './GraphCanvas'
import { edgeKey, edgesOf, nodeTitle } from './graphView'
import { NodeInspector } from './NodeInspector'
import { NodeLibrary } from './NodeLibrary'
import { useDraftEditor } from './useDraftEditor'
import { usePublishFlow } from './usePublishFlow'
import { issueCounts } from './validation'
import { ValidationPanel } from './ValidationPanel'

const workflowPath = (id: string) => `/workflows/${encodeURIComponent(id)}`

function SaveStatus({
    dirty,
    saving,
    failed,
}: {
    dirty: boolean
    saving: boolean
    failed: boolean
}) {
    const text = saving
        ? 'Saving draft…'
        : failed
          ? 'Changes not saved'
          : dirty
            ? 'Unsaved changes'
            : 'All changes saved to draft'
    return (
        <span role="status" className="text-sm text-muted">
            {text}
        </span>
    )
}

// jsdom has no matchMedia.
const prefersReducedMotion = () =>
    typeof matchMedia === 'function' &&
    matchMedia('(prefers-reduced-motion: reduce)').matches

/** Shortcuts apply on the editor, never inside text fields (AGENTS §81). */
function isTextInput(target: EventTarget) {
    return (
        target instanceof HTMLElement &&
        (target.isContentEditable ||
            ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName))
    )
}

function DraftEditor({
    workflow,
    rules,
    catalogQuery,
    onReload,
    onPublished,
}: {
    workflow: Workflow
    rules: ConnectionRules
    catalogQuery: UseQueryResult<NodeCatalog>
    onReload: () => void
    onPublished: (version: WorkflowVersion) => void
}) {
    const editor = useDraftEditor(workflow)
    const { state, dispatch } = editor
    const clean = !editor.dirty && !editor.saving && editor.saveError === null
    const publishFlow = usePublishFlow({
        workflowId: workflow.id,
        revision: state.revision,
        clean,
        onPublished,
    })
    const { checked } = publishFlow
    const issuesByNode = useMemo(
        () => issueCounts(checked?.result.issues ?? []),
        [checked],
    )
    const { catalog } = rules
    const { setCenter, screenToFlowPosition } = useReactFlow()
    const { notify } = useToast()
    const canvasRef = useRef<HTMLElement>(null)
    const conflict =
        (editor.saveError instanceof ApiError &&
            editor.saveError.kind === 'conflict') ||
        publishFlow.stale
    const selected = new Set(state.selection)
    const inspected =
        state.selection.length === 1
            ? state.graph.nodes.find((n) => n.id === state.selection[0])
            : undefined

    const hasSelection =
        state.selection.length > 0 || state.edgeSelection.length > 0
    function deleteSelection() {
        // The focused node or edge is about to disappear; without this,
        // focus falls to <body> and the editor's shortcuts stop working.
        if (canvasRef.current?.contains(document.activeElement))
            canvasRef.current.focus()
        dispatch({
            type: 'delete',
            nodeIds: state.selection,
            edgeKeys: state.edgeSelection,
        })
    }
    function selectAll() {
        dispatch({
            type: 'select',
            nodeIds: state.graph.nodes.map((n) => n.id),
            edgeKeys: state.graph.edges.map(edgeKey),
        })
    }
    function connect(edge: Edge) {
        dispatch({ type: 'connect', edge })
    }

    function onKeyDown(event: KeyboardEvent) {
        if (isTextInput(event.target)) return
        // Keys without a modifier act only on the canvas, so Backspace on a
        // focused inspector button never deletes nodes.
        const onCanvas =
            event.target instanceof Node &&
            canvasRef.current?.contains(event.target) === true
        if (!(event.ctrlKey || event.metaKey)) {
            const plain =
                !onCanvas || event.altKey
                    ? null
                    : event.key === 'Delete' || event.key === 'Backspace'
                      ? deleteSelection
                      : event.key === 'Escape'
                        ? () => {
                              dispatch({
                                  type: 'select',
                                  nodeIds: [],
                                  edgeKeys: [],
                              })
                          }
                        : null
            if (!plain) return
            event.preventDefault()
            plain()
            return
        }
        const key = event.key.toLowerCase()
        // Selected page text is copied by the browser, not as nodes.
        if (key === 'c' && window.getSelection()?.isCollapsed === false) return
        const action =
            key === 'z' && !event.shiftKey
                ? () => {
                      dispatch({ type: 'undo' })
                  }
                : (key === 'z' && event.shiftKey) || key === 'y'
                  ? () => {
                        dispatch({ type: 'redo' })
                    }
                  : key === 'c'
                    ? () => {
                          dispatch({ type: 'copy' })
                      }
                    : key === 'v'
                      ? editor.paste
                      : key === 's'
                        ? editor.save
                        : key === 'd'
                          ? editor.duplicate
                          : key === 'a' && onCanvas
                            ? selectAll
                            : null
        if (!action) return
        event.preventDefault()
        action()
    }

    /** Adds a registered node type; anything else (a stale drop, a type a
     * plugin removed) is refused, since the draft could not use it. */
    function addNode(nodeType: string, at?: NodePosition) {
        if (!catalog?.has(nodeType)) {
            notify({
                tone: 'warning',
                title: 'Node not added',
                description: `"${nodeType}" is not a registered node type.`,
            })
            return
        }
        const rect = canvasRef.current?.getBoundingClientRect()
        const p =
            at ??
            screenToFlowPosition({
                x: (rect?.left ?? 0) + (rect?.width ?? 0) / 2,
                y: (rect?.top ?? 0) + (rect?.height ?? 0) / 2,
            })
        dispatch({
            type: 'add',
            node: {
                id: newNodeId(),
                node_type: nodeType,
                config: {},
                label: '',
                // On the canvas grid, like dragged nodes.
                position: {
                    x: Math.round(p.x / GRID) * GRID,
                    y: Math.round(p.y / GRID) * GRID,
                },
            },
        })
    }

    function focusNode(nodeId: string) {
        dispatch({ type: 'select', nodeIds: [nodeId], edgeKeys: [] })
        const position = state.graph.nodes.find(
            (n) => n.id === nodeId,
        )?.position
        if (position)
            void setCenter(position.x + 90, position.y + 40, {
                zoom: 1,
                duration: prefersReducedMotion() ? 0 : 200,
            })
    }

    /** Jumps to where a validation issue points: its node, else its edge. */
    function locate(issue: ValidationIssue) {
        if (
            issue.node_id !== null &&
            state.graph.nodes.some((n) => n.id === issue.node_id)
        ) {
            focusNode(issue.node_id)
            return
        }
        if (issue.edge)
            dispatch({
                type: 'select',
                nodeIds: [],
                edgeKeys: [edgeKey(issue.edge)],
            })
    }

    return (
        <div className="flex flex-col gap-3" onKeyDown={onKeyDown}>
            <div
                role="toolbar"
                aria-label="Editor"
                className="flex flex-wrap items-center gap-2"
            >
                <Button
                    size="sm"
                    onClick={() => {
                        dispatch({ type: 'undo' })
                    }}
                    disabled={state.past.length === 0}
                    aria-keyshortcuts="Control+Z Meta+Z"
                >
                    Undo
                </Button>
                <Button
                    size="sm"
                    onClick={() => {
                        dispatch({ type: 'redo' })
                    }}
                    disabled={state.future.length === 0}
                    aria-keyshortcuts="Control+Shift+Z Meta+Shift+Z Control+Y"
                >
                    Redo
                </Button>
                <Button
                    size="sm"
                    onClick={() => {
                        dispatch({ type: 'copy' })
                    }}
                    disabled={state.selection.length === 0}
                    aria-keyshortcuts="Control+C Meta+C"
                >
                    Copy
                </Button>
                <Button
                    size="sm"
                    onClick={editor.paste}
                    disabled={state.clipboard === null}
                    aria-keyshortcuts="Control+V Meta+V"
                >
                    Paste
                </Button>
                <Button
                    size="sm"
                    onClick={editor.duplicate}
                    disabled={state.selection.length === 0}
                    aria-keyshortcuts="Control+D Meta+D"
                >
                    Duplicate
                </Button>
                <Button
                    size="sm"
                    onClick={deleteSelection}
                    disabled={!hasSelection}
                    aria-keyshortcuts="Delete Backspace"
                >
                    Delete
                </Button>
                <Button
                    size="sm"
                    variant="primary"
                    onClick={editor.save}
                    loading={editor.saving}
                    disabled={!editor.dirty && !editor.saving}
                    aria-keyshortcuts="Control+S Meta+S"
                >
                    Save draft
                </Button>
                <SaveStatus
                    dirty={editor.dirty}
                    saving={editor.saving}
                    failed={editor.saveError !== null}
                />
            </div>

            {conflict ? (
                <Alert
                    tone="danger"
                    title="The draft was changed elsewhere"
                    action={
                        <Button size="sm" onClick={onReload}>
                            {editor.dirty
                                ? 'Discard my changes and reload'
                                : 'Reload the draft'}
                        </Button>
                    }
                >
                    {editor.dirty
                        ? 'Your changes are not saved. Saving them would overwrite the newer draft, so the editor will not do it.'
                        : 'The editor shows an older revision, so it can neither validate nor publish it. Reload to continue with the newer draft.'}
                </Alert>
            ) : editor.saveError !== null ? (
                <Alert
                    tone="danger"
                    title="Could not save the draft"
                    action={
                        <Button size="sm" onClick={editor.save}>
                            Retry
                        </Button>
                    }
                >
                    {errorMessage(editor.saveError)} Autosave is paused until a
                    save succeeds.
                </Alert>
            ) : null}

            {/* The canvas needs width: three columns only from 2xl (at lg
                it shrank to a sliver between library and panels), library
                beside canvas from xl, stacked below. DOM order stays the
                visual order, so Tab order follows the layout. */}
            <div className="flex flex-col gap-3 xl:grid xl:grid-cols-[18rem_minmax(0,1fr)] 2xl:min-h-[32rem] 2xl:grid-cols-[18rem_minmax(0,1fr)_24rem]">
                <NodeLibrary
                    query={catalogQuery}
                    onAdd={(d) => {
                        addNode(d.node_type)
                    }}
                />
                <section
                    ref={canvasRef}
                    // Focusable, so a click on the empty pane still lands
                    // keyboard shortcuts here.
                    tabIndex={-1}
                    aria-label="Graph canvas"
                    className="h-[32rem] min-w-0 overflow-hidden rounded-md border border-border bg-canvas 2xl:h-auto"
                >
                    <GraphCanvas
                        graph={state.graph}
                        selection={state.selection}
                        edgeSelection={state.edgeSelection}
                        rules={rules}
                        issueCounts={issuesByNode}
                        onMove={(positions, done) => {
                            dispatch({ type: 'move', positions, done })
                        }}
                        onSelect={(nodeIds) => {
                            dispatch({ type: 'select', nodeIds })
                        }}
                        onSelectEdges={(edgeKeys) => {
                            dispatch({ type: 'select', edgeKeys })
                        }}
                        onDropNode={addNode}
                        onConnect={connect}
                        onReconnect={(from, edge) => {
                            dispatch({ type: 'reconnect', from, edge })
                        }}
                        onRefuse={(problem) => {
                            notify({
                                tone: 'warning',
                                title: 'Not connected',
                                description: problem.message,
                            })
                        }}
                    />
                </section>
                <div className="flex flex-col gap-3 xl:col-span-2 2xl:col-span-1">
                    <ValidationPanel
                        publishFlow={publishFlow}
                        revision={state.revision}
                        clean={clean}
                        currentVersion={workflow.current_version}
                        nodeName={(id) => {
                            const node = state.graph.nodes.find(
                                (n) => n.id === id,
                            )
                            return (
                                node &&
                                nodeTitle(node, catalog?.get(node.node_type))
                            )
                        }}
                        onLocate={locate}
                    />
                    {inspected && (
                        // One key for both panels: per node, so its half-typed
                        // numbers and JSON stay its own.
                        <Fragment key={inspected.id}>
                            <NodeInspector
                                node={inspected}
                                definition={catalog?.get(inspected.node_type)}
                                onConfigure={(config, field) => {
                                    dispatch({
                                        type: 'configure',
                                        nodeId: inspected.id,
                                        config,
                                        field,
                                    })
                                }}
                            />
                            <ConnectionsPanel
                                node={inspected}
                                graph={state.graph}
                                rules={rules}
                                onConnect={connect}
                                onRemove={(edge) => {
                                    dispatch({
                                        type: 'delete',
                                        nodeIds: [],
                                        edgeKeys: [edgeKey(edge)],
                                    })
                                }}
                            />
                        </Fragment>
                    )}
                    {/* The canvas as a list: the graph without pointer or sight
                    (frontend/AGENTS §100). */}
                    <section
                        aria-labelledby="draft-nodes"
                        className="rounded-md border border-border bg-surface p-3"
                    >
                        <h2 id="draft-nodes" className="mb-2 font-semibold">
                            Nodes ({state.graph.nodes.length})
                        </h2>
                        {/* Adding, deleting, connecting or undoing changes
                            the graph off-focus: announce its new size. */}
                        <p role="status" className="sr-only">
                            {`${String(state.graph.nodes.length)} nodes, ${String(state.graph.edges.length)} connections`}
                        </p>
                        {state.graph.nodes.length === 0 ? (
                            <p className="text-sm text-muted">
                                This draft has no nodes yet.
                            </p>
                        ) : (
                            // Scrolls on its own: at full length it set the
                            // row's height, and the canvas, stretched to it,
                            // centered a large graph far below the screen.
                            <ul className="flex max-h-[32rem] flex-col gap-1 overflow-y-auto text-sm">
                                {state.graph.nodes.map((node) => {
                                    const own = edgesOf(state.graph, node.id)
                                    const incoming = own.filter(
                                        (e) => e.target_node_id === node.id,
                                    ).length
                                    const outgoing = own.filter(
                                        (e) => e.source_node_id === node.id,
                                    ).length
                                    const issues =
                                        issuesByNode.get(node.id) ?? 0
                                    return (
                                        <li key={node.id}>
                                            <button
                                                type="button"
                                                aria-pressed={selected.has(
                                                    node.id,
                                                )}
                                                onClick={() => {
                                                    focusNode(node.id)
                                                }}
                                                className="focus-ring w-full rounded px-2 py-1 text-left hover:bg-surface-muted aria-pressed:bg-surface-muted aria-pressed:font-semibold"
                                            >
                                                {nodeTitle(
                                                    node,
                                                    catalog?.get(
                                                        node.node_type,
                                                    ),
                                                )}
                                                <span className="block text-xs text-muted">
                                                    {node.node_type} ·{' '}
                                                    {incoming} in, {outgoing}{' '}
                                                    out
                                                </span>
                                                {issues > 0 && (
                                                    <span className="block text-xs font-medium text-danger-text">
                                                        {issues} validation{' '}
                                                        {issues === 1
                                                            ? 'error'
                                                            : 'errors'}
                                                    </span>
                                                )}
                                            </button>
                                        </li>
                                    )
                                })}
                            </ul>
                        )}
                    </section>
                </div>
            </div>

            <ConfirmDialog
                open={editor.blocker.state === 'blocked'}
                title="Leave with unsaved changes?"
                confirmLabel="Leave without saving"
                cancelLabel="Stay in editor"
                onCancel={() => {
                    editor.blocker.reset?.()
                }}
                onConfirm={() => {
                    editor.blocker.proceed?.()
                }}
            >
                {editor.saving
                    ? 'The draft is still being saved. Leaving now may lose the latest changes.'
                    : 'Changes to the draft since the last save will be lost.'}
            </ConfirmDialog>
        </div>
    )
}

function EditorHeader({ workflow }: { workflow: Workflow }) {
    const published = workflow.current_version
    return (
        <PageHeader
            title={`Draft editor: ${workflow.name}`}
            actions={<Badge tone="info">Draft</Badge>}
            description={
                <>
                    <span className="block">
                        You are editing the draft (revision{' '}
                        {String(workflow.revision)}). Saving changes the draft
                        only;{' '}
                        {published > 0
                            ? `published versions, the latest being v${String(published)}, stay unchanged.`
                            : 'nothing has been published yet.'}{' '}
                        Publishing is a separate step.
                    </span>
                    <TextLink to={workflowPath(workflow.id)}>
                        Back to workflow
                    </TextLink>
                </>
            }
        />
    )
}

export function EditorPage() {
    const { workflowId = '' } = useParams()
    const client = useApiClient()
    // Loaded once per editor session: background refetches must not
    // replace local edits. `session` restarts it from the server's draft.
    const query = useQuery({
        ...workflowQueries.detail(client, workflowId),
        refetchOnWindowFocus: false,
        refetchOnReconnect: false,
    })
    const nodes = useQuery(nodeQueries.catalog(client))
    // A viewer could edit locally but never save: show why instead.
    const viewOnly =
        useQuery(workflowQueries.sharing(client, workflowId)).data?.access ===
        'view'
    const [session, setSession] = useState(0)
    const [published, setPublished] = useState<WorkflowVersion | null>(null)
    const { notify } = useToast()
    const rules = useMemo(
        (): ConnectionRules => ({
            catalog:
                nodes.data &&
                new Map(nodes.data.items.map((d) => [d.node_type, d])),
            compatibility: nodes.data?.port_compatibility,
        }),
        [nodes.data],
    )

    async function reload() {
        // A failed reload keeps the editor (and the error) as it is.
        if ((await query.refetch()).isSuccess) setSession((s) => s + 1)
    }

    if (
        query.data === undefined &&
        query.error instanceof ApiError &&
        query.error.kind === 'not_found'
    )
        return (
            <ErrorState
                title="Workflow not found"
                titleAs="h1"
                description="It does not exist, was deleted, or you do not have access to it."
                action={
                    <TextLink to="/workflows">Back to all workflows</TextLink>
                }
            />
        )
    return (
        <>
            {query.data === undefined && <PageHeader title="Draft editor" />}
            <QuerySection query={query} subject="the workflow draft">
                {(workflow) => (
                    <>
                        <EditorHeader workflow={workflow} />
                        {viewOnly && (
                            <Alert
                                tone="info"
                                title="View-only access"
                                className="mb-3"
                            >
                                This workflow is shared with you as a viewer:
                                you can look at it, but not change its draft.
                                Ask its owner for editor access, or{' '}
                                <TextLink
                                    to={`${workflowPath(workflow.id)}/versions`}
                                >
                                    browse its published versions
                                </TextLink>
                                .
                            </Alert>
                        )}
                        {nodes.isError && nodes.data === undefined && (
                            <Alert tone="warning" className="mb-3">
                                The node catalog could not be loaded, so ports
                                without connections are not shown, and no nodes
                                can be added or connected.{' '}
                                {errorMessage(nodes.error)}
                            </Alert>
                        )}
                        {published && (
                            <Alert
                                tone="success"
                                title={`Version ${String(published.version)} is published`}
                                className="mb-3"
                            >
                                Published{' '}
                                <time dateTime={published.published_at}>
                                    {formatDateTime(published.published_at)}
                                </time>
                                . This version is immutable; you keep editing
                                the draft, and further changes need another
                                publish.{' '}
                                <TextLink to={workflowPath(workflow.id)}>
                                    View workflow
                                </TextLink>
                            </Alert>
                        )}
                        {!viewOnly && (
                            <ReactFlowProvider key={session}>
                                <DraftEditor
                                    workflow={workflow}
                                    rules={rules}
                                    catalogQuery={nodes}
                                    onReload={() => void reload()}
                                    onPublished={(version) => {
                                        setPublished(version)
                                        notify({
                                            tone: 'success',
                                            title: `Version ${String(version.version)} published`,
                                        })
                                        // Publishing advances the draft revision:
                                        // continue from the server's draft.
                                        void reload()
                                    }}
                                />
                            </ReactFlowProvider>
                        )}
                    </>
                )}
            </QuerySection>
        </>
    )
}
