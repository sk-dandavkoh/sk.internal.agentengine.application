export const versionsPath = (projectId: string) =>
    `/projects/${encodeURIComponent(projectId)}/versions`

export const versionPath = (projectId: string, version: number) =>
    `${versionsPath(projectId)}/${String(version)}`
