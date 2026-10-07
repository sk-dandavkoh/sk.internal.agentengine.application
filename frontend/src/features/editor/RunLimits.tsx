import { useQuery } from '@tanstack/react-query'
import { useApiClient } from '../../api/context'
import { systemQueries } from '../../api/queries'
import { TextLink } from '../../components/ui'
import { EngineLimitsList } from '../settings/EngineLimits'

/** The limits people ask about while building, beside the canvas; the
 * full list is in Settings. Nothing shows while they load or fail: they
 * are a help here, not the page's subject. */
export function RunLimits() {
    const limits = useQuery(systemQueries.limits(useApiClient())).data
    if (!limits) return null
    return (
        <details className="rounded-md border border-border bg-surface p-3">
            <summary className="focus-ring cursor-pointer rounded font-semibold">
                Run limits
            </summary>
            <div className="mt-2 flex flex-col gap-2">
                <EngineLimitsList
                    limits={limits}
                    only={[
                        'execution_timeout_seconds',
                        'node_timeout_seconds',
                        'llm_timeout_seconds',
                        'node_max_attempts',
                        'graph_max_nodes',
                    ]}
                />
                <TextLink to="/settings">All limits</TextLink>
            </div>
        </details>
    )
}
