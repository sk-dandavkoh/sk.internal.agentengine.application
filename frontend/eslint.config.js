import js from '@eslint/js'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import { defineConfig, globalIgnores } from 'eslint/config'
import globals from 'globals'
import tseslint from 'typescript-eslint'

// XSS sinks: the UI renders everything as React text. Any exception
// needs an architectural justification (AGENTS §64), not a disable comment.
const htmlSinks = [
    {
        selector: "JSXAttribute[name.name='dangerouslySetInnerHTML']",
        message: 'Render text; HTML from data is an XSS sink.',
    },
]
// Tokens and secrets never go to browser storage (AGENTS §62). The only
// stored value is the theme preference (src/features/settings/theme.ts).
const storage = {
    selector: 'Identifier[name=/^(localStorage|sessionStorage)$/]',
    message: 'No browser storage: it outlives the session and XSS reads it.',
}

export default defineConfig([
    globalIgnores(['dist', 'coverage', 'playwright-report', 'test-results']),
    {
        files: ['**/*.{ts,tsx,js}'],
        extends: [
            js.configs.recommended,
            tseslint.configs.strictTypeChecked,
            tseslint.configs.stylisticTypeChecked,
        ],
        languageOptions: {
            parserOptions: {
                projectService: true,
                tsconfigRootDir: import.meta.dirname,
            },
        },
    },
    {
        // Plain JS (this config file) is outside every tsconfig; lint it untyped.
        files: ['**/*.js'],
        extends: [tseslint.configs.disableTypeChecked],
    },
    {
        files: ['src/**/*.{ts,tsx}'],
        extends: [
            reactHooks.configs.flat.recommended,
            reactRefresh.configs.vite,
        ],
        languageOptions: { globals: globals.browser },
    },
    {
        files: ['src/**/*.{ts,tsx}'],
        // Tests read storage and markup to prove nothing leaks there.
        ignores: ['src/**/*.test.{ts,tsx}', 'src/test/**'],
        rules: {
            'no-eval': 'error',
            'no-new-func': 'error',
            'no-restricted-syntax': ['error', ...htmlSinks, storage],
            'no-restricted-properties': [
                'error',
                ...['innerHTML', 'outerHTML', 'insertAdjacentHTML'].map(
                    (property) => ({
                        property,
                        message: 'Set textContent or render with React.',
                    }),
                ),
                { object: 'document', property: 'write' },
            ],
        },
    },
    {
        files: ['src/features/settings/theme.ts'],
        rules: { 'no-restricted-syntax': ['error', ...htmlSinks] },
    },
    {
        // HTTP goes through src/api (ApiClient) only: one place for auth,
        // error mapping and response validation.
        files: ['src/**/*.{ts,tsx}'],
        ignores: ['src/api/**'],
        rules: {
            'no-restricted-globals': [
                'error',
                { name: 'fetch', message: 'Use the ApiClient from src/api.' },
                {
                    name: 'XMLHttpRequest',
                    message: 'Use the ApiClient from src/api.',
                },
            ],
        },
    },
])
