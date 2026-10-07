import { useQuery } from '@tanstack/react-query'
import { useApiClient } from '../../api/context'
import { credentialQueries } from '../../api/queries'
import { Input, Select, TextLink } from '../../components/ui'
import { useSession } from '../auth/session'

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

const KIND_LABELS: Readonly<Record<string, string>> = {
    api_key: 'API key',
    smtp: 'SMTP login',
    provider: 'AI provider',
    generic: 'secret',
}

/**
 * A credential is chosen by name and stored as `credential:<name>`. The
 * secret never reaches the browser: the list is names and kinds only
 * (`GET /credential-names`, open to every user). A stored value that is
 * not a reference is hidden, not shown, since it may be a secret.
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
    const names = useQuery({ ...credentialQueries.names(client), retry: false })
    const session = useSession().data
    const admin =
        session?.status === 'active' && session.session.user.role === 'admin'
    const stored = typeof value === 'string' ? value : ''
    const isReference = stored === '' || stored.startsWith(CREDENTIAL_PREFIX)
    const name = isReference ? stored.slice(CREDENTIAL_PREFIX.length) : ''
    const hidden = isReference
        ? undefined
        : 'The stored value is not a credential reference and is hidden. Choose a credential to replace it.'
    const pick = (next: string) => {
        onChange(next === '' ? undefined : CREDENTIAL_PREFIX + next)
    }

    // Without the list (an older backend, a network error) a name can still
    // be typed: the backend resolves it either way.
    if (names.isError)
        return (
            <Input
                label={label}
                required={required}
                description={[
                    description,
                    'Enter the name of a credential; its secret is never shown here.',
                ]
                    .filter(Boolean)
                    .join(' ')}
                error={error ?? hidden}
                autoComplete="off"
                spellCheck={false}
                value={name}
                onChange={(e) => {
                    pick(e.target.value.trim().replace(/^credential:/, ''))
                }}
            />
        )

    const items = names.data?.items ?? []
    const known = items.some((c) => c.name === name)
    const none = names.data !== undefined && items.length === 0
    return (
        <div className="flex flex-col gap-1">
            <Select
                label={label}
                required={required}
                description={[
                    description,
                    none
                        ? 'There are no credentials yet.'
                        : 'Its secret is never shown here.',
                ]
                    .filter(Boolean)
                    .join(' ')}
                error={
                    error ??
                    hidden ??
                    (name !== '' && names.data && !known
                        ? `There is no credential named "${name}".`
                        : undefined)
                }
                disabled={names.isPending}
                value={name}
                onChange={(e) => {
                    pick(e.target.value)
                }}
            >
                <option value="">
                    {names.isPending
                        ? 'Loading credentials…'
                        : required
                          ? 'Choose a credential…'
                          : 'None'}
                </option>
                {name !== '' && !known && (
                    <option value={name}>{name} (not found)</option>
                )}
                {items.map((c) => (
                    <option key={c.name} value={c.name}>
                        {c.name} ({KIND_LABELS[c.kind] ?? c.kind})
                    </option>
                ))}
            </Select>
            {none &&
                (admin ? (
                    <TextLink to="/credentials">Create a credential</TextLink>
                ) : (
                    <p className="text-sm text-muted">
                        Ask an administrator to create one.
                    </p>
                ))}
        </div>
    )
}
