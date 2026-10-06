import { fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import {
    apiError,
    ok,
    renderRoute,
    stubBackend,
    type Handler,
} from '../../test/app'

const status = { version: '0.27.0', ready: true, failures: {} }

afterEach(() => {
    localStorage.clear()
    delete document.documentElement.dataset['theme']
})

async function renderSettings(handlers: Record<string, Handler> = {}) {
    const bodies: unknown[] = []
    const requests = stubBackend({
        'GET /system/status': () => ok(status),
        'GET /auth/tokens': () => ok({ items: [] }),
        'POST /auth/password': (_url, init) => {
            bodies.push(JSON.parse(init.body as string))
            return { status: 204 }
        },
        ...handlers,
    })
    const rendered = renderRoute('/settings')
    await screen.findByRole('heading', { level: 1, name: 'Settings' })
    return { ...rendered, bodies, requests }
}

function fillPassword(current: string, next: string, confirm = next) {
    fireEvent.change(screen.getByLabelText(/^Current password/), {
        target: { value: current },
    })
    fireEvent.change(screen.getByLabelText(/^New password/), {
        target: { value: next },
    })
    fireEvent.change(screen.getByLabelText(/^Confirm new password/), {
        target: { value: confirm },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Change password' }))
}

describe('Settings', () => {
    it('shows the account, its session and the backend status', async () => {
        await renderSettings()

        expect(screen.getByText('Username').nextSibling).toHaveTextContent(
            'ada',
        )
        expect(screen.getByText('Role').nextSibling).toHaveTextContent(
            'Administrator',
        )
        expect(screen.getByText('Session expires')).toBeInTheDocument()
        expect(await screen.findByText('0.27.0')).toBeInTheDocument()
        expect(screen.getByText('Ready')).toBeInTheDocument()
    })

    it('changes the password and clears the form', async () => {
        const { bodies } = await renderSettings()

        fillPassword('old passphrase!', 'a much better passphrase')

        expect(await screen.findByText('Password changed')).toBeInTheDocument()
        expect(bodies).toEqual([
            {
                current_password: 'old passphrase!',
                new_password: 'a much better passphrase',
            },
        ])
        expect(screen.getByLabelText(/^Current password/)).toHaveValue('')
    })

    it('marks a wrong current password without ending the session', async () => {
        const { router } = await renderSettings({
            'POST /auth/password': () =>
                apiError(
                    401,
                    'INVALID_CREDENTIALS',
                    'Invalid username or password.',
                ),
        })

        fillPassword('not my password', 'a much better passphrase')

        await waitFor(() => {
            expect(
                screen.getByLabelText(/^Current password/),
            ).toHaveAccessibleDescription('Invalid username or password.')
        })
        // A 401 for a wrong password is not a dead session.
        expect(router.state.location.pathname).toBe('/settings')
    })

    it('shows the backend validation message for a weak password', async () => {
        await renderSettings({
            'POST /auth/password': () =>
                apiError(
                    422,
                    'DOMAIN_VALIDATION_ERROR',
                    'Password must be at least 12 characters.',
                ),
        })
        fillPassword('old passphrase!', 'short but ok?')
        expect(await screen.findByRole('alert')).toHaveTextContent(
            'Password must be at least 12 characters.',
        )
    })

    it('does not send mismatched new passwords', async () => {
        const { bodies } = await renderSettings()

        fillPassword('old passphrase!', 'a much better passphrase', 'typo')

        expect(
            screen.getByLabelText(/^Confirm new password/),
        ).toHaveAccessibleDescription('The passwords do not match.')
        expect(bodies).toEqual([])
    })

    it('creates an access token and shows its value only once', async () => {
        const sent: unknown[] = []
        let items: object[] = []
        await renderSettings({
            'GET /auth/tokens': () => ok({ items }),
            'POST /auth/tokens': (_url, init) => {
                sent.push(JSON.parse(init.body as string))
                const meta = {
                    id: 'pat_1',
                    name: 'claude-laptop',
                    hint: 'akasha_pat_AbCd',
                    created_at: '2026-10-06T08:00:00Z',
                    expires_at: null,
                    last_used_at: null,
                }
                items = [meta]
                return {
                    status: 201,
                    body: { ...meta, token: 'akasha_pat_AbCdSecretValue' },
                }
            },
        })
        expect(await screen.findByText('No access tokens.')).toBeInTheDocument()

        fireEvent.click(screen.getByRole('button', { name: 'New token' }))
        fireEvent.change(await screen.findByLabelText(/^Name/), {
            target: { value: 'claude-laptop' },
        })
        fireEvent.change(screen.getByLabelText(/^Expires/), {
            target: { value: '' },
        })
        fireEvent.change(screen.getByLabelText(/^Your password/), {
            target: { value: 'my passphrase!' },
        })
        fireEvent.click(screen.getByRole('button', { name: 'Create token' }))

        expect(
            await screen.findByRole('textbox', { name: 'Token' }),
        ).toHaveValue('akasha_pat_AbCdSecretValue')
        expect(sent).toEqual([
            {
                name: 'claude-laptop',
                password: 'my passphrase!',
                expires_in_days: null,
            },
        ])
        fireEvent.click(screen.getByRole('button', { name: 'Done' }))
        // Listed by name and hint; the value is gone for good.
        expect(await screen.findByText('claude-laptop')).toBeInTheDocument()
        expect(screen.getByText('akasha_pat_AbCd…')).toBeInTheDocument()
        expect(
            screen.queryByDisplayValue('akasha_pat_AbCdSecretValue'),
        ).not.toBeInTheDocument()
    })

    it('marks a wrong password when creating a token', async () => {
        await renderSettings({
            'POST /auth/tokens': () =>
                apiError(
                    401,
                    'INVALID_CREDENTIALS',
                    'The current password is wrong.',
                ),
        })
        fireEvent.click(
            await screen.findByRole('button', { name: 'New token' }),
        )
        fireEvent.change(await screen.findByLabelText(/^Name/), {
            target: { value: 'agent' },
        })
        fireEvent.change(screen.getByLabelText(/^Your password/), {
            target: { value: 'wrong' },
        })
        fireEvent.click(screen.getByRole('button', { name: 'Create token' }))
        await waitFor(() => {
            expect(
                screen.getByLabelText(/^Your password/),
            ).toHaveAccessibleDescription(/The current password is wrong\./)
        })
    })

    it('revokes an access token after confirmation', async () => {
        let items = [
            {
                id: 'pat_1',
                name: 'old-agent',
                hint: 'akasha_pat_WxYz',
                created_at: '2026-10-01T08:00:00Z',
                expires_at: '2026-12-30T08:00:00Z',
                last_used_at: '2026-10-05T08:00:00Z',
            },
        ]
        const revoked: string[] = []
        await renderSettings({
            'GET /auth/tokens': () => ok({ items }),
            'DELETE /auth/tokens/pat_1': (url) => {
                revoked.push(url.pathname)
                items = []
                return { status: 204 }
            },
        })
        fireEvent.click(
            await screen.findByRole('button', { name: 'Revoke old-agent' }),
        )
        fireEvent.click(
            await screen.findByRole('button', { name: 'Revoke token' }),
        )
        expect(await screen.findByText('No access tokens.')).toBeInTheDocument()
        expect(revoked).toEqual(['/api/v1/auth/tokens/pat_1'])
    })

    it('pins the theme on the document and remembers it', async () => {
        await renderSettings()
        const select = screen.getByLabelText(/^Theme/)
        expect(select).toHaveValue('system')

        fireEvent.change(select, { target: { value: 'dark' } })
        expect(document.documentElement.dataset['theme']).toBe('dark')
        expect(localStorage.getItem('akasha.theme')).toBe('dark')

        fireEvent.change(select, { target: { value: 'system' } })
        expect(document.documentElement.dataset['theme']).toBeUndefined()
        expect(localStorage.getItem('akasha.theme')).toBeNull()
    })
})
