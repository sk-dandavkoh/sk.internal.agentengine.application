import { expect, test } from './fixtures'
import { stubSession } from './session'

// No backend behind the preview server: only the session is answered.
test.beforeEach(async ({ page }) => {
    await stubSession(page)
})

test('production build boots into the dashboard', async ({ page }) => {
    await page.goto('/')

    await expect(page).toHaveTitle('Akasha Agent Engine')
    await expect(page).toHaveURL('/dashboard')
    await expect(
        page.getByRole('heading', { level: 1, name: 'Dashboard' }),
    ).toBeVisible()
})

test('sidebar navigation changes the URL and page', async ({ page }) => {
    await page.goto('/dashboard')

    await page
        .getByRole('navigation', { name: 'Main' })
        .getByRole('link', { name: 'Executions' })
        .click()

    await expect(page).toHaveURL('/executions')
    await expect(
        page.getByRole('heading', { level: 1, name: 'Executions' }),
    ).toBeVisible()
})

test('deep links to nested and unknown routes resolve client-side', async ({
    page,
}) => {
    await page.goto('/workflows/p_1/editor')
    await expect(
        page.getByRole('heading', { level: 1, name: 'Editor' }),
    ).toBeVisible()

    await page.goto('/does/not/exist')
    await expect(
        page.getByRole('heading', { level: 1, name: 'Page not found' }),
    ).toBeVisible()
})

test('a page chunk removed by a newer release offers a reload', async ({
    page,
}) => {
    await page.goto('/dashboard')
    // A tab opened before an upgrade asks for a chunk the new release no
    // longer has (docs/deployment.md: assets 404, never index.html).
    await page.route('**/assets/ExecutionsPage-*.js', (route) =>
        route.fulfill({ status: 404 }),
    )

    await page
        .getByRole('navigation', { name: 'Main' })
        .getByRole('link', { name: 'Executions' })
        .click()

    await expect(
        page.getByText('This page could not be displayed. Try reloading it.'),
    ).toBeVisible()
    await expect(page.getByRole('button', { name: 'Reload' })).toBeVisible()
    // The shell stays usable.
    await expect(page.getByRole('navigation', { name: 'Main' })).toBeVisible()
})
