# Phase 14 — Live Execution + WebSocket

Report date: 2026-10-04, updated the same day after a pre-Phase-15 follow-up (§11, "Follow-up"). Branch `main`. Base commit: `ec482ab` (frontend phase 13 — execution ui). Everything described here is uncommitted in the working tree (§23).

## 1. Executive Summary

**Supposed to achieve.** Roadmap §20 (`docs/roadmap-frontend.md`, "Phase 14 — Live Execution + WebSocket"):
- **Goal:** "Laufende Executions in Echtzeit beobachten" (watch running executions in real time).
- **Scope:** WebSocket client, connection lifecycle, reconnect, event handling, execution status updates, node status updates, live graph updates, connection status indicator.
- **Acceptance:** "Während einer laufenden Execution werden Änderungen ohne manuelles Refresh sichtbar." (during a running execution, changes become visible without a manual refresh).

The session prompt added:
- authentication boundary, subscription, backoff, event-to-UI mapping;
- authoritative-state reconciliation ("WebSocket events are updates; the backend persisted state remains authoritative");
- deterministic WebSocket tests (no sleeps);
- backend implementation of anything missing, tested and documented.

**Discovered at the start.** The backend had no WebSocket endpoint. `docs/architecture.md` §22 stated "Not over HTTP in V1: … live updates (WebSocket, §23; clients poll `/executions/{id}/events?after=`)". The phase therefore needed a backend gateway first.

**Actually implemented.**
- **Backend:** `WS /api/v1/executions/{execution_id}/live` (`backend/src/akasha/api/live.py`):
  - authentication and authorization in the first frame;
  - session re-check per poll;
  - an event stream that tails the persisted event log by `position`;
  - an explicit `end` frame.
- **Supporting backend changes:**
  - a new service method `ExecutionService.follow_events`;
  - a new setting `AKASHA_LIVE_POLL_SECONDS`;
  - 10 new PostgreSQL-backed integration tests through a real uvicorn server and a real WebSocket client (9 in the first version of this report, which wrongly said 10; the follow-up added the shutdown test).
- **Frontend:**
  - a validated WebSocket client with resume-by-position, dedup and exponential backoff (`frontend/src/api/live.ts`);
  - a hook that turns every subscription and event batch into a TanStack Query invalidation (`useLiveExecution.ts`);
  - a connection indicator and an activity list (`live.tsx`);
  - a node status table on the execution detail page;
  - a deterministic `FakeWebSocket` test harness;
  - 12 new unit/component tests and 1 new full-stack Playwright test against the real backend.

**Resulting state.**
- The detail page of an unfinished execution follows it live. Status, timestamps, the cancel button and node statuses update without a manual refresh.
- Dropped connections reconnect with backoff, resume after the last seen event position, and refetch the execution.
- Displayed state always comes from the HTTP resources. Events only trigger refetches.
- **Fully wired in tests only.** In the real app the frontend has no session token source yet (login UI is Phase 21). The live connection is therefore rejected (4401), and the page says "Live updates unavailable". The full-stack test injects the token, just as that suite already injects the HTTP `Authorization` header.

**Acceptance criteria.**
- The single roadmap criterion passes, verified against the real backend (§14).
- Scope item "Live Graph Updates" is only partial: node statuses are shown live as a table, not on a graph canvas (§14, §19).

**Phase Status:** COMPLETE WITH LIMITATIONS

---

## 2. Phase Objective

**Roadmap §20:**
- **Ziel:** "Laufende Executions in Echtzeit beobachten."
- **Scope:** WebSocket Client, Connection Lifecycle, Reconnect, Event Handling, Execution Status Updates, Node Status Updates, Live Graph Updates, Connection Status Indicator.
- **Acceptance Criteria:** "Während einer laufenden Execution werden Änderungen ohne manuelles Refresh sichtbar."
- **OpenCode prompt:**
  - "Nutze die vom Backend definierten Event-Typen."
  - "Keine eigene konkurrierende Event-Semantik erfinden."
  - "STOP nach Phase 14."

**Applicable rules:**
- `frontend/AGENTS.md` §38–41: live events are not the only truth; robustness against duplicates, ordering, staleness, auth expiry, server restart and malformed messages; runtime validation of events; reconciliation after reconnect.
- `frontend/AGENTS.md` §67: authenticated WebSocket; knowing an execution id must not be enough.
- `frontend/AGENTS.md` §102–104: order by event id/position; never show "Live" without a connection; mark stale data.
- `AGENTS.md` §82: WebSocket reliability; refetch authoritative state.
- `AGENTS.md` §35: secrets never in URLs.

**Explicitly not this phase's responsibility:**
- Execution inspector with graph, logs, event timeline and node inspector (Phase 15).
- Monitoring (Phase 16).
- Login UI and session storage (Phase 21).
- Frontend security hardening (Phase 23).

---

## 3. Scope Implemented

### Architecture
- **Live stream source:** a tail of the persisted event log (PostgreSQL), not a push from the in-process `EventPublisher` (§5, Decision 1).
- **Credential path:** the session token is carried in the first WebSocket frame (§5, Decision 2).
- **Event role:** events are notifications; the frontend reconciles by refetching (§5, Decision 3).

### Backend/API
- **`live.py`:**
  - `create_live_router()` registers `@router.websocket("/executions/{execution_id}/live")`.
  - It is mounted in `routes.create_api_v1_router()` on the outer `/api/v1` router, not under the `protected` router: the HTTP guards read request headers, which a browser WebSocket cannot send.
- **`SubscribeMessage`** (Pydantic, `extra="forbid"`): `type: Literal["subscribe"]`, `token: str` (default `""`, max 256), `after: int` (≥ 0).
- **`_stream()`:**
  - Waits at most `SUBSCRIBE_TIMEOUT_SECONDS = 10` for the subscribe frame.
  - Authenticates (`AuthenticationService.authenticate`) and authorizes (`AuthorizationService.require_execution`), then sends `subscribed`.
  - Loops: `follow_events` → `events` frame if any → `end` + close 1000 if the execution is terminal and nothing more is pending → wait up to `live_poll_seconds` on the socket → re-authenticate.
- **`_Closed`:** an internal exception carrying a close code and reason, used to leave the stream with a protocol close.
- **Close codes:** `CLOSE_PROTOCOL = 4400`, `CLOSE_UNAUTHENTICATED = 4401`, `CLOSE_NOT_FOUND = 4404`, `CLOSE_INTERNAL = 1011`.
- **`ExecutionService.follow_events`** (`application/executions.py`): returns `(Execution, Page[Event])`, reading the execution before its events in one unit of work. `list_events` now delegates to it (same behavior, same signature).
- **Setting** `Settings.live_poll_seconds` (`config.py`): default 0.5, `gt=0`, env `AKASHA_LIVE_POLL_SECONDS`.
- **Access-policy docstring** in `routes.py` lists the WebSocket route and how it is guarded.

### Frontend/UI
- **`src/api/live.ts`:**
  - `subscribeExecution(client, executionId, handlers)` returns an unsubscribe function.
  - Zod `serverFrameSchema` (discriminated union `subscribed | events | end`, reusing `executionEventSchema` and `executionStatusSchema`).
  - `LIVE_CLOSE`, the `LiveState` union, `LiveHandlers`, `RECONNECT_BASE_MS = 1000`, `RECONNECT_MAX_MS = 30000`, `reconnectDelay(attempt)`.
- **`src/api/client.ts`:** `ApiClient.baseUrl` and `ApiClient.getToken` changed from `private readonly` to `readonly`, so the live connection uses the same origin and the same token source.
- **`src/features/executions/useLiveExecution.ts`:**
  - `useLiveExecution(executionId, active)` returns `{ state, events }`.
  - `MAX_ACTIVITY = 200`.
  - `describeEvent(event)` maps the 15 backend execution/node event types to one sentence each; unknown types fall back to the raw `event_type`.
- **`src/features/executions/live.tsx`:** `LiveIndicator` (state → label, tone, optional hint; `role="status"`) and `LiveActivity` (ordered list of received events).
- **`src/features/executions/ExecutionDetailPage.tsx`:**
  - calls `useLiveExecution(execution.id, execution.finished_at === null)`;
  - renders `LiveIndicator`;
  - new "Nodes" card: a `Table` of `report.nodes` with label/id + type, status verbatim, attempts, started, finished;
  - "Live activity" card, shown while a subscription exists;
  - `ExecutionView` is rendered with `key={e.id}`.
- **`frontend/vite.config.ts`:** the `/api` proxy is `{ target, ws: true }` for both `server` and `preview`.

### Testing
- **Backend:** a live section in `backend/tests/integration/test_executions_api.py` with fixture `live_url` and helpers `_token`, `_frame`, `_close_code`, `_subscribe`, `_drain`. The `env` fixture now sets `live_poll_seconds=0.01`.
- **Frontend harness:**
  - `src/test/websocket.ts`: `FakeWebSocket`, `sockets()`, `lastSocket()`.
  - `src/test/setup.ts`: installs `FakeWebSocket` as the global `WebSocket` before every test.
- **Frontend tests:** `src/api/live.test.ts` (new); a "Live updates" describe block in `ExecutionsPage.test.tsx`.
- **Full stack:** new test in `frontend/tests/fullstack/workflows.spec.ts`.

---

## 4. Files Changed

| File | Change | Reason |
|---|---|---|
| `backend/src/akasha/api/live.py` | Created | WebSocket gateway: protocol, auth boundary, log tail, close codes |
| `backend/src/akasha/api/routes.py` | Modified | Mount `create_live_router()` on `/api/v1` outside the HTTP guards; access-policy docstring |
| `backend/src/akasha/application/executions.py` | Modified | `follow_events` (execution + events in one read); `list_events` delegates |
| `backend/src/akasha/config.py` | Modified | `live_poll_seconds` setting |
| `backend/tests/integration/test_executions_api.py` | Modified | 10 live tests (7 test functions, one parametrized ×4) plus the `LiveServer` / `live_server` / `live_url` real-socket fixtures; `env` uses a 0.01 s poll |
| `backend/src/akasha/bootstrap.py` | Modified (follow-up) | `Container.events` comment no longer claims the WebSocket gateway subscribes there |
| `backend/README.md` | Modified (follow-up) | `AKASHA_LIVE_POLL_SECONDS` in the settings table |
| `docs/deployment.md` | Modified (follow-up) | Reverse proxy must forward WebSocket upgrades (nginx example); graceful stop closes live WebSockets with 1012 |
| `frontend/src/api/live.ts` | Created | WebSocket client: validation, dedup, resume, backoff, state |
| `frontend/src/api/live.test.ts` | Created | 8 unit tests of the client |
| `frontend/src/api/client.ts` | Modified | `baseUrl` and `getToken` readable (one origin, one token source) |
| `frontend/src/features/executions/useLiveExecution.ts` | Created | Hook (subscription → query invalidation) and `describeEvent` |
| `frontend/src/features/executions/live.tsx` | Created | `LiveIndicator`, `LiveActivity` |
| `frontend/src/features/executions/ExecutionDetailPage.tsx` | Modified | Indicator, Nodes card, Live activity card, keyed view |
| `frontend/src/features/executions/ExecutionsPage.test.tsx` | Modified | 4 "Live updates" component tests |
| `frontend/src/test/websocket.ts` | Created | Deterministic `FakeWebSocket` |
| `frontend/src/test/setup.ts` | Modified | Install `FakeWebSocket` globally per test |
| `frontend/tests/fullstack/workflows.spec.ts` | Modified | Full-stack live test (real backend, dropped connection) |
| `frontend/vite.config.ts` | Modified | Proxy WebSocket upgrades under `/api` |
| `docs/architecture.md` | Modified | §22 endpoint list and removed "not over HTTP: live updates"; §23 implementation; §33 (SDK) note on per-attempt flushing |
| `frontend/README.md` | Modified | API layer bullet, dashboard note, new "Live execution" section |
| `docs/reports/frontend/phase-14-live-execution-websocket.md` | Created | This report |

Not changed:
- No new directories.
- No migrations.
- No dependency changes.
- No regenerated files. `frontend/src/api/openapi.json` is unchanged: FastAPI does not include WebSocket routes in OpenAPI, and no HTTP schema changed.

---

## 5. Architecture Changes

### Decision 1 — The live stream tails the persisted event log

**Decision.** The gateway reads `events WHERE execution_id = :id AND position > :after ORDER BY position` from PostgreSQL every `live_poll_seconds`, instead of subscribing to `Container.events` (`EventPublisher`).

**Context.**
- `EventPublisher` is in-process and at-most-once (`application/events.py` docstring).
- In the Compose deployment, executions run in a separate `worker` service (`AKASHA_WORKER_ENABLED: "false"` on the API, `docker-compose.yml`). An API-process subscriber would never see those events.
- The persisted log is authoritative and carries a monotonic `position`.

**Chosen approach.**
- `ExecutionService.follow_events` reads the execution and then the next page of events in one unit of work.
- The gateway sends the page, advances its cursor, and ends when the execution is terminal and the page has no more.

**Alternatives considered.**
- In-process `EventPublisher` subscription: misses worker events; loses events on crash.
- Redis pub/sub (Redis is already a dependency for the queue): still needs the log for catch-up; adds a second delivery path.
- PostgreSQL LISTEN/NOTIFY: same catch-up need; more moving parts.

**Reason.** One source (the log) serves both live delivery and catch-up, works across processes, and needed no new infrastructure.

**Consequences.**
- Cost scales with subscribers: one poll (≈3 indexed queries including the session check) per subscription per tick. Marked with a `ponytail:` comment in `live.py`.
- Latency is ≤ `live_poll_seconds` after commit.
- A push wake-up can be added later without changing the protocol.

### Decision 2 — Authentication in the first frame

**Decision.** The client sends `{"type":"subscribe","token":…,"after":…}` as its first frame. There is no header and no query-parameter authentication.

**Context.**
- The API uses `Authorization: Bearer` only (`api/auth.py`), and browsers cannot set headers on a WebSocket.
- Tokens must not appear in URLs (`AGENTS.md` §35; the access log records paths).

**Chosen approach.**
- First-frame authentication with a 10 s deadline.
- The same `AuthenticationService.authenticate` and `AuthorizationService.require_execution` used by HTTP.
- Session re-check on every poll tick.

**Alternatives considered.**
- `?token=` query parameter: leaks to logs.
- Token smuggled in `Sec-WebSocket-Protocol`: non-standard, ends up in proxy logs too.
- Cookie session: would introduce ambient credentials and a CSRF/CSWSH surface the API currently does not have.

**Reason.** It is the only option that keeps secrets out of URLs and adds no ambient credential.

**Consequences.**
- Any WebSocket client (frontend, Playwright) must send the token in the frame.
- The frontend needs a token source (`ApiClient.getToken`), which Phase 21 provides.

### Decision 3 — Events are notifications, the HTTP resources are the state

**Decision.** The frontend never derives execution or node status from event payloads. Every `subscribed` frame and every `events` frame invalidates `queryKeys.executions.detail(id)`, which by prefix covers the detail, the report and the events queries.

**Context.**
- Session prompt and `frontend/AGENTS.md` §41.
- The Phase 13 handoff: "After an execution event, refetch the execution rather than recomputing `cancellable` from the event."

**Chosen approach.**
- Invalidate on each frame (the server batches one frame per tick).
- `end` additionally invalidates `executions.overview()`.

**Alternatives considered.** Optimistic status from `execution.*` and `node.*` events with later reconciliation. Rejected: it duplicates the backend state machine in the client and can disagree with it.

**Consequences.**
- Each events frame causes up to two refetches (detail, report).
- Correctness after missed events relies only on the refetch, which is what the reconnect test verifies.

### Decision 4 — A live view starts once and ends with the server

**Decision.** `useLiveExecution` captures `active` once with `useState(active)`. A started subscription runs until the server sends `end`, the session or execution is rejected, or the page unmounts.

**Context.** The first implementation tied the connection to `finished_at === null`. A refetch that saw the terminal state closed the socket before `end` arrived (§10).

**Consequences.** The final events of a run are always shown. A run that was finished when the page opened never connects.

---

## 6. Domain / Data Model Changes

- **Domain:** no domain entities, enums, value objects, tables, indexes, constraints or migrations changed.
- **Application read model:** new return shape `tuple[Execution, Page[Event]]` from `ExecutionService.follow_events`. `list_events` is unchanged externally.
- **New API-layer model:** `api.live.SubscribeMessage` (the client's first frame).
- **Reused model:** `api.executions.EventOut` (unchanged) is reused for streamed events.
- **Frontend types:**
  - `LiveState`: UI connection state only, never an execution state.
  - `LiveHandlers`.
  - The Zod `serverFrameSchema` (module-private).

No persisted-data compatibility implications.

---

## 7. API / Contract Changes

### New: `WS /api/v1/executions/{execution_id}/live`

- **Purpose:** live events of one execution.
- **Client → server (first and only frame):** `{"type": "subscribe", "token": "<session token>", "after": <int ≥ 0>}`.
  - Extra keys are refused.
  - A missing `token` defaults to `""`, which is treated as unauthenticated.
  - Further client frames after subscribing are a protocol error (4400).
- **Server → client:**
  - `{"type": "subscribed", "execution_id": "...", "after": n}`
  - `{"type": "events", "items": [EventOut, ...]}`: ascending `position`, each stored event with `position > after` exactly once per connection, at most 100 per frame.
  - `{"type": "end", "status": "<completed|failed|cancelled|timeout>"}`, followed by close 1000.
- **Close codes:**

  | Code | Meaning | Client action |
  |---|---|---|
  | 4400 | Subscribe frame missing (10 s), not text, not JSON, wrong shape, or an unexpected client frame | Stop |
  | 4401 | Missing, invalid or expired session; also logout or disable mid-stream | Stop |
  | 4404 | Unknown execution or one the caller may not see (same answer) | Stop |
  | 1011 | Internal or configuration error (e.g. no database) | Reconnect |
  | other | Transient (network, server restart) | Reconnect |

- **Compatibility:**
  - New endpoint; no existing endpoint changed.
  - Not in OpenAPI (FastAPI omits WebSocket routes).
  - The contract is documented in `live.py`'s module docstring and `docs/architecture.md` §23, and mirrored by the Zod schema in `frontend/src/api/live.ts`.
  - The `items` shape is the OpenAPI-covered `EventOut`, so the existing frontend contract test (`contract.node.test.ts`) indirectly guards the event item schema.

### Changed

- **Architecture §22:** endpoint list adds the WebSocket. The "clients poll `/executions/{id}/events?after=`" statement is removed.
- **Settings:** `AKASHA_LIVE_POLL_SECONDS` (new, optional).

### Frontend API layer

- `ApiClient.baseUrl` and `ApiClient.getToken` are now public `readonly` (previously `private readonly`). This is additive.

---

## 8. Implementation Details

### Backend: `live.py`
- **Lifecycle:**
  - `live_execution` accepts the socket, then runs `_stream`.
  - `WebSocketDisconnect` → logged `live_disconnected`.
  - `_Closed` → logged `live_closed` with `close_code`, then `_close(code, reason)`.
  - Any other `Exception` → `logger.exception("live_failed")` and `_close(1011)`.
  - `_close` closes only while both sides are still `CONNECTED` (follow-up), so an error after a disconnect does not produce a second error.
  - On server shutdown, uvicorn closes open WebSockets with 1012; the loop's pending receive returns the disconnect and the handler ends (tested in the follow-up). This catch is deliberately broad and commented: one connection's bug must not leak a trace.
- **Configuration check:** missing `auth`, `authorization` or `executions` in the container → close 1011 ("Live updates are not configured."). It fails closed.
- **Frame reading (`_receive_text`):** uses `websocket.receive()`. A disconnect message raises `WebSocketDisconnect`; a non-text frame raises `_Closed(4400)`.
- **Waiting between polls:**
  - The loop waits on `_receive_text` under `asyncio.timeout(live_poll_seconds)` rather than `asyncio.sleep`. A client that leaves is noticed immediately (the disconnect message arrives) instead of after the execution ends.
  - A timeout is the normal path. Any received frame means the client broke the protocol (4400).
- **End condition:** `follow_events` reads the execution before its events, in one unit of work. A terminal status is committed in the same transaction as its final events, so if the execution read is terminal, a subsequent events read includes every event. `end` is sent only when the execution is terminal and `page.has_more` is false.
- **Pagination:** `limit=MAX_PAGE_SIZE` (100). While `has_more`, the loop continues without waiting.
- **Serialization:** `EventOut.from_domain(e).model_dump(mode="json")`. Payloads pass through `redact()`, as on the HTTP history.
- **Logging:** `live_subscribed` (execution_id, user_id), `live_closed` (execution_id, close_code), `live_disconnected`, `live_failed`. The token, payloads and frames are never logged. WebSocket connections bypass the HTTP access-log middleware; these events replace it.

### Frontend: `subscribeExecution` (`api/live.ts`)
- **URL:** `new URL(`${client.baseUrl}/executions/${encodeURIComponent(id)}/live`, window.location.href)` with `http→ws` / `https→wss`. Same origin as HTTP requests.
- **On open:** sends `{type:'subscribe', token: client.getToken?.() ?? '', after}`.
- **State held:**
  - `after`: highest delivered position;
  - `attempt`: backoff counter;
  - `socket`, `timer`;
  - `stopped`.
- **Frame handling:**
  - Unparseable (non-string data, invalid JSON, schema mismatch) → `ws.close()`, then the reconnect path, whose resubscribe reconciles.
  - `subscribed` → `attempt = 0`, `onState('live')`, `onSubscribed()`.
  - `events` → filter sequentially: keep `e.position > after`, advancing `after`. This drops duplicates and stale or out-of-order-older events. Calls `onEvents(fresh)` if any remain.
  - `end` → `stop('ended')`, `onEnd(status)`.
- **On close** (ignored if `stopped` or for a superseded socket):
  - 4401 → `stop('unauthenticated')`;
  - 4404 or 4400 → `stop('unavailable')`;
  - otherwise `onState('reconnecting')` and `setTimeout(connect, reconnectDelay(attempt++))`.
- **Backoff:** `reconnectDelay(n) = min(30000, 1000·2^n)`, without jitter (`ponytail:` comment).

### Frontend: `useLiveExecution`
- `const [follow] = useState(active)`. The effect depends on `[client, queryClient, executionId, follow]`. Its cleanup is the unsubscribe function (closes the socket, cancels the timer).
- `reconcile()` = `queryClient.invalidateQueries({ queryKey: queryKeys.executions.detail(executionId) })`. With the default `cancelRefetch: true`, a newer invalidation supersedes an in-flight refetch.
- Activity state: `setEvents(seen => [...seen, ...fresh].slice(-200))`.
- `ExecutionView` is keyed by execution id, so state cannot carry over when the route parameter changes.

### State separation
- **Server state** (execution, report, events): TanStack Query, unchanged.
- **Live connection state and activity list:** local UI state in the hook. They are never written into the query cache.

---

## 9. Findings

### Finding 1: The backend had no WebSocket endpoint
- **Impact:** HIGH (the phase could not be done frontend-only).
- **Resolution:** FIXED.
- **Notes:** This was anticipated by the Phase 13 handoff. Implemented as described in §5.

### Finding 2: The in-process `EventPublisher` cannot feed a live gateway in the documented deployment
- **Impact:** MEDIUM.
- **Resolution:** WORKAROUND (by design: log tail); stale comment FIXED in the follow-up.
- **Notes:**
  - `Container.events` carries the comment "consumers (WebSocket gateway, metrics) subscribe here", and its `publish` docstring (`application/events.py`) describes live-update consumers. Neither holds once workers run in a separate process (`docker-compose.yml`).
  - That comment in `bootstrap.py` was misleading. A check also showed that nothing in production subscribes to `Container.events` at all. The comment was corrected in the follow-up (§11).

### Finding 3: The frontend has no session token source
- **Impact:** HIGH for real-app usefulness, none for correctness.
- **Resolution:** DEFERRED (Phase 21).
- **Notes:**
  - `App.tsx` constructs `new ApiClient()` without `getToken`. HTTP works in the full-stack suite only because Playwright injects the header.
  - In the real app the live connection therefore closes 4401 and the page says "Live updates unavailable".

### Finding 4: `events.position` is an identity column, not a per-execution gap-free sequence
- **Impact:** LOW.
- **Resolution:** OPEN (documented).
- **Notes:**
  - A tail by `position > after` could skip an event whose transaction commits after a higher-numbered one.
  - Within one execution there is a single writer at a time (runtime single-writer rule), so this does not occur in one execution's stream. The `end`-triggered refetch covers the state in any case.
  - Gap detection on the client is impossible because positions are global.

### Finding 5: Cancelling a queued execution emits more than one event
- **Impact:** LOW.
- **Resolution:** FIXED in the test.
- **Notes:**
  - The full-stack test first assumed 2 activity entries (created, cancelled) and saw 5 (node cancellations included).
  - The test now compares against the stored event history instead of a guessed count.

### Finding 6: WebSocket routes cannot sit under the HTTP-guarded router
- **Impact:** LOW.
- **Resolution:** FIXED.
- **Notes:** `current_identity` and `authorize_path` read the `Authorization` header of an HTTP `Request`. The live router is mounted outside them and authenticates in its first frame. `routes.py`'s access-policy docstring records this.

---

## 10. Bugs / Problems Found

### Bug 1: Indicator stuck on "Live" after the execution finished (introduced during this phase, fixed before completion)
- **Problem:** the component test "follows a running execution…" expected "Live updates finished" after the `end` frame, but found "Live".
- **Root cause:**
  - `useLiveExecution` depended on `active = finished_at === null`.
  - The `events` frame triggered a refetch that returned `completed`, so `active` became false and the effect cleanup closed the socket.
  - The following `end` frame went to a stopped connection, and the state stayed `live`.
- **Fix:** follow decided once per execution (`useState(active)`); the server's `end` closes the stream.
- **Regression test:** `ExecutionsPage.test.tsx` › "Live updates" › "follows a running execution and shows the backend state it refetches".
- **Affected area:** `features/executions/useLiveExecution.ts`.

### Tooling and compile problems during the phase (all fixed)
- **TS2304 `window`:** a first `ApiClient.webSocketUrl()` used `window`. `client.ts` is also compiled under the Node-typed config (`client.node.test.ts`). URL building moved to `live.ts`.
- **TS4111 index-signature access** on event payloads: changed to `payload['attempt']` etc.
- **TS1294** (`erasableSyntaxOnly`): the parameter property in `FakeWebSocket` was replaced by an explicit field.
- **ESLint `react-refresh/only-export-components`:** the hook and `describeEvent` moved out of `live.tsx` into `useLiveExecution.ts`.
- **`FakeWebSocket` URL:** `new WebSocket(url)` was given a `URL` object, which the fake stored as an object. Now `url.href` is passed.

### Test-code mistakes fixed
- **Default parameter:** `follow(undefined)` triggered the default token. Changed to `follow(null)`.
- **Ambiguous query:** the component tests queried `getByRole('status')`, but the page has several status regions. They now query the indicator by its text.

**Pre-existing bugs discovered:** none.

---

## 11. Fixes Applied

| Symptom | Root cause | Implementation | Verification |
|---|---|---|---|
| "Live" shown after the run ended | Socket closed by an effect cleanup before `end` | `useState(active)` follow-once | Component test passes; full-stack test reaches "Live updates finished" |
| A missing token answered 4400 ("malformed") | `token` had `min_length=1` | `token` defaults to `""`; an empty token raises `AuthenticationRequiredError` → 4401 | `test_live_rejects_invalid_sessions_and_hidden_executions` covers `""`; frontend shows "unavailable" for 4401 |
| `tsc -b` failed (`window`, TS4111, TS1294) | See §10 | See §10 | `npx tsc -b` passes |
| `eslint` failed (react-refresh) | Hook and components in one `.tsx` | Split into `useLiveExecution.ts` and `live.tsx` | `npm run lint` passes |
| Full-stack activity count off (5 ≠ 2) | Guessed count | Compare with `GET /executions/{id}/events` | Full-stack 12/12 |

### Follow-up before Phase 15 (2026-10-04)

These items were reviewed after the first version of this report and fixed before Phase 15:

| Item | Before | Implementation | Verification |
|---|---|---|---|
| Shutdown with an open stream unverified (`AGENTS.md` §48) | No test stopped the server while a viewer was attached | New test `test_live_server_shutdown_closes_open_streams_for_reconnect`. The fixture became `LiveServer` (url, server, task, `stop()`) with `live_url` derived from it | Passes: `server.stop()` returns within the 10 s hang guard, and the idle client receives close **1012**, which the frontend treats as transient (reconnect). No production change was needed: uvicorn closes WebSockets on shutdown and the stream loop ends on the resulting disconnect |
| Double close in error paths | `_Closed` and unexpected-error handlers called `websocket.close()` unconditionally; with the socket already closed by either side that raises a second error that buries the first in logs | `live._close()` closes only when both `client_state` and `application_state` are `CONNECTED` (`fastapi.websockets.WebSocketState`) | Existing 4400/4401/4404 tests still pass, so the normal close path is unchanged. The already-closed branch has no dedicated test: it cannot be triggered deterministically from a client |
| Stale `Container.events` comment | Named the WebSocket gateway as a subscriber | Reworded: in-process, at-most-once; the live WebSocket reads the persisted log because workers run in other processes | Review |
| Deployment docs | No word on WebSocket proxying or `AKASHA_LIVE_POLL_SECONDS` | `docs/deployment.md` §2: upgrade forwarding for `/api/`, nginx example, idle-timeout note; §6: graceful stop closes live WebSockets with 1012; `backend/README.md` settings table row | Checked uvicorn's defaults: `ws_ping_interval=20.0`, so an idle stream carries a ping every 20 s and nginx's default 60 s read timeout keeps it open |
| Report test count | §1, §4, §12 said 10 backend live tests; there were 9 | Corrected; now 10 with the shutdown test | `pytest -k live`: 10 selected, 10 passed |

---

## 12. Testing & Verification

### Unit Tests (frontend, Vitest, `src/api/live.test.ts`): 8 tests, all pass
1. Same-origin `ws://` URL; token in the subscribe frame, not the URL; `connecting → live`; `onSubscribed` called once.
2. Duplicates (same position) and stale (lower position) events dropped across frames; delivery `[3, 5, 7]`.
3. `end` → `ended`, `onEnd('completed')`, no reconnect after close and timers.
4. Close 1006 → `reconnecting`; no new socket before `reconnectDelay(0)`; new socket at exactly 1000 ms; resubscribe with `after: 4`; second `onSubscribed` (reconcile); later duplicate 4 dropped.
5. Backoff 1000/2000/4000/8000, cap 30000, reset to the base delay after a successful subscribe.
6. No token → frame `token: ''`; 4401 → `unauthenticated`; 4404 → `unavailable`; neither reconnects.
7. Non-JSON frame → client closes; a schema-invalid `events` frame delivers nothing; reconnect, then resubscribe.
8. After unsubscribe: socket closed, later frames ignored, no reconnect.

Timers: `vi.useFakeTimers()`. Sockets: `FakeWebSocket`.

### Component Tests (frontend, `ExecutionsPage.test.tsx` › "Live updates"): 4 tests, all pass
1. Running execution: "Connecting" → subscribe frame `{token:'', after:0}` → "Live".
   - The detail read count increases after `subscribed` (reconciliation).
   - Backend switched to `completed`, then an events frame: the activity shows "Node fetch completed (attempt 1)" and "Execution completed", and the Details card shows "Completed" from the refetch.
   - `end` → "Live updates finished".
2. Disconnect (1006) → "Reconnecting" + "may be out of date", and "Live" is absent.
   - The backend finishes while disconnected (no events delivered).
   - After `reconnectDelay(0)` the new socket subscribes with `after: 3`.
   - On `subscribed`, Details shows "Completed" (pure reconciliation, no event) and the indicator returns to "Live".
   - Uses `vi.useFakeTimers({ shouldAdvanceTime: true })`.
3. Close 4401 → "Live updates unavailable" + "Use Refresh"; only one socket ever created.
4. A finished execution creates no socket and shows no indicator.

### Integration / API Tests (backend, real PostgreSQL via Testcontainers, real uvicorn on a pre-bound socket, real `websockets` client): 10 tests, all pass
1. `test_live_streams_a_running_execution_until_it_ends`
   - Subscribed to a QUEUED execution: `execution.created`.
   - `runtime.run`, then drain.
   - The streamed items are exactly equal to `GET /events` items; `end {status: completed}`; close 1000.
2. `test_live_resumes_after_the_last_seen_position`: `after` = 2nd event → exactly `items[2:]`; `after` = last → `([], end)`.
3. `test_live_never_streams_secrets`: a failing node whose error contains `sk-live-…` → `node.failed` streamed, secret absent; `end {status: failed}`.
4. `test_live_rejects_invalid_sessions_and_hidden_executions`: `"not-a-session"` and `""` → 4401; another member's execution → 4404; unknown id → 4404.
5. `test_live_rejects_a_malformed_subscribe_frame` ×4: `"not json"`, `{"type":"hello",…}`, `after: -1`, binary frame → 4400.
6. `test_live_closes_when_the_session_ends_mid_stream`: `POST /auth/logout` with the stream's token → next frame is close 4401.
7. `test_live_server_shutdown_closes_open_streams_for_reconnect` (follow-up): an idle subscriber on a QUEUED execution; server stop → returns promptly, client receives 1012.

Waits use `asyncio.timeout(10)` purely as a hang guard around `recv()`. There are no sleeps, and the server needs no readiness wait because the listening socket is bound before `serve()`.

### Infrastructure Tests
- PostgreSQL (Testcontainers, `postgres:17.2` per existing fixtures) is exercised by the backend tests above. No Redis involvement.
- Full backend suite (after the follow-up): 1926 passed, 6 skipped. The 6 skips are pre-existing.

### E2E Tests
- **Mocked (`npx playwright test`):** 23/23 pass. No new mocked E2E test; the existing ones were run to check nothing regressed.
- **Full stack (`npx playwright test -c playwright.fullstack.config.ts`):** 12/12 pass, including the new "a run is followed live, across a dropped connection, to its end":
  - production bundle, `vite preview` proxy with `ws: true`, real backend and PostgreSQL;
  - `page.routeWebSocket` adds the token to the subscribe frame and passes everything else through;
  - "Live" and "Execution created" are shown;
  - the connection is closed with 1012 → "Reconnecting";
  - `POST /cancel` via the API while disconnected;
  - the page shows "Live updates finished", "Execution cancelled" and "Cancelled";
  - two subscribe frames were sent, the second with `after > 0`;
  - the activity item count equals the stored event count.

### Security Tests
Covered by integration tests 3–6 (redaction, invalid/empty session, cross-user access, malformed input, revocation) and by frontend unit test 1 (token not in the URL). No dedicated security scan was run.

### Accessibility Tests
No automated accessibility audit was run. Manual design points (not tested by a tool):
- the indicator uses `role="status"` and a text label, so tone is not the only signal;
- the activity is an `<ol>` with `aria-label`;
- the Nodes table uses the existing accessible `Table` with a caption.

### Performance Tests
None run.

---

## 13. Quality Gates

All results are from runs after the last code change.

| Quality Gate | Result | Notes |
|---|---|---|
| Formatter (frontend) | PASS | `npm run format:check` |
| Linter (frontend) | PASS | `npm run lint`; failed once (react-refresh, §10), fixed |
| Type Checker (frontend) | PASS | `npx tsc -b`; failed three times during the phase (§10), fixed |
| Unit/Component Tests (frontend) | PASS | `npx vitest run`: 28 files, 353 tests |
| Build (frontend) | PASS | `npm run build` |
| E2E mocked | PASS | 23/23 |
| E2E full stack | PASS | 12/12; the new test failed once on a wrong expected count (§9 Finding 5), fixed; re-run 12/12 after the follow-up's `live.py` change |
| Formatter (backend) | PASS | `uv run ruff format --check .` (197 files) |
| Linter (backend) | PASS | `uv run ruff check .`; the follow-up first failed I001 (import order after adding `WebSocketState`), fixed |
| Type Checker (backend) | PASS | `uv run mypy .` (192 source files) |
| Backend tests (unit/integration/API) | PASS | `uv run pytest` after the follow-up: 1926 passed, 6 skipped (1925 passed before it). One pre-existing warning: Starlette's `httpx` deprecation |
| Contract | PASS | `tests/test_openapi_contract.py` and frontend `contract.node.test.ts` pass; OpenAPI snapshot unchanged |
| Security Checks | N/A | No dedicated scanner; security behavior covered by tests (§12) |
| Accessibility | N/A | No automated audit run |

---

## 14. Acceptance Criteria Verification

| Acceptance Criterion | Status | Evidence |
|---|---|---|
| Changes during a running execution become visible without a manual refresh (roadmap §20) | PASS | Full-stack test: the cancellation done through the API (not the page) appears on the open page as "Cancelled" and "Execution cancelled" without reload. Component test 1: the status changes from Running to Completed through the live path. Backend test 1: every stored event is streamed. **Caveat:** in the real app without Phase 21 token wiring the connection is refused; see the scope table and §20. |

The scope items are not acceptance criteria, but are evaluated for completeness:

| Scope item | Status | Evidence / gap |
|---|---|---|
| WebSocket client | PASS | `api/live.ts`, unit tests |
| Connection lifecycle | PASS | States `connecting/live/reconnecting/ended/unauthenticated/unavailable`; unsubscribe on unmount (unit test 8) |
| Reconnect | PASS | Backoff and resume (unit tests 4–5, component test 2, full-stack test) |
| Event handling | PASS | Validation, dedup, stale dropping, unknown types (unit tests 2, 7; `describeEvent` default) |
| Execution status updates | PASS | Component test 1, full-stack test |
| Node status updates | PASS | "Nodes" card from the report, refetched on every events frame. Shown verbatim (`completed`, `failed`, …) without labels or tones. Not asserted by a dedicated live test: the refetch path is the same invalidation as the execution detail |
| Live graph updates | PARTIAL | No graph canvas on the execution page; node status is a table. The execution graph view belongs to Phase 15 ("Execution Inspector → Graph"). Not blocking the acceptance criterion |
| Connection status indicator | PASS | `LiveIndicator`; component tests 1–3; never "Live" while disconnected (component test 2) |
| (session) Authentication boundary | PASS | Backend tests 4, 6; token not in the URL (unit test 1) |
| (session) Authoritative-state reconciliation | PASS | Component test 2 (state updated with no event delivered), full-stack test |
| (session) Usable in the real app | PARTIAL | Needs the Phase 21 token source (§9 Finding 3) |

The phase status COMPLETE WITH LIMITATIONS follows from the two PARTIAL rows; neither blocks the acceptance criterion.

---

## 15. Security Considerations

- **Authentication:** the first frame carries the session token, checked by the same `AuthenticationService.authenticate` as HTTP. A missing or empty token → 4401. The session is re-checked every poll, so logout, expiry and disabled users end the stream (tested for logout).
- **Authorization:** `AuthorizationService.require_execution`, the same rule as HTTP. Unknown and hidden executions both answer 4404, so a known execution id is not enough to subscribe (`frontend/AGENTS.md` §67).
- **Secrets:**
  - The token is never in the URL (unit test 1), never logged (`live.py` logs ids only), and not stored by the frontend (read from `getToken` per connection).
  - Event payloads are redacted (`EventOut.from_domain` → `redact`); tested with a secret.
- **CSRF / cross-site WebSocket hijacking:** there are no cookies or other ambient credentials, so a foreign origin cannot subscribe without a token. No Origin check was added.
- **Input validation:**
  - The subscribe frame is strictly validated (`extra="forbid"`, token ≤ 256 characters, `after ≥ 0`, text only) with a 10 s deadline.
  - Client frames after subscribing are rejected (4400).
  - The maximum message size is uvicorn's default (`ws_max_size`, 16 MiB); not lowered (§20).
- **Output to the browser:**
  - Every server frame is Zod-validated.
  - Unknown event types are displayed as text (React-escaped).
  - No `innerHTML` or `dangerouslySetInnerHTML`.
- **Error disclosure:** unexpected errors close 1011 with "Internal error."; the trace stays in server logs (`live_failed`).
- **Resource exhaustion:** no per-user or global cap on concurrent subscriptions exists (§20, §21).

---

## 16. Performance Considerations

- **Server load:** per subscription per `live_poll_seconds` (0.5 s): `follow_events` (execution load plus the events query, indexed by `ix_events_execution_position`) and one session lookup. No measurements were taken.
- **Batching:** up to 100 events per frame; continued without waiting while `has_more`.
- **Client:** each events frame triggers one invalidation, i.e. refetches of the detail and the report (and the events query if mounted). The rate is bounded by the server tick, at most about 2 frames/s.
- **Memory:** the activity list is capped at 200 entries in the client.
- **Initial subscribe:** `after = 0`, so the full history is streamed in pages of 100. Large executions mean many frames on first open, but each frame is bounded.
- **Measurements:** none. No benchmark or load test was performed.

---

## 17. Documentation Changes

| File | Reason | What |
|---|---|---|
| `docs/architecture.md` §22 | API surface changed | Added `/api/v1/executions/{id}/live (WebSocket)`; removed "live updates … clients poll" from the "Not over HTTP in V1" note |
| `docs/architecture.md` §23 | New implementation | Protocol, auth boundary, close codes, log-tail design, reconciliation rule, known ceilings |
| `docs/architecture.md` §33 (SDK implementation) | Stale "Revisit when a live-update consumer exists" | Now states the gateway streams from the log, so per-attempt flushing stays |
| `backend/src/akasha/api/live.py` docstring | Contract next to code | Full protocol and close codes |
| `backend/src/akasha/api/routes.py` docstring | Access policy | WebSocket route and how it is guarded |
| `frontend/README.md` | Frontend behavior | API layer bullet; dashboard note; new "Live execution" section (reconciliation, dedup, backoff, states, token, test harness) |
| `docs/reports/frontend/phase-14-live-execution-websocket.md` | Phase report | This document |

Follow-up additions:
- `docs/deployment.md` §2 and §6: WebSocket proxying and graceful-stop behavior.
- `backend/README.md`: `AKASHA_LIVE_POLL_SECONDS`.
- `backend/src/akasha/bootstrap.py`: `Container.events` comment.

**Should be updated in a future phase:** nothing known from this phase.

---

## 18. Dependencies / Infrastructure Changes

- **New dependencies:** none.
  - Backend tests import `websockets` (17.1) and `uvicorn`. Both were already installed through the production dependency `uvicorn[standard]`. `websockets` is not declared as a direct dev dependency; it is used only through that transitive pin in `uv.lock`.
- **Removed or upgraded dependencies:** none.
- **Configuration:** `AKASHA_LIVE_POLL_SECONDS` (optional, default 0.5).
- **Dev/preview proxy:** `frontend/vite.config.ts` `/api` → `{ target, ws: true }`. Without it, dev and preview (and the full-stack suite) cannot reach the WebSocket.
- **Docker / Compose:** unchanged.
- **Database / Redis:** unchanged. No migration.
- **Test infrastructure:**
  - The global `FakeWebSocket` is installed in `src/test/setup.ts`, so no Vitest test can open a real WebSocket.
  - The backend `env` fixture uses `live_poll_seconds=0.01`.

---

## 19. Deferred Work

| What | Why deferred | Intended phase | Blocker | Affects this phase? |
|---|---|---|---|---|
| Session token source (`getToken`) in the real app | Login UI and session storage are Phase 21 | 21 | Phase 21 | Live updates are refused in the real app until then; acceptance verified with injected token |
| Live status on a graph canvas | The execution graph view belongs to the Execution Inspector | 15 | — | Scope item "Live Graph Updates" PARTIAL |
| Event timeline / full history view for finished runs | Execution Inspector "Events" | 15 | — | No |
| Live updates on lists and dashboard | Not required by the criterion; lists keep Refresh | 16 or later | — | No |
| Active-subscription metric | Monitoring | 16 | — | No |
| Push wake-up (LISTEN/NOTIFY or Redis pub/sub) | Polling suffices at current scale; no measurement | When measured | — | No |
| Reconnect jitter | Few clients per server in self-hosted use | 24 (performance/UX hardening) or when needed | — | No |
| Subscription limits (per user/global), smaller `ws_max_size` | Not required yet | 23 (frontend security hardening) / backend hardening | — | No |

---

## 20. Known Limitations

- **Real app (no token source):** live updates are unavailable in the running app until Phase 21; the page says "Live updates unavailable … Use Refresh". Refresh still works.
- **Latency:** up to `live_poll_seconds` (0.5 s) after commit, plus the runtime's own save cadence (per node outcome).
- **Server cost** grows linearly with open detail pages of unfinished executions.
- **Theoretical event skip** under concurrent writers to one execution (§9 Finding 4). The state is still correct after `end` because of the refetch.
- **Message size:** the server accepts incoming WebSocket messages up to uvicorn's default `ws_max_size` before validating the first frame.
- **No connection limit** per user or per process.
- **Initial history:** opening the page of a long-running execution streams its whole history first (paged, ≤100 per frame). The activity keeps only the last 200 entries.
- **"Reconnecting" can outlive the run:** if HTTP works but WebSockets do not (e.g. a proxy without upgrade support), the indicator stays "Reconnecting" with backoff up to 30 s, even after a manual Refresh shows a finished run. It says "may be out of date", which is conservative, not false.
- **Node status text** in the Nodes table is the raw backend value (`completed`, `failed`, …) without labels or tones.
- **Live view starts only on unfinished runs:** a run that is already finished when the page opens shows no activity list (history is the Phase 15 Events view).

---

## 21. Open Issues

1. **No token source in the frontend.**
   - **Impact:** the live feature is not usable in the shipped app.
   - **Severity:** MEDIUM. **Area:** frontend auth.
   - **Next action:** Phase 21 must pass `getToken` to `new ApiClient(...)` in `App.tsx`; `live.ts` uses it automatically. Then the full-stack spec's `routeWebSocket` token injection can be removed.
   - **Target:** 21.
2. **No subscription limits.**
   - **Impact:** one user can open many connections, each polling the database.
   - **Severity:** LOW (authenticated users only). **Area:** `api/live.py`.
   - **Next action:** cap per user/process if measured or required.
   - **Target:** 23 or backend hardening.

---

## 22. Architectural / Product Decisions

- **Live delivery reads the event log, not the in-process publisher.** The reason is that workers run in other processes; do not "optimize" by switching to `Container.events` without a cross-process transport and log catch-up.
- **The token travels in the first frame.** Never move it into the URL. A header is impossible for browsers, and cookies would add CSRF surface.
- **Close codes 4400/4401/4404 are terminal for clients; everything else is transient.** The frontend's retry policy depends on this mapping.
- **`end` is emitted only when the execution is terminal and every event was sent.** This relies on `follow_events` reading the execution before its events; keep that order.
- **The frontend derives no status from events.** Events invalidate queries; the HTTP resources are what is shown. `LiveState` is connection state, not execution state.
- **A live view is started once per mounted execution and runs until the server's `end`.** Tying it to refetched data loses the final events (§10).
- **One `ApiClient` provides both origin and token to the WebSocket**, so Phase 21 needs to wire the token in one place only.
- **The global `FakeWebSocket` in Vitest setup is intentional:** it makes unexpected real network use impossible in unit and component tests.

---

## 23. Repository / Git State

- **Branch:** `main`. HEAD: `ec482ab frontend phase 13 - execution ui`. Working tree clean at phase start (`git status` showed only Phase 13 work, which had been committed as `ec482ab` by then).
- **Modified (15 tracked files):**
  - `backend/README.md` (follow-up)
  - `backend/src/akasha/api/routes.py`
  - `backend/src/akasha/application/executions.py`
  - `backend/src/akasha/bootstrap.py` (follow-up)
  - `backend/src/akasha/config.py`
  - `backend/tests/integration/test_executions_api.py`
  - `docs/architecture.md`
  - `docs/deployment.md` (follow-up)
  - `frontend/README.md`
  - `frontend/src/api/client.ts`
  - `frontend/src/features/executions/ExecutionDetailPage.tsx`
  - `frontend/src/features/executions/ExecutionsPage.test.tsx`
  - `frontend/src/test/setup.ts`
  - `frontend/tests/fullstack/workflows.spec.ts`
  - `frontend/vite.config.ts`
  - (`git diff --stat`: 648 insertions, 22 deletions.)
- **Untracked (new):**
  - `backend/src/akasha/api/live.py`
  - `frontend/src/api/live.ts`
  - `frontend/src/api/live.test.ts`
  - `frontend/src/features/executions/live.tsx`
  - `frontend/src/features/executions/useLiveExecution.ts`
  - `frontend/src/test/websocket.ts`
  - this report
- **Index:** nothing staged. No commit made.
- **Unrelated changes:** none detected.
- **Report rename:** an earlier, shorter draft of this report (`docs/reports/frontend/phase-14-live-execution.md`, created in this session and never committed) was replaced by this file. Only the name in the prompt's convention remains.
- **Generated artifacts:** `frontend/dist/` and `frontend/test-results/` were produced by the build and Playwright runs (pre-existing, ignored directories).

---

## 24. Phase Completion Assessment

### Implemented
- Backend WebSocket gateway with first-frame authentication, authorization, per-poll session re-check, log-tail streaming, `end`, and defined close codes.
- `ExecutionService.follow_events`; the `live_poll_seconds` setting.
- Frontend WebSocket client with validation, dedup/stale dropping, resume by position, exponential backoff and terminal close-code handling.
- Hook with query-invalidation reconciliation; connection indicator; activity list with backend event-type mapping; Nodes status table.
- Deterministic WebSocket test harness; Vite WebSocket proxy.

### Verified
- 10 backend integration tests (real PostgreSQL, real server and client), including graceful shutdown with an open stream.
- 8 frontend unit tests and 4 component tests.
- 1 full-stack test (real backend, dropped connection, missed events, reconciliation).
- All quality gates (§13).

### Not Implemented
- Graph-canvas live status.

### Deferred
- Token source (21), execution graph and timeline (15), subscription metric (16), limits (23), push wake-up, jitter (§19).

### Open
- §21 issues 1–2. Former issues "deployment docs" and "stale `bootstrap.py` comment" were fixed in the follow-up (§11).

### Blocking Issues
- None for the phase acceptance criterion.
- Real-app usability depends on Phase 21.

**Phase Status:** COMPLETE WITH LIMITATIONS

---

## 25. Handoff to Next Phase (15 — Execution Inspector + Logs)

**The next phase can assume:**
- `WS /api/v1/executions/{id}/live` exists, with the protocol in `backend/src/akasha/api/live.py` and `docs/architecture.md` §23.
- `subscribeExecution` (`frontend/src/api/live.ts`) and `useLiveExecution` (`features/executions/useLiveExecution.ts`) are reusable:
  - the hook returns `{ state, events }`;
  - events are already validated, deduplicated and ordered by position.
- Any query under `queryKeys.executions.detail(id)` (detail, report, events) is invalidated on every live frame. New inspector queries placed under that prefix become live automatically.
- `describeEvent` maps all backend event types to text; reuse it for an event timeline.
- `LiveIndicator` and `LiveActivity` are components in `features/executions/live.tsx`.

**Contracts:**
- Streamed `items` are `EventOut`, the same as `GET /executions/{id}/events`.
- Positions are monotonic per execution but not contiguous (global identity), so gaps are normal and cannot be detected.
- Close codes 4400/4401/4404 are terminal; everything else reconnects.

**Must not change casually:**
- The token stays in the subscribe frame.
- No client-side status from events: invalidate and refetch.
- The `follow_events` read order (execution, then events).
- The global `FakeWebSocket` in `src/test/setup.ts`.

**Known constraints:**
- The real app sends no token until Phase 21, so live updates show "unavailable" there.
- The full-stack harness runs no worker (`AKASHA_WORKER_ENABLED=false`). Executions there stay QUEUED unless cancelled; a live graph test needs cancellation or a worker.
- The node status table shows raw backend values. The inspector should introduce proper node-status labels and tones, as `executionStatusDisplay` does for executions.

**Prerequisites:** none beyond the above. The execution graph for the inspector needs the pinned version graph (`GET /workflows/{id}/versions/{n}`) combined with `report.nodes` statuses.
