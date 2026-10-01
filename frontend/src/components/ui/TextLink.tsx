import { Link, type LinkProps } from 'react-router'
import { cx } from '../../lib/cx'

/** In-app text link. Navigation is a link, never a button. */
export function TextLink({ className, ...props }: LinkProps) {
    return (
        <Link
            className={cx(
                'focus-ring rounded-sm font-medium text-primary underline underline-offset-2 hover:text-primary-hover',
                className,
            )}
            {...props}
        />
    )
}
