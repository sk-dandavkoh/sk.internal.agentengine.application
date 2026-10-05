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

## Local development

The UI needs a backend: every screen sits behind a session. Two terminals:

```bash
npm run dev:backend   # terminal 1: needs Docker and uv
npm run dev           # terminal 2: http://localhost:5173
```

`dev:backend` runs the full-stack suite's throwaway backend
(`tests/fullstack/backend.ts --dev`): the backend of this checkout on fresh
PostgreSQL and Redis containers, migrated, with the in-process worker so
executions run, on port 8000 (Vite's default proxy target). It creates an
`admin` account and prints its generated password; sign in with that.
Everything is gone after Ctrl+C, and the next start removes leftovers of a
run that was killed. For data that survives restarts, run the backend
yourself (`backend/README.md`, first admin via `/auth/setup`).

Port 8000 taken? The script refuses to start instead of sharing the port.
Pick another one for both sides:

```bash
AKASHA_E2E_API_PORT=8001 npm run dev:backend
AKASHA_API_PROXY_TARGET=http://127.0.0.1:8001 npm run dev
```

(or put `AKASHA_API_PROXY_TARGET` in `.env.local`). The built-in example
projects are in `backend/examples/projects/` (see `backend/README.md`).

## Commands

| Command                  | Purpose                                              |
| ------------------------ | ---------------------------------------------------- |
| `npm run dev`            | Dev server on http://localhost:5173                  |
| `npm run dev:backend`    | Throwaway backend for `dev` (see Local development)  |
| `npm run check`          | Format check, lint, typecheck, unit tests, build     |
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
npm run check && npm run test:e2e && npm run test:fullstack
```

**Full-stack suite** (`tests/fullstack/`, `playwright.fullstack.config.ts`):
the production bundle against the backend of this checkout, nothing
stubbed. `backend.ts` (a Node script, so it also runs on Windows) starts PostgreSQL 17.2 (tmpfs storage) and Redis in
labelled containers, migrates and serves the backend with its in-process
worker on port 8765 (preview on 4174), so executions really run; every run
starts with an empty database. Specs that need a run to stay RUNNING use a
long `core.delay` node and cancel it. The suite creates the
first admin through `/auth/setup`; browsers sign in like the UI does, with
the session cookie (the auth specs through the login form itself).
`globalTeardown` removes the containers; `backend.ts` also removes leftovers
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
│   ├── components/     QuerySection: loading/error/stale states of one query
│   ├── components/ui/  design system primitives (import from the index)
│   ├── features/       product areas; a feature owns its screens, hooks and logic
│   ├── lib/            small framework-free helpers (cx, format, form)
│   ├── styles/         global CSS: Tailwind v4 entry and design tokens
│   └── test/           Vitest setup and test harness (see Testing)
├── tests/e2e/          Playwright specs against a stubbed API
├── tests/fullstack/    Playwright specs against the real backend; backend.ts
├── index.html
├── vite.config.ts      Vite + Vitest config
├── playwright.config.ts
├── eslint.config.js
├── tsconfig.app.json   browser code (src/)
└── tsconfig.node.json  tooling configs and e2e tests
```

There are no global `hooks/`, `schemas/` or `types/` directories (`AGENTS.md`
§6 lists them as examples): hooks live in the feature that uses them, API
schemas and the types inferred from them in `src/api/`. Unit and component
tests sit next to the code as `*.test.ts(x)`; `*.node.test.ts` runs in Node
instead of jsdom (the API client against a real HTTP server).

### Adding a feature

A screen is a route, a feature folder and the API calls it needs. Take
`features/reports/` as the model:

1. **API**: the service function in `src/api/<router>.ts` (Zod response
   schema, type inferred from it), its query options in `queries.ts` (key in
   `queryKeys`) or its mutation hook in `mutations.ts` (with what it
   invalidates). Add the call to `contract.node.test.ts`; its coverage test
   fails until you do. A new backend endpoint means regenerating
   `src/api/openapi.json` (see "Contract with the backend").
2. **Route**: a `lazy` entry in `src/app/routes.tsx`, and a `navItems` entry
   in `src/app/navigation.ts` if it is a top-level area.
3. **Screen**: `features/<area>/<Name>Page.tsx`, composed from
   `components/ui` (`PageHeader`, `Card`, `Table`, …). Wrap each query in
   `QuerySection` for loading, error, stale and empty states. Navigable state
   (filters, tabs, page) goes in the URL.
4. **Tests**: `<Name>Page.test.tsx` next to it (see Testing), plus a
   Playwright spec only for what needs a real browser.
5. **Docs**: a section in this README, like the ones below.

### Conventions

- **Errors**: every API failure is an `ApiError` with a `kind` (see API
  layer). Show `error.message` (user-facing by construction) via
  `ErrorState`, `Alert` or a field's `error`; map 422 `details` to fields;
  answer 409 by reloading, never by overwriting. Do not catch errors to hide
  them, and never show stack traces. A thrown render error reaches the route
  boundary.
- **No `fetch`** outside `src/api/` (ESLint enforces it), no
  `dangerouslySetInnerHTML`, no secrets in state, storage, URLs or logs.
- **Components**: function components, named exports, props typed inline or
  as an `interface` above the component. Split a file when it holds more than
  one screen-level responsibility (the editor is the model: canvas, library,
  inspector and panels are separate files).
- **Backend decides**: status, validity, permissions and compatibility come
  from responses. Local checks are UX hints only (`AGENTS.md` §7).

## Testing

`npm test` runs every `src/**/*.test.ts(x)` in jsdom; `npm run test:watch`
reruns on change. No test reaches a network.

**Screen tests** (`src/test/app.tsx`) render real routes with the real
router, `ApiClient` and `QueryClient`; only `fetch` is replaced:

```tsx
import { ok, apiError, pending, renderRoute, stubBackend } from '../../test/app'

it('shows the report', async () => {
    const requests = stubBackend({
        'GET /reports/executions/exec_1': () => ok(report()),
        'POST /executions/exec_1/cancel': () =>
            apiError(409, 'EXECUTION_STATE', 'Already finished.'),
    })
    const { ready } = renderRoute('/reports/exec_1')
    await ready // the lazy route is loaded; findBy* now times rendering only
    expect(await screen.findByRole('heading', { name: /Report/ })).toBeVisible()
    expect(requests.map((url) => url.pathname)).toContain(
        '/api/v1/reports/executions/exec_1',
    )
})
```

- `stubBackend(handlers)`: keys are `"METHOD /path"` below `/api/v1`; a
  handler gets the `URL` and `RequestInit` and returns `ok(body)`,
  `apiError(status, code, message)` (the backend's error envelope), any
  `{ status, body }`, or `pending()` to stay loading. Unknown routes answer 404. The user is signed in as `testUser` (admin) unless the test overrides
  `GET /auth/session` (`noSession()` for signed out). It returns the request
  log.
- **Fixtures**: build response bodies with small functions at the top of the
  test file that return a valid backend object and take overrides
  (`execution(id, status)`, `project(overrides)`). They must match the
  backend contract, since the client validates every response: a fixture the
  schema rejects shows up as a "malformed response" error, not as a pass.
- **Live updates**: `WebSocket` is `FakeWebSocket` (`src/test/websocket.ts`)
  in every test. Drive it by hand: `serverOpen()`, `serverSend(frame)`,
  `serverClose(code)`, and read `frames()` the client sent. Use fake timers
  for backoff, never sleeps.
- **Browser gaps**: jsdom has no layout and no real modal dialogs;
  `setup.ts` stubs just enough for React Flow and `<dialog>` to mount.
  Geometry, focus trapping and Escape belong in Playwright.
- Locale is pinned to en-US in `setup.ts`, so formatted output is stable.

**E2E** (`tests/e2e/`, `npm run test:e2e`): Playwright against the
production build with the API stubbed through `page.route`. Call
`stubSession(page)` (`tests/e2e/session.ts`) after the spec's own catch-all
route. Use it for real-browser behavior: drag, layout, focus, native
dialogs. The design-system catalog `/dev/ui` is its target for primitives.

**Full stack** (`tests/fullstack/`, `npm run test:fullstack`): see above.
Use it for journeys whose value is the real backend (publish, run,
inspect).

There is no Storybook: `/dev/ui` already renders every primitive in every
state and both themes, and feature screens are best seen against
`dev:backend`.

## Decisions

- **Tailwind v4 via `@tailwindcss/vite`**: no `tailwind.config` or PostCSS
  config; design tokens live in `src/styles/index.css` (`@theme`).
- **TypeScript strict plus** `noUncheckedIndexedAccess`,
  `exactOptionalPropertyTypes`, `noPropertyAccessFromIndexSignature`,
  `verbatimModuleSyntax`, `erasableSyntaxOnly`.
- **E2E runs against the production build** (`build` + `preview`), not the dev
  server, so it exercises what ships.
- **React Router (data mode)**: routes live in `src/app/routes.tsx`; areas
  are `lazy` routes so each feature becomes its own chunk.
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
  URL `/api/v1` (same origin), JSON bodies, query params, the
  `X-Akasha-CSRF` header that lets the backend accept the HttpOnly session
  cookie (see "Authentication"), an `X-Request-ID` per request (the backend logs it),
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
  e.g. `auth.ts` (`login`, `logout`, `fetchSession`, `changePassword`),
  `projects.ts`, `executions.ts`, `reports.ts`, `nodes.ts`, `credentials.ts`,
  `plugins.ts`.
- **Live updates**: `live.ts` follows one execution over the backend
  WebSocket (`docs/architecture.md` §23); see "Live execution" below.

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
JSON only), user management (Phase 21).

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
"Refresh" refetches the queries on screen. No polling; live updates follow
one execution on its detail page (below). Recent failures expand to their recorded errors: the execution
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
  revision, undo/redo stacks, node and edge selection, clipboard, drag
  origin). Graphs are
  immutable values. Dirty means `graph !== saved`, so undoing back to the
  saved graph makes the editor clean again. One drag is one undo step, and
  history keeps 100 steps. Copy/paste is an internal clipboard: pasted nodes
  get fresh `node_<32 hex>` ids and remapped edges. Duplicate is copy and
  paste in one step that leaves the clipboard alone. Connect, reconnect
  (the edge keeps its place) and delete (nodes with every edge they touch,
  plus selected edges) are single undo steps. The reducer refuses only
  what the backend would refuse to store (self-edges, duplicate edges,
  dangling ends).
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
  minimap, and node and edge selection (click, Ctrl/⌘-click, Shift-drag a
  box; arrow keys move nodes). Drag from an output to an input to connect,
  or drag an edge end to reconnect it. Library nodes can be
  dropped onto it (see Node library). Ports come from the node
  catalog, plus any port an edge uses, so every stored edge is drawn.
  Unknown node types are marked in text. Port types are shown on the card,
  and required inputs are starred. A required input with no connection is
  named on the card ("Missing required input").
- `connections.ts`: whether a connection may be drawn. It uses the node
  contracts and the backend's `port_compatibility` table from `GET /nodes`
  (architecture §45), so the type rule is never copied into the frontend. It
  reports self, duplicate, input already connected, incompatible type,
  unknown port or type, and catalog not loaded. While a drag runs, the
  ports that fit grow and turn green, the others fade, and the drawn line
  is solid or dashed red. A drop on a port that refuses shows the reason
  in a toast. Stored edges the rule rejects (imports, contract changes)
  are drawn dashed with a text label.
- `ConnectionsPanel.tsx`: the selected node's connections as lists with
  Remove buttons, plus a "New connection" form. The form lists every input
  of every other node, with the reason when one cannot take the chosen
  output. It is the keyboard and screen-reader way to edit edges, checked
  by the same rules as dragging.
- Node positions are part of the backend graph (`position: {x, y} | null`,
  architecture §7.2). Never-placed nodes are laid out on load and saved with
  the next edit.
- Shortcuts (on the editor, never in text fields): Ctrl/⌘+Z undo,
  Ctrl/⌘+Shift+Z or Ctrl+Y redo, Ctrl/⌘+C/V copy/paste, Ctrl/⌘+D
  duplicate, Ctrl/⌘+S save. On the canvas only: Delete/Backspace delete
  the selection, Ctrl/⌘+A selects all, Escape clears the selection.
- The node list beside the canvas is the non-visual view of the graph
  (AGENTS §100): every node with type and connection counts. Selecting a
  node there selects and centers it on the canvas.

Tests: `editorState.test.ts` covers the reducer. `EditorPage.test.tsx` runs
the real route against a stub backend that enforces revisions (load, select,
zoom, move, undo/redo, save, autosave, failures, conflict, leave guard).
`connections.test.ts` covers the connection rules. Graph editing in
`EditorPage.test.tsx` covers the panel, delete, duplicate, select all,
undo/redo, invalid stored edges and reload consistency.
`tests/e2e/editor.spec.ts` covers what needs real layout: drag, pan, edge
paths, minimap, saved positions, drag-to-connect with fit feedback, refused
drops, reconnect, edge delete and box selection.
`tests/fullstack/projects.spec.ts` connects built-ins by dragging against
the real backend, checks `/validate` and reloads.

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

## Validate and publish

`features/editor/ValidationPanel.tsx` + `usePublishWorkflow.ts`, at the top
of the editor's side column: Draft → Validate → Publish → immutable version.

- The backend decides both steps. Only the saved draft can be validated or
  published; while the editor has unsaved changes both buttons are off and
  the panel says why. Validate and publish send `expected_revision` (the
  revision the editor shows): a 409 means the draft changed elsewhere and
  the editor offers a reload instead of a result.
- A result belongs to one revision. Any later edit marks it outdated; only
  a current, valid result enables Publish.
- Every issue is listed, grouped by where it points (node, connection,
  trigger, project) with a count per group. Node issues name node and port,
  mark the node on the canvas and in the node list, and jump to it; edge-only
  issues select the edge. Trigger and project issues have no editor surface
  yet and are listed only. The backend reports errors only, no warnings.
- Publish needs a confirmation naming the version number. A publish the
  backend refuses (422) replaces the result with its issues; other failures
  stay in the dialog for a retry. Success shows the version and reloads
  the draft, because publishing advances the draft revision.

Tests: `validation.test.ts`, the "Validate and publish" block of
`EditorPage.test.tsx` (valid, invalid, multiple and node errors, navigation,
unsaved changes, stale draft at validate and at publish, refused and failed
publish) and `tests/fullstack/projects.spec.ts` (validate → fix → publish
against the real backend).

## Project versions

`features/versions/`: `/projects/:id/versions` and
`/projects/:id/versions/:n`. Published versions are shown as what they are:
immutable snapshots, never as an editable draft.

- The list keeps the draft (revision, editor link) in its own card, apart
  from the published versions (number, published at, published by,
  description; the project's `current_version` is marked "Latest").
  Versions come oldest first, as the backend pages them.
- A version page shows its metadata, an "Immutable" badge and a read-only
  notice pointing edits to the draft, its snapshot on a frozen canvas
  (`SnapshotCanvas`: the editor's node cards, pan and zoom only) and as
  tables (nodes with the names of their configured fields but not their
  values, connections, triggers, dependencies, files), a comparison and
  the executions that ran it (`GET /projects/:id/executions?version=n`).
- Comparison (`compare.ts`) diffs a version against another version or the
  current draft (the default for the first version): added, removed and
  changed nodes, connections, triggers, dependencies and files. Node
  positions are layout and ignored; files compare by path and size
  (contents are not served).
- "Restore to draft…" replaces the draft with a copy of the version after
  a confirmation naming the revision it replaces (409 reloads the project),
  then opens the editor. "Clone as new project…" creates an unpublished
  project from the version. Neither changes the version.
- Every execution's version links to its version page (dashboard tables),
  so it is always clear which snapshot a run used.
- Authors show their username (the user id in a tooltip, or alone if the
  account cannot be found).

Tests: `compare.test.ts`, `VersionsPage.test.tsx` (loading, empty,
pagination, API failure, not found, immutable presentation and frozen
canvas, comparison with versions and the draft, restore incl. conflict,
clone, per-version executions, navigation from project and dashboard) and
`tests/fullstack/projects.spec.ts` (publish twice, run v1, inspect,
compare, clone and restore against the real backend).

## Live execution

The execution detail page of an unfinished run subscribes to
`WS /api/v1/executions/{id}/live` (`src/api/live.ts`,
`features/executions/useLiveExecution.ts`). Events are notifications, not
state:

- every (re)subscribe and every events frame invalidates the execution's
  queries (detail, report, events), so the page shows the refetched,
  persisted state; nothing is derived from event payloads;
- the client tracks the highest event `position` it received: it drops
  duplicates and stale events and resubscribes with `after` = that
  position, so the backend replays exactly what was missed;
- a dropped connection retries after 1 s, 2 s, 4 s … (at most 30 s,
  reset on success); 4401 (no or invalid session) and 4404/4400 stop it;
- the indicator says `Connecting`, `Live`, `Reconnecting` (with "may be out
  of date"), `Live updates finished` or `Live updates unavailable`; it
  never says Live without a subscription. Refresh still works throughout;
- "Live activity" lists the events received (last 200) in words per
  backend event type; unknown types show their raw name. "Nodes" lists the
  report's node statuses verbatim;
- events only say when to look again: refetches of the execution, report
  and logs are throttled to one per second (`RECONCILE_INTERVAL_MS`), the
  first immediately; the end of the stream refetches at once.

The subscribe frame carries no token: the backend authenticates the
handshake by the session cookie, for the UI's own origin only (ADR 0002).

Tests drive a hand-controlled `FakeWebSocket` (`src/test/websocket.ts`,
installed for every test, so none reaches a network) and fake timers for
the backoff; no sleeps.

## Execution inspector

`/executions/:id` is the inspector (`features/executions/inspector/`), tabs
in the URL (`?tab=nodes&node=<id>`, shareable). Everything comes from the
backend: the execution, its report (`/reports/executions/{id}`), its pinned
version's graph and its node logs (`/executions/{id}/logs`):

- **Overview**: details, usage/cost, run snapshot (engine and node
  versions, redacted input), live activity while it runs;
- **Graph**: the version's graph with each node's state; a click opens it;
- **Nodes**: node table and the node inspector (status, attempts and
  retries, timing, error, failed attempts by number, outputs, usage, cost
  and the node's logs);
- **Logs**: node log lines, filtered by level, node and message text on the
  backend, 100 per page;
- **Errors**: the execution error and every failed attempt per node;
- **Events**: the report's event timeline, 200 at a time.

Untrusted content (outputs, log messages and fields, error texts, labels)
renders as React text only, never as HTML or Markdown; text over 20,000
characters is cut with a "Show all" toggle. Redaction is the backend's; the
UI shows `[REDACTED]` as sent and stores nothing in browser storage. Node
inputs are not shown: the backend does not persist them (the upstream
outputs are what a node received).

## Monitoring

`/monitoring` (`features/monitoring/`): the runs created in a time window,
from `GET /monitoring/summary?since=&until=` (at most 31 days; the
backend aggregates persisted executions, not Prometheus counters). The range
(last hour, 24 hours, 7 days or custom) lives in the URL; a preset is
anchored when picked, and Refresh moves it to now.

- Headline stats (success and failure rate over finished runs, with the
  denominator), a stacked timeline with a table carrying the same numbers,
  durations per outcome, top error codes and per-node-type performance
  (completions, failures, retries, durations, tokens, cost per currency).
- "Right now" (status counts) and system status are current state, not the
  window.
- `model.ts` only formats and mirrors the backend's custom-range rules for
  field errors; every figure is the backend's. Data the backend does not
  have (trigger breakdown, provider usage, queue depth) is not shown.

## Reports

`/reports` (`features/reports/`) lists finished executions (completed,
failed, timed out, cancelled), newest first. It uses the paginated
`GET /executions?status=` list; a report exists for every execution, so no
separate list endpoint is needed. Each row links to its report and its
inspector. `/reports/:executionId` renders `GET /reports/executions/{id}`
alone: summary (status, project, version, trigger, timestamps, the
backend's `duration_ms`), usage and cost, nodes (status, attempts,
duration, tokens, cost, outputs on demand), errors, run snapshot and the
event timeline. It reuses the inspector panels. Logs are not part of the
report; the page links to the inspector's Logs tab. The inspector header
links back to the report.

- Nothing is computed in the browser: every figure is the report's own.
- **Download JSON** fetches the report again and saves the response body
  byte for byte (`ApiClient.requestWithBody`), after schema validation. The
  file is the backend's canonical export (sorted keys, deterministic),
  including fields this UI does not know yet; a malformed export is a toast,
  never a file. The file name keeps only `[A-Za-z0-9_-]` of the id.
- The same untrusted-text rules as the inspector apply: React text only,
  long text cut at 20,000 characters, `[REDACTED]` shown as sent.

## Credentials

`/credentials` (`features/credentials/`, admin only) lists credential
metadata: name, type, description, the `credential:<name>` reference nodes
use, and when it changed. Create, edit (description; rotate the value) and
delete (confirmed) go through `/api/v1/credentials`. Name and type are the
identity and cannot be edited. Members get "Admin access required"; their
projects still reference credentials by name in the node inspector.

Secret handling: the API never returns a value, so the UI has none to show
(the value column is a fixed mask). The value is typed into an uncontrolled
`type="password"` input (`autocomplete="new-password"`, spellcheck off),
read from `FormData` at submit, sent only in the JSON body, and never held in
React state. The mutation's variables are cleared with `reset()` on close and
evicted by `gcTime: 0`; the dialog content unmounts. On edit an empty value
field is not sent (keep). `CredentialsPage.test.tsx` checks that the value is
in none of DOM, inputs, browser storage, URL, request URLs, console output
or the query/mutation cache after create, failed create and rotate.

Not shown: which projects use a credential; the backend has no usage
endpoint yet.

## Triggers

`/projects/:projectId/triggers` (`features/triggers/`, linked from the
project page) lists the draft's triggers: type, id, configuration, the
invoke or webhook endpoint, status and the backend's validation verdict.
Create, edit, enable/disable and delete (confirmed) all `PUT` the whole
draft with `expected_revision`, so a stale page gets a 409 and is reloaded
instead of overwriting newer work. There is no separate trigger API: the
backend stores triggers on the draft (architecture §125).

- Status is read from backend state: Enabled/Disabled from the draft;
  Published / Unpublished changes / Not published by comparing the draft
  with the latest published version, which is the only one that fires.
  Triggers removed from the draft but still live in that version are listed.
- Validation is `POST /validate` pinned to the shown revision
  (`projectQueries.validation`, keyed by revision, so every save re-checks).
  Cron syntax, interval range and webhook secrets are decided there, never
  in the browser. Structural errors (empty cron, interval ≤ 0) are the
  PUT's 422 and stay in the dialog.
- All six backend types can be created: manual, API, cron (5 fields, UTC),
  interval (1 s to 1 year), webhook and event.
- Event triggers pick an event (`execution.completed|failed|cancelled|
timed_out`, `project.published`) and a source: this project, or any
  other project the caller can access. The backend fires only if the
  trigger project's owner can access the source (an admin any project, a
  member only their own).
  Stored values the pickers do not offer are shown as "Unsupported" or
  "Unavailable" rather than silently replaced. The backend dispatcher
  fires them and refuses chains deeper than
  `AKASHA_LIMIT_EVENT_TRIGGER_DEPTH` (architecture §129).
- Manual triggers whose published copy is enabled have a Run button
  (`POST …/invoke`). It opens the new execution; a refusal is a toast.
- Webhooks: the signing secret is a credential reference
  (`credential:<name>`, chosen with the shared `CredentialInput`). The page
  shows the URL and the `X-Akasha-Signature: sha256=<hex>` scheme, never a
  value. A config holding a non-reference is shown as "not a credential
  reference" and its input stays empty.
- Unknown config keys are kept on edit. The type of an existing trigger is
  fixed: delete it and create a new one instead.

## Plugins

`/plugins` (`features/plugins/`, admin only) lists installed plugins from
`GET /plugins`: state, version, engine/SDK constraints, dependencies, node
types and, for a failed plugin, its reason. Plugins are executable code, so a
trust warning is always shown (ADR 0001).

- Enable, disable and remove go through the backend after a confirmation;
  a removal that projects block can be forced behind a second confirmation.
  Compatibility, validity and in-use are the backend's answer, shown with
  its error details.
- Install uploads a `.zip` only when the operator turned on
  `AKASHA_PLUGINS_HTTP_INSTALL` (`install_enabled` in the list); otherwise
  the page points to the CLI.
- Plugin metadata is untrusted and rendered as plain text only.

## Authentication

The backend owns the session (architecture §59, ADR 0002). The UI signs in
with `POST /auth/login` and `"cookie": true`, so the session token goes into
an `HttpOnly`, `SameSite=Strict` cookie and never reaches JavaScript: no
token in state, storage, URLs or logs. Every request sends `X-Akasha-CSRF`,
the header without which the backend ignores the cookie (CSRF). The live
WebSocket is authenticated by the same cookie, same origin only.

- **Session state** (`features/auth/session.ts`): one TanStack query,
  `['auth', 'session']` (`GET /auth/session`), is the only place the UI
  knows who is signed in: `active` (user, absolute `expires_at`) or
  `anonymous` (`expired` when this tab's session ended). A reload restores
  the session from the cookie. A failed check (backend down, 5xx) shows an
  error with Try again, not the login page.
- **Session end**: any 401 except a wrong password (`INVALID_CREDENTIALS`),
  from whichever request, calls `ApiClient`'s `onSessionEnded`, which ends
  the UI session. This covers expiry, revocation, logout in another tab and
  password changes elsewhere.
- **Protected routes**: `RequireSession` wraps the whole shell. Without a
  session it redirects to `/login` and passes the requested location in
  history state, never a `?next=` parameter, so a crafted link cannot
  redirect elsewhere. After the redirect it drops every cached query except
  the session, so the next user never sees the previous user's data.
  Hiding routes is UX: the backend refuses every request without a
  session anyway.
- **Login** (`/login`): backend messages for wrong credentials (the same
  message for unknown users) and throttling (429). After sign-in the user
  returns to the requested page. The password lives only in the form and in
  mutation variables, which are dropped once the form unmounts
  (`gcTime: 0`).
- **Logout** (sidebar): `POST /auth/logout` revokes the session and expires
  the cookie. If logout fails for any reason other than an already dead
  session, the UI stays signed in and says so: the cookie would still work.
- **Settings** (`/settings`): account (username, role, created, session
  expiry), change password (other sessions are revoked; a wrong current
  password is a field error, not a sign-out), theme (system/light/dark),
  engine status (the dashboard's System card). Not included: user
  management for admins (backend `/users` exists) and the first-admin setup
  (an operator step, `docs/deployment.md`).

## Security

The threat model: a self-hosted UI whose data (node outputs, LLM text,
logs, plugin metadata, error messages) is untrusted, served on the same
origin as the API. The backend authorizes every request, so hiding a button
is never a control.

- **Rendering**: all data is rendered as React text. There is no Markdown or
  HTML rendering, and ESLint rejects `dangerouslySetInnerHTML`, `innerHTML`,
  `outerHTML`, `insertAdjacentHTML`, `document.write`, `eval` and
  `new Function` in production code.
- **Content Security Policy**: the production `index.html` gets a strict
  policy (`vite.config.ts`): scripts, styles and requests from its own origin
  only, no inline code. Every E2E spec fails on a CSP violation
  (`tests/e2e/fixtures.ts`), and `security.spec.ts` proves that injected
  inline scripts do not run. Framing is forbidden by the proxy header
  (`docs/deployment.md`).
- **URLs**: links from data (plugin documentation) must be `http(s)`. Ids
  in API paths are encoded, and `ApiClient` refuses `.`/`..` segments, so a
  crafted route cannot redirect a request to another endpoint. Login
  returns only to in-app paths taken from history state.
- **Storage**: only the theme is stored (`localStorage`); ESLint rejects
  storage anywhere else. Secrets stay in uncontrolled inputs and in
  `gcTime: 0` mutations (Credentials, Authentication).
- **Downloads**: report exports are saved as `application/json` blobs with a
  sanitized file name. **Uploads**: the plugin package is size-checked in the
  browser for UX; the backend validates it.
- **Errors**: users see backend envelope messages (redacted server-side) or
  fixed fallbacks, never stack traces or raw responses.
- **Dependencies**: `npm audit` before a release.

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
classes. Each token is `light-dark(light, dark)`: the root's `color-scheme`
follows the OS, and `data-theme="light"|"dark"` on `<html>` (Settings,
stored in localStorage as `akasha.theme`) pins it. Every interactive element
uses the `focus-ring` utility.

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
