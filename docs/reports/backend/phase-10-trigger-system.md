# Phase 10 — Trigger System

## 1. Executive Summary

**Goal.** Roadmap §14 (Phase 10) lets executions start through different
trigger mechanisms: Manual, API, Cron, Interval and Webhook. It uses a
`TriggerService`, a `TriggerRegistry` and a `TriggerContext`, and every trigger
must go through the existing Execution Service. Acceptance criterion: *No
trigger type implements its own execution engine.*

**How the phase ran.**
1. **Implementation:** all mechanisms were built; 798 tests passed.
2. **Review:** the first version of this report checked the code with
   targeted probes and found six verified defects (D1–D6). Because of them
   it lowered the status to COMPLETE WITH LIMITATIONS.
3. **Follow-up fix:** on request, D1–D6 were fixed in the same uncommitted
   working tree, with regression tests. This version of the report describes
   the final state and records both the defects and their fixes (§10–§11).

**What was built (final state).**
- **Domain:**
  - `TriggerContext`: the normalized trigger context of architecture §17.
    Its payload may nest at most 64 levels.
  - Construction checks only the Phase 02 structural trigger rules, plus a
    new rule that the interval must be finite.
  - The stricter rules are *validation issues* that block publishing, not
    construction errors:
    - schedule rules via `schedule_problem` / `cron_problem`: cron must
      parse, be deterministic and be able to fire; intervals must lie
      between 1 s and 1 year;
    - unique trigger ids per workflow (`DUPLICATE_TRIGGER_ID`).
  - `ValidationIssue` gained a `trigger_id` location field.
- **Application** (`akasha.application.triggers`):
  - `TriggerRegistry`, scoped by workflow, resolves triggers on the *latest
    published version*.
  - `TriggerService` (`fire`, `fire_manual`): its only effect is
    `ExecutionService.create_execution`.
- **Persistence:** `WorkflowVersionRepository.list_latest(workflow_id=None)`.
  No schema change.
- **Runtime:** `TriggerScheduler`, an in-process CRON/INTERVAL scheduler:
  - injectable clock and sleep;
  - owned by the `Container`, started by the FastAPI lifespan;
  - state keyed by (workflow, trigger), with a per-trigger error boundary;
  - an unschedulable trigger is parked instead of being retried each tick.
- **API:**
  - `POST /api/v1/workflows/{workflow_id}/versions/{version}/execute`
  - `POST /api/v1/workflows/{workflow_id}/triggers/{trigger_id}/invoke`
  - `POST /api/v1/webhooks/{workflow_id}/{trigger_id}` (202)
  - `GET /api/v1/executions/{execution_id}`
- **Configuration:** three `AKASHA_*` settings.
- **Dependency:** `croniter 6.2.4`.
- **Documentation:** architecture §125.

**Resulting state.**
- Every supported mechanism produces the same canonical `Execution`:
  - the ad-hoc manual run;
  - configured MANUAL and API triggers;
  - WEBHOOK, CRON and INTERVAL.
- That execution is created by `ExecutionService.create_execution`, pinned to
  an immutable version, `QUEUED`, with the trigger type, trigger id and
  correlation id recorded.
- One integration test runs a webhook-created execution to `COMPLETED`
  through the real `ExecutionRuntime`.
- The deployment still has no component that consumes `QUEUED` executions
  (Phase 22). That was known before this phase.

**Verification.**
- 841 tests pass: the 753 of Phase 09 plus 88 new ones. 160 of them are
  PostgreSQL integration tests.
- ruff, ruff format, mypy (strict, src + tests) and `uv build` pass.
- The phase suites passed 3 × 134 consecutive runs.
- Every D1–D6 scenario has a regression test.

**Acceptance.**
- The roadmap acceptance criterion, the roadmap test list, the phase prompt
  and the session's test list (including duplicate configurations) are
  satisfied (§14).
- Still PARTIAL:
  - the explicit event-trigger boundary;
  - documentation/contract completeness: `backend/README.md`, the OpenAPI
    webhook body, the `status` enum.
- Real limitations remain (§20):
  - nothing runs the queued executions yet;
  - one scheduler runs per process;
  - webhooks have no authentication;
  - trigger delivery is not idempotent.

**Phase Status:** COMPLETE WITH LIMITATIONS

---

## 2. Phase Objective

### Roadmap (`/docs/roadmap-backend.md` §14)

> **Goal:** Allow executions to start through different trigger mechanisms.
> **Scope:** Implement `ManualTrigger`, `APITrigger`, `CronTrigger`,
> `IntervalTrigger`, `WebhookTrigger`. Create `TriggerService`,
> `TriggerRegistry`, `TriggerContext`. All triggers must converge on the same
> Execution Service.
> **Tests:** manual trigger, API trigger, cron scheduling, interval
> scheduling, webhook payload, invalid trigger configuration.
> **Acceptance Criteria:** No trigger type implements its own execution
> engine.

### Phase prompt (roadmap §37, "Phase 10 Prompt")

- Implement TriggerService, TriggerRegistry, TriggerContext and the Manual,
  API, Cron, Interval and Webhook triggers.
- "Every trigger must converge on the existing Execution Service and Execution
  Runtime." "Do not create trigger-specific execution engines."
- "Create tests for every trigger type." Stop after Phase 10.

### Session instructions (additional)

- Where the architecture requires it, establish the event-trigger boundary.
  Do not implement unrelated event infrastructure.
- Trigger → Execution Service → Execution Runtime; no separate execution
  paths.
- Tests:
  - trigger configuration and validation;
  - manual execution and API execution;
  - cron scheduling and interval scheduling;
  - webhook invocation;
  - duplicate and invalid trigger configurations;
  - trigger-to-execution behavior.
- No sleep-based scheduler tests; use deterministic clock and scheduler
  abstractions.
- Completion: every supported trigger creates the same canonical Execution
  representation, and the quality gates pass.
- Follow-up request: "fix the defects and then update the report" (D1–D6).

### Architecture requirements that apply

- **§17:** a normalized `TriggerContext` (`trigger_type`, `timestamp`,
  `payload`, `headers`, `source`, `metadata`). The runtime is independent of
  the mechanism.
- **§125:** executing a trigger resolves the intended immutable version,
  never the draft.
- **§126:** a manual execution returns an Execution id, which the frontend
  observes.
- **§127:** a webhook goes through authentication and validation, creates an
  execution, queues it, and the runtime runs it. No long-running synchronous
  execution in the endpoint.
- **§128 and §153.9:** cron runs, manual runs and webhooks use the same
  execution service and the same runtime.
- **vision §13:** Trigger → Execution Request → Execution Queue → Execution
  Runtime.

### Explicitly not this phase's responsibility

- **Phase 11:** events, including `trigger.fired`.
- **No phase yet:** event-triggered executions (architecture §129–§131).
- **Phase 12:** metrics.
- **Phase 13:** credentials and webhook secrets.
- **Phase 20:** authentication and authorization.
- **Phase 21:** CLI.
- **Phase 22:** queue and workers, i.e. running `QUEUED` executions in a
  deployment.
- **Phase 23:** recovery.
- **Phase 24:** request and resource limits.
- **Phase 25:** security hardening, including webhooks and rate limiting.

---

## 3. Scope Implemented

### Domain
- **`domain/cron.py` (new):**
  - `cron_problem(expr) -> str | None`. It reports a problem when the
    expression:
    - does not have exactly 5 fields;
    - contains croniter's hashed or random tokens (`H`, `R`, `R(0-5)`,
      `H/15`, …), because schedules must be deterministic;
    - cannot be parsed, or can never fire. This is checked with `next_fire`
      from a fixed start (2000-01-01 UTC) and caught as `CroniterError`.
  - Its messages never contain the expression itself.
  - `next_fire(expr, after)` returns the first fire time strictly after
    `after`, in UTC.
- **`domain/trigger.py` (modified):**
  - **Construction:**
    - CRON requires a non-empty string (Phase 02 rule);
    - INTERVAL must be numeric, not a boolean, finite and `> 0` (Phase 02
      rule plus finiteness);
    - EVENT requires an event name (unchanged).
  - `schedule_problem(trigger)`:
    - CRON → `cron_problem`;
    - INTERVAL must satisfy `1 <= interval_seconds <= MAX_INTERVAL_SECONDS`
      (31,536,000 s, one year).
  - `TriggerContext` (frozen):
    - fields: `trigger_type`, `timestamp` (timezone-aware), `payload`,
      `headers`, `source`, `metadata`;
    - rejects payloads nested deeper than `MAX_PAYLOAD_DEPTH` (64), checked
      iteratively, with `details.max_depth`.
- **`domain/graph_validation.py` (modified):**
  - new `ValidationCode`s `DUPLICATE_TRIGGER_ID` and
    `INVALID_TRIGGER_SCHEDULE`;
  - `ValidationIssue.trigger_id` (optional location).
- **`domain/workflow.py` (modified):**
  - `validate_triggers(triggers)`: duplicate ids and schedule problems, each
    with its `trigger_id`.
  - `WorkflowDraft.validate` includes it, so publishing is blocked.
  - `WorkflowVersion.validate` (the runtime's pre-run check) deliberately
    does not.

### Persistence
- `WorkflowVersionRepository.list_latest(*, workflow_id=None) ->
  Sequence[tuple[str, WorkflowVersion]]`: the latest published version of
  every workflow, or of one workflow. Implemented in SQLAlchemy as a
  `max(version) GROUP BY workflow_id` subquery joined back to
  `workflow_versions` and ordered by `workflow_id`.
- No schema change and no migration.

### Application (`application/triggers.py`, new)
- `TriggerRegistry`:
  - `resolve(workflow_id, trigger_id, *, trigger_type=None)` loads only that
    workflow's latest version;
  - `list_scheduled()` returns the enabled CRON/INTERVAL triggers of all
    latest versions, ordered by (trigger id, workflow id).
- `TriggerService`:
  - `fire(resolved, context)` rejects a disabled trigger (409) and a context
    type that differs from the trigger type (`DomainValidationError`);
  - `fire_manual(...)` handles the ad-hoc manual run.
  - Both go through `_create` → `ExecutionService.create_execution` and log
    `trigger_fired`.
- `ResolvedTrigger`, `TriggerNotFoundError` (404), `TriggerDisabledError`
  (409), `INVOCABLE_TYPES`, `SCHEDULED_TYPES`.

### Runtime (`runtime/scheduler.py`, new)
- `TriggerScheduler(registry, service, *, clock, poll_seconds=1.0, sleep)`.
- The state is `_due: {(workflow_id, trigger_id): (spec, next_due |
  None)}`:
  - the spec is `"cron <expr>"` or `"interval <value>"`;
  - a changed spec re-arms the trigger;
  - `None` means "parked" (the schedule cannot be computed).
- `tick()` runs a per-trigger error boundary:
  1. the first sight arms the trigger without firing it;
  2. once due, the next boundary is computed *before* firing, so a failing
     fire is not retried on every tick;
  3. missed boundaries are skipped.
- `run()` polls until cancelled. A tick that fails is logged as
  `trigger_tick_failed`.

### API
- `api/triggers.py` (new): the four endpoints of §7.
  - Trigger URLs are scoped by workflow.
  - Webhook body errors — malformed JSON, invalid UTF-8, nesting too deep for
    the parser — map to 422.
  - Header allowlist.
  - `PayloadTooLargeError` (413).
- `api/trigger_schemas.py` (new): `ExecutionDetail`.
- `api/routes.py` mounts the router.
- `api/workflow_schemas.py`: `ValidationIssueOut.trigger_id`.
- `application/workflows.py`: `issue_payload` includes `trigger_id`.

### Composition and configuration
- `Container`:
  - new fields `executions`, `triggers`, `trigger_registry`, `scheduler`,
    and the private `_scheduler_task`;
  - `start()`;
  - `shutdown()` cancels and awaits the scheduler task, then disposes the
    database.
- `create_container` wires everything through one shared `uow()` factory.
- The lifespan calls `container.start()`.
- New `Settings`: `scheduler_enabled` (default `True`),
  `scheduler_poll_seconds` (`1.0`), `webhook_max_payload_bytes` (`1048576`).

### Testing and documentation
- 88 new test items in total (§12).
- Architecture §125 rewritten (§17).

---

## 4. Files Changed

Tracked files changed: 18 files, +516/−22.

| File | Change | Reason |
|---|---|---|
| `backend/src/akasha/domain/cron.py` | Created (52 lines) | `cron_problem`, `next_fire` |
| `backend/src/akasha/domain/trigger.py` | Modified (+100/−5) | Construction rules, `schedule_problem`, limits, `TriggerContext` with depth check |
| `backend/src/akasha/domain/graph_validation.py` | Modified (+4) | 2 new validation codes; `ValidationIssue.trigger_id` |
| `backend/src/akasha/domain/workflow.py` | Modified (+41/−5) | `validate_triggers`; wired into `WorkflowDraft.validate`; `WorkflowVersion.validate` docstring |
| `backend/src/akasha/application/triggers.py` | Created (172) | Registry (scoped by workflow), service, errors |
| `backend/src/akasha/application/persistence.py` | Modified (+13) | `list_latest(workflow_id=None)` protocol |
| `backend/src/akasha/application/workflows.py` | Modified (+1) | `issue_payload` includes `trigger_id` |
| `backend/src/akasha/infrastructure/persistence.py` | Modified (+31/−1) | SQLAlchemy `list_latest` |
| `backend/src/akasha/runtime/scheduler.py` | Created (156) | `TriggerScheduler` |
| `backend/src/akasha/api/triggers.py` | Created (183) | Execute / invoke / webhook / get-execution routes |
| `backend/src/akasha/api/trigger_schemas.py` | Created (34) | `ExecutionDetail` |
| `backend/src/akasha/api/workflow_schemas.py` | Modified (+1) | `ValidationIssueOut.trigger_id` |
| `backend/src/akasha/api/routes.py` | Modified (+2) | Mount the trigger router |
| `backend/src/akasha/api/app.py` | Modified (+1) | Lifespan starts container background work |
| `backend/src/akasha/bootstrap.py` | Modified (+50/−5) | Wiring; scheduler task ownership |
| `backend/src/akasha/config.py` | Modified (+7) | Scheduler and webhook settings |
| `backend/pyproject.toml` | Modified (+5) | `croniter>=6.2.4`; mypy override |
| `backend/uv.lock` | Modified (+35) | croniter 6.2.4, python-dateutil 2.9.0.post0, six 1.17.0 (written by `uv add`) |
| `backend/tests/application/__init__.py` | Created (empty) | New test package |
| `backend/tests/application/test_trigger_service.py` | Created (310) | 12 service/registry unit tests |
| `backend/tests/runtime/test_scheduler.py` | Created (345) | 15 scheduler tests |
| `backend/tests/integration/test_triggers_api.py` | Created (652) | 22 HTTP + PostgreSQL tests |
| `backend/tests/domain/test_trigger_credentials_plugin.py` | Modified (+141/−3) | 57 items (Phase 09: 23) |
| `backend/tests/domain/test_workflow.py` | Modified (+41) | 3 trigger-validation tests |
| `backend/tests/test_lifecycle.py` | Modified (+35) | 2 new tests; wiring test extended |
| `backend/tests/integration/test_workflows_api.py` | Modified (+3) | The pinned issue wire shape includes `trigger_id` (deliberate additive contract change) |
| `docs/architecture.md` | Modified (+5/−3) | §125 rules |
| `docs/reports/backend/phase-10-trigger-system.md` | Created | This report |

- New directory: `backend/tests/application/`.
- Migrations: none. Deleted files: none.
- Generated: `backend/uv.lock`.

---

## 5. Architecture Changes

### A1 — Triggers resolve to the latest published version

#### Decision
A trigger is live only on the latest published version of its workflow.
Publishing retires older versions' triggers, and drafts never fire.

#### Context
§125 said only "according to the trigger model". Triggers live in the draft,
and each version holds an immutable copy with stable ids.

#### Chosen Approach
`TriggerRegistry` reads `list_latest()`. §125 now states the rule.

#### Alternatives Considered
- **All published versions stay live.** Rejected: publishing v2 would not
  retire v1's cron, so both would fire.
- **An "active version" pointer.** Rejected: no such field or use case
  exists; it would need a migration and an API.
- **A trigger table.** Rejected: a duplicate concept (AGENTS §6) plus a
  migration.

#### Reason
The smallest rule that is deterministic and never touches a draft.

#### Consequences
- Republishing without a trigger retires its endpoint.
- Webhook URLs survive a republish while the draft keeps the id.
- Workflow status (`DISABLED`) is not consulted (F7).

### A2 — Trigger types are mechanisms, not classes

#### Decision
There are no `ManualTrigger`/`APITrigger`/… classes. The Phase 02 `Trigger`
entity and `TriggerType` enum remain the only trigger concept.

| Roadmap name | Implemented as |
|---|---|
| ManualTrigger | `POST /workflows/{p}/versions/{v}/execute` (ad hoc) and `POST /workflows/{p}/triggers/{t}/invoke` (configured MANUAL) |
| APITrigger | `POST /workflows/{p}/triggers/{t}/invoke` |
| WebhookTrigger | `POST /webhooks/{p}/{t}` |
| CronTrigger, IntervalTrigger | `TriggerScheduler` |

#### Context
The roadmap names five triggers; the domain already had one entity with a
type enum.

#### Chosen Approach
Entry points that build a `TriggerContext` and call `TriggerService`.

#### Alternatives Considered
One class per type. Rejected: near-identical classes calling the same
service (AGENTS §6, §119).

#### Reason
One trigger concept, and no duplicated execution paths.

#### Consequences
- A new mechanism means a new entry point.
- `TriggerRegistry` looks up configured triggers; it is not a registry of
  implementations.

### A3 — Trigger rules that postdate stored data are validation issues

This decision was made during the follow-up fix.

#### Decision
The rules added in this phase are reported by `validate_triggers` as issues
that block publishing. `Trigger` construction keeps only the rules that every
persisted trigger has always satisfied. The added rules are:
- cron syntax;
- deterministic cron;
- cron can fire;
- interval between 1 s and 1 year;
- unique ids.

#### Context
The first implementation enforced them at construction. Stored drafts and
versions are rebuilt through the same constructors, so older data stopped
loading:
- the registry scan failed for all workflows (D4);
- an affected workflow's draft could not even be loaded to fix it
  (`update_draft` loads it first).

The codebase already had the opposite precedent. `validate_dependencies`:
"a malformed constraint is a validation issue, not a construction error:
already persisted workflows must stay loadable to be fixed".

#### Chosen Approach
- `schedule_problem` / `cron_problem` / `validate_triggers` run inside
  `WorkflowDraft.validate`, so `/validate` reports them and publish refuses.
- Construction additionally rejects only NaN and infinity. PostgreSQL JSONB
  cannot store them, so no stored data can contain them.

#### Alternatives Considered
- **Strict construction, with a lenient loading path for stored data.**
  Rejected: two validation modes for one entity.
- **Tolerant loading that drops invalid triggers.** Rejected: the next draft
  save would silently delete user data.

#### Reason
Follows the existing precedent. Stored data stays loadable and fixable.

#### Consequences
- A draft may contain invalid triggers; `PUT` accepts them.
- Versions published before these rules may contain invalid schedules. The
  scheduler parks them (A5).
- Trigger issues are not part of `WorkflowVersion.validate`, so a run never
  depends on how it was triggered.

### A4 — Trigger ids are unique per workflow; addressing is scoped by workflow

This decision was made during the follow-up fix.

#### Decision
- Trigger ids must be unique within a workflow (`DUPLICATE_TRIGGER_ID` blocks
  publishing).
- Webhook and invoke URLs carry the workflow id.
- The registry resolves within one workflow.
- Scheduler state is keyed by (workflow id, trigger id).

#### Context
Duplicate ids were accepted inside one draft and across workflows.
Resolution scanned all workflows and returned the first match, so a second
workflow could capture another workflow's webhook deliveries (D6).

#### Chosen Approach
The workflow id is part of each trigger URL; per-draft uniqueness is a
publish gate.

#### Alternatives Considered
- **Global uniqueness at publish.** Needs a lock or a constraint over JSONB
  documents, so it races or needs a migration.
- **Server-generated ids.** An API change, and it breaks the stable ids that
  clients choose.

#### Reason
No coordination across workflows is needed. A second workflow can never
receive another workflow's deliveries, even with colliding ids.

#### Consequences
- **URL change.** Old: `/triggers/{id}/invoke`, `/webhooks/{id}`. New:
  `/workflows/{p}/triggers/{t}/invoke`, `/webhooks/{p}/{t}`. The old URLs
  were never committed or consumed.
- **Resolution cost.** Webhook/invoke resolution now loads one workflow's
  latest version instead of every workflow's.

### A5 — In-process scheduler owned by the Container

#### Decision
One asyncio task per API process (`runtime/scheduler.py`). `Container.start()`
creates it and `Container.shutdown()` cancels it.

#### Context
Workers arrive in Phase 22; §73 anticipates a separate scheduler service.

#### Chosen Approach
A poll loop with an injectable clock and sleep, and a pure `tick()` pass.
The follow-up fix added:
- a per-trigger error boundary;
- (workflow, trigger) keys;
- a schedule spec in the state, so a changed type or schedule re-arms
  immediately;
- parking for schedules that cannot be computed.

#### Alternatives Considered
- **APScheduler or similar.** Rejected: a dependency with its own
  persistence model; the loop is small and must be deterministic in tests.
- **A separate process.** No worker or CLI infrastructure exists yet.

#### Reason
Deterministic, testable, minimal.

#### Consequences
- Several API processes fire duplicates unless all but one set
  `AKASHA_SCHEDULER_ENABLED=false` (F14).
- State lives in memory.
- The name overlaps with the runtime's node "scheduler loop" (F11).

### A6 — Webhooks answer 202; execute and invoke answer 201

#### Decision
Webhooks return `202 Accepted` with the `QUEUED` execution. Execute and
invoke return `201 Created`.

#### Context
§127 says the webhook endpoint must not execute synchronously.

#### Chosen Approach
The same `ExecutionDetail` body with different status codes.

#### Alternatives Considered
201 for all three.

#### Reason
202 tells an external sender "accepted, not done".

#### Consequences
Clients must not expect the same status code from every mechanism.

### A7 — croniter is used by the domain

#### Decision
Cron parsing is delegated to croniter.

#### Context
Hand-written cron parsing is error-prone.

#### Chosen Approach
`akasha.domain.cron` wraps croniter:
- it rejects croniter's `H`/`R` extensions;
- it checks that a fire time exists.

#### Alternatives Considered
Hand-written parsing.

#### Reason
A maintained parser.

#### Consequences
- The domain is no longer stdlib-only. `tests/test_architecture.py` allows
  it; the `domain/__init__.py` text was not updated.
- The accepted dialect is croniter's 5-field syntax: names, `L`, `#`.

### A8 — Payload nesting limit of 64 at the trigger boundary

This decision was made during the follow-up fix.

#### Decision
`TriggerContext` rejects payloads nested deeper than 64 levels.

#### Context
- `copy.deepcopy` in `Execution.create` raises `RecursionError` at about 500
  levels (probed); `json.loads` parses up to about 5000.
- A roughly 1 KB webhook body could therefore trigger a 500.

#### Chosen Approach
An iterative depth check in `TriggerContext`, the single boundary every
mechanism passes through. It is a `ponytail:` constant; making it
configurable is Phase 24.

#### Alternatives Considered
- **Catch `RecursionError` where it happens.** Rejected: it happens deep in
  `Execution.create`.
- **Limit only in the webhook route.** Rejected: that leaves other
  mechanisms unprotected.

#### Reason
One check for all mechanisms, well below every observed failure depth.

#### Consequences
Payloads nested deeper than 64 levels are rejected with 422.

---

## 6. Domain / Data Model Changes

### `Trigger` construction rules (`Trigger._validate_config`)

| Type | Before Phase 10 | After Phase 10 (final) |
|---|---|---|
| CRON | Non-empty string | Unchanged (syntax moved to publish validation) |
| INTERVAL | Number, not a bool, `> 0` | Also finite (NaN/inf rejected; JSONB can never store them) |
| EVENT / others | Unchanged | Unchanged |

### New publish-time rules (`validate_triggers` in `WorkflowDraft.validate`)

| Code | Rule | Location |
|---|---|---|
| `duplicate_trigger_id` | A trigger id appears more than once in the draft | `trigger_id` |
| `invalid_trigger_schedule` | Cron is not 5 fields, uses `H`/`R`, does not parse, or never fires; interval outside [1, 31,536,000] s | `trigger_id` |

- Messages carry no config values. This respects the "locations only" rule of
  `ValidationIssue`.
- `WorkflowVersion.validate`, the runtime's pre-run check, does not include
  trigger rules.

### New value object `TriggerContext` (`domain/trigger.py`, frozen)

| Field | Type | Rule |
|---|---|---|
| `trigger_type` | `TriggerType` | Coerced from a string |
| `timestamp` | `datetime` | Defaults to `utcnow()`; a naive datetime raises `DomainValidationError` |
| `payload` | `Mapping[str, Any]` | Copied at the top level; nesting ≤ `MAX_PAYLOAD_DEPTH` (64); becomes `Execution.input` |
| `headers` | `Mapping[str, str]` | Not persisted |
| `source` | `str` | Mechanism name; logged only |
| `metadata` | `Mapping[str, str]` | `correlation_id` is read from it |

### Other types
- `ValidationCode`: `DUPLICATE_TRIGGER_ID`, `INVALID_TRIGGER_SCHEDULE`.
- `ValidationIssue.trigger_id: str | None = None`.
- Constants `MAX_INTERVAL_SECONDS = 31_536_000` and `MAX_PAYLOAD_DEPTH = 64`.
- Application: `ResolvedTrigger`, `TriggerNotFoundError`,
  `TriggerDisabledError`.
- API: `ExecutionDetail`.
- Repository protocol: `list_latest(*, workflow_id=None)`.

### Database and serialization
- No table, column, index or constraint changed. No migration.
- `executions.metadata` now also records non-MANUAL `trigger_type` and
  `trigger_id` values.
- Trigger JSON is unchanged (`id`, `trigger_type`, `config`, `enabled`).
- Stored triggers load under the final construction rules, because every
  value accepted before Phase 10 still passes. Verified for cron `garbage` on
  PostgreSQL (§12).

### State transitions
- Unchanged. All mechanisms go through `Execution.create` and `enqueue()`
  (CREATED → QUEUED).

---

## 7. API / Contract Changes

The following applies to all four endpoints:
- Errors use the existing `ErrorResponse` body
  `{code, message, details, request_id}`. OpenAPI declares 404, 409, 413,
  422 and 500.
- There is no authentication, like all of `/api/v1` until Phase 20.
- Without a database, the endpoints answer `CONFIGURATION_ERROR` (500).

### `POST /api/v1/workflows/{workflow_id}/versions/{version}/execute`
- **Purpose:** manual run of a published version (§126).
- **Request:**
  - optional JSON object body, which becomes `Execution.input`;
  - `version` must be ≥ 1;
  - optional `x-request-id`, which becomes `correlation_id`.
- **Response:** 201 `ExecutionDetail` with `trigger_type: "manual"`,
  `trigger_id: null`, `status: "queued"`.
- **Errors:**
  - 404 `WORKFLOW_VERSION_NOT_FOUND`.
  - 422 `VALIDATION_ERROR`:
    - a body that is not a JSON object (tested; the status only is
      asserted);
    - a request that is not JSON (probed on `/invoke`, which declares the
      same body);
    - JSON nested too deeply for pydantic (probed at depth 501).
  - 422 `DOMAIN_VALIDATION_ERROR`: NaN or NUL in the input; nesting deeper
    than 64 that pydantic accepts; an invalid `x-request-id`.
  - 400 `HTTP_ERROR`: FastAPI cannot parse the body at all (e.g. 50,000
    nested `[`).

### `POST /api/v1/workflows/{workflow_id}/triggers/{trigger_id}/invoke`
- **Purpose:** programmatic invocation of a configured API or MANUAL
  trigger.
- **Request:** optional JSON object body; optional `x-request-id`.
- **Response:** 201 `ExecutionDetail` with `trigger_type` "api" or "manual"
  and `trigger_id` set.
- **Errors:**
  - 404 `TRIGGER_NOT_FOUND`, with `details = {workflow_id, trigger_id}`. The
    following cases are deliberately indistinguishable:
    - an unknown workflow or trigger;
    - a trigger that exists only in a draft or a retired version;
    - a trigger of another type.
  - 409 `TRIGGER_DISABLED`.
  - 422/400 as for `/execute`.

### `POST /api/v1/webhooks/{workflow_id}/{trigger_id}`
- **Purpose:** an external webhook delivery to a WEBHOOK trigger (§127).
- **Request:**
  - raw body of any content type, parsed with `json.loads`;
  - an empty body counts as `{}`;
  - the body must be a JSON object;
  - at most `webhook_max_payload_bytes` bytes, checked after reading;
  - OpenAPI documents no request body (F16).
- **Response:** 202 `ExecutionDetail`.
- **Errors:**
  - 404 `TRIGGER_NOT_FOUND`, also for non-webhook trigger ids.
  - 409 `TRIGGER_DISABLED`.
  - 413 `PAYLOAD_TOO_LARGE`, with `details.max_bytes`.
  - 422 `DOMAIN_VALIDATION_ERROR` for any of:
    - malformed JSON;
    - invalid UTF-8;
    - JSON nested too deeply to parse;
    - a non-object value;
    - nesting deeper than 64;
    - NaN;
    - an invalid `x-request-id`.

### `GET /api/v1/executions/{execution_id}`
- 200 `ExecutionDetail`; 404 `EXECUTION_NOT_FOUND`.

### `ExecutionDetail`

| Field | Type | Source |
|---|---|---|
| `id` | string | `Execution.id` |
| `workflow_id` | string | `Execution.workflow_id` |
| `version` | int | `workflow_version.version` |
| `status` | string (plain string in OpenAPI — F16) | `ExecutionStatus` value, e.g. `"queued"` |
| `trigger_type` | `TriggerType` enum | metadata |
| `trigger_id` | string or null | metadata |
| `correlation_id` | string or null | metadata (`x-request-id`) |
| `created_at` | datetime | `Execution.created_at` |

### Changed existing contracts
- **Validation issues** (`POST /workflows/{id}/validate` and the
  `details.issues` of `WORKFLOW_VALIDATION_FAILED`):
  - every issue now carries `trigger_id` (null for graph and dependency
    issues). This is additive;
  - the pinned-shape test in `test_workflows_api.py` was updated
    accordingly;
  - new code values: `duplicate_trigger_id`, `invalid_trigger_schedule`.
- **Draft triggers** (`PUT /workflows/{id}`):
  - the construction rules are the Phase 02 rules plus finite intervals;
  - invalid schedules and duplicate ids are *accepted into the draft*;
  - `/validate` reports them, and `publish` answers 422
    `WORKFLOW_VALIDATION_FAILED`.
- **Trigger ids** are still chosen by the client (`<prefix>_<32 hex>`). They
  must be unique within a workflow, not globally.

### New error codes
- `TRIGGER_NOT_FOUND` (404), `TRIGGER_DISABLED` (409), `PAYLOAD_TOO_LARGE`
  (413).

### Compatibility
- The four endpoints are new. Their URLs changed once during this phase
  (A4); no consumer existed.
- The validation-issue change is additive.
- No change to workflow CRUD, node, plugin or SDK contracts.
- No WebSocket or event contracts exist yet.

---

## 8. Implementation Details

### Control flow

```text
POST /workflows/{p}/versions/{v}/execute ─► TriggerService.fire_manual ────────┐
POST /workflows/{p}/triggers/{t}/invoke ─► TriggerRegistry.resolve(p, t) ─► fire ─┤
POST /webhooks/{p}/{t} ─► TriggerRegistry.resolve(p, t, WEBHOOK) ─► fire ───────┤
TriggerScheduler.tick ─► TriggerRegistry.list_scheduled ─► fire ─────────────────┤
                                                                                ▼
            TriggerService._create ─► ExecutionService.create_execution
            (load pinned version → Execution.create → enqueue → commit: QUEUED)
                                ┆ no production consumer until Phase 22
                    ExecutionRuntime.run(execution_id)
```

### Resolution and transactions
- `resolve` reads `list_latest(workflow_id=p)`, i.e. one row, in a read-only
  unit of work.
- Creation happens in a separate write unit of work.
- A publish between the two can make the resolved version non-latest. The
  execution then runs the version that was latest at resolution time (L8).
  This is safe because versions are immutable.
- In a version published before the uniqueness rule, duplicate ids resolve
  to the first match in stored order.

### Execution metadata
- `trigger_type` comes from the context.
- `trigger_id` is the configured trigger's id, or `None` for the ad-hoc
  manual run.
- `correlation_id` comes from `x-request-id`; `ExecutionMetadata` requires
  1–128 printable characters.

### Trigger context
- API mechanisms copy only three headers: `content-type`, `user-agent` and
  `x-request-id` (an allowlist).
- `source` is the type value (`"manual"` / `"api"` / `"webhook"`); the
  scheduler uses `"schedule"`.
- Neither `headers` nor `source` is persisted. `source` is logged. Nothing
  reads `headers` yet.
- The depth check walks the payload with an explicit stack. It returned
  "too deep" for a list nested 20,000 levels without overflowing (tested).

### Scheduler semantics
- **Key:** `(workflow_id, trigger_id)`.
- **Spec:** `"cron <expr>"` or `"interval <repr(value)>"`. `repr` avoids a
  float conversion that could raise on huge integers.
- **First sight, or a changed spec:** the scheduler sets `(spec, None)` and
  then `(spec, first_due)`. A failing `first_due` leaves the trigger parked,
  and the per-trigger handler logs `scheduled_trigger_failed` once.
- **A parked trigger** (`None`) is skipped silently until its spec changes,
  i.e. after a republish with a different schedule.
- **When due** (`now >= due`):
  1. park, then compute `next_due`:
     - cron: `next_fire(expr, now)`;
     - interval: `due + (⌊(now − due) / interval⌋ + 1) × interval`, which
       stays on the grid;
  2. fire.

  A failing fire is therefore logged once per boundary, never on every
  tick.
- **Pruning:** keys that `list_scheduled()` no longer returns lose their
  state.
- **Timing:** the sleep comes after the tick; cron is evaluated in UTC; no
  timezone setting.
- **Tick-level failures** (e.g. the database is unavailable in
  `list_scheduled`) are logged as `trigger_tick_failed` on every poll (F17).

### Lifecycle
- `Container.start()` is idempotent.
- `shutdown()` cancels the task, awaits it (suppressing `CancelledError`),
  then disposes the database.
- If the task is cancelled during a fire, the unit of work rolls back
  unless it had already committed.
- `httpx.ASGITransport` does not run the lifespan, so integration tests
  never start the loop implicitly.

### Error handling
- The new errors go through the existing `AkashaError` handler.
- Webhook parsing catches `ValueError`, which covers `JSONDecodeError` and
  `UnicodeDecodeError`, and `RecursionError`, and maps them to 422.
- `/execute` and `/invoke` bodies are parsed by FastAPI and pydantic (400 or
  422).

### Observability
- `trigger_fired` (INFO) logs ids, the type, the source and the correlation
  id; never the payload.
- `scheduled_trigger_failed` and `trigger_tick_failed` log at ERROR with a
  traceback.
- No metrics and no domain events (Phases 11/12).

### Extending the system
To add a mechanism:
1. Build a `TriggerContext`.
2. Resolve with `TriggerRegistry.resolve(workflow_id, trigger_id, ...)`, or
   add a query there.
3. Call `TriggerService.fire`.

Never call `ExecutionService.create_execution` or `ExecutionRuntime` from an
entry point.

---

## 9. Findings

Resolution values below reflect the final state after the follow-up fix.

### F1 — `WorkflowVersion` has no workflow id

#### Finding
`WorkflowVersion` does not carry its `workflow_id`. The first draft of the
registry referenced a non-existent `version.workflow_ref`.

#### Impact
LOW

#### Resolution
WORKAROUND

#### Notes
`list_latest` returns `(workflow_id, version)` pairs.

### F2 — Cron expressions that can never fire were accepted

#### Finding
Examples: `0 0 31 2 *`, `0 0 30 2 *`. `next_fire` raised
`CroniterBadDateError`.

#### Impact
HIGH, together with D1.

#### Resolution
FIXED

#### Notes
- `cron_problem` computes one fire time from a fixed start, which makes this
  a publish-time issue.
- The scheduler parks versions published before the rule.

### F3 — croniter's dialect is broader than documented; `R` is random

#### Finding
- Accepted: `L`, `#`, weekday/month names, and `7` for Sunday.
- `R`, `R(0-5)` and `0 R * * *` gave a different `next_fire` result on
  each call.

#### Impact
MEDIUM

#### Resolution
FIXED

#### Notes
- `H`/`R` tokens are rejected as non-deterministic.
- The module docstring now describes the accepted dialect accurately.

### F4 — The interval check accepted values that cannot be scheduled

#### Finding
`1e12`, `1e300`, `10**30`, `inf` and `nan` passed the check. The arming
arithmetic then raised `OverflowError` or `ValueError`.

#### Impact
HIGH, together with D1.

#### Resolution
FIXED

#### Notes
- NaN and inf are rejected at construction; JSONB could never store them
  anyway (verified: `DBAPIError`).
- The range [1 s, 1 year] is a publish-time rule.

### F5 — Tightening validation at construction made stored data unloadable

#### Finding
Tightening construction rules broke loading of stored data (D4).
- Verified before the fix on PostgreSQL: one row with cron `garbage` made
  registry calls fail for every workflow, and another workflow's webhook
  answered 422 with `details: {"cron": "garbage"}`.

#### Impact
HIGH

#### Resolution
FIXED (A3)

#### Notes
`test_legacy_trigger_configs_stay_loadable_and_isolated` covers it.

### F6 — Duplicate trigger ids; client-chosen ids may be guessable

#### Finding
- Duplicate ids were accepted inside one draft and across workflows.
- The first match by workflow-id order won, so deliveries could be captured
  by another workflow (D6).

#### Impact
HIGH

#### Resolution
- FIXED: duplicates (a publish gate) and cross-workflow routing (A4).
- OPEN: guessable client-chosen ids.

#### Notes
- Ids are still chosen by the client (any `<prefix>_<32 hex>`, e.g. all
  zeros), and `GET` returns them.
- Anyone who knows a workflow id and a trigger id can fire the trigger. This
  remains until webhook and API authentication exist (Phases 13/20/25).

### F7 — Workflow status is not consulted

#### Finding
Vision §7 says "Disabled: automatic triggers are deactivated". No use case
sets `DISABLED`, and the registry does not read workflows.

#### Impact
LOW (latent)

#### Resolution
DEFERRED

### F8 — `QUEUED` executions are never run in the deployment

#### Finding
`ExecutionRuntime` has no production caller (grep of `src/`).

#### Impact
MEDIUM

#### Resolution
DEFERRED (Phase 22)

#### Notes
The docstring of `api/triggers.py` used to say "the runtime picks it up".
That inaccuracy is FIXED.

### F9 — mypy checks the tests; the `uow` fixture type is incompatible

#### Finding
- `uv run mypy .` checks the tests too.
- The integration `uow` fixture (`Callable[[],
  AbstractAsyncContextManager[...]]`) is not assignable to
  `akasha.application.workflows.UowFactory`.

#### Impact
LOW

#### Resolution
WORKAROUND

#### Notes
New tests use `lambda: SqlAlchemyUnitOfWork(database)`.

### F10 — croniter ships no `py.typed`

#### Finding
croniter has no type information.

#### Impact
LOW

#### Resolution
WORKAROUND

#### Notes
A mypy override is in place.

### F11 — The name "scheduler" is used twice in `runtime/`

#### Finding
`runtime/runtime.py` already has a node "scheduler loop" (architecture §12).
The component map in `runtime/__init__.py` does not list
`runtime.scheduler`.

#### Impact
LOW

#### Resolution
OPEN (documentation)

### F12 — Request bodies are not bounded before reading

#### Finding
- The webhook reads the whole body before checking the size limit.
- `/execute` and `/invoke` bodies have no size limit.
- The webhook does not enforce a content type.

#### Impact
MEDIUM

#### Resolution
DEFERRED (Phase 24/25)

### F13 — `x-request-id` can cause a rejection

#### Finding
A header longer than 128 characters, or one with non-printable characters,
gets the request rejected with 422.

#### Impact
LOW

#### Resolution
OPEN

### F14 — One scheduler per process

#### Finding
Several processes fire duplicates; there is no leader election.

#### Impact
MEDIUM

#### Resolution
DEFERRED (Phase 22/26)

#### Notes
Workaround: `AKASHA_SCHEDULER_ENABLED=false` on every process but one.

### F15 — Trigger delivery is not idempotent

#### Finding
The same webhook delivered twice creates two executions (probed).

#### Impact
MEDIUM

#### Resolution
DEFERRED (not assigned to a phase)

### F16 — Gaps in the OpenAPI contract

#### Finding
- The webhook documents no request body.
- `ExecutionDetail.status` has no enum.
- No test checks OpenAPI for the new paths.

#### Impact
LOW

#### Resolution
OPEN

#### Notes
`ValidationIssueOut` now documents `trigger_id` (probed).

### F17 — Scheduler failures can flood the log

#### Finding
Scheduler failures were logged at the poll rate.

#### Impact
LOW

#### Resolution
- FIXED for per-trigger failures, which are now logged once per boundary or
  spec.
- OPEN for tick-level failures, which are still logged on every poll.

### F18 — D5 was broader than first reported (found during the fix)

#### Finding
Probes:
- `copy.deepcopy` raises `RecursionError` from about 500 levels (depths
  100 / 500 / 900 / … / 5000);
- `json.loads` parses up to 5000 and overflows at 20,000;
- pydantic's `JsonValue` rejects depth 501 on `/execute` and `/invoke`
  (422 `VALIDATION_ERROR`).

A webhook body of about 1 KB could therefore cause a 500. The first report
only mentioned 50,000 levels.

#### Impact
HIGH

#### Resolution
FIXED (A8)

### F19 — The strict construction rules also locked workflows (found during the fix)

#### Finding
With the first implementation, a workflow whose draft held a
pre-Phase-10 trigger could not be loaded. `update_draft` (`PUT`) loads the
workflow first, so the workflow could not even be fixed through the API.

#### Impact
HIGH

#### Resolution
FIXED (A3)

#### Notes
Found by inspection. The test now shows that `GET` workflow, `GET` version
and `validate` work for such data.

### F20 — croniter accepts Unicode decimal digits

#### Finding
For example, `٣ * * * *` is read as minute 3.

#### Impact
LOW

#### Resolution
Accepted (harmless)

### F21 — Updating an existing contract test

#### Finding
Adding `trigger_id` to validation issues broke
`test_validate_reports_structured_issues`, which pins the issue wire shape.

#### Impact
LOW

#### Resolution
FIXED

#### Notes
This is a deliberate, additive contract change. The test was updated to the
new shape and still asserts the exact key set. It was not weakened.

---

## 10. Bugs / Problems Found

### Introduced during implementation, fixed before the first report

| # | Problem | Root cause | Fix | Regression test | Area |
|---|---|---|---|---|---|
| B1 | `tick()` counted failed fires as successful | The exception was swallowed, but the fire was still counted | Only successful fires count (now part of `_step`) | `test_failed_fire_does_not_break_the_pass` | `runtime/scheduler.py` |
| B2 | `run()` would stop forever on the first exception from `tick()`, unnoticed | No exception boundary around `tick()` | `try`/`except` + `trigger_tick_failed` log | `test_survives_a_broken_tick` | `runtime/scheduler.py` |
| B3 | The registry draft used the non-existent `version.workflow_ref` | Assumed that `WorkflowVersion` knows its workflow | `list_latest` returns `(workflow_id, version)` pairs | Registry tests | `application/triggers.py` |

### Defects found in the review (first report) and fixed in the follow-up

All six were introduced in this phase. The only exception is D6's missing
uniqueness rule, which predates it (Phases 02/06) but could be exploited only
since this phase.

| # | Problem | Root cause | Fix | Regression tests |
|---|---|---|---|---|
| D1 | One trigger that cannot be scheduled made every tick fail; every trigger sorted after it (other workflows included) never fired | `_arm`/`_is_due` ran outside the per-trigger `try` | A per-trigger error boundary around arm, due and fire. A schedule that cannot be computed is parked, so it is logged once | `test_unschedulable_trigger_is_parked_and_logged_once` (3 cases: interval `1e12`, cron `0 0 31 2 *`, cron `not a cron`); scheduler part of `test_legacy_trigger_configs_stay_loadable_and_isolated` |
| D2 | A trigger id republished with another type made every tick fail with `KeyError` | State was keyed by id only, and the type-specific dictionaries went out of sync | One state map keyed by (workflow, trigger) that holds the schedule spec. A changed spec re-arms | `test_type_change_under_same_id_rearms`, `test_changed_schedule_rearms_from_now` |
| D3 | Validation accepted configurations that cannot be scheduled (F2, F4) and the non-deterministic `R` (F3) | The croniter object was built, but no fire time was ever computed; intervals had no upper bound or finiteness check | `cron_problem` (5 fields, no `H`/`R`, a fire time must exist). Intervals must lie in [1 s, 1 year] (publish gate) and be finite (construction) | `TestScheduleProblem` (28 items), `test_interval_requires_positive_finite_seconds`, `test_unschedulable_triggers_are_issues_with_location`, `test_invalid_trigger_configs_block_publish_not_drafts` |
| D4 | Previously valid stored configurations broke resolution and scheduling for all workflows and disclosed another workflow's configuration; the affected workflow could not be fixed (F5, F19) | The Phase 10 rules were enforced at construction, which also runs while loading stored data | The rules became publish-time validation issues (A3); resolution is scoped by workflow (A4) | `test_legacy_trigger_configs_stay_loadable_and_isolated` |
| D5 | Some webhook bodies returned 500: 50,000 nested `[` (parser overflow); invalid UTF-8 (by inspection); about 500 levels (deepcopy in `Execution.create`, F18) | Only `JSONDecodeError` was caught; there was no depth limit | The route catches `ValueError`/`RecursionError` → 422. `TriggerContext` limits nesting to 64 (A8) | `test_unparseable_or_too_deep_payloads_are_422_not_500`, `test_payload_depth_limit`, `test_depth_check_survives_pathological_nesting` |
| D6 | Duplicate trigger ids were accepted; resolution picked the workflow whose id sorted first | No uniqueness rule; resolution was global | `DUPLICATE_TRIGGER_ID` publish gate; workflow-scoped URLs, registry and scheduler keys (A4) | `test_duplicate_trigger_ids_are_an_issue`, `test_invalid_trigger_configs_block_publish_not_drafts[duplicate]`, `test_same_trigger_id_in_two_workflows_is_routed_by_workflow`, `test_resolution_is_scoped_by_workflow`, `test_list_scheduled_keeps_same_id_in_two_workflows`, `test_same_trigger_id_in_two_workflows_fires_both` |

### Test and tooling problems

| # | Problem | Fix | Area |
|---|---|---|---|
| P1 | Two new tests failed on their first run because of wrong expectations: nested payload isolation, and a re-anchored interval boundary | Tests corrected; implementation unchanged | Test code |
| P2 | Test-authoring errors caught by mypy/ruff: a variable read before assignment, missing imports, a missing `@asynccontextmanager`, `NodeResult()` without `outputs`, `Graph` imported from the wrong module, fakes not matching the expected types, the `uow` fixture passed to services (F9) | Fixed. Fakes subclass the protocols or the concrete classes (one `cast`). The only new `type: ignore` is a deliberate string-coercion test | Test code |
| P3 | Lint findings (E501, F401, RUF059) and formatter reflows, including the joining of implicit string concatenations during the follow-up | Fixed | Source and tests |
| P4 | mypy rejected the untyped croniter | Override (F10) | `pyproject.toml` |
| P5 | The follow-up broke `test_validate_reports_structured_issues` (a pinned issue shape) | A deliberate, additive contract change; test updated (F21) | `tests/integration/test_workflows_api.py` |

### Pre-existing problems discovered

- **E1:** non-finite numbers in draft JSON reach PostgreSQL and fail with
  `DBAPIError`. Verified for a trigger interval of NaN before the fix.
  - Trigger intervals now reject NaN and infinity at construction.
  - Other config values, e.g. node configs, are unchanged by this phase and
    still reach the database.
  - Origin: Phases 05/06.
- **F8:** no production runtime caller.

### Environment

- No environmental failures.
- The host runs 8 unrelated long-running containers; they were not touched.

---

## 11. Fixes Applied

### 1. B1 / B2 / B3
See §10. Verification: the listed tests.

### 2. D1 — per-trigger isolation in the scheduler
- **Symptom:** `bad` sorted first → every tick raised, and `good` never
  fired.
- **Root cause:** arming and due computation ran outside the per-trigger
  `try`.
- **Implementation:**
  - `tick()` wraps `_step(resolved, now)` in `try`/`except Exception` and
    logs `scheduled_trigger_failed`;
  - `_step` stores `(spec, None)` before computing a boundary, so a
    computation that fails leaves the trigger parked. A parked trigger is
    skipped silently until its spec changes.
- **Verification:** the parametrized test shows `good` fires
  `[0, 1, 1, 1]` over 4 ticks and only one `scheduled_trigger_failed`
  record is logged.

### 3. D2 — state keyed by (workflow, trigger) with a schedule spec
- **Symptom:** a CRON → INTERVAL republish under the same id → `KeyError`
  on every tick.
- **Root cause:** separate per-type dictionaries keyed by the id alone.
- **Implementation:** one dictionary,
  `_due[(workflow_id, trigger_id)] = (spec, next_due | None)`. A spec that
  differs from the stored one re-arms from `now`.
- **Verification:**
  - `test_type_change_under_same_id_rearms`: no exception; fires on the new
    interval.
  - `test_changed_schedule_rearms_from_now`: 30 s → 60 s takes effect at
    once.

### 4. D3 — unschedulable and non-deterministic configurations
- **Implementation:**
  - `cron_problem`: field count, an `H`/`R` token regex that ignores names
    such as `MAR FRI`, and `next_fire` from a fixed start that catches
    `CroniterError`. Probes showed that every malformed input raised a
    `CroniterError` subclass.
  - `schedule_problem` for intervals: `1 <= s <= 31,536,000`.
  - Construction rejects NaN and infinity.
- **Verification:** 28 `TestScheduleProblem` items, the domain and API
  tests listed in §10.

### 5. D4 — keep stored data loadable
- **Implementation:**
  - The Phase 10 rules moved from construction to `validate_triggers`
    (A3).
  - Webhook/invoke resolution is scoped by workflow (A4), so another
    workflow's data is never loaded for a request.
- **Verification:** `test_legacy_trigger_configs_stay_loadable_and_isolated`
  on PostgreSQL. A workflow row and version row were rewritten to cron
  `garbage`. Then:
  - `GET` workflow → 200, `GET` version → 200;
  - `validate` reports `invalid_trigger_schedule` with the trigger id;
  - the other workflow's webhook → 202, with no `garbage` in the response;
  - the scheduler fires the healthy interval trigger while the legacy cron
    stays parked.

### 6. D5 — webhook body robustness
- **Implementation:**
  - `except (ValueError, RecursionError)` in the webhook route;
  - an iterative 64-level depth check in `TriggerContext`.
- **Verification:**
  - integration test: 50,000 × `[`, invalid UTF-8 and depth 501 on the
    webhook all give 422; depth 501 on invoke and execute gives 422;
  - unit tests: depth 64 is accepted, depth 65 rejected; 20,000 nested
    lists are rejected without a stack overflow.

### 7. D6 — workflow-scoped trigger addressing
- **Implementation:**
  - `DUPLICATE_TRIGGER_ID` in `validate_triggers`;
  - `resolve(workflow_id, trigger_id)` via `list_latest(workflow_id=…)`;
  - URLs `/workflows/{p}/triggers/{t}/invoke` and `/webhooks/{p}/{t}`;
  - scheduler keys `(workflow_id, trigger_id)`;
  - `list_scheduled` ordered by (trigger id, workflow id).
- **Verification:** the 6 tests listed in §10 D6.

### 8. Documentation corrections in touched files
- `domain/cron.py`: the dialect is now described accurately.
- `api/triggers.py`: the "runtime picks it up" statement was corrected.
- Architecture §125: workflow scoping and publish-gate rules.

---

## 12. Testing & Verification

### Test counts (final)

| Module | Items | New in Phase 10 |
|---|---|---|
| `tests/domain/test_trigger_credentials_plugin.py` | 57 | 34 (Phase 09: 23) |
| `tests/domain/test_workflow.py` | 23 | 3 |
| `tests/application/test_trigger_service.py` | 12 | 12 |
| `tests/runtime/test_scheduler.py` | 15 | 15 |
| `tests/test_lifecycle.py` | 5 | 2 |
| `tests/integration/test_triggers_api.py` | 22 | 22 |
| `tests/integration/test_workflows_api.py` | 37 | 0 (1 test changed, F21) |
| **Whole suite** | **841** | **88** (Phase 09: 753) |

- The suite splits into 681 non-integration and 160 integration items.
- The first implementation added 45 items (798). The follow-up added 43
  more: +26 domain trigger tests (net, after replacing the old cron-syntax
  construction tests), +3 workflow validation, +2 registry, +6 scheduler,
  +6 integration.

### Unit tests

**Domain**
- Construction rules: a non-empty cron string; a finite, positive interval
  (NaN and inf are rejected).
- `TestScheduleProblem`:
  - 11 unusable crons: garbage, out of range, 6 fields, never fires,
    `R`/`H` variants;
  - 7 usable crons, including names containing R/H, `L` and `#`;
  - 7 interval bounds cases;
  - 3 types without a schedule.
  - Messages never contain the expression.
- `TriggerContext`: the defaults, coercion, a naive timestamp, the top-level
  copy, the depth limit of 64 (with `details.max_depth`), and a 20,000-level
  list handled without overflow.
- `TestTriggerValidation`: duplicate ids produce an issue with a location;
  schedule issues are listed in draft order; trigger issues are absent from
  `WorkflowVersion.validate`.

**Application** (in-memory fakes that implement the repository protocols
explicitly)
- `fire`: the created execution and its metadata, disabled (409), type
  mismatch, unknown version.
- `fire_manual`.
- Registry:
  - latest-version resolution;
  - retirement by a newer version;
  - drafts never resolve;
  - the type filter;
  - workflow scoping (the same id in 2 workflows, and an unknown workflow);
  - `list_scheduled` filtering and order;
  - the same id in 2 workflows kept apart.

**Scheduler** (`FakeClock` and an injected sleep; no real sleeping)
- Interval: boundaries and context fields; missed runs skipped.
- Cron: `*/2` boundaries; 6 h of downtime gives one fire.
- Pruning and re-arming; a changed schedule re-arms from `now`; a failed
  fire does not break the pass.
- `TestIsolation`: 3 unschedulable configurations parked and logged once; a
  type change re-arms; the same id in two workflows fires for both.
- `run()`: cancellation, and it survives a broken tick.
- `poll_seconds` is validated.

**Lifecycle**
- The container wiring.
- The scheduler task starts and stops.
- The scheduler can be disabled by configuration.

### Integration and API tests

`test_triggers_api.py`, 22 tests on PostgreSQL 17.2 via Testcontainers,
through `httpx.ASGITransport`:

| Test | Verifies |
|---|---|
| `test_manual_execute_creates_canonical_execution` | 201, `manual`, `trigger_id` null, `correlation_id` from `x-request-id`; `GET /executions/{id}` |
| `test_manual_execute_unknown_version` | 404 `WORKFLOW_VERSION_NOT_FOUND` |
| `test_get_unknown_execution` | 404 `EXECUTION_NOT_FOUND` |
| `test_manual_execute_invalid_input_rejected` | JSON array → 422 (status only) |
| `test_invoke_api_trigger` / `test_invoke_manual_trigger_allowed` | 201 with `trigger_id` |
| `test_invoke_unknown_or_foreign_type_is_404` | Unknown trigger, unknown workflow and WEBHOOK id → identical 404 |
| `test_webhook_fires_with_payload_as_input` | 202; `correlation_id`; the persisted input equals the body |
| `test_webhook_unknown_or_wrong_type_is_404` | Unknown trigger, unknown workflow and API id → 404 |
| `test_webhook_rejects_non_object_payload` | Malformed JSON, JSON array → 422 |
| `test_unparseable_or_too_deep_payloads_are_422_not_500` | D5 on all three mechanisms |
| `test_webhook_payload_limit` | 413 |
| `test_disabled_trigger_is_409` | 409 |
| `test_republish_retires_old_trigger` | 202, then 404 after republishing without the trigger |
| `test_invalid_trigger_configs_block_publish_not_drafts` (3 cases) | `PUT` 200 → `validate` reports code and `trigger_id` → publish 422 `WORKFLOW_VALIDATION_FAILED` |
| `test_same_trigger_id_in_two_workflows_is_routed_by_workflow` | Each workflow's URL creates its own workflow's execution |
| `test_legacy_trigger_configs_stay_loadable_and_isolated` | D4 (see §11.5) |
| `test_triggered_execution_runs_through_the_same_runtime` | A webhook-created execution runs to `COMPLETED` through `ExecutionRuntime` |
| `test_scheduler_fires_interval_trigger_into_db` | Fake clock; exactly 1 `queued` row with trigger metadata |
| `test_scheduler_cron_fires_at_boundary` | Boundary, no re-fire, one fire after downtime; 2 rows (counts only) |

Error assertions use `assert_error`, which checks the key set, the code and
that no traceback appears. The exceptions:
- `test_manual_execute_invalid_input_rejected` checks the status only;
- the invoke/execute part of the D5 test checks the status and that no
  traceback appears.

### Infrastructure tests
- PostgreSQL 17.2 via Testcontainers for all 160 integration tests.
- `list_latest` with and without `workflow_id` is exercised through the
  registry.
- No Redis.

### E2E tests
- None exist yet (backend/AGENTS §64).

### Security tests
- 404 uniformity for unknown and wrong-type ids, and unknown workflows.
- 413, 409, and 422 for malformed, invalid UTF-8 and too-deep payloads.
- Cross-workflow routing (D6).
- No cross-workflow configuration leak (D4 test asserts that `garbage` is
  absent).
- No stack traces in error bodies.
- **Not asserted:** the header allowlist. The webhook test sends an
  `Authorization` header but checks only the response and the stored input.

### Probes (scratch scripts in `%TEMP%\opencode`, deleted afterwards)

| Scenario | Before the fix | After the fix |
|---|---|---|
| A bad trigger sorted first (interval `1e12`, cron `0 0 31 2 *`) | Every tick raised; the healthy trigger fired 0 times | The healthy trigger fires; the bad one is parked and logged once (test) |
| Same id republished CRON → INTERVAL | `KeyError` on every tick | Re-armed (test) |
| Duplicate ids in one draft / in two workflows | Accepted; resolved to the workflow sorting first | Draft accepted, `validate` issue, publish 422; each workflow routed to its own trigger (tests) |
| Stored cron `garbage` in one workflow (PostgreSQL) | Registry calls failed for all workflows; another workflow's webhook got 422 disclosing `{"cron": "garbage"}` | Loadable; issue reported; other workflows unaffected (test) |
| Webhook with 50,000 × `[` | 500 `INTERNAL_ERROR` | 422 `DOMAIN_VALIDATION_ERROR` |
| Webhook with depth 501 (~1 KB) | Not probed; deepcopy fails from about 500 levels, so 500 is inferred | 422 "trigger payload is nested too deeply" |
| Webhook with invalid UTF-8 | Not probed; `UnicodeDecodeError` was not caught (inspection) | 422 |
| `/invoke`, `/execute` with depth 501 | Not probed | 422 `VALIDATION_ERROR` (pydantic's nesting limit) |
| `/invoke`, `/execute` with depth 64 / depth 100 | Not probed | 201 / 422 `DOMAIN_VALIDATION_ERROR`. Pydantic accepts depth 100, and `TriggerContext` rejects it |
| `/invoke`, `/execute` with 50,000 × `[` | 400 `HTTP_ERROR` | Not re-probed; unchanged code path |
| Webhook with NaN; `x-request-id` of 200 characters; the same delivery twice | 422; 422 (F13); 2 executions (F15) | Not re-probed; code paths unchanged apart from the URL |
| `/openapi.json` | 4 paths; no webhook body; `status` a plain string | Workflow-scoped paths present; `ValidationIssueOut` includes `trigger_id`; webhook body and `status` enum still missing (F16) |

### Flakiness
- The phase-specific modules ran 3 times in a row (the 5 trigger modules
  plus `tests/domain/test_workflow.py`): 134 passed each time, in
  10.37–11.03 s.

### Not covered by tests
- The header allowlist.
- The `trigger_fired` log line.
- `CONFIGURATION_ERROR` on the trigger routes.
- A FastAPI lifespan that starts a real scheduler (the `Container` is tested
  directly).
- Settings bounds.
- OpenAPI for the new paths.
- Cron metadata assertions in the database.
- The stored input of an invoke call.
- Idempotency (it does not exist).

### Performance and accessibility
- No performance tests or benchmarks (§16).
- Accessibility: not applicable.

---

## 13. Quality Gates

| Quality Gate | Result | Notes |
|---|---|---|
| Formatter (`uv run ruff format --check .`) | PASS | 101 files already formatted |
| Linter (`uv run ruff check .`) | PASS | All checks passed |
| Type checker (`uv run mypy .`, strict, src + tests) | PASS | No issues in 99 source files |
| Unit tests | PASS | 681 non-integration items |
| Integration tests | PASS | 160 items (PostgreSQL 17.2, Testcontainers), 22 of them in `test_triggers_api.py` |
| API tests | PASS | `test_triggers_api.py`, `test_workflows_api.py` |
| Full suite (`uv run pytest`) | PASS | 841 passed, 0 failed, 0 skipped, 1 warning (the existing `StarletteDeprecationWarning`), 76.73 s. During the follow-up one run had 1 failure (F21: pinned issue shape); that contract change was deliberate and the test was updated |
| E2E tests | N/A | None exist yet |
| Build (`uv build`) | PASS | sdist 282,972 B and wheel 98,418 B built into `%TEMP%\opencode\phase10-build`, then deleted. This build was not run during the first implementation (§24) |
| Security checks | PASS (automated tests only) | §12. Open security items: F6 (guessable client ids), F12, no webhook authentication. No dependency-audit tool is configured |
| Accessibility | N/A | Backend |
| Flakiness | PASS | 3 × 134 |
| Line endings | PASS | 28 changed/new files, 0 CR bytes |

---

## 14. Acceptance Criteria Verification

| Acceptance Criterion | Status | Evidence |
|---|---|---|
| Roadmap AC: "No trigger type implements its own execution engine" | PASS | Every mechanism ends in `TriggerService._create` → `ExecutionService.create_execution`. No graph traversal in `api/triggers.py`, `application/triggers.py` or `runtime/scheduler.py`. `ExecutionRuntime(` has no call site in `src/`. `test_triggered_execution_runs_through_the_same_runtime` |
| Roadmap scope: TriggerService, TriggerRegistry, TriggerContext | PASS | `application/triggers.py`, `domain/trigger.py` |
| Roadmap scope: Manual, API, Cron, Interval, Webhook triggers | PASS | As mechanisms (A2) |
| Roadmap scope: converge on the same Execution Service | PASS | Single call site `TriggerService._create`; `test_fire_creates_queued_execution_with_metadata` |
| Roadmap test: manual trigger | PASS | `test_manual_execute_creates_canonical_execution`, `test_invoke_manual_trigger_allowed`, `test_fire_manual` |
| Roadmap test: API trigger | PASS | `test_invoke_api_trigger`, `test_fire_creates_queued_execution_with_metadata` |
| Roadmap test: cron scheduling | PASS | `TestCron`, `test_scheduler_cron_fires_at_boundary` |
| Roadmap test: interval scheduling | PASS | `TestInterval`, `test_scheduler_fires_interval_trigger_into_db` |
| Roadmap test: webhook payload | PASS | `test_webhook_fires_with_payload_as_input`, `test_webhook_rejects_non_object_payload`, `test_unparseable_or_too_deep_payloads_are_422_not_500`, `test_webhook_payload_limit` |
| Roadmap test: invalid trigger configuration | PASS | `TestScheduleProblem`, `test_interval_requires_positive_finite_seconds`, `test_invalid_trigger_configs_block_publish_not_drafts`, `test_disabled_trigger_is_409`, the wrong-type 404 tests |
| Prompt: converge on the existing Execution Service **and Runtime** | PASS, with a note | Proven through the runtime by a test; the deployment has no runtime caller yet (F8, Phase 22) |
| Prompt: tests for every trigger type | PASS | Manual, API, webhook, cron, interval |
| Session: every supported trigger creates the same canonical Execution | PASS | One creation path and one metadata schema. Identical `ExecutionDetail` fields for manual/API/webhook; database rows for interval (cron: count) |
| Session: tests for duplicate/invalid trigger configurations | PASS | Duplicates: `test_duplicate_trigger_ids_are_an_issue`, `test_invalid_trigger_configs_block_publish_not_drafts[…duplicate…]`, `test_same_trigger_id_in_two_workflows_is_routed_by_workflow`, `test_resolution_is_scoped_by_workflow`, `test_same_trigger_id_in_two_workflows_fires_both`. Invalid: see above |
| Session: event-trigger boundary without unrelated event infrastructure | PARTIAL | See the details after this table. Does not block |
| Session: no sleep-based scheduler tests; deterministic clock/scheduler | PASS | `FakeClock`, injected sleep, `tick()`. No real sleep in the new tests (only a 30 s `wait_for` guard) |
| Session: quality gates pass | PASS | §13 |
| Follow-up: fix defects D1–D6 | PASS | §10/§11; each has regression tests |
| Definition of Done (§34): documentation / API contracts updated where required | PARTIAL | Architecture §125 and the touched docstrings are updated. Outdated: `backend/README.md` (settings, API table, layout), the runtime component map, `domain/__init__.py`. OpenAPI gaps (F16) |

Details for the event-trigger boundary row:
- **Present:** `TriggerContext` and `TriggerService.fire` do not depend on the
  trigger type.
- **Missing:**
  - a registry query for EVENT triggers;
  - recursion-depth data (§129–§131);
  - documentation of the boundary.

**Conclusion.**
- **Satisfied:** all roadmap, prompt and session test requirements, and the
  follow-up.
- **PARTIAL:** two items — the event-trigger boundary, and documentation /
  contract completeness.
- **Remaining limitations:** §20.
- Status: COMPLETE WITH LIMITATIONS.

---

## 15. Security Considerations

### Authentication and authorization
- None on the new endpoints, like all of `/api/v1` until Phase 20.
- Anyone who can reach the API can:
  - run any published version;
  - invoke API and MANUAL triggers;
  - deliver webhooks;
  - read any execution by id.

### Webhook authentication
- None: no signature and no secret (Phases 13/20/25).
- The workflow id and the trigger id are the only barrier, and both are
  guessable:
  - clients choose trigger ids;
  - any `<prefix>_<32 hex>` is accepted, including all zeros;
  - `GET` on workflows returns both ids.

### Cross-workflow isolation (fixed, D4/D6)
- Webhook and invoke resolution load only the addressed workflow's latest
  version.
- A colliding trigger id in another workflow can no longer capture
  deliveries.
- Another workflow's stored data can no longer fail a request or leak into
  its error body (tested: no `garbage` in the response).

### Information disclosure
- Wrong-type ids, unknown ids and unknown workflows all get identical 404
  responses (tested).
- Error bodies contain no stack traces (tested).
- Validation issues carry trigger ids but no configuration values.

### Secrets and sensitive data
- The header allowlist keeps `Authorization`, `Cookie` and other headers out
  of `TriggerContext`.
- Headers are not persisted or logged, and payloads are not logged. This
  was checked by reading the code; no test asserts it.
- Payloads are stored in plain text as `executions.input`. No API response
  of this phase returns them. Redaction is Phase 13.

### Input validation
- Only JSON objects are accepted, nested at most 64 levels.
- NaN, infinity and NUL are rejected by `Execution`.
- Parser failures (overflow, invalid UTF-8) map to 422.
- `x-request-id` is validated (F13).

### Resource exhaustion
- Webhook bodies are read in full before the size check.
- `/execute` and `/invoke` bodies have no size limit (F12).
- There is no rate limiting (§108, Phase 25).
- 1-second interval triggers can create executions without limit
  (Phase 24).
- A single bad schedule can no longer stall other workflows' scheduling
  (D1 fixed).

### Logging
- Scheduler failures are logged at ERROR with a traceback: once per trigger
  boundary or spec, and once per poll for tick-level failures (F17).
- No payloads or secrets are logged.

### Not applicable
- SSRF, path traversal, file handling, expression evaluation, plugins and
  WebSockets are not touched by this phase.

### Supply chain
- croniter, python-dateutil and six (§18). No dependency audit was run.

---

## 16. Performance Considerations

### Resolution
- `resolve(workflow_id, trigger_id)` loads **one** row: the addressed
  workflow's latest version.
- The first implementation loaded the latest version of *every* workflow for
  each webhook or invoke call.

### Scheduler scan
- `list_scheduled()` still loads the latest version row of every workflow,
  graph included, on every tick. With the defaults that is once per second
  per process.
- The limit is marked with a `ponytail:` comment in
  `infrastructure/persistence.py`.
- The `(workflow_id, version)` unique index is available for the join. No
  `EXPLAIN` was run.

### Tick cost
- O(scheduled triggers) per tick.
- Cron `next_fire` took about 0.2–1.0 ms per call in the probes (indicative
  only).
- Interval jumps are O(1).
- Parked triggers cost one dictionary lookup.

### Validation cost
- `cron_problem` computes one `next_fire` per cron trigger on every
  `validate` or `publish`.
- The depth check is O(payload size), with an early exit at 64 levels.

### Fires
- Fires are sequential, one write transaction each.
- The sleep comes after the tick, with no drift compensation.

### Payloads
- Webhook bodies are limited to 1 MiB by default.
- `ExecutionDetail` is small.

### Measurements
- Test durations only: the full suite took 76.73 s; the 134 phase-specific
  tests took 10.37–11.03 s including the PostgreSQL container start.
- No benchmarks.

---

## 17. Documentation Changes

### Updated in this phase

| File | Reason | What was documented |
|---|---|---|
| `docs/architecture.md` §125 | The resolution rule was undefined; then the follow-up decisions (A3, A4) | Configured on the draft; resolves to the latest published version; drafts never fire; ids unique per workflow, URLs carry the workflow id; schedule and uniqueness rules are publish-blocking validation issues; no catch-up |
| `backend/src/akasha/domain/trigger.py` (docstring) | Construction vs. publish-time rules | Why the stricter rules are validation issues |
| `backend/src/akasha/domain/cron.py` (docstring) | The first version claimed "POSIX only" and "always schedulable" (F2, F3) | The accepted croniter dialect; the `H`/`R` rejection; the fixed probe start |
| `backend/src/akasha/domain/workflow.py` (docstrings) | Trigger validation | `validate_triggers`; why `WorkflowVersion.validate` leaves out trigger rules |
| `backend/src/akasha/api/triggers.py` (docstring) | The first version said "the runtime picks it up" (F8) | Workflow-scoped URLs; QUEUED until workers exist |
| `backend/src/akasha/application/persistence.py`, `application/triggers.py`, `runtime/scheduler.py` (docstrings) | New behavior | `list_latest(workflow_id=…)`, workflow-scoped resolution, the scheduler's state and parking |
| `docs/reports/backend/phase-10-trigger-system.md` | Phase report | This document; it replaces the first version, which listed D1–D6 as open |

### Still outdated (not done)

| File | Problem |
|---|---|
| `backend/README.md` | The settings table lacks the 3 new settings; there is no trigger/execution API table; the layout comments are outdated |
| `backend/src/akasha/runtime/__init__.py` | The component map does not list `runtime.scheduler` (F11) |
| `backend/src/akasha/domain/__init__.py` | Says "stdlib only where practical"; the domain imports croniter |
| Architecture §17 / §22, `docs/adr/` | Decisions A2–A8 are recorded only in code and in this report. No ADR directory exists |

---

## 18. Dependencies / Infrastructure Changes

### Dependencies

| Change | Details | Why |
|---|---|---|
| New runtime dependency `croniter>=6.2.4` | Locked at 6.2.4 (sdist 2026-07-10 per `uv.lock`). MIT. `github.com/pallets-eco/croniter`. Pure Python | Cron parsing and next-fire computation |
| Transitive `python-dateutil 2.9.0.post0` | Dual BSD / Apache-2.0 | Required by croniter |
| Transitive `six 1.17.0` | MIT | Required by python-dateutil |
| mypy override for `croniter` | `ignore_missing_imports = true` | croniter ships no types |

### Configuration and constants

| Variable / constant | Default | Purpose |
|---|---|---|
| `AKASHA_SCHEDULER_ENABLED` | `true` | Run the in-process scheduler |
| `AKASHA_SCHEDULER_POLL_SECONDS` | `1.0` (> 0) | Poll interval |
| `AKASHA_WEBHOOK_MAX_PAYLOAD_BYTES` | `1048576` (> 0) | Webhook body limit |
| `MAX_INTERVAL_SECONDS` (code) | 31,536,000 | Upper bound for intervals (publish gate) |
| `MAX_PAYLOAD_DEPTH` (code) | 64 | Payload nesting limit. A `ponytail:` constant; making it configurable is Phase 24 |

### Other changes
- **Startup:** with a database configured, startup launches one background
  scheduler task per process.
- **Database:** no schema change and no migration.
- **Not affected:** Redis, Docker/compose and external services.
- **Test infrastructure:** new package `tests/application/`; the existing
  PostgreSQL 17.2 Testcontainers fixtures are reused.
- **Dependency audit:** not performed.

---

## 19. Deferred Work

| What | Why deferred | Intended phase | Dependency / blocker | Affects completion? |
|---|---|---|---|---|
| Running `QUEUED` executions (queue, workers) | Outside the roadmap scope | 22 | Queue | No |
| Firing event triggers, plus recursion safeguards | Needs the event system | Not assigned (after 11) | Phase 11 | No (PARTIAL in §14) |
| `trigger.fired` and `execution.*` events | Event system | 11 | — | No |
| Webhook authentication and signatures | Credentials and auth are missing | 13 / 20 / 25 | Credential service | No |
| Authentication and authorization on the endpoints | All of `/api/v1` is unauthenticated | 20 | — | No |
| Idempotency for trigger delivery (§92) | Not in scope | Not assigned (22 covers queue duplicates) | — | No |
| Request size limits for execute/invoke; a streaming or `Content-Length` limit for webhooks; rate limiting; a configurable depth limit | Request limits deferred since Phase 06 | 24 / 25 | — | No |
| Listing triggers and exposing webhook URLs | Not in roadmap §14 | Before or with frontend Phase 18 | — | No |
| Distributed scheduling, persisted schedule state, a catch-up policy | In-process until workers exist | 22 / 23 / 26 | Workers | No |
| Metrics | Observability phase | 12 | — | No |
| Cron timezone support | Not requested | Not assigned | — | No |
| Gating triggers on workflow status | No status use cases exist | Not assigned | Status transitions | No |

---

## 20. Known Limitations

### L1 — Queued executions are never run
Nothing in the deployment executes `QUEUED` executions.

### L2 — In-process scheduler with in-memory state
- One scheduler runs per API process, so several processes fire duplicates.
- State lives in memory:
  - a restart re-anchors interval schedules, so an interval longer than the
    process uptime may never fire;
  - the first tick after a restart only arms, so a cron boundary during
    downtime is lost.

### L3 — No catch-up
Missed runs are skipped; each trigger fires at most once per tick.

### L4 — Invalid schedules in older published versions are parked
- A version published before these rules may contain a schedule that
  cannot be computed. The scheduler parks it and logs once, until the
  workflow republishes with a changed schedule.
- A legacy interval below 1 s fires at most once per tick.

### L5 — Invalid triggers are allowed in drafts
Drafts may contain invalid triggers (by design). Only publishing refuses
them.

### L6 — Cron semantics
- Cron is evaluated in UTC only.
- The accepted dialect is croniter's 5-field syntax, including names, `L`
  and `#`.
- Unicode decimal digits are also accepted (F20).

### L7 — The scheduler scans every workflow
It reads every workflow's latest version row on every tick.

### L8 — Race between resolution and creation
A publish between resolution and creation can run the version that was just
retired.

### L9 — No trigger listing endpoint
No endpoint lists triggers. The webhook URL follows a convention
(`/api/v1/webhooks/{workflow_id}/{trigger_id}`) and is not returned by any
endpoint.

### L10 — Webhook body handling
- Any content type is accepted.
- An empty body is treated as `{}`.
- The size limit is checked after reading the body.
- A caller-supplied `x-request-id` can cause a rejection (F13).

### L11 — `headers` and `source` are not stored
`TriggerContext.headers` and `source` are not persisted, and nothing reads
`headers` yet.

### L12 — Minimal `ExecutionDetail`
`ExecutionDetail.status` is not an enum in OpenAPI, and the response has no
node-level detail and no input.

### L13 — No authentication
API and webhook triggers carry no authentication, and the ids are
guessable (F6).

### L14 — Workflow status is ignored
Trigger resolution ignores workflow status (F7).

---

## 21. Open Issues

### Resolved by the follow-up fix (listed as open in the first report)

| # | Issue | Resolution |
|---|---|---|
| OI1 | D1 + D3: one unschedulable trigger stalled scheduling across workflows | FIXED: a per-trigger error boundary, parking, publish-time schedule rules |
| OI2 | D2: `KeyError` after a type change under the same id | FIXED: state keyed by (workflow, trigger) with a spec; re-arms on change |
| OI3 | D4: stored data became unloadable; cross-workflow outage and disclosure | FIXED: rules moved to validation issues; resolution scoped by workflow |
| OI4 | D6: duplicate trigger ids; cross-workflow capture | FIXED: `DUPLICATE_TRIGGER_ID` publish gate; workflow-scoped addressing |
| OI5 | D5: deeply nested webhook bodies returned 500 | FIXED: parse errors → 422; 64-level depth limit |

### Still open

| # | Issue | Impact | Severity | Affected area | Recommended next action | Target phase |
|---|---|---|---|---|---|---|
| OI6 | F13: an invalid `x-request-id` rejects the request | Legitimate webhooks can be rejected | LOW | `api/triggers.py` | Drop or truncate invalid correlation ids for external mechanisms | 12 or a follow-up |
| OI7 | F16: OpenAPI has no webhook body and no `status` enum, and no test covers the new paths | The frontend contract is ambiguous | LOW | `api/` | Document the body; add the enum; add an OpenAPI test | Before frontend Phase 18 |
| OI8 | Outdated documentation (§17) | Misleads future developers | LOW | README, docstrings | Update `backend/README.md`, the runtime component map, `domain/__init__.py` | Next documentation pass |
| OI9 | F7: workflow status is not consulted | Latent | LOW | `application/triggers.py` | Consult `Workflow.status` once status transitions exist | When status use cases are added |
| OI10 | F17: tick-level failures are logged on every poll | Log noise while the database is down | LOW | `runtime/scheduler.py` | Rate-limit repeated tick-failure logs | 12 |
| OI11 | F6: client-chosen, guessable trigger ids, and no webhook authentication | Anyone who knows the ids can fire | MEDIUM | API / security | Webhook signatures or secrets through credential references; server-generated or unguessable ids | 13 / 20 / 25 |

F12, F14 and F15 are intentionally deferred and tracked in §19.

---

## 22. Architectural / Product Decisions

### Version resolution and trigger addressing
- A trigger resolves to the **latest published version** of its workflow.
  Drafts never fire (A1, §125).
- Trigger ids are **unique per workflow, not globally**. Every trigger URL
  carries the workflow id, and scheduler state is keyed by (workflow id,
  trigger id) (A4).
- A webhook URL survives a republish only while the draft keeps the trigger
  id.

### Validation rules
- **Rules that postdate stored data are validation issues, not construction
  errors** (A3). This follows the `validate_dependencies` precedent.
  - Construction may be tightened only for values that can never have been
    persisted (e.g. NaN/inf, which JSONB rejects).
- Trigger rules gate **publishing only**. `WorkflowVersion.validate`, the
  runtime's pre-run check, ignores them: a run never depends on how it was
  triggered.
- Schedules must be **deterministic and able to fire**: croniter's `H`/`R`
  are rejected; one fire time is computed from a fixed start. Intervals must
  lie in [1 s, 1 year].
- Trigger payloads may nest at most 64 levels, enforced in `TriggerContext`,
  the single boundary every mechanism passes (A8).

### Mechanisms and the service
- Roadmap trigger types are **mechanisms, not classes** (A2).
- **Only `TriggerService` creates trigger executions**, and only via
  `ExecutionService.create_execution`.
- There are two forms of MANUAL execution: ad hoc (`/execute`, no
  `trigger_id`) and configured (`/invoke`).

### Scheduler
- Triggers are armed on first sight and never fire retroactively.
- Missed runs are skipped.
- Cron runs in UTC.
- An unschedulable trigger is parked and logged once.
- The next boundary is advanced before firing, so a failure is logged once
  per boundary.
- There is one loop per process, owned by the `Container`. Clock and sleep
  are injectable.

### API conventions
- A wrong-type id answers **404**, the same as an unknown id.
- Webhooks answer **202**; execute and invoke answer **201**.
- `ExecutionDetail` carries no input or outputs.
- Headers pass through an allowlist and are never persisted.
- `GET /api/v1/executions/{execution_id}` is the minimal observation
  endpoint §126 requires.
- The manual run is the workflow-scoped `POST
  /workflows/{p}/versions/{v}/execute`.

---

## 23. Repository / Git State

- **Branch:** `main`.
- **HEAD:** `36b1a3e backend phase 09 - node registry and built-in nodes`. No
  commit was made for Phase 10; none was requested.
- **Working tree:** uncommitted; nothing staged.
- **Modified tracked files:** 18, +516/−22 (§4).
- **Untracked:**
  - `backend/src/akasha/{api/trigger_schemas.py, api/triggers.py,
    application/triggers.py, domain/cron.py, runtime/scheduler.py}`;
  - `backend/tests/application/` (`__init__.py`,
    `test_trigger_service.py`);
  - `backend/tests/integration/test_triggers_api.py`,
    `backend/tests/runtime/test_scheduler.py`;
  - this report.
- **Unrelated changes:** none detected.
- **Line endings:** LF only (0 CR bytes) in all 28 changed or new files.
- **Temporary artifacts:**
  - The build output and all scratch probe scripts in `%TEMP%\opencode` were
    deleted.
  - No test containers remained.
  - 8 unrelated long-running containers on the Docker host (created between
    December 2025 and August 2026) were not touched.

---

## 24. Phase Completion Assessment

### Implemented
- **Domain:**
  - `TriggerContext` with a depth limit;
  - cron and schedule validation as publish gates;
  - unique trigger ids per workflow;
  - `ValidationIssue.trigger_id`.
- **Application:** a registry scoped by workflow; `TriggerService`.
- **Persistence:** `list_latest(workflow_id=None)`.
- **Runtime:** `TriggerScheduler` with per-trigger isolation, spec-based
  re-arming and parking.
- **API:** the four endpoints.
- **Wiring:** container wiring, the lifespan start, three settings.
- **Dependencies and docs:** croniter; architecture §125.
- **Follow-up:** D1–D6 fixed.

### Verified
- 841/841 tests pass (88 new in this phase), including 160 PostgreSQL
  integration tests.
- Regression tests for D1–D6.
- A webhook-created execution runs to `COMPLETED` through the runtime.
- ruff, format, mypy and `uv build` pass.
- 3 × 134 flakiness runs pass.

### Not Implemented
- Per-type trigger classes (a decision, A2).
- Listing triggers.
- Webhook authentication.
- Delivery idempotency.
- Firing event triggers.

### Deferred
- See §19: workers (22), events (11), credentials and authentication
  (13/20), limits and hardening (24/25).

### Open
- OI6–OI11 (§21).

### Blocking Issues
- None for the acceptance criteria.
- OI11 (no webhook authentication, guessable ids) is the most significant
  item left for a shared deployment. It depends on Phases 13/20.

### Corrections to earlier statements

| Earlier statement | Where | Correction |
|---|---|---|
| "Tests (798 total, +43 new)" | End-of-implementation summary | +45 at that point; 88 in total after the follow-up |
| "Build: PASS (818 files/gates …)" | End-of-implementation summary | Malformed, and no build had been run. Builds were run for both report versions: PASS |
| "Status: COMPLETE" | End-of-implementation summary | COMPLETE WITH LIMITATIONS (§14) |
| "failed ticks/fires logged and survived" | End-of-implementation summary | Not true before the follow-up (D1/D2). Now true per trigger; tick-level failures are still logged each poll (F17) |
| "unguessable trigger-id URLs" | End-of-implementation summary | Clients choose ids, and they are guessable (F6, OI11) |
| D5 threshold "50,000 nested `[`" | First report | About 500 levels (~1 KB) sufficed (F18) |
| D4 described as a registry and `GET version` problem | First report | It also locked the affected workflow's draft (F19) |
| D1–D6 listed as OPEN | First report | FIXED (§10–§11) |

**Phase Status:** COMPLETE WITH LIMITATIONS

---

## 25. Handoff to Next Phase

The next phase is Phase 11, the Event System.

### What the next phase can assume
- Every trigger execution is created in one place:
  `TriggerService._create` → `ExecutionService.create_execution`.
  - `ExecutionService.create_execution` is the natural point to emit
    `execution.created`.
  - `TriggerService._create` is the natural point for `trigger.fired`. Vision
    §14 lists that event; the Phase 11 roadmap list does not.
- `ExecutionMetadata` carries `trigger_type`, `trigger_id` and
  `correlation_id`.
- `TriggerRegistry.resolve(workflow_id, trigger_id, trigger_type=…)` and
  `list_scheduled()`.
- `WorkflowVersionRepository.list_latest(workflow_id=None)`.
- Validation issues carry `trigger_id`.
- The codes `duplicate_trigger_id` and `invalid_trigger_schedule` exist.

### Event triggers (when they are scheduled)
1. Add a registry query for EVENT triggers, e.g. by event name, over the
   latest versions.
2. Fire them with `TriggerService.fire(resolved, TriggerContext(EVENT, …))`.
3. Before enabling them, add recursion or trigger-depth data to the context
   and the metadata (§129–§131).
4. Add any EVENT config rules as validation issues (A3), not construction
   errors.

### Must not change casually
- The §125 rules: latest-version resolution, workflow-scoped ids and URLs,
  publish-gate validation.
- The URL shapes `/api/v1/webhooks/{workflow_id}/{trigger_id}` and
  `/api/v1/workflows/{workflow_id}/triggers/{trigger_id}/invoke`.
- The error codes `TRIGGER_NOT_FOUND`, `TRIGGER_DISABLED`,
  `PAYLOAD_TOO_LARGE` and the two validation codes.
- The fields of `ExecutionDetail` and the `trigger_id` field of validation
  issues.
- The scheduler's injectable clock and sleep, and the `Container` owning its
  task.
- The "no trigger-specific execution engines" rule.

### Known constraints
- One in-process scheduler per process.
- No consumer of `QUEUED` executions.
- No authentication.
- Cron only in UTC.
- The scheduler scans every workflow's latest version on every tick.

### Prerequisites for Phase 11
- None block it.
- The execution lifecycle events can come from the existing runtime state
  transitions and `ExecutionService`. None of that needs trigger changes.
