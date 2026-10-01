import { useId, type TextareaHTMLAttributes } from 'react'
import { cx } from '../../lib/cx'
import { Field, type FieldMessages } from './Field'
import { controlClasses, fieldDescribedBy } from './shared'

export interface TextareaProps
    extends TextareaHTMLAttributes<HTMLTextAreaElement>, FieldMessages {
    label: string
}

export function Textarea({
    label,
    description,
    error,
    id,
    className,
    ...props
}: TextareaProps) {
    const generatedId = useId()
    const textareaId = id ?? generatedId
    const messages = { description, error }

    return (
        <Field
            id={textareaId}
            label={label}
            required={props.required}
            {...messages}
        >
            <textarea
                id={textareaId}
                aria-invalid={error ? true : undefined}
                aria-describedby={fieldDescribedBy(textareaId, messages)}
                className={cx(
                    controlClasses(Boolean(error)),
                    'min-h-24 py-2',
                    className,
                )}
                {...props}
            />
        </Field>
    )
}
