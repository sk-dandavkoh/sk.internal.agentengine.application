import { randomUUID } from 'node:crypto'
import type { APIRequestContext, Page } from '@playwright/test'
import { expect, test } from '../e2e/fixtures'
import { adminApi, signIn } from './session'

// Human-in-the-loop through the UI against the real backend and worker:
// the core node portfolio's nodes are in the library, a Human Input node is
// placed, configured and connected, the published run stops WAITING, the
// execution page shows the question, the answer sent from its generated
// form resumes the run, and the approved branch completes. `api` only
// checks what the backend stored.

let api: APIRequestContext

test.beforeAll(async ({ baseURL }) => {
    if (!baseURL) throw new Error('baseURL is not configured')
    api = await adminApi(baseURL)
})

test.afterAll(async () => {
    await api.dispose()
})

const library = (p: Page) => p.getByRole('region', { name: 'Node library' })

async function addNode(p: Page, type: string, name: string) {
    await library(p).getByRole('searchbox', { name: 'Search nodes' }).fill(type)
    await library(p)
        .getByRole('button', { name: `Add ${name} to graph` })
        .click()
}

async function connect(p: Page, from: string, output: string, to: string) {
    await p
        .getByRole('region', { name: /^Nodes/ })
        .getByRole('button', { name: new RegExp(`^${from}`) })
        .click()
    const panel = p.getByRole('region', {
        name: new RegExp(`^Connections: ${from}`),
    })
    await panel
        .getByRole('combobox', { name: 'From output' })
        .selectOption(output)
    await panel
        .getByRole('combobox', { name: 'To input' })
        .selectOption({ label: `${to} › value` })
    await panel.getByRole('button', { name: 'Connect', exact: true }).click()
}

test('a human approval stops the run, the answer from the UI resumes it', async ({
    page: p,
}) => {
    test.setTimeout(120_000)
    await signIn(p)
    const name = `Approval ${randomUUID().slice(0, 8)}`
    const workflow = (await (
        await api.post('/api/v1/workflows', { data: { name } })
    ).json()) as { id: string }

    await test.step('the new core nodes are in the library', async () => {
        await p.goto(`/workflows/${workflow.id}/editor`)
        for (const [type, label] of [
            ['core.human', 'Human Input'],
            ['core.transform', 'Transform'],
            ['core.database', 'Database Query'],
            ['core.browser', 'Browser'],
        ] as const) {
            await library(p)
                .getByRole('searchbox', { name: 'Search nodes' })
                .fill(type)
            await expect(
                library(p).getByRole('button', {
                    name: `Add ${label} to graph`,
                }),
            ).toBeVisible()
        }
    })

    await test.step('place, configure and connect a Human Input node', async () => {
        await addNode(p, 'core.input', 'Input')
        await addNode(p, 'core.human', 'Human Input')
        const inspector = p.getByRole('region', {
            name: /^Settings: Human Input/,
        })
        await expect(
            inspector.getByText(/publishing would be rejected/),
        ).toBeVisible() // the backend requires a prompt
        await inspector
            .getByRole('textbox', { name: /^Prompt/ })
            .fill('Approve the order for {{ value.customer }}?')
        await expect(inspector.getByText('Configuration valid')).toBeVisible()
        await addNode(p, 'core.output', 'Output')
        await connect(p, 'Input', 'value', 'Human Input')
        await connect(p, 'Human Input', 'approved', 'Output')
        await p.keyboard.press('ControlOrMeta+s')
        await expect(p.getByText('All changes saved to draft')).toBeVisible()
    })

    await test.step('publish it with a manual trigger', async () => {
        const current = (await (
            await api.get(`/api/v1/workflows/${workflow.id}`)
        ).json()) as { revision: number; name: string; draft: object }
        const updated = await api.put(`/api/v1/workflows/${workflow.id}`, {
            data: {
                expected_revision: current.revision,
                name: current.name,
                draft: {
                    ...current.draft,
                    triggers: [
                        {
                            id: `trg_${randomUUID().replaceAll('-', '')}`,
                            trigger_type: 'manual',
                            config: {},
                        },
                    ],
                },
            },
        })
        expect(updated.status()).toBe(200)
        await p.reload()
        const panel = p.getByRole('region', { name: 'Validate and publish' })
        await panel.getByRole('button', { name: 'Validate' }).click()
        await expect(panel.getByText('Valid', { exact: true })).toBeVisible()
        await panel.getByRole('button', { name: 'Publish…' }).click()
        await p
            .getByRole('dialog', { name: 'Publish version 1?' })
            .getByRole('button', { name: 'Publish version 1' })
            .click()
        await expect(p.getByText('Version 1 is published')).toBeVisible()
    })

    const started = await api.post(
        `/api/v1/workflows/${workflow.id}/versions/1/execute`,
        { data: { customer: 'Ada' } },
    )
    expect(started.status()).toBe(201)
    const executionId = ((await started.json()) as { id: string }).id

    await test.step('the execution page shows the waiting question', async () => {
        await p.goto(`/executions/${executionId}`)
        const card = p
            .getByRole('heading', { name: 'Waiting for input' })
            .locator('xpath=ancestor::section[1]')
        await expect(card).toBeVisible({ timeout: 30_000 })
        await expect(card.getByText('Approve the order for Ada?')).toBeVisible()
        const stored = (await (
            await api.get(`/api/v1/executions/${executionId}`)
        ).json()) as { status: string }
        expect(stored.status).toBe('waiting')

        await card.getByLabel(/^Decision/).selectOption({ label: 'approve' })
        await card.getByLabel(/^Comment/).fill('Looks right')
        await card.getByRole('button', { name: 'Send answer' }).click()
        await expect(p.getByText('Answer sent')).toBeVisible()
    })

    await test.step('the answered run completes on the approved branch', async () => {
        await expect(p.getByText('Completed').first()).toBeVisible({
            timeout: 30_000,
        })
        const report = (await (
            await api.get(`/api/v1/reports/executions/${executionId}`)
        ).json()) as {
            execution: { status: string }
            nodes: { node_type: string; outputs: unknown }[]
        }
        expect(report.execution.status).toBe('completed')
        expect(
            report.nodes.find((n) => n.node_type === 'core.output')?.outputs,
        ).toEqual({ value: { customer: 'Ada' } })
        const human = report.nodes.find((n) => n.node_type === 'core.human')
        expect(human?.outputs).toMatchObject({
            approved: { customer: 'Ada' },
            response: { decision: 'approve', comment: 'Looks right' },
        })
    })
})
