import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { App } from './App'

describe('App', () => {
    it('boots into the dashboard inside the shell', async () => {
        render(<App />)

        expect(
            await screen.findByRole('heading', { level: 1, name: 'Dashboard' }),
        ).toBeInTheDocument()
        expect(
            screen.getByRole('navigation', { name: 'Main' }),
        ).toBeInTheDocument()
        expect(window.location.pathname).toBe('/dashboard')
    })
})
