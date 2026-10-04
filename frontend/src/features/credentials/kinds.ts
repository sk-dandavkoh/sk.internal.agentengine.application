import type { CredentialKind } from '../../api/credentials'

export const kindLabels = {
    api_key: 'API key',
    smtp: 'SMTP',
    provider: 'LLM provider',
    generic: 'Generic',
} as const satisfies Record<CredentialKind, string>
