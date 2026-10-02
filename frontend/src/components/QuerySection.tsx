import type { UseQueryResult } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { errorMessage } from '../api/errors'
import { Alert, Button, ErrorState, LoadingState } from './ui'

interface QuerySectionProps<T> {
    query: UseQueryResult<T>
    /** What is loading, e.g. "recent executions". */
    subject: string
    children: (data: T) => ReactNode
}

/**
 * The loading, error and success states of one data-driven region. A failed
 * refresh keeps showing the last good data, marked as possibly stale.
 */
export function QuerySection<T>({
    query,
    subject,
    children,
}: QuerySectionProps<T>) {
    const retry = (
        <Button
            size="sm"
            loading={query.isFetching}
            onClick={() => void query.refetch()}
        >
            Retry
        </Button>
    )
    if (query.data !== undefined) {
        return (
            <>
                {query.isError && (
                    <Alert tone="warning" className="mb-3" action={retry}>
                        Could not refresh {subject}; showing earlier data.{' '}
                        {errorMessage(query.error)}
                    </Alert>
                )}
                {children(query.data)}
            </>
        )
    }
    if (query.isError) {
        return (
            <ErrorState
                title={`Could not load ${subject}`}
                titleAs="h3"
                description={errorMessage(query.error)}
                action={retry}
            />
        )
    }
    return <LoadingState label={`Loading ${subject}…`} />
}
