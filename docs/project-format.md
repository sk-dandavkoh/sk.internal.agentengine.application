# Project Package Format

Package format version: **1** (`akasha.application.project_package.FORMAT_VERSION`)
Project schema version: **1** (`akasha.domain.project.PROJECT_SCHEMA_VERSION`)

A Project Package is the portable form of one Project (architecture §56/§57).
It can be exported from one installation and imported into another
compatible one. It **never contains secrets**. Configuration holds credential
references (`credential:<name>`), and the target installation provides its
own values under the same names.

---

## 1. Layout

A package is a ZIP archive (`application/zip`, conventionally
`*.akasha.zip`):

```text
manifest.json          what this is, what it needs, integrity inventory
project.json           name, description, graph, triggers, dependencies
docs/...               documentation
prompts/...            prompt templates and other text templates
resources/...          data files the project works with
assets/...             images and other binary assets
```

Nothing else is allowed at any level. Directory entries are tolerated and
ignored.

| Package section | Where it lives |
|---|---|
| manifest | `manifest.json` |
| metadata | `project.json`: `name`, `description` |
| graph | `project.json`: `graph.nodes`, `graph.edges` |
| node configuration | `project.json`: `graph.nodes[].config` (prompt templates of `core.llm` included) |
| prompts / templates | node configuration, plus files under `prompts/` |
| dependencies | `project.json`: `dependencies` (declared); `manifest.json`: `requires` (derived) |
| resources | `resources/` |
| documentation | `description`, plus files under `docs/` |
| assets | `assets/` |

## 2. `manifest.json`

```json
{
  "format": "akasha-project-package",
  "format_version": 1,
  "project_schema_version": 1,
  "engine_version": "0.1.0",
  "engine_compatibility": ">=0.1.0",
  "source_project_version": 3,
  "requires": {
    "nodes": [
      {"node_type": "core.input", "version": "1.0.0"},
      {"node_type": "core.llm", "version": "1.0.0"}
    ],
    "credentials": ["llm-key"]
  },
  "files": [
    {"path": "project.json", "size": 1234, "sha256": "…"},
    {"path": "prompts/system.txt", "size": 56, "sha256": "…"}
  ]
}
```

- `engine_version`: the exporting Engine. Informational (reproducibility).
- `engine_compatibility`: the Engine versions that may import the package,
  as a version constraint (`*`, or comma-separated `>=`/`<=`/`>`/`<`/`==`
  clauses). Export writes `>=MAJOR.MINOR.0` of the exporting Engine, because
  an older release line may lack behaviour the project relied on.
- `source_project_version`: the exported published version. `null` means
  the draft was exported.
- `requires.nodes`: every node type the graph uses (exactly those, once
  each), with the version installed at export.
- `requires.credentials`: every credential name the configuration
  references. **Names only.**
- `files`: every other entry, with size and SHA-256.

## 3. `project.json`

```json
{
  "name": "Lead Qualification",
  "description": "Qualifies inbound leads.",
  "graph": {
    "nodes": [{"id": "node_<32 hex>", "node_type": "core.llm",
               "config": {"credential": "credential:llm-key",
                          "user_prompt": "Classify {{ lead.name }}"},
               "label": "llm"}],
    "edges": [{"source_node_id": "…", "source_port": "value",
               "target_node_id": "…", "target_port": "variables"}]
  },
  "triggers": [{"id": "trig_<32 hex>", "trigger_type": "manual",
                "config": {}, "enabled": true}],
  "dependencies": [{"kind": "node", "name": "core.llm",
                    "version_constraint": ">=1.0.0,<2.0.0"}]
}
```

Node and trigger ids are project-scoped and kept as they are. Importing the
same package twice yields two independent projects.

## 4. Project files

Paths look like `<root>/<segment>[/<segment>…]`, where root is one of `docs`,
`prompts`, `resources` or `assets`. There are 1–8 segments. Each segment is
`[A-Za-z0-9][A-Za-z0-9._-]{0,99}`, so `..`, `.`, hidden files, absolute
paths and backslashes cannot be expressed.

Limits: at most 100 files, 1 MiB each and 4 MiB in total.

Files are stored with the project draft and snapshotted into every published
version. The draft API (`PUT /projects/{id}`) keeps them unchanged; files
are set by import only. Text files (UTF-8) must not contain credential-shaped
strings. Binary files cannot be inspected and are the author's
responsibility.

## 5. Export

`GET /api/v1/projects/{id}/export[?version=N]` returns the package of
published version `N`, or of the draft when `N` is omitted.

- Export is deterministic: the same project yields byte-identical packages.
  Entries are in a fixed order with fixed timestamps, JSON keys are sorted,
  and no export time is recorded.
- Export **refuses** (`PLAINTEXT_SECRET`, locations only) rather than strips
  if the content holds secret material. This is only reachable for data
  stored before credential references were enforced.
- Export refuses (`PROJECT_PACKAGE_INCOMPATIBLE`) if the project uses a
  node type that is not installed, because its requirements cannot be
  recorded.
- Credential values, the credential encryption key and session state are
  never read by the exporter.
- Recorded as a `project.exported` event.

## 6. Import

`POST /api/v1/projects/import` with an `application/zip` body (max 16 MiB,
enforced while streaming) returns `201` with:

```json
{"project": {...}, "validation": {"valid": true, "issues": []},
 "missing_credentials": ["llm-key"]}
```

The result is a **new, unpublished project** with a new id, whose draft is the
package content. Publishing stays a separate, explicit step. Import is
all-or-nothing, and a rejected package leaves nothing behind.

Checks, in order:

1. **Archive** (`PROJECT_PACKAGE_INVALID`, `PROJECT_PACKAGE_TOO_LARGE`):
   - it must be a valid ZIP with at most 512 entries and 102 files;
   - no unexpected names (traversal, absolute or unknown roots), duplicates,
     symlinks or other non-regular files, encryption, or compression other
     than stored/deflate;
   - declared and actual sizes are within limits, so zip bombs fail before
     inflating;
   - nothing is ever extracted to disk.
2. **Versions** (`PROJECT_PACKAGE_INCOMPATIBLE`): `format_version` and
   `project_schema_version` must be supported. They are checked before the
   rest of the schema, so a newer format is reported as unsupported, not as
   malformed. The importing Engine must also satisfy
   `engine_compatibility`.
3. **Schema** (`PROJECT_PACKAGE_INVALID`):
   - strict JSON: UTF-8, no NaN/Infinity, no NUL characters;
   - strict schemas: unknown fields are rejected, and types are not coerced;
   - error details carry locations and messages, never input values.
4. **Integrity** (`PROJECT_PACKAGE_INVALID`): the inventory lists exactly the
   entries present, with matching size and SHA-256. `requires` must match
   the content (node types used, credentials referenced).
5. **Domain structure** (`PROJECT_PACKAGE_INVALID`): ids, edges and triggers
   are checked by the domain constructors, and file paths and limits by the
   project file rules.
6. **Requirements** (`PROJECT_PACKAGE_INCOMPATIBLE`, with `issues`):
   - every required node type is installed in a SemVer-compatible version:
     the same major version, at least the exported one;
   - declared dependencies hold, including `kind: "plugin"` against the
     enabled plugins.
7. **Secrets** (`PLAINTEXT_SECRET`): the same write rule as every draft.

Other graph findings, such as a missing edge in an exported work-in-progress
draft, do not reject the import. They are returned in `validation`, and
publishing stays gated as usual. Credentials the installation lacks are
listed in `missing_credentials`, which is not an error. Recorded as a
`project.imported` event.

## 7. Compatibility policy

- **Format version**: bumped when the archive layout or the manifest shape
  changes. An Engine reads only the versions it lists as supported.
- **Project schema version**: bumped when `project.json` changes meaning.
  Only schema 1 exists. When schema 2 arrives, the reader gains an explicit
  migration from 1 (architecture §82). Unsupported versions are rejected,
  never reinterpreted.
- **Node versions**: SemVer. A new major version means the configuration
  may no longer fit, so the import is rejected. The target also validates
  each node's configuration against its own schema (`validation`).
- **Plugins**: required through declared project dependencies
  (`{"kind": "plugin", ...}`). The package does not name the plugin that
  provides a node type. A missing plugin shows up as a missing node type.
