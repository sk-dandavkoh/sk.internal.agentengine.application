import type { Page } from '@playwright/test'
import type { WorkflowSharing } from '../../src/api/sharing'
import { expect, test } from './fixtures'
import { stubSession } from './session'

// The workflow lifecycle in a real browser (native <dialog>, focus, layout).
// The backend is an in-memory fake at the network boundary that enforces
// the rules the UI must not bypass: revisions and "published workflows
// cannot be deleted".
const AT = '2026-10-02T08:00:00Z'

interface FakeWorkflow {
    id: string
    name: string
    description: string
    status: string
    owner_id: string
    revision: number
    current_version: number
    created_at: string
    updated_at: string
    draft: { graph: { nodes: []; edges: [] }; triggers: []; dependencies: [] }
    files: []
}

function fakeWorkflow(
    id: string,
    name: string,
    published = false,
): FakeWorkflow {
    return {
        id,
        name,
        description: '',
        status: published ? 'published' : 'draft',
        owner_id: 'user_1',
        revision: 1,
        current_version: published ? 1 : 0,
        created_at: AT,
        updated_at: AT,
        draft: {
            graph: { nodes: [], edges: [] },
            triggers: [],
            dependencies: [],
        },
        files: [],
    }
}

const fail = (status: number, code: string, message: string) => ({
    status,
    json: { code, message, details: {}, request_id: 'r' },
})

async function fakeBackend(p: Page, initial: FakeWorkflow[] = []) {
    const workflows = new Map(initial.map((x) => [x.id, x]))
    let next = 1
    await p.route('**/api/v1/**', async (route) => {
        const request = route.request()
        const url = new URL(request.url())
        const [, id, action] =
            /^\/api\/v1\/workflows(?:\/([^/]+))?(?:\/([^/]+))?$/.exec(
                url.pathname,
            ) ?? []
        const method = request.method()
        const body = (request.postDataJSON() ?? {}) as Record<string, unknown>
        const name = typeof body.name === 'string' ? body.name.trim() : ''
        const reply = (r: { status?: number; json?: unknown }) =>
            route.fulfill(r)
        const workflow = id ? workflows.get(id) : undefined

        if (!url.pathname.startsWith('/api/v1/workflows'))
            return reply(fail(404, 'NOT_FOUND', 'Not found.'))
        if (!id && method === 'GET') {
            const q = (url.searchParams.get('q') ?? '').toLowerCase()
            // Detail-only fields are extra here; the client ignores them.
            const items = [...workflows.values()].filter((x) =>
                x.name.toLowerCase().includes(q),
            )
            return reply({
                json: {
                    items,
                    page: { offset: 0, limit: 25, has_more: false },
                },
            })
        }
        if ((!id || action === 'duplicate') && method === 'POST') {
            if (!name)
                return reply({
                    status: 422,
                    json: {
                        code: 'VALIDATION_ERROR',
                        message: 'Request validation failed.',
                        details: { 'body.name': 'Enter a name.' },
                        request_id: 'r',
                    },
                })
            const created = fakeWorkflow(`wf_${String(next++)}`, name)
            workflows.set(created.id, created)
            return reply({ status: 201, json: created })
        }
        if (!workflow)
            return reply(fail(404, 'WORKFLOW_NOT_FOUND', 'Workflow not found.'))
        // The stubbed session user is an admin: manage access, no members.
        if (action === 'members' && method === 'GET')
            return reply({
                json: {
                    access: 'manage',
                    owner: {
                        user_id: workflow.owner_id,
                        username: 'owner',
                        display_name: null,
                    },
                    members: [],
                } satisfies WorkflowSharing,
            })
        if (method === 'GET') return reply({ json: workflow })
        if (method === 'PUT') {
            if (body.expected_revision !== workflow.revision)
                return reply(
                    fail(409, 'CONCURRENT_MODIFICATION', 'Reload and retry.'),
                )
            Object.assign(workflow, {
                name,
                description: body.description,
                revision: workflow.revision + 1,
            })
            return reply({ json: workflow })
        }
        if (method === 'DELETE') {
            if (workflow.current_version > 0)
                return reply(
                    fail(
                        409,
                        'WORKFLOW_HAS_VERSIONS',
                        'A workflow with published versions cannot be deleted.',
                    ),
                )
            workflows.delete(workflow.id)
            return reply({ status: 204 })
        }
        return reply(fail(405, 'METHOD_NOT_ALLOWED', 'Not allowed.'))
    })
    await stubSession(p)
}

test('create, open, rename, duplicate and delete in a real browser', async ({
    page: p,
}) => {
    await fakeBackend(p)
    await p.goto('/workflows')
    await expect(
        p.getByRole('heading', { name: 'No workflows yet' }),
    ).toBeVisible()

    // Create: a blank name is refused by the backend and shown at the field.
    await p.getByRole('button', { name: 'New workflow' }).first().click()
    const dialog = p.getByRole('dialog', { name: 'New workflow' })
    await expect(dialog.getByLabel('Name')).toBeFocused()
    await p.keyboard.type('   ')
    await p.keyboard.press('Enter')
    await expect(dialog.getByText('Enter a name.')).toBeVisible()
    await dialog.getByLabel('Name').fill('Invoice triage')
    await p.keyboard.press('Enter')

    await expect(
        p.getByRole('heading', { level: 1, name: 'Invoice triage' }),
    ).toBeVisible()
    await expect(p).toHaveURL(/\/workflows\/wf_1$/)

    // Rename.
    await p.getByRole('button', { name: 'Edit details' }).click()
    const edit = p.getByRole('dialog', { name: 'Edit workflow details' })
    await edit.getByLabel('Name').fill('Invoice triage v2')
    await edit.getByRole('button', { name: 'Save' }).click()
    await expect(
        p.getByRole('heading', { level: 1, name: 'Invoice triage v2' }),
    ).toBeVisible()
    await expect(edit).toBeHidden()

    // Duplicate opens the copy.
    await p.getByRole('button', { name: 'Duplicate' }).click()
    await p
        .getByRole('dialog', { name: 'Duplicate workflow' })
        .getByRole('button', { name: 'Duplicate' })
        .click()
    await expect(
        p.getByRole('heading', { level: 1, name: 'Copy of Invoice triage v2' }),
    ).toBeVisible()

    // Delete the copy: Escape cancels, focus returns to the trigger.
    const remove = p.getByRole('button', { name: 'Delete', exact: true })
    await remove.click()
    await p.keyboard.press('Escape')
    await expect(remove).toBeFocused()
    await remove.click()
    await p
        .getByRole('dialog', { name: 'Delete workflow?' })
        .getByRole('button', { name: 'Delete workflow' })
        .click()

    await expect(p).toHaveURL(/\/workflows$/)
    const table = p.getByRole('table', { name: 'Workflows' })
    await expect(table.getByRole('link')).toHaveText(['Invoice triage v2'])
})

test('the backend refuses to delete a published workflow', async ({
    page: p,
}) => {
    await fakeBackend(p, [fakeWorkflow('wf_p', 'Published one', true)])
    await p.goto('/workflows/wf_p')

    await p.getByRole('button', { name: 'Delete', exact: true }).click()
    const dialog = p.getByRole('dialog', { name: 'Delete workflow?' })
    await dialog.getByRole('button', { name: 'Delete workflow' }).click()

    await expect(
        dialog.getByText(
            'A workflow with published versions cannot be deleted.',
        ),
    ).toBeVisible()
    await expect(p).toHaveURL(/\/workflows\/wf_p$/)
})

test('mobile: list and detail never scroll sideways', async ({ page: p }) => {
    await p.setViewportSize({ width: 375, height: 812 })
    await fakeBackend(p, [
        fakeWorkflow('wf_l', 'A workflow with a rather long name for a phone'),
    ])
    for (const path of ['/workflows', '/workflows/wf_l']) {
        await p.goto(path)
        await expect(p.getByRole('heading', { level: 1 })).toBeVisible()
        await expect(
            p.getByText('A workflow with a rather long').first(),
        ).toBeVisible()
        // A string: this tsconfig has no DOM types; it runs in the page.
        const overflow = await p.evaluate<number>(
            'document.documentElement.scrollWidth - window.innerWidth',
        )
        expect(overflow).toBeLessThanOrEqual(0)
    }
})
