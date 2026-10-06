# Phase 28 — Frontend V1 Integration (Frontend V1 Completion Report)

Report date: 2026-10-05. Branch `main`. Base commit `7637ce1` (frontend phase 27 — production readiness). All work described here is **uncommitted**.

**Phase Status:** COMPLETE. **Frontend V1:** COMPLETE.

## 0. Method and findings

Every gate was run first on the untouched tree, before any change: format, lint, typecheck, unit/component tests, build, stubbed E2E, and full-stack E2E against the real backend. All passed.

Then the real-backend coverage was checked against each area Phase 28 names, without assuming the earlier phases were right. The V1 journey, credentials, sessions, WebSocket disconnect, triggers, versions and the accessibility sweep already ran against Backend V1. Three areas had been checked only against stubs:

- permission errors for a non-admin user;
- Settings (password change);
- Plugins (visited by the a11y sweep only).

| # | Finding | Class | Resolution |
|---|---------|-------|------------|
| 1 | No real-backend test showed that a **member** is refused admin areas and other users' workflows by the backend, rather than only by the UI. | Coverage gap | `tests/fullstack/roles.spec.ts`. A member signs in through the form. `/credentials` and `/plugins` get a 403 from the backend and show "Admin access required". A crafted `POST /credentials` from the page with the session cookie gets a 403. Another user's workflow gets a 404 and shows "Workflow not found". No product defect. |
| 2 | Settings → change password had no real-backend test. | Coverage gap | Same spec. A wrong current password is a field error, not a sign-out. A successful change keeps this session and revokes the member's other session (401); the backend accepts the new password. No product defect. |
| 3 | Plugins page had no functional real-backend test. | Coverage gap | Same spec. The admin view shows exactly what `GET /plugins` returned (trust warning, uploads-off notice, empty registry). No product defect. |
| 4 | The root `README.md` did not say where the run, test and deploy instructions are, and its tree listed `pnpm-lock.yaml`. The project uses npm. | Documentation | A quick-start pointer to `backend/README.md`, `frontend/README.md` and `docs/deployment.md`. The lockfile entry is corrected. |

While the spec was being written, two of its early assertions were found to be wrong. Playwright's `page.request` does not send the `SameSite=Strict` session cookie, so it got a 401 instead of the intended 403. A workflow-list check written the same way would have passed for the wrong reason. Both checks now run inside the page with `fetch`, the way the existing session spec does. No assertion was weakened.

**No product code changed in this phase.** No critical or blocking defect was found.

## 1. Implemented capabilities (Frontend V1)

The full V1 journey runs through the UI against Backend V1 (`journey.spec.ts`, one test of steps):

- login → dashboard → create workflow → draft editor;
- node library → add, configure and connect nodes → save;
- manual trigger → validate → publish (immutable version) → run from the trigger;
- live execution to the end → inspector (nodes, logs, graph) → monitoring (figures checked against the backend summary);
- report and its JSON download → export → import → validate, publish and run the import.

Areas, by feature folder (details in `frontend/README.md`):

| Area | Folder / route |
|---|---|
| Authentication, session expiry, logout | `features/auth` (`/login`, `RequireSession`) |
| Dashboard | `features/dashboard` (`/dashboard`) |
| Workflows, import/export | `features/workflows` (`/workflows`, `/workflows/:id`) |
| Draft editor: graph, node library, node inspector, connections, undo/redo, copy/paste, autosave, validate and publish | `features/editor` (`/workflows/:id/editor`) |
| Versions, comparison, restore, clone | `features/versions` |
| Triggers (all six types) | `features/triggers` |
| Executions, live updates, inspector, logs | `features/executions` |
| Monitoring | `features/monitoring` |
| Reports | `features/reports` |
| Credentials (write-only secrets) | `features/credentials` |
| Plugins | `features/plugins` |
| Settings | `features/settings` |
| Design system and `/dev/ui` catalog | `components/ui` |

## 2. Architecture summary

- **Layers:** route (`app/routes.tsx`, lazy per area) → feature screen → TanStack Query options/mutations (`api/queries.ts`, `api/mutations.ts`) → service function per backend router → `ApiClient` (the only HTTP code; ESLint enforces it).
- **Contracts:** every response is validated by Zod, and the domain types are inferred from those schemas. `src/api/openapi.json` is the backend's generated snapshot. `contract.node.test.ts` checks every service call against it; `backend/tests/test_openapi_contract.py` fails when the snapshot is stale.
- **State boundaries:**
  - server state lives only in the query cache;
  - navigable UI state lives in the URL;
  - editor state is a pure reducer (`editorState.ts`), persisted only through `useUpdateWorkflow` with `expected_revision`;
  - session state is one query (`['auth','session']`);
  - there is no global client store.
- **Graph editor:** React Flow, controlled by the editor state. Connection rules come from the backend's node contracts and `port_compatibility` table, and the backend's `/validate` is authoritative. Separate files hold the canvas, library, inspector, connections panel and validation panel.
- **WebSocket reconciliation:** events are notifications, not state. Each frame or resubscribe invalidates the execution's queries, throttled to one refetch per second. The client deduplicates by event `position` and resumes with `after=`. Reconnects back off exponentially; 4401 stops the stream. The indicator never shows "Live" without a subscription.

## 3. Important design decisions

- **Backend is the source of truth** for validity, publishability, execution state, permissions and plugin compatibility. Local checks are UX hints.
- **No optimistic updates.** Writes stay pending until the backend answers and the cache is refetched. A 409 leads to a reload and never overwrites.
- **Cookie session (ADR 0002):** an HttpOnly, `SameSite=Strict` cookie plus the `X-Akasha-CSRF` header. No token reaches JavaScript, and the WebSocket is authenticated by the same cookie, same origin only.
- **Same-origin deployment** with no runtime config. The bundle is static behind a reverse proxy with a strict CSP (`docs/deployment.md`).
- **Schema-driven node forms.** No node has its own form, so plugin nodes are configurable without frontend changes.
- **Tests at three levels:**
  - jsdom screen tests on the real router and client, with only `fetch` stubbed;
  - Playwright on the production bundle for real-browser behavior;
  - Playwright on the production bundle against the real backend for journeys.

## 4. Test suites executed (2026-10-05, final tree)

| Gate | Command | Result |
|---|---|---|
| Format | `npm run format:check` | PASS |
| Lint | `npm run lint` | PASS |
| Typecheck | `npm run typecheck` (`tsc -b`, strict) | PASS |
| Unit + component + API contract | `npm test` | PASS, 473 tests / 38 files |
| Production build | `npm run build` | PASS |
| E2E, production bundle, stubbed API | `npm run test:e2e` | PASS, 35 |
| E2E, production bundle, real Backend V1 (PostgreSQL 17.2 + Redis containers, real worker) | `npm run test:fullstack` | PASS, 24 (22 existing + 2 new) |
| Dependency audit | `npm audit` | 0 vulnerabilities |

Every gate was run twice: once before any change, once on the final tree. The full-stack suite left no containers behind.

## 5. E2E results

**Full stack (24, all pass):**

- `journey.spec.ts`: the V1 journey (§1).
- `credentials.spec.ts`: a credential is referenced, exported without its secret and reported missing on import; a corrupt package is refused.
- `workflows.spec.ts`:
  - workflow lifecycle and stale-revision conflict;
  - node library and inspector against the backend registry;
  - drag-connect and the backend port rule;
  - validate → fix → publish;
  - sessions: none, reload, sign-out, ended elsewhere;
  - versions;
  - cancel;
  - live run across a dropped WebSocket;
  - failed-run diagnosis;
  - triggers and event triggers.
- `roles.spec.ts` (new): member permission errors, Settings password change, Plugins.
- `a11y.spec.ts`: every screen in both themes.

**Stubbed (35, all pass):** the editor's drag, pan, box-select and keyboard-only build; the editor at the node limit and at 390 to 1600 px; dialogs, menus, tooltips and toasts; CSP enforcement; an unreachable backend; a chunk removed by a newer release; dashboard and workflow layouts on mobile.

## 6. Accessibility results

- axe finds no violations on any of the 16 screens, in light and dark, at 390, 768, 1024 and 1440 px, all filled with real backend data. No screen scrolls sideways at those widths.
- The `/dev/ui` catalog passes axe in both color schemes.
- Keyboard use is covered by tests:
  - every dashboard control is reachable;
  - a graph can be built and saved by keyboard alone;
  - dialogs trap focus and restore it;
  - menus and tooltips work from the keyboard.
- The non-visual graph view is the node list plus the connections panel.
- Status is always text plus tone, never color alone.

## 7. Security verification

| Requirement | Evidence |
|---|---|
| Backend authorization cannot be bypassed | `roles.spec.ts`: backend 403 on admin areas and on a crafted write, 404 on another user's workflow. `workflows.spec.ts`: 401 without a session. |
| Credential values never exposed | `credentials.spec.ts`: not in the export package or in any response the page received. `CredentialsPage.test.tsx`: not in the DOM, storage, URL, request URLs, console or query/mutation cache. |
| Session token not readable by scripts | HttpOnly `SameSite=Strict` cookie; `document.cookie` is empty; nothing in storage (`workflows.spec.ts`). |
| Published versions immutable | Read-only version pages; restore and clone never touch the version; the backend has no version write. Checked in the full-stack versions spec. |
| Frontend does not fabricate state | Status, validity and live state come from refetched backend data. The live view refetches on events and does not derive state from them. |
| XSS | All data is rendered as React text. ESLint bans `dangerouslySetInnerHTML`, `innerHTML`, `eval` and `new Function`. The strict CSP is enforced in every E2E spec, and injected inline scripts do not run (`security.spec.ts`). |
| WebSocket auth | Cookie and same-origin check on the backend; 4401 stops the client. |
| Dependencies | `npm audit`: 0 vulnerabilities. |

## 8. Performance verification

- Each area is a lazy route chunk. The entry chunk is 74 kB gzip, and the graph canvas (64 kB gzip) loads only for the editor, version and inspector views.
- The editor stays usable at the backend's node limit (`editor.spec.ts`).
- Logs page on the server at 100 per page, and events at 200 per page.
- Live refetches are throttled to one per second.
- Text over 20,000 characters is cut, with a "Show all" toggle.

## 9. Production build verification

- `npm run build` passes with no warnings. There are no source maps, and builds are byte-reproducible (Phase 27).
- The CSP is in the production `index.html`.
- `npm ci` uses the committed lockfile, exact versions and engine-strict.
- Deployment (static bundle behind a same-origin reverse proxy, caching, upgrades, browser floor) is in `docs/deployment.md` § "The UI".

## 10. Known limitations

- E2E runs on Chromium only. Firefox 128+ and Safari 17.4+ support is derived from the APIs used, not tested.
- The UI must be served at the host root; a sub-path deployment is not supported.
- No favicon.
- No client-side error telemetry. Requests are correlated server-side by `X-Request-ID`.
- Aggregates the backend does not provide are not shown: dashboard-wide token and cost totals, trigger and provider breakdowns, queue depth, and which workflows use a credential.
- Trigger and workflow-level validation issues are listed but have no editor surface to jump to.
- The full-stack suite uses fixed ports (8765 and 4174) and one worker, so it cannot run in parallel with itself.

## 11. Explicitly deferred (not implemented)

- **No frontend container image, and no UI service in `docker-compose.yml`.** The documented deployment serves the static bundle from the operator's reverse proxy. Adding an image would be new deployment scope, not an integration fix.
- User management UI for admins (backend `/users` exists) and a first-admin setup screen.
- Firefox and WebKit Playwright projects.
- Internationalization: V1 is English-only, consistently.

## 12. Recommended next development step

Make deployment of the whole product one step: a pinned, non-root static-file image (or a proxy service) in `docker-compose.yml` that serves the built UI and proxies `/api` to the API service, plus a deployment test that runs the full-stack journey against that Compose stack.

This closes the last gap between the tested setup (Vite preview + backend) and a production install. It should go through an ADR, since it changes the deployment topology in `docs/deployment.md`.

## Files changed in this phase

- `frontend/tests/fullstack/roles.spec.ts` (new)
- `frontend/README.md`: the full-stack spec list
- `README.md`: quick-start pointer and lockfile name
- `docs/reports/frontend/phase-28-frontend-v1-integration.md` (this report)
