import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { Alert } from './Alert'
import { Badge } from './Badge'
import { Card } from './Card'
import { EmptyState, ErrorState, LoadingState, Skeleton } from './states'
import { Table } from './Table'

describe('Alert', () => {
    it('announces errors assertively, other tones politely, with a text prefix', () => {
        render(
            <>
                <Alert tone="danger" title="Publish failed">
                    Fix the graph.
                </Alert>
                <Alert tone="warning">Unconnected nodes.</Alert>
            </>,
        )

        expect(screen.getByRole('alert')).toHaveTextContent(
            'Error: Publish failedFix the graph.',
        )
        expect(screen.getByRole('status')).toHaveTextContent(
            'Warning: Unconnected nodes.',
        )
    })
})

describe('Badge', () => {
    it('keeps the status as text next to a decorative dot', () => {
        render(
            <Badge tone="success" dot>
                Completed
            </Badge>,
        )

        expect(screen.getByText('Completed')).toBeInTheDocument()
    })
})

describe('Card', () => {
    it('titles its section with a configurable heading level', () => {
        render(
            <Card
                title="Recent runs"
                titleAs="h3"
                actions={<button>View all</button>}
            >
                Body
            </Card>,
        )

        expect(
            screen.getByRole('heading', { level: 3, name: 'Recent runs' }),
        ).toBeInTheDocument()
        expect(
            screen.getByRole('button', { name: 'View all' }),
        ).toBeInTheDocument()
    })
})

describe('states', () => {
    it('EmptyState offers a heading and next step', () => {
        render(
            <EmptyState
                title="No projects yet"
                action={<button>Create project</button>}
            />,
        )

        expect(
            screen.getByRole('heading', { level: 2, name: 'No projects yet' }),
        ).toBeInTheDocument()
        expect(
            screen.getByRole('button', { name: 'Create project' }),
        ).toBeInTheDocument()
    })

    it('ErrorState is an alert with a default title', () => {
        render(<ErrorState description="Executions could not be loaded." />)

        expect(screen.getByRole('alert')).toHaveTextContent(
            'Something went wrongExecutions could not be loaded.',
        )
    })

    it('LoadingState is a labelled status; Skeleton is hidden from AT', () => {
        render(
            <>
                <LoadingState label="Loading runs…" />
                <Skeleton />
            </>,
        )

        expect(screen.getByRole('status')).toHaveTextContent('Loading runs…')
        expect(
            document.querySelector('[aria-hidden="true"].animate-pulse'),
        ).not.toBeNull()
    })
})

describe('Table', () => {
    const columns = [
        {
            key: 'name',
            header: 'Name',
            cell: (row: { id: string; name: string }) => row.name,
        },
    ]

    it('is a captioned table with column headers and rows', () => {
        render(
            <Table
                caption="Projects"
                hideCaption
                columns={columns}
                rows={[
                    { id: '1', name: 'Invoices' },
                    { id: '2', name: 'Digest' },
                ]}
                rowKey={(row) => row.id}
            />,
        )
        const table = screen.getByRole('table', { name: 'Projects' })

        expect(
            within(table).getByRole('columnheader', { name: 'Name' }),
        ).toHaveAttribute('scope', 'col')
        expect(within(table).getAllByRole('row')).toHaveLength(3)
        expect(
            within(table).getByRole('cell', { name: 'Digest' }),
        ).toBeInTheDocument()
    })

    it('renders the empty message spanning all columns', () => {
        render(
            <Table
                caption="Projects"
                columns={columns}
                rows={[]}
                rowKey={(row) => row.id}
                empty="No projects match."
            />,
        )

        expect(
            screen.getByRole('cell', { name: 'No projects match.' }),
        ).toHaveAttribute('colspan', '1')
    })
})
