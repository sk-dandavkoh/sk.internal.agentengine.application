import { useId, type SubmitEvent } from 'react'
import { ApiError, errorMessage } from '../../api/errors'
import { Alert, Button, Dialog, Input, Textarea } from '../../components/ui'
import { formText } from '../../lib/form'

export interface ProjectFormValues {
    name: string
    description: string
}

interface ProjectFormDialogProps {
    open: boolean
    onClose: () => void
    title: string
    submitLabel: string
    initial: ProjectFormValues
    /** false: the form edits the name only (e.g. duplicate). */
    withDescription?: boolean
    pending: boolean
    /** The last failed submit; null/undefined when there is none. */
    error: unknown
    onSubmit: (values: ProjectFormValues) => void
}

// The backend's limits (api/project_schemas.py), mirrored so the browser
// stops obvious mistakes early. The backend still decides: whatever it
// rejects is shown next to the field.
const NAME_MAX = 200
const DESCRIPTION_MAX = 10_000

/** Backend field errors ("body.name": "...") by form field, plus the rest. */
function submitErrors(error: unknown) {
    if (!error)
        return { form: undefined, name: undefined, description: undefined }
    const details =
        error instanceof ApiError && error.code === 'VALIDATION_ERROR'
            ? error.details
            : {}
    const field = (name: string) => {
        const message = details[`body.${name}`]
        return typeof message === 'string' ? message : undefined
    }
    const name = field('name')
    const description = field('description')
    return {
        form: (name ?? description) ? undefined : errorMessage(error),
        name,
        description,
    }
}

export function ProjectFormDialog({
    open,
    onClose,
    title,
    submitLabel,
    initial,
    withDescription = true,
    pending,
    error,
    onSubmit,
}: ProjectFormDialogProps) {
    const formId = useId()
    const errors = submitErrors(error)

    function submit(event: SubmitEvent<HTMLFormElement>) {
        event.preventDefault()
        if (pending) return
        const data = new FormData(event.currentTarget)
        onSubmit({
            name: formText(data, 'name'),
            description: formText(data, 'description'),
        })
    }

    return (
        <Dialog
            open={open}
            onClose={onClose}
            title={title}
            dismissible={!pending}
            footer={
                <>
                    <Button onClick={onClose} disabled={pending}>
                        Cancel
                    </Button>
                    <Button
                        type="submit"
                        form={formId}
                        variant="primary"
                        loading={pending}
                    >
                        {submitLabel}
                    </Button>
                </>
            }
        >
            <form id={formId} onSubmit={submit} className="flex flex-col gap-4">
                {errors.form && <Alert tone="danger">{errors.form}</Alert>}
                <Input
                    label="Name"
                    name="name"
                    required
                    maxLength={NAME_MAX}
                    defaultValue={initial.name}
                    autoComplete="off"
                    error={errors.name}
                    data-autofocus
                />
                {withDescription && (
                    <Textarea
                        label="Description"
                        name="description"
                        maxLength={DESCRIPTION_MAX}
                        defaultValue={initial.description}
                        error={errors.description}
                    />
                )}
            </form>
        </Dialog>
    )
}
