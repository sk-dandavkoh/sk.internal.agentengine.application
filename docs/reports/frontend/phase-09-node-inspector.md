# Phase 09 — Node Configuration Inspector

## 1. Executive Summary

**What the phase was supposed to achieve.** Roadmap §15 (`docs/roadmap-frontend.md`): an inspector panel for the selected node. The config form had to be generated from the backend Node Contract's config schema. It had to support String, Number, Boolean, Select, Secret Reference, JSON, List, Object, Textarea and Prompt Editor. Required fields, defaults and descriptions had to be honored. Secret values must never be shown, and backend validation must stay authoritative. The session prompt additionally required:
- nullable values
- validation errors
- tests for schema rendering, required fields, validation, defaults, nested configuration, credential references, invalid configuration, save failure and schema changes
- implementing (and testing and documenting) any backend function the inspector needed

**Actually implemented.**

Backend:
- The config-schema validator (`NodeConfigSchema`, `backend/src/akasha/domain/node.py`) now collects every issue with its field path, where it used to raise on the first one.
- It supports nullable type lists, typed map values (`additionalProperties` as a schema) and `format: "credential-reference"`.
- It treats `title` / `description` / `default` as form annotations.
- A new stateless endpoint, `POST /api/v1/nodes/{node_type}/config/validate`, returns per-field issues.
- All built-in node schemas are annotated, and SDK 1.1.0 exports the new names.

Frontend:
- A schema reader: `frontend/src/features/editor/configSchema.ts`.
- One recursive form renderer: `ConfigForm.tsx`.
- An inspector panel: `NodeInspector.tsx`, wired into `EditorPage.tsx`.
- A new `configure` editor-reducer action, so config edits are undoable draft changes saved by the existing save/autosave path.

**Resulting system state.** Selecting exactly one node in `/workflows/:id/editor` shows a "Settings: <node>" panel:
- The form is derived only from that node's `config_schema`.
- The backend checks the config 400 ms after typing settles, and issues appear at their fields and in a list.
- Edits are saved with the draft.

No node has a hand-written form. The tests prove the completion criterion ("a new backend Node with a supported configuration schema can appear in the Inspector without a new hard-coded component") in two ways:
- with a fictional plugin node `acme.rich` (component tests)
- with the built-in `core.http` against the real backend (full-stack Playwright)

**Acceptance criteria.** All five roadmap criteria pass with evidence (§14). Two scope items, Textarea and Prompt Editor, are implemented minimally (§20). The phase is therefore reported as complete with limitations.

**Phase Status:** COMPLETE WITH LIMITATIONS

---

## 2. Phase Objective

Roadmap §15, "Ziel": "Node-Konfiguration professionell bearbeiten können." The flow it prescribes:

```text
Node Contract → Config Schema → Form Renderer → User Input → Draft Config
```

**Acceptance criteria (roadmap):**
1. Formulare werden aus Schema generiert.
2. Required Fields werden berücksichtigt.
3. Defaults werden berücksichtigt.
4. Secret Values werden niemals angezeigt.
5. Backend Validation bleibt autoritativ.

The roadmap prompt also names dynamic forms, defaults, required fields, descriptions, secret references, validation feedback and unsaved changes.

**Explicitly not responsible for:**
- graph editing: delete, connect and duplicate nodes, edges (Phase 10)
- the graph validation and publish workflow UX (Phase 11)
- credentials management UI
- node label editing (not in scope)

---

## 3. Scope Implemented

### Backend / Domain (`backend/src/akasha/domain/node.py`)

- **`ConfigIssue`** (new frozen dataclass): `path: tuple[str | int, ...]` and `message: str`. Its `location` property renders `config.tools[0].name`.
- **`NodeConfigSchema.issues(config)`** (new) returns all violations, recursing at every depth.
- **`NodeConfigSchema.validate_config`** now raises `NodeConfigurationError(f"{location} {message}", details={"path": location})` for the first issue.
- **Supported keywords:**
  - `type` as a string or a list, e.g. `["integer", "null"]`
  - `required` and `additionalProperties: false` at every depth (previously top level only)
  - `additionalProperties` given as a schema, which validates map values
  - `format: "credential-reference"`, enforced with `is_credential_reference`
  - unchanged: `enum`, `minimum`, `maximum`, `minLength`, `maxItems`, `pattern`
- **`CREDENTIAL_REFERENCE_FORMAT = "credential-reference"`** (new constant).
- **Default checks:** `NodeConfigSchema.__post_init__` rejects a top-level property whose `default` violates its own schema (`NodeContractError`).
- **`NodeDefinition.config_issues(config)`** (new) returns plaintext-secret locations first, then schema issues.
- **Helpers:** the old `_check_json_type`, `_check_constraints` and `_check_type_keyword` are replaced by `_JSON_TYPES`, `_issues` and `_constraint_messages`.

### Backend / Domain (`backend/src/akasha/domain/credentials.py`)

- **`plaintext_secret_locations(config)`** (new) returns key/index tuples.
- **`plaintext_secret_paths`** is now a formatting wrapper around it; its output is unchanged (`.a.b[0]`).

### Backend / API (`backend/src/akasha/api/nodes.py`)

- **`POST /api/v1/nodes/{node_type}/config/validate`**, with the models `ConfigValidationRequest`, `ConfigIssueOut` and `ConfigValidationOut`.
- The router is mounted under the existing protected (authenticated) router.

### Backend / Nodes (schema annotations)

| Node | Defaults | Descriptions / format | Other changes |
|---|---|---|---|
| `core.input` | — | `field` | — |
| `core.log` | `level` = `DEFAULT_LEVEL` ("info"), `include_value` = false | `include_value` (sensitivity note) | — |
| `core.delay` | — | `seconds` | — |
| `core.set` | — | `fields`, `key` | — |
| `core.pick` | — | `path` | — |
| `core.if` | — | `operand`, `field` | — |
| `core.switch` | — | `cases` | — |
| `core.http` | `method` = `DEFAULT_METHOD` ("GET"), `timeout_seconds` = `DEFAULT_TIMEOUT_SECONDS`, `follow_redirects` = false, `fail_on_error_status` = true | — | `headers` values typed as strings; `header_credentials` values are credential references |
| `core.web_search` | `max_results` = `DEFAULT_MAX_RESULTS` (5) | — | — |
| `core.llm` | — | `credential` is a credential reference (was `pattern: "^credential:"`); prompt templates get `format: "template"` and a description; `provider`, `temperature`, `max_tokens`, `output_schema` described | — |
| `core.smtp` | `security` = `DEFAULT_SECURITY` ("starttls"), `timeout_seconds` = `DEFAULT_TIMEOUT_SECONDS` (30) | `password_credential` is a credential reference (was `minLength: 1`); `port` described | — |

- The node code reads these defaults from the same new constants that the schemas use.

### Backend / SDK

- `backend/src/akasha/sdk/__init__.py`: `SDK_VERSION` goes from `"1.0.0"` to `"1.1.0"`. New exports: `ConfigIssue` and `CREDENTIAL_REFERENCE_FORMAT`.
- `backend/examples/hmac_signer/node.py`: `secret_credential` declares the format.

### Frontend / API layer

- `frontend/src/api/nodes.ts`:
  - `configIssueSchema` and `configValidationSchema` (zod)
  - `validateNodeConfig(client, nodeType, config, signal)`
- `frontend/src/api/queries.ts`:
  - `queryKeys.nodeConfigValidation(nodeType, config)`, which is `['nodes', type, 'config-validation', config]`
  - `nodeQueries.configValidation`, with `staleTime: INSTALLATION_STALE_TIME_MS`
- `frontend/src/api/openapi.json`: regenerated snapshot.

### Frontend / Editor feature

- `configSchema.ts` (new, pure):
  - `fieldSpec(schema) → FieldSpec`, a discriminated union over `text | credential | number | boolean | select | object | map | list | json`
  - `properties(schema)`
  - helpers: `fits`, `emptyValue`, `humanize`, `display`, `pathKey`, `describePath`, `issuesByPath`
- `ConfigForm.tsx` (new):
  - `ConfigField` handles the null toggle, "Use default", and falls back to JSON for wrong-type values.
  - Controls: `TextField`, `CredentialField`, `NumberField`, `BooleanField`, `SelectField`, `JsonField`, `ListField`, `MapField`, `Group`.
  - `PropertyFields` renders schema properties plus keys the schema does not know.
  - `useDraftText` holds local text for numbers and JSON.
- `NodeInspector.tsx` (new): the panel, the debounced validation query and the issue list.
- `editorState.ts`:
  - new `configure` action
  - new `lastEdit` state field, which coalesces typing in one field into one undo step
  - `commit` now resets `lastEdit`
- `EditorPage.tsx`: the right column (`lg:w-96`) shows the inspector above the existing node list when exactly one node is selected. The inspector is keyed by node id.

### Testing

See §12.

---

## 4. Files Changed

| File | Change | Reason |
|---|---|---|
| `backend/src/akasha/domain/node.py` | Modified | Issue-collecting validator, new keywords, `ConfigIssue`, `CREDENTIAL_REFERENCE_FORMAT`, `NodeDefinition.config_issues`, default check |
| `backend/src/akasha/domain/credentials.py` | Modified | `plaintext_secret_locations` (path tuples for the API) |
| `backend/src/akasha/domain/graph_validation.py` | Modified | `_check_node_config` reports every config violation (follow-up fix, §11 item 5) |
| `backend/tests/domain/test_graph_validation.py` | Modified | `test_invalid_config_missing_key` asserts all issues |
| `backend/src/akasha/api/nodes.py` | Modified | Config validation endpoint and models |
| `backend/src/akasha/nodes/{core,control,http,llm,smtp}.py` | Modified | Schema annotations; default constants shared with node code |
| `backend/src/akasha/sdk/__init__.py` | Modified | SDK 1.1.0; new exports |
| `backend/examples/hmac_signer/node.py` | Modified | Declares credential-reference format |
| `backend/tests/domain/test_node_contract.py` | Modified | 7 new tests (1 parametrized ×4); 2 message assertions updated |
| `backend/tests/nodes/test_builtin_contract.py` | Modified | New parametrized test over all 13 built-ins |
| `backend/tests/nodes/test_http_node.py` | Modified | Header test rewritten (schema + runtime guard); new header-credential test |
| `backend/tests/nodes/test_llm_node.py` | Modified | Malformed-credential case moved from runtime-error test to schema-rejection test |
| `backend/tests/integration/test_executions_api.py` | Modified | Catalog annotations asserted; 3 new endpoint tests |
| `backend/tests/test_sdk.py` | Modified | `PUBLIC_API` includes the two new names |
| `frontend/src/api/nodes.ts`, `queries.ts` | Modified | Validation API function and query options |
| `frontend/src/api/openapi.json` | Modified (generated) | Regenerated with `AKASHA_UPDATE_OPENAPI_SNAPSHOT=1 uv run pytest tests/test_openapi_contract.py` |
| `frontend/src/api/contract.node.test.ts` | Modified | `validateNodeConfig` registered in the contract coverage list |
| `frontend/src/features/editor/configSchema.ts` | Created | Schema → field specs |
| `frontend/src/features/editor/ConfigForm.tsx` | Created | Generic form renderer |
| `frontend/src/features/editor/NodeInspector.tsx` | Created | Inspector panel |
| `frontend/src/features/editor/editorState.ts` | Modified | `configure` action, `lastEdit` |
| `frontend/src/features/editor/EditorPage.tsx` | Modified | Inspector placement |
| `frontend/src/features/editor/configSchema.test.ts` | Created | 11 unit tests |
| `frontend/src/features/editor/NodeInspector.test.tsx` | Created | 16 component tests |
| `frontend/src/features/editor/editorState.test.ts` | Modified | 4 `configure` tests |
| `frontend/tests/fullstack/workflows.spec.ts` | Modified | 1 full-stack inspector test |
| `docs/architecture.md` | Modified | §3.5 implementation notes; §22 endpoint table |
| `docs/sdk.md` | Modified | SDK version; `NodeConfigSchema` keywords; `CREDENTIAL_REFERENCE_FORMAT` row |
| `frontend/README.md` | Modified | New "Node inspector" section |
| `docs/reports/frontend/phase-09-node-inspector.md` | Created | This report |

There are no new directories, migrations, configuration files or dependency changes. `git diff --stat`: 27 tracked files changed, 1062 insertions and 173 deletions, plus 6 untracked new files.

---

## 5. Architecture Changes

### 5.1 Backend endpoint for per-field config validation

- **Decision:** add `POST /api/v1/nodes/{node_type}/config/validate`.
- **Context:** The inspector must show validation errors per field. The only existing check was `POST /workflows/{id}/validate`. That endpoint:
  - works on the saved draft
  - reports only the first config issue per node
  - carries no field path, only a message
  - belongs to Phase 11's UX

  `AGENTS.md` §4 and `backend/AGENTS.md` §2 forbid making the frontend the authority for config validation.
- **Chosen approach:** a stateless endpoint backed by `NodeDefinition.config_issues`. It returns every issue with a key/index path.
- **Alternatives considered:**
  1. Re-implement the JSON-Schema subset in TypeScript. Rejected: it duplicates domain rules and could drift.
  2. Use the workflow validate endpoint only. Rejected: it requires a save and gives no field paths.
  3. Add a JSON-Schema library to the frontend. Rejected: it adds a dependency and is still a second, non-authoritative interpreter.
- **Consequences:**
  - The frontend sends the config about 400 ms after typing settles; results are cached per config.
  - Publish remains the authoritative gate.
  - Phase 11 may reuse `ConfigIssue` paths to focus fields.

### 5.2 Credential reference as a declared schema format

- **Decision:** `format: "credential-reference"` marks credential-holding config strings. It is enforced by validation.
- **Context:** Before this phase, built-ins marked credential fields three different ways: a pattern (`^credential:`), `minLength: 1`, or a bare `object`. There was no marker that a form could rely on.
- **Chosen approach:** one standard JSON-Schema `format` value, usable directly or as `additionalProperties` (credential maps).
- **Alternatives considered:**
  1. A frontend heuristic on key names: duplicates `is_sensitive_key`. Rejected.
  2. Enforce the format at node registration for every sensitive key: rejected because it would refuse existing third-party plugins at load, which is a breaking SDK change.
- **Consequences:** built-ins are tested to declare the format on every sensitive key. Third-party plugins may omit it (§20).

### 5.3 Schema `default` is an annotation, never inserted

- **Decision:** `default` documents what the node does when the key is absent. Neither the validator nor the inspector writes it into configs. A default that violates its own property schema is a `NodeContractError`.
- **Reason:**
  - Nodes already apply defaults in code (`cfg.get(key, X)`), and they now share the constants with the schema.
  - Writing defaults would change stored drafts merely by opening a node.
- **Consequence:** configs stay minimal, and "Use default" removes the key.

`docs/architecture.md` §3.5 records 5.1–5.3. No ADR was created; the decisions extend existing §3.5 ("Schema Driven") rather than replacing an architectural direction.

---

## 6. Domain / Data Model Changes

- **New value object:** `ConfigIssue(path, message)` in `akasha.domain.node`.
- **New constant:** `CREDENTIAL_REFERENCE_FORMAT`.
- **Validation semantics (backend):**
  - **Stricter:**
    - nested `required` and nested `additionalProperties: false` are now enforced
    - built-in `headers` and `header_credentials` values are typed
    - the `core.llm.credential` and `core.smtp.password_credential` formats are enforced
  - **Newly accepted:** type lists such as `["string", "null"]`. Previously these raised an uncaught `TypeError` (unhashable list).
- **Error message format changed (intentional):**
  - `missing required config key: url` → `config.url is required`
  - `unknown config key: x` → `config.x is not allowed`
  - details are `{"path": "config.url"}` instead of `{"key": ...}`. No code read `details["key"]`; I checked with grep.
- **Editor state (frontend):**
  - `EditorState.lastEdit: string | null`
  - `EditorAction` gains `{ type: 'configure'; nodeId; config; field }`
- **Persistence, migrations and serialization:** none. Node config is stored as before (JSON in the draft graph).
- **Compatibility:** an already published version whose config was valid before stays executable, because the runtime does not re-validate config. The only configs that become newly rejectable at publish are ones that would have failed at run time anyway: non-string HTTP header values, and malformed credential references in a sensitive field, which the plaintext check also rejected. Built-in node versions were not bumped (§20).

---

## 7. API / Contract Changes

### `POST /api/v1/nodes/{node_type}/config/validate` (new)

- **Purpose:** every issue publish would find in one node's config, located per field.
- **Auth:** authenticated; any role. It sits under the existing protected router; tested with a member token and without a token.
- **Request:** `{"config": {<JSON object>}}`
- **Response 200:** `{"valid": bool, "issues": [{"path": [str|int, ...], "message": str}]}`. Plaintext-secret issues come first, then schema issues in schema order.
- **Errors:**
  - 404 `NODE_NOT_FOUND` for an unknown type
  - 422 `VALIDATION_ERROR` for a malformed body
  - 401 without a session
  - 500 `NODE_CONTRACT_ERROR` if a node's own schema is malformed (developer error, existing mapping)
- **Compatibility:** additive.

### `GET /api/v1/nodes` (changed content)

The shape is unchanged; `config_schema` content for built-ins gained `description`, `default`, `format` and typed `additionalProperties`.

### SDK contract

`SDK_VERSION` 1.1.0 (additive): `ConfigIssue`, `CREDENTIAL_REFERENCE_FORMAT`, `NodeConfigSchema.issues`, `NodeDefinition.config_issues`.

### Frontend/backend contract

- New zod schemas mirror the response.
- `contract.node.test.ts` checks them against the regenerated `openapi.json`.
- `backend/tests/test_openapi_contract.py` guards the snapshot.

---

## 8. Implementation Details

### Schema reading (`configSchema.ts`)

- `fieldSpec` only interprets the subset the backend validates:
  - `enum` → select (options may be any JSON)
  - single `string` → text, credential (format) or multiline template (format `template`)
  - `number` / `integer` → number
  - `boolean` → boolean
  - `object` with `properties` → object; with `additionalProperties` schema → map; otherwise → JSON
  - `array` with `items` → list (`maxItems` kept); otherwise → JSON
  - no type, multiple non-null types, or an unknown type → JSON
- A `null` member of a type list sets `nullable`.
- JSON objects are read with bracket access, because of `noPropertyAccessFromIndexSignature`.

### Rendering (`ConfigForm.tsx`)

- **Change model:** every control calls `onChange(next | undefined)`, where `undefined` means "remove key".
  - Object properties are `optional`: clearing removes the key.
  - List items and map values are not optional: clearing keeps an empty value.
- **Wrong-type values:** `fits(spec, value)` false renders `JsonField` with a hint, so a mismatched value (old config, changed schema) is visible and fixable, not dropped.
- **Unknown keys:** `PropertyFields` renders keys the schema does not know as JSON fields with a "Remove <key>" button.
- **Local text (numbers and JSON):** `useDraftText` keeps the typed text while it produced the current value or does not parse. It re-derives the text when the value changes from outside (undo, schema change), using the "adjust state during render" pattern. Unparsable input is never committed; the error ("Enter a number." / "Not valid JSON.") is derived from the text (`parseNumber` / `parseJson` return an `INVALID` symbol).
- **Select:** options are matched by `JSON.stringify` text. The unset option reads "Default (X)", "Not set" or "Choose…". A stored value outside the enum is shown as an extra option and flagged.
- **Boolean:** when unset, the checkbox shows the schema default. Toggling writes an explicit value.
- **Credential:** an `<input list>` backed by a `<datalist>` of credential names from `credentialQueries.list(client, {limit: 100})` with `retry: false`. The query is admin-only; for members it fails silently and the name is typed freely.
  - Input `credential:x` is normalized to the name.
  - The stored value is `credential:<name>`.
  - A stored string that is not a reference is not rendered; the field shows an explanatory error instead.
- **Map:** entries are added through a "New <label> entry" key input with duplicate detection. Renaming is remove plus add.

### Inspector (`NodeInspector.tsx`)

- `useDebounced(node.config, 400)` feeds `useQuery(nodeQueries.configValidation(...))` with `placeholderData: keepPreviousData`. The query is disabled when the node type is not in the catalog.
- The status line (`role="status"`) shows one of: "Checking configuration…", the error message, a "Configuration valid" badge, or "N problems; publishing would be rejected."
- Issues are listed with `describePath` and mapped onto fields by exact path (`issuesByPath`).

### State and persistence

- `onConfigure(config, field)` dispatches `configure`.
- The reducer replaces only that node's `config`.
- When `lastEdit === "<nodeId> <field>"`, the graph is replaced without a new history entry. `select`, `undo`, `redo` and any other commit reset `lastEdit`.
- Persistence is the unchanged `useDraftEditor` path: debounced autosave (1500 ms), explicit save, revision-guarded PUT, autosave paused after a failure, and the leave guard.
- Config edits never publish.

### Caching

- Validation results are cached per `(nodeType, config)` under the `['nodes']` prefix, so invalidating the catalog also invalidates verdicts.

---

## 9. Findings

### F1 — Validator crashed on JSON-Schema type lists
- **Impact:** MEDIUM. A plugin node declaring `{"type": ["string", "null"]}` would raise `TypeError: unhashable type: 'list'` in `_check_type_keyword` (dict `.get` with a list key) at publish or validation, which is an unmapped 500.
- **Resolution:** FIXED.
- **Notes:** type lists are now supported. Unsupported entries raise `NodeContractError` (`test_unsupported_types_are_contract_errors`).

### F2 — Credential fields had no machine-readable marker
- **Impact:** MEDIUM. A schema-driven form could not tell which fields take credential references. The built-ins used a pattern, `minLength`, or nothing, and `header_credentials` was an untyped object.
- **Resolution:** FIXED for built-ins and the example plugin. Third-party plugins: OPEN (§21).

### F3 — Built-in schemas had no descriptions or defaults
- **Impact:** LOW. Defaults lived only in node code (`cfg.get("method", "GET")`), so a form could not show them.
- **Resolution:** FIXED. Defaults are shared constants used by both the schema and the code.

### F4 — `core.http` non-string header values passed publish and failed only at run time
- **Impact:** LOW.
- **Resolution:** FIXED. The schema now types header values. The runtime guard `HTTP_INVALID_HEADERS` is kept for versions published earlier, because the runtime does not re-validate config.

### F5 — Graph validation reported only the first config issue per node
- **Impact:** LOW. This was the pre-existing `ponytail:` note in `graph_validation._check_node_config`. Users fixing a config at publish time had to repeat validate once per problem.
- **Resolution:** FIXED (follow-up within this phase, see §11 item 5). `_check_node_config` now emits one `invalid_config` issue per `NodeConfigSchema.issues` entry.

### F6 — The runtime does not re-validate config before `execute`
- **Impact:** informational. Verified by grep: there is no `validate_config` call in `akasha/runtime`.
- **Resolution:** no change. This is the reason the HTTP runtime header guard was kept.

---

## 10. Bugs / Problems Found

| Problem | Classification | Root cause | Fix | Regression test | Area |
|---|---|---|---|---|---|
| `TypeError` on `type: [..]` in config schemas | Pre-existing bug | `{...}.get(expected)` with a list key | `_issues` accepts type lists; non-string or unknown entries raise `NodeContractError` | `test_nullable_type_lists`, `test_unsupported_types_are_contract_errors` | backend domain |
| Stale "Enter a number." / "Not valid JSON." after undo | Introduced in this phase, found in self-review | Validity was a separate `useState` not reset when the value changed from outside | Validity is derived from the displayed text | `NodeInspector.test.tsx` "undo reverts a config edit in the form" (asserts `toBeValid()` after undo) | frontend form |
| Test helper override typing too loose (`Record<string, Handler \| JsonObject \| …>`); ESLint `no-unnecessary-type-assertion` | Introduced in this phase (test code) | Route overrides were mixed with options | Explicit `handlers` option | lint passes | frontend tests |
| One component test asserted on a request log its custom handler did not populate | Introduced in this phase (test bug) | Override handler replaced the recording one | Assertion removed; request bodies are covered by "re-checks the config once typing settles" | — | frontend tests |
| E501 line length in `api/nodes.py` | Introduced in this phase | Formatting | `ruff format` | ruff passes | backend |

No infrastructure or environment problems occurred. Docker, Testcontainers and Playwright browsers were all available.

---

## 11. Fixes Applied

1. **Type-list crash (F1).**
   - Symptom: `TypeError` for list types.
   - Root cause and implementation: see §10.
   - Verification: backend unit tests pass, and the full suite passes (1908).
2. **HTTP header typing (F4).**
   - Symptom: non-string header values were accepted at publish.
   - Implementation: `additionalProperties: {"type": "string"}`.
   - Verification: `test_non_string_header_values_are_rejected` asserts both the schema rejection (`config.headers.X-N`) and the retained runtime guard via direct `execute`.
3. **Stale local error after undo.**
   - Implementation: derived validity.
   - Verification: the component test above.
4. **Existing tests adjusted to the intended behavior change, not weakened.**
   - `test_config_required_and_unknown_keys`: the message regexes were updated to the new wording.
   - `test_invalid_configuration_is_a_configuration_error[credential:Not Valid]`: the case moved to `test_config_schema_rejects`, because the schema now rejects it before the node runs.
5. **Publish validation reports every config problem (follow-up after the first report).**
   - Symptom: `POST /workflows/{id}/validate` and publish returned one `invalid_config` issue per node, the first violation only, so fixing a config took one validate round per problem.
   - Root cause: `graph_validation._check_node_config` called `validate_config`, which raises on the first issue.
   - Implementation: it now iterates `definition.config_schema.issues(config)` and emits one `ValidationIssue(INVALID_CONFIG, "invalid configuration: <location> <message>", node_id)` per issue. Message format per issue is unchanged. Unchanged: plaintext secrets still short-circuit into one `plaintext_secret` issue (avoids a duplicate "must be a credential reference" for the same field); a malformed node schema (`NodeContractError`) still yields one issue.
   - Verification: `tests/domain/test_graph_validation.py::test_invalid_config_missing_key` now asserts both issues (`config.retries is required`, `config.x is not allowed`); full backend suite re-run (§13).

---

## 12. Testing & Verification

All results below are from runs on the final working tree, after the last code edit.

### Unit Tests

- **Backend** (`tests/domain/test_node_contract.py`), new tests:
  - `test_config_issues_collects_every_violation_with_its_path`: required, pattern, nested list item fields, nested closed objects, top-level unknown key, `location`, first-issue exception text
  - `test_config_issues_never_echo_values`
  - `test_nullable_type_lists`
  - `test_unsupported_types_are_contract_errors` (×4)
  - `test_additional_properties_schema_checks_map_values`
  - `test_credential_reference_format`
  - `test_annotations_are_not_validated_and_defaults_must_be_valid`
- **Backend** (`tests/nodes/`):
  - `test_secret_config_keys_declare_credential_references` (×13 built-ins)
  - `test_header_credentials_must_be_references`
  - rewritten `test_non_string_header_values_are_rejected`
- **Frontend** `configSchema.test.ts` (11): kind mapping, recursion (list → object → map of credentials), JSON fallbacks, nullable, annotations including falsy defaults, property order and required, `fits`, `emptyValue`, `humanize` / `describePath`, `issuesByPath`.
- **Frontend** `editorState.test.ts` (4 new): configure replaces only the node config and is undoable back to the clean saved graph; per-field coalescing; select ends the step; unknown node and in-drag no-ops.
- **Result:** PASS.

### Component Tests

`NodeInspector.test.tsx` (16) drives the real route, router, ApiClient and QueryClient against a stub `fetch`. It covers:
- every field kind rendered from the schema alone, with required, description, default, null toggle and `inputmode`
- nested edits (select, number, boolean, object, list with `maxItems`, map, JSON) saved into the PUT body
- defaults shown but not written, "Use default", cleared optional keys removed
- null set and clear
- backend issues at fields (`aria-invalid`, accessible description) and in the list, including an unknown key
- debounced re-check (the intermediate value is not sent)
- validation endpoint failure
- invalid number and JSON not committed
- credential reference stored, suggestions from metadata, no `/credentials/<name>` request
- a non-reference stored value hidden from the whole document, with the member 403 path
- save failure keeps the edit and shows the error
- catalog schema change (new required field, old key shown and removable)
- wrong-type value shown as JSON
- undo
- a node with no settings
- a node type missing from the catalog (no form, no validate request)

**Result:** PASS. Existing `EditorPage.test.tsx` also passes unchanged.

### API / Integration Tests (PostgreSQL Testcontainer)

`tests/integration/test_executions_api.py`:
- `test_node_catalog_lists_every_registered_type` is extended with SMTP format and default assertions.
- `test_validate_config_reports_every_issue_with_its_path`: member token, `core.http`.
- `test_validate_config_locates_secrets_without_echoing_them`: the secret string is absent from the response body.
- `test_validate_config_errors`: 404, 422, 401.

**Result:** PASS.

### Contract Tests

- `frontend/src/api/contract.node.test.ts` (36 tests) passes, including `validateNodeConfig` against the OpenAPI snapshot.
- `backend/tests/test_openapi_contract.py` passes with the regenerated snapshot.

### E2E Tests

- Mocked-backend Playwright (`tests/e2e`): 21/21 PASS (regression check; no new spec).
- Full-stack Playwright (`tests/fullstack`, real backend + PostgreSQL): 6/6 PASS. This includes the new "the inspector configures a built-in node from its backend schema":
  - add `core.http`
  - see the real backend's field error for `ftp://`
  - fix it, set method, add a header credential
  - see "Configuration valid", save
  - assert the stored config via API

### Security Tests

Covered by the tests listed above:
- validation messages never echo values (domain and API)
- plaintext secrets located by path
- credential format enforced
- built-in sensitive keys declare the format
- a non-reference credential value is never rendered
- no credential-value request is ever made

**Result:** PASS.

### Accessibility

- Verified through Testing Library role and name queries and accessible-description and validity assertions. These cover labels, `aria-invalid`, `aria-describedby` errors, fieldset/legend groups, the `role="status"` line and the labelled region.
- No automated axe audit was run, and no manual screen-reader test was done.

### Performance

None performed.

---

## 13. Quality Gates

| Quality Gate | Result | Notes |
|---|---|---|
| Formatter | PASS | `ruff format --check` (195 files); `prettier --check`. Failed once during the phase (E501 in `api/nodes.py`, unformatted `README.md`); fixed with the formatters |
| Linter | PASS | `ruff check`; `eslint .`. Failed once during the phase (`no-base-to-string`, `no-unnecessary-type-assertion`); fixed |
| Type Checker | PASS | `mypy .` (190 source files); `tsc -b`. Failed once during the phase (TS4111 index-signature access); fixed |
| Unit Tests | PASS | Frontend `vitest run`: 22 files, 262 tests |
| Integration Tests | PASS | Backend full `pytest`: 1908 passed, 6 skipped (pre-existing skips), 1 warning (Starlette `httpx` deprecation, pre-existing) |
| API Tests | PASS | Included in the backend suite (Testcontainers PostgreSQL) |
| E2E Tests | PASS | Playwright mocked 21/21; full-stack 6/6 |
| Build | PASS | `vite build` |
| Security Checks | PASS | Tests listed in §12; no separate scanner run |
| Accessibility | PASS (limited) | Role/label/description assertions only; no axe audit |
| Contract | PASS | OpenAPI snapshot test (backend) and `contract.node.test.ts` (frontend) |

---

## 14. Acceptance Criteria Verification

| Acceptance Criterion | Status | Evidence |
|---|---|---|
| Formulare werden aus Schema generiert | PASS | `properties()` / `fieldSpec()` + `PropertyFields`; no node-specific component exists. `NodeInspector.test.tsx` "renders every field kind from the node schema alone" uses fictional `acme.rich`; full-stack test renders `core.http` from the real catalog; "follows schema changes…" re-renders a changed schema |
| Required Fields werden berücksichtigt | PASS | Required marker and native `required` from schema `required` (`toBeRequired()` assertions); the backend's "is required" issue is shown at the field (`aria-invalid`) and in the list |
| Defaults werden berücksichtigt | PASS | Default in description ("Default: GET."), placeholder, select "Default (GET)", checkbox reflects default; "Use default" removes the key; defaults never written ("shows defaults without writing them…"). Backend: defaults validated against their schema; built-in defaults shared with node code |
| Secret Values werden niemals angezeigt | PASS | Credential fields show and store names/references only; a stored non-reference value is hidden (document-wide assertion); no endpoint returns secrets (no `/credentials/<name>` requests asserted); validation responses never echo values (API test with secret). Caveat in §20 (unannotated third-party plugin fields) |
| Backend Validation bleibt autoritativ | PASS | All config-validity messages come from `POST /nodes/{type}/config/validate`; the frontend only flags unparsable number/JSON text and does not commit it. Publish still runs graph validation. Full-stack test shows the real backend's message |

Roadmap scope types:
- String, Number, Boolean, Select, Secret Reference, JSON, List and Object are implemented as dedicated kinds.
- **Textarea** is used for templates and JSON only. There is no generic multiline hint for plain strings.
- **Prompt Editor** is a monospace textarea plus the schema's variable-syntax description, without highlighting or variable insertion.

Both are reported in §20. They do not block any acceptance criterion, which is why the status is "COMPLETE WITH LIMITATIONS".

---

## 15. Security Considerations

- **Secrets and credentials:**
  - The inspector never requests or holds secret values; only `credential:<name>` strings are stored.
  - A stored non-reference string in a credential field is not rendered (it may be a secret).
  - Credential suggestions use `GET /credentials` metadata (admin only). Members get 403, handled silently with `retry: false`.
- **Validation endpoint:**
  - authenticated, any role
  - stateless, nothing persisted
  - responses carry paths and messages, never values (tested)
  - It is bounded by the existing `RequestSizeLimit` middleware.
  - Schema `pattern`s run against user input. This is the same exposure as the existing draft validation; patterns come from trusted node code.
- **Plaintext secrets in plain fields:** a user can still type a secret into a field that is not declared as a credential reference (e.g. an unannotated plugin key). It is then held in editor state until the save is rejected (`PLAINTEXT_SECRET`, pre-existing backend rule). The validation endpoint also flags it by path. See §20.
- **Logging:** no frontend logging was added. Config bodies of the validate request are not logged by the backend; no new logging was introduced.
- **XSS:** all schema strings (titles, descriptions, enum labels, credential descriptions) render as React text. There is no `dangerouslySetInnerHTML`.
- **Authorization:** unchanged; the endpoint uses the existing protected router.

---

## 16. Performance Considerations

- Validation requests are debounced (400 ms) and cached per `(nodeType, config)` with `staleTime` 5 min. One inspector produces at most one request per settled edit.
- The query key includes the config object; TanStack hashes it structurally. This is fine for typical configs, and large configs make hashing proportionally more expensive. Not measured.
- `fieldSpec` / `properties` are recomputed on each inspector render (no memoization). Schemas are small (tens of keys); not measured.
- Backend `issues()` walks the config once. Cost is linear in config size plus regex checks.
- No measurements were performed.

---

## 17. Documentation Changes

| File | Reason | Content |
|---|---|---|
| `docs/architecture.md` §3.5 | New contract behavior | Supported keywords, annotations, `ConfigIssue`, `config_issues`, the endpoint and its authority, built-in format rule |
| `docs/architecture.md` §22 | Endpoint table | `/api/v1/nodes/{type}/config/validate` |
| `docs/sdk.md` | SDK 1.1.0 | Version; `NodeConfigSchema` keyword list, annotations, `issues`; new `CREDENTIAL_REFERENCE_FORMAT` row |
| `frontend/README.md` | New feature | "Node inspector" section: kinds, defaults, validation flow, credentials, schema changes, undo, tests |
| `docs/reports/frontend/phase-09-node-inspector.md` | Phase report | This document |

Should be updated in a future phase: `docs/workflow-format.md` does not mention the config schema annotations. It is not required, because the workflow format is unchanged.

---

## 18. Dependencies / Infrastructure Changes

None. No dependency, version, environment variable, Docker, database, Redis or test-infrastructure change. The SDK version constant (1.0.0 → 1.1.0) is a contract version, not a dependency.

---

## 19. Deferred Work

| What | Why deferred | Intended phase | Affects this phase? |
|---|---|---|---|
| Clicking a graph validation issue to focus the inspector field | Validation UX belongs to Phase 11 | Phase 11 | No |
| Rich prompt editor (variable highlighting or insertion from connected inputs) | Not required by acceptance criteria; the variables come from port data, which ties into Phase 10/11 | Not scheduled | No |
| Generic multiline hint for plain strings (e.g. a `format: "multiline"`) | No current built-in needs it; YAGNI | When a node needs it | No |
| Node label editing in the inspector | Not in Phase 09 scope | Phase 10 or later | No |
| Enforcing the credential format for third-party plugins | Breaking SDK change | Next SDK major | No |

---

## 20. Known Limitations

1. **Unannotated plugin secret fields.** Third-party plugin config keys that are sensitive but lack `format: credential-reference` render as plain text inputs. Plaintext is still rejected by the backend at save and publish.
2. **Prompt Editor is minimal.** `format: "template"` gives a monospace textarea plus the description `Reference variables as {{ name }} or {{ lead.name }}.` There is no highlighting, autocomplete or variable list.
3. **Textarea only for templates and JSON.** Long plain strings (e.g. `core.log.message`) use a single-line input.
4. **Integer fields commit any finite number.** Non-integers are reported by the backend ("must be integer"), not prevented locally.
5. **Credential suggestions are admin-only.** This follows the backend's credential authorization. Members type the name; references are not checked for existence until run time (pre-existing behavior).
6. **Coalesced undo.** Undo coalesces continuous typing per field until another field, node selection or undo. Typing in one field, pausing, and typing again is still one step.
7. **Stale issue list while re-checking.** For up to about 400 ms plus the request time, the issue list shows the previous config's issues; the status line says "Checking configuration…" during that time.
8. **Built-in node versions not bumped** despite the schema changes (rationale in §6). If strict versioning of schema tightenings is desired, `core.http`, `core.llm` and `core.smtp` would need patch bumps.
9. **Graph validation issues carry `node_id` but no config path.** Each config problem is its own `invalid_config` issue, but the field is only named inside the message (`invalid configuration: config.retries is required`).

---

## 21. Open Issues

| Issue | Impact | Severity | Area | Recommended next action | Target |
|---|---|---|---|---|---|
| Third-party plugins may declare sensitive config keys without `format: credential-reference` | Plaintext can be typed into editor state before the backend rejects it | MEDIUM | SDK / plugins / inspector | Decide whether to enforce at registration with an SDK 2.0 bump, or warn in `akasha node validate` / `check_node_contract` | SDK roadmap / CLI phase |
| `ValidationIssue` has no config path field | Phase 11 cannot focus the inspector field from a publish issue without parsing the message | LOW | backend graph validation / API | Add an optional `config_path` to `ValidationIssue` and the API schema (additive) | Phase 11 |
| No automated accessibility audit of the inspector | Possible unnoticed a11y issues | LOW | frontend | Add an axe check to the Playwright editor spec | Phase with accessibility audit |

---

## 22. Architectural / Product Decisions

- **The backend is the only judge of config validity.** The frontend does not interpret constraints (pattern, min/max, required). It only rejects text it cannot parse into a value, so that half-typed input is never committed.
- **Unknown or mismatched values are never dropped silently.** Keys not in the schema and wrong-type values are shown as JSON with a remove option. This keeps drafts and schema changes honest.
- **Defaults are documentation.** Configs contain only what the user set. "Use default" is a key removal. Node code and schema share default constants.
- **Clearing an optional field removes the key; clearing a list item or map value keeps an empty value.** Absent and empty are different for the backend (`required`, defaults).
- **Config edits are ordinary draft edits.** They share undo history, save/autosave, the revision conflict handling and the leave guard. The inspector has no separate save button or local "apply" step, so there is only one source of unsaved state.
- **The inspector is keyed by node id.** Half-typed local text belongs to one node and is reset on switching.
- **Credential selection by name with `<datalist>`.** This is a native control that works for admins (suggestions) and members (free text) without a role check in the UI.
- **No new frontend dependency** (no JSON-Schema form library). The supported subset is small and backend-defined.

---

## 23. Repository / Git State

- **Branch:** `main`.
- **HEAD:** `e19ec47 frontend phase 08 - node library`.
- **Working tree:** uncommitted changes from this phase only; nothing was committed.
  - Modified (27): every file listed in §4 under Modified.
  - Untracked (6): `ConfigForm.tsx`, `NodeInspector.tsx`, `NodeInspector.test.tsx`, `configSchema.ts`, `configSchema.test.ts`, this report.
- **Unrelated changes:** none detected. The working tree was clean at the start of the session.

---

## 24. Phase Completion Assessment

### Implemented
- Schema-driven inspector covering every kind the backend schema subset can express.
- Backend per-field config validation endpoint and issue-collecting validator.
- Nullable, map and credential-reference support.
- Annotated built-in schemas; SDK 1.1.0.
- Undoable config edits through the existing draft persistence.

### Verified
- Backend: 1908 tests (Testcontainers PostgreSQL), ruff, mypy.
- Frontend: 262 unit/component tests, ESLint, `tsc`, Prettier, build.
- Playwright: mocked 21/21, full-stack 6/6.
- All runs on the final tree.

### Not Implemented
- Rich prompt editor.
- Generic multiline text hint.
- Label editing.

### Deferred
- See §19.

### Open
- See §21.

### Blocking Issues
- None.

**Phase Status:** COMPLETE WITH LIMITATIONS

---

## 25. Handoff to Next Phase

- **What the next phase can assume:**
  - Selecting one node shows the inspector.
  - `dispatch({type: 'configure', nodeId, config, field})` is the single way configs change.
  - Config changes are undoable and saved with the draft.
- **Contracts now available:**
  - `POST /api/v1/nodes/{type}/config/validate` returns `{valid, issues[{path, message}]}`.
  - `NodeDefinition.config_issues` and `NodeConfigSchema.issues` in Python.
  - `ConfigIssue.path` uses the same key/index form as the frontend's `pathKey`/`describePath`.
- **For Phase 10 (graph editing):**
  - Deleting a node must also clear selection; the inspector disappears automatically.
  - Duplicating a node copies `config` as is.
  - The reducer's `lastEdit` must be reset by every new commit; `commit()` already does this.
- **For Phase 11 (validation/publish):**
  - Workflow validation issues currently carry `node_id` but no config path.
  - To focus a field from a validation issue, extend `ValidationIssue` with the `ConfigIssue` path rather than parsing messages.
- **Must not be changed casually:**
  - `format: "credential-reference"` semantics (enforced and SDK-public).
  - "Defaults are never written into configs".
  - Message wording of `ConfigIssue` is user-facing in the inspector.
  - The `openapi.json` snapshot must be regenerated on any API change.
- **Constraints:**
  - The validate endpoint checks one node in isolation. It knows nothing about edges or graph-level rules.
