import { infiniteQueryOptions, queryOptions } from '@tanstack/react-query'
import type { ApiClient } from './client'
import { getCredential, listCredentials } from './credentials'
import {
    getExecution,
    getExecutionStatusCounts,
    listExecutionEvents,
    listExecutionLogs,
    listExecutions,
    listProjectExecutions,
    type ExecutionListParams,
    type LogFilter,
    type ProjectExecutionListParams,
} from './executions'
import { listNodeDefinitions, validateNodeConfig } from './nodes'
import { listPlugins } from './plugins'
import {
    getProject,
    getProjectVersion,
    listProjects,
    listProjectVersions,
    type ProjectListParams,
} from './projects'
import { getMonitoringSummary, type MonitoringWindow } from './monitoring'
import { getExecutionReport } from './reports'
import type { JsonObject, PageParams } from './schemas'
import { getSystemStatus } from './system'

/**
 * Every server-state query key. Keys nest by ownership so invalidating a
 * prefix reaches everything below it: `projects.detail(id)` covers the
 * project's versions and executions, `executions.detail(id)` its events
 * and report. Every execution write invalidates `executions.overview()`:
 * the cross-project listings and status counts.
 */
export const queryKeys = {
    projects: {
        lists: () => ['projects', 'list'] as const,
        list: (params: ProjectListParams) =>
            ['projects', 'list', params] as const,
        detail: (projectId: string) => ['projects', projectId] as const,
        versions: (projectId: string, page: PageParams) =>
            ['projects', projectId, 'versions', 'list', page] as const,
        version: (projectId: string, version: number) =>
            ['projects', projectId, 'versions', version] as const,
        executions: (projectId: string) =>
            ['projects', projectId, 'executions'] as const,
        executionList: (
            projectId: string,
            params: ProjectExecutionListParams,
        ) => ['projects', projectId, 'executions', params] as const,
    },
    executions: {
        overview: () => ['executions', 'overview'] as const,
        list: (params: ExecutionListParams) =>
            ['executions', 'overview', 'list', params] as const,
        statusCounts: () =>
            ['executions', 'overview', 'status-counts'] as const,
        detail: (executionId: string) => ['executions', executionId] as const,
        events: (executionId: string, after: number) =>
            ['executions', executionId, 'events', after] as const,
        report: (executionId: string) =>
            ['executions', executionId, 'report'] as const,
        logs: (executionId: string, filter: LogFilter) =>
            ['executions', executionId, 'logs', filter] as const,
    },
    nodes: () => ['nodes'] as const,
    /** Below `nodes()`: a catalog refresh re-checks configs too. */
    nodeConfigValidation: (nodeType: string, config: JsonObject) =>
        ['nodes', nodeType, 'config-validation', config] as const,
    system: () => ['system', 'status'] as const,
    monitoring: (window: MonitoringWindow) =>
        ['monitoring', 'summary', window] as const,
    plugins: () => ['plugins'] as const,
    credentials: {
        lists: () => ['credentials', 'list'] as const,
        list: (page: PageParams) => ['credentials', 'list', page] as const,
        detail: (name: string) => ['credentials', 'detail', name] as const,
    },
}

// The node catalog and plugin list change only when the backend restarts
// with other code; refetching them on every mount would be noise.
export const INSTALLATION_STALE_TIME_MS = 5 * 60_000

/**
 * Query options per read endpoint, for `useQuery`, `useSuspenseQuery` or
 * route loaders (`queryClient.ensureQueryData`). Everything else keeps the
 * defaults of `createQueryClient`: stale immediately, refetched on mount.
 */
export const projectQueries = {
    list: (client: ApiClient, params: ProjectListParams = {}) =>
        queryOptions({
            queryKey: queryKeys.projects.list(params),
            queryFn: ({ signal }) => listProjects(client, params, signal),
        }),
    detail: (client: ApiClient, projectId: string) =>
        queryOptions({
            queryKey: queryKeys.projects.detail(projectId),
            queryFn: ({ signal }) => getProject(client, projectId, signal),
        }),
    versions: (client: ApiClient, projectId: string, page: PageParams = {}) =>
        queryOptions({
            queryKey: queryKeys.projects.versions(projectId, page),
            queryFn: ({ signal }) =>
                listProjectVersions(client, projectId, page, signal),
        }),
    version: (client: ApiClient, projectId: string, version: number) =>
        queryOptions({
            queryKey: queryKeys.projects.version(projectId, version),
            queryFn: ({ signal }) =>
                getProjectVersion(client, projectId, version, signal),
            // Published versions are immutable: never stale.
            staleTime: Infinity,
        }),
    executions: (
        client: ApiClient,
        projectId: string,
        params: ProjectExecutionListParams = {},
    ) =>
        queryOptions({
            queryKey: queryKeys.projects.executionList(projectId, params),
            queryFn: ({ signal }) =>
                listProjectExecutions(client, projectId, params, signal),
        }),
}

export const executionQueries = {
    /** Across projects, newest first (the caller's projects; all for an admin). */
    list: (client: ApiClient, params: ExecutionListParams = {}) =>
        queryOptions({
            queryKey: queryKeys.executions.list(params),
            queryFn: ({ signal }) => listExecutions(client, params, signal),
        }),
    statusCounts: (client: ApiClient) =>
        queryOptions({
            queryKey: queryKeys.executions.statusCounts(),
            queryFn: ({ signal }) => getExecutionStatusCounts(client, signal),
        }),
    detail: (client: ApiClient, executionId: string) =>
        queryOptions({
            queryKey: queryKeys.executions.detail(executionId),
            queryFn: ({ signal }) => getExecution(client, executionId, signal),
        }),
    /** One page of events after position `after` (0: from the start). */
    events: (client: ApiClient, executionId: string, after = 0) =>
        queryOptions({
            queryKey: queryKeys.executions.events(executionId, after),
            queryFn: ({ signal }) =>
                listExecutionEvents(client, executionId, { after }, signal),
        }),
    report: (client: ApiClient, executionId: string) =>
        queryOptions({
            queryKey: queryKeys.executions.report(executionId),
            queryFn: ({ signal }) =>
                getExecutionReport(client, executionId, signal),
        }),
    /** Node log lines, oldest first; each page continues after the last. */
    logs: (client: ApiClient, executionId: string, filter: LogFilter = {}) =>
        infiniteQueryOptions({
            queryKey: queryKeys.executions.logs(executionId, filter),
            queryFn: ({ signal, pageParam }) =>
                listExecutionLogs(
                    client,
                    executionId,
                    { ...filter, after: pageParam, limit: LOG_PAGE_SIZE },
                    signal,
                ),
            initialPageParam: 0,
            getNextPageParam: (last) =>
                last.has_more ? last.next_after : undefined,
        }),
}

// Lines per request: the backend's maximum page size.
export const LOG_PAGE_SIZE = 100

export const monitoringQueries = {
    /** A fixed window: the same window always means the same runs, so a
     * refresh is a new window (the page moves `until`), not a refetch. */
    summary: (client: ApiClient, window: MonitoringWindow) =>
        queryOptions({
            queryKey: queryKeys.monitoring(window),
            queryFn: ({ signal }) =>
                getMonitoringSummary(client, window, signal),
        }),
}

export const nodeQueries = {
    catalog: (client: ApiClient) =>
        queryOptions({
            queryKey: queryKeys.nodes(),
            queryFn: ({ signal }) => listNodeDefinitions(client, signal),
            staleTime: INSTALLATION_STALE_TIME_MS,
        }),
    /** A POST without side effects, cached by config: the same config of
     * the same node code gets the same verdict. */
    configValidation: (
        client: ApiClient,
        nodeType: string,
        config: JsonObject,
    ) =>
        queryOptions({
            queryKey: queryKeys.nodeConfigValidation(nodeType, config),
            queryFn: ({ signal }) =>
                validateNodeConfig(client, nodeType, config, signal),
            staleTime: INSTALLATION_STALE_TIME_MS,
        }),
}

export const pluginQueries = {
    list: (client: ApiClient) =>
        queryOptions({
            queryKey: queryKeys.plugins(),
            queryFn: ({ signal }) => listPlugins(client, signal),
            staleTime: INSTALLATION_STALE_TIME_MS,
        }),
}

export const credentialQueries = {
    list: (client: ApiClient, page: PageParams = {}) =>
        queryOptions({
            queryKey: queryKeys.credentials.list(page),
            queryFn: ({ signal }) => listCredentials(client, page, signal),
        }),
    detail: (client: ApiClient, name: string) =>
        queryOptions({
            queryKey: queryKeys.credentials.detail(name),
            queryFn: ({ signal }) => getCredential(client, name, signal),
        }),
}

export const systemQueries = {
    status: (client: ApiClient) =>
        queryOptions({
            queryKey: queryKeys.system(),
            queryFn: ({ signal }) => getSystemStatus(client, signal),
        }),
}
