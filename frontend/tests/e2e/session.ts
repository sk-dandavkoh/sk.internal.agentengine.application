import type { Page } from '@playwright/test'
import type { Session } from '../../src/api/auth'

// Typed against the frontend contract so a schema change fails typecheck
// here instead of every page rendering "Could not check your session".
const session = {
    expires_at: '2099-01-01T00:00:00Z',
    user: {
        id: 'u_e2e',
        username: 'e2e',
        display_name: null,
        role: 'admin',
        disabled: false,
        created_at: '2026-10-01T08:00:00Z',
        updated_at: '2026-10-01T08:00:00Z',
        last_login_at: null,
    },
} satisfies Session

// Every area needs a session (RequireSession). Register after a spec's own
// catch-all route: Playwright runs the most recently added match first.
export async function stubSession(page: Page) {
    await page.route('**/api/v1/auth/session', (route) =>
        route.fulfill({ json: session }),
    )
}
