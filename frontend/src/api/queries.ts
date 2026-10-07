import {
    infiniteQueryOptions,
    queryOptions,
    type InfiniteData,
} from '@tanstack/react-query'
import { listAccessTokens } from './auth'
import type { ApiClient } from './client'
import {
    getCredential,
    listCredentialNames,
    listCredentials,
} from './credentials'
import {
    getExecution,
    getExecutionStatusCounts,
    listExecutionEvents,
    listExecutionLogs,
    listExecutions,
    listWorkflowExecutions,
    type ExecutionListParams,
    type LogFilter,
    type LogPage,
    type WorkflowExecutionListParams,
    listPendingInputs,
} from './executions'
import { listNodeDefinitions, validateNodeConfig } from './nodes'
import { listPlugins } from './plugins'
import {
    getWorkflow,
    getWorkflowVersion,
    listWorkflows,
    listWorkflowVersions,
    validateWorkflow,
    type WorkflowListParams,
} from './workflows'
import { getMonitoringSummary, type MonitoringWindow } from './monitoring'
import { getExecutionReport } from './reports'
import type { JsonObject, PageParams } from './schemas'
import { getWorkflowSharing } from './sharing'
import { getEngineLimits, getSystemStatus, listLlmProviders } from './system'
import { getUser, listUsers, type UserListParams } from './users'

/**
 * Every server-state query key. Keys nest by ownership so invalidating a
 * prefix reaches everything below it: `workflows.detail(id)` covers the
 * workflow's versions and executions, `executions.detail(id)` its events
 * and report. Every execution write invalidates `executions.overview()`:
 * the cross-workflow listings and status counts.
 */
export const queryKeys = {
    workflows: {
        lists: () => ['workflows', 'list'] as const,
        list: (params: WorkflowListParams) =>
            ['workflows', 'list', params] as const,
        detail: (workflowId: string) => ['workflows', workflowId] as const,
        versions: (workflowId: string, page: PageParams) =>
            ['workflows', workflowId, 'versions', 'list', page] as const,
        version: (workflowId: string, version: number) =>
            ['workflows', workflowId, 'versions', version] as const,
        executions: (workflowId: string) =>
            ['workflows', workflowId, 'executions'] as const,
        /** Owner, members and the caller's access (below the workflow, so
         * a workflow refresh refreshes it too). */
        sharing: (workflowId: string) =>
            ['workflows', workflowId, 'members'] as const,
        /** The backend's verdict on one draft revision. */
        validation: (workflowId: string, revision: number) =>
            ['workflows', workflowId, 'validation', revision] as const,
        executionList: (
            workflowId: string,
            params: WorkflowExecutionListParams,
        ) => ['workflows', workflowId, 'executions', params] as const,
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
        inputs: (executionId: string) =>
            ['executions', executionId, 'inputs'] as const,
        logs: (executionId: string, filter: LogFilter) =>
            ['executions', executionId, 'logs', filter] as const,
    },
    nodes: () => ['nodes'] as const,
    /** Below `nodes()`: a catalog refresh re-checks configs too. */
    nodeConfigValidation: (nodeType: string, config: JsonObject) =>
        ['nodes', nodeType, 'config-validation', config] as const,
    system: () => ['system', 'status'] as const,
    limits: () => ['system', 'limits'] as const,
    llmProviders: () => ['system', 'llm-providers'] as const,
    /** The signed-in session (features/auth). Survives a user switch;
     * everything else is dropped then. */
    session: () => ['auth', 'session'] as const,
    /** Whether the installation still needs its first admin (login page). */
    setupStatus: () => ['auth', 'setup'] as const,
    monitoring: (window: MonitoringWindow) =>
        ['monitoring', 'summary', window] as const,
    plugins: () => ['plugins'] as const,
    // Not under 'auth': these belong to one user and must be dropped on a
    // user switch like other server state (features/auth/session.ts).
    accessTokens: () => ['access-tokens'] as const,
    users: {
        lists: () => ['users', 'list'] as const,
        list: (params: UserListParams) => ['users', 'list', params] as const,
        detail: (id: string) => ['users', 'detail', id] as const,
    },
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
export const workflowQueries = {
    list: (client: ApiClient, params: WorkflowListParams = {}) =>
        queryOptions({
            queryKey: queryKeys.workflows.list(params),
            queryFn: ({ signal }) => listWorkflows(client, params, signal),
        }),
    detail: (client: ApiClient, workflowId: string) =>
        queryOptions({
            queryKey: queryKeys.workflows.detail(workflowId),
            queryFn: ({ signal }) => getWorkflow(client, workflowId, signal),
        }),
    versions: (client: ApiClient, workflowId: string, page: PageParams = {}) =>
        queryOptions({
            queryKey: queryKeys.workflows.versions(workflowId, page),
            queryFn: ({ signal }) =>
                listWorkflowVersions(client, workflowId, page, signal),
        }),
    version: (client: ApiClient, workflowId: string, version: number) =>
        queryOptions({
            queryKey: queryKeys.workflows.version(workflowId, version),
            queryFn: ({ signal }) =>
                getWorkflowVersion(client, workflowId, version, signal),
            // Published versions are immutable: never stale.
            staleTime: Infinity,
        }),
    /** Validates the stored draft at `revision` (a POST without side
     * effects); a draft changed since is a 409, never another draft's
     * verdict. */
    validation: (client: ApiClient, workflowId: string, revision: number) =>
        queryOptions({
            queryKey: queryKeys.workflows.validation(workflowId, revision),
            queryFn: () => validateWorkflow(client, workflowId, revision),
            // One revision's verdict changes only with the installed node
            // code, like a config verdict; not re-POSTed on every focus.
            staleTime: INSTALLATION_STALE_TIME_MS,
        }),
    sharing: (client: ApiClient, workflowId: string) =>
        queryOptions({
            queryKey: queryKeys.workflows.sharing(workflowId),
            queryFn: ({ signal }) =>
                getWorkflowSharing(client, workflowId, signal),
        }),
    executions: (
        client: ApiClient,
        workflowId: string,
        params: WorkflowExecutionListParams = {},
    ) =>
        queryOptions({
            queryKey: queryKeys.workflows.executionList(workflowId, params),
            queryFn: ({ signal }) =>
                listWorkflowExecutions(client, workflowId, params, signal),
        }),
}

export const executionQueries = {
    /** Across workflows, newest first (the caller's workflows; all for an admin). */
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
    /** What the execution's waiting nodes ask for. */
    inputs: (client: ApiClient, executionId: string) =>
        queryOptions({
            queryKey: queryKeys.executions.inputs(executionId),
            queryFn: ({ signal }) =>
                listPendingInputs(client, executionId, signal),
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
            queryFn: ({ client: cache, queryKey, signal, pageParam }) => {
                // A refetch (each live update) reloads every loaded page.
                // Lines are append-only, written in position order by one
                // writer per execution: a full page never changes, so only
                // the tail is requested again.
                const data =
                    cache.getQueryData<InfiniteData<LogPage, number>>(queryKey)
                const cached = data?.pages[data.pageParams.indexOf(pageParam)]
                if (cached?.has_more) return cached
                return listExecutionLogs(
                    client,
                    executionId,
                    { ...filter, after: pageParam, limit: LOG_PAGE_SIZE },
                    signal,
                )
            },
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
    /** Names for pickers. Under the list key: credential changes refresh it. */
    names: (client: ApiClient) =>
        queryOptions({
            queryKey: [...queryKeys.credentials.lists(), 'names'] as const,
            queryFn: ({ signal }) =>
                listCredentialNames(client, { limit: 100 }, signal),
        }),
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

export const userQueries = {
    list: (client: ApiClient, params: UserListParams = {}) =>
        queryOptions({
            queryKey: queryKeys.users.list(params),
            queryFn: ({ signal }) => listUsers(client, params, signal),
        }),
    detail: (client: ApiClient, id: string) =>
        queryOptions({
            queryKey: queryKeys.users.detail(id),
            queryFn: ({ signal }) => getUser(client, id, signal),
        }),
}

export const accessTokenQueries = {
    list: (client: ApiClient) =>
        queryOptions({
            queryKey: queryKeys.accessTokens(),
            queryFn: ({ signal }) => listAccessTokens(client, signal),
        }),
}

export const systemQueries = {
    status: (client: ApiClient) =>
        queryOptions({
            queryKey: queryKeys.system(),
            queryFn: ({ signal }) => getSystemStatus(client, signal),
        }),
    /** Operator settings: they change only with a restart. */
    limits: (client: ApiClient) =>
        queryOptions({
            queryKey: queryKeys.limits(),
            queryFn: ({ signal }) => getEngineLimits(client, signal),
            staleTime: INSTALLATION_STALE_TIME_MS,
        }),
    llmProviders: (client: ApiClient) =>
        queryOptions({
            queryKey: queryKeys.llmProviders(),
            queryFn: ({ signal }) => listLlmProviders(client, signal),
            staleTime: INSTALLATION_STALE_TIME_MS,
        }),
}
