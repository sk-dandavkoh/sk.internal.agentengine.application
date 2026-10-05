import type { Page } from '@playwright/test'

// Every area needs a session (RequireSession). Register after a spec's own
// catch-all route: Playwright runs the most recently added match first.
export async function stubSession(page: Page) {
    await page.route('**/api/v1/auth/session', (route) =>
        route.fulfill({
            json: {
                expires_at: '2099-01-01T00:00:00Z',
                user: {
                    id: 'u_e2e',
                    username: 'e2e',
                    role: 'admin',
                    disabled: false,
                    created_at: '2026-10-01T08:00:00Z',
                    updated_at: '2026-10-01T08:00:00Z',
                },
            },
        }),
    )
}
