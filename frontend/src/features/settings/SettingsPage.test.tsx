import { fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { apiError, ok, renderRoute, stubBackend } from '../../test/app'

const status = { version: '0.27.0', ready: true, failures: {} }

afterEach(() => {
    localStorage.clear()
    delete document.documentElement.dataset['theme']
})

async function renderSettings(handlers = {}) {
    const bodies: unknown[] = []
    const requests = stubBackend({
        'GET /system/status': () => ok(status),
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
