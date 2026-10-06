import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { useParams } from 'react-router'
import { useApiClient } from '../../api/context'
import { ApiError } from '../../api/errors'
import type { Workflow, WorkflowVersionSummary } from '../../api/workflows'
import { workflowQueries } from '../../api/queries'
import { QuerySection } from '../../components/QuerySection'
import {
    Button,
    Card,
    EmptyState,
    ErrorState,
    PageHeader,
    Table,
    TextLink,
    type TableColumn,
} from '../../components/ui'
import {
    Author,
    ImmutableBadge,
    LatestBadge,
    Time,
    VersionLink,
} from './shared'

const PAGE_SIZE = 50
const workflowPath = (id: string) => `/workflows/${encodeURIComponent(id)}`

/** The one mutable state of a workflow, kept apart from its versions. */
function DraftCard({ workflow }: { workflow: Workflow }) {
    return (
        <Card
            title="Draft"
            description="The editable working copy. Publishing freezes it as the next version; editing it never changes a published version."
            actions={
                <TextLink to={`${workflowPath(workflow.id)}/editor`}>
                    Open draft in editor
                </TextLink>
            }
        >
            <dl className="flex flex-wrap gap-x-8 gap-y-2 text-sm">
                <div>
                    <dt className="text-muted">Revision</dt>
                    <dd>{workflow.revision}</dd>
                </div>
                <div>
                    <dt className="text-muted">Last updated</dt>
                    <dd>
                        <Time iso={workflow.updated_at} />
                    </dd>
                </div>
            </dl>
        </Card>
    )
}

function VersionList({ workflow }: { workflow: Workflow }) {
    const [offset, setOffset] = useState(0)
    const query = useQuery({
        ...workflowQueries.versions(useApiClient(), workflow.id, {
            offset,
            limit: PAGE_SIZE,
        }),
        placeholderData: keepPreviousData,
    })
    const columns: TableColumn<WorkflowVersionSummary>[] = [
        {
            key: 'version',
            header: 'Version',
            cell: (v) => (
                <span className="flex items-center gap-2">
                    <VersionLink workflowId={workflow.id} version={v.version} />
                    {v.version === workflow.current_version && <LatestBadge />}
                </span>
            ),
        },
        {
            key: 'published',
            header: 'Published',
            cell: (v) => <Time iso={v.published_at} />,
        },
        {
            key: 'author',
            header: 'Published by',
            cell: (v) => <Author author={v.published_by} />,
        },
        {
            key: 'description',
            header: 'Description',
            cell: (v) => (
                <span className="line-clamp-2 max-w-md">
                    {v.description || (
                        <span className="text-muted">No description</span>
                    )}
                </span>
            ),
        },
    ]

    return (
        <Card
            title="Published versions"
            description="Immutable snapshots, oldest first. Every execution runs exactly one of them."
            actions={<ImmutableBadge />}
        >
            <div>
                <QuerySection query={query} subject="versions">
                    {({ items, page }) =>
                        items.length === 0 && offset === 0 ? (
                            <EmptyState
                                title="Not published yet"
                                titleAs="h3"
                                description="Validate and publish the draft in the editor to create version 1."
                            />
                        ) : (
                            <div
                                aria-busy={query.isPlaceholderData || undefined}
                            >
                                <Table
                                    caption="Published versions"
                                    hideCaption
                                    columns={columns}
                                    rows={items}
                                    rowKey={(v) => v.id}
                                    empty="No versions on this page."
                                />
                                <nav
                                    aria-label="Pagination"
                                    className="mt-3 flex justify-end gap-2"
                                >
                                    <Button
                                        size="sm"
                                        disabled={offset === 0}
                                        onClick={() => {
                                            setOffset(
                                                Math.max(0, offset - PAGE_SIZE),
                                            )
                                        }}
                                    >
                                        Previous
                                    </Button>
                                    <Button
                                        size="sm"
                                        disabled={!page.has_more}
                                        onClick={() => {
                                            setOffset(offset + PAGE_SIZE)
                                        }}
                                    >
                                        Next
                                    </Button>
                                </nav>
                            </div>
                        )
                    }
                </QuerySection>
            </div>
        </Card>
    )
}

export function WorkflowNotFound() {
    return (
        <ErrorState
            title="Workflow not found"
            titleAs="h1"
            description="It does not exist, was deleted, or you do not have access to it."
            action={<TextLink to="/workflows">Back to all workflows</TextLink>}
        />
    )
}

export function VersionsPage() {
    const { workflowId = '' } = useParams()
    const query = useQuery(workflowQueries.detail(useApiClient(), workflowId))

    if (
        query.data === undefined &&
        query.error instanceof ApiError &&
        query.error.kind === 'not_found'
    )
        return <WorkflowNotFound />
    return (
        <>
            <PageHeader
                title={
                    query.data ? `Versions of ${query.data.name}` : 'Versions'
                }
                description={
                    <TextLink to={workflowPath(workflowId)}>
                        Back to workflow
                    </TextLink>
                }
            />
            <QuerySection query={query} subject="the workflow">
                {(workflow) => (
                    <div className="flex flex-col gap-6">
                        <DraftCard workflow={workflow} />
                        <VersionList workflow={workflow} />
                    </div>
                )}
            </QuerySection>
        </>
    )
}
