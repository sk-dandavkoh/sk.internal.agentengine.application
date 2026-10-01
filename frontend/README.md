# Akasha Frontend

React + TypeScript single-page app for the Akasha Agent Engine. Rules live in
[`AGENTS.md`](AGENTS.md); the phase plan in
[`../docs/roadmap-frontend.md`](../docs/roadmap-frontend.md).

## Requirements

- Node.js `^22.22.0 || >=24` (`.nvmrc` pins 24)
- npm (the committed `package-lock.json` is authoritative; `.npmrc` saves exact versions)

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
│   ├── styles/         global CSS (Tailwind v4 entry)
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
`components/` (shared UI), `features/<domain>/`, `api/`, `hooks/`, `lib/`,
`schemas/`, `types/`. Unit and component tests sit next to the code as
`*.test.ts(x)`.

## Decisions

- **Tailwind v4 via `@tailwindcss/vite`**: no `tailwind.config` or PostCSS
  config; theme tokens go in CSS when Phase 02 needs them.
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
  through TanStack Query, added at the app root together with the API client
  (Phase 03/04). Feature and editor state stay inside their feature subtree.
  There is no global client store.
