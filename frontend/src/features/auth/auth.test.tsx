import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import {
    apiError,
    noSession,
    ok,
    renderRoute,
    stubBackend,
    testSession,
    type Handler,
} from '../../test/app'
import { readLoginState } from './loginState'

const emptyPage = { items: [], page: { offset: 0, limit: 25, has_more: false } }

/** A backend whose session follows logins and logouts, like the real one. */
function sessionBackend(handlers: Record<string, Handler> = {}) {
    const state = { signedIn: false, loginBodies: [] as unknown[] }
    const requests = stubBackend({
        'GET /auth/session': () =>
            state.signedIn ? ok(testSession) : noSession(),
        'POST /auth/login': (_url, init) => {
            state.loginBodies.push(JSON.parse(init.body as string))
            state.signedIn = true
            return ok({ token: null, ...testSession })
        },
        'POST /auth/logout': () => {
            state.signedIn = false
            return { status: 204 }
        },
        'GET /workflows': () => (state.signedIn ? ok(emptyPage) : noSession()),
        ...handlers,
    })
    return { state, requests }
}

async function signIn(username = 'ada', password = 'correct horse battery') {
    fireEvent.change(await screen.findByLabelText(/^Username/), {
        target: { value: username },
    })
    fireEvent.change(screen.getByLabelText(/^Password/), {
        target: { value: password },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))
}

const loginHeading = () =>
    screen.findByRole('heading', { name: 'Sign in to Akasha' })

describe('protected routes', () => {
    it('send a visitor without a session to the login page, loading nothing', async () => {
        const { requests } = sessionBackend()
        const { router } = renderRoute('/workflows?q=x')

        expect(await loginHeading()).toBeInTheDocument()
        expect(router.state.location.pathname).toBe('/login')
        // Not an expiry: nobody was signed in.
        expect(screen.queryByText(/session has ended/)).not.toBeInTheDocument()
        // The page never mounted, so it never asked for its data; only the
        // login page asked whether setup is due.
        expect(requests.map((u) => u.pathname)).toEqual(['/api/v1/auth/setup'])
    })

    it('restore the session after a reload without asking to sign in', async () => {
        const { state } = sessionBackend()
        state.signedIn = true // the cookie from an earlier sign-in
        renderRoute('/workflows')

        expect(
            await screen.findByRole('heading', { level: 1, name: 'Workflows' }),
        ).toBeInTheDocument()
        expect(screen.getByText('ada')).toBeInTheDocument()
    })

    it('show an error, not the login page, when the backend is unreachable', async () => {
        let down = true
        stubBackend({
            'GET /auth/session': () =>
                down
                    ? apiError(500, 'INTERNAL_ERROR', 'Engine failure.')
                    : ok(testSession),
        })
        const { router } = renderRoute('/dashboard')

        const alert = await screen.findByRole('alert')
        expect(alert).toHaveTextContent('Could not check your session')
        expect(alert).toHaveTextContent('Engine failure.')
        expect(router.state.location.pathname).toBe('/dashboard')

        down = false
        fireEvent.click(
            within(alert).getByRole('button', { name: 'Try again' }),
        )
        expect(
            await screen.findByRole('heading', { level: 1, name: 'Dashboard' }),
        ).toBeInTheDocument()
    })
})

describe('login', () => {
    it('signs in by cookie and returns to the page that was requested', async () => {
        const { state } = sessionBackend()
        const { router, queryClient } = renderRoute('/workflows?q=x')
        await loginHeading()

        await signIn()

        expect(
            await screen.findByRole('heading', { level: 1, name: 'Workflows' }),
        ).toBeInTheDocument()
        expect(router.state.location.pathname).toBe('/workflows')
        expect(router.state.location.search).toBe('?q=x')
        expect(state.loginBodies).toEqual([
            {
                username: 'ada',
                password: 'correct horse battery',
                cookie: true,
            },
        ])
        // No token ever reaches client state; the password leaves it as
        // soon as the login form is gone.
        const cached = () =>
            JSON.stringify([
                queryClient
                    .getQueryCache()
                    .getAll()
                    .map((q) => q.state.data),
                queryClient
                    .getMutationCache()
                    .getAll()
                    .map((m) => m.state),
            ])
        expect(cached()).not.toContain('token')
        await waitFor(() => {
            expect(cached()).not.toContain('correct horse battery')
        })
    })

    it('shows the backend message for wrong credentials and stays put', async () => {
        sessionBackend({
            'POST /auth/login': () =>
                apiError(
                    401,
                    'INVALID_CREDENTIALS',
                    'Invalid username or password.',
                ),
        })
        const { router } = renderRoute('/login')

        await signIn('ada', 'wrong password!')

        expect(await screen.findByRole('alert')).toHaveTextContent(
            'Invalid username or password.',
        )
        expect(router.state.location.pathname).toBe('/login')
        // Still usable: the form is there for the next attempt.
        expect(
            screen.getByRole('button', { name: 'Sign in' }),
        ).not.toHaveAttribute('aria-disabled', 'true')
    })

    it('explains a throttled login', async () => {
        sessionBackend({
            'POST /auth/login': () =>
                apiError(
                    429,
                    'LOGIN_THROTTLED',
                    'Too many failed logins. Try again later.',
                ),
        })
        renderRoute('/login')
        await signIn()
        expect(await screen.findByRole('alert')).toHaveTextContent(
            'Too many failed logins.',
        )
    })

    it('skips the form when already signed in', async () => {
        const { state } = sessionBackend()
        state.signedIn = true
        const { router } = renderRoute('/login')

        expect(
            await screen.findByRole('heading', { level: 1, name: 'Dashboard' }),
        ).toBeInTheDocument()
        expect(router.state.location.pathname).toBe('/dashboard')
    })
})

describe('first-run setup', () => {
    /** A backend without accounts: setup creates one, then login works. */
    function freshBackend(handlers: Record<string, Handler> = {}) {
        const setupBodies: unknown[] = []
        let hasAdmin = false
        const backend = sessionBackend({
            'GET /auth/setup': () => ok({ required: !hasAdmin }),
            'POST /auth/setup': (_url, init) => {
                setupBodies.push(JSON.parse(init.body as string))
                hasAdmin = true
                return { status: 201, body: testSession.user }
            },
            ...handlers,
        })
        return { ...backend, setupBodies }
    }

    async function fillSetup(password: string, confirm = password) {
        fireEvent.change(await screen.findByLabelText(/^Username/), {
            target: { value: ' root ' },
        })
        fireEvent.change(screen.getByLabelText(/^Password/), {
            target: { value: password },
        })
        fireEvent.change(screen.getByLabelText(/^Confirm password/), {
            target: { value: confirm },
        })
        fireEvent.click(
            screen.getByRole('button', { name: 'Create administrator' }),
        )
    }

    it('creates the first admin and signs in as it', async () => {
        const { state, setupBodies } = freshBackend()
        const { router } = renderRoute('/workflows')

        expect(
            await screen.findByRole('heading', { name: 'Set up Akasha' }),
        ).toBeInTheDocument()
        await fillSetup('a long enough password')

        expect(
            await screen.findByRole('heading', { level: 1, name: 'Workflows' }),
        ).toBeInTheDocument()
        expect(router.state.location.pathname).toBe('/workflows')
        expect(setupBodies).toEqual([
            { username: 'root', password: 'a long enough password' },
        ])
        // Then signed in by cookie, like the sign-in form does.
        expect(state.loginBodies).toEqual([
            {
                username: 'root',
                password: 'a long enough password',
                cookie: true,
            },
        ])
    })

    it('refuses mismatched passwords before asking the backend', async () => {
        const { setupBodies } = freshBackend()
        renderRoute('/login')
        await fillSetup('a long enough password', 'something else entirely')

        expect(
            await screen.findByText('The passwords do not match.'),
        ).toBeInTheDocument()
        expect(setupBodies).toEqual([])
    })

    it('shows the backend validation message', async () => {
        freshBackend({
            'POST /auth/setup': () =>
                apiError(
                    422,
                    'DOMAIN_VALIDATION_ERROR',
                    'Usernames may contain only a-z, 0-9, ".", "_" and "-".',
                ),
        })
        renderRoute('/login')
        await fillSetup('a long enough password')

        expect(await screen.findByRole('alert')).toHaveTextContent(
            'Usernames may contain only',
        )
        expect(
            screen.getByRole('heading', { name: 'Set up Akasha' }),
        ).toBeInTheDocument()
    })

    it('offers sign-in when someone else finished setup first', async () => {
        let setupDone = false
        freshBackend({
            'GET /auth/setup': () => ok({ required: !setupDone }),
            'POST /auth/setup': () => {
                setupDone = true
                return apiError(
                    409,
                    'SETUP_ALREADY_COMPLETED',
                    'The initial admin account already exists.',
                )
            },
        })
        renderRoute('/login')
        await fillSetup('a long enough password')

        expect(await loginHeading()).toBeInTheDocument()
        expect(
            screen.getByText(/Setup was already completed/),
        ).toBeInTheDocument()
    })

    it('shows sign-in when the setup status cannot be read', async () => {
        sessionBackend({
            'GET /auth/setup': () =>
                apiError(503, 'SERVICE_UNAVAILABLE', 'Engine restarting.'),
        })
        renderRoute('/login')
        expect(await loginHeading()).toBeInTheDocument()
    })
})

describe('session end', () => {
    it('sends the user to sign in again when the backend rejects the session', async () => {
        const { state } = sessionBackend()
        state.signedIn = true
        const { router } = renderRoute('/dashboard')
        await screen.findByRole('heading', { level: 1, name: 'Dashboard' })

        // Expired, revoked or signed out elsewhere: the next request says so.
        state.signedIn = false
        fireEvent.click(
            within(screen.getByRole('navigation', { name: 'Main' })).getByRole(
                'link',
                { name: 'Workflows' },
            ),
        )

        expect(await loginHeading()).toBeInTheDocument()
        expect(screen.getByRole('status')).toHaveTextContent(
            'Your session has ended',
        )

        await signIn()
        expect(
            await screen.findByRole('heading', { level: 1, name: 'Workflows' }),
        ).toBeInTheDocument()
        expect(router.state.location.pathname).toBe('/workflows')
    })

    it('signs out on the server and forgets the previous user’s data', async () => {
        const { state, requests } = sessionBackend()
        state.signedIn = true
        const { router, queryClient } = renderRoute('/workflows')
        await screen.findByRole('heading', { level: 1, name: 'Workflows' })

        fireEvent.click(screen.getByRole('button', { name: 'Sign out' }))

        expect(await loginHeading()).toBeInTheDocument()
        expect(router.state.location.pathname).toBe('/login')
        expect(screen.queryByText(/session has ended/)).not.toBeInTheDocument()
        expect(requests.some((u) => u.pathname === '/api/v1/auth/logout')).toBe(
            true,
        )
        expect(state.signedIn).toBe(false)
        await waitFor(() => {
            expect(
                queryClient
                    .getQueryCache()
                    .getAll()
                    .map((q) => q.queryKey.slice(0, 2).join('/'))
                    .sort(),
            ).toEqual(['auth/session', 'auth/setup'])
        })
    })

    it('stays signed in when signing out fails, and says so', async () => {
        const { state } = sessionBackend({
            'POST /auth/logout': () =>
                apiError(503, 'SERVICE_UNAVAILABLE', 'Engine restarting.'),
        })
        state.signedIn = true
        const { router } = renderRoute('/workflows')
        await screen.findByRole('heading', { level: 1, name: 'Workflows' })

        fireEvent.click(screen.getByRole('button', { name: 'Sign out' }))

        expect(
            await screen.findByText('Could not sign out'),
        ).toBeInTheDocument()
        // The server session (and its cookie) is still valid: pretending
        // otherwise would leave it usable behind the user's back.
        expect(router.state.location.pathname).toBe('/workflows')
    })
})

describe('readLoginState', () => {
    it('returns only to in-app paths', () => {
        const from = (pathname: string) => ({
            from: { pathname, search: '?a=1', hash: '#h' },
        })
        expect(readLoginState(from('/workflows')).returnTo).toBe(
            '/workflows?a=1#h',
        )
        expect(readLoginState(from('//evil.example')).returnTo).toBe('/')
        expect(readLoginState(from('https://evil.example')).returnTo).toBe('/')
        expect(readLoginState(from('/login')).returnTo).toBe('/')
        expect(readLoginState('garbage')).toEqual({
            returnTo: '/',
            expired: false,
        })
        expect(readLoginState({ expired: true }).expired).toBe(true)
    })
})
