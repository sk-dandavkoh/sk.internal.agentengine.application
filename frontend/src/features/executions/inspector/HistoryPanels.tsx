import { useState } from 'react'
import type { LogLevel } from '../../../api/executions'
import type { ExecutionReport } from '../../../api/reports'
import { Alert, Button, Card, Input, Select } from '../../../components/ui'
import { describeEvent } from '../useLiveExecution'
import { Time } from '../shared'
import { ErrorText } from './display'
import {
    EVENT_STEP,
    MAX_SEARCH_CHARS,
    nodeNamer,
    nodeStatusDisplay,
} from './format'
import { LevelFilter, LogList } from './LogList'

export function LogsPanel({
    executionId,
    report,
}: {
    executionId: string
    report: ExecutionReport
}) {
    const [level, setLevel] = useState<LogLevel>()
    const [nodeId, setNodeId] = useState('')
    // Applied on submit, not per keystroke: one request per search.
    const [draft, setDraft] = useState('')
    const [search, setSearch] = useState('')
    const name = nodeNamer(report)
    return (
        <Card
            title="Logs"
            description="What the nodes wrote to their logger, per attempt. Redacted and limited by the backend."
        >
            <div className="flex flex-col gap-4">
                <div className="flex flex-wrap gap-4">
                    <LevelFilter value={level} onChange={setLevel} />
                    <Select
                        label="Node"
                        value={nodeId}
                        onChange={(e) => {
                            setNodeId(e.target.value)
                        }}
                    >
                        <option value="">All nodes</option>
                        {report.nodes.map((n) => (
                            <option key={n.node_id} value={n.node_id}>
                                {name(n.node_id)}
                            </option>
                        ))}
                    </Select>
                    <form
                        role="search"
                        className="flex items-end gap-2"
                        onSubmit={(e) => {
                            e.preventDefault()
                            setSearch(draft.trim())
                        }}
                    >
                        <Input
                            label="Message contains"
                            type="search"
                            maxLength={MAX_SEARCH_CHARS}
                            value={draft}
                            onChange={(e) => {
                                setDraft(e.target.value)
                                // Clearing the box (or its × button) shows
                                // everything again without a submit.
                                if (e.target.value === '') setSearch('')
                            }}
                        />
                        <Button type="submit">Search</Button>
                    </form>
                </div>
                <LogList
                    executionId={executionId}
                    filter={{
                        ...(nodeId && { node_id: nodeId }),
                        ...(level && { level }),
                        ...(search && { q: search }),
                    }}
                    nodeName={name}
                />
            </div>
        </Card>
    )
}

/** Every recorded failure: the execution's and each node's attempts. */
export function ErrorsPanel({
    report,
    onInspect,
}: {
    report: ExecutionReport
    onInspect: (nodeId: string) => void
}) {
    const failing = report.nodes.filter((n) => n.failures.length > 0)
    const error = report.execution.error
    if (!error && failing.length === 0)
        return (
            <Card title="Errors">
                <p className="text-sm text-muted">
                    No errors were recorded for this execution.
                </p>
            </Card>
        )
    return (
        <Card title="Errors">
            <div className="flex flex-col gap-6 text-sm">
                {error && (
                    <section aria-label="Execution error">
                        <h3 className="mb-2 font-semibold">Execution</h3>
                        <ErrorText error={error} />
                    </section>
                )}
                {failing.map((node) => {
                    const title = node.label || node.node_id
                    return (
                        <section
                            key={node.node_id}
                            aria-label={`Errors of node ${title}`}
                        >
                            <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                                <h3 className="font-semibold">
                                    Node {title}{' '}
                                    <span className="font-normal text-muted">
                                        ({node.node_type},{' '}
                                        {String(node.failures.length)} of{' '}
                                        {String(node.attempts)} attempts failed;
                                        node{' '}
                                        {nodeStatusDisplay[
                                            node.status
                                        ].label.toLowerCase()}
                                        )
                                    </span>
                                </h3>
                                <Button
                                    size="sm"
                                    variant="ghost"
                                    aria-label={`Inspect node ${title}`}
                                    onClick={() => {
                                        onInspect(node.node_id)
                                    }}
                                >
                                    Inspect
                                </Button>
                            </div>
                            <ol className="flex flex-col gap-3">
                                {node.failures.map((failure) => (
                                    <li key={failure.attempt}>
                                        <p className="text-xs text-muted">
                                            Attempt {String(failure.attempt)}
                                        </p>
                                        <ErrorText error={failure} />
                                    </li>
                                ))}
                            </ol>
                        </section>
                    )
                })}
            </div>
        </Card>
    )
}

/** The persisted event timeline of the report, oldest first. */
export function EventsPanel({ report }: { report: ExecutionReport }) {
    const [shown, setShown] = useState(EVENT_STEP)
    const name = nodeNamer(report)
    const events = report.events
    return (
        <Card
            title="Events"
            description="The lifecycle history the backend recorded."
        >
            <div className="flex flex-col gap-3 text-sm">
                {report.events_truncated && (
                    <Alert tone="info">
                        This execution has more events than the report holds;
                        the earliest ones are shown.
                    </Alert>
                )}
                {events.length === 0 ? (
                    <p className="text-muted">No events were recorded.</p>
                ) : (
                    <ol
                        aria-label="Event timeline"
                        className="flex flex-col gap-1"
                    >
                        {events.slice(0, shown).map((e, i) => (
                            <li key={e.position ?? i} className="flex gap-3">
                                <span className="text-muted">
                                    <Time iso={e.occurred_at} />
                                </span>
                                <span>{describeEvent(e, name)}</span>
                            </li>
                        ))}
                    </ol>
                )}
                {events.length > shown && (
                    <Button
                        size="sm"
                        className="self-start"
                        onClick={() => {
                            setShown(shown + EVENT_STEP)
                        }}
                    >
                        Show more events ({String(events.length - shown)} more)
                    </Button>
                )}
            </div>
        </Card>
    )
}
