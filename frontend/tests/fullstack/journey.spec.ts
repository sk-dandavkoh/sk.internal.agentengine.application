import { readFileSync } from 'node:fs'
import { randomUUID } from 'node:crypto'
import type { APIRequestContext, Page } from '@playwright/test'
import { expect, test } from '../e2e/fixtures'
import { admin, adminApi, downloaded, unzip } from './session'

// The V1 user journey end to end, through the UI only, against the real
// backend and worker (playwright.fullstack.config.ts): sign in, build a
// project from nothing, publish, run, follow, inspect, measure, report,
// export, import and run the import. Each step builds on the last, so this
// is one test of steps; `api` only checks what the backend stored.

let api: APIRequestContext

test.beforeAll(async ({ baseURL }) => {
    if (!baseURL) throw new Error('baseURL is not configured')
    api = await adminApi(baseURL)
})

test.afterAll(async () => {
    await api.dispose()
})

interface StoredProject {
    id: string
    current_version: number
    draft: {
        graph: {
            nodes: { id: string; node_type: string; config: object }[]
            edges: { source_node_id: string; target_node_id: string }[]
        }
        triggers: { id: string; trigger_type: string }[]
    }
}

async function stored(projectId: string): Promise<StoredProject> {
    const response = await api.get(`/api/v1/projects/${projectId}`)
    expect(response.status()).toBe(200)
    return (await response.json()) as StoredProject
}

async function addNode(p: Page, type: string, name: string) {
    const library = p.getByRole('region', { name: 'Node library' })
    await library.getByRole('searchbox', { name: 'Search nodes' }).fill(type)
    await library.getByRole('button', { name: `Add ${name} to graph` }).click()
}

async function connect(p: Page, from: string, to: string) {
    await p
        .getByRole('region', { name: /^Nodes/ })
        .getByRole('button', { name: new RegExp(`^${from}`) })
        .click()
    const panel = p.getByRole('region', {
        name: new RegExp(`^Connections: ${from}`),
    })
    await panel
        .getByRole('combobox', { name: 'To input' })
        .selectOption({ label: `${to} › value` })
    await panel.getByRole('button', { name: 'Connect', exact: true }).click()
}

async function validateAndPublish(p: Page, version: number) {
    const panel = p.getByRole('region', { name: 'Validate and publish' })
    await panel.getByRole('button', { name: 'Validate' }).click()
    await expect(panel.getByText('Valid', { exact: true })).toBeVisible()
    await panel.getByRole('button', { name: 'Publish…' }).click()
    await p
        .getByRole('dialog', { name: `Publish version ${String(version)}?` })
        .getByRole('button', { name: `Publish version ${String(version)}` })
        .click()
    await expect(
        p.getByText(`Version ${String(version)} is published`),
    ).toBeVisible()
}

/** Runs the project's published manual trigger from its row; returns the
 * execution id once the page shows it completed. */
async function runTrigger(p: Page, projectId: string): Promise<string> {
    await p.goto(`/projects/${projectId}/triggers`)
    const row = p
        .getByRole('table', { name: 'Triggers' })
        .getByRole('row')
        .filter({ hasText: 'Manual' })
    await expect(row.getByText('Published')).toBeVisible()
    await row.getByRole('button', { name: /^Run trigger/ }).click()
    await expect(p).toHaveURL(/\/executions\/exec_[0-9a-f]{32}$/)
    const executionId = p.url().split('/').pop() ?? ''
    await expect(
        p.getByRole('heading', { name: 'Execution', level: 1 }),
    ).toBeVisible()
    await expect(p.getByText('Completed').first()).toBeVisible()
    return executionId
}

test('the V1 journey: build, publish, run, inspect, report, export, import, run again', async ({
    browser,
}) => {
    test.setTimeout(180_000)
    const context = await browser.newContext()
    const p = await context.newPage()
    const name = `Journey ${randomUUID().slice(0, 8)}`
    const message = `journey-log-${randomUUID().slice(0, 8)}`
    let projectId = ''
    let executionId = ''
    let packagePath = ''

    await test.step('login, landing on the dashboard', async () => {
        await p.goto('/dashboard')
        await expect(p).toHaveURL('/login')
        await p.getByLabel(/^Username/).fill(admin().username)
        await p.getByLabel(/^Password/).fill(admin().password)
        await p.getByRole('button', { name: 'Sign in' }).click()
        await expect(p).toHaveURL('/dashboard')
        await expect(
            p.getByRole('heading', { level: 1, name: 'Dashboard' }),
        ).toBeVisible()
        await expect(
            p.getByRole('heading', { name: 'Execution outcomes' }),
        ).toBeVisible()
    })

    await test.step('create the project and open it', async () => {
        await p
            .getByRole('navigation', { name: 'Main' })
            .getByRole('link', { name: 'Projects' })
            .click()
        await p.getByRole('button', { name: 'New project' }).first().click()
        const dialog = p.getByRole('dialog', { name: 'New project' })
        await dialog.getByLabel('Name').fill(name)
        await dialog.getByRole('button', { name: 'Create project' }).click()
        await expect(p.getByRole('heading', { level: 1, name })).toBeVisible()
        projectId = /\/projects\/([^/]+)$/.exec(p.url())?.[1] ?? ''
        expect((await stored(projectId)).draft.graph.nodes).toEqual([])
    })

    await test.step('edit the draft: add, configure and connect nodes', async () => {
        await p.getByRole('link', { name: 'Open editor' }).click()
        await addNode(p, 'core.input', 'Input')
        await addNode(p, 'core.log', 'Log')
        const inspector = p.getByRole('region', { name: /^Settings: Log/ })
        // The backend judges the config: a Log needs a message.
        await expect(
            inspector.getByText(/publishing would be rejected/),
        ).toBeVisible()
        await inspector.getByRole('textbox', { name: /^Message/ }).fill(message)
        await expect(inspector.getByText('Configuration valid')).toBeVisible()
        await addNode(p, 'core.output', 'Output')
        await connect(p, 'Input', 'Log')
        await connect(p, 'Log', 'Output')
        await expect(p.locator('.react-flow__edge')).toHaveCount(2)
    })

    await test.step('save the graph', async () => {
        await p.keyboard.press('ControlOrMeta+s')
        await expect(p.getByText('All changes saved to draft')).toBeVisible()
        const { graph } = (await stored(projectId)).draft
        const typeOf = (id: string) =>
            graph.nodes.find((n) => n.id === id)?.node_type ?? '?'
        expect(
            graph.edges.map(
                (e) =>
                    `${typeOf(e.source_node_id)}>${typeOf(e.target_node_id)}`,
            ),
        ).toEqual(['core.input>core.log', 'core.log>core.output'])
        expect(
            graph.nodes.find((n) => n.node_type === 'core.log')?.config,
        ).toEqual({ message })
    })

    await test.step('configure a manual trigger', async () => {
        await p.goto(`/projects/${projectId}/triggers`)
        await p.getByRole('button', { name: 'New trigger' }).click()
        const dialog = p.getByRole('dialog', { name: 'New trigger' })
        await dialog.getByLabel('Type').selectOption('manual')
        await dialog.getByRole('button', { name: 'Create trigger' }).click()
        const row = p
            .getByRole('table', { name: 'Triggers' })
            .getByRole('row')
            .filter({ hasText: 'Manual' })
        await expect(row.getByText('Not published')).toBeVisible()
        expect(
            (await stored(projectId)).draft.triggers.map((t) => t.trigger_type),
        ).toEqual(['manual'])
    })

    await test.step('validate and publish', async () => {
        await p.goto(`/projects/${projectId}/editor`)
        await validateAndPublish(p, 1)
        expect((await stored(projectId)).current_version).toBe(1)
    })

    await test.step('trigger a run and follow it live to its end', async () => {
        // Observed, not altered: the page's real stream from the backend.
        const frames: string[] = []
        p.on('websocket', (ws) => {
            ws.on('framereceived', ({ payload }) => {
                frames.push(String(payload))
            })
        })
        executionId = await runTrigger(p, projectId)
        const activity = p.getByRole('list', { name: 'Live activity' })
        await expect(activity.getByText('Execution completed')).toBeVisible()
        await expect(p.getByText('Live updates finished')).toBeVisible()
        expect(frames.some((f) => f.includes(executionId))).toBe(true)
        const execution = (await (
            await api.get(`/api/v1/executions/${executionId}`)
        ).json()) as { status: string; trigger_type: string; version: number }
        expect(execution).toMatchObject({
            status: 'completed',
            trigger_type: 'manual',
            version: 1,
        })
    })

    await test.step('inspect the execution and its logs', async () => {
        await p.getByRole('tab', { name: 'Nodes' }).click()
        await expect(p.getByText('Completed')).not.toHaveCount(0)
        await p.getByRole('tab', { name: 'Logs' }).click()
        const lines = p.getByRole('list', { name: 'Log lines' })
        await expect(lines.getByText(message)).toBeVisible()
        await p.getByLabel('Message contains').fill(message)
        await p.getByRole('button', { name: 'Search' }).click()
        await expect(lines.getByText(message)).toBeVisible()
        await p.getByRole('tab', { name: 'Graph' }).click()
        const graph = p.getByRole('figure', {
            name: 'Execution graph (read-only)',
        })
        await expect(graph.getByText('Completed')).toHaveCount(3)
    })

    await test.step('view the metrics', async () => {
        const summary = p.waitForResponse(
            (r) => r.url().includes('/api/v1/monitoring/summary') && r.ok(),
        )
        await p
            .getByRole('navigation', { name: 'Main' })
            .getByRole('link', { name: 'Monitoring' })
            .click()
        const body = (await (await summary).json()) as {
            nodes: {
                node_type: string
                metrics: { node_status_counts: Record<string, number> }
            }[]
        }
        // The page shows the backend's numbers, and they include this run.
        const logged =
            body.nodes.find((n) => n.node_type === 'core.log')?.metrics
                .node_status_counts.completed ?? 0
        expect(logged).toBeGreaterThanOrEqual(1)
        const row = p
            .getByRole('table', { name: 'Node performance by node type' })
            .getByRole('row')
            .filter({ hasText: 'core.log' })
        await expect(row.getByRole('cell').nth(1)).toHaveText(String(logged))
    })

    await test.step('view and download the report', async () => {
        await p.goto(`/executions/${executionId}`)
        await p.getByRole('link', { name: 'Report', exact: true }).click()
        await expect(
            p.getByRole('heading', { level: 1, name: 'Execution report' }),
        ).toBeVisible()
        await expect(p.getByText(executionId).first()).toBeVisible()
        const nodes = p.getByRole('table', { name: 'Nodes' })
        await expect(nodes.getByText('Completed')).toHaveCount(3)
        const { path } = await downloaded(p, () =>
            p.getByRole('button', { name: 'Download JSON' }).click(),
        )
        const report = JSON.parse(readFileSync(path, 'utf8')) as {
            execution_id: string
            execution: { status: string }
        }
        expect(report).toMatchObject({
            execution_id: executionId,
            execution: { status: 'completed' },
        })
    })

    await test.step('export the project', async () => {
        await p.goto(`/projects/${projectId}`)
        const { download, path } = await downloaded(p, () =>
            p.getByRole('button', { name: 'Export', exact: true }).click(),
        )
        expect(download.suggestedFilename()).toBe(
            `${projectId}-draft.akasha.zip`,
        )
        await expect(p.getByText('Project exported')).toBeVisible()
        const files = unzip(readFileSync(path))
        expect([...files.values()].join()).toContain(message)
        packagePath = path
    })

    await test.step('import it as a new project', async () => {
        await p.goto('/projects')
        await p.getByRole('button', { name: 'Import project' }).click()
        const dialog = p.getByRole('dialog', { name: 'Import project' })
        await dialog
            .getByLabel('Project package (.zip)')
            .setInputFiles(packagePath)
        await dialog
            .getByRole('button', { name: 'Import', exact: true })
            .click()
        await expect(dialog.getByText(`Imported “${name}”`)).toBeVisible()
        await expect(dialog.getByText('The draft is valid')).toBeVisible()
        await dialog.getByRole('button', { name: 'Open project' }).click()
        await expect(p.getByRole('heading', { level: 1, name })).toBeVisible()
        const importedId = /\/projects\/([^/]+)$/.exec(p.url())?.[1] ?? ''
        expect(importedId).not.toBe(projectId)
        const imported = await stored(importedId)
        expect(imported.current_version).toBe(0)
        expect(imported.draft).toEqual((await stored(projectId)).draft)
        projectId = importedId
    })

    await test.step('validate, publish and run the imported project', async () => {
        await p.getByRole('link', { name: 'Open editor' }).click()
        await validateAndPublish(p, 1)
        const rerun = await runTrigger(p, projectId)
        expect(rerun).not.toBe(executionId)
        await p.getByRole('tab', { name: 'Logs' }).click()
        await expect(
            p.getByRole('list', { name: 'Log lines' }).getByText(message),
        ).toBeVisible()
        const execution = (await (
            await api.get(`/api/v1/executions/${rerun}`)
        ).json()) as { status: string; project_id: string }
        expect(execution).toMatchObject({
            status: 'completed',
            project_id: projectId,
        })
    })

    await context.close()
})
