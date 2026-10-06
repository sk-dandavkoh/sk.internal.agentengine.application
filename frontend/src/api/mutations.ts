import {
    useMutation,
    useQueryClient,
    type QueryClient,
} from '@tanstack/react-query'
import {
    createAccessToken,
    revokeAccessToken,
    type CreateAccessTokenInput,
} from './auth'
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
    executeWorkflowVersion,
    invokeTrigger,
    respondToInput,
} from './executions'
import {
    createWorkflow,
    deleteWorkflow,
    duplicateWorkflow,
    importWorkflow,
    publishWorkflow,
    restoreWorkflowVersion,
    updateWorkflow,
    validateWorkflow,
    type CreateWorkflowInput,
    type UpdateWorkflowInput,
} from './workflows'
import {
    disablePlugin,
    enablePlugin,
    installPlugin,
    removePlugin,
} from './plugins'
import { queryKeys } from './queries'
import type { JsonObject } from './schemas'
import {
    leaveWorkflow,
    putWorkflowMember,
    removeWorkflowMember,
    transferWorkflowOwnership,
    type WorkflowRole,
} from './sharing'
import {
    createUser,
    updateUser,
    type CreateUserInput,
    type UpdateUserInput,
} from './users'

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

// -- workflows ----------------------------------------------------------------

export function useCreateWorkflow() {
    return useApiMutation(
        (client, input: CreateWorkflowInput) => createWorkflow(client, input),
        (qc) => invalidate(qc, queryKeys.workflows.lists()),
    )
}

/** Creates a new draft-only workflow from an uploaded package. */
export function useImportWorkflow() {
    return useApiMutation(
        (client, file: Blob) => importWorkflow(client, file),
        (qc) => invalidate(qc, queryKeys.workflows.lists()),
    )
}

/** Saves the draft; a stale `expected_revision` fails with a conflict. */
export function useUpdateWorkflow() {
    return useApiMutation(
        (client, v: { workflowId: string; input: UpdateWorkflowInput }) =>
            updateWorkflow(client, v.workflowId, v.input),
        (qc, workflow) => {
            // The response is the whole saved workflow: no refetch needed.
            qc.setQueryData(queryKeys.workflows.detail(workflow.id), workflow)
            return invalidate(qc, queryKeys.workflows.lists())
        },
    )
}

/** Copies the draft, or published `version` when given. */
export function useDuplicateWorkflow() {
    return useApiMutation(
        (client, v: { workflowId: string; name: string; version?: number }) =>
            duplicateWorkflow(client, v.workflowId, v.name, v.version),
        (qc) => invalidate(qc, queryKeys.workflows.lists()),
    )
}

/** Replaces the draft with a version's copy; versions are untouched. */
export function useRestoreWorkflowVersion() {
    return useApiMutation(
        (
            client,
            v: {
                workflowId: string
                version: number
                expectedRevision: number
            },
        ) =>
            restoreWorkflowVersion(
                client,
                v.workflowId,
                v.version,
                v.expectedRevision,
            ),
        (qc, workflow) => {
            qc.setQueryData(queryKeys.workflows.detail(workflow.id), workflow)
            return invalidate(qc, queryKeys.workflows.lists())
        },
    )
}

export function useDeleteWorkflow() {
    return useApiMutation(
        (client, workflowId: string) => deleteWorkflow(client, workflowId),
        (qc, _result, workflowId) => {
            qc.removeQueries({
                queryKey: queryKeys.workflows.detail(workflowId),
            })
            return invalidate(qc, queryKeys.workflows.lists())
        },
    )
}

/** Changes nothing on the server, so it invalidates nothing. */
export function useValidateWorkflow() {
    return useApiMutation(
        (client, v: { workflowId: string; expectedRevision?: number }) =>
            validateWorkflow(client, v.workflowId, v.expectedRevision),
        () => Promise.resolve(),
    )
}

export function usePublishWorkflow() {
    return useApiMutation(
        (client, v: { workflowId: string; expectedRevision?: number }) =>
            publishWorkflow(client, v.workflowId, v.expectedRevision),
        // Status, current version, revision and the version list change.
        (qc, _version, v) =>
            invalidate(
                qc,
                queryKeys.workflows.detail(v.workflowId),
                queryKeys.workflows.lists(),
            ),
    )
}

// -- executions ----------------------------------------------------------------

export function useExecuteWorkflowVersion() {
    return useApiMutation(
        (
            client,
            v: { workflowId: string; version: number; input?: JsonObject },
        ) => executeWorkflowVersion(client, v.workflowId, v.version, v.input),
        (qc, execution) =>
            invalidate(
                qc,
                queryKeys.workflows.executions(execution.workflow_id),
                queryKeys.executions.overview(),
            ),
    )
}

export function useInvokeTrigger() {
    return useApiMutation(
        (
            client,
            v: { workflowId: string; triggerId: string; input?: JsonObject },
        ) => invokeTrigger(client, v.workflowId, v.triggerId, v.input),
        (qc, execution) =>
            invalidate(
                qc,
                queryKeys.workflows.executions(execution.workflow_id),
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
                queryKeys.workflows.executions(execution.workflow_id),
                queryKeys.executions.overview(),
            ),
    )
}

/** Answers a waiting node. No optimistic update: the server decides
 * (validation, a concurrent answer, a cancelled run). */
export function useRespondToInput() {
    return useApiMutation(
        (
            client,
            v: { executionId: string; nodeId: string; response: JsonObject },
        ) => respondToInput(client, v.executionId, v.nodeId, v.response),
        (qc, execution) =>
            invalidate(
                qc,
                // Prefix: the execution, its inputs, events and report.
                queryKeys.executions.detail(execution.id),
                queryKeys.workflows.executions(execution.workflow_id),
                queryKeys.executions.overview(),
            ),
    )
}

// -- access tokens -------------------------------------------------------------

export function useCreateAccessToken() {
    return useApiMutation(
        (client, input: CreateAccessTokenInput) =>
            createAccessToken(client, input),
        (qc) => invalidate(qc, queryKeys.accessTokens()),
        // The variables hold the password and the result the token value.
        { gcTime: 0 },
    )
}

export function useRevokeAccessToken() {
    return useApiMutation(
        (client, id: string) => revokeAccessToken(client, id),
        (qc) => invalidate(qc, queryKeys.accessTokens()),
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

// -- sharing -------------------------------------------------------------------

export function usePutWorkflowMember() {
    return useApiMutation(
        (
            client,
            v: { workflowId: string; username: string; role: WorkflowRole },
        ) => putWorkflowMember(client, v.workflowId, v.username, v.role),
        (qc, _member, v) =>
            invalidate(qc, queryKeys.workflows.sharing(v.workflowId)),
    )
}

export function useRemoveWorkflowMember() {
    return useApiMutation(
        (client, v: { workflowId: string; username: string }) =>
            removeWorkflowMember(client, v.workflowId, v.username),
        (qc, _result, v) =>
            invalidate(qc, queryKeys.workflows.sharing(v.workflowId)),
    )
}

/** The workflow's owner changes (and with it the caller's access): the
 * workflow and everything below it is refetched, and so are the lists. */
export function useTransferWorkflowOwnership() {
    return useApiMutation(
        (client, v: { workflowId: string; username: string }) =>
            transferWorkflowOwnership(client, v.workflowId, v.username),
        (qc, _sharing, v) =>
            invalidate(
                qc,
                queryKeys.workflows.detail(v.workflowId),
                queryKeys.workflows.lists(),
            ),
    )
}

/** After leaving, the workflow is unreachable: its cached state goes and
 * the lists are refetched without it. */
export function useLeaveWorkflow() {
    return useApiMutation(
        (client, workflowId: string) => leaveWorkflow(client, workflowId),
        (qc, _result, workflowId) => {
            qc.removeQueries({
                queryKey: queryKeys.workflows.detail(workflowId),
            })
            return invalidate(
                qc,
                queryKeys.workflows.lists(),
                queryKeys.executions.overview(),
            )
        },
    )
}

// -- users ---------------------------------------------------------------------

// The variables may hold a password: dropped as soon as nobody observes.
export function useCreateUser() {
    return useApiMutation(
        (client, input: CreateUserInput) => createUser(client, input),
        (qc) => invalidate(qc, queryKeys.users.lists()),
        secretOptions,
    )
}

/** Refetches the session too: the edited user may be the signed-in one
 * (display name), and one cheap request beats comparing ids here. */
export function useUpdateUser() {
    return useApiMutation(
        (client, v: { id: string; input: UpdateUserInput }) =>
            updateUser(client, v.id, v.input),
        (qc, user) => {
            qc.setQueryData(queryKeys.users.detail(user.id), user)
            return invalidate(qc, queryKeys.users.lists(), queryKeys.session())
        },
        secretOptions,
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
