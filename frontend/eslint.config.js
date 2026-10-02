import js from '@eslint/js'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import { defineConfig, globalIgnores } from 'eslint/config'
import globals from 'globals'
import tseslint from 'typescript-eslint'

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
