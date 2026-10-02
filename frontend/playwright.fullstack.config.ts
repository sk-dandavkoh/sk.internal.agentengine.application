import { randomUUID } from 'node:crypto'
import { defineConfig, devices } from '@playwright/test'

// Full-stack E2E: the production bundle against the real backend of this
// checkout on a fresh, empty database (tests/fullstack/backend.sh). Needs
// Docker and uv. Never reuses running servers: every run starts clean.
// ponytail: fixed ports (strict, so a clash fails loudly); pick free ones
// if this suite ever runs in parallel with itself.
const apiPort = 8765
const port = 4174
const baseURL = `http://127.0.0.1:${String(port)}`
// The run's admin password: made once here, inherited by every worker (a
// worker restarted after a failure must log in as the same admin).
process.env.AKASHA_E2E_ADMIN_PASSWORD ??= randomUUID()

export default defineConfig({
    testDir: './tests/fullstack',
    forbidOnly: !!process.env.CI,
    retries: 0,
    // One backend, one database: the specs share it.
    workers: 1,
    reporter: 'list',
    globalTeardown: './tests/fullstack/teardown.ts',
    use: { baseURL, trace: 'retain-on-failure' },
    projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
    webServer: [
        {
            command: './tests/fullstack/backend.sh',
            env: { AKASHA_E2E_API_PORT: String(apiPort) },
            url: `http://127.0.0.1:${String(apiPort)}/ready`,
            timeout: 180_000,
            reuseExistingServer: false,
            gracefulShutdown: { signal: 'SIGTERM', timeout: 15_000 },
            stdout: 'ignore',
            stderr: 'pipe',
        },
        {
            command: `npm run build && npm run preview -- --host 127.0.0.1 --port ${String(port)} --strictPort`,
            env: {
                AKASHA_API_PROXY_TARGET: `http://127.0.0.1:${String(apiPort)}`,
            },
            url: baseURL,
            reuseExistingServer: false,
        },
    ],
})
