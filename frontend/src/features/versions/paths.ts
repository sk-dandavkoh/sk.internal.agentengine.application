export const versionsPath = (workflowId: string) =>
    `/workflows/${encodeURIComponent(workflowId)}/versions`

export const versionPath = (workflowId: string, version: number) =>
    `${versionsPath(workflowId)}/${String(version)}`
