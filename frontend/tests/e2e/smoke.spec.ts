import { expect, test } from '@playwright/test'

test('production build serves the application', async ({ page }) => {
    await page.goto('/')

    await expect(page).toHaveTitle('Akasha Agent Engine')
    await expect(
        page.getByRole('heading', { level: 1, name: 'Akasha Agent Engine' }),
    ).toBeVisible()
})
