import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { stubBackend } from '../test/app'
import { App } from './App'

describe('App', () => {
    it('boots into the dashboard inside the shell', async () => {
        const requests = stubBackend()
        render(<App />)

        expect(
            await screen.findByRole('heading', { level: 1, name: 'Dashboard' }),
        ).toBeInTheDocument()
        expect(
            screen.getByRole('navigation', { name: 'Main' }),
        ).toBeInTheDocument()
        expect(window.location.pathname).toBe('/dashboard')
        // Same origin, under the versioned API prefix.
        expect(requests.length).toBeGreaterThan(0)
        for (const url of requests)
            expect(url.pathname.startsWith('/api/v1/')).toBe(true)
    })
})
