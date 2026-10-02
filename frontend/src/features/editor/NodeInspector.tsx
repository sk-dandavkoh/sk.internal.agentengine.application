import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { useApiClient } from '../../api/context'
import { errorMessage } from '../../api/errors'
import type { NodeDefinition } from '../../api/nodes'
import type { NodeInstance } from '../../api/projects'
import { nodeQueries } from '../../api/queries'
import type { JsonObject } from '../../api/schemas'
import { Alert, Badge } from '../../components/ui'
import { PropertyFields } from './ConfigForm'
import { describePath, issuesByPath, properties } from './configSchema'
import { nodeTitle } from './graphView'

/** Typing pauses this long before the backend checks the config. */
export const VALIDATION_DELAY_MS = 400

function useDebounced<T>(value: T, ms: number): T {
    const [settled, setSettled] = useState(value)
    useEffect(() => {
        const timer = setTimeout(() => {
            setSettled(value)
        }, ms)
        return () => {
            clearTimeout(timer)
        }
    }, [value, ms])
    return settled
}

/**
 * Settings of one node, generated from its config schema. Edits go into
 * the draft graph (saved with it, undoable); the backend checks each config
 * as it settles and publish checks it again, so its verdict is what counts.
 */
export function NodeInspector({
    node,
    definition,
    onConfigure,
}: {
    node: NodeInstance
    definition: NodeDefinition | undefined
    onConfigure: (config: JsonObject, field: string) => void
}) {
    const client = useApiClient()
    const config = useDebounced(node.config, VALIDATION_DELAY_MS)
    const check = useQuery({
        ...nodeQueries.configValidation(client, node.node_type, config),
        enabled: definition !== undefined,
        placeholderData: keepPreviousData,
    })
    const issues = check.data?.issues ?? []
    const fields = definition ? properties(definition.config_schema) : []
    const settling = config !== node.config || check.isFetching

    return (
        <section
            aria-labelledby="node-inspector"
            className="flex flex-col gap-3 rounded-md border border-border bg-surface p-3"
        >
            <div>
                <h2 id="node-inspector" className="font-semibold">
                    Settings: {nodeTitle(node, definition)}
                </h2>
                <p className="text-xs text-muted">
                    {node.node_type}
                    {definition && ` · v${definition.version}`}
                </p>
                {definition?.description && (
                    <p className="mt-1 text-sm text-muted">
                        {definition.description}
                    </p>
                )}
            </div>

            {!definition ? (
                <Alert tone="warning" title="Settings unavailable">
                    &quot;{node.node_type}&quot; is not in the node catalog, so
                    its settings cannot be shown. The configuration is kept as
                    it is.
                </Alert>
            ) : (
                <>
                    <p role="status" className="text-sm text-muted">
                        {settling ? (
                            'Checking configuration…'
                        ) : check.isError ? (
                            `Could not check the configuration: ${errorMessage(check.error)}`
                        ) : issues.length === 0 ? (
                            <Badge tone="success">Configuration valid</Badge>
                        ) : (
                            `${String(issues.length)} ${issues.length === 1 ? 'problem' : 'problems'}; publishing would be rejected.`
                        )}
                    </p>
                    {issues.length > 0 && (
                        <ul
                            aria-label="Configuration problems"
                            className="list-inside list-disc text-sm text-danger-text"
                        >
                            {issues.map((issue, i) => (
                                <li key={i}>
                                    {describePath(issue.path)}: {issue.message}
                                </li>
                            ))}
                        </ul>
                    )}
                    {fields.length === 0 &&
                    Object.keys(node.config).length === 0 ? (
                        <p className="text-sm text-muted">
                            This node has no settings.
                        </p>
                    ) : (
                        <PropertyFields
                            properties={fields}
                            value={node.config}
                            path={[]}
                            issues={issuesByPath(issues)}
                            onChange={onConfigure}
                        />
                    )}
                </>
            )}
        </section>
    )
}
