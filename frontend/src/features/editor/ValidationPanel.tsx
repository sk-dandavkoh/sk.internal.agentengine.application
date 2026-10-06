import { useId } from 'react'
import { errorMessage } from '../../api/errors'
import type { ValidationIssue } from '../../api/workflows'
import { Alert, Badge, Button, ConfirmDialog } from '../../components/ui'
import type { PublishFlow } from './usePublishFlow'
import {
    issueScope,
    SCOPE_LABELS,
    publishIssues,
    type IssueScope,
} from './validation'

const SCOPES: readonly IssueScope[] = [
    'node',
    'connection',
    'trigger',
    'workflow',
]

const plural = (n: number, word: string) =>
    `${String(n)} ${word}${n === 1 ? '' : 's'}`

/**
 * The Draft → Validate → Publish workflow as one panel: what the backend
 * said about which revision, every issue it reported, and the explicit,
 * confirmed publish. Issues that point at a node or a connection jump to
 * it in the editor.
 */
export function ValidationPanel({
    publishFlow,
    revision,
    clean,
    currentVersion,
    nodeName,
    onLocate,
}: {
    publishFlow: PublishFlow
    revision: number
    clean: boolean
    /** Latest published version; 0 before the first publish. */
    currentVersion: number
    /** Title of a draft node; undefined once it was deleted. */
    nodeName: (nodeId: string) => string | undefined
    onLocate: (issue: ValidationIssue) => void
}) {
    const statusId = useId()
    const { checked, current } = publishFlow
    const next = currentVersion + 1
    const issues = checked?.result.issues ?? []

    const status = !clean
        ? 'Save the draft first: the backend validates and publishes the saved draft only.'
        : publishFlow.stale
          ? 'The draft was changed elsewhere. Reload it before validating or publishing.'
          : checked === null
            ? `Revision ${String(revision)} has not been validated yet.`
            : !current
              ? `These results are for revision ${String(checked.revision)}; the draft changed since. Validate again.`
              : checked.result.valid
                ? `The backend accepted revision ${String(revision)}. It can be published as version ${String(next)}.`
                : `The backend rejected revision ${String(revision)} with ${plural(issues.length, 'error')}. Fix them and validate again.`

    const publishFailure =
        publishFlow.publishError !== null &&
        publishIssues(publishFlow.publishError) === null
            ? errorMessage(publishFlow.publishError)
            : undefined

    return (
        <section
            aria-labelledby={`${statusId}-title`}
            className="flex flex-col gap-3 rounded-md border border-border bg-surface p-3"
        >
            <div className="flex flex-wrap items-center justify-between gap-2">
                <h2 id={`${statusId}-title`} className="font-semibold">
                    Validate and publish
                </h2>
                {checked !== null &&
                    (current ? (
                        checked.result.valid ? (
                            <Badge tone="success" dot>
                                Valid
                            </Badge>
                        ) : (
                            <Badge tone="danger" dot>
                                {plural(issues.length, 'error')}
                            </Badge>
                        )
                    ) : (
                        <Badge tone="neutral" dot>
                            Outdated
                        </Badge>
                    ))}
            </div>
            <ol className="flex flex-wrap gap-x-2 text-xs text-muted">
                <li>Draft (revision {revision})</li>
                <li aria-hidden="true">→</li>
                <li>Validate</li>
                <li aria-hidden="true">→</li>
                <li>Publish</li>
                <li aria-hidden="true">→</li>
                <li>Immutable version {next}</li>
            </ol>
            <p id={statusId} role="status" className="text-sm">
                {status}
            </p>
            <div className="flex flex-wrap gap-2">
                <Button
                    size="sm"
                    onClick={publishFlow.validate}
                    loading={publishFlow.validating}
                    disabled={!clean || publishFlow.stale}
                    aria-describedby={statusId}
                >
                    Validate
                </Button>
                <Button
                    size="sm"
                    variant="primary"
                    onClick={publishFlow.requestPublish}
                    disabled={!publishFlow.canPublish}
                    aria-describedby={statusId}
                >
                    Publish…
                </Button>
            </div>
            {publishFlow.validateError !== null && (
                <Alert tone="danger" title="Could not validate the draft">
                    {errorMessage(publishFlow.validateError)}
                </Alert>
            )}

            {issues.length > 0 && (
                <div className="flex flex-col gap-2">
                    <p className="text-sm text-muted">
                        {SCOPES.map((scope) => {
                            const n = issues.filter(
                                (i) => issueScope(i) === scope,
                            ).length
                            return n > 0
                                ? `${SCOPE_LABELS[scope]}: ${String(n)}`
                                : null
                        })
                            .filter((s) => s !== null)
                            .join(' · ')}
                    </p>
                    {SCOPES.map((scope) => {
                        const inScope = issues.filter(
                            (i) => issueScope(i) === scope,
                        )
                        if (inScope.length === 0) return null
                        return (
                            <div key={scope}>
                                <h3 className="text-sm font-semibold">
                                    {SCOPE_LABELS[scope]}
                                </h3>
                                <ul
                                    aria-label={`${SCOPE_LABELS[scope]} errors`}
                                    className="flex flex-col gap-1 text-sm"
                                >
                                    {inScope.map((issue, index) => (
                                        <li key={index}>
                                            <IssueItem
                                                issue={issue}
                                                nodeName={nodeName}
                                                onLocate={onLocate}
                                            />
                                        </li>
                                    ))}
                                </ul>
                            </div>
                        )
                    })}
                </div>
            )}

            <ConfirmDialog
                open={publishFlow.confirming}
                title={`Publish version ${String(next)}?`}
                confirmLabel={`Publish version ${String(next)}`}
                tone="primary"
                irreversible
                pending={publishFlow.publishing}
                error={publishFailure}
                onCancel={publishFlow.cancelPublish}
                onConfirm={publishFlow.publish}
            >
                The backend validates draft revision {revision} once more and
                freezes it as version {next}. Published versions are immutable:
                later edits change the draft only, and executions keep running
                the version they started with.
            </ConfirmDialog>
        </section>
    )
}

function IssueItem({
    issue,
    nodeName,
    onLocate,
}: {
    issue: ValidationIssue
    nodeName: (nodeId: string) => string | undefined
    onLocate: (issue: ValidationIssue) => void
}) {
    const node =
        issue.node_id === null
            ? null
            : (nodeName(issue.node_id) ?? `deleted node ${issue.node_id}`)
    const where = [
        node && `Node "${node}"`,
        issue.port !== null && `port "${issue.port}"`,
        issue.trigger_id !== null && `trigger "${issue.trigger_id}"`,
        issue.edge !== null &&
            issue.node_id === null &&
            `connection ${issue.edge.source_node_id}.${issue.edge.source_port} → ${issue.edge.target_node_id}.${issue.edge.target_port}`,
    ].filter(Boolean)
    const body = (
        <>
            {where.length > 0 && (
                <span className="block font-medium">{where.join(' → ')}</span>
            )}
            <span className="block">{issue.message}</span>
            <span className="block font-mono text-xs text-muted">
                {issue.code}
            </span>
        </>
    )
    const locatable =
        (issue.node_id !== null && nodeName(issue.node_id) !== undefined) ||
        issue.edge !== null
    return locatable ? (
        <button
            type="button"
            onClick={() => {
                onLocate(issue)
            }}
            className="focus-ring w-full rounded border-l-4 border-danger px-2 py-1 text-left hover:bg-surface-muted"
        >
            {body}
        </button>
    ) : (
        <div className="rounded border-l-4 border-danger px-2 py-1">{body}</div>
    )
}
