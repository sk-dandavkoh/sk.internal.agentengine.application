import { useState } from 'react'
import { useNavigate } from 'react-router'
import { errorMessage } from '../../api/errors'
import { useExecuteProjectVersion } from '../../api/mutations'
import { Alert, Button, Dialog, Textarea, useToast } from '../../components/ui'
import { executionPath, parseInput } from './format'

/** Starts a manual run of one published version and opens it. */
export function RunVersion({
    projectId,
    version,
}: {
    projectId: string
    version: number
}) {
    const [open, setOpen] = useState(false)
    const [text, setText] = useState('{}')
    const [inputError, setInputError] = useState<string>()
    const run = useExecuteProjectVersion()
    const navigate = useNavigate()
    const toast = useToast()
    const close = () => {
        run.reset()
        setInputError(undefined)
        setOpen(false)
    }
    const submit = () => {
        const input = parseInput(text)
        if (typeof input === 'string') {
            setInputError(input)
            return
        }
        setInputError(undefined)
        run.mutate(
            { projectId, version, input },
            {
                onSuccess: (execution) => {
                    close()
                    toast.notify({
                        tone: 'success',
                        title: `Execution of v${String(version)} started`,
                    })
                    void navigate(executionPath(execution.id))
                },
            },
        )
    }
    return (
        <>
            <Button
                variant="primary"
                onClick={() => {
                    setOpen(true)
                }}
            >
                Run…
            </Button>
            <Dialog
                open={open}
                onClose={close}
                dismissible={!run.isPending}
                title={`Run v${String(version)}`}
                description="Starts a manual execution pinned to this published version."
                footer={
                    <>
                        <Button onClick={close} disabled={run.isPending}>
                            Cancel
                        </Button>
                        <Button
                            variant="primary"
                            loading={run.isPending}
                            onClick={submit}
                        >
                            Start execution
                        </Button>
                    </>
                }
            >
                <form
                    className="flex flex-col gap-3"
                    onSubmit={(event) => {
                        event.preventDefault()
                        submit()
                    }}
                >
                    <Textarea
                        label="Input (JSON object)"
                        description="Passed to the run as its execution input."
                        className="font-mono"
                        rows={6}
                        value={text}
                        error={inputError}
                        onChange={(event) => {
                            setText(event.target.value)
                        }}
                    />
                    {run.error && (
                        <Alert
                            tone="danger"
                            title="Could not start the execution"
                        >
                            {errorMessage(run.error)}
                        </Alert>
                    )}
                </form>
            </Dialog>
        </>
    )
}
