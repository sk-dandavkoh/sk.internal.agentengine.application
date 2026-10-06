import { useQuery } from '@tanstack/react-query'
import { useId, useState, type SubmitEvent } from 'react'
import { useApiClient } from '../../api/context'
import { errorMessage } from '../../api/errors'
import type { Execution, PendingInput } from '../../api/executions'
import { useRespondToInput } from '../../api/mutations'
import { executionQueries } from '../../api/queries'
import { QuerySection } from '../../components/QuerySection'
import { Alert, Button, Card, useToast } from '../../components/ui'
import { formatDateTime } from '../../lib/format'
import { PropertyFields } from '../editor/ConfigForm'
import { properties, type JsonValue } from '../editor/configSchema'

/**
 * The questions a WAITING execution asks (core.human), each answerable
 * with a form generated from its response schema — the same schema-driven
 * fields as node settings. The backend validates the answer and decides
 * whether it still counts (already answered, cancelled, another branch
 * running); nothing here re-derives that.
 */
export function PendingInputs({ execution }: { execution: Execution }) {
    const waiting = execution.status === 'waiting'
    const inputs = useQuery({
        ...executionQueries.inputs(useApiClient(), execution.id),
        enabled: waiting,
    })
    // Owned here, not by the form: an answered run may finish before the
    // response arrives, which unmounts the form (and would drop its
    // per-call callbacks, i.e. the confirmation).
    const respond = useRespondToInput()
    if (!waiting) return null
    return (
        <Card
            title="Waiting for input"
            description="The run continues once these are answered."
        >
            <QuerySection query={inputs} subject="the pending inputs">
                {(data) =>
                    data.items.length === 0 ? (
                        <p className="text-sm text-muted">
                            Nothing to answer right now.
                        </p>
                    ) : (
                        <div className="flex flex-col gap-6">
                            {data.items.map((input) => (
                                <InputForm
                                    key={input.node_id}
                                    executionId={execution.id}
                                    input={input}
                                    respond={respond}
                                />
                            ))}
                        </div>
                    )
                }
            </QuerySection>
        </Card>
    )
}

function InputForm({
    executionId,
    input,
    respond,
}: {
    executionId: string
    input: PendingInput
    respond: ReturnType<typeof useRespondToInput>
}) {
    const [value, setValue] = useState<Record<string, JsonValue>>({})
    const toast = useToast()
    // One mutation serves every form: show its state on the form it is for.
    const mine = respond.variables?.nodeId === input.node_id
    const promptId = useId()
    const submit = (event: SubmitEvent<HTMLFormElement>) => {
        event.preventDefault()
        respond.mutate(
            {
                executionId,
                nodeId: input.node_id,
                response: value,
            },
            {
                onSuccess: () => {
                    toast.notify({
                        tone: 'success',
                        title: 'Answer sent',
                        description: 'The execution continues.',
                    })
                },
            },
        )
    }
    return (
        <form
            className="flex flex-col gap-4"
            aria-labelledby={promptId}
            onSubmit={submit}
        >
            {/* Text, never markup: the prompt is rendered from workflow data. */}
            <p id={promptId} className="whitespace-pre-wrap text-sm">
                {input.prompt}
            </p>
            {input.deadline !== null && (
                <p className="text-xs text-muted">
                    Answer by {formatDateTime(input.deadline)}; after that the
                    workflow continues without an answer.
                </p>
            )}
            <PropertyFields
                properties={properties(input.response_schema)}
                value={value}
                path={[]}
                issues={new Map()}
                onChange={(next) => {
                    setValue(next)
                }}
            />
            {mine && respond.error && (
                <Alert tone="danger">{errorMessage(respond.error)}</Alert>
            )}
            <div>
                <Button
                    type="submit"
                    variant="primary"
                    loading={mine && respond.isPending}
                >
                    Send answer
                </Button>
            </div>
        </form>
    )
}
