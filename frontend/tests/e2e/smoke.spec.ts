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
    await page.goto('/projects/p_1/editor')
    await expect(
        page.getByRole('heading', { level: 1, name: 'Editor' }),
    ).toBeVisible()

    await page.goto('/does/not/exist')
    await expect(
        page.getByRole('heading', { level: 1, name: 'Page not found' }),
    ).toBeVisible()
})
