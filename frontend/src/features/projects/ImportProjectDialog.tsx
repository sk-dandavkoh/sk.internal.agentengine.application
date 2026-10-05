import { useId, useState } from 'react'
import { useNavigate } from 'react-router'
import { z } from 'zod'
import { ApiError, errorMessage } from '../../api/errors'
import { useImportProject } from '../../api/mutations'
import {
    MAX_PROJECT_PACKAGE_BYTES,
    type ImportResult,
} from '../../api/projects'
import { Alert, Button, Dialog } from '../../components/ui'

// Every message below is the backend's, rendered as plain text.

// PROJECT_PACKAGE_INCOMPATIBLE details (application/project_package.py).
const detailsSchema = z.object({
    issues: z.array(z.object({ code: z.string(), message: z.string() })),
})

function List({ title, items }: { title: string; items: string[] }) {
    return (
        <div>
            <p className="font-medium">{title}</p>
            <ul className="list-disc pl-5">
                {items.map((item) => (
                    <li key={item} className="break-words">
                        {item}
                    </li>
                ))}
            </ul>
        </div>
    )
}

function ImportError({ error }: { error: unknown }) {
    const parsed =
        error instanceof ApiError
            ? detailsSchema.safeParse(error.details)
            : undefined
    return (
        <Alert tone="danger" title={errorMessage(error)}>
            {parsed?.success && (
                <List
                    title="Requirements not met"
                    items={parsed.data.issues.map((i) => i.message)}
                />
            )}
        </Alert>
    )
}

function Imported({ result }: { result: ImportResult }) {
    const { project, validation, missing_credentials: missing } = result
    return (
        <div className="flex flex-col gap-3">
            <Alert tone="success" title={`Imported “${project.name}”`}>
                It is a new project with an unpublished draft.
            </Alert>
            {validation.valid ? (
                <Alert tone="info" title="The draft is valid">
                    It can be published as it is.
                </Alert>
            ) : (
                <Alert tone="warning" title="The draft needs changes">
                    <List
                        title="Validation issues"
                        items={validation.issues.map((i) => i.message)}
                    />
                </Alert>
            )}
            {missing.length > 0 && (
                <Alert tone="warning" title="Credentials to create">
                    <List
                        title="Referenced but not on this installation"
                        items={missing}
                    />
                </Alert>
            )}
        </div>
    )
}

export function ImportProjectDialog({ onClose }: { onClose: () => void }) {
    const importing = useImportProject()
    const navigate = useNavigate()
    const inputId = useId()
    const [file, setFile] = useState<File | null>(null)
    // UX only: the backend enforces type and size itself.
    const tooLarge = file !== null && file.size > MAX_PROJECT_PACKAGE_BYTES
    const result = importing.data
    const close = () => {
        importing.reset()
        onClose()
    }

    return (
        <Dialog
            open
            onClose={close}
            title="Import project"
            dismissible={!importing.isPending}
            description="Upload a project package (.akasha.zip). The engine checks it and creates a new project from it; nothing is published or run."
            footer={
                result ? (
                    <>
                        <Button onClick={close}>Close</Button>
                        <Button
                            variant="primary"
                            onClick={() => {
                                void navigate(
                                    `/projects/${encodeURIComponent(result.project.id)}`,
                                )
                            }}
                        >
                            Open project
                        </Button>
                    </>
                ) : (
                    <>
                        <Button onClick={close} disabled={importing.isPending}>
                            Cancel
                        </Button>
                        <Button
                            variant="primary"
                            loading={importing.isPending}
                            disabled={!file || tooLarge}
                            onClick={() => {
                                if (file) importing.mutate(file)
                            }}
                        >
                            Import
                        </Button>
                    </>
                )
            }
        >
            {result ? (
                <Imported result={result} />
            ) : (
                <div className="flex flex-col gap-4">
                    <div className="flex flex-col gap-1.5">
                        <label
                            htmlFor={inputId}
                            className="text-sm font-medium"
                        >
                            Project package (.zip)
                        </label>
                        <input
                            id={inputId}
                            type="file"
                            accept=".zip,application/zip"
                            aria-invalid={tooLarge || undefined}
                            className="focus-ring block w-full rounded-md text-sm file:mr-3 file:rounded-md file:border file:border-border-strong file:bg-surface file:px-3 file:py-1.5"
                            onChange={(event) => {
                                importing.reset()
                                setFile(event.target.files?.[0] ?? null)
                            }}
                        />
                        {tooLarge && (
                            <p className="text-sm font-medium text-danger-text">
                                The package exceeds{' '}
                                {MAX_PROJECT_PACKAGE_BYTES / 1024 / 1024} MiB.
                            </p>
                        )}
                    </div>
                    {importing.error && <ImportError error={importing.error} />}
                </div>
            )}
        </Dialog>
    )
}
