# Phase 18 — Triggers UI

Report date: 2026-10-04. Branch `main`. Base commit `b319e54` (frontend phase 17 — credentials ui). All work described here is **uncommitted** in the working tree (§23).

## 1. Executive Summary

**Supposed to achieve.** Roadmap §24 ("Phase 18 — Triggers UI"): configure and manage triggers.
- Scope: Manual, Cron, Interval, Webhook, API and Event triggers "soweit Backend vorhanden" (as far as the backend provides them).
- UI: list, create, edit, enable/disable, delete, configuration and status.
- No trigger logic of its own in the frontend.

The session prompt added:
- validation;
- tests for every type's configuration UI, invalid schedules, missing fields, API errors and authorization failures;
- implement, test and document any missing backend function.

**Discovered.**
- **Trigger CRUD:** the backend already offered trigger CRUD. Triggers are part of the Workflow Draft, replaced by `PUT /api/v1/workflows/{id}` under optimistic concurrency, and validated by `POST /workflows/{id}/validate`.
- **Event triggers never fired:** they were accepted by the domain, but no code dispatched them (`TriggerType.EVENT` was never fired anywhere).
- **Manual triggers:** they could only be fired through the invoke endpoint, with no UI path.

**Actually implemented.**
- **Frontend:**
  - the page `/workflows/:workflowId/triggers` (`frontend/src/features/triggers/`);
  - a create/edit dialog covering all six trigger types;
  - Run for published manual triggers;
  - a validation query pinned to the draft revision;
  - a shared credential-reference input (pulled out of the editor);
  - 23 component tests and 2 full-stack Playwright tests.
- **Backend:**
  - EVENT trigger dispatch: domain rules, validation code `INVALID_TRIGGER_EVENT`, `EventTriggerDispatcher`;
  - loop protection through `ExecutionMetadata.trigger_depth` and `AKASHA_LIMIT_EVENT_TRIGGER_DEPTH`;
  - authorization through the existing `may_access_workflow` rule;
  - two event-repository read methods;
  - 10 integration tests, domain tests, and config and lifecycle tests.

**Resulting state.** A user can:
- list a workflow's draft triggers, with type, config summary, endpoint, enabled state, publish state relative to the latest version, and the backend's per-trigger validation verdict;
- create, edit, enable/disable and delete triggers of every type;
- run a published manual trigger.

Every write is a revision-checked draft PUT. All six trigger types fire in the backend. An event trigger created in the UI starts its workflow when a source workflow's run completes, verified full-stack against PostgreSQL and Redis.

**Acceptance criteria.** All three roadmap criteria pass (§14).

**Phase Status:** COMPLETE WITH LIMITATIONS

The limitations (§20) are:
- no next-fire / last-fired display;
- event dispatch is at-most-once without catch-up;
- no workflow sharing between members.

## 2. Phase Objective

Roadmap §24:
- **Goal:** "Trigger konfigurieren und verwalten" (configure and manage triggers).
- **Scope:** Manual, Cron, Interval, Webhook, API and Event, "soweit Backend vorhanden". UI: Trigger List, Create, Edit, Enable/Disable, Delete, Configuration, Status.
- **Acceptance:**
  - trigger configuration uses backend contracts;
  - activation/deactivation is visible;
  - webhook information is shown securely.
- **OpenCode prompt:** "Unterstütze nur Trigger-Typen, die vom Backend tatsächlich bereitgestellt werden … Keine eigene Trigger-Logik im Frontend. STOP nach Phase 18." (Support only trigger types the backend actually provides; no trigger logic of its own in the frontend.)

`frontend/AGENTS.md` rules that apply:
- §7: trigger state comes from the backend.
- §48: distinguish the six types; the frontend must not treat a config as valid when the backend rejects it.
- §54: trigger delete needs confirmation.
- §94: use revisions.

**Not responsible for:**
- trigger execution logic in the frontend (explicitly excluded);
- reports (Phase 19), plugins (Phase 20), the login UI (Phase 21) and security hardening (Phase 23);
- a workflow sharing/grant model.

**Scope extension (requested by the user during the phase, in follow-up prompts):**
- "fix this": make event triggers fire, and fix other issues worth fixing in this phase. This led to the backend event dispatcher and the manual Run button.
- "Fix it": let an admin's workflow react to other users' workflows. This led to the access-rule change.

## 3. Scope Implemented

### Frontend/UI
- **`TriggersPage`** (`features/triggers/TriggersPage.tsx`, route `/workflows/:workflowId/triggers`):
  - **Data:** loads the workflow (`workflowQueries.detail`); loads the latest version when `current_version > 0` (`workflowQueries.version`, immutable, `staleTime: Infinity`); and loads the validation verdict of the shown revision (`workflowQueries.validation`).
  - **Info alert:** says triggers are saved to the draft at revision N and fire only from the latest published version, with links to that version and to the editor (where publishing happens).
  - **Table "Triggers"** (`Table`, caption hidden under the Card title), with columns:
    - **Type:** label plus id.
    - **Configuration:** the `triggerSummary` text plus the endpoint. Webhook rows show the URL and the signing scheme; manual/API rows show the invoke URL.
    - **Status:** an Enabled/Disabled badge (with a dot) plus Published / Unpublished changes / Not published.
    - **Validation:** "Checking…", "Not checked", "Valid", or the backend issue messages with an sr-only "Error:" prefix.
    - **Actions:** Run (manual only, when the published copy is enabled), Disable/Enable, Edit, Delete. Every button has an accessible name containing the trigger id.
  - **Removed from the draft:** a warning lists published trigger ids that are absent from the draft (they stay live until the next publish).
  - **States:** loading and error (with Retry) through `QuerySection`, a not-found state, an empty-table text, a validation-failed alert with Retry, and a save-error alert.
- **`TriggerFormDialog`** (`features/triggers/TriggerFormDialog.tsx`):
  - **Create:** a Type select over `creatableTypes` (all six). **Edit:** the type is fixed, and the id is shown.
  - **Fields per type:**
    - **Cron:** expression input.
    - **Interval:** a number input, `min=1`, `max=31536000`, `step="any"`.
    - **Webhook:** signing secret through `CredentialInput`, required before submit.
    - **Event:** an Event select over `triggerableEvents`, plus a Source workflow select ("This workflow" plus the workflows the caller can access).
  - Enabled switch.
  - On edit, config keys the form does not know are kept.
  - A stored value the pickers do not offer is shown as "Unsupported: …" or "Unavailable workflow …" rather than replaced.
  - The form content remounts on every open (`Dialog` mounts content only while open).
- **`triggerTypes.ts`:** labels, descriptions, `creatableTypes`, `triggerableEvents`, `MAX_INTERVAL_SECONDS`, `WEBHOOK_SECRET_FIELD`, `EVENT_SOURCE_FIELD`, `newTriggerId()` (`trig_<32 hex>`, the backend's `new_id("trig")` shape), and `triggerSummary()`.
- **`CredentialInput`** (`features/credentials/CredentialInput.tsx`): a credential-name input with a `datalist` of credential names. It stores `credential:<name>` and hides a stored non-reference value. Moved out of `editor/ConfigForm.tsx` so the webhook form reuses it; `ConfigForm.CredentialField` now wraps it.
- **Navigation:** a "Triggers" link in the `WorkflowDetailPage` header.

### Frontend API layer
- `queryKeys.workflows.validation(workflowId, revision)` and `workflowQueries.validation(...)`, which call the existing `validateWorkflow(client, id, revision)`. The key sits below `workflows.detail(id)`, so invalidating the workflow reaches it.
- The `validationCodeSchema` enum gained `'invalid_trigger_event'`.
- Writes reuse the existing `useUpdateWorkflow` (draft PUT) and `useInvokeTrigger`; no new mutation hooks.

### Domain (backend)
- **`domain/trigger.py`:**
  - `EVENT_SOURCE_FIELD = "source_workflow_id"`;
  - `TRIGGERABLE_EVENTS` = {`execution.completed`, `execution.failed`, `execution.cancelled`, `execution.timed_out`, `workflow.published`};
  - `event_problem(trigger)`, plus the helper `_is_id`;
  - `TriggerContext.depth: int = 0` (≥ 0; bool rejected).
- **`domain/graph_validation.py`:** `ValidationCode.INVALID_TRIGGER_EVENT`.
- **`domain/workflow.py`:** `validate_triggers()` reports `event_problem` as `INVALID_TRIGGER_EVENT`.
- **`domain/execution.py`:** `ExecutionMetadata.trigger_depth: int = 0` (≥ 0).

### Persistence (backend)
- **`EventRepository` protocol and `SqlAlchemyEventRepository`:** `list(..., positions=...)` filters by `position IN (...)`, and `max_position()` returns `COALESCE(MAX(position), 0)`.
- **`SqlAlchemyExecutionRepository.save`:** writes `metadata.trigger_depth`. `_metadata()` reads it with a default of 0.

### Runtime (backend)
- **`runtime/event_triggers.py`:** `EventTriggerDispatcher` with `tick()`, `run()`, `_dispatch`, `_refusal`, `_context` and `_log_extra`; module-level `_may_observe`; constants `BATCH_SIZE = 500` and `MAX_GAPS = 10_000`.
- **`application/triggers.py`:**
  - `TriggerRegistry.list_event_triggers()`, with `list_scheduled()` refactored onto the shared `_list_enabled(types)`;
  - `TriggerService._create` passes `trigger_depth=context.depth`.
- **`bootstrap.py`:**
  - `Container.event_triggers`;
  - `_scheduler_task` replaced by `_tasks: list[asyncio.Task]` (scheduler and dispatcher), with an idempotent `start()` and a `shutdown()` that cancels and awaits all tasks;
  - the dispatcher is created when `scheduler_enabled`.
- **`config.py`:** `limit_event_trigger_depth` (default 8, range 1–100).

### Testing
See §12.

## 4. Files Changed

| File | Change | Reason |
|---|---|---|
| `frontend/src/features/triggers/TriggersPage.tsx` | Created | Trigger list, status, actions, page states |
| `frontend/src/features/triggers/TriggerFormDialog.tsx` | Created | Create/edit form for all types |
| `frontend/src/features/triggers/triggerTypes.ts` | Created | Type metadata, summaries, mirrored constants |
| `frontend/src/features/triggers/TriggersPage.test.tsx` | Created | 23 component tests |
| `frontend/src/features/credentials/CredentialInput.tsx` | Created | Shared credential-reference input |
| `frontend/src/features/editor/ConfigForm.tsx` | Modified | `CredentialField` delegates to `CredentialInput` (−71/+ lines) |
| `frontend/src/features/editor/configSchema.ts` | Modified | `CREDENTIAL_PREFIX` moved to `CredentialInput.tsx` |
| `frontend/src/features/workflows/WorkflowDetailPage.tsx` | Modified | "Triggers" link |
| `frontend/src/app/routes.tsx` | Modified | Lazy route `/workflows/:workflowId/triggers` |
| `frontend/src/api/queries.ts` | Modified | Validation key and query options |
| `frontend/src/api/workflows.ts` | Modified | `invalid_trigger_event` enum value |
| `frontend/src/api/openapi.json` | Modified (regenerated) | Contract snapshot: +1 enum value |
| `frontend/tests/fullstack/workflows.spec.ts` | Modified | 2 trigger journeys (+170 lines) |
| `frontend/tests/fullstack/backend.sh` | Modified | Credential key; scheduler enabled |
| `frontend/README.md` | Modified | "Triggers" section |
| `backend/src/akasha/runtime/event_triggers.py` | Created | Event trigger dispatcher |
| `backend/src/akasha/domain/trigger.py` | Modified | Event rules, context depth |
| `backend/src/akasha/domain/execution.py` | Modified | `trigger_depth` |
| `backend/src/akasha/domain/graph_validation.py` | Modified | New validation code |
| `backend/src/akasha/domain/workflow.py` | Modified | Validation of event triggers |
| `backend/src/akasha/application/triggers.py` | Modified | `list_event_triggers`, depth passthrough |
| `backend/src/akasha/application/persistence.py` | Modified | Event repository protocol |
| `backend/src/akasha/infrastructure/persistence.py` | Modified | `positions`, `max_position`, depth codec |
| `backend/src/akasha/bootstrap.py` | Modified | Dispatcher wiring, task list |
| `backend/src/akasha/config.py` | Modified | `limit_event_trigger_depth` |
| `backend/tests/integration/test_event_triggers.py` | Created | 10 PostgreSQL integration tests |
| `backend/tests/domain/test_trigger_credentials_plugin.py` | Modified | `TestEventProblem`, `TestTriggerDepth` |
| `backend/tests/application/test_trigger_service.py` | Modified | `FakeEvents` follows the protocol |
| `backend/tests/test_lifecycle.py` | Modified | Both loops start/stop; idempotent start |
| `backend/tests/test_config.py` | Modified | Depth limit bounds |
| `backend/README.md` | Modified | Setting row |
| `docs/architecture.md` | Modified | §129 implementation notes |
| `docs/reports/frontend/phase-18-triggers-ui.md` | Created | This report |

- **New directory:** `frontend/src/features/triggers/`.
- **Migrations:** none. `trigger_depth` lives in the existing `executions.metadata` JSONB document.
- **Generated file:** `frontend/src/api/openapi.json`, regenerated with `AKASHA_UPDATE_OPENAPI_SNAPSHOT=1 uv run pytest tests/test_openapi_contract.py`.

## 5. Architecture Changes

### 5.1 Trigger CRUD stays on the draft PUT

- **Decision.** No `/workflows/{id}/triggers` resource. The UI edits triggers by replacing the draft.
- **Context.** Architecture §125 puts triggers on the draft and publishes them with it. The roadmap lists `/api/v1/triggers` only as an example.
- **Chosen approach.** Every create/edit/toggle/delete is `PUT /workflows/{id}` with `{...draft, triggers: next}` and `expected_revision`.
- **Alternatives considered.** A dedicated trigger resource with its own endpoints.
- **Reason.** It would duplicate the draft write and its optimistic concurrency, and create two ways to change one aggregate.
- **Consequences.**
  - Trigger edits and editor saves share the draft revision. Whichever is second gets a 409, and the page reloads.
  - Changes apply only after publishing.

### 5.2 Event trigger dispatch reads the persisted event log

- **Decision.** A dedicated `EventTriggerDispatcher` task polls the `events` table.
- **Context.**
  - Executions run in worker processes.
  - `EventPublisher` is in-process and best effort (`application/events.py`).
  - Event positions come from an identity column assigned at insert, so a global cursor can skip a concurrently committing event. The repository docstring already warned about this.
- **Chosen approach.**
  - The first tick anchors at `max_position()`.
  - Each tick reads `position > high` (batch 500), records skipped positions as gaps with a first-seen time, re-reads gaps with `positions=`, and drops gaps older than 60 s (`MAX_GAPS` cap).
  - Matching triggers fire through `TriggerService.fire`.
  - The task runs where `scheduler_enabled` is true, with the same poll interval.
- **Alternatives considered.**
  - Subscribing to `EventPublisher`: it would miss worker-process events.
  - A persisted cursor with replay: it needs new persistence and idempotency keys, and conflicts with the "no catch-up" rule used for cron (§125).
- **Reason.** It is correct across processes, adds no schema, and keeps one firing funnel (§32).
- **Consequences.**
  - At most once per event and trigger.
  - No dispatch of events stored while no dispatcher ran.
  - One replica only, like the scheduler.

### 5.3 Loop protection through execution depth

- **Decision.** `ExecutionMetadata.trigger_depth`: 0 for non-event runs, the source execution's depth + 1 for event runs (1 for `workflow.published`).
- **Context.** Architecture §130/§131 require protection against event-trigger recursion.
- **Chosen approach.** The depth is stored in the metadata JSON. A fire at depth > `AKASHA_LIMIT_EVENT_TRIGGER_DEPTH` is refused and logged (`event_trigger_depth_exceeded`).
- **Alternatives considered.** Rate limiting, or forbidding self-sources.
- **Reason.** Depth bounds every chain deterministically, and still allows legitimate self- or multi-hop chains.
- **Consequences.** The value is not exposed in the API. It appears in the DB metadata and in logs.

### 5.4 Event source authorization through the API's access rule

- **Decision.** A trigger may react to another workflow only if the trigger workflow's owner passes `may_access_workflow(owner, source.owner_id)` and is not disabled. Two ownerless workflows may chain.
- **Context.** Events must not let a user observe or chain onto workflows they cannot access (§49). A first version required identical owners; the user asked to allow admins.
- **Chosen approach.** `_may_observe(uow, workflow_id, source)` runs at fire time.
- **Alternatives considered.** The same-owner rule (too narrow for admins); a grant model (out of scope).
- **Reason.** It reuses the single existing authorization rule, with no new model.
- **Consequences.** Members cannot share sources with each other until a grant model exists.

## 6. Domain / Data Model Changes

- **`TriggerContext.depth`** (new field, default 0): backward compatible for all constructors.
- **`ExecutionMetadata.trigger_depth`** (new field, default 0): persisted in `executions.metadata` JSONB. Rows without the key read as 0, so no migration is needed.
- **`ValidationCode.INVALID_TRIGGER_EVENT`** (new enum member). It is a publish gate, not a construction error: drafts with old or free-text event names stay loadable (consistent with §125).
- **EVENT trigger config:**
  - `event` (required non-empty string, unchanged construction rule) must be in `TRIGGERABLE_EVENTS` to publish;
  - the optional `source_workflow_id` must be a `wf_<32 hex>` id to publish.
- **Compatibility:** a published version that contains an event trigger with a non-triggerable `event` stays published. That trigger simply never matches anything. A republish reports the issue.
- No tables, indexes or constraints changed.

## 7. API / Contract Changes

- **`POST /api/v1/workflows/{id}/validate`, `POST /publish`:** the `ValidationIssue.code` enum gains `invalid_trigger_event`. The message names the allowed events or the field, never config values.
  - *Compatibility:* additive for the backend. The frontend's strict Zod enum was updated in the same change; an older frontend build would reject a response containing the new code (`malformed_response`).
- **Execution input of event-triggered runs** (new semantics, existing field): `{"event": {"type", "event_id", "workflow_id", "workflow_version", "execution_id", "occurred_at", "data"}}`. `data` is the event's closed payload: ids, statuses, error codes.
- **Executions created by event triggers:** `trigger_type: "event"` and `trigger_id` in `ExecutionDetail` (existing fields). The correlation id is the source event's, when present.
- **No new endpoints. No endpoint removed or changed in shape.**
- **Frontend usage:** `PUT /workflows/{id}` (trigger writes), `POST /workflows/{id}/validate` with `expected_revision`, `GET /workflows/{id}/versions/{n}`, `GET /workflows` (source picker, `limit=100`, `sort=name`), `GET /credentials` (names, admin only; members type freely), and `POST /workflows/{id}/triggers/{tid}/invoke` (Run).

## 8. Implementation Details

### Frontend
- **Status derivation:** `publishStates()` runs `versions/compare.ts::diffVersions(latest, draft)` over triggers:
  - added → Not published;
  - changed → Unpublished changes;
  - otherwise → Published;
  - removed ids → the warning list.
  It is display only. `live` maps published trigger ids to their published copy and gates the Run button (`trigger_type === 'manual' && live.get(id)?.enabled`).
- **Writes:** `saveTriggers(next, successTitle, onSuccess)` calls `useUpdateWorkflow` with the unchanged name and description. On success the mutation sets `workflows.detail` from the response, so the revision changes and the validation query re-runs under the new key. On a 409 the workflow query is invalidated, and the error stays visible until the next attempt. No optimistic updates (AGENTS §85).
- **Validation query:** `retry: false`. A 409 (draft changed elsewhere) or 5xx shows "Could not check the triggers" with Retry, and cells show "Not checked".
- **Run:** `useInvokeTrigger`. On success: a toast, then navigation to `/executions/{id}`. On error: a danger toast with the backend message.
- **Webhook display:** `triggerSummary` shows `Signed with credential:<name>` only for reference strings; anything else shows "Signing secret is not a credential reference". The URL is `new URL(client.baseUrl + path, window.location.origin)`.
- **Client-side checks (UX only):**
  - native `required`/`min`/`max` attributes;
  - the webhook secret is required before submit;
  - cron syntax is not checked in the browser.

### Backend dispatcher control flow (`EventTriggerDispatcher.tick`)
1. The first call stores `max_position()` and returns 0.
2. Read the fresh batch (all event types, needed for gap detection) and the gap re-reads in one UoW.
3. Update `high` and the gaps; expire old gaps; cap their number.
4. Filter candidates to `TRIGGERABLE_EVENTS`. If there are none, return without loading triggers.
5. `registry.list_event_triggers()` (enabled EVENT triggers on latest versions).
6. Per event: match on `config.event == event_type` and `config.source_workflow_id` (default: trigger workflow) `== event.workflow_id`.
7. Load the source workflow and the source execution's depth.
8. Compute `_may_observe` per foreign trigger workflow.
9. Per match: refuse (logged warning `event_trigger_depth_exceeded` / `event_trigger_source_denied`) or fire. A fire exception is logged (`event_trigger_failed`) and the loop continues.

`run()` sleeps `poll_seconds` unless the last batch was full (`behind`). A failed tick is logged (`event_trigger_tick_failed`) and retried.

**Lifecycle:** `Container.start()` creates the scheduler and dispatcher tasks once. `shutdown()` cancels and awaits them before the workers stop and the DB closes.

## 9. Findings

### Event triggers were accepted but never fired
- **Impact:** HIGH
- **Resolution:** FIXED
- **Notes:** The domain validated `{"event": ...}` since Phase 10, but nothing consumed it. The first Phase 18 pass hid creation in the UI; the dispatcher (§5.2) then made the type real.

### Global event positions do not follow commit order
- **Impact:** MEDIUM
- **Resolution:** FIXED (gap tracking)
- **Notes:** Already documented in the `EventRepository.list` docstring. It is reproduced in `test_an_event_committed_after_a_later_one_is_still_dispatched` with a real open transaction.

### Manual triggers had no UI firing path
- **Impact:** MEDIUM
- **Resolution:** FIXED (Run button)
- **Notes:** Ad-hoc version runs existed (Phase 13); configured manual triggers were reachable only through the API.

### The full-stack backend had no credential encryption key
- **Impact:** LOW (test infrastructure)
- **Resolution:** FIXED
- **Notes:** `POST /api/v1/credentials` answered 500 in the full-stack environment, because the credential store is disabled without `AKASHA_CREDENTIAL_ENCRYPTION_KEY`. `backend.sh` now generates a throwaway key per run.

### The same-owner source rule excluded admins
- **Impact:** MEDIUM
- **Resolution:** FIXED
- **Notes:** Replaced by `may_access_workflow` (§5.4) at the user's request.

### Shutdown log noise in the full-stack suite
- **Impact:** LOW
- **Resolution:** OPEN
- **Notes:** The in-process worker, and now the scheduler/dispatcher, log failed DB/Redis polls after `teardown.ts` removes the containers. Tests are unaffected.

### `TriggerRegistry` loads all latest versions per call
- **Impact:** LOW
- **Resolution:** DEFERRED
- **Notes:** A pre-existing pattern (`list_scheduled` runs every second). `list_event_triggers` is called only for ticks that have triggerable events. Not measured.

## 10. Bugs / Problems Found

| Problem | Root cause | Fix | Regression test | Origin |
|---|---|---|---|---|
| Event triggers never fired | No dispatcher existed | `EventTriggerDispatcher` | `test_event_triggers.py` (10) | Pre-existing gap |
| Credential creation 500 in the full-stack suite | No encryption key in `backend.sh` | Throwaway key | The full-stack trigger test creates a credential | Test infrastructure |
| Missing interval reads 0 / TS4111 errors | Dot access on index-signature config (`noPropertyAccessFromIndexSignature`) | Bracket access | Typecheck | Introduced and fixed in this phase |
| Lint errors in the test stub (`no-base-to-string`, unused var, dynamic delete) | First draft | Typed options, `jsonBody` helper, literal-key delete | Lint | Introduced and fixed in this phase |
| Playwright `getByLabel('Event', {exact})` timed out | The required marker is part of the label text | Regex `/^Event/` | Full-stack event test | Introduced and fixed in this phase |
| 2 component tests failed after enabling event creation | The tests encoded the old "not creatable" behavior | Rewritten to the new behavior | `TriggersPage.test.tsx` | Intended behavior change |

## 11. Fixes Applied

- **Event dispatch:**
  - *Symptom:* event-trigger workflows never ran.
  - *Root cause:* no consumer.
  - *Implementation:* §5.2/§8.
  - *Verification:* 10 integration tests, mutation checks, and the full-stack event journey.
- **Manual trigger Run:**
  - *Symptom:* there was no way to fire a configured manual trigger from the UI.
  - *Implementation:* a Run button through `useInvokeTrigger`.
  - *Verification:* component tests (success navigation, hidden when disabled or not published, refusal toast) and the full-stack step "Run opens the execution".
- **Admin sources:**
  - *Symptom:* an admin's trigger could not observe other users' workflows.
  - *Root cause:* the same-owner rule.
  - *Implementation:* `_may_observe` uses `may_access_workflow` and checks `disabled`.
  - *Verification:* `test_an_admin_reacts_to_any_workflow_until_disabled`, `test_a_member_cannot_react_to_another_users_workflow`, and three mutation checks.
- **Full-stack credential key:**
  - *Symptom:* a 500 on credential create.
  - *Verification:* the full-stack trigger test passes.

## 12. Testing & Verification

### Unit Tests (backend)
`tests/domain/test_trigger_credentials_plugin.py`:
- `TestEventProblem`: each triggerable event is accepted; `node.completed`, `execution.created` and `lead.created` are rejected, with no value echoed; the source id shape; other types are unaffected.
- `TestTriggerDepth`: defaults; −1 and `True` rejected for the context and the metadata.

`tests/test_config.py`: depth limits 0 and 101 rejected. Result: PASS.

### Component Tests (frontend)
`TriggersPage.test.tsx`, 23 tests against a stateful stub backend: PUT enforces `expected_revision` and bumps the revision; validate answers only for the stored revision.

- **Listing:** type, summary, endpoint, Enabled/Disabled, Published / Unpublished changes / Not published, backend issues, removed-from-draft notice.
- **Secrets:** a plaintext webhook secret is never in the DOM or the input.
- **Empty state.**
- **Creating each type** (`it.each`: manual, api, cron, interval, webhook). Checks the PUT body (revision, name/description unchanged, other triggers kept, config, id shape), the saved row and re-validation.
- **Event:** creation on another accessible workflow (the option list is all other caller-visible workflows); edit back to own workflow, with unsupported stored values shown.
- **Run:** navigates to `/executions/exec_1`, exactly one invoke; hidden for disabled or not-published copies and for API triggers. A refused Run (409) shows a toast and stays on the page.
- **Invalid schedule:** the backend `invalid_trigger_schedule` message appears after save.
- **422 `DOMAIN_VALIDATION_ERROR`:** stays in the dialog, no state change.
- **Missing webhook secret:** blocked without a PUT.
- **Edit** keeps the id and unknown keys and toggles enabled. **Enable/Disable** sends revisions 3 then 4. **Delete** happens only after confirmation.
- **409 conflict:** the newer draft is reloaded and nothing is overwritten.
- **403 authorization:** the message is shown in the dialog and the list is unchanged.
- **404** not-found state; **500** load error with Retry; validation 500 shows "Not checked".

Result: PASS (23/23). Existing editor tests still pass after the `CredentialInput` extraction.

### Integration Tests (backend, PostgreSQL via Testcontainers)
`tests/integration/test_event_triggers.py`, 10 tests. Workflows are published over HTTP, and source runs execute through `ExecutionRuntime`.

- cross-workflow fire, with the input, depth and listing checked;
- no replay of history before the first tick;
- a self-trigger stops at depth 3 with `max_depth=3`;
- non-matching and disabled triggers are ignored;
- `workflow.published` as a source;
- a member cannot react to an admin's workflow;
- an admin reacts to a member's workflow until disabled;
- an event committed after a later one is dispatched, using a real open transaction;
- a rolled-back gap expires after 60 s on a fake clock;
- `/validate` reports `invalid_trigger_event` without echoing values.

`tests/test_lifecycle.py`: both tasks start and stop, and a second `start()` is a no-op. Result: PASS.

**Mutation checks** (the source was temporarily modified, the targeted test run, then the file restored and confirmed by `diff`). Each of these made its test fail:
- no gap recording;
- depth check disabled;
- the anchor set to 0;
- the access rule replaced by the same-owner rule;
- the disabled check removed;
- the access rule always true.

A conflict-reload mutation in the frontend also failed its component test.

### E2E Tests
- **`npm run test:e2e`** (stubbed backend): 23 passed, unchanged suite.
- **`npm run test:fullstack`** (production bundle, real backend, PostgreSQL 17.2, Redis 8.2.1): 15 passed, including:
  - *"triggers are configured, validated and stored by the backend":*
    1. A cron that never fires shows `cron expression is invalid or never fires`; it is fixed to "Valid", then disabled.
    2. A webhook is signed with a real credential.
    3. After reload, the stored draft is compared through the API.
    4. After publish: "Published"; an unsigned webhook call gets 401.
    5. Delete, then the "Removed from the draft" notice.
    6. Run opens `/executions/exec_…`.
  - *"an event trigger starts a workflow when another one completes":* an event trigger is created in the UI on the upstream workflow; the upstream run completes in the worker; the downstream workflow gets one `event` execution within 20 s (`expect.poll`).

### Security Tests
- Component: the plaintext webhook secret is not rendered.
- Full-stack: an unsigned webhook gets 401.
- Integration: cross-user source refused; disabled admin refused; validation messages without config values.
- No dedicated security scan was run.

### Accessibility Tests
No automated accessibility tooling (axe) was run. What exists:
- tests query by role and accessible name (buttons named with the trigger id, a labelled switch, dialog names);
- status is shown in text (badges with labels), never by color alone;
- validation errors carry an sr-only "Error:" prefix.

Keyboard navigation and visual focus were not manually verified in a browser this phase.

### Performance Tests
None.

## 13. Quality Gates

The final run happened after the last code change (the admin access fix). No code has changed since.

| Quality Gate | Result | Notes |
|---|---|---|
| Formatter | PASS | `ruff format --check` (208 files); `prettier --check` |
| Linter | PASS | `ruff check`; `eslint .`. Intermediate failures (4 test lint errors, a dynamic delete, a long line) fixed |
| Type Checker | PASS | `mypy .` (203 source files); `tsc -b`. Intermediate TS4111 errors fixed |
| Unit/Component Tests | PASS | Frontend `vitest`: 33 files, 416 tests |
| Backend tests (unit + integration + API) | PASS | `uv run pytest`: 2005 passed, 6 skipped (5 OpenRouter smoke without key, 1 deployment test without `AKASHA_TESTS_DEPLOYMENT=1`, both opt-in and pre-existing) |
| Contract | PASS | OpenAPI snapshot regenerated; `test_openapi_contract.py` and the frontend `contract.node.test.ts` pass |
| E2E (stubbed) | PASS | 23 |
| E2E (full-stack) | PASS | 15. An earlier run failed on the credential key and on a label locator; both fixed |
| Build | PASS | `vite build` |
| Security Checks | N/A | No scanner run; see §12 |
| Accessibility | N/A | No automated audit; see §12 |

## 14. Acceptance Criteria Verification

| Acceptance Criterion | Status | Evidence |
|---|---|---|
| Trigger-Konfiguration verwendet Backend Contracts. (Trigger configuration uses backend contracts.) | PASS | Writes go through `PUT /workflows/{id}` with `TriggerBody` (`api/workflows.ts` `triggerSchema`); verdicts come from `/validate` (`validationIssueSchema` with `trigger_id`); structural errors are the backend's 422, shown verbatim. No schedule/cron logic in the browser. Types and config keys mirror `domain/trigger.py` and are documented as mirrors. Full-stack test compares the stored draft with the UI. |
| Aktivierung/Deaktivierung ist sichtbar. (Activation/deactivation is visible.) | PASS | An Enabled/Disabled text badge per row, plus Published / Unpublished changes, derived from draft vs. latest version. Toggle tests (component) and full-stack "Disabled" after reload. |
| Webhook-Informationen werden sicher dargestellt. (Webhook information is shown securely.) | PASS | Only the URL, the signature scheme and `credential:<name>` are shown. Non-reference values are hidden in the summary and the input (component test with a canary secret). The signing secret is chosen by credential name; values never reach the browser (no endpoint returns them). |

Roadmap scope items:

| Item | Status |
|---|---|
| Manual / API / Cron / Interval / Webhook / Event | PASS: all six creatable and firing |
| List / Create / Edit / Enable/Disable / Delete / Configuration / Status | PASS |

Session prompt items:

| Item | Status |
|---|---|
| Validation | PASS |
| Tests per type, invalid schedules, missing fields, API errors, authorization failures | PASS (§12) |
| Missing backend functions implemented, tested, documented | PASS (event dispatch) |

## 15. Security Considerations

- **Authorization:**
  - Trigger writes use the existing workflow authorization on PUT/validate (a 403 is shown in the UI; tested).
  - Event sources pass `may_access_workflow` for the trigger workflow's owner, at fire time, and disabled owners are refused. This prevents a member from observing or chaining onto other users' workflows. Refusals are logged with ids only.
- **Secrets:**
  - The webhook secret is a credential reference only; the UI never shows or stores values. A plaintext value in config is hidden.
  - Event-trigger input contains only the closed, secret-free event fields.
  - The full-stack key is generated per run and never committed.
- **Error disclosure:** `INVALID_TRIGGER_EVENT` messages contain no config values (tested).
- **Webhooks:** unchanged (HMAC, fail closed); 401 verified full-stack. There is still no replay protection (architecture §127).
- **Loop/resource abuse:** event chains are bounded by `AKASHA_LIMIT_EVENT_TRIGGER_DEPTH`. Each fire also passes the existing admission control (`ResourceLimitError`, active-execution cap).
- **XSS:** all values are rendered as React text; no `dangerouslySetInnerHTML`.

## 16. Performance Considerations

- **Dispatcher reads:**
  - each poll (1 s): one `position > high LIMIT 500` query over the primary key, plus one `position IN (gaps)` query when gaps exist;
  - for ticks with triggerable events: `list_latest` versions, and per matching event the source workflow, source execution and owner lookups.
  Not measured.
- **Throughput bound:** without sleeping while `behind`, the dispatcher processes ≥ 500 events per tick round. Node events dominate the volume.
- **Memory:** at most 10,000 gap entries.
- **Frontend:**
  - one validation POST per revision (cached by key);
  - one workflow list (≤ 100) when the event form is open;
  - the published version is cached indefinitely.

## 17. Documentation Changes

| File | What |
|---|---|
| `docs/architecture.md` §129 | Event trigger config, dispatcher behavior (log-based, gaps, no catch-up, at-most-once), input shape, access rule, depth limit |
| `backend/README.md` | `AKASHA_LIMIT_EVENT_TRIGGER_DEPTH` row |
| `frontend/README.md` | "Triggers" section: page, writes, status, validation, types, event sources, Run, webhook display |
| `frontend/tests/fullstack/backend.sh` | Comments for the key and the scheduler |
| `docs/reports/frontend/phase-18-triggers-ui.md` | This report |

Documentation to update in a future phase: if a grant model or event replay is introduced, architecture §129 must change.

## 18. Dependencies / Infrastructure Changes

- **Dependencies:** none added or removed (frontend or backend).
- **Environment variable:** `AKASHA_LIMIT_EVENT_TRIGGER_DEPTH` (default 8). `AKASHA_SCHEDULER_ENABLED` now also controls the event dispatcher.
- **Database:** no migration. The JSONB metadata gains the key `trigger_depth`.
- **Test infrastructure:** the full-stack backend generates `AKASHA_CREDENTIAL_ENCRYPTION_KEY` with `openssl rand`, and no longer sets `AKASHA_SCHEDULER_ENABLED=false`.

## 19. Deferred Work

| What | Why deferred | Phase | Affects completion |
|---|---|---|---|
| Next-fire / last-fired per trigger | Scheduler due times are in memory only; no endpoint | Not planned | No |
| Event replay / persisted cursor / retry | Needs idempotency and persistence design; conflicts with the no-catch-up rule | Not planned | No |
| Workflow sharing so members can use other members' sources | Needs a grant model | Not planned (architecture lists per-resource grants as future work) | No |
| Source-workflow existence check at publish | Cross-aggregate check outside pure domain validation; enforced at fire time | Not planned | No |
| Webhook replay protection | Backend security work (§127) | Phase 23 candidate | No |
| Node-emitted events as trigger sources | Not required; volume and contract concerns | Not planned | No |
| Automated accessibility audit, responsive check | Roadmap phases 25/26 | 25–26 | No |

## 20. Known Limitations

- **Event dispatch delivery:**
  - events stored while no dispatcher runs (downtime, `scheduler_enabled=false`) are never dispatched;
  - a failed fire is not retried;
  - a gap older than 60 s is abandoned, so an event committed later than that is missed.
- **One replica:** the dispatcher, like the scheduler, must run in one replica only. Two would double-fire.
- **Validation timing:** validation reflects the saved draft. Schedule and event errors appear after saving, not while typing.
- **Source picker:** it lists the caller's accessible workflows (≤ 100, by name). When an admin edits a member's workflow, it offers workflows the member's trigger will be refused for. The hint explains the rule.
- **Type changes:** an existing trigger's type cannot be changed (delete and recreate).
- **Depth visibility:** `trigger_depth` is not exposed through the API.
- **Accessibility:** not audited with tooling; keyboard behavior not manually verified.

## 21. Open Issues

| Issue | Impact | Severity | Area | Next action | Target |
|---|---|---|---|---|---|
| Shutdown log noise in the full-stack suite (worker, scheduler, dispatcher poll a removed DB) | Noisy logs only | LOW | Test infrastructure | Stop the server before teardown, or ignore expected errors | Phase 26 |
| `TriggerRegistry` loads all latest versions per scheduler tick | Unmeasured DB load at scale | LOW | Backend runtime | Measure; index triggers by type if needed | Phase 24 / backend |

## 22. Architectural / Product Decisions

- Triggers are edited only through the draft PUT; there is no trigger resource (§5.1).
- The event dispatcher reads the persisted log, anchors at the end, tracks gaps, and fires at most once (§5.2). Do not switch it to `EventPublisher`: worker events never reach it.
- `trigger_depth` is the loop-protection mechanism (§5.3).
- The event source rule is the API's `may_access_workflow` for the trigger workflow's owner, at fire time (§5.4).
- The frontend mirrors `TRIGGERABLE_EVENTS`, `MAX_INTERVAL_SECONDS` and the config keys for pickers only; `/validate` decides.
- Status is derived by comparing the draft with the latest version, because only the latest version fires (§125).
- `CredentialInput` is shared by the editor and the triggers feature and lives in `features/credentials/`.

## 23. Repository / Git State

- **Branch:** `main`. HEAD is `b319e54` (frontend phase 17). Nothing is committed for this phase.
- **Modified (25):** see §4. Diff stat: 485 insertions, 86 deletions in tracked files.
- **Untracked:** `backend/src/akasha/runtime/event_triggers.py`, `backend/tests/integration/test_event_triggers.py`, `frontend/src/features/triggers/`, `frontend/src/features/credentials/CredentialInput.tsx`, `docs/reports/frontend/phase-18-triggers-ui.md`.
- **Unrelated changes:** none detected. No stashes. `test-results/` from Playwright runs is ignored by git.

## 24. Phase Completion Assessment

### Implemented
- the triggers page;
- create/edit/enable/disable/delete for all six types;
- status and validation display;
- manual Run;
- shared credential input;
- event trigger dispatch with loop protection and access control;
- the validation code.

### Verified
- frontend: 416 unit and component tests, of which 23 are trigger tests;
- backend: 2005 tests, of which 10 are event-trigger integration tests;
- mutation checks;
- 23 stubbed E2E tests;
- 15 full-stack tests, including two trigger journeys;
- lint, format, types, build and contract.

### Not Implemented
- next-fire / last-fired display;
- member-to-member source sharing;
- event replay.

### Deferred
See §19.

### Open
See §21 (two LOW issues).

### Blocking Issues
None.

**Phase Status:** COMPLETE WITH LIMITATIONS

## 25. Handoff to Next Phase

**The next phase can assume:**
- `/workflows/:workflowId/triggers` exists.
- `workflowQueries.validation(client, id, revision)` gives the backend verdict for a revision.
- `CredentialInput` is the component for credential references.
- All six trigger types fire in the backend.
- Event-triggered executions have `trigger_type: "event"` and input `{event: {...}}`.

**Contracts:**
- `ValidationCode` includes `invalid_trigger_event`.
- The EVENT config is `{event, source_workflow_id?}`.
- `AKASHA_LIMIT_EVENT_TRIGGER_DEPTH` sets the depth limit.

**Must not change casually:**
- the dispatcher's log-based reading and gap handling;
- the anchor-at-end, no-catch-up semantics (a replay would double-fire with the current design);
- the fire-time access check;
- the frontend's mirrored constants without the backend counterparts (`domain/trigger.py`).

**Constraints:**
- one scheduler/dispatcher replica;
- trigger edits share the draft revision with the editor.

**For Phase 19 (Reports):** event-triggered runs carry the source's correlation id, so reports can group them. `trigger_depth` is not in the API yet; add it to `ExecutionDetail` if reports need it.
