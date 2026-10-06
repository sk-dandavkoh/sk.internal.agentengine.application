import type { WorkflowVersion } from '../../api/workflows'

/** Keys that appear in only one side, and keys on both whose value differs. */
export interface Changes {
    added: string[]
    removed: string[]
    changed: string[]
}

/** What a diff compares: a published version, or the draft's contents. */
export type Snapshot = Pick<
    WorkflowVersion,
    'graph' | 'triggers' | 'dependencies' | 'files'
>

export interface VersionDiff {
    nodes: Changes
    edges: Changes
    triggers: Changes
    dependencies: Changes
    files: Changes
    identical: boolean
}

function changes(
    from: ReadonlyMap<string, string>,
    to: ReadonlyMap<string, string>,
): Changes {
    const sorted = (keys: Iterable<string>) => [...keys].sort()
    return {
        added: sorted([...to.keys()].filter((k) => !from.has(k))),
        removed: sorted([...from.keys()].filter((k) => !to.has(k))),
        changed: sorted(
            [...to.keys()].filter(
                (k) => from.has(k) && from.get(k) !== to.get(k),
            ),
        ),
    }
}

// ponytail: values compare by JSON.stringify; the backend serializes stored
// JSON with a stable key order, so equal configs stringify equally.
const keyed = <T>(
    items: readonly T[],
    key: (item: T) => string,
    value: (item: T) => unknown,
) => new Map(items.map((item) => [key(item), JSON.stringify(value(item))]))

/**
 * What changed from snapshot `from` to snapshot `to` (versions or the
 * draft). Display only. Node positions are layout and ignored; edges have
 * no identity beyond their endpoints.
 */
export function diffVersions(from: Snapshot, to: Snapshot): VersionDiff {
    const nodes = (v: Snapshot) =>
        keyed(
            v.graph.nodes,
            (n) => n.id,
            (n) => [n.node_type, n.label, n.config],
        )
    const edges = (v: Snapshot) =>
        keyed(
            v.graph.edges,
            (e) =>
                `${e.source_node_id}.${e.source_port} → ${e.target_node_id}.${e.target_port}`,
            () => null,
        )
    const triggers = (v: Snapshot) =>
        keyed(
            v.triggers,
            (t) => t.id,
            (t) => [t.trigger_type, t.enabled, t.config],
        )
    const dependencies = (v: Snapshot) =>
        keyed(
            v.dependencies,
            (d) => `${d.kind}:${d.name}`,
            (d) => d.version_constraint,
        )
    const files = (v: Snapshot) =>
        keyed(
            v.files,
            (f) => f.path,
            (f) => f.size,
        )
    const diff = {
        nodes: changes(nodes(from), nodes(to)),
        edges: changes(edges(from), edges(to)),
        triggers: changes(triggers(from), triggers(to)),
        dependencies: changes(dependencies(from), dependencies(to)),
        files: changes(files(from), files(to)),
    }
    const identical = Object.values(diff).every(
        (c) =>
            c.added.length === 0 &&
            c.removed.length === 0 &&
            c.changed.length === 0,
    )
    return { ...diff, identical }
}
