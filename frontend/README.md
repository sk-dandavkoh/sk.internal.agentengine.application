# Akasha Frontend

React + TypeScript single-page app for the Akasha Agent Engine. Rules live in
[`AGENTS.md`](AGENTS.md); the phase plan in
[`../docs/roadmap-frontend.md`](../docs/roadmap-frontend.md).

## Requirements

- Node.js `^22.22.0 || >=24` (`.nvmrc` pins 24)
- npm (the committed `package-lock.json` is authoritative; `.npmrc` saves exact versions)
- `package.json` is indented with 2 spaces (Prettier override): npm copies its
  indentation into `package-lock.json`, so a 4-space `package.json` made every
  `npm install` re-indent the whole lockfile

## Setup

```bash
npm ci
npx playwright install chromium   # once, only for E2E tests
```

## Commands

| Command                | Purpose                                              |
| ---------------------- | ---------------------------------------------------- |
| `npm run dev`          | Dev server on http://localhost:5173                  |
| `npm run build`        | Typecheck, then production bundle into `dist/`       |
| `npm run preview`      | Serve `dist/` locally                                |
| `npm run typecheck`    | `tsc -b` over app and tooling configs                |
| `npm run lint`         | ESLint (type-checked `typescript-eslint` strict set) |
| `npm run format`       | Prettier write                                       |
| `npm run format:check` | Prettier check (CI)                                  |
| `npm test`             | Vitest unit/component tests (jsdom)                  |
| `npm run test:e2e`     | Playwright against a fresh production build          |

Quality gate before finishing a phase:

```bash
npm run format:check && npm run lint && npm run typecheck && npm test && npm run build && npm run test:e2e
```

## Configuration

Copy `.env.example` to `.env.local` (git-ignored).

- `AKASHA_API_PROXY_TARGET` (default `http://127.0.0.1:8000`): backend the dev
  and preview servers proxy `/api` to. It is read only by `vite.config.ts` and
  never enters the bundle.
- Only `VITE_*` variables are exposed to browser code. Never put secrets in them.

The browser always calls the backend on the same origin (`/api/v1/...`). In
development Vite proxies it; in production a reverse proxy must do the same.
This keeps the backend free of CORS configuration.

## Structure

```text
frontend/
├── src/
│   ├── main.tsx        entry point: mounts <App/>
│   ├── app/            app root: router, shell layout, route boundaries
│   ├── api/            the only backend communication layer (see API layer)
│   ├── components/ui/  design system primitives (import from the index)
│   ├── lib/            small framework-free helpers (cx)
│   ├── styles/         global CSS: Tailwind v4 entry and design tokens
│   └── test/           Vitest setup (jest-dom matchers, RTL cleanup)
├── tests/e2e/          Playwright specs
├── index.html
├── vite.config.ts      Vite + Vitest config
├── playwright.config.ts
├── eslint.config.js
├── tsconfig.app.json   browser code (src/)
└── tsconfig.node.json  tooling configs and e2e tests
```

Later phases add directories as they gain content, following `AGENTS.md` §6:
`features/<domain>/`, `hooks/`, `schemas/`, `types/`. Unit and component tests sit next to the code as
`*.test.ts(x)`.

## Decisions

- **Tailwind v4 via `@tailwindcss/vite`**: no `tailwind.config` or PostCSS
  config; design tokens live in `src/styles/index.css` (`@theme`).
- **TypeScript strict plus** `noUncheckedIndexedAccess`,
  `exactOptionalPropertyTypes`, `noPropertyAccessFromIndexSignature`,
  `verbatimModuleSyntax`, `erasableSyntaxOnly`.
- **E2E runs against the production build** (`build` + `preview`), not the dev
  server, so it exercises what ships.
- **React Router (data mode)**: routes live in `src/app/routes.tsx`; areas
  are `lazy` routes so each feature becomes its own chunk. Until a feature's
  phase lands, its route points at the shared `PlaceholderPage` chunk.
- **Boundaries**: the root route's `errorElement` catches shell failures; a
  pathless content route's `errorElement` keeps the sidebar usable when a page
  fails. Unknown paths render `NotFoundPage` inside the shell. The first load
  shows `HydrateFallback`; later navigations show a pending bar and set
  `aria-busy` on `<main>`.
- **State boundaries**: the URL is the navigation state. Server state goes
  through TanStack Query (provided at the app root). Feature and editor state
  stay inside their feature subtree. There is no global client store.

## API layer

`src/api/` is the only code that speaks HTTP; ESLint rejects `fetch` and
`XMLHttpRequest` everywhere else in `src/`.

- **`client.ts` — `ApiClient.request(method, path, schema, options)`**: base
  URL `/api/v1` (same origin), JSON bodies, query params, `Bearer` token from
  the injected `getToken`, an `X-Request-ID` per request (the backend logs it),
  a timeout (default 30 s, per request overridable) and caller cancellation via
  `signal`. Every response body is validated with its Zod `schema`; an empty
  body (204) parses as `undefined`, so no-content calls pass `z.undefined()`.
- **`errors.ts` — `ApiError`**: every failure, discriminated by `kind`:
  `network`, `timeout`, `cancelled`, `malformed_response`, `validation` (422),
  `authentication` (401), `authorization` (403), `not_found` (404), `conflict`
  (409), `rate_limited` (429), `server` (5xx), `http` (other 4xx). `code`,
  `details` and `requestId` come from the backend error envelope; `message` is
  always user-facing (the backend's redacted message, or a fallback when no
  envelope arrived, e.g. a proxy's 502). Route error boundaries display it.
- **`queryClient.ts`**: queries retry at most twice and only on transient
  failures (`network`, `timeout`, 502/503/504); mutations never retry, since a
  timed-out write may have taken effect.
- **`context.ts`**: the `ApiClient` is injected through `ApiClientContext` /
  `useApiClient()`, never imported as a singleton.
- **Services**: one module per backend router, functions taking the client,
  e.g. `auth.ts` (`login`, `logout`, `fetchCurrentUser`). Domain services and
  query hooks follow in Phase 04; session storage and login UI in Phase 21.
- **No WebSocket client**: the V1 backend has no WebSocket endpoint; live
  execution state is polled (`docs/architecture.md` §22).

Tests: `client.node.test.ts` runs the client against a real local HTTP server
(Vitest `node` environment; typechecked by `tsconfig.node.json`).

**Contract with the backend.** `src/api/openapi.json` is the backend's OpenAPI
document, generated by the backend and committed (never edit it by hand).
Two tests keep both sides in step:

- `backend/tests/test_openapi_contract.py` fails when the snapshot is stale,
  and when a documented error response is not `ErrorResponse`. After an
  intended API change, regenerate it from `backend/`:
  `AKASHA_UPDATE_OPENAPI_SNAPSHOT=1 uv run pytest tests/test_openapi_contract.py`
- `src/api/contract.node.test.ts` calls every service function through a
  recording client and checks method, path (templated paths included), the
  success response schema (exact field set, types, enums, formats, nullability,
  and no field required that the backend marks optional), request body field
  names, and the error envelope. A new service function must be added to its
  call list; a coverage test fails otherwise.

## Design system

All UI is composed from `src/components/ui` (`import { Button, Dialog } from
'../components/ui'`). Features do not restyle primitives; a `className` prop,
where offered, is for layout only (margin, width, grid placement). The live
reference is the unlisted route **`/dev/ui`** (catalog of every primitive and
state, both themes); it is also the E2E target for native browser behavior.

**Tokens** (`src/styles/index.css`): components use semantic colors only —
`canvas`, `surface`, `surface-muted`, `border`, `border-strong`, `fg`, `muted`,
`focus`, `primary`/`on-primary`, `danger`/`on-danger`, and per-tone
`{neutral,info,success,warning,danger}-{soft,text}` — never raw palette
classes. Dark theme follows `prefers-color-scheme`; `data-theme="light"` on
`<html>` opts out. Every interactive element uses the `focus-ring` utility.

**Conventions**

| Concern        | Convention                                                                    |
| -------------- | ----------------------------------------------------------------------------- |
| Page structure | `PageHeader` renders the page's single `h1` (+ description, actions)          |
| Sections       | `Card` (title `h2` by default, `titleAs="h3"` when nested)                    |
| Typography     | system sans; `text-2xl` page title, `text-base` section title, `text-sm` body |
| Spacing        | 4 px grid: `gap-2` inside controls, `gap-4` in forms, `gap-6` between cards   |
| Controls       | 40 px (`md`) / 32 px (`sm`) height, `rounded-md`                              |
| Content width  | `<main>` caps at `max-w-6xl`, `p-4` mobile / `p-6` from `sm`                  |
| Status         | `Badge` with `dot`; tone plus text, never color alone                         |

**Primitives**

- Actions: `Button` (`primary`/`secondary`/`ghost`/`danger`, `sm`/`md`,
  `loading` keeps focus and blocks activation), `TextLink` (navigation).
- Forms: `Input`, `Textarea`, `Select` (native, children are `<option>`s),
  `Checkbox`, `Switch`. Each takes `label`, `description`, `error`; the error
  sets `aria-invalid` and is linked via `aria-describedby`. Show submit
  progress with `Button loading`; it cannot be submitted twice.
- Overlays: `Dialog` (native modal `<dialog>`; `data-autofocus` picks the
  initial focus), `ConfirmDialog` (names the consequence, `irreversible`,
  `pending`, `error`; focus starts on Cancel), `DropdownMenu`, `Tooltip`
  (supplementary text only; never the sole label).
- Structure: `Card`, `Tabs` (controlled via `value`/`onValueChange` for URL
  state), `Table` (captioned, `empty` slot; paginate server-side).
- Feedback: `Alert` (inline), `useToast().notify()` (transient; success/info
  time out after 5 s, warnings/errors stay), `Badge`.
- Async states: `LoadingState`, `Skeleton` (decorative; pair with
  `aria-busy`), `EmptyState`, `ErrorState` (user-facing text only).
