import { useId, type InputHTMLAttributes } from 'react'
import { cx } from '../../lib/cx'
import { Field, type FieldMessages } from './Field'
import { controlClasses, fieldDescribedBy } from './shared'

export interface InputProps
    extends InputHTMLAttributes<HTMLInputElement>, FieldMessages {
    label: string
}

export function Input({
    label,
    description,
    error,
    id,
    className,
    ...props
}: InputProps) {
    const generatedId = useId()
    const inputId = id ?? generatedId
    const messages = { description, error }

    return (
        <Field
            id={inputId}
            label={label}
            required={props.required}
            {...messages}
        >
            <input
                id={inputId}
                aria-invalid={error ? true : undefined}
                aria-describedby={fieldDescribedBy(inputId, messages)}
                className={cx(
                    controlClasses(Boolean(error)),
                    'h-10',
                    className,
                )}
                {...props}
            />
        </Field>
    )
}
