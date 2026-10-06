# Phase 27 — Backend V1 Integration

## 1. Executive Summary

**Goal.** Roadmap §31: validate the complete backend as one coherent system,
using the official example workflows, and fix what integration exposes.

**Method.** Run every suite → classify each failure → trace it to its root
cause → fix it at the owning layer → add a regression test → rerun the
targeted suite, then the full suite.

**Result.** The complete journey works end to end for all four official
example workflows, through the production composition root. It covers
create, configure, validate, publish, trigger, queue, worker, runtime,
events/logs/metrics, inspection, report, export, import and re-execution.

| # | Finding | Class | Severity | Resolution |
|---|---|---|---|---|
| D1 | Runtime race: a retry backoff that ends between two clock reads ends the scheduler loop with a READY node, so `complete()` raises `INVALID_STATE_TRANSITION` and the run is lost | runtime defect | HIGH | Fixed in `ExecutionRuntime._schedule`; deterministic regression test |
| D2 | The vision §58 example workflows could not be built: no transform/constant node, `core.web_search` (LIST) cannot feed `core.llm.variables` (OBJECT), and `core.if` tests only the whole value | V1 capability gap | HIGH (acceptance) | `core.set`, `core.pick`, `core.if.field` (1.1.0) — decision approved by the workflow owner |
| D3 | Architecture §22 API surface missing: no node catalog, execution list, event history or cancel over HTTP (cancel was CLI-only) | API gap | MEDIUM (frontend blocker) | Thin endpoints over existing services — decision approved by the workflow owner |
| D4 | Event-history pagination: the router requested `limit + 1` rows, which the service's page check rejects at `limit=100` | defect (found by the new tests) | LOW | The service returns a `Page` like every other listing |
| T1 | A parametrized test id (a 200 KB nesting bomb) exceeds the Windows env-var limit via `PYTEST_CURRENT_TEST` (2 errors) | test infrastructure | — | Explicit `ids=` (2 sites) |
| T2 | POSIX-only assertions (0600 mode, SIGINT/SIGTERM, unprivileged symlinks) fail on a Windows host (5 failures) | test infrastructure | — | `posix_only` marker; the mode assertion inside the backup/restore test is guarded, not the whole test |

**Phase Status:** COMPLETE WITH LIMITATIONS (§11).

---

## 2. Implemented Capabilities (Backend V1)

| Area | State | Evidence |
|---|---|---|
| Startup, configuration, health/readiness | fail-fast settings; `/health` vs `/ready` (DB, schema head, Redis) | `test_lifecycle.py`, `test_health.py`, deployment test |
| Migrations | Alembic; empty → head; upgrade path | `test_persistence.py`, deployment test |
| Authentication / authorization | local accounts, scrypt, opaque sessions, owner-scoped workflows, default-deny router | `test_auth_api.py` (incl. OpenAPI walk + cross-tenant 404s, now covering the new routes) |
| Workflows, drafts, validation, publish, immutable versions | optimistic concurrency; versions read-only | `test_workflows_api.py`, E2E `assert_version_is_immutable` |
| Node contracts, registry, built-in nodes, SDK, plugins | 13 built-ins; SDK contract tests; plugin lifecycle | `tests/nodes`, `test_sdk.py`, `test_plugins.py`, `test_plugin_runtime.py` |
| Runtime: branching, parallel, merge, control flow, retry, timeout, cancellation, recovery | | `test_runtime.py`, `test_reliability.py`, `test_workers.py` |
| Triggers | manual, API, webhook (HMAC), cron, interval | `test_triggers_api.py`, E2E cron via the scheduler on an injected clock |
| Events, structured logs, metrics | persisted events; JSON logs with ids; Prometheus | `test_events.py`, `test_observability.py`, E2E |
| Credentials, redaction | AES-256-GCM, references only, redaction at every output channel | `test_credentials.py`, `test_redaction.py`, E2E secret canaries |
| LLM abstraction, LLM node, tokens, cost | AIService → LiteLLM; usage and cost in the report | `test_ai_service.py`, `test_llm_node_runtime.py`, E2E |
| Reports, import/export | deterministic packages, no secrets | `test_reports.py`, `test_workflow_packages*.py`, E2E round trip |
| CLI, workers, queue | operator CLI; Redis queue; worker heartbeat/recovery | `test_cli.py`, `test_workers.py` |
| Resource limits, security hardening | Phase 24/25 | `test_resource_limits.py`, Phase 25 suites |
| Docker deployment, backup/restore | Compose with pinned images | `tests/deployment/test_compose_lifecycle.py` |

---

## 3. Changes in This Phase

### 3.1 D1 — scheduler race (runtime)

`_schedule` launched READY nodes whose `retry_at <= clock()`, then called
`_retry_wait`, which read the clock again and counted only `retry_at >
clock()`. When the backoff ended between the two reads, the node was in
neither set. With nothing running the loop broke, and `execution.complete()`
refused, correctly, because a node was unfinished. The exception escaped
the run.

Fix: break only when nothing runs **and** no node is READY. Otherwise loop
again and launch it. This cannot spin: with nothing running, a due READY
node always gets a slot, and a future one yields a positive backoff.

Regression test: `test_backoff_ending_between_clock_reads_still_runs_the_retry`
uses a clock that advances 15 ms per read against a 20 ms backoff. It
failed before the fix (`INVALID_STATE_TRANSITION`) and passes after. The
existing wall-clock test `test_backoff_is_exponential_and_recorded` failed
in both full runs on Windows (timer granularity about 15.6 ms) and now
passes.

### 3.2 D2 — data-shaping nodes

| Node | Contract | Purpose |
|---|---|---|
| `core.set` 1.0.0 | in `value` (JSON, optional) → out `value` (OBJECT); config `fields` (object), `key` | constants into a graph (a cron run has no input); wrap a list for `core.llm.variables` |
| `core.pick` 1.0.0 | in `value` (JSON) → out `value` (JSON); config `path` (required) | one field to a typed port (e.g. LLM structured output → SMTP subject) |
| `core.if` 1.1.0 | optional `field` | test `score` but route the whole value |

Paths share one lookup (`nodes/core.py: lookup`, also used by `core.llm`
templates): mapping keys and list indices only, nothing evaluated. Errors:
`SET_KEY_MISSING` (a value with no `key` is a wiring bug, not silently
dropped), `PICK_PATH_MISSING`, `CONDITION_FIELD_MISSING`. The `core.if`
change is a minor version: existing configurations stay valid, and imports
of 1.0.0 packages are accepted (same major, newer minor).

### 3.3 D3 — API surface (architecture §22)

| Route | Notes |
|---|---|
| `GET /api/v1/nodes` | catalog from the registry: ports (type, required, nullable, multiple, schema), config JSON Schema, capabilities. Any session |
| `GET /api/v1/workflows/{id}/executions` | newest first, `{items, page}` like other listings. Uses the `ExecutionSummary` read model: one query per page, no node rows or snapshots |
| `GET /api/v1/executions/{id}/events?after=&limit=` | ascending by position; `next_after` + `has_more`; payloads pass through `redact` |
| `POST /api/v1/executions/{id}/cancel` | `ExecutionRuntime.cancel` (the CLI's path): an owning worker elsewhere sees it on its next heartbeat. 202, finished → 409 |

`GET /executions/{id}` moved from `api/triggers.py` to `api/executions.py`.
It now also returns `started_at` and `finished_at`. That is additive: no
field was removed or renamed. Every new route carries a workflow or
execution id in its path, so `authorize_path` scopes it with no new
authorization code. The cross-tenant test (`scoped_requests`) and the
OpenAPI default-deny walk both cover them.

### 3.4 Example workflows (architecture §88)

`backend/examples/workflows/{hello-world,llm,web-research,lead-generation}.json`
and a README. Deviation from vision §58: Hello World's "Transform" is
`core.set`. Web Research's "Report" is the execution report
(`/reports/executions/{id}`), not a node. Lead Generation's "Scoring" is
`core.if` on the LLM's `score` field.

---

## 4. Test Suites Executed

All on Windows 11, Docker 29.1.2, Python 3.14. Testcontainers: `postgres:17.2`,
`redis:8.2.1-alpine`, `axllent/mailpit:v1.21.8`.

| Run | Result |
|---|---|
| Baseline (before changes) | 1808 passed, 5 failed, 2 errors, 6 skipped |
| Final full suite (`AKASHA_TESTS_REQUIRE_DOCKER=1 uv run pytest`) | **1843 passed, 10 skipped, 0 failed** (301 s) |
| Deployment (`AKASHA_TESTS_DEPLOYMENT=1 uv run pytest tests/deployment`) | **1 passed** (159 s) |

The 10 skips: deployment (run separately, above), 5 external OpenRouter
smoke tests (need a real key, never part of CI), 3 POSIX-only and 1
symlink-privilege test (§3, T2; they run on Linux CI).

Pyramid coverage: unit (`tests/domain`, `tests/runtime`, `tests/nodes`,
`tests/test_*.py`), component/application (`tests/application`),
integration with real PostgreSQL and Redis (`tests/integration`), API (`test_*_api.py`),
contract (`test_builtin_contract.py`, SDK contract suite, OpenAPI walk),
infrastructure (persistence, migrations, workers, Mailpit), security
(Phase 25 suites, redaction, isolation, secret canaries in E2E), E2E
(`test_e2e_examples.py`, deployment lifecycle).

### 4.1 New tests

| Test | Proves |
|---|---|
| `test_e2e_examples.py` (4) | each example: HTTP → validate → publish → immutable version → trigger (manual invoke; **cron** via the scheduler) → Redis → in-process worker → runtime → events, listing, report → export (no secrets in the archive) → import → publish → run the copy. LLM via LiteLLM against a loopback OpenAI-compatible server (prompt rendering, key only in the `Authorization` header, token usage, `cost_complete: false` for unpriced endpoints). Web search via a loopback SearXNG shape. **E-mail via Mailpit**, on both branches of the score gate. Structured log fields, `/metrics`, no secret in any log line or report |
| `test_executions_api.py` (8) | listing order and pagination, bad pages (422), unknown ids (404), event cursor walk, redacted failure events, cancel (202, then 409, never revived), catalog shape for a member |
| node tests (`test_core_nodes.py`, `test_control_nodes.py`, `test_builtin_contract.py`) | `core.set`, `core.pick`, `core.if.field`, built-in set |
| `test_backoff_ending_between_clock_reads_still_runs_the_retry` | D1 |

---

## 5. Quality Gates

| Gate | Command | Result |
|---|---|---|
| Format | `uv run ruff format --check .` | PASS (192 files) |
| Lint | `uv run ruff check .` | PASS |
| Types | `uv run mypy .` | PASS (187 files) |
| Tests | `uv run pytest` with `AKASHA_TESTS_REQUIRE_DOCKER=1` | PASS |
| Infrastructure | PostgreSQL, Redis, Mailpit Testcontainers | PASS |
| Security | Phase 25 suites, isolation and default-deny incl. new routes, E2E secret canaries | PASS |
| E2E | `test_e2e_examples.py` | PASS (4/4) |
| Deployment / backup / restore | `tests/deployment` | PASS |
| Dependency audit | `uv export --frozen` → `pip-audit --no-deps` | PASS (no known vulnerabilities) |

---

## 6. Deployment and Backup/Restore Verification

`test_compose_lifecycle.py` against the real image and `docker-compose.yml`:
clean install → migrate → first admin, credential, executions → graceful
stop (exit 0) → restart → container recreation → backup → `down -v` →
fresh install → restore → verify (login, history, credentials, a new run)
→ worker killed mid-execution and resumed → PostgreSQL restarted under a
running API. Passed.

---

## 7. Architecture Review

| Boundary | Verdict |
|---|---|
| Dependency direction | Enforced by `test_architecture.py`; the new modules follow it (API → application/runtime/nodes; infrastructure behind the repository protocols) |
| Domain purity | No framework imports in `domain/`; the new read model lives in the application layer |
| API | Thin routers; DTOs (`ExecutionDetail`, `EventOut`, `NodeTypeOut`), never ORM rows; one error format |
| Runtime | One runtime for API workers, CLI and tests; D1 fixed at its source |
| Persistence | CAS saves, explicit transactions; listing does not load aggregates |
| Plugins / providers | Plugin nodes join the same registry, so the catalog covers them. Providers only behind `AIService` |
| Security | Default deny; foreign ids → 404; redaction on events, reports and logs; credential references only |
| Observability | Correlation and execution ids in logs; events persisted with the state change; metrics bounded |
| Configuration | `Settings` only; no `os.getenv` in business logic (one documented `setdefault` for LiteLLM's offline price map) |
| TODO/FIXME | none in `src/`. Deliberate shortcuts are marked `ponytail:` with their ceiling (§11) |

---

## 8. Important Design Decisions

1. **Data shaping is declarative.** `core.set` and `core.pick` extend the
   no-expressions rule of `core.if`/`core.switch` and the LLM templates.
   An expression engine stays out of V1 (architecture §30/§31).
2. **Examples are `workflow.json`, not ZIPs.** They are diffable and
   reviewable, and they map 1:1 onto both the draft API and the package
   format. The E2E test produces and imports the real packages.
3. **The event history is a cursor over `position`.** It is also the
   documented recovery path for a future WebSocket client (AGENTS §82).
4. **Cancel over HTTP reuses `ExecutionRuntime.cancel`.** Cross-process
   delivery is the existing heartbeat mechanism (Phase 23). No second
   cancel path exists.

---

## 9. Documentation Updated

- `docs/architecture.md`: §22 implemented V1 surface and what is not over HTTP; §37 built-in list and data shaping; §88 location and test of the examples
- `README.md`: built-in node list (`core.set`, `core.pick`, `core.llm`; `ai.llm` was wrong)
- `backend/README.md`: execution/node API table, example workflows
- `backend/examples/workflows/README.md`

---

## 10. Explicitly Deferred (not implemented)

- WebSocket live updates (frontend roadmap Phase 14). Polling the event
  history works now.
- `/api/v1/plugins`: plugin management stays in the operator CLI (trusted
  code, Phase 17 decision).
- Durable delays (`WAITING` with a persistent timer), loop nodes, an
  expression engine, credential key rotation, streamed backups, webhook
  replay protection (each marked `ponytail:` at its site).
- Frontend work of any kind.

---

## 11. Known Limitations

| Limitation | Impact | Mitigation / upgrade path |
|---|---|---|
| Login throttle and trigger scheduler are per API process | requires exactly one `api` replica (documented in `docs/deployment.md`) | Redis-backed throttle and a leader-elected scheduler for HA |
| No cost estimate for custom/self-hosted LLM endpoints | report shows usage with `cost_complete: false` | price table per configured provider |
| Event payloads/report tolerate redaction false negatives for unknown secret shapes | known values are masked exactly; unknown shapes rely on patterns | Phase 25 analysis unchanged |
| `core.smtp` blocking dialogue continues after cancellation (bounded by its timeout) | at-least-once side effects | documented in `deployment.md` §9 |
| POSIX semantics (0600 backups, graceful SIGTERM) verified on Linux only | Windows development hosts skip 3 tests | production image is Linux |
| Node catalog unpaginated | bounded by installed code | paginate if plugin catalogs grow |
| Phase 26 has no phase report in `docs/reports/backend/` | its results are recorded in `docs/deployment.md` and re-verified here (§6) | — |

---

## 12. Recommended Next Step

Start the frontend roadmap at its first phase. The backend contract it
needs now exists: node catalog, workflows/drafts/versions, execution
listing, event history, cancel and reports. Generate the frontend API types
from `/openapi.json` (AGENTS §21) and add an OpenAPI snapshot check to CI,
so frontend and backend contracts cannot diverge silently.
