import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { useApiClient } from '../../api/context'
import { ApiError, errorMessage } from '../../api/errors'
import { useTestNode } from '../../api/mutations'
import type { NodeDefinition, NodePort } from '../../api/nodes'
import { executionQueries, workflowQueries } from '../../api/queries'
import type { ExecutionReport } from '../../api/reports'
import type { JsonObject } from '../../api/schemas'
import type { JsonValue } from './configSchema'
import {
    Alert,
    Badge,
    Button,
    ConfirmDialog,
    Textarea,
} from '../../components/ui'
import { formatDateTime } from '../../lib/format'
import { DataView } from '../executions/inspector/NodeData'
import { ErrorText } from '../executions/inspector/display'

// "Test node" (ADR 0010): run this node of the saved draft once with
// inputs typed here or taken from the workflow's last run. The backend runs
// it (credentials, limits, redaction); nothing is stored.

/** Text ports take plain text: nobody should have to type JSON quotes. */
const isText = (port: NodePort) => port.type === 'string' && !port.multiple

type Fields = Record<string, string>
interface Parsed {
    values: JsonObject
    problems: Record<string, string>
}

function parse(ports: readonly NodePort[], fields: Fields): Parsed {
    const values: JsonObject = {}
    const problems: Record<string, string> = {}
    for (const port of ports) {
        const text = fields[port.name] ?? ''
        // Empty: the input stays unconnected, as in a run without an edge.
        if (text === '') continue
        if (isText(port)) {
            values[port.name] = text
            continue
        }
        try {
            // JSON.parse yields JSON by definition.
            const parsed: unknown = JSON.parse(text)
            values[port.name] = parsed as JsonValue
        } catch {
            problems[port.name] = 'Not valid JSON.'
        }
    }
    return { values, problems }
}

const show = (port: NodePort, value: unknown) =>
    isText(port) && typeof value === 'string'
        ? value
        : JSON.stringify(value, null, 2)

/** This node's inputs in `report`, as field texts. */
function fromRun(
    ports: readonly NodePort[],
    nodeId: string,
    report: ExecutionReport,
) {
    const reached = report.nodes.find((n) => n.node_id === nodeId)?.inputs ?? []
    const fields: Fields = {}
    for (const port of ports) {
        const values = reached
            .filter((i) => i.port === port.name)
            .map((i) => i.value)
        if (values.length === 0) continue
        fields[port.name] = show(port, port.multiple ? values : values[0])
    }
    return fields
}

export function NodeTestPanel({
    workflowId,
    nodeId,
    definition,
    revision,
    saved,
    onSave,
}: {
    workflowId: string
    nodeId: string
    definition: NodeDefinition
    revision: number
    /** Whether the draft on the server is what the editor shows. */
    saved: boolean
    onSave: () => void
}) {
    const client = useApiClient()
    const ports = definition.inputs
    const [fields, setFields] = useState<Fields>({})
    // Entry nodes (no inputs, e.g. Input) read what the run was started with.
    const [runInput, setRunInput] = useState('{}')
    const [confirming, setConfirming] = useState(false)
    const test = useTestNode()

    const lastRun = useQuery(
        workflowQueries.executions(client, workflowId, { limit: 1 }),
    )
    const last = lastRun.data?.items[0]
    const report = useQuery({
        ...executionQueries.report(client, last?.id ?? ''),
        enabled: last !== undefined,
    })
    const ranHere = report.data?.nodes.find((n) => n.node_id === nodeId)?.inputs

    const { values, problems } = parse(ports, fields)
    let executionInput: JsonObject | null = null
    try {
        const parsed: unknown = JSON.parse(runInput)
        if (
            typeof parsed === 'object' &&
            parsed !== null &&
            !Array.isArray(parsed)
        )
            executionInput = parsed as JsonObject
    } catch {
        executionInput = null
    }
    const ready =
        saved && Object.keys(problems).length === 0 && executionInput !== null

    function run() {
        if (!executionInput) return
        test.mutate({
            workflowId,
            nodeId,
            expectedRevision: revision,
            inputs: values,
            executionInput,
        })
    }
    const sideEffects = definition.capabilities['has_side_effects'] === true
    const result = test.data
    const conflict =
        test.error instanceof ApiError && test.error.kind === 'conflict'

    return (
        <section aria-labelledby="node-test" className="flex flex-col gap-3">
            <div>
                <h3 id="node-test" className="font-semibold">
                    Test this node
                </h3>
                <p className="text-sm text-muted">
                    Runs only this node, once, with the values below. Nothing is
                    saved as a run.
                    {sideEffects &&
                        ' Its effects are real: it really writes, sends or calls.'}
                </p>
            </div>

            {ranHere && ranHere.length > 0 && last && (
                <div className="flex flex-wrap items-center gap-2 text-sm">
                    <Button
                        size="sm"
                        onClick={() => {
                            if (!report.data) return
                            setFields(fromRun(ports, nodeId, report.data))
                            setRunInput(
                                JSON.stringify(
                                    report.data.execution.input,
                                    null,
                                    2,
                                ),
                            )
                        }}
                    >
                        Use the input of the last run
                    </Button>
                    <span className="text-muted">
                        v{String(last.version)},{' '}
                        {formatDateTime(last.started_at ?? last.created_at)}
                    </span>
                </div>
            )}

            {Object.values(fields).some((f) => f.includes('[REDACTED]')) && (
                <p className="text-sm text-warning-text">
                    Some values were hidden in the run&apos;s record and read
                    [REDACTED]; the test would send exactly that text. Replace
                    them if the node needs the real value.
                </p>
            )}
            {ports.map((port) => (
                <Textarea
                    key={port.name}
                    label={port.name}
                    rows={isText(port) ? 2 : 4}
                    className={isText(port) ? undefined : 'font-mono text-xs'}
                    description={
                        isText(port)
                            ? 'Plain text. Empty: not connected.'
                            : 'As JSON, e.g. "text", 42, [1, 2] or {"name": "Ada"}. Empty: not connected.'
                    }
                    error={problems[port.name]}
                    value={fields[port.name] ?? ''}
                    onChange={(event) => {
                        setFields({
                            ...fields,
                            [port.name]: event.target.value,
                        })
                    }}
                />
            ))}
            {ports.length === 0 && (
                <Textarea
                    label="Run input"
                    rows={4}
                    className="font-mono text-xs"
                    description="What a run would be started with, as a JSON object."
                    error={
                        executionInput === null
                            ? 'Must be a JSON object.'
                            : undefined
                    }
                    value={runInput}
                    onChange={(event) => {
                        setRunInput(event.target.value)
                    }}
                />
            )}

            {!saved && (
                <Alert tone="info">
                    A test runs the saved draft. Save your changes first.{' '}
                    <Button size="sm" onClick={onSave}>
                        Save changes
                    </Button>
                </Alert>
            )}
            <div>
                <Button
                    variant="primary"
                    disabled={!ready}
                    loading={test.isPending}
                    onClick={() => {
                        if (sideEffects) setConfirming(true)
                        else run()
                    }}
                >
                    Run test
                </Button>
            </div>

            {test.error && (
                <Alert tone="danger" title="The test did not run">
                    {conflict
                        ? 'The draft was changed elsewhere. Reload the editor, then test again.'
                        : errorMessage(test.error)}
                </Alert>
            )}
            {result && (
                <section
                    aria-label="Test result"
                    className="flex flex-col gap-3 rounded-md border border-border p-3"
                >
                    <p className="flex flex-wrap items-center gap-2 text-sm">
                        <Badge
                            tone={
                                result.status === 'completed'
                                    ? 'success'
                                    : 'danger'
                            }
                            dot
                        >
                            {result.status === 'completed'
                                ? 'Succeeded'
                                : 'Failed'}
                        </Badge>
                        <span className="text-muted">
                            in {result.duration_ms.toLocaleString()} ms
                        </span>
                    </p>
                    {result.error && (
                        <ErrorText
                            error={{ ...result.error, node_id: nodeId }}
                        />
                    )}
                    {result.outputs &&
                        Object.entries(result.outputs).map(([port, value]) => (
                            <div key={port} className="flex flex-col gap-1">
                                <p className="text-xs font-semibold">{port}</p>
                                <DataView value={value} label={port} />
                            </div>
                        ))}
                    {result.logs.length > 0 && (
                        <div>
                            <p className="text-xs font-semibold">Logs</p>
                            <ul className="font-mono text-xs">
                                {result.logs.map((line, i) => (
                                    <li key={i}>
                                        <span className="text-muted">
                                            {line.level}
                                        </span>{' '}
                                        {line.message}
                                    </li>
                                ))}
                            </ul>
                        </div>
                    )}
                </section>
            )}

            <ConfirmDialog
                open={confirming}
                title="Run with real effects?"
                confirmLabel="Run test"
                onCancel={() => {
                    setConfirming(false)
                }}
                onConfirm={() => {
                    setConfirming(false)
                    run()
                }}
            >
                {definition.name} changes things outside this workflow: the test
                really writes, sends or calls, just like a run would.
            </ConfirmDialog>
        </section>
    )
}
