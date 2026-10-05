import { expect, test } from '@playwright/test'
import { stubSession } from './session'

// Native behavior jsdom cannot provide (top layer, focus containment,
// Escape, focus return), checked on the design-system catalog page.
test.beforeEach(async ({ page }) => {
    await stubSession(page)
    await page.goto('/dev/ui')
    await expect(
        page.getByRole('heading', { level: 1, name: 'UI catalog' }),
    ).toBeVisible()
})

test('modal dialog traps focus, closes on Escape and restores focus', async ({
    page,
}) => {
    const trigger = page.getByRole('button', { name: 'Delete item' })
    await trigger.click()

    const dialog = page.getByRole('dialog', { name: 'Delete item?' })
    await expect(dialog).toBeVisible()
    await expect(dialog.getByRole('button', { name: 'Cancel' })).toBeFocused()
    // Modal (top layer, page behind inert), not merely shown.
    await expect(page.locator('dialog:modal')).toHaveCount(1)

    // Native modal: Tab may step out to the browser UI, but never onto the
    // inert page behind, and cycles back into the dialog.
    for (let i = 0; i < 5; i++) {
        await page.keyboard.press('Tab')
        await expect(page.locator(':focus:not(dialog, dialog *)')).toHaveCount(
            0,
        )
    }
    await expect(page.locator('dialog :focus')).toHaveCount(1)

    await page.keyboard.press('Escape')
    await expect(dialog).toBeHidden()
    await expect(trigger).toBeFocused()
})

test('confirm runs the action once and closes', async ({ page }) => {
    await page.getByRole('button', { name: 'Delete item' }).click()
    await page
        .getByRole('dialog')
        .getByRole('button', { name: 'Delete item' })
        .click()

    await expect(page.getByRole('dialog')).toBeHidden()
    await expect(page.getByText('Last menu action: Deleted item')).toBeVisible()
})

test('dropdown menu is keyboard operable', async ({ page }) => {
    const trigger = page.getByRole('button', { name: 'Actions' })
    await trigger.focus()
    await page.keyboard.press('ArrowDown')

    await expect(
        page.getByRole('menuitem', { name: 'Duplicate' }),
    ).toBeFocused()
    await page.keyboard.press('ArrowDown')
    await expect(page.getByRole('menuitem', { name: 'Delete' })).toBeFocused()
    await page.keyboard.press('Escape')
    await expect(page.getByRole('menu')).toBeHidden()
    await expect(trigger).toBeFocused()

    await page.keyboard.press('Enter')
    await page.keyboard.press('Enter')
    await expect(page.getByText('Last menu action: Duplicate')).toBeVisible()
})

test('tooltip appears on keyboard focus and hides on Escape', async ({
    page,
}) => {
    await page.getByRole('button', { name: 'Run' }).focus()
    await expect(page.getByRole('tooltip')).toHaveText(
        'Runs the published version',
    )
    await page.keyboard.press('Escape')
    await expect(page.getByRole('tooltip')).toBeHidden()
})

test('toasts appear in the notification region', async ({ page }) => {
    await page.getByRole('button', { name: 'Error toast' }).click()

    const region = page.getByRole('region', { name: 'Notifications' })
    await expect(region.getByRole('alert')).toContainText('Publish failed')
    await region.getByRole('button', { name: 'Dismiss notification' }).click()
    await expect(region.getByRole('alert')).toBeHidden()
})

// Light canvas is slate-50, dark is slate-950 (oklch lightness 0.98 / 0.12).
for (const [colorScheme, lightness] of [
    ['light', /^oklch\(0\.98/],
    ['dark', /^oklch\(0\.12/],
] as const) {
    test(`theme tokens follow the ${colorScheme} color scheme`, async ({
        page,
    }) => {
        await page.emulateMedia({ colorScheme })
        await expect(page.locator('body')).toHaveCSS(
            'background-color',
            lightness,
        )
    })
}

test('a theme pinned in Settings overrides the OS color scheme', async ({
    page,
}) => {
    await page.emulateMedia({ colorScheme: 'light' })
    await page.evaluate('document.documentElement.dataset.theme = "dark"')
    await expect(page.locator('body')).toHaveCSS(
        'background-color',
        /^oklch\(0\.12/,
    )
})

test('catalog fits a phone viewport without page-level horizontal scroll', async ({
    page,
}) => {
    await page.setViewportSize({ width: 375, height: 812 })
    const overflow = await page.evaluate<boolean>(
        'document.documentElement.scrollWidth > window.innerWidth',
    )
    expect(overflow).toBe(false)
})
