import { defineConfig, devices } from '@playwright/test'

const port = 4173
const baseURL = `http://127.0.0.1:${String(port)}`

export default defineConfig({
    testDir: './tests/e2e',
    forbidOnly: !!process.env.CI,
    retries: 0,
    reporter: 'list',
    use: { baseURL, trace: 'retain-on-failure' },
    projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
    // E2E runs against the production bundle, not the dev server.
    webServer: {
        command: `npm run build && npm run preview -- --host 127.0.0.1 --port ${String(port)} --strictPort`,
        url: baseURL,
        reuseExistingServer: !process.env.CI,
    },
})
