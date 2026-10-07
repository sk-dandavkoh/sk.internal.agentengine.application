import { readFileSync } from 'node:fs'
import { randomUUID } from 'node:crypto'
import type { APIRequestContext, Page } from '@playwright/test'
import { expect, test } from '../e2e/fixtures'
import { admin, adminApi, downloaded, unzip } from './session'

// The V1 user journey end to end, through the UI only, against the real
// backend and worker (playwright.fullstack.config.ts): sign in, build a
// workflow from nothing, publish, run, follow, inspect, measure, report,
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

interface StoredWorkflow {
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

async function stored(workflowId: string): Promise<StoredWorkflow> {
    const response = await api.get(`/api/v1/workflows/${workflowId}`)
    expect(response.status()).toBe(200)
    return (await response.json()) as StoredWorkflow
}

/** Adds a node; its dialog opens. Closed unless `keepOpen`. */
async function addNode(p: Page, type: string, name: string, keepOpen = false) {
    const library = p.getByRole('region', { name: 'Node library' })
    await library.getByRole('searchbox', { name: 'Search nodes' }).fill(type)
    await library.getByRole('button', { name: `Add ${name} to graph` }).click()
    const dialog = p.getByRole('dialog', { name })
    await expect(dialog).toBeVisible()
    if (!keepOpen) await dialog.getByRole('button', { name: 'Done' }).click()
    return dialog
}

async function connect(p: Page, from: string, to: string) {
    await p
        .getByRole('region', { name: /^Nodes/ })
        .getByRole('button', { name: new RegExp(`^${from}`) })
        .click()
    const dialog = p.getByRole('dialog', { name: from })
    await dialog
        .getByRole('combobox', { name: 'Send value to' })
        .selectOption({ label: `${to} › value` })
    await dialog.getByRole('button', { name: 'Done' }).click()
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

/** Runs the workflow's published manual trigger from its row; returns the
 * execution id once the page shows it completed. */
async function runTrigger(p: Page, workflowId: string): Promise<string> {
    await p.goto(`/workflows/${workflowId}/triggers`)
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
    let workflowId = ''
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

    await test.step('create the workflow and open it', async () => {
        await p
            .getByRole('navigation', { name: 'Main' })
            .getByRole('link', { name: 'Workflows' })
            .click()
        await p.getByRole('button', { name: 'New workflow' }).first().click()
        const dialog = p.getByRole('dialog', { name: 'New workflow' })
        await dialog.getByLabel('Name').fill(name)
        await dialog.getByRole('button', { name: 'Create workflow' }).click()
        await expect(p.getByRole('heading', { level: 1, name })).toBeVisible()
        workflowId = /\/workflows\/([^/]+)$/.exec(p.url())?.[1] ?? ''
        expect((await stored(workflowId)).draft.graph.nodes).toEqual([])
    })

    await test.step('edit the draft: add, configure and connect nodes', async () => {
        await p.getByRole('link', { name: 'Open editor' }).click()
        await addNode(p, 'core.input', 'Input')
        const dialog = await addNode(p, 'core.log', 'Log', true)
        const inspector = dialog.getByRole('region', { name: 'Settings' })
        // The backend judges the config: a Log needs a message.
        await expect(
            inspector.getByText(/before the workflow can be published/),
        ).toBeVisible()
        await inspector.getByRole('textbox', { name: /^Message/ }).fill(message)
        await expect(inspector.getByText('Configuration valid')).toBeVisible()
        await dialog.getByRole('button', { name: 'Done' }).click()
        await addNode(p, 'core.output', 'Output')
        await connect(p, 'Input', 'Log')
        await connect(p, 'Log', 'Output')
        await expect(p.locator('.react-flow__edge')).toHaveCount(2)
    })

    await test.step('save the graph', async () => {
        await p.keyboard.press('ControlOrMeta+s')
        await expect(p.getByText('All changes saved to draft')).toBeVisible()
        const { graph } = (await stored(workflowId)).draft
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
        await p.goto(`/workflows/${workflowId}/triggers`)
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
            (await stored(workflowId)).draft.triggers.map(
                (t) => t.trigger_type,
            ),
        ).toEqual(['manual'])
    })

    await test.step('validate and publish', async () => {
        await p.goto(`/workflows/${workflowId}/editor`)
        await validateAndPublish(p, 1)
        expect((await stored(workflowId)).current_version).toBe(1)
    })

    await test.step('trigger a run and follow it live to its end', async () => {
        // Observed, not altered: the page's real stream from the backend.
        const frames: string[] = []
        p.on('websocket', (ws) => {
            ws.on('framereceived', ({ payload }) => {
                frames.push(String(payload))
            })
        })
        executionId = await runTrigger(p, workflowId)
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

        // A node opens in place: what reached it, from where, and its output.
        await graph.getByText('Log', { exact: true }).click()
        const node = p.getByRole('dialog', { name: 'Log' })
        await expect(
            node
                .getByRole('region', { name: 'Input' })
                .getByText(/from Input › value/),
        ).toBeVisible()
        await expect(
            node.getByRole('region', { name: 'Output' }).getByText('value'),
        ).toBeVisible()
        await node.getByRole('button', { name: 'Close' }).click()
    })

    await test.step('test one node with the input of the last run', async () => {
        await p.goto(`/workflows/${workflowId}/editor`)
        await p
            .getByRole('region', { name: /^Nodes/ })
            .getByRole('button', { name: /^Log/ })
            .click()
        const dialog = p.getByRole('dialog', { name: 'Log' })
        await dialog
            .getByRole('button', { name: 'Use the input of the last run' })
            .click()
        await dialog.getByRole('button', { name: 'Run test' }).click()
        const result = dialog.getByRole('region', { name: 'Test result' })
        await expect(result.getByText('Succeeded')).toBeVisible()
        await expect(result.getByText(message)).toBeVisible() // its log line
        // A test is not a run: still exactly the one execution.
        const runs = (await (
            await api.get(`/api/v1/workflows/${workflowId}/executions`)
        ).json()) as { items: unknown[] }
        expect(runs.items).toHaveLength(1)
        await dialog.getByRole('button', { name: 'Done' }).click()
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

    await test.step('export the workflow', async () => {
        await p.goto(`/workflows/${workflowId}`)
        const { download, path } = await downloaded(p, () =>
            p.getByRole('button', { name: 'Export', exact: true }).click(),
        )
        expect(download.suggestedFilename()).toBe(
            `${workflowId}-draft.akasha.zip`,
        )
        await expect(p.getByText('Workflow exported')).toBeVisible()
        const files = unzip(readFileSync(path))
        expect([...files.values()].join()).toContain(message)
        packagePath = path
    })

    await test.step('import it as a new workflow', async () => {
        await p.goto('/workflows')
        await p.getByRole('button', { name: 'Import workflow' }).click()
        const dialog = p.getByRole('dialog', { name: 'Import workflow' })
        await dialog
            .getByLabel('Workflow package (.zip)')
            .setInputFiles(packagePath)
        await dialog
            .getByRole('button', { name: 'Import', exact: true })
            .click()
        await expect(dialog.getByText(`Imported “${name}”`)).toBeVisible()
        await expect(dialog.getByText('The draft is valid')).toBeVisible()
        await dialog.getByRole('button', { name: 'Open workflow' }).click()
        await expect(p.getByRole('heading', { level: 1, name })).toBeVisible()
        const importedId = /\/workflows\/([^/]+)$/.exec(p.url())?.[1] ?? ''
        expect(importedId).not.toBe(workflowId)
        const imported = await stored(importedId)
        expect(imported.current_version).toBe(0)
        expect(imported.draft).toEqual((await stored(workflowId)).draft)
        workflowId = importedId
    })

    await test.step('validate, publish and run the imported workflow', async () => {
        await p.getByRole('link', { name: 'Open editor' }).click()
        await validateAndPublish(p, 1)
        const rerun = await runTrigger(p, workflowId)
        expect(rerun).not.toBe(executionId)
        await p.getByRole('tab', { name: 'Logs' }).click()
        await expect(
            p.getByRole('list', { name: 'Log lines' }).getByText(message),
        ).toBeVisible()
        const execution = (await (
            await api.get(`/api/v1/executions/${rerun}`)
        ).json()) as { status: string; workflow_id: string }
        expect(execution).toMatchObject({
            status: 'completed',
            workflow_id: workflowId,
        })
    })

    await context.close()
})
