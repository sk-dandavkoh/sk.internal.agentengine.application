import { expect, test } from '@playwright/test'
import { stubSession } from './session'

// The plain Playwright test: this spec provokes a violation on purpose.
test('the production bundle enforces its Content Security Policy', async ({
    page,
}) => {
    await stubSession(page)
    const violations: string[] = []
    page.on('console', (message) => {
        if (message.text().includes('Content Security Policy'))
            violations.push(message.text())
    })
    await page.goto('/dashboard')
    await expect(
        page.getByRole('heading', { level: 1, name: 'Dashboard' }),
    ).toBeVisible()

    // What an injected-HTML bug would amount to: inline script and an
    // inline event handler. Neither may run.
    // A string, not a function: the e2e tsconfig has no DOM types.
    const ran = await page.evaluate(`(async () => {
        const script = document.createElement('script')
        script.textContent = 'window.__xss = true'
        document.body.append(script)
        const img = document.createElement('img')
        img.setAttribute('src', '/missing.png')
        img.setAttribute('onerror', 'window.__xss = true')
        document.body.append(img)
        await new Promise((resolve) => setTimeout(resolve, 200))
        return window.__xss === true
    })()`)

    expect(ran).toBe(false)
    expect(violations.length).toBeGreaterThan(0)
})
