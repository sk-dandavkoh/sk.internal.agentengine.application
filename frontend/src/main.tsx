import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { App } from './app/App'
import { applyTheme, readTheme } from './features/settings/theme'
import './styles/index.css'

// Before the first render, so a pinned theme never flashes the OS one.
applyTheme(readTheme())

const root = document.getElementById('root')
if (!root) {
    throw new Error('Missing #root element in index.html')
}

createRoot(root).render(
    <StrictMode>
        <App />
    </StrictMode>,
)
