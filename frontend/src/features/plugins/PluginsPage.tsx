import { useQuery } from '@tanstack/react-query'
import { useState, type ReactNode } from 'react'
import { useApiClient } from '../../api/context'
import { ApiError } from '../../api/errors'
import type { Plugin, PluginState } from '../../api/plugins'
import { pluginQueries } from '../../api/queries'
import { QuerySection } from '../../components/QuerySection'
import {
    Alert,
    Badge,
    Button,
    Card,
    EmptyState,
    ErrorState,
    PageHeader,
    type Tone,
} from '../../components/ui'
import { pluginLabel } from './label'
import {
    InstallPluginDialog,
    RemovePluginDialog,
    SetEnabledDialog,
} from './PluginDialogs'

// The page shows backend plugin state as-is: whether an action is legal,
// compatible or blocked is answered by the backend when it is attempted.
// Plugin metadata is untrusted text, rendered as plain text only.

const states: Record<PluginState, { label: string; tone: Tone }> = {
    enabled: { label: 'Enabled', tone: 'success' },
    disabled: { label: 'Disabled', tone: 'neutral' },
    failed: { label: 'Failed', tone: 'danger' },
}

// Why a plugin is FAILED, per stable code (backend plugins/registry.py).
const failures: Record<string, string> = {
    PLUGIN_MANIFEST_INVALID:
        'Its manifest (akasha-plugin.json) is missing or invalid, so nothing about it can be trusted or loaded.',
    PLUGIN_INCOMPATIBLE:
        'It is not compatible with this engine or SDK, a plugin it depends on is not enabled, or its dependencies form a cycle.',
    PLUGIN_LOAD_FAILED:
        'Its code raised an error while loading. The server log has the details.',
    PLUGIN_DEPENDENCIES_FAILED:
        'Its Python libraries could not be installed or conflict with libraries already loaded. The server log has the details.',
}

type Action =
    | { kind: 'install' }
    | { kind: 'enable' | 'disable' | 'remove'; plugin: Plugin }

function Term({ term, children }: { term: string; children: ReactNode }) {
    return (
        <div className="flex flex-col gap-0.5">
            <dt className="text-xs font-medium tracking-wide text-muted uppercase">
                {term}
            </dt>
            <dd className="text-sm break-words">{children}</dd>
        </div>
    )
}

function PluginCard({
    plugin,
    onAction,
}: {
    plugin: Plugin
    onAction: (action: Action) => void
}) {
    const m = plugin.metadata
    const state = states[plugin.state]
    const label = pluginLabel(plugin)
    // Enabling a FAILED plugin retries its activation; without readable
    // metadata the backend refuses, so the button is not offered.
    const canEnable = plugin.state !== 'enabled' && m !== null
    return (
        <Card
            title={label}
            description={
                <span className="flex flex-wrap items-center gap-2">
                    <code>{plugin.plugin_id}</code>
                    {m && <span>v{m.version}</span>}
                    <Badge tone={state.tone} dot>
                        {state.label}
                    </Badge>
                </span>
            }
            actions={
                <div className="flex flex-wrap gap-2">
                    {plugin.state === 'enabled' ? (
                        <Button
                            size="sm"
                            aria-label={`Disable ${label}`}
                            onClick={() => {
                                onAction({ kind: 'disable', plugin })
                            }}
                        >
                            Disable
                        </Button>
                    ) : (
                        canEnable && (
                            <Button
                                size="sm"
                                aria-label={`Enable ${label}`}
                                onClick={() => {
                                    onAction({ kind: 'enable', plugin })
                                }}
                            >
                                {plugin.state === 'failed'
                                    ? 'Retry enable'
                                    : 'Enable'}
                            </Button>
                        )
                    )}
                    <Button
                        size="sm"
                        variant="danger"
                        aria-label={`Remove ${label}`}
                        onClick={() => {
                            onAction({ kind: 'remove', plugin })
                        }}
                    >
                        Remove
                    </Button>
                </div>
            }
        >
            <div className="flex flex-col gap-4 p-4">
                {plugin.state === 'failed' && (
                    <Alert
                        tone="danger"
                        title={`Not active: ${plugin.error ?? 'unknown error'}`}
                    >
                        {(plugin.error && failures[plugin.error]) ??
                            'The engine could not activate it at startup.'}
                    </Alert>
                )}
                {m?.description && (
                    <p className="text-sm break-words whitespace-pre-line">
                        {m.description}
                    </p>
                )}
                {m && (
                    <dl className="grid gap-4 sm:grid-cols-2">
                        <Term term="Engine compatibility">
                            <code>{m.engine_compatibility}</code>
                        </Term>
                        <Term term="SDK compatibility">
                            <code>{m.sdk_compatibility}</code>
                        </Term>
                        <Term term="Dependencies">
                            {m.dependencies.length === 0 ? (
                                <span className="text-muted">None</span>
                            ) : (
                                <ul>
                                    {m.dependencies.map((d) => (
                                        <li key={`${d.kind}:${d.name}`}>
                                            {d.kind} <code>{d.name}</code>{' '}
                                            <code>{d.version_constraint}</code>
                                        </li>
                                    ))}
                                </ul>
                            )}
                        </Term>
                        <Term term="Provides node types">
                            <ul>
                                {m.node_types.map((t) => (
                                    <li key={t}>
                                        <code>{t}</code>
                                    </li>
                                ))}
                            </ul>
                        </Term>
                    </dl>
                )}
            </div>
        </Card>
    )
}

export function PluginsPage() {
    const client = useApiClient()
    const query = useQuery(pluginQueries.list(client))
    const [action, setAction] = useState<Action | null>(null)
    const close = () => {
        setAction(null)
    }
    const forbidden =
        query.error instanceof ApiError && query.error.kind === 'authorization'
    const installEnabled = query.data?.install_enabled === true

    return (
        <>
            <PageHeader
                title="Plugins"
                description="Installed plugins and the node types they provide. Every change here is checked by the engine."
                actions={
                    installEnabled && (
                        <Button
                            variant="primary"
                            onClick={() => {
                                setAction({ kind: 'install' })
                            }}
                        >
                            Install plugin
                        </Button>
                    )
                }
            />
            {forbidden ? (
                <ErrorState
                    title="Admin access required"
                    description="Only admins can view and manage plugins. Plugin node types still appear in the editor's node library."
                />
            ) : (
                <div className="flex flex-col gap-4">
                    <Alert tone="warning" title="Plugins run with full access">
                        Plugins are trusted code. Enabled plugins run inside the
                        engine process, unsandboxed, with access to its data,
                        credentials and network. Changes made here reach
                        dedicated worker processes only after they restart.
                    </Alert>
                    <QuerySection query={query} subject="plugins">
                        {({ items, install_enabled }) => (
                            <>
                                {!install_enabled && (
                                    <Alert tone="info" title="Uploads are off">
                                        This installation does not accept plugin
                                        uploads. An operator installs plugins on
                                        the host with{' '}
                                        <code>
                                            akasha plugin install &lt;path&gt;
                                        </code>{' '}
                                        (or enables uploads with{' '}
                                        <code>AKASHA_PLUGINS_HTTP_INSTALL</code>
                                        ).
                                    </Alert>
                                )}
                                {items.length === 0 ? (
                                    <EmptyState
                                        title="No plugins installed"
                                        description="The built-in node types are available. Plugins add more."
                                    />
                                ) : (
                                    <ul
                                        aria-label="Installed plugins"
                                        className="flex flex-col gap-4"
                                    >
                                        {items.map((plugin) => (
                                            <li key={plugin.plugin_id}>
                                                <PluginCard
                                                    plugin={plugin}
                                                    onAction={setAction}
                                                />
                                            </li>
                                        ))}
                                    </ul>
                                )}
                            </>
                        )}
                    </QuerySection>
                </div>
            )}
            {action?.kind === 'install' && (
                <InstallPluginDialog onClose={close} />
            )}
            {(action?.kind === 'enable' || action?.kind === 'disable') && (
                <SetEnabledDialog
                    plugin={action.plugin}
                    enable={action.kind === 'enable'}
                    onClose={close}
                />
            )}
            {action?.kind === 'remove' && (
                <RemovePluginDialog plugin={action.plugin} onClose={close} />
            )}
        </>
    )
}
