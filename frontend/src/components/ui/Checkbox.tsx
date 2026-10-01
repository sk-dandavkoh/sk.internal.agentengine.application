import { useId, type InputHTMLAttributes } from 'react'
import { cx } from '../../lib/cx'

export interface CheckboxProps extends Omit<
    InputHTMLAttributes<HTMLInputElement>,
    'type'
> {
    label: string
    description?: string | undefined
    /** Renders as an on/off switch (role="switch"); same native input. */
    switch?: boolean
}

const box =
    'size-4 shrink-0 rounded border-border-strong accent-primary disabled:cursor-not-allowed'

// Thumb drawn with ::before on an appearance-none input, so state is shown by
// position as well as color.
const track =
    'relative h-5 w-9 shrink-0 cursor-pointer appearance-none rounded-full bg-border-strong transition-colors checked:bg-primary disabled:cursor-not-allowed before:absolute before:top-0.5 before:left-0.5 before:size-4 before:rounded-full before:bg-white before:shadow before:transition-transform checked:before:translate-x-4 motion-reduce:before:transition-none'

export function Checkbox({
    label,
    description,
    switch: isSwitch = false,
    id,
    className,
    ...props
}: CheckboxProps) {
    const generatedId = useId()
    const inputId = id ?? generatedId
    const descriptionId = `${inputId}-description`

    return (
        <div className={cx('flex items-start gap-2.5', className)}>
            <input
                type="checkbox"
                role={isSwitch ? 'switch' : undefined}
                id={inputId}
                aria-describedby={description ? descriptionId : undefined}
                className={cx('focus-ring mt-0.5', isSwitch ? track : box)}
                {...props}
            />
            <div className="flex flex-col gap-0.5">
                <label
                    htmlFor={inputId}
                    className={cx(
                        'text-sm font-medium text-fg',
                        props.disabled && 'opacity-70',
                    )}
                >
                    {label}
                </label>
                {description && (
                    <p id={descriptionId} className="text-sm text-muted">
                        {description}
                    </p>
                )}
            </div>
        </div>
    )
}

export function Switch(props: Omit<CheckboxProps, 'switch'>) {
    return <Checkbox {...props} switch />
}
