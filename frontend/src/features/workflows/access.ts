import type { WorkflowAccess } from '../../api/sharing'

const rank = { view: 1, edit: 2, manage: 3 } as const

/** Whether `access` allows what `needed` requires (UX only: the backend
 * refuses anything more regardless). Undefined: not known yet, so no. */
export function allows(
    access: WorkflowAccess | undefined,
    needed: WorkflowAccess,
) {
    return access !== undefined && rank[access] >= rank[needed]
}
