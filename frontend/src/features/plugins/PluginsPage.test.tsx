import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
    apiError,
    ok,
    renderRoute,
    stubBackend,
    type Reply,
} from '../../test/app'

function plugin(id: string, overrides: object = {}) {
    return {
        plugin_id: id,
        state: 'enabled',
        error: null,
        metadata: {
            name: 'Text Tools',
            version: '1.0.0',
            description: 'Small text transformations.',
            engine_compatibility: '>=0.1.0,<1.0.0',
            sdk_compatibility: '>=1.0.0,<2.0.0',
            dependencies: [
                { kind: 'plugin', name: 'base', version_constraint: '>=1.0.0' },
            ],
            node_types: [`${id}.slugify`],
        },
        ...overrides,
    }
}

const list = (items: unknown[], install_enabled = true) =>
    ok({ items, install_enabled })

function error(status: number, code: string, details: object): Reply {
    return {
        status,
        body: { code, message: `${code} message.`, details, request_id: 'r' },
    }
}

afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
})

/** The list item of the plugin whose card is titled `name`. */
async function card(name: string) {
    const heading = await screen.findByRole('heading', { name, level: 2 })
    const item = heading.closest('li')
    if (!item) throw new Error(`no list item for ${name}`)
    return within(item)
}

function zipFile(size = 3) {
    return new File([new Uint8Array(size)], 'acme.zip', {
        type: 'application/zip',
    })
}

async function openInstall() {
    fireEvent.click(
        await screen.findByRole('button', { name: 'Install plugin' }),
    )
    const dialog = within(await screen.findByRole('dialog'))
    fireEvent.change(dialog.getByLabelText('Plugin package (.zip)'), {
        target: { files: [zipFile()] },
    })
    fireEvent.click(dialog.getByLabelText('I trust the source of this package'))
    return dialog
}

describe('PluginsPage', () => {
    it('lists plugins with version, compatibility, dependencies, nodes and state', async () => {
        stubBackend({
            'GET /plugins': () =>
                list([
                    plugin('text_tools'),
                    plugin('old', {
                        state: 'disabled',
                        metadata: { ...plugin('old').metadata, name: 'Old' },
                    }),
                    {
                        plugin_id: 'broken',
                        state: 'failed',
                        error: 'PLUGIN_MANIFEST_INVALID',
                        metadata: null,
                    },
                ]),
        })
        renderRoute('/plugins')

        const tools = await card('Text Tools')
        expect(tools.getByText('v1.0.0')).toBeVisible()
        expect(tools.getByText('Enabled')).toBeVisible()
        expect(tools.getByText('>=0.1.0,<1.0.0')).toBeVisible()
        expect(tools.getByText('base')).toBeVisible()
        expect(tools.getByText('text_tools.slugify')).toBeVisible()
        expect(
            tools.getByRole('button', { name: 'Disable Text Tools' }),
        ).toBeVisible()

        const old = await card('Old')
        expect(old.getByText('Disabled')).toBeVisible()
        expect(old.getByRole('button', { name: 'Enable Old' })).toBeVisible()

        const broken = await card('broken')
        expect(broken.getByText('Failed')).toBeVisible()
        expect(
            broken.getByText('Not active: PLUGIN_MANIFEST_INVALID'),
        ).toBeVisible()
        // Without metadata the backend cannot enable it: no button.
        expect(broken.queryByRole('button', { name: /Enable/ })).toBeNull()
        expect(screen.getByText('Plugins run with full access')).toBeVisible()
    })

    it('renders plugin metadata as text, never as markup', async () => {
        const evil = '<img src=x onerror="window.pwned=1">'
        stubBackend({
            'GET /plugins': () =>
                list([
                    plugin('evil', {
                        metadata: {
                            ...plugin('evil').metadata,
                            name: evil,
                            description: `<script>alert(1)</script>${evil}`,
                        },
                    }),
                ]),
        })
        renderRoute('/plugins')

        const item = await card(evil)
        expect(item.getByText(`<script>alert(1)</script>${evil}`)).toBeVisible()
        expect(document.querySelector('img[src="x"]')).toBeNull()
        expect(document.querySelector('script')).toBeNull()
    })

    it('points to the CLI when uploads are disabled', async () => {
        stubBackend({ 'GET /plugins': () => list([], false) })
        renderRoute('/plugins')

        expect(await screen.findByText('Uploads are off')).toBeVisible()
        expect(screen.getByText('No plugins installed')).toBeVisible()
        expect(
            screen.queryByRole('button', { name: 'Install plugin' }),
        ).toBeNull()
    })

    it('installs an uploaded package after an explicit trust confirmation', async () => {
        let items: unknown[] = []
        let sent: { type: string | null; body: unknown } | undefined
        stubBackend({
            'GET /plugins': () => list(items),
            'POST /plugins': (_url, init) => {
                sent = {
                    type: new Headers(init.headers).get('content-type'),
                    body: init.body,
                }
                items = [plugin('acme')]
                return { status: 201, body: plugin('acme') }
            },
        })
        renderRoute('/plugins')

        fireEvent.click(
            await screen.findByRole('button', { name: 'Install plugin' }),
        )
        const dialog = within(await screen.findByRole('dialog'))
        const submit = dialog.getByRole('button', {
            name: 'Install and run plugin code',
        })
        expect(dialog.getByText('Only install code you trust')).toBeVisible()
        fireEvent.change(dialog.getByLabelText('Plugin package (.zip)'), {
            target: { files: [zipFile()] },
        })
        expect(submit).toBeDisabled() // trust not yet confirmed
        fireEvent.click(
            dialog.getByLabelText('I trust the source of this package'),
        )
        fireEvent.click(submit)

        expect(
            await screen.findByText('Plugin “Text Tools” installed'),
        ).toBeVisible()
        expect(sent?.type).toBe('application/zip')
        expect(sent?.body).toBeInstanceOf(File)
        await waitFor(() => {
            expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
        })
        expect(await card('Text Tools')).toBeDefined()
    })

    it('blocks a package over the size limit before uploading', async () => {
        const upload = vi.fn(() => ok(plugin('acme')))
        stubBackend({ 'GET /plugins': () => list([]), 'POST /plugins': upload })
        renderRoute('/plugins')
        fireEvent.click(
            await screen.findByRole('button', { name: 'Install plugin' }),
        )
        const dialog = within(await screen.findByRole('dialog'))

        fireEvent.change(dialog.getByLabelText('Plugin package (.zip)'), {
            target: { files: [zipFile(16 * 1024 * 1024 + 1)] },
        })
        fireEvent.click(
            dialog.getByLabelText('I trust the source of this package'),
        )

        expect(dialog.getByText(/exceeds 16 MiB/)).toBeVisible()
        expect(
            dialog.getByRole('button', { name: 'Install and run plugin code' }),
        ).toBeDisabled()
        expect(upload).not.toHaveBeenCalled()
    })

    it('shows an invalid package with the backend-reported fields', async () => {
        stubBackend({
            'GET /plugins': () => list([]),
            'POST /plugins': () =>
                error(422, 'PLUGIN_MANIFEST_INVALID', {
                    fields: ['components.nodes.0.entry_point'],
                }),
        })
        renderRoute('/plugins')
        const dialog = await openInstall()

        fireEvent.click(
            dialog.getByRole('button', { name: 'Install and run plugin code' }),
        )

        const alert = await dialog.findByRole('alert')
        expect(alert).toHaveTextContent('PLUGIN_MANIFEST_INVALID message.')
        expect(alert).toHaveTextContent('components.nodes.0.entry_point')
        expect(screen.getByRole('dialog')).toBeVisible()
    })

    it('shows an incompatible package with its compatibility issues', async () => {
        stubBackend({
            'GET /plugins': () => list([]),
            'POST /plugins': () =>
                error(409, 'PLUGIN_INCOMPATIBLE', {
                    plugin_id: 'acme',
                    issues: ["engine 0.1.0 does not satisfy '>=99.0.0'"],
                }),
        })
        renderRoute('/plugins')
        const dialog = await openInstall()

        fireEvent.click(
            dialog.getByRole('button', { name: 'Install and run plugin code' }),
        )

        const alert = await dialog.findByRole('alert')
        expect(alert).toHaveTextContent('Compatibility issues')
        expect(alert).toHaveTextContent(
            "engine 0.1.0 does not satisfy '>=99.0.0'",
        )
    })

    it('disables and enables through the backend and shows its new state', async () => {
        let state = 'enabled'
        const calls: string[] = []
        stubBackend({
            'GET /plugins': () => list([plugin('acme', { state })]),
            'POST /plugins/acme/disable': () => {
                calls.push('disable')
                state = 'disabled'
                return ok(plugin('acme', { state }))
            },
            'POST /plugins/acme/enable': () => {
                calls.push('enable')
                state = 'enabled'
                return ok(plugin('acme', { state }))
            },
        })
        renderRoute('/plugins')

        fireEvent.click(
            await screen.findByRole('button', { name: 'Disable Text Tools' }),
        )
        let dialog = within(await screen.findByRole('dialog'))
        fireEvent.click(dialog.getByRole('button', { name: 'Disable plugin' }))
        expect(
            await screen.findByText('Plugin “Text Tools” disabled'),
        ).toBeVisible()
        expect((await card('Text Tools')).getByText('Disabled')).toBeVisible()

        fireEvent.click(
            await screen.findByRole('button', { name: 'Enable Text Tools' }),
        )
        dialog = within(await screen.findByRole('dialog'))
        expect(dialog.getByText(/trusted code/)).toBeVisible()
        fireEvent.click(dialog.getByRole('button', { name: 'Enable plugin' }))
        expect(
            await screen.findByText('Plugin “Text Tools” enabled'),
        ).toBeVisible()
        expect(calls).toEqual(['disable', 'enable'])
    })

    it('keeps the state when the backend refuses to disable', async () => {
        stubBackend({
            'GET /plugins': () => list([plugin('acme')]),
            'POST /plugins/acme/disable': () =>
                error(409, 'PLUGIN_IN_USE', { dependents: ['addon'] }),
        })
        renderRoute('/plugins')

        fireEvent.click(
            await screen.findByRole('button', { name: 'Disable Text Tools' }),
        )
        const dialog = within(await screen.findByRole('dialog'))
        fireEvent.click(dialog.getByRole('button', { name: 'Disable plugin' }))

        const alert = await dialog.findByRole('alert')
        expect(alert).toHaveTextContent('Enabled plugins that depend on it')
        expect(alert).toHaveTextContent('addon')
        fireEvent.click(dialog.getByRole('button', { name: 'Cancel' }))
        expect((await card('Text Tools')).getByText('Enabled')).toBeVisible()
    })

    it('removes after confirmation, and forces only after an explicit second confirmation', async () => {
        let items = [plugin('acme')]
        const removals: string[] = []
        stubBackend({
            'GET /plugins': () => list(items),
            'DELETE /plugins/acme': (url) => {
                const force = url.searchParams.get('force')
                removals.push(force ?? 'none')
                if (force !== 'true')
                    return error(409, 'PLUGIN_IN_USE', {
                        plugin_id: 'acme',
                        projects: ['p-1'],
                    })
                items = []
                return ok({ plugin_id: 'acme', affected_projects: ['p-1'] })
            },
        })
        renderRoute('/plugins')

        fireEvent.click(
            await screen.findByRole('button', { name: 'Remove Text Tools' }),
        )
        const dialog = within(await screen.findByRole('dialog'))
        expect(dialog.getByText('This cannot be undone.')).toBeVisible()
        fireEvent.click(dialog.getByRole('button', { name: 'Remove plugin' }))

        const alert = await dialog.findByRole('alert')
        expect(
            within(alert).getByRole('link', { name: 'p-1' }),
        ).toHaveAttribute('href', '/projects/p-1')
        // Still listed: the refused removal changed nothing.
        expect(
            screen.getByRole('heading', { name: 'Text Tools', level: 2 }),
        ).toBeVisible()
        fireEvent.click(dialog.getByRole('button', { name: 'Remove anyway' }))

        expect(
            await screen.findByText('Plugin “Text Tools” removed'),
        ).toBeVisible()
        expect(await screen.findByText('No plugins installed')).toBeVisible()
        expect(removals).toEqual(['none', 'true'])
    })

    it('shows a load failure with a retry', async () => {
        let fail = true
        stubBackend({
            'GET /plugins': () =>
                fail
                    ? apiError(500, 'INTERNAL_ERROR', 'Backend down.')
                    : list([plugin('acme')]),
        })
        renderRoute('/plugins')

        expect(await screen.findByText('Could not load plugins')).toBeVisible()
        expect(screen.getByText('Backend down.')).toBeVisible()
        fail = false
        fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
        expect(await card('Text Tools')).toBeDefined()
    })

    it('explains that plugins are admin only on 403', async () => {
        stubBackend({
            'GET /plugins': () =>
                apiError(403, 'PERMISSION_DENIED', 'Admin role required.'),
        })
        renderRoute('/plugins')

        expect(await screen.findByText('Admin access required')).toBeVisible()
        expect(
            screen.queryByRole('button', { name: 'Install plugin' }),
        ).toBeNull()
    })
})
