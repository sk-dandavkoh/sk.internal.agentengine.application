import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { loadEnv, type Plugin } from 'vite'
import { defineConfig } from 'vitest/config'

// Second line of defense against XSS: the bundle has no inline scripts,
// styles (React sets style props through the CSSOM, which CSP allows) or
// data: URIs, and talks only to its own origin (connect-src 'self' covers
// the same-origin live WebSocket). A <meta> cannot set frame-ancestors;
// the reverse proxy sends that header (docs/deployment.md).
const CONTENT_SECURITY_POLICY = [
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self'",
    "img-src 'self'",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'self'",
].join('; ')

// Build only: the dev server injects an inline React refresh preamble.
const contentSecurityPolicy: Plugin = {
    name: 'akasha-csp',
    apply: 'build',
    transformIndexHtml: () => [
        {
            tag: 'meta',
            attrs: {
                'http-equiv': 'Content-Security-Policy',
                content: CONTENT_SECURITY_POLICY,
            },
            // Before any script or stylesheet it governs.
            injectTo: 'head-prepend',
        },
    ],
}

export default defineConfig(({ mode }) => {
    // Unprefixed AKASHA_* vars are read here only and never reach the bundle.
    const env = loadEnv(mode, process.cwd(), 'AKASHA_')
    const apiTarget = env.AKASHA_API_PROXY_TARGET ?? 'http://127.0.0.1:8000'

    return {
        plugins: [react(), tailwindcss(), contentSecurityPolicy],
        // The browser always talks to the same origin; in development Vite
        // forwards /api to the backend, so the backend needs no CORS setup.
        // ws: the live execution WebSocket is under /api too.
        server: { proxy: { '/api': { target: apiTarget, ws: true } } },
        preview: { proxy: { '/api': { target: apiTarget, ws: true } } },
        test: {
            environment: 'jsdom',
            setupFiles: ['./src/test/setup.ts'],
            include: ['src/**/*.test.{ts,tsx}'],
            restoreMocks: true,
            unstubGlobals: true,
            // A hang detector, not a wait: the heaviest jsdom tests take
            // ~1 s alone but exceed vitest's 5 s default when every worker
            // competes for the CPU (full parallel run on a busy machine).
            testTimeout: 15_000,
        },
    }
})
