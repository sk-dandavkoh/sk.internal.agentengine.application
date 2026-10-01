import type { ReactNode } from 'react'

export interface FieldMessages {
    /** Help text, linked to the control via aria-describedby. */
    description?: string | undefined
    /** Validation message; marks the control aria-invalid. */
    error?: string | undefined
}

interface FieldProps extends FieldMessages {
    id: string
    label: string
    required?: boolean | undefined
    children: ReactNode
}

/** Label, help text and error around one form control. */
export function Field({
    id,
    label,
    required,
    description,
    error,
    children,
}: FieldProps) {
    return (
        <div className="flex flex-col gap-1.5">
            <label htmlFor={id} className="text-sm font-medium text-fg">
                {label}
                {required && (
                    <span aria-hidden="true" className="text-danger-text">
                        {' '}
                        *
                    </span>
                )}
            </label>
            {description && (
                <p id={`${id}-description`} className="text-sm text-muted">
                    {description}
                </p>
            )}
            {children}
            {error && (
                <p
                    id={`${id}-error`}
                    className="text-sm font-medium text-danger-text"
                >
                    {error}
                </p>
            )}
        </div>
    )
}
