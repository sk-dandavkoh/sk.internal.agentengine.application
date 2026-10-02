import { useQuery, type UseQueryResult } from '@tanstack/react-query'
import { ReactFlowProvider, useReactFlow } from '@xyflow/react'
import { useMemo, useRef, useState, type KeyboardEvent } from 'react'
import { useParams } from 'react-router'
import { useApiClient } from '../../api/context'
import { ApiError, errorMessage } from '../../api/errors'
import type { NodeDefinition } from '../../api/nodes'
import type { NodePosition, Project } from '../../api/projects'
import { nodeQueries, projectQueries } from '../../api/queries'
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
import { newNodeId } from './editorState'
import { GRID, GraphCanvas } from './GraphCanvas'
import { nodeTitle } from './graphView'
import { NodeLibrary } from './NodeLibrary'
import { useDraftEditor } from './useDraftEditor'

const projectPath = (id: string) => `/projects/${encodeURIComponent(id)}`

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

/** Shortcuts apply on the editor, never inside text fields (AGENTS §81). */
function isTextInput(target: EventTarget) {
    return (
        target instanceof HTMLElement &&
        (target.isContentEditable ||
            ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName))
    )
}

function DraftEditor({
    project,
    catalog,
    catalogQuery,
    onReload,
}: {
    project: Project
    catalog: ReadonlyMap<string, NodeDefinition> | undefined
    catalogQuery: UseQueryResult<{ items: NodeDefinition[] }>
    onReload: () => void
}) {
    const editor = useDraftEditor(project)
    const { state, dispatch } = editor
    const { setCenter, screenToFlowPosition } = useReactFlow()
    const { notify } = useToast()
    const canvasRef = useRef<HTMLElement>(null)
    const conflict =
        editor.saveError instanceof ApiError &&
        editor.saveError.kind === 'conflict'
    const selected = new Set(state.selection)

    function onKeyDown(event: KeyboardEvent) {
        if (!(event.ctrlKey || event.metaKey) || isTextInput(event.target))
            return
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
        dispatch({ type: 'select', nodeIds: [nodeId] })
        const position = state.graph.nodes.find(
            (n) => n.id === nodeId,
        )?.position
        if (position)
            void setCenter(position.x + 90, position.y + 40, {
                zoom: 1,
                duration: 200,
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
                            Discard my changes and reload
                        </Button>
                    }
                >
                    Your changes are not saved. Saving them would overwrite the
                    newer draft, so the editor will not do it.
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

            <div className="flex min-h-[32rem] flex-col gap-3 lg:flex-row">
                <NodeLibrary
                    query={catalogQuery}
                    onAdd={(d) => {
                        addNode(d.node_type)
                    }}
                />
                <section
                    ref={canvasRef}
                    aria-label="Graph canvas"
                    className="h-[32rem] min-w-0 flex-1 overflow-hidden rounded-md border border-border bg-canvas lg:h-auto"
                >
                    <GraphCanvas
                        graph={state.graph}
                        selection={state.selection}
                        catalog={catalog}
                        onMove={(positions, done) => {
                            dispatch({ type: 'move', positions, done })
                        }}
                        onSelect={(nodeIds) => {
                            dispatch({ type: 'select', nodeIds })
                        }}
                        onDropNode={addNode}
                    />
                </section>
                {/* The canvas as a list: the graph without pointer or sight
                    (frontend/AGENTS §100). */}
                <section
                    aria-labelledby="draft-nodes"
                    className="rounded-md border border-border bg-surface p-3 lg:w-72"
                >
                    <h2 id="draft-nodes" className="mb-2 font-semibold">
                        Nodes ({state.graph.nodes.length})
                    </h2>
                    {state.graph.nodes.length === 0 ? (
                        <p className="text-sm text-muted">
                            This draft has no nodes yet.
                        </p>
                    ) : (
                        <ul className="flex flex-col gap-1 text-sm">
                            {state.graph.nodes.map((node) => {
                                const incoming = state.graph.edges.filter(
                                    (e) => e.target_node_id === node.id,
                                ).length
                                const outgoing = state.graph.edges.filter(
                                    (e) => e.source_node_id === node.id,
                                ).length
                                return (
                                    <li key={node.id}>
                                        <button
                                            type="button"
                                            aria-pressed={selected.has(node.id)}
                                            onClick={() => {
                                                focusNode(node.id)
                                            }}
                                            className="focus-ring w-full rounded px-2 py-1 text-left hover:bg-surface-muted aria-pressed:bg-surface-muted aria-pressed:font-semibold"
                                        >
                                            {nodeTitle(
                                                node,
                                                catalog?.get(node.node_type),
                                            )}
                                            <span className="block text-xs text-muted">
                                                {node.node_type} · {incoming}{' '}
                                                in, {outgoing} out
                                            </span>
                                        </button>
                                    </li>
                                )
                            })}
                        </ul>
                    )}
                </section>
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

function EditorHeader({ project }: { project: Project }) {
    const published = project.current_version
    return (
        <PageHeader
            title={`Draft editor: ${project.name}`}
            actions={<Badge tone="info">Draft</Badge>}
            description={
                <>
                    <span className="block">
                        You are editing the draft (revision{' '}
                        {String(project.revision)}). Saving changes the draft
                        only;{' '}
                        {published > 0
                            ? `published versions, the latest being v${String(published)}, stay unchanged.`
                            : 'nothing has been published yet.'}{' '}
                        Publishing is a separate step.
                    </span>
                    <TextLink to={projectPath(project.id)}>
                        Back to project
                    </TextLink>
                </>
            }
        />
    )
}

export function EditorPage() {
    const { projectId = '' } = useParams()
    const client = useApiClient()
    // Loaded once per editor session: background refetches must not
    // replace local edits. `session` restarts it from the server's draft.
    const query = useQuery({
        ...projectQueries.detail(client, projectId),
        refetchOnWindowFocus: false,
        refetchOnReconnect: false,
    })
    const nodes = useQuery(nodeQueries.catalog(client))
    const [session, setSession] = useState(0)
    const catalog = useMemo(
        () =>
            nodes.data &&
            new Map(nodes.data.items.map((d) => [d.node_type, d])),
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
                title="Project not found"
                titleAs="h1"
                description="It does not exist, was deleted, or you do not have access to it."
                action={
                    <TextLink to="/projects">Back to all projects</TextLink>
                }
            />
        )
    return (
        <>
            {query.data === undefined && <PageHeader title="Draft editor" />}
            <QuerySection query={query} subject="the project draft">
                {(project) => (
                    <>
                        <EditorHeader project={project} />
                        {nodes.isError && nodes.data === undefined && (
                            <Alert tone="warning" className="mb-3">
                                The node catalog could not be loaded, so ports
                                without connections are not shown and no nodes
                                can be added. {errorMessage(nodes.error)}
                            </Alert>
                        )}
                        <ReactFlowProvider key={session}>
                            <DraftEditor
                                project={project}
                                catalog={catalog}
                                catalogQuery={nodes}
                                onReload={() => void reload()}
                            />
                        </ReactFlowProvider>
                    </>
                )}
            </QuerySection>
        </>
    )
}
