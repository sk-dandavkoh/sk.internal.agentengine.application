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

| Command                  | Purpose                                              |
| ------------------------ | ---------------------------------------------------- |
| `npm run dev`            | Dev server on http://localhost:5173                  |
| `npm run build`          | Typecheck, then production bundle into `dist/`       |
| `npm run preview`        | Serve `dist/` locally                                |
| `npm run typecheck`      | `tsc -b` over app and tooling configs                |
| `npm run lint`           | ESLint (type-checked `typescript-eslint` strict set) |
| `npm run format`         | Prettier write                                       |
| `npm run format:check`   | Prettier check (CI)                                  |
| `npm test`               | Vitest unit/component tests (jsdom)                  |
| `npm run test:e2e`       | Playwright against a fresh production build          |
| `npm run test:fullstack` | Playwright against the real backend (Docker + uv)    |

Quality gate before finishing a phase:

```bash
npm run format:check && npm run lint && npm run typecheck && npm test && npm run build && npm run test:e2e && npm run test:fullstack
```

**Full-stack suite** (`tests/fullstack/`, `playwright.fullstack.config.ts`):
the production bundle against the backend of this checkout, nothing
stubbed. `backend.sh` starts PostgreSQL 17.2 in a labelled container with
tmpfs storage, migrates and serves the backend on port 8765 (preview on
4174); every run starts with an empty database. The suite creates the
first admin through `/auth/setup` and, until the login UI exists (Phase
21), the browser sends that admin's token as a request header.
`globalTeardown` removes the container; `backend.sh` also removes leftovers
of a crashed run. Needs Docker and `uv`.

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
│   ├── features/       product areas (dashboard, …); a feature owns its screens
│   ├── components/     QuerySection: loading/error/stale states of one query
│   ├── lib/            small framework-free helpers (cx, format, form)
│   ├── styles/         global CSS: Tailwind v4 entry and design tokens
│   └── test/           Vitest setup; app.tsx renders routes against a stub backend
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
  e.g. `auth.ts` (`login`, `logout`, `fetchCurrentUser`), `projects.ts`,
  `executions.ts`, `reports.ts`, `nodes.ts`, `credentials.ts`, `plugins.ts`.
  Session storage and login UI follow in Phase 21.
- **No WebSocket client**: the V1 backend has no WebSocket endpoint; live
  execution state is polled (`docs/architecture.md` §22).

### Domain types

The domain types are the Zod schemas of the service modules, with the
TypeScript type inferred from each (`Project = z.infer<typeof projectSchema>`).
There is no second, hand-written model: what the type promises is what the
response was checked against. Shared pieces live in `schemas.ts`.

| Backend concept     | Type (module)                                                         |
| ------------------- | --------------------------------------------------------------------- |
| Project             | `ProjectSummary` (lists), `Project` (detail) — `projects.ts`          |
| ProjectDraft        | `ProjectDraft` (`graph`, `triggers`, `dependencies`)                  |
| ProjectVersion      | `ProjectVersionSummary`, `ProjectVersion` (immutable)                 |
| Graph / Node / Edge | `Graph`, `NodeInstance`, `Edge`                                       |
| Trigger             | `Trigger`, `TriggerType`                                              |
| Validation errors   | `ValidationResult`, `ValidationIssue`, `ValidationCode`               |
| Node definition     | `NodeDefinition`, `NodePort` — `nodes.ts`                             |
| Execution / Event   | `Execution`, `ExecutionStatus`, `ExecutionEvent` — `executions.ts`    |
| NodeExecution       | `NodeExecution`, `ExecutionReport`, `ExecutionMetrics` — `reports.ts` |
| CredentialReference | `CredentialReference` (metadata only) — `credentials.ts`              |
| Plugin              | `Plugin`, `PluginState` — `plugins.ts`                                |

Enums (statuses, validation codes, trigger types) mirror the backend's and
are display data only: transitions, validity and publishability are decided
by the backend (AGENTS §4). Fields with a backend default are optional on the
wire and filled in by the schema. Request-only failures (422 on a write)
arrive as `ApiError` with the backend's `details`.

### Server state

TanStack Query owns all server state; nothing copies it into React state or a
store.

- **`queries.ts`**: `queryKeys` (the only place keys are spelled) and query
  options per read endpoint — `projectQueries`, `executionQueries`,
  `nodeQueries`, `pluginQueries`, `credentialQueries` — used as
  `useQuery(projectQueries.detail(useApiClient(), id))` or in route loaders
  via `queryClient.ensureQueryData`. Keys nest by ownership:
  `['projects', id]` covers the project's versions and executions,
  `['executions', id]` its events and report. Every execution write also
  invalidates `['executions', 'overview']`: the cross-project lists and the
  status counts the dashboard shows.
- **Freshness**: default stale-immediately (refetch on mount, stale data
  shown meanwhile); published versions `staleTime: Infinity` (immutable);
  node catalog and plugins 5 minutes (change only on backend restart).
- **`mutations.ts`**: one hook per write (`useUpdateProject`,
  `usePublishProject`, `useExecuteProjectVersion`, `useCancelExecution`,
  `useCreateCredential`, …). Each declares what it invalidates; the mutation
  stays pending until that refetch finished. No optimistic updates. Saving a
  draft writes the returned project into the cache (no refetch); deleting
  removes the project's queries. Credential mutations use `gcTime: 0` so the
  secret in their variables leaves the mutation cache once unobserved.

**State boundaries**: server state = the query cache above. UI state
(dialogs, selection, filters) = component state or the URL. Editor state
(Phase 07) is initialized from `project.draft`, edited locally and persisted
only through `useUpdateProject`; it never writes into the query cache.

Not covered: project import/export (binary ZIP bodies; the client speaks
JSON only), user management (Phase 21). Logs are the execution events; the
backend has no separate log API.

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

## Dashboard

`features/dashboard/` (`/dashboard`): status counts, active executions,
recent failures, recent executions, system readiness/version and projects by
status, each from its backend endpoint (`/executions?status=`,
`/executions/status-counts`, `/system/status`, `/projects`). Every section
loads, fails, retries and shows its empty state on its own
(`QuerySection`); a failed refresh keeps the last data with a warning. When
every section has failed with nothing to show (backend down, no session),
one page-level error with Retry replaces them.
"Refresh" refetches the queries on screen. No polling: live updates are
Phase 14. Recent failures expand to their recorded errors: the execution
report (`/reports/executions/{id}`) is fetched only when a row is opened.

Not shown, because the backend has no aggregate for them: token usage and
cost (per execution only, in the report) and Prometheus metrics (`/metrics`
is a scrape target, not an API). Project names and status counts cover the
first 100 projects (the page limit); beyond that the card says so.

## Projects

`features/projects/`: `/projects` lists the caller's projects (search by
name, status filter, sort, 25 per page) with search, filter, sort and page
in the URL, so links and Back restore the view. All of it runs on the
backend (`GET /projects?q=&status=&sort=`), never on a client-side copy.
`/projects/:projectId` shows the metadata and offers Open editor
(`/projects/:id/editor`, Phase 07), Edit details (name and description),
Duplicate and Delete.

Writes are authoritative, not optimistic: a dialog stays open with a
pending button until the backend answers, and the screen shows only data
the backend returned. Edit details sends the draft back unchanged with the
`revision` it was read at; a 409 is shown and the project reloaded. Delete
asks for confirmation; the backend refuses projects with published
versions (409 `PROJECT_HAS_VERSIONS`), and the dialog shows that answer.
Request validation errors (`details["body.name"]`) appear at their field.

## Draft editor

`features/editor/` (`/projects/:projectId/editor`) edits the project
**draft** only. The page says so: "Draft" badge, the draft revision, and
that published versions stay unchanged. Saving never publishes.

- `editorState.ts`: the pure editor state and reducer (graph, saved graph,
  revision, undo/redo stacks, selection, clipboard, drag origin). Graphs are
  immutable values. Dirty means `graph !== saved`, so undoing back to the
  saved graph makes the editor clean again. One drag is one undo step, and
  history keeps 100 steps. Copy/paste is an internal clipboard: pasted nodes
  get fresh `node_<32 hex>` ids and remapped edges.
- `useDraftEditor.ts`: persistence. A save is `PUT /projects/{id}` with the
  whole draft (only the graph changed) and the `expected_revision` the
  editor last saw. Only one save runs at a time, and a 409 is never
  overwritten: the user may discard local edits and reload. Autosave runs
  1.5 s after the last change, not mid-drag and not after a failed save
  (no retry loop; Save or Retry resumes it). Leaving with unsaved changes asks
  first (router blocker plus `beforeunload`). A save keeps the undo stack.
- `GraphCanvas.tsx`: React Flow (`@xyflow/react`), controlled. Positions
  and selection come from the editor state. Measured node sizes are
  rendering state and stay local. Pan, zoom (0.2–2×, controls, wheel), grid,
  minimap, and node selection (click or keyboard; arrow keys move nodes).
  Connecting and deleting are off until Phase 10. Library nodes can be
  dropped onto it (see Node library). Ports come from the node
  catalog, plus any port an edge uses, so every stored edge is drawn.
  Unknown node types are marked in text.
- Node positions are part of the backend graph (`position: {x, y} | null`,
  architecture §7.2). Never-placed nodes are laid out on load and saved with
  the next edit.
- Shortcuts (on the editor, never in text fields): Ctrl/⌘+Z undo,
  Ctrl/⌘+Shift+Z or Ctrl+Y redo, Ctrl/⌘+C/V copy/paste, Ctrl/⌘+S save.
- The node list beside the canvas is the non-visual view of the graph
  (AGENTS §100): every node with type and connection counts. Selecting a
  node there selects and centers it on the canvas.

Tests: `editorState.test.ts` covers the reducer. `EditorPage.test.tsx` runs
the real route against a stub backend that enforces revisions (load, select,
zoom, move, undo/redo, save, autosave, failures, conflict, leave guard).
`tests/e2e/editor.spec.ts` covers what needs real layout: drag, pan, edge
paths, minimap and the saved positions.

## Node library

`features/editor/NodeLibrary.tsx`, beside the canvas: the node catalog from
`GET /nodes` (the backend registry, built-in and plugin nodes alike). There
is no node list in the frontend; a new backend node appears without a
frontend change. Unknown categories and capabilities are shown humanized.

- Search (name, type, description, category), category and capability
  filters, all local: the catalog is small and unpaginated. Results are
  grouped by category with a live "n of m nodes" count.
- Each item shows a category icon, name, type, version, description and
  capabilities. "Preview and details" opens a preview (the canvas card
  itself, `NodeCard.tsx`, shared with `GraphCanvas`), then ports (type,
  required, multiple), the config schema's required settings and the
  documentation link (only `http(s)` URLs are linked: it is plugin
  metadata).
- Icons are per category (`CATEGORY_GLYPHS` in `NodeCard.tsx`, the
  backend's built-in categories); any other category, e.g. a plugin's, gets
  a generic glyph. Icons are decorative: the category is always text too.
  The node contract has no icon field; add one only when a plugin needs
  its own icon.
- Add puts the node at the canvas center; dragging an item onto the canvas
  puts it at the drop point. Both are one undoable edit that selects the
  new node; it is saved like any other draft change. Adds onto an occupied
  spot are offset, so repeated adds stay visible.
- Refused: a type the catalog does not contain (a stale drop, a removed
  plugin) shows a toast, and a duplicate node id (the backend would reject
  the draft). New nodes start with empty config; configuring them is the
  inspector (Phase 09), validity is the backend's `/validate`.

## Node inspector

`features/editor/NodeInspector.tsx`, beside the canvas while exactly one
node is selected. Its form is generated from the node's `config_schema`
(`configSchema.ts` reads the schema, `ConfigForm.tsx` renders it); no node
has a form of its own, so a new backend or plugin node is configurable
without a frontend change.

- Field kinds: text, prompt template (`format: template`), number/integer,
  boolean, select (`enum`), credential reference (`format:
credential-reference`), nested object, list (`items`, `maxItems`), map
  (`additionalProperties` schema) and JSON for anything else (no type,
  type unions, free-form objects). `type: [..., "null"]` adds a "no value
  (null)" toggle.
- Required fields are marked; `description` and `default` are shown.
  Defaults are never written: an unset field is absent and the node uses
  its default ("Use default" removes a set value). Clearing an optional
  field removes the key.
- Validation is the backend's: the config is sent to `POST
/nodes/{type}/config/validate` 400 ms after typing settles (cached per
  config), and each issue shows at its field and in a list. Locally only
  unparsable input is caught (a number or JSON being typed); it is not
  committed, so the draft keeps the last valid value.
- Credentials: the field takes a credential name and stores
  `credential:<name>`; names are suggested from the credential list (admin
  only; members type the name). A stored value that is not a reference is
  hidden, never shown, since it might be a secret.
- Schema changes: keys the schema does not know stay visible (as JSON,
  with Remove), and a value of the wrong type is shown as JSON instead of
  being dropped. A node type missing from the catalog shows no form.
- Edits are draft changes like any other: saved by save/autosave, and
  undoable. Typing on in one field is one undo step.

Tests: `configSchema.test.ts` (schema reading), `editorState.test.ts`
(the `configure` action), `NodeInspector.test.tsx` (the route against a
stub backend: every field kind, nesting, defaults, null, validation,
credentials, invalid input, save failure, schema changes) and
`tests/fullstack/projects.spec.ts` (core.http configured against the real
backend).

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
