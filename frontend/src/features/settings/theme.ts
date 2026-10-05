// The color theme: a UI preference, so localStorage is the right place
// (frontend AGENTS §62). 'system' follows the OS via CSS color-scheme;
// the others pin data-theme on <html> (styles/index.css).

export const THEMES = ['system', 'light', 'dark'] as const
export type Theme = (typeof THEMES)[number]

const STORAGE_KEY = 'akasha.theme'

function isTheme(value: unknown): value is Theme {
    return THEMES.some((theme) => theme === value)
}

export function readTheme(): Theme {
    try {
        const stored = localStorage.getItem(STORAGE_KEY)
        return isTheme(stored) ? stored : 'system'
    } catch {
        // Storage blocked (privacy mode, policy): the OS theme still works.
        return 'system'
    }
}

export function applyTheme(theme: Theme) {
    const { dataset } = document.documentElement
    if (theme === 'system') delete dataset['theme']
    else dataset['theme'] = theme
}

export function saveTheme(theme: Theme) {
    applyTheme(theme)
    try {
        if (theme === 'system') localStorage.removeItem(STORAGE_KEY)
        else localStorage.setItem(STORAGE_KEY, theme)
    } catch {
        // Not persisted when storage is blocked; it applies to this tab.
    }
}
