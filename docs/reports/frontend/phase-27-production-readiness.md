# Phase 27 — Frontend Production Readiness

Report date: 2026-10-05. Branch `main`. Base commit `1342a34` (frontend phase 26 — E2E testing). All work described here is **uncommitted**.

## 1. Summary

**Objective** (`docs/roadmap-frontend.md` §33): a production build that can run against the production backend.

**Method.** Every check ran against the production bundle (`vite build` + `vite preview`), not the dev server:

- the full gate (`npm run check`), stubbed E2E, and full-stack E2E against the real backend on PostgreSQL and Redis containers;
- the bundle served on a LAN address over plain HTTP (an insecure context) and inspected in a browser;
- two builds compared byte for byte;
- `dist/` inspected for source maps and CSP, and the source scanned for browser APIs that set the browser floor.

**Result.** One blocking defect, fixed (§2). Everything else was already in place from earlier phases:

- same-origin API and WebSocket URLs (`ws`/`wss` follow the page);
- strict CSP;
- route error boundaries;
- session, backend-unavailable and retry states;
- transient-only query retries, and no write retries;
- lazy chunks, no source maps, 0 audit findings.

What was missing was deployment documentation for the UI itself.

**Phase Status:** COMPLETE

## 2. Findings and fixes

| # | Finding | Fix |
|---|---------|-----|
| 1 | **Blocking: the UI was dead on plain HTTP off localhost.** `crypto.randomUUID` exists only in secure contexts. `ApiClient` called it for every `X-Request-ID`, and it threw before any request was sent. The UI showed "Could not check your session" and could not recover. The editor's and the triggers' id generators had the same problem. `docs/deployment.md` explicitly supports this setup (`AKASHA_SESSION_COOKIE_SECURE=false`). Reproduced at `http://192.168.139.78:4180`: `isSecureContext === false`, no `/api` request went out. | `lib/randomHex.ts` (`crypto.getRandomValues`, available everywhere) gives the same 32-hex shape as backend `new_id`. Used by `ApiClient`, `newNodeId` and `newTriggerId`. ESLint now rejects `crypto.randomUUID`. Re-checked on the same origin: the session request goes out. |
| 2 | Deployment docs covered the UI's security headers only, not how to build, serve, cache or upgrade it. | `docs/deployment.md` § "The UI": reproducible build steps, same-origin and root-path requirement, supported browsers, no source maps, `index.html` `no-cache` vs immutable `/assets/`, a 404 (not `index.html`) for missing assets, and upgrades that keep old assets. `frontend/README.md` links to it. |
| 3 | No test of what an open tab does after an upgrade removed its page chunks. | E2E `smoke.spec.ts`: the chunk returns 404, the page shows "This page could not be displayed" with Reload, and the shell stays usable. |

## 3. Verified, no change needed

- **Environment and API config:** no runtime configuration. `/api/v1` is same-origin. `AKASHA_API_PROXY_TARGET` is read by `vite.config.ts` only and never enters the bundle. `.env.example` has no secrets.
- **WebSocket:** the URL comes from `window.location` (`wss` on HTTPS). Cookie auth, backoff reconnect and resume-after-position are covered by the full-stack "dropped connection" spec.
- **Backend unavailable:** reads retry twice on network errors, timeouts and 502/503/504, then show a fixed message with "Try again". Writes are never retried.
- **Source maps:** none built (Vite default). No `sourceMappingURL` in `dist/`.
- **Reproducibility:** two consecutive builds into separate directories are byte-identical. `npm ci` uses the lockfile, exact versions and `engine-strict`.
- **Browser floor:** Chrome/Edge 116, Firefox 128, Safari 17.4. Set by `AbortSignal.any` and Tailwind v4's CSS (oklch, color-mix). Documented.

## 4. Quality gates (all run on 2026-10-05)

| Gate | Command | Result |
|---|---|---|
| Format | `npm run format:check` | PASS |
| Lint | `npm run lint` | PASS |
| Typecheck | `npm run typecheck` | PASS |
| Unit / component | `npm test` | PASS, 473 tests / 38 files |
| Build | `npm run build` | PASS |
| E2E (production bundle, stubbed API, a11y + CSP fixtures) | `npm run test:e2e` | PASS, 35 |
| Full-stack E2E (production bundle, real backend) | `npm run test:fullstack` | PASS, 22 |
| Dependency audit | `npm audit` | 0 vulnerabilities |

The full-stack run used the fixed client. After it, only tests, docs and lint config changed, and the whole stubbed E2E suite was rerun on the rebuilt bundle.

## 5. Known limitations

- E2E runs on Chromium only. Firefox and Safari support is derived from the APIs used, not tested.
- No favicon. Browsers get a 404 for `/favicon.ico`.
- The UI must be served at the host root (absolute `/assets/` URLs); a sub-path deployment is not supported.
- No client-side error telemetry: errors stay in the browser console, and requests are correlated server-side via `X-Request-ID`.

## 6. Not implemented (out of scope)

- A frontend container image, or serving the UI from the backend Compose stack (Phase 28 integration, or an explicit decision).
- Firefox/WebKit Playwright projects.
