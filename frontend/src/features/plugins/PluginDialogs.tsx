import { useId, useState } from 'react'
import { z } from 'zod'
import { ApiError, errorMessage } from '../../api/errors'
import {
    useInstallPlugin,
    useRemovePlugin,
    useSetPluginEnabled,
} from '../../api/mutations'
import { MAX_PLUGIN_PACKAGE_BYTES, type Plugin } from '../../api/plugins'
import {
    Alert,
    Button,
    Checkbox,
    ConfirmDialog,
    Dialog,
    TextLink,
    useToast,
} from '../../components/ui'
import { pluginLabel } from './label'

// Every message below comes from the backend; none of it is interpreted as
// markup (plain text nodes only), and plugin metadata never is either.

// The structured details the plugin endpoints attach to their errors
// (backend plugins/registry.py, manifest.py, application/plugins.py).
const detailsSchema = z.object({
    issues: z.array(z.string()).optional(), // PLUGIN_INCOMPATIBLE
    fields: z.array(z.string()).optional(), // PLUGIN_MANIFEST_INVALID
    reason: z.string().optional(),
    dependents: z.array(z.string()).optional(), // PLUGIN_IN_USE (plugins)
    workflows: z.array(z.string()).optional(), // PLUGIN_IN_USE (workflows)
    path: z.string().optional(), // PLUGIN_PACKAGE_INVALID
    output: z.string().optional(), // PLUGIN_DEPENDENCIES_FAILED: uv's explanation
    distributions: z.array(z.string()).optional(), // ... already loaded: restart
})

/** The backend's error message plus whatever structured detail it sent. */
function PluginError({ error }: { error: unknown }) {
    const parsed =
        error instanceof ApiError
            ? detailsSchema.safeParse(error.details)
            : undefined
    const d = parsed?.success ? parsed.data : {}
    const list = (title: string, items: string[] | undefined) =>
        items &&
        items.length > 0 && (
            <div>
                <p className="font-medium">{title}</p>
                <ul className="list-disc pl-5">
                    {items.map((item) => (
                        <li key={item} className="break-words">
                            {item}
                        </li>
                    ))}
                </ul>
            </div>
        )
    return (
        <Alert tone="danger" title={errorMessage(error)}>
            {list('Compatibility issues', d.issues)}
            {list('Invalid manifest fields', d.fields)}
            {d.reason && <p className="break-words">{d.reason}</p>}
            {d.path && (
                <p className="break-words">
                    Entry: <code>{d.path}</code>
                </p>
            )}
            {d.output && (
                <pre className="max-h-48 overflow-auto text-xs whitespace-pre-wrap break-words">
                    {d.output}
                </pre>
            )}
            {list('Libraries loaded in another version', d.distributions)}
            {list('Enabled plugins that depend on it', d.dependents)}
            {d.workflows && d.workflows.length > 0 && (
                <div>
                    <p className="font-medium">Workflows that use it</p>
                    <ul className="list-disc pl-5">
                        {d.workflows.map((id) => (
                            <li key={id}>
                                <TextLink to={`/workflows/${id}`}>
                                    {id}
                                </TextLink>
                            </li>
                        ))}
                    </ul>
                </div>
            )}
        </Alert>
    )
}

const TRUST_NOTICE =
    'Plugins are trusted code: they run inside the engine process with its full access to data, credentials and the network. They are not sandboxed.'

export function InstallPluginDialog({ onClose }: { onClose: () => void }) {
    const install = useInstallPlugin()
    const toast = useToast()
    const inputId = useId()
    const [file, setFile] = useState<File | null>(null)
    const [trusted, setTrusted] = useState(false)
    // UX only: the backend enforces type and size itself.
    const tooLarge = file !== null && file.size > MAX_PLUGIN_PACKAGE_BYTES
    const close = () => {
        install.reset()
        onClose()
    }

    return (
        <Dialog
            open
            onClose={close}
            title="Install plugin"
            dismissible={!install.isPending}
            description="Upload a plugin package as a .zip. The engine validates its manifest and compatibility before any of its code runs."
            footer={
                <>
                    <Button onClick={close} disabled={install.isPending}>
                        Cancel
                    </Button>
                    <Button
                        variant="danger"
                        loading={install.isPending}
                        disabled={!file || tooLarge || !trusted}
                        onClick={() => {
                            if (!file) return
                            install.mutate(file, {
                                onSuccess: (plugin) => {
                                    toast.notify({
                                        tone: 'success',
                                        title: `Plugin “${pluginLabel(plugin)}” installed`,
                                    })
                                    close()
                                },
                            })
                        }}
                    >
                        Install and run plugin code
                    </Button>
                </>
            }
        >
            <div className="flex flex-col gap-4">
                <Alert tone="warning" title="Only install code you trust">
                    {TRUST_NOTICE} Installing enables the plugin immediately.
                </Alert>
                <div className="flex flex-col gap-1.5">
                    <label htmlFor={inputId} className="text-sm font-medium">
                        Plugin package (.zip)
                    </label>
                    <input
                        id={inputId}
                        type="file"
                        accept=".zip,application/zip"
                        aria-invalid={tooLarge || undefined}
                        className="focus-ring block w-full rounded-md text-sm file:mr-3 file:rounded-md file:border file:border-border-strong file:bg-surface file:px-3 file:py-1.5"
                        onChange={(event) => {
                            install.reset()
                            setFile(event.target.files?.[0] ?? null)
                        }}
                    />
                    {tooLarge && (
                        <p className="text-sm font-medium text-danger-text">
                            The package exceeds{' '}
                            {MAX_PLUGIN_PACKAGE_BYTES / 1024 / 1024} MiB.
                        </p>
                    )}
                </div>
                <Checkbox
                    label="I trust the source of this package"
                    checked={trusted}
                    onChange={(event) => {
                        setTrusted(event.target.checked)
                    }}
                />
                {install.error && <PluginError error={install.error} />}
            </div>
        </Dialog>
    )
}

export function SetEnabledDialog({
    plugin,
    enable,
    onClose,
}: {
    plugin: Plugin
    enable: boolean
    onClose: () => void
}) {
    const mutation = useSetPluginEnabled()
    const toast = useToast()
    const label = pluginLabel(plugin)
    const close = () => {
        mutation.reset()
        onClose()
    }
    return (
        <ConfirmDialog
            open
            title={enable ? `Enable “${label}”?` : `Disable “${label}”?`}
            confirmLabel={enable ? 'Enable plugin' : 'Disable plugin'}
            tone={enable ? 'primary' : 'danger'}
            pending={mutation.isPending}
            onCancel={close}
            onConfirm={() => {
                mutation.mutate(
                    { pluginId: plugin.plugin_id, enabled: enable },
                    {
                        onSuccess: () => {
                            toast.notify({
                                tone: 'success',
                                title: `Plugin “${label}” ${enable ? 'enabled' : 'disabled'}`,
                            })
                            close()
                        },
                    },
                )
            }}
        >
            {enable
                ? `${TRUST_NOTICE} The engine checks compatibility again, then loads its code and registers its node types.`
                : 'Its node types are unregistered. Workflows that use them fail to validate, publish and run until it is enabled again.'}{' '}
            Dedicated worker processes see the change after a restart.
            {mutation.error && (
                <div className="mt-3">
                    <PluginError error={mutation.error} />
                </div>
            )}
        </ConfirmDialog>
    )
}

export function RemovePluginDialog({
    plugin,
    onClose,
}: {
    plugin: Plugin
    onClose: () => void
}) {
    const remove = useRemovePlugin()
    const toast = useToast()
    const label = pluginLabel(plugin)
    // The backend refused because workflows use the plugin; the next
    // confirmation is the explicit "remove anyway".
    const inUse =
        remove.error instanceof ApiError &&
        remove.error.code === 'PLUGIN_IN_USE' &&
        Array.isArray(remove.error.details['workflows'])
    const close = () => {
        remove.reset()
        onClose()
    }
    return (
        <ConfirmDialog
            open
            title={`Remove “${label}”?`}
            confirmLabel={inUse ? 'Remove anyway' : 'Remove plugin'}
            irreversible
            pending={remove.isPending}
            onCancel={close}
            onConfirm={() => {
                remove.mutate(
                    { pluginId: plugin.plugin_id, force: inUse },
                    {
                        onSuccess: ({ affected_workflows }) => {
                            toast.notify({
                                tone: 'success',
                                title: `Plugin “${label}” removed`,
                                ...(affected_workflows.length > 0 && {
                                    description: `${String(affected_workflows.length)} workflow(s) now reference missing node types.`,
                                }),
                            })
                            close()
                        },
                    },
                )
            }}
        >
            The installed package <code>{plugin.plugin_id}</code> is deleted
            from the engine. Workflow data is kept.
            {inUse &&
                ' Removing it anyway breaks the workflows below until it is installed again.'}
            {remove.error && (
                <div className="mt-3">
                    <PluginError error={remove.error} />
                </div>
            )}
        </ConfirmDialog>
    )
}
