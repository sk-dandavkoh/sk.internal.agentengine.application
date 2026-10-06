import { useState } from 'react'
import {
    Alert,
    Badge,
    Button,
    Card,
    Checkbox,
    ConfirmDialog,
    Dialog,
    DropdownMenu,
    EmptyState,
    ErrorState,
    Input,
    LoadingState,
    PageHeader,
    Select,
    Skeleton,
    Switch,
    Table,
    Tabs,
    Textarea,
    Tooltip,
    useToast,
} from '../components/ui'

interface DemoRow {
    id: string
    name: string
    status: 'Active' | 'Draft' | 'Failed'
}

const rows: DemoRow[] = [
    { id: '1', name: 'Invoice triage', status: 'Active' },
    { id: '2', name: 'Weekly digest', status: 'Draft' },
    { id: '3', name: 'Lead enrichment', status: 'Failed' },
]

const statusTone = {
    Active: 'success',
    Draft: 'neutral',
    Failed: 'danger',
} as const

// Internal reference for the design system (not in the navigation). It is the
// visual check for both themes and the real-browser target of the E2E tests
// for native dialog, menu and tooltip behavior.
export function UiCatalogPage() {
    const { notify } = useToast()
    const [dialogOpen, setDialogOpen] = useState(false)
    const [confirmOpen, setConfirmOpen] = useState(false)
    const [lastAction, setLastAction] = useState('none')

    return (
        <>
            <PageHeader
                title="UI catalog"
                description="Reusable primitives in every state."
                actions={<Button variant="primary">Primary action</Button>}
            />
            <div className="grid items-start gap-6 lg:grid-cols-2">
                <Card title="Buttons">
                    <div className="flex flex-wrap gap-2">
                        <Button variant="primary">Primary</Button>
                        <Button>Secondary</Button>
                        <Button variant="ghost">Ghost</Button>
                        <Button variant="danger">Danger</Button>
                        <Button disabled>Disabled</Button>
                        <Button variant="primary" loading>
                            Saving
                        </Button>
                        <Button size="sm">Small</Button>
                    </div>
                </Card>

                <Card title="Form controls">
                    <form
                        className="flex flex-col gap-4"
                        onSubmit={(event) => {
                            event.preventDefault()
                        }}
                    >
                        <Input
                            label="Workflow name"
                            description="Shown in the workflow list."
                            required
                        />
                        <Input
                            label="Webhook path"
                            defaultValue="bad path"
                            error="Use letters, digits and dashes only."
                        />
                        <Select label="Provider" defaultValue="">
                            <option value="" disabled>
                                Choose a provider
                            </option>
                            <option value="a">Provider A</option>
                            <option value="b">Provider B</option>
                        </Select>
                        <Textarea label="Notes" />
                        <Checkbox
                            label="Notify on failure"
                            description="Sends a message when an execution fails."
                        />
                        <Switch label="Enabled" defaultChecked />
                        <Checkbox label="Unavailable option" disabled />
                    </form>
                </Card>

                <Card title="Overlays">
                    <div className="flex flex-wrap items-center gap-2">
                        <Button
                            onClick={() => {
                                setDialogOpen(true)
                            }}
                        >
                            Open dialog
                        </Button>
                        <Button
                            variant="danger"
                            onClick={() => {
                                setConfirmOpen(true)
                            }}
                        >
                            Delete item
                        </Button>
                        <DropdownMenu
                            label="Actions"
                            items={[
                                {
                                    label: 'Duplicate',
                                    onSelect: () => {
                                        setLastAction('Duplicate')
                                    },
                                },
                                {
                                    label: 'Archive',
                                    disabled: true,
                                    onSelect: () => {
                                        setLastAction('Archive')
                                    },
                                },
                                {
                                    label: 'Delete',
                                    tone: 'danger',
                                    onSelect: () => {
                                        setLastAction('Delete')
                                    },
                                },
                            ]}
                        />
                        <Tooltip content="Runs the published version">
                            <Button>Run</Button>
                        </Tooltip>
                    </div>
                    <p className="mt-3 text-sm text-muted">
                        Last menu action: {lastAction}
                    </p>
                </Card>

                <Card title="Feedback">
                    <div className="flex flex-col gap-3">
                        <div className="flex flex-wrap gap-2">
                            <Badge>Neutral</Badge>
                            <Badge tone="info">Info</Badge>
                            <Badge tone="success" dot>
                                Completed
                            </Badge>
                            <Badge tone="warning" dot>
                                Retrying
                            </Badge>
                            <Badge tone="danger" dot>
                                Failed
                            </Badge>
                        </div>
                        <Alert tone="info" title="Heads up">
                            Drafts are not executed until published.
                        </Alert>
                        <Alert tone="warning">Two nodes are unconnected.</Alert>
                        <div className="flex flex-wrap gap-2">
                            <Button
                                onClick={() => {
                                    notify({
                                        tone: 'success',
                                        title: 'Workflow saved',
                                    })
                                }}
                            >
                                Success toast
                            </Button>
                            <Button
                                onClick={() => {
                                    notify({
                                        tone: 'danger',
                                        title: 'Publish failed',
                                        description:
                                            'The graph has validation errors.',
                                    })
                                }}
                            >
                                Error toast
                            </Button>
                        </div>
                    </div>
                </Card>

                <Card title="Tabs" className="lg:col-span-2">
                    <Tabs
                        label="Demo sections"
                        items={[
                            {
                                id: 'table',
                                label: 'Table',
                                content: (
                                    <Table
                                        caption="Workflows"
                                        hideCaption
                                        rows={rows}
                                        rowKey={(row) => row.id}
                                        columns={[
                                            {
                                                key: 'name',
                                                header: 'Name',
                                                cell: (row) => row.name,
                                            },
                                            {
                                                key: 'status',
                                                header: 'Status',
                                                align: 'end',
                                                cell: (row) => (
                                                    <Badge
                                                        tone={
                                                            statusTone[
                                                                row.status
                                                            ]
                                                        }
                                                        dot
                                                    >
                                                        {row.status}
                                                    </Badge>
                                                ),
                                            },
                                        ]}
                                    />
                                ),
                            },
                            {
                                id: 'states',
                                label: 'States',
                                content: (
                                    <div className="grid gap-4 md:grid-cols-3">
                                        <EmptyState
                                            title="No workflows yet"
                                            description="Create a workflow to start building."
                                            action={
                                                <Button variant="primary">
                                                    Create workflow
                                                </Button>
                                            }
                                        />
                                        <ErrorState
                                            description="Executions could not be loaded."
                                            action={<Button>Retry</Button>}
                                        />
                                        <LoadingState />
                                    </div>
                                ),
                            },
                            {
                                id: 'skeleton',
                                label: 'Skeleton',
                                content: (
                                    <div
                                        aria-busy="true"
                                        className="flex flex-col gap-2"
                                    >
                                        <Skeleton className="w-1/3" />
                                        <Skeleton />
                                        <Skeleton className="w-2/3" />
                                    </div>
                                ),
                            },
                        ]}
                    />
                </Card>
            </div>

            <Dialog
                open={dialogOpen}
                onClose={() => {
                    setDialogOpen(false)
                }}
                title="Rename workflow"
                description="The name is shown in the workflow list."
                footer={
                    <>
                        <Button
                            onClick={() => {
                                setDialogOpen(false)
                            }}
                        >
                            Cancel
                        </Button>
                        <Button
                            variant="primary"
                            onClick={() => {
                                setDialogOpen(false)
                            }}
                        >
                            Save
                        </Button>
                    </>
                }
            >
                <Input label="Name" defaultValue="Invoice triage" />
            </Dialog>

            <ConfirmDialog
                open={confirmOpen}
                onCancel={() => {
                    setConfirmOpen(false)
                }}
                onConfirm={() => {
                    setConfirmOpen(false)
                    setLastAction('Deleted item')
                }}
                title="Delete item?"
                confirmLabel="Delete item"
                irreversible
            >
                The item “Invoice triage” and its history will be deleted.
            </ConfirmDialog>
        </>
    )
}
