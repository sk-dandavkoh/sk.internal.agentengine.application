import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useApiClient } from '../../api/context'
import { errorMessage } from '../../api/errors'
import type { NodeDefinition } from '../../api/nodes'
import type { NodeInstance } from '../../api/workflows'
import { nodeQueries } from '../../api/queries'
import type { JsonObject } from '../../api/schemas'
import { Alert, Badge } from '../../components/ui'
import { PropertyFields } from './ConfigForm'
import { useDebounced, VALIDATION_DELAY_MS } from './useDebounced'
import { describePath, issuesByPath, properties } from './configSchema'

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
            className="flex flex-col gap-3"
        >
            <h3 id="node-inspector" className="font-semibold">
                Settings
            </h3>

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
                            `${String(issues.length)} ${issues.length === 1 ? 'setting needs' : 'settings need'} attention before the workflow can be published.`
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
                            Nothing to set up: this node works as it is.
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
