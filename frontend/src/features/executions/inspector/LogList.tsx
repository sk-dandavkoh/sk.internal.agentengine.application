import { useInfiniteQuery } from '@tanstack/react-query'
import { useApiClient } from '../../../api/context'
import { errorMessage } from '../../../api/errors'
import {
    logLevelSchema,
    type LogFilter,
    type LogLevel,
    type LogLine,
} from '../../../api/executions'
import { executionQueries } from '../../../api/queries'
import {
    Alert,
    Badge,
    Button,
    ErrorState,
    LoadingState,
    Select,
    type Tone,
} from '../../../components/ui'
import { Time } from '../shared'
import { JsonBlock, UntrustedText } from './display'

const levelTone: Record<LogLevel, Tone> = {
    DEBUG: 'neutral',
    INFO: 'info',
    WARNING: 'warning',
    ERROR: 'danger',
    CRITICAL: 'danger',
}

function Line({
    line,
    nodeName,
}: {
    line: LogLine
    nodeName: ((nodeId: string) => string) | undefined
}) {
    const fields = Object.keys(line.fields).length > 0
    return (
        <li className="flex flex-col gap-1 border-b border-border py-2 last:border-b-0">
            <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
                {/* The level is text, not only color. */}
                <Badge tone={levelTone[line.level]}>{line.level}</Badge>
                <Time iso={line.logged_at} />
                {nodeName && <span>{nodeName(line.node_id)}</span>}
                <span>attempt {String(line.attempt)}</span>
            </div>
            <p className="font-mono text-xs">
                <UntrustedText text={line.message} />
            </p>
            {fields && (
                <details className="text-xs">
                    <summary className="focus-ring cursor-pointer text-muted">
                        Fields
                    </summary>
                    <JsonBlock value={line.fields} />
                </details>
            )}
        </li>
    )
}

/**
 * Node log lines from the backend, oldest first, loaded a page at a time.
 * Live runs refetch them with the execution (its query key prefix).
 */
export function LogList({
    executionId,
    filter,
    nodeName,
}: {
    executionId: string
    filter: LogFilter
    /** Names each line's node; omit when all lines are of one node. */
    nodeName?: (nodeId: string) => string
}) {
    const query = useInfiniteQuery(
        executionQueries.logs(useApiClient(), executionId, filter),
    )
    if (query.data === undefined) {
        if (query.isError)
            return (
                <ErrorState
                    title="Could not load the logs"
                    titleAs="h3"
                    description={errorMessage(query.error)}
                    action={
                        <Button size="sm" onClick={() => void query.refetch()}>
                            Retry
                        </Button>
                    }
                />
            )
        return <LoadingState label="Loading logs…" />
    }
    const lines = query.data.pages.flatMap((p) => p.items)
    return (
        <div className="flex flex-col gap-3">
            {query.isError && (
                <Alert tone="warning">
                    Could not load more log lines; showing earlier data.{' '}
                    {errorMessage(query.error)}
                </Alert>
            )}
            {lines.length === 0 ? (
                <p className="text-sm text-muted">
                    {filter.level || filter.q
                        ? 'No log lines match the filters.'
                        : 'No log lines were recorded.'}
                </p>
            ) : (
                <ol aria-label="Log lines" className="flex flex-col">
                    {lines.map((line) => (
                        <Line
                            key={line.position}
                            line={line}
                            nodeName={nodeName}
                        />
                    ))}
                </ol>
            )}
            {query.hasNextPage && (
                <Button
                    size="sm"
                    className="self-start"
                    loading={query.isFetchingNextPage}
                    onClick={() => void query.fetchNextPage()}
                >
                    Load more lines
                </Button>
            )}
        </div>
    )
}

/** A minimum level picker for a log list. */
export function LevelFilter({
    value,
    onChange,
}: {
    value: LogLevel | undefined
    onChange: (level: LogLevel | undefined) => void
}) {
    return (
        <Select
            label="Minimum level"
            value={value ?? 'all'}
            onChange={(e) => {
                const parsed = logLevelSchema.safeParse(e.target.value)
                onChange(parsed.success ? parsed.data : undefined)
            }}
        >
            <option value="all">All levels</option>
            {logLevelSchema.options.map((level) => (
                <option key={level} value={level}>
                    {level} and above
                </option>
            ))}
        </Select>
    )
}
