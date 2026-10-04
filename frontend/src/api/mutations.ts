import {
    useMutation,
    useQueryClient,
    type QueryClient,
} from '@tanstack/react-query'
import type { ApiClient } from './client'
import { useApiClient } from './context'
import {
    createCredential,
    deleteCredential,
    updateCredential,
    type CreateCredentialInput,
    type UpdateCredentialInput,
} from './credentials'
import {
    cancelExecution,
    executeProjectVersion,
    invokeTrigger,
} from './executions'
import {
    createProject,
    deleteProject,
    duplicateProject,
    publishProject,
    restoreProjectVersion,
    updateProject,
    validateProject,
    type CreateProjectInput,
    type UpdateProjectInput,
} from './projects'
import {
    disablePlugin,
    enablePlugin,
    installPlugin,
    removePlugin,
} from './plugins'
import { queryKeys } from './queries'
import type { JsonObject } from './schemas'

// One hook per write. Each names the server state it changes; the mutation
// stays pending until that state is refetched, so callers never render the
// old data as if the write had not happened. No optimistic updates: the
// backend's answer is authoritative (AGENTS §85).

function useApiMutation<Variables, Result>(
    mutate: (client: ApiClient, variables: Variables) => Promise<Result>,
    affects: (
        queryClient: QueryClient,
        result: Result,
        variables: Variables,
    ) => Promise<unknown>,
    options: { gcTime?: number } = {},
) {
    const client = useApiClient()
    const queryClient = useQueryClient()
    return useMutation({
        mutationFn: (variables: Variables) => mutate(client, variables),
        onSuccess: (result, variables) =>
            affects(queryClient, result, variables),
        ...options,
    })
}

const invalidate = (
    queryClient: QueryClient,
    ...keys: (readonly unknown[])[]
) =>
    Promise.all(
        keys.map((queryKey) => queryClient.invalidateQueries({ queryKey })),
    )

// -- projects ----------------------------------------------------------------

export function useCreateProject() {
    return useApiMutation(
        (client, input: CreateProjectInput) => createProject(client, input),
        (qc) => invalidate(qc, queryKeys.projects.lists()),
    )
}

/** Saves the draft; a stale `expected_revision` fails with a conflict. */
export function useUpdateProject() {
    return useApiMutation(
        (client, v: { projectId: string; input: UpdateProjectInput }) =>
            updateProject(client, v.projectId, v.input),
        (qc, project) => {
            // The response is the whole saved project: no refetch needed.
            qc.setQueryData(queryKeys.projects.detail(project.id), project)
            return invalidate(qc, queryKeys.projects.lists())
        },
    )
}

/** Copies the draft, or published `version` when given. */
export function useDuplicateProject() {
    return useApiMutation(
        (client, v: { projectId: string; name: string; version?: number }) =>
            duplicateProject(client, v.projectId, v.name, v.version),
        (qc) => invalidate(qc, queryKeys.projects.lists()),
    )
}

/** Replaces the draft with a version's copy; versions are untouched. */
export function useRestoreProjectVersion() {
    return useApiMutation(
        (
            client,
            v: { projectId: string; version: number; expectedRevision: number },
        ) =>
            restoreProjectVersion(
                client,
                v.projectId,
                v.version,
                v.expectedRevision,
            ),
        (qc, project) => {
            qc.setQueryData(queryKeys.projects.detail(project.id), project)
            return invalidate(qc, queryKeys.projects.lists())
        },
    )
}

export function useDeleteProject() {
    return useApiMutation(
        (client, projectId: string) => deleteProject(client, projectId),
        (qc, _result, projectId) => {
            qc.removeQueries({ queryKey: queryKeys.projects.detail(projectId) })
            return invalidate(qc, queryKeys.projects.lists())
        },
    )
}

/** Changes nothing on the server, so it invalidates nothing. */
export function useValidateProject() {
    return useApiMutation(
        (client, v: { projectId: string; expectedRevision?: number }) =>
            validateProject(client, v.projectId, v.expectedRevision),
        () => Promise.resolve(),
    )
}

export function usePublishProject() {
    return useApiMutation(
        (client, v: { projectId: string; expectedRevision?: number }) =>
            publishProject(client, v.projectId, v.expectedRevision),
        // Status, current version, revision and the version list change.
        (qc, _version, v) =>
            invalidate(
                qc,
                queryKeys.projects.detail(v.projectId),
                queryKeys.projects.lists(),
            ),
    )
}

// -- executions ----------------------------------------------------------------

export function useExecuteProjectVersion() {
    return useApiMutation(
        (
            client,
            v: { projectId: string; version: number; input?: JsonObject },
        ) => executeProjectVersion(client, v.projectId, v.version, v.input),
        (qc, execution) =>
            invalidate(
                qc,
                queryKeys.projects.executions(execution.project_id),
                queryKeys.executions.overview(),
            ),
    )
}

export function useInvokeTrigger() {
    return useApiMutation(
        (
            client,
            v: { projectId: string; triggerId: string; input?: JsonObject },
        ) => invokeTrigger(client, v.projectId, v.triggerId, v.input),
        (qc, execution) =>
            invalidate(
                qc,
                queryKeys.projects.executions(execution.project_id),
                queryKeys.executions.overview(),
            ),
    )
}

export function useCancelExecution() {
    return useApiMutation(
        (client, v: { executionId: string; reason?: string }) =>
            cancelExecution(client, v.executionId, v.reason),
        (qc, execution) =>
            invalidate(
                qc,
                queryKeys.executions.detail(execution.id),
                queryKeys.projects.executions(execution.project_id),
                queryKeys.executions.overview(),
            ),
    )
}

// -- credentials ---------------------------------------------------------------

// Mutation variables carry the secret value: drop the finished mutation
// from the cache as soon as no component observes it (AGENTS §23).
const secretOptions = { gcTime: 0 }

export function useCreateCredential() {
    return useApiMutation(
        (client, input: CreateCredentialInput) =>
            createCredential(client, input),
        (qc) => invalidate(qc, queryKeys.credentials.lists()),
        secretOptions,
    )
}

export function useUpdateCredential() {
    return useApiMutation(
        (client, v: { name: string; input: UpdateCredentialInput }) =>
            updateCredential(client, v.name, v.input),
        (qc, credential) => {
            qc.setQueryData(
                queryKeys.credentials.detail(credential.name),
                credential,
            )
            return invalidate(qc, queryKeys.credentials.lists())
        },
        secretOptions,
    )
}

export function useDeleteCredential() {
    return useApiMutation(
        (client, name: string) => deleteCredential(client, name),
        (qc, _result, name) => {
            qc.removeQueries({ queryKey: queryKeys.credentials.detail(name) })
            return invalidate(qc, queryKeys.credentials.lists())
        },
    )
}

// -- plugins -------------------------------------------------------------------

// A plugin change adds or removes node types: the catalog is refetched too.
const pluginsChanged = (qc: QueryClient) =>
    invalidate(qc, queryKeys.plugins(), queryKeys.nodes())

export function useInstallPlugin() {
    return useApiMutation(
        (client, file: Blob) => installPlugin(client, file),
        pluginsChanged,
    )
}

export function useSetPluginEnabled() {
    return useApiMutation(
        (client, v: { pluginId: string; enabled: boolean }) =>
            v.enabled
                ? enablePlugin(client, v.pluginId)
                : disablePlugin(client, v.pluginId),
        pluginsChanged,
    )
}

export function useRemovePlugin() {
    return useApiMutation(
        (client, v: { pluginId: string; force?: boolean }) =>
            removePlugin(client, v.pluginId, { force: v.force ?? false }),
        pluginsChanged,
    )
}
