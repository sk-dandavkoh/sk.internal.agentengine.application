import { useId, type SelectHTMLAttributes } from 'react'
import { cx } from '../../lib/cx'
import { Field, type FieldMessages } from './Field'
import { controlClasses, fieldDescribedBy } from './shared'

export interface SelectProps
    extends SelectHTMLAttributes<HTMLSelectElement>, FieldMessages {
    label: string
}

export function Select({
    label,
    description,
    error,
    id,
    className,
    children,
    ...props
}: SelectProps) {
    const generatedId = useId()
    const selectId = id ?? generatedId
    const messages = { description, error }

    return (
        <Field
            id={selectId}
            label={label}
            required={props.required}
            {...messages}
        >
            <select
                id={selectId}
                aria-invalid={error ? true : undefined}
                aria-describedby={fieldDescribedBy(selectId, messages)}
                className={cx(
                    controlClasses(Boolean(error)),
                    'h-10',
                    className,
                )}
                {...props}
            >
                {children}
            </select>
        </Field>
    )
}
