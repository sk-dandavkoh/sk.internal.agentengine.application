import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { loadEnv } from 'vite'
import { defineConfig } from 'vitest/config'

export default defineConfig(({ mode }) => {
    // Unprefixed AKASHA_* vars are read here only and never reach the bundle.
    const env = loadEnv(mode, process.cwd(), 'AKASHA_')
    const apiTarget = env.AKASHA_API_PROXY_TARGET ?? 'http://127.0.0.1:8000'

    return {
        plugins: [react(), tailwindcss()],
        // The browser always talks to the same origin; in development Vite
        // forwards /api to the backend, so the backend needs no CORS setup.
        server: { proxy: { '/api': apiTarget } },
        preview: { proxy: { '/api': apiTarget } },
        test: {
            environment: 'jsdom',
            setupFiles: ['./src/test/setup.ts'],
            include: ['src/**/*.test.{ts,tsx}'],
            restoreMocks: true,
            unstubGlobals: true,
        },
    }
})
