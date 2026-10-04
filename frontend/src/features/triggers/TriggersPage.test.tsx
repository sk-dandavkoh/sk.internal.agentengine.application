import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { z } from 'zod'
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
    apiError,
    ok,
    renderRoute,
    stubBackend,
    type Reply,
} from '../../test/app'

const PID = 'proj_1'
const AT = '2026-10-04T08:00:00Z'
const PATH = `/projects/${PID}/triggers`
// If it shows up anywhere on screen, a stored secret leaked.
const SECRET = 'sk-live-canary-1234'

const OTHER = 'proj_2'

const summary = (id: string, name: string, owner_id: string) => ({
    id,
    name,
    description: '',
    status: 'published',
    owner_id,
    revision: 1,
    current_version: 1,
    created_at: AT,
    updated_at: AT,
})

const hex = (n: number) => String(n).padStart(32, '0')
const tid = (n: number) => `trig_${hex(n)}`

interface TriggerJson {
    id: string
    trigger_type: string
    config: Record<string, unknown>
    enabled: boolean
}

const trigger = (
    n: number,
    trigger_type: string,
    config: Record<string, unknown> = {},
    enabled = true,
): TriggerJson => ({ id: tid(n), trigger_type, config, enabled })

interface Issue {
    code: string
    message: string
    trigger_id: string
}

const issue = (trigger_id: string, code: string, message: string) => ({
    code,
    message,
    trigger_id,
})

const putSchema = z.object({
    expected_revision: z.number(),
    name: z.string(),
    description: z.string(),
    draft: z.object({
        graph: z.unknown(),
        triggers: z.array(
            z.object({
                id: z.string(),
                trigger_type: z.string(),
                config: z.record(z.string(), z.unknown()),
                enabled: z.boolean(),
            }),
        ),
        dependencies: z.array(z.unknown()),
    }),
})

/**
 * A backend holding one project: PUT replaces the draft under optimistic
 * concurrency, validate answers for the stored revision. Overrides replace
 * single routes (errors).
 */
interface BackendOptions {
    triggers?: TriggerJson[]
    published?: TriggerJson[]
    validate?: (triggers: TriggerJson[]) => Issue[]
    overrides?: Record<string, () => Reply>
}

const jsonBody = (init: RequestInit): unknown =>
    typeof init.body === 'string' ? JSON.parse(init.body) : undefined

function backend({
    triggers = [],
    published,
    validate = () => [],
    overrides = {},
}: BackendOptions = {}) {
    const state = {
        revision: 3,
        triggers,
        puts: [] as z.infer<typeof putSchema>[],
    }
    const project = () => ({
        id: PID,
        name: 'Lead scoring',
        description: 'Scores leads',
        status: published ? 'published' : 'draft',
        owner_id: 'user_1',
        revision: state.revision,
        current_version: published ? 1 : 0,
        created_at: AT,
        updated_at: AT,
        draft: {
            graph: { nodes: [], edges: [] },
            triggers: state.triggers,
            dependencies: [],
        },
        files: [],
    })
    const requests = stubBackend({
        [`GET /projects/${PID}`]: () => ok(project()),
        [`GET /projects/${PID}/versions/1`]: () =>
            ok({
                id: 'pver_1',
                version: 1,
                description: '',
                schema_version: 1,
                published_at: AT,
                published_by: null,
                graph: { nodes: [], edges: [] },
                triggers: published ?? [],
                dependencies: [],
                files: [],
            }),
        [`PUT /projects/${PID}`]: (_url, init) => {
            const body = putSchema.parse(jsonBody(init))
            state.puts.push(body)
            if (body.expected_revision !== state.revision)
                return apiError(409, 'CONFLICT', 'The draft was changed.')
            state.triggers = body.draft.triggers
            state.revision += 1
            return ok(project())
        },
        [`POST /projects/${PID}/validate`]: (_url, init) => {
            const body = z
                .object({ expected_revision: z.number() })
                .parse(jsonBody(init))
            if (body.expected_revision !== state.revision)
                return apiError(409, 'CONFLICT', 'The draft was changed.')
            const issues = validate(state.triggers).map((i) => ({
                node_id: null,
                port: null,
                edge: null,
                ...i,
            }))
            return ok({ valid: issues.length === 0, issues })
        },
        'GET /projects': () =>
            ok({
                items: [
                    summary(PID, 'Lead scoring', 'user_1'),
                    summary(OTHER, 'CRM sync', 'user_1'),
                    summary('proj_9', 'Someone else', 'user_2'),
                ],
                page: { offset: 0, limit: 100, has_more: false },
            }),
        'GET /credentials': () =>
            ok({
                items: [
                    {
                        name: 'hook-key',
                        kind: 'generic',
                        description: 'Webhook signing',
                        reference: 'credential:hook-key',
                        created_at: AT,
                        updated_at: AT,
                    },
                ],
                page: { offset: 0, limit: 100, has_more: false },
            }),
        ...overrides,
    })
    return { state, requests }
}

const validates = (requests: URL[]) =>
    requests.filter((u) => u.pathname.endsWith('/validate')).length

async function table() {
    return screen.findByRole('table', { name: 'Triggers' })
}

async function rowOf(id: string) {
    const t = await table()
    const cell = await within(t).findByText(id)
    const row = cell.closest('tr')
    if (!row) throw new Error('no row')
    return row
}

async function openCreate() {
    fireEvent.click(await screen.findByRole('button', { name: 'New trigger' }))
    return screen.findByRole('dialog', { name: 'New trigger' })
}

function chooseType(dialog: HTMLElement, type: string) {
    fireEvent.change(within(dialog).getByLabelText(/^Type/), {
        target: { value: type },
    })
}

function submit(dialog: HTMLElement, name = 'Create trigger') {
    fireEvent.click(within(dialog).getByRole('button', { name }))
}

afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
})

describe('TriggersPage', () => {
    it('lists draft triggers with their backend state', async () => {
        const changed = trigger(2, 'interval', { interval_seconds: 60 })
        backend({
            triggers: [
                trigger(1, 'cron', { cron: '0 9 * * 1' }),
                changed,
                trigger(
                    3,
                    'webhook',
                    { secret_credential: 'credential:hook-key' },
                    false,
                ),
                trigger(4, 'event', { event: 'lead.created' }),
                trigger(5, 'event', {
                    event: 'execution.failed',
                    source_project_id: OTHER,
                }),
            ],
            published: [
                trigger(1, 'cron', { cron: '0 9 * * 1' }),
                { ...changed, config: { interval_seconds: 30 } },
                trigger(9, 'api'),
            ],
            validate: () => [
                issue(tid(4), 'invalid_trigger_event', 'event name is unknown'),
            ],
        })
        renderRoute(PATH)

        const cron = await rowOf(tid(1))
        expect(within(cron).getByText('Cron')).toBeVisible()
        expect(
            within(cron).getByText('Schedule: 0 9 * * 1 (UTC)'),
        ).toBeVisible()
        expect(within(cron).getByText('Enabled')).toBeVisible()
        expect(within(cron).getByText('Published')).toBeVisible()
        expect(await within(cron).findByText('Valid')).toBeVisible()

        const interval = await rowOf(tid(2))
        expect(within(interval).getByText('Every 60 s')).toBeVisible()
        expect(within(interval).getByText('Unpublished changes')).toBeVisible()

        const webhook = await rowOf(tid(3))
        expect(within(webhook).getByText('Disabled')).toBeVisible()
        expect(within(webhook).getByText('Not published')).toBeVisible()
        expect(
            within(webhook).getByText('Signed with credential:hook-key'),
        ).toBeVisible()
        expect(
            within(webhook).getByText(
                `POST http://localhost:3000/api/v1/webhooks/${PID}/${tid(3)}`,
                { exact: false },
            ),
        ).toBeVisible()
        expect(
            within(webhook).getByRole('button', {
                name: `Enable trigger ${tid(3)}`,
            }),
        ).toBeVisible()

        const event = await rowOf(tid(4))
        expect(
            within(event).getByText('On lead.created in this project'),
        ).toBeVisible()
        expect(
            within(await rowOf(tid(5))).getByText(
                `On execution.failed in project ${OTHER}`,
            ),
        ).toBeVisible()
        expect(
            await within(event).findByText('event name is unknown'),
        ).toBeVisible()

        // Deleted from the draft but still live in v1.
        expect(screen.getByText('Removed from the draft')).toBeVisible()
        expect(screen.getByText(tid(9))).toBeVisible()
    })

    it('never shows a webhook secret stored as plaintext', async () => {
        backend({
            triggers: [trigger(1, 'webhook', { secret_credential: SECRET })],
        })
        renderRoute(PATH)
        const row = await rowOf(tid(1))
        expect(
            within(row).getByText(
                'Signing secret is not a credential reference',
            ),
        ).toBeVisible()
        fireEvent.click(
            within(row).getByRole('button', { name: `Edit trigger ${tid(1)}` }),
        )
        const dialog = await screen.findByRole('dialog')
        expect(within(dialog).getByLabelText(/Signing secret/)).toHaveValue('')
        expect(document.body.innerHTML).not.toContain(SECRET)
    })

    it('shows the empty state for a project without triggers', async () => {
        backend()
        renderRoute(PATH)
        expect(await screen.findByText(/No triggers\./)).toBeVisible()
        expect(screen.getByText(/this project has none yet/)).toBeVisible()
    })

    it.each([
        ['manual', () => undefined, {}],
        ['api', () => undefined, {}],
        [
            'cron',
            (d: HTMLElement) => {
                fireEvent.change(within(d).getByLabelText(/Cron expression/), {
                    target: { value: ' */15 * * * * ' },
                })
            },
            { cron: '*/15 * * * *' },
        ],
        [
            'interval',
            (d: HTMLElement) => {
                fireEvent.change(within(d).getByLabelText(/Interval/), {
                    target: { value: '90' },
                })
            },
            { interval_seconds: 90 },
        ],
        [
            'webhook',
            (d: HTMLElement) => {
                fireEvent.change(within(d).getByLabelText(/Signing secret/), {
                    target: { value: 'hook-key' },
                })
            },
            { secret_credential: 'credential:hook-key' },
        ],
    ])(
        'creates a %s trigger and reloads its backend state',
        async (type, fill, config) => {
            const { state, requests } = backend({
                triggers: [trigger(1, 'manual')],
            })
            renderRoute(PATH)
            await rowOf(tid(1))
            await waitFor(() => {
                expect(validates(requests)).toBe(1)
            })

            const dialog = await openCreate()
            chooseType(dialog, type)
            fill(dialog)
            submit(dialog)

            await waitFor(() => {
                expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
            })
            const [put] = state.puts
            expect(put?.expected_revision).toBe(3)
            expect(put?.name).toBe('Lead scoring')
            expect(put?.description).toBe('Scores leads')
            const created = put?.draft.triggers[1]
            expect(put?.draft.triggers[0]).toEqual(trigger(1, 'manual'))
            expect(created?.trigger_type).toBe(type)
            expect(created?.config).toEqual(config)
            expect(created?.enabled).toBe(true)
            expect(created?.id).toMatch(/^trig_[0-9a-f]{32}$/)
            // What the table shows is the saved draft, re-validated at its new
            // revision.
            await rowOf(created?.id ?? '')
            await waitFor(() => {
                expect(validates(requests)).toBe(2)
            })
            expect(
                screen.getByText('Trigger created in the draft'),
            ).toBeVisible()
        },
    )

    it('creates an event trigger on another accessible project', async () => {
        const { state } = backend()
        renderRoute(PATH)
        const dialog = await openCreate()
        chooseType(dialog, 'event')
        const event = within(dialog).getByLabelText(/^Event/)
        expect(event).toHaveValue('execution.completed')
        fireEvent.change(event, { target: { value: 'execution.failed' } })
        const source = within(dialog).getByLabelText(/Source project/)
        // Every other project the caller can access; the backend applies
        // the owner's access when it fires.
        await within(source).findByRole('option', { name: 'CRM sync' })
        expect(
            within(source)
                .getAllByRole('option')
                .map((o) => o.textContent),
        ).toEqual(['This project', 'CRM sync', 'Someone else'])
        fireEvent.change(source, { target: { value: OTHER } })
        submit(dialog)

        await waitFor(() => {
            expect(state.triggers[0]?.config).toEqual({
                event: 'execution.failed',
                source_project_id: OTHER,
            })
        })
        expect(state.triggers[0]?.trigger_type).toBe('event')
    })

    it('switches an event trigger back to its own project', async () => {
        const { state } = backend({
            triggers: [
                trigger(1, 'event', {
                    event: 'node.completed',
                    source_project_id: 'proj_gone',
                }),
            ],
        })
        renderRoute(PATH)
        fireEvent.click(
            within(await rowOf(tid(1))).getByRole('button', {
                name: `Edit trigger ${tid(1)}`,
            }),
        )
        const dialog = await screen.findByRole('dialog')
        // Stored values the picker does not offer stay visible, not lost.
        expect(within(dialog).getByLabelText(/^Event/)).toHaveValue(
            'node.completed',
        )
        expect(within(dialog).getByLabelText(/Source project/)).toHaveValue(
            'proj_gone',
        )
        fireEvent.change(within(dialog).getByLabelText(/^Event/), {
            target: { value: 'project.published' },
        })
        fireEvent.change(within(dialog).getByLabelText(/Source project/), {
            target: { value: '' },
        })
        submit(dialog, 'Save trigger')
        await waitFor(() => {
            expect(state.triggers[0]?.config).toEqual({
                event: 'project.published',
            })
        })
    })

    it('runs a published manual trigger and opens the execution', async () => {
        const manual = trigger(1, 'manual')
        const { requests } = backend({
            triggers: [manual, trigger(2, 'manual'), trigger(3, 'api')],
            published: [
                manual,
                trigger(2, 'manual', {}, false),
                trigger(3, 'api'),
            ],
            overrides: {
                [`POST /projects/${PID}/triggers/${tid(1)}/invoke`]: () => ({
                    status: 201,
                    body: {
                        id: 'exec_1',
                        project_id: PID,
                        version: 1,
                        status: 'queued',
                        trigger_type: 'manual',
                        trigger_id: tid(1),
                        correlation_id: 'c',
                        created_at: AT,
                        started_at: null,
                        finished_at: null,
                        cancellable: true,
                    },
                }),
            },
        })
        const { router } = renderRoute(PATH)
        // Only where the published copy is enabled and manual.
        expect(
            within(await rowOf(tid(2))).queryByRole('button', { name: /^Run/ }),
        ).not.toBeInTheDocument()
        expect(
            within(await rowOf(tid(3))).queryByRole('button', { name: /^Run/ }),
        ).not.toBeInTheDocument()
        fireEvent.click(
            within(await rowOf(tid(1))).getByRole('button', {
                name: `Run trigger ${tid(1)}`,
            }),
        )
        await waitFor(() => {
            expect(router.state.location.pathname).toBe('/executions/exec_1')
        })
        expect(
            requests.filter((u) => u.pathname.endsWith('/invoke')),
        ).toHaveLength(1)
    })

    it('reports a refused run without leaving the page', async () => {
        const manual = trigger(1, 'manual')
        backend({
            triggers: [manual],
            published: [manual],
            overrides: {
                [`POST /projects/${PID}/triggers/${tid(1)}/invoke`]: () =>
                    apiError(409, 'TRIGGER_DISABLED', 'Trigger is disabled.'),
            },
        })
        const { router } = renderRoute(PATH)
        fireEvent.click(
            within(await rowOf(tid(1))).getByRole('button', {
                name: `Run trigger ${tid(1)}`,
            }),
        )
        expect(await screen.findByText('Trigger is disabled.')).toBeVisible()
        expect(router.state.location.pathname).toBe(PATH)
    })

    it('reports an invalid schedule from the backend after saving', async () => {
        backend({
            validate: (triggers) =>
                triggers
                    .filter((t) => t.trigger_type === 'cron')
                    .map((t) =>
                        issue(
                            t.id,
                            'invalid_trigger_schedule',
                            'cron expression is invalid or never fires',
                        ),
                    ),
        })
        renderRoute(PATH)
        const dialog = await openCreate()
        chooseType(dialog, 'cron')
        fireEvent.change(within(dialog).getByLabelText(/Cron expression/), {
            target: { value: '0 0 31 2 *' },
        })
        submit(dialog)
        expect(
            await screen.findByText(
                'cron expression is invalid or never fires',
            ),
        ).toBeVisible()
    })

    it('keeps the dialog open with the backend message when a config is rejected', async () => {
        const { state } = backend({
            overrides: {
                [`PUT /projects/${PID}`]: () =>
                    apiError(
                        422,
                        'DOMAIN_VALIDATION_ERROR',
                        "interval trigger requires positive, finite 'interval_seconds'",
                    ),
            },
        })
        renderRoute(PATH)
        const dialog = await openCreate()
        chooseType(dialog, 'interval')
        // Bypasses the browser's constraint check, as an older browser or a
        // crafted request would: the backend still refuses.
        const form = dialog.querySelector('form')
        if (!form) throw new Error('no form')
        fireEvent.submit(form)
        expect(
            await within(dialog).findByText(/requires positive, finite/),
        ).toBeVisible()
        expect(state.triggers).toEqual([])
    })

    it('requires a signing secret for webhooks before saving', async () => {
        const { state } = backend()
        renderRoute(PATH)
        const dialog = await openCreate()
        chooseType(dialog, 'webhook')
        const form = dialog.querySelector('form')
        if (!form) throw new Error('no form')
        fireEvent.submit(form)
        expect(
            await within(dialog).findByText(/Choose the credential/),
        ).toBeVisible()
        expect(state.puts).toHaveLength(0)
    })

    it('edits a trigger, keeping its id and unknown config keys', async () => {
        const { state } = backend({
            triggers: [
                trigger(1, 'cron', { cron: '0 9 * * *', note: 'keep' }),
                trigger(2, 'manual'),
            ],
        })
        renderRoute(PATH)
        const row = await rowOf(tid(1))
        fireEvent.click(
            within(row).getByRole('button', { name: `Edit trigger ${tid(1)}` }),
        )
        const dialog = await screen.findByRole('dialog', {
            name: 'Edit Cron trigger',
        })
        expect(within(dialog).queryByLabelText(/^Type/)).not.toBeInTheDocument()
        const input = within(dialog).getByLabelText(/Cron expression/)
        expect(input).toHaveValue('0 9 * * *')
        fireEvent.change(input, { target: { value: '30 6 * * 1-5' } })
        fireEvent.click(within(dialog).getByRole('switch', { name: /Enabled/ }))
        submit(dialog, 'Save trigger')

        await waitFor(() => {
            expect(state.triggers[0]).toEqual(
                trigger(
                    1,
                    'cron',
                    { cron: '30 6 * * 1-5', note: 'keep' },
                    false,
                ),
            )
        })
        expect(state.triggers[1]).toEqual(trigger(2, 'manual'))
        const saved = await rowOf(tid(1))
        expect(
            await within(saved).findByText('Schedule: 30 6 * * 1-5 (UTC)'),
        ).toBeVisible()
        expect(within(saved).getByText('Disabled')).toBeVisible()
    })

    it('enables and disables a trigger through the draft', async () => {
        const { state } = backend({ triggers: [trigger(1, 'api')] })
        renderRoute(PATH)
        const row = await rowOf(tid(1))
        fireEvent.click(
            within(row).getByRole('button', {
                name: `Disable trigger ${tid(1)}`,
            }),
        )
        await waitFor(() => {
            expect(state.triggers[0]?.enabled).toBe(false)
        })
        expect(
            await within(await rowOf(tid(1))).findByText('Disabled'),
        ).toBeVisible()

        fireEvent.click(
            await within(await rowOf(tid(1))).findByRole('button', {
                name: `Enable trigger ${tid(1)}`,
            }),
        )
        await waitFor(() => {
            expect(state.triggers[0]?.enabled).toBe(true)
        })
        expect(state.puts.map((p) => p.expected_revision)).toEqual([3, 4])
    })

    it('deletes a trigger after confirmation', async () => {
        const { state } = backend({
            triggers: [trigger(1, 'api'), trigger(2, 'manual')],
        })
        renderRoute(PATH)
        const row = await rowOf(tid(1))
        fireEvent.click(
            within(row).getByRole('button', {
                name: `Delete trigger ${tid(1)}`,
            }),
        )
        const dialog = await screen.findByRole('dialog', {
            name: 'Delete trigger?',
        })
        expect(state.puts).toHaveLength(0)
        fireEvent.click(
            within(dialog).getByRole('button', { name: 'Delete trigger' }),
        )
        await waitFor(() => {
            expect(state.triggers).toEqual([trigger(2, 'manual')])
        })
        await waitFor(() => {
            expect(screen.queryByText(tid(1))).not.toBeInTheDocument()
        })
    })

    it('reloads the draft on a revision conflict instead of overwriting it', async () => {
        const { state, requests } = backend({ triggers: [trigger(1, 'api')] })
        renderRoute(PATH)
        await rowOf(tid(1))
        // Someone else saved meanwhile.
        state.revision = 7
        state.triggers = [trigger(1, 'api'), trigger(5, 'manual')]

        fireEvent.click(
            within(await rowOf(tid(1))).getByRole('button', {
                name: `Disable trigger ${tid(1)}`,
            }),
        )
        expect(await screen.findByText('The draft was changed.')).toBeVisible()
        // The newer draft is reloaded and nothing was overwritten.
        expect(await screen.findByText(tid(5))).toBeVisible()
        expect(state.triggers[0]?.enabled).toBe(true)
        expect(
            requests.filter((u) => u.pathname === `/api/v1/projects/${PID}`)
                .length,
        ).toBeGreaterThanOrEqual(3)
    })

    it('shows an authorization failure without changing the list', async () => {
        backend({
            triggers: [trigger(1, 'api')],
            overrides: {
                [`PUT /projects/${PID}`]: () =>
                    apiError(
                        403,
                        'FORBIDDEN',
                        'You do not have access to this project.',
                    ),
            },
        })
        renderRoute(PATH)
        fireEvent.click(
            within(await rowOf(tid(1))).getByRole('button', {
                name: `Delete trigger ${tid(1)}`,
            }),
        )
        const dialog = await screen.findByRole('dialog', {
            name: 'Delete trigger?',
        })
        fireEvent.click(
            within(dialog).getByRole('button', { name: 'Delete trigger' }),
        )
        expect(
            await within(dialog).findByText(
                'You do not have access to this project.',
            ),
        ).toBeVisible()
        fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))
        expect(await rowOf(tid(1))).toBeVisible()
    })

    it('shows a not-found state for an unknown or inaccessible project', async () => {
        stubBackend()
        renderRoute(PATH)
        expect(
            await screen.findByRole('heading', { name: 'Project not found' }),
        ).toBeVisible()
    })

    it('shows a retryable error when the project cannot be loaded', async () => {
        backend({
            overrides: {
                [`GET /projects/${PID}`]: () =>
                    apiError(500, 'INTERNAL_ERROR', 'The server failed.'),
            },
        })
        renderRoute(PATH)
        expect(
            await screen.findByText('Could not load the triggers'),
        ).toBeVisible()
        expect(screen.getByRole('button', { name: 'Retry' })).toBeVisible()
    })

    it('says when the backend could not check the triggers', async () => {
        backend({
            triggers: [trigger(1, 'api')],
            overrides: {
                [`POST /projects/${PID}/validate`]: () =>
                    apiError(500, 'INTERNAL_ERROR', 'The server failed.'),
            },
        })
        renderRoute(PATH)
        expect(
            await screen.findByText(/Could not check the triggers/),
        ).toBeVisible()
        expect(
            within(await rowOf(tid(1))).getByText('Not checked'),
        ).toBeVisible()
    })
})
