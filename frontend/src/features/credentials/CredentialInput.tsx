import { useQuery } from '@tanstack/react-query'
import { useId } from 'react'
import { useApiClient } from '../../api/context'
import { credentialQueries } from '../../api/queries'
import { Input } from '../../components/ui'

export const CREDENTIAL_PREFIX = 'credential:'

interface CredentialInputProps {
    label: string
    required?: boolean
    description?: string | undefined
    error?: string | undefined
    /** The stored config value; anything but a reference is hidden. */
    value: unknown
    /** `undefined` when cleared. */
    onChange: (reference: string | undefined) => void
}

/**
 * A credential is chosen by name and stored as `credential:<name>`. The
 * secret never reaches the browser: the backend has no endpoint returning
 * it, and the suggestions are credential metadata (names). A stored value
 * that is not a reference is hidden, not shown, since it may be a secret.
 */
export function CredentialInput({
    label,
    required = false,
    description,
    error,
    value,
    onChange,
}: CredentialInputProps) {
    const client = useApiClient()
    const listId = useId()
    // Admin only; for members the list fails and the name is typed freely.
    const known = useQuery({
        ...credentialQueries.list(client, { limit: 100 }),
        retry: false,
    })
    const stored = typeof value === 'string' ? value : ''
    const isReference = stored === '' || stored.startsWith(CREDENTIAL_PREFIX)
    return (
        <>
            <Input
                label={label}
                required={required}
                description={[
                    description,
                    'Enter the name of a credential; its secret is never shown here.',
                ]
                    .filter(Boolean)
                    .join(' ')}
                error={
                    error ??
                    (isReference
                        ? undefined
                        : 'The stored value is not a credential reference and is hidden. Enter a credential name to replace it.')
                }
                list={listId}
                autoComplete="off"
                spellCheck={false}
                value={
                    isReference ? stored.slice(CREDENTIAL_PREFIX.length) : ''
                }
                onChange={(e) => {
                    const name = e.target.value
                        .trim()
                        .replace(/^credential:/, '')
                    onChange(name === '' ? undefined : CREDENTIAL_PREFIX + name)
                }}
            />
            <datalist id={listId}>
                {known.data?.items.map((c) => (
                    <option key={c.name} value={c.name}>
                        {c.description || c.kind}
                    </option>
                ))}
            </datalist>
        </>
    )
}
