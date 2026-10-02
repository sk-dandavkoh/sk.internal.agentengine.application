import { useEffect, useEffectEvent, useReducer, useRef, useState } from 'react'
import { useBlocker } from 'react-router'
import { useUpdateProject } from '../../api/mutations'
import type { Project } from '../../api/projects'
import {
    editorReducer,
    initEditor,
    isDirty,
    newNodeId,
    type EditorAction,
} from './editorState'

export const AUTOSAVE_DELAY_MS = 1500

/**
 * Editor state of `project`'s draft plus its persistence: explicit save,
 * debounced autosave and the unsaved-changes guard.
 *
 * Saves are single-flight and pinned to the draft revision the editor last
 * saw, so a draft changed elsewhere fails with a conflict instead of being
 * overwritten (frontend/AGENTS §94). Saving never publishes: it writes the
 * mutable draft only (architecture §123).
 *
 * A failed save stops autosave until the user saves explicitly; a broken
 * backend must not get a request every few seconds (frontend/AGENTS §31).
 */
export function useDraftEditor(project: Project) {
    const [state, dispatch] = useReducer(editorReducer, project, (p) =>
        initEditor(p.draft.graph, p.revision),
    )
    const update = useUpdateProject()
    const inFlight = useRef(false)
    const [saveError, setSaveError] = useState<unknown>(null)
    const dirty = isDirty(state)
    const saving = update.isPending

    function save() {
        if (inFlight.current || state.dragOrigin || !isDirty(state)) return
        inFlight.current = true
        const graph = state.graph
        setSaveError(null)
        update.mutate(
            {
                projectId: project.id,
                input: {
                    // Only the graph is edited here; the rest of the draft
                    // goes back as loaded (the revision guards against it
                    // having changed meanwhile).
                    name: project.name,
                    description: project.description,
                    draft: { ...project.draft, graph },
                    expected_revision: state.revision,
                },
            },
            {
                onSuccess: (saved) => {
                    dispatch({ type: 'saved', graph, revision: saved.revision })
                },
                onError: setSaveError,
                onSettled: () => {
                    inFlight.current = false
                },
            },
        )
    }

    const autosave = useEffectEvent(save)
    const autosaveBlocked = saveError !== null
    useEffect(() => {
        if (!dirty || saving || autosaveBlocked || state.dragOrigin) return
        const timer = setTimeout(autosave, AUTOSAVE_DELAY_MS)
        return () => {
            clearTimeout(timer)
        }
    }, [state.graph, dirty, saving, autosaveBlocked, state.dragOrigin])

    const unsafeToLeave = dirty || saving
    const blocker = useBlocker(
        ({ currentLocation, nextLocation }) =>
            unsafeToLeave && currentLocation.pathname !== nextLocation.pathname,
    )
    useEffect(() => {
        if (!unsafeToLeave) return
        const warn = (event: BeforeUnloadEvent) => {
            event.preventDefault()
        }
        window.addEventListener('beforeunload', warn)
        return () => {
            window.removeEventListener('beforeunload', warn)
        }
    }, [unsafeToLeave])

    // Ids are made here so the reducer stays pure.
    const freshIds = (ids: readonly string[]) =>
        Object.fromEntries(ids.map((id) => [id, newNodeId()]))

    function paste() {
        if (!state.clipboard) return
        dispatch({
            type: 'paste',
            ids: freshIds(state.clipboard.nodes.map((n) => n.id)),
        })
    }

    function duplicate() {
        dispatch({ type: 'duplicate', ids: freshIds(state.selection) })
    }

    return {
        state,
        dispatch: dispatch as (action: EditorAction) => void,
        dirty,
        saving,
        saveError,
        save,
        paste,
        duplicate,
        blocker,
    }
}
