import { expect, test as base } from '@playwright/test'

// Every spec runs the production bundle under its Content Security Policy
// (vite.config.ts). A violation means the policy breaks a feature or the
// bundle started relying on inline code: either way the test fails.
export const test = base.extend<{ cspViolations: undefined }>({
    cspViolations: [
        async ({ page }, use) => {
            const violations: string[] = []
            page.on('console', (message) => {
                if (message.text().includes('Content Security Policy'))
                    violations.push(message.text())
            })
            await use(undefined)
            expect(violations).toEqual([])
        },
        { auto: true },
    ],
})

export { expect }
