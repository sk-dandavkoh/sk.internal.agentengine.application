import { useState } from 'react'
import { errorMessage } from '../../api/errors'
import type { Execution } from '../../api/executions'
import { useCancelExecution } from '../../api/mutations'
import { Button, ConfirmDialog, useToast } from '../../components/ui'

/**
 * Offered only while the backend says the run is cancellable. Cancellation
 * is cooperative: a run owned by a live worker is still RUNNING in the
 * response and becomes CANCELLED at the worker's next check.
 */
export function CancelExecution({
    execution,
    size = 'md',
}: {
    execution: Execution
    size?: 'sm' | 'md'
}) {
    const [open, setOpen] = useState(false)
    const cancel = useCancelExecution()
    const toast = useToast()
    if (!execution.cancellable) return null
    const close = () => {
        cancel.reset()
        setOpen(false)
    }
    return (
        <>
            <Button
                size={size}
                variant="danger"
                aria-label={`Cancel execution ${execution.id}`}
                onClick={() => {
                    setOpen(true)
                }}
            >
                Cancel
            </Button>
            <ConfirmDialog
                open={open}
                onCancel={close}
                title="Cancel this execution?"
                confirmLabel="Cancel execution"
                cancelLabel="Keep running"
                pending={cancel.isPending}
                error={cancel.error ? errorMessage(cancel.error) : undefined}
                onConfirm={() => {
                    cancel.mutate(
                        { executionId: execution.id },
                        {
                            onSuccess: (result) => {
                                close()
                                toast.notify(
                                    result.status === 'cancelled'
                                        ? {
                                              tone: 'success',
                                              title: 'Execution cancelled',
                                          }
                                        : {
                                              tone: 'info',
                                              title: 'Cancellation requested',
                                              description:
                                                  'The worker stops the run at its next check; the status updates then.',
                                          },
                                )
                            },
                        },
                    )
                }}
            >
                Execution{' '}
                <code className="font-mono text-xs">{execution.id}</code> stops;
                nodes still running are cancelled. A cancelled run is never
                resumed.
            </ConfirmDialog>
        </>
    )
}
