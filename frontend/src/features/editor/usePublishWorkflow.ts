import { useState } from 'react'
import { usePublishProject, useValidateProject } from '../../api/mutations'
import type { ProjectVersion, ValidationResult } from '../../api/projects'
import { isConflict, publishIssues } from './validation'

/**
 * Draft → Validate → Publish against the backend, which decides both
 * (frontend/AGENTS §34). Both requests are pinned to the draft revision
 * the editor shows, so a result never describes, and a publish never
 * freezes, a draft the user has not seen; a mismatch sets `stale`.
 *
 * `clean`: everything in the editor is saved. Only the saved draft can be
 * validated or published, so a dirty editor makes any result outdated.
 */
export function usePublishWorkflow({
    projectId,
    revision,
    clean,
    onPublished,
}: {
    projectId: string
    revision: number
    clean: boolean
    onPublished: (version: ProjectVersion) => void
}) {
    const validation = useValidateProject()
    const publication = usePublishProject()
    const [checked, setChecked] = useState<{
        revision: number
        result: ValidationResult
    } | null>(null)
    const [stale, setStale] = useState(false)
    const [confirming, setConfirming] = useState(false)

    const current = checked !== null && checked.revision === revision && clean
    const canPublish = current && checked.result.valid && !stale

    function validate() {
        if (!clean) return
        validation.mutate(
            { projectId, expectedRevision: revision },
            {
                onSuccess: (result) => {
                    setChecked({ revision, result })
                },
                onError: (error) => {
                    if (isConflict(error)) setStale(true)
                },
            },
        )
    }

    function requestPublish() {
        if (!canPublish) return
        publication.reset()
        setConfirming(true)
    }

    function publish() {
        if (!canPublish || publication.isPending) return
        publication.mutate(
            { projectId, expectedRevision: revision },
            {
                onSuccess: (version) => {
                    setConfirming(false)
                    onPublished(version)
                },
                onError: (error) => {
                    if (isConflict(error)) {
                        setConfirming(false)
                        setStale(true)
                        return
                    }
                    // The backend refused the draft after all (e.g. the node
                    // catalog changed): its issues replace the last result.
                    const issues = publishIssues(error)
                    if (issues) {
                        setConfirming(false)
                        setChecked({
                            revision,
                            result: { valid: false, issues },
                        })
                    }
                    // Anything else stays in the open dialog for a retry.
                },
            },
        )
    }

    return {
        /** The last backend result, possibly for an older revision. */
        checked,
        /** `checked` describes exactly the draft in the editor. */
        current,
        canPublish,
        stale,
        validating: validation.isPending,
        validateError: isConflict(validation.error) ? null : validation.error,
        validate,
        confirming,
        requestPublish,
        cancelPublish: () => {
            if (!publication.isPending) setConfirming(false)
        },
        publish,
        publishing: publication.isPending,
        publishError: publication.error,
    }
}

export type PublishWorkflow = ReturnType<typeof usePublishWorkflow>
