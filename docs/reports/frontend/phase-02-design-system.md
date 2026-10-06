# Phase 02 — Design System + UI Foundation

## 1. Executive Summary

Phase 02 (`/docs/roadmap-frontend.md` §8) was meant to build one consistent, reusable UI foundation. Later features should be able to assemble their screens mainly from shared primitives instead of styling each screen from scratch.

**What was implemented:**

- **Design tokens.** Semantic color tokens are defined in `frontend/src/styles/index.css` (Tailwind v4 `@theme`):
  - a dark theme that follows `prefers-color-scheme`
  - a single `focus-ring` utility for keyboard focus
  - a base layer: body colors, scroll lock while a modal is open, the dialog backdrop
- **Primitives.** 20 primitives in the new directory `frontend/src/components/ui/`, exported through `index.ts`:
  - actions: `Button`, `TextLink`
  - form fields: `Input`, `Textarea`, `Select`, `Checkbox`, `Switch`
  - overlays: `Dialog`, `ConfirmDialog`, `DropdownMenu`, `Tooltip`
  - layout and structure: `Tabs`, `Card`, `Table`, `PageHeader`
  - feedback: `Badge`, `Alert`, toast notifications (`ToastProvider` + `useToast`)
  - async states: `LoadingState`, `Skeleton`, `EmptyState`, `ErrorState`
- **Native platform elements first.** The browser's own elements do most of the work: `<dialog>` with `showModal()`, the native `<select>`, and native checkboxes (`role="switch"` for `Switch`). The keyboard models for menus and tabs are written by hand, following the WAI-ARIA Authoring Practices Guide (APG).
- **No runtime dependency was added.** One devDependency was added: `@testing-library/user-event` `14.6.7`.
- **Shell migration.** The Phase 01 shell now uses the primitives. Open Issue O4 from Phase 01 (focus on route change) is closed.
- **Catalog page.** An unlisted route `/dev/ui` (`UiCatalogPage`) is the visual reference for every primitive. It is also the page the real-browser E2E tests run against.
- **Documentation.** Conventions are documented in `frontend/README.md`, in a new "Design system" section.

**Resulting state:**

- 65 Vitest tests pass in 12 files; 49 of them are new.
- 11 Playwright tests pass against the production build; 8 of them are new.
- Format, lint, typecheck, build and `npm audit` all pass.
- All five roadmap acceptance criteria are PASS (§14).
- Remaining limitations are listed in §20. The main one: overlays can be clipped inside `overflow` containers. None of them blocks the next phase.

**Phase Status:** COMPLETE WITH LIMITATIONS

---

## 2. Phase Objective

**Roadmap §8, goal:** "Eine konsistente UI-Basis schaffen." ("Create a consistent UI foundation.")

**Roadmap scope:**

- typography and spacing
- buttons, inputs, selects, checkbox, switch
- tabs, cards, dialogs, dropdowns, tooltips
- badges, alerts, tables
- empty states, loading skeletons, toasts
- confirmation dialogs, status indicators
- all delivered as reusable components

**Additional requirements from the task prompt:**

- forms, notifications, error states
- sizing and layout conventions
- interaction states, focus states, responsive behavior
- design tokens
- accessibility for every primitive: keyboard use, visible focus, semantic HTML, labels and accessible names, screen-reader behavior, disabled and loading states
- component tests for rendering, interaction, keyboard use, disabled states, error states and accessibility-relevant behavior

**The roadmap prompt restricts this phase to generic UI components:** "Implementiere nur generische UI-Komponenten. Keine fachlichen Workflow-, Node-, Execution- oder Backend-Features." ("Implement only generic UI components. No workflow, node, execution or backend features.")

**Explicitly not in this phase:**

- the API client (Phase 03)
- domain types and server state (Phase 04)
- every feature screen (Phase 05 onward)
- a theme toggle (Settings, Phase 21)
- the full accessibility hardening pass (Phase 25)

---

## 3. Scope Implemented

### Design tokens and global styles (`src/styles/index.css`)

**Semantic color tokens (`@theme`):**

| Group      | Tokens                                                                          |
| ---------- | ------------------------------------------------------------------------------- |
| Surfaces   | `canvas`, `surface`, `surface-muted`, `border`, `border-strong`                 |
| Text       | `fg`, `muted`                                                                   |
| Focus      | `focus`                                                                         |
| Actions    | `primary`, `primary-hover`, `on-primary`, `danger`, `danger-hover`, `on-danger` |
| Tone pairs | `{neutral,info,success,warning,danger}-{soft,text}`                             |

Light values map to Tailwind's slate, indigo, red, sky, emerald and amber palettes.

**Dark theme.** All tokens are redefined under `@media (prefers-color-scheme: dark)` for `:root:not([data-theme='light'])`, and `color-scheme: dark` is set. Setting `data-theme="light"` on `<html>` opts out.

**`@utility focus-ring`.** Draws a 2 px `--color-focus` outline with a 2 px offset on `:focus-visible`.

**Base layer:**

- `body` gets `bg-canvas font-sans text-fg antialiased`.
- `html` gets `scrollbar-gutter: stable`.
- `html:has(dialog[open])` gets `overflow: hidden`, which locks background scroll while a modal is open.
- `::backdrop` is a 50 % slate tint.

### Primitives (`src/components/ui/`)

| Primitive                                              | File                                      | Behavior                                                                                                                                                                                                                                                                                                                        |
| ------------------------------------------------------ | ----------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `Button`                                               | `Button.tsx`                              | Variants `primary`, `secondary` (default), `ghost`, `danger`; sizes `md` (40 px) and `sm` (32 px). Defaults to `type="button"`. `loading` sets `aria-disabled` and `aria-busy`, shows a spinner and blocks click and submit, while the button stays focusable. Accepts `ref` (React 19 `ComponentProps<'button'>`).             |
| `Field` (internal)                                     | `Field.tsx`                               | Label, an optional required asterisk (`aria-hidden`), a description and an error message.                                                                                                                                                                                                                                       |
| `Input`, `Textarea`, `Select`                          | `Input.tsx`, `Textarea.tsx`, `Select.tsx` | Native controls wrapped in `Field`. `useId` provides the ID, `aria-describedby` points to the description and error, and an error sets `aria-invalid`. `Select` takes `<option>` children.                                                                                                                                      |
| `Checkbox`, `Switch`                                   | `Checkbox.tsx`                            | A native checkbox. `Switch` is the same component with `role="switch"` and a track-and-thumb style drawn with `::before`. Optional description.                                                                                                                                                                                 |
| `Dialog`                                               | `Dialog.tsx`                              | Native modal `<dialog>`. Props: `open`, `onClose`, `title` (becomes `aria-labelledby`), `description` (becomes `aria-describedby`), `footer`, `dismissible`, `size` (`sm`/`md`/`lg`). Content renders only while open. `[data-autofocus]` sets the initial focus.                                                               |
| `ConfirmDialog`                                        | `ConfirmDialog.tsx`                       | Built on `Dialog`. Props: `title`, consequence text as children, `confirmLabel`, `tone` (default `danger`), `irreversible` (adds "This cannot be undone."), `pending`, `error`. Initial focus goes to Cancel.                                                                                                                   |
| `DropdownMenu`                                         | `DropdownMenu.tsx`                        | APG menu button. Items are `{label, onSelect, disabled?, tone?: 'danger'}`. Options: `variant`, `align`.                                                                                                                                                                                                                        |
| `Tooltip`                                              | `Tooltip.tsx`                             | Uses `cloneElement` to add `aria-describedby` to a single child. Shows on hover and focus; hides on Escape, blur and pointer leave.                                                                                                                                                                                             |
| `Tabs`                                                 | `Tabs.tsx`                                | Data-driven (`items: {id, label, content}[]`), with an accessible name via `label`. Supports uncontrolled use (`defaultValue`) and controlled use (`value` + `onValueChange`).                                                                                                                                                  |
| `Card`                                                 | `Card.tsx`                                | `<section>` with optional `title` (heading level via `titleAs`, `h2` by default), `description` and `actions`.                                                                                                                                                                                                                  |
| `Table`                                                | `Table.tsx`                               | Generic `Table<Row>` with `caption` (optionally `hideCaption`), `columns` (`{key, header, cell, align}`), `rows`, `rowKey` and `empty`. Wrapped in `overflow-x-auto`.                                                                                                                                                           |
| `Badge`                                                | `Badge.tsx`                               | Five tones; optional `dot`, which serves as the status indicator.                                                                                                                                                                                                                                                               |
| `Alert`                                                | `Alert.tsx`                               | Four feedback tones, each with an icon and a screen-reader-only prefix (`Info:`, `Success:`, `Warning:`, `Error:`). `danger` uses `role="alert"`; the others use `role="status"`. Optional `title` and `action`.                                                                                                                |
| `ToastProvider`, `useToast`                            | `ToastProvider.tsx`, `toastContext.ts`    | `notify({tone, title, description}) → id` and `dismiss(id)`. Details in §8.                                                                                                                                                                                                                                                     |
| `LoadingState`, `Skeleton`, `EmptyState`, `ErrorState` | `states.tsx`                              | `LoadingState` is `role="status"` with a spinner and a label. `Skeleton` is `aria-hidden` and pulses (no animation under `motion-reduce`). `EmptyState` and `ErrorState` show a title (heading level via `titleAs`), a description and an action; `ErrorState` is `role="alert"` with the default title "Something went wrong". |
| `PageHeader`                                           | `PageHeader.tsx`                          | The page's single `h1`, plus a description and actions.                                                                                                                                                                                                                                                                         |
| `TextLink`                                             | `TextLink.tsx`                            | A styled React Router `Link`.                                                                                                                                                                                                                                                                                                   |

Supporting files:

- `icons.tsx`: decorative inline SVG icons (`aria-hidden`, `focusable="false"`) and the `Spinner`.
- `shared.ts`: tone class maps, feedback tone metadata, `controlClasses(invalid)` and `fieldDescribedBy()`.
- `src/lib/cx.ts`: a class-name join helper.

### Application shell (`src/app/`)

| File                     | Change                                                                                                                                                                                                                                                                                                                             |
| ------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `App.tsx`                | Wraps `RouterProvider` in `ToastProvider`.                                                                                                                                                                                                                                                                                         |
| `AppLayout.tsx`          | Uses tokens. Active nav item: `bg-surface-muted font-semibold text-fg`, plus `aria-current` from `NavLink`. All links use `focus-ring`. The skip link uses tokens. The pending bar uses `bg-primary`. `<main>` is capped at `max-w-6xl` with padding `p-4` / `sm:p-6`. **New:** focus moves to `<main>` when the pathname changes. |
| `AppLoading.tsx`         | Renders `LoadingState`.                                                                                                                                                                                                                                                                                                            |
| `PlaceholderPage.tsx`    | Renders `PageHeader`.                                                                                                                                                                                                                                                                                                              |
| `NotFoundPage.tsx`       | Renders `PageHeader` and `TextLink`.                                                                                                                                                                                                                                                                                               |
| `RouteErrorBoundary.tsx` | Renders `ErrorState titleAs="h1"` with a `Button` labelled "Reload".                                                                                                                                                                                                                                                               |
| `routes.tsx`             | New lazy route `/dev/ui` → `UiCatalogPage`. It sits inside the content error boundary and is not in `navItems`.                                                                                                                                                                                                                    |
| `UiCatalogPage.tsx`      | New. Shows every primitive in its states, with static demo data.                                                                                                                                                                                                                                                                   |

### Testing

- 8 new Vitest files in `src/components/ui/` (48 tests), plus 1 new test in `src/app/AppLayout.test.tsx`.
- `src/test/setup.ts`: a jsdom stand-in for `HTMLDialogElement.showModal` and `close`.
- New Playwright spec `tests/e2e/ui.spec.ts` (8 tests).

---

## 4. Files Changed

| File                                                                                                                                                                                   | Change               | Reason                                                                                                                                                          |
| -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `frontend/src/components/ui/` (directory)                                                                                                                                              | Created              | Shared UI layer (`frontend/AGENTS.md` §6, §16, §96)                                                                                                             |
| `frontend/src/components/ui/{Alert,Badge,Button,Card,Checkbox,ConfirmDialog,Dialog,DropdownMenu,Field,Input,PageHeader,Select,Table,Tabs,Textarea,TextLink,ToastProvider,Tooltip}.tsx` | Created              | The primitives (§3)                                                                                                                                             |
| `frontend/src/components/ui/states.tsx`                                                                                                                                                | Created              | Loading, skeleton, empty and error states                                                                                                                       |
| `frontend/src/components/ui/icons.tsx`                                                                                                                                                 | Created              | Decorative icons and the spinner; avoids an icon dependency                                                                                                     |
| `frontend/src/components/ui/shared.ts`                                                                                                                                                 | Created              | Tone maps, control classes, `fieldDescribedBy`. These are non-component exports, kept apart from `.tsx` files to satisfy `react-refresh/only-export-components` |
| `frontend/src/components/ui/toastContext.ts`                                                                                                                                           | Created              | Toast context and the `useToast` hook (same lint reason)                                                                                                        |
| `frontend/src/components/ui/index.ts`                                                                                                                                                  | Created              | Single import surface for the primitives                                                                                                                        |
| `frontend/src/components/ui/{Button,fields,Dialog,DropdownMenu,Tabs,Tooltip,ToastProvider,display}.test.tsx`                                                                           | Created              | Component tests (§12)                                                                                                                                           |
| `frontend/src/lib/` (directory), `frontend/src/lib/cx.ts`                                                                                                                              | Created              | Class-name join helper                                                                                                                                          |
| `frontend/src/app/UiCatalogPage.tsx`                                                                                                                                                   | Created              | Visual reference and E2E target                                                                                                                                 |
| `frontend/tests/e2e/ui.spec.ts`                                                                                                                                                        | Created              | Real-browser checks of native behavior                                                                                                                          |
| `frontend/src/styles/index.css`                                                                                                                                                        | Modified (+95 lines) | Tokens, dark theme, `focus-ring`, base layer                                                                                                                    |
| `frontend/src/test/setup.ts`                                                                                                                                                           | Modified             | jsdom stand-in for `showModal`/`close` (F1)                                                                                                                     |
| `frontend/src/app/App.tsx`                                                                                                                                                             | Modified             | Mounts `ToastProvider`                                                                                                                                          |
| `frontend/src/app/AppLayout.tsx`                                                                                                                                                       | Modified             | Tokens, focus on navigation (O4), content width                                                                                                                 |
| `frontend/src/app/AppLayout.test.tsx`                                                                                                                                                  | Modified             | New test for focus on navigation                                                                                                                                |
| `frontend/src/app/{AppLoading,NotFoundPage,PlaceholderPage,RouteErrorBoundary}.tsx`                                                                                                    | Modified             | Migrated to the primitives                                                                                                                                      |
| `frontend/src/app/routes.tsx`                                                                                                                                                          | Modified             | Adds the `/dev/ui` lazy route                                                                                                                                   |
| `frontend/package.json`, `frontend/package-lock.json`                                                                                                                                  | Modified             | `@testing-library/user-event` `14.6.7`. The lockfile kept 2-space indentation, giving a +15-line diff (F5)                                                      |
| `frontend/README.md`                                                                                                                                                                   | Modified             | Structure section and new "Design system" section                                                                                                               |
| `docs/reports/frontend/phase-02-design-system.md`                                                                                                                                      | Created              | This report                                                                                                                                                     |

- **Totals:** tracked files 13 changed, +275 / −58. New source and test files: 2,856 lines in total, of which `UiCatalogPage.tsx` is 334 and `ui.spec.ts` is 113.
- **Deleted files:** none.
- **Not part of this phase:** migrations, generated files and environment variables.
- **Build output:** `frontend/dist/` and `frontend/test-results/` are git-ignored.
- **Temporary tooling:** I created a `.claude/launch.json` in the repo root for a dev-server preview and deleted it again. It does not exist in the tree.

---

## 5. Architecture Changes

No changes were made to `/docs/architecture.md` or `/docs/vision.md`. The decisions below concern the frontend's internal structure.

### 5.1 Native platform elements instead of a headless UI library

- **Decision:** Build overlays and form controls on browser-native elements, and write the keyboard models for menus and tabs by hand.
- **Context:** Dialogs, menus, tabs and tooltips need correct focus handling and keyboard behavior. `frontend/AGENTS.md` §3 requires a clear architectural benefit before adding any dependency.
- **Chosen approach:**
  - `Dialog`: `<dialog>.showModal()`.
  - Form fields: native `<select>` and native checkboxes, with `role="switch"` for `Switch`.
  - `DropdownMenu` and `Tabs`: the APG keyboard model, implemented in about 120 lines each.
- **Alternatives considered:** Radix UI or React Aria (a headless library), and a hand-built modal with portal, focus trap and `inert`.
- **Reason:**
  - The native modal dialog provides the top layer, an inert background, Escape handling and focus return with no extra code.
  - Menus and tabs are small enough to own, and they are covered by tests.
  - No runtime dependency is needed.
- **Consequences:**
  - jsdom cannot run native dialogs, so a test stand-in is needed (F1), and native behavior is tested in Playwright.
  - Overlays have no collision handling or top-layer positioning (§20 L1).
  - A future Combobox or searchable select would be a larger build. That is when a library would become worth re-evaluating.

### 5.2 Semantic tokens as the only color vocabulary

- **Decision:** Components use only semantic token utilities (`bg-surface`, `text-muted`, …), never raw palette classes.
- **Context:** The roadmap asks for dark and light themes to be prepared or supported, and for consistent UI.
- **Chosen approach:**
  - Tokens live in `@theme` and point to Tailwind palette variables.
  - The dark theme overrides the same variables in a media query.
- **Alternatives considered:** Tailwind's `dark:` variant on every component.
- **Reason:**
  - A theme becomes a variable swap in one file.
  - Components do not repeat dark-mode classes.
- **Consequences:**
  - New UI must use tokens.
  - A manual toggle (Phase 21) only needs to set `data-theme`. Forcing dark on a light OS would need one more selector: `:root[data-theme='dark']`.

### 5.3 Toasts are the only global UI context

- **Decision:** `ToastProvider` sits at the app root, above the router. Nothing else is global.
- **Context:** The guidelines limit Context use (`frontend/AGENTS.md` §97/§98). Notifications are needed app-wide.
- **Chosen approach:** One context provides `{notify, dismiss}`. The value is memoized and holds no toast state, so consumers do not re-render when toasts change.
- **Alternatives considered:** A global store, or an event emitter.
- **Reason:** It is the smallest mechanism that works, and it is testable with `renderHook`.
- **Consequences:** Phase 03/04 mutations can call `useToast().notify` anywhere inside `App`.

### 5.4 Design-system catalog route included in the production build

- **Decision:** `/dev/ui` is a lazy route in every build. It is not listed in the navigation.
- **Context:** Native dialog, focus and menu behavior can only be verified in a real browser. The Phase 00 decision requires E2E tests to run against the production build.
- **Alternatives considered:**
  - A route only in development (`import.meta.env.DEV`): E2E could not reach it.
  - A flag enabled only at build time: this would be a feature flag that needs an owner and a removal plan.
  - Storybook, or Playwright component testing: new dependencies.
- **Reason:** It costs nothing on initial load (a separate 5.12 kB chunk), and the page contains no data or API calls.
- **Consequences:** The route is publicly reachable. Remove it, or gate it behind authentication, if that becomes unwanted (Phase 21/27).

### 5.5 Focus moves to `<main>` after navigation

- **Decision:** `AppLayout` focuses `<main>` (which has `tabIndex={-1}`) whenever `location.pathname` changes.
- **Context:** This was Open Issue O4 from Phase 01.
- **Chosen approach:** A ref compares the previous pathname with the current one. This avoids the extra mount effect that StrictMode runs in development.
- **Alternatives considered:** Focusing the page's `h1`.
- **Reason:** `<main>` always exists. Every page also has an `h1`, but no component guarantees that.
- **Consequences:** Focus no longer moves on search or hash changes. That is intended, because those are not page changes.

---

## 6. Domain / Data Model Changes

Not applicable. No domain types, schemas, DTOs or persisted data were added or changed.

---

## 7. API / Contract Changes

No backend or frontend–backend contract changes. There are no endpoints, events or schemas in this phase.

The new internal frontend contract is the public surface of `src/components/ui/index.ts`:

- **Components:** `Alert`, `Badge`, `Button`, `Card`, `Checkbox`, `Switch`, `ConfirmDialog`, `Dialog`, `DropdownMenu`, `Input`, `PageHeader`, `Select`, `EmptyState`, `ErrorState`, `LoadingState`, `Skeleton`, `Table`, `Tabs`, `Textarea`, `TextLink`, `ToastProvider`, `Tooltip`
- **Hook:** `useToast`
- **Types:** the prop types, plus `ButtonVariant`, `MenuItem`, `TabItem`, `TableColumn`, `ToastApi`, `ToastOptions`, `Tone`, `FeedbackTone`

Compatibility: the surface is new and has no consumers yet beyond `src/app/`.

---

## 8. Implementation Details

- **`Button` loading.**
  - `loading` sets `aria-disabled` instead of `disabled`, so the focused button keeps focus. With `disabled`, focus would drop to `<body>`.
  - The click handler calls `preventDefault()` and returns. This also stops a `type="submit"` button from submitting its form, so a form cannot be submitted twice (`frontend/AGENTS.md` §92).
- **`Dialog` lifecycle.**
  - A `useEffect` on `open` calls `showModal()` or `close()`.
  - The `open` prop stays the source of truth. The native `close` event calls `onClose` only while `open` is still `true`. That means a close the browser initiates, such as Escape, is reported, while the `close()` triggered by the parent setting `open=false` is not reported back.
  - `dismissible={false}` calls `preventDefault()` on `cancel` and hides the close button.
  - Initial focus: after `showModal()`, the element marked `[data-autofocus]` is focused. React's `autoFocus` would not work here, because it runs during commit, before the dialog is shown, and React does not render an `autofocus` attribute that the native dialog could pick up.
  - Content mounts only while open, so a form inside always starts fresh.
- **`ConfirmDialog` ownership.** The caller passes `pending` and `error` (for example, from a mutation) and closes the dialog on success. The dialog holds no promise state. While pending:
  - confirm shows the loading state
  - Cancel is disabled
  - the dialog cannot be dismissed
- **`DropdownMenu` keyboard model.**
  - On the trigger, ArrowDown and ArrowUp open the menu with focus on the first or last enabled item. A `pendingFocus` ref carries that choice into the effect that runs after rendering.
  - Inside the menu:
    - ArrowDown and ArrowUp wrap; Home and End jump to the ends.
    - Escape closes the menu and focuses the trigger.
    - Tab closes the menu and lets focus move on naturally.
    - Enter, Space or a click closes the menu, focuses the trigger, then calls `onSelect`.
  - Disabled items carry the native `disabled` attribute and are excluded from navigation through the selector `[role="menuitem"]:not(:disabled)`.
  - A `pointerdown` listener on `document` closes the menu on any click outside.
- **`Tabs`.**
  - Roving `tabIndex` (0 on the selected tab, −1 on the others).
  - Arrow keys use automatic activation: moving focus also selects the tab.
  - Only the selected panel renders, and it has `tabIndex={0}`.
  - `aria-controls` is set only on the selected tab, so it never points at a panel that is not rendered.
- **`Tooltip`.**
  - The trigger always carries `aria-describedby`. The hidden tooltip text still counts as the description, because the accessible-name calculation includes referenced hidden content.
  - Hover listeners sit on the wrapper, so the pointer can move onto the tooltip without it disappearing (WCAG 1.4.13).
  - A `keydown` listener on `document` handles Escape while the tooltip is open.
- **Toasts.**
  - The `<section aria-label="Notifications" aria-live="polite">` is always mounted, so screen readers announce toasts that are added later.
  - Error toasts use `role="alert"`; the others use `role="status"`.
  - At most 4 toasts are kept (`MAX_TOASTS`); the oldest is dropped first.
  - Auto-dismiss:
    - `info` and `success` close after 5000 ms (`TOAST_DURATION_MS`).
    - The timer pauses while the pointer or focus is on the toast (WCAG 2.2.1).
    - `warning` and `danger` stay until dismissed.
  - IDs come from a monotonically increasing ref counter.
  - `useToast` throws outside the provider.
- **Information is never conveyed by color alone.**
  - Alerts and toasts: an icon plus a screen-reader-only prefix.
  - Badges: always a text label.
  - Switch: the thumb changes position as well as color.
  - Invalid fields: error text plus `aria-invalid`.
  - Active nav item: font weight plus `aria-current`.
- **Field wiring.** `fieldDescribedBy(id, {description, error})` returns `"<id>-description <id>-error"`, or `undefined` when neither is set.
- **Styling rules.** `cx()` only joins class names and does not resolve Tailwind conflicts. Therefore `className` on a primitive is documented as layout-only, and primitives never accept color overrides. The ghost button does not set a text color, so it inherits the surrounding color; this matters inside tinted toasts.

---

## 9. Findings

### F1 — jsdom does not implement `HTMLDialogElement.showModal`, `close` or the Popover API

- **Impact:** MEDIUM
- **Resolution:** WORKAROUND
- **Notes:**
  - `jsdom/lib/jsdom/living/nodes/HTMLDialogElement-impl.js` is an empty implementation.
  - `src/test/setup.ts` now installs a stand-in: `showModal()` sets `open = true`, and `close()` sets `open = false` and dispatches `close`.
  - Unit tests simulate Escape by dispatching `cancel` followed by `close()`.
  - The real native behavior (`:modal`, the inert background, Escape, focus return) is verified in `tests/e2e/ui.spec.ts`.
  - This finding is why I did not use the Popover API for menus and tooltips.

### F2 — React Testing Library's async `act` wrapper hangs under Vitest fake timers

- **Impact:** LOW
- **Resolution:** WORKAROUND
- **Notes:**
  - With `vi.useFakeTimers()`, every `user-event` interaction timed out after 5 s.
  - I tried the user-event options `advanceTimers` and `delay: null`, and limiting `toFake`. None helped.
  - Likely cause, from the symptoms (I did not trace it in the RTL source): the RTL wrapper waits on a `setTimeout` that the fake timers freeze, because it only recognizes Jest fake timers.
  - The toast timer tests now use synchronous `fireEvent`. A comment in the test file explains why.

### F3 — Chrome lets Tab move from a modal `<dialog>` into the browser UI

- **Impact:** LOW
- **Resolution:** No fix needed; this is platform behavior.
- **Notes:**
  - The first E2E draft asserted that focus always stays inside the dialog, and it failed.
  - Native modal dialogs keep the page inert but allow focus to reach the browser chrome.
  - The test now asserts what actually matters: focus never lands on page content outside the dialog (`:focus:not(dialog, dialog *)` count is 0), and focus cycles back into the dialog.

### F4 — Chrome may close a non-dismissible dialog on repeated Escape

- **Impact:** LOW
- **Resolution:** WORKAROUND, by design
- **Notes:**
  - I did not reproduce this. It is a known Chrome close-watcher anti-abuse rule.
  - If it happens, the `close` event still calls `onClose`, so the owner's state stays consistent.
  - The confirmed action keeps running. Its result is handled by the caller, for example by a mutation.

### F5 — `npm install` re-indented `package-lock.json` (Phase 01 O2, again)

- **Impact:** LOW
- **Resolution:** WORKAROUND
- **Notes:**
  - Installing `user-event` produced a diff of about 9,000 lines.
  - I rewrote the lockfile with its original 2-space JSON indentation, using `JSON.stringify(..., null, 2)`. The diff dropped to +15 lines.
  - O2 itself remains open.

### F6 — The browser-pane screenshot tool does not capture the top layer

- **Impact:** LOW (tooling only)
- **Resolution:** WORKAROUND
- **Notes:**
  - Screenshots in the browser pane never showed the open dialog.
  - Querying the DOM confirmed the dialog was rendered and `:modal`.
  - For the visual check I used Playwright screenshots, which do show the dialog and toasts.

---

## 10. Bugs / Problems Found

All of the following were introduced during this phase and fixed before the phase ended. No pre-existing bugs were found.

| #   | Problem                                                                | Root cause                                                                                                                                         | Fix                                                                                           | Regression test                                                         | Area                |
| --- | ---------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------- | ------------------- |
| B1  | The ConfirmDialog description test failed                              | The test expected `"…deleted.This cannot…"`. The actual accessible description correctly contains a space between the two blocks                   | Corrected the test expectation (the implementation was right)                                 | `Dialog.test.tsx` › "names the consequence and focuses the safe choice" | Test                |
| B2  | "Dismisses programmatically by id" failed                              | The test helper returned `act(() => api.notify(...))`. In React 19, `act` returns a thenable, not the callback's value, so the ID was not a number | Capture the ID inside the `act` callback                                                      | `ToastProvider.test.tsx` › "dismisses programmatically by id"           | Test                |
| B3  | Toast tests that used user-event timed out                             | F2                                                                                                                                                 | Use `fireEvent` in the fake-timer tests                                                       | `ToastProvider.test.tsx` (7 tests)                                      | Test infrastructure |
| B4  | ESLint `react-hooks/globals` error in the toast test                   | A capture component assigned a module variable during render                                                                                       | Use `renderHook(useToast, { wrapper: ToastProvider })`                                        | Lint gate                                                               | Test                |
| B5  | ESLint `no-unsafe-*` errors in `ui.spec.ts`                            | `tsconfig.node.json` has no DOM lib, so `page.evaluate(() => document…)` callbacks were untyped                                                    | Use locator assertions (`toHaveCSS`, `:focus` locators) and a string `page.evaluate<boolean>` | Lint gate                                                               | E2E                 |
| B6  | The E2E focus-containment assertion failed                             | F3: Tab legitimately leaves the dialog for the browser UI                                                                                          | Assert that focus never reaches inert page content                                            | `ui.spec.ts` › "modal dialog traps focus…"                              | E2E                 |
| B7  | Catalog cards stretched to the height of the tallest card in their row | CSS grid default `align-items: stretch`                                                                                                            | `items-start` on the catalog grid                                                             | Visual check                                                            | Catalog page only   |

---

## 11. Fixes Applied

- **Lockfile churn.**
  - Symptom: a lockfile diff of about 9,000 lines after `npm install`.
  - Root cause: npm took the indentation from `package.json` (F5).
  - Implementation: re-serialized the lockfile with 2 spaces.
  - Verification: `git diff --stat` shows +15 lines; `npm ci`-equivalent tests and the build pass.
- **Ghost button color conflict.**
  - Symptom: the toast dismiss button needed `text-current` in addition to the ghost variant's `text-fg`, and with two conflicting utility classes the winner is undefined.
  - Root cause: `cx` has no conflict resolution.
  - Implementation: the ghost variant no longer sets a text color and inherits it instead.
  - Verification: visual check of the toasts in both themes.
- **Missing `h1` on the route error page.**
  - Symptom: migrating to `ErrorState` would have replaced Phase 01's `h1` with a `<p>`.
  - Root cause: the original `ErrorState` design had no heading.
  - Implementation: `EmptyState` and `ErrorState` now render a heading (`titleAs`, `h2` by default); `RouteErrorBoundary` passes `h1`.
  - Verification: `RouteErrorBoundary.test.tsx` and `display.test.tsx`.
- **Layout shift when a dialog opens.**
  - Symptom: the page shifted when `overflow: hidden` removed the scrollbar.
  - Implementation: `scrollbar-gutter: stable` on `html`.
  - Verification: visual check in the browser pane.
- **Test-harness fixes:** B1–B6 above.

---

## 12. Testing & Verification

### Component Tests (Vitest 5.0.3 + React Testing Library + user-event, jsdom)

Result: **65/65 passed in 12 files.** 48 tests are new for the primitives, 1 is new for the shell, and 16 come from Phase 01.

| File                                               | Tests                         | Covered                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| -------------------------------------------------- | ----------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `Button.test.tsx`                                  | 3                             | `type="button"` default; activation by click, Enter and Space; disabled ignores activation; while loading the button is focusable via Tab, has `aria-disabled` and `aria-busy`, and click and form submission are blocked                                                                                                                                                                                                                                                                                               |
| `fields.test.tsx`                                  | 8                             | Input: label, `required`, `aria-invalid`, combined accessible description; no `aria-describedby` and no invalid state without messages; typing; disabled is not editable. Textarea wiring. Select change with `selectOptions`. Checkbox: label click and Space toggle, description. Switch `role="switch"`. Disabled switch does not toggle                                                                                                                                                                             |
| `Dialog.test.tsx`                                  | 8                             | Dialog: not rendered before it opens; accessible name and description; `data-autofocus` receives focus; close button; a browser-initiated close (cancel + close) calls `onClose` once; non-dismissible blocks Escape and hides the close button. ConfirmDialog: consequence and irreversible text in the description; Cancel focused; confirm and cancel callbacks; pending blocks a second confirm and dismissal (Cancel disabled, `aria-disabled` on confirm); error shown as `role="alert"` with the `Error:` prefix |
| `DropdownMenu.test.tsx`                            | 7                             | `aria-haspopup`/`aria-expanded`; click opens with focus on the first item; arrows wrap, skip disabled items, Home/End; ArrowUp opens on the last item; Enter selects, closes and returns focus; Escape closes and returns focus; disabled items do not run; outside click and Tab close the menu                                                                                                                                                                                                                        |
| `Tabs.test.tsx`                                    | 4                             | Tablist name, `aria-selected`, panel labelled by its tab, only one panel rendered; single tab stop; ArrowLeft/ArrowRight wrap, Home/End; Tab moves to the panel; click selects; controlled mode reports `onValueChange` and keeps the selection while the owner does not change `value`                                                                                                                                                                                                                                 |
| `Tooltip.test.tsx`                                 | 3                             | Trigger is described while the tooltip is hidden; appears on Tab focus, hides on Escape, reappears on refocus, hides on blur; hover and unhover                                                                                                                                                                                                                                                                                                                                                                         |
| `ToastProvider.test.tsx`                           | 7                             | Persistent polite region; success shows `role="status"` with the `Success:` prefix; dismissed at exactly 5000 ms (still present at 4999 ms); danger is `role="alert"` and survives 60 s, dismiss button removes it; hover pauses the timer; cap of 4, oldest dropped; `dismiss(id)`; `useToast` throws outside the provider                                                                                                                                                                                             |
| `display.test.tsx`                                 | 8                             | Alert roles and prefixes by tone; Badge keeps its text; Card heading level and actions; EmptyState heading and action; ErrorState alert with the default title; LoadingState status label; Skeleton `aria-hidden`; Table caption (when hidden), `scope="col"` headers, row count, cells; empty row with `colspan`                                                                                                                                                                                                       |
| `src/app/AppLayout.test.tsx`                       | +1 (2 in total)               | `<main>` is not focused on first load and is focused after a link navigation                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| `src/app/{App,routes,RouteErrorBoundary}.test.tsx` | 14 (from Phase 01, unchanged) | Still pass after the shell migration                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |

**Manual mutation check.** I applied each edit below to the source temporarily, ran the relevant test file, and restored the file. Each edit made at least one test fail:

1. removing the focus call in `AppLayout`
2. removing the loading `preventDefault` in `Button`
3. removing the non-dismissible `preventDefault` in `Dialog`
4. dropping `:not(:disabled)` from the menu item selector
5. removing the toast `.slice(-MAX_TOASTS)`
6. removing the hover pause in `ToastItem`

### E2E Tests (Playwright 1.63.0, Chromium, production build served by `vite preview`)

Result: **11/11 passed** (3 from Phase 01, 8 new in `ui.spec.ts`).

- The dialog matches `dialog:modal`.
  - Initial focus is on Cancel.
  - Over 5 Tab presses, focus never lands outside the dialog.
  - Focus returns into the dialog.
  - Escape closes it, and focus returns to the trigger.
- Confirm runs the action once and closes the dialog.
- Dropdown via keyboard: ArrowDown opens, focus moves between items, Escape returns to the trigger, Enter twice runs "Duplicate".
- Tooltip appears on focus and hides on Escape.
- An error toast appears as `role="alert"` in the Notifications region, and its dismiss button removes it.
- The light theme gives `body` a background of `oklch(0.98…)`; dark gives `oklch(0.12…)`.
- At 375 px wide, `scrollWidth` is not greater than `innerWidth`.

### Accessibility Tests

- The Vitest and Playwright tests above check roles, accessible names and descriptions, keyboard models, live-region roles and focus management.
- **Not performed:**
  - an automated rule audit (axe or a similar tool)
  - screen-reader testing with NVDA or VoiceOver
  - measured contrast ratios

### Visual / Responsive Verification (manual)

- The dev server was inspected in the browser pane (dark scheme). I also exercised the menu and dialog keyboard flows there.
- Playwright screenshots of `/dev/ui`:
  - full page at 1280 × 900 in light and dark
  - with the ConfirmDialog and two toasts open, in both themes
  - at 375 × 812
- Two problems surfaced and were fixed: the card stretch (B7) and the scrollbar shift.

### Not applicable

Integration, API, infrastructure, security and performance test categories do not apply to this phase. There is no backend interaction.

---

## 13. Quality Gates

All results are from the final run on the current working tree.

| Quality Gate         | Result  | Notes                                                                                                                                                         |
| -------------------- | ------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Formatter            | PASS    | `npm run format:check`                                                                                                                                        |
| Linter               | PASS    | `npm run lint`. Initially failed (B4, B5); fixed                                                                                                              |
| Type Checker         | PASS    | `npm run typecheck` (`tsc -b`, strict plus `exactOptionalPropertyTypes` and related flags)                                                                    |
| Unit/Component Tests | PASS    | `npm test`: 65/65. Initially 1 failure (B1), then 3 (B2, B3); fixed                                                                                           |
| Integration Tests    | N/A     | No backend interaction                                                                                                                                        |
| API Tests            | N/A     |                                                                                                                                                               |
| E2E Tests            | PASS    | `npm run test:e2e`: 11/11. Initially 1 failure (B6); fixed                                                                                                    |
| Build                | PASS    | `npm run build`: `index-*.js` 332.42 kB (104.38 kB gzip), `index-*.css` 23.95 kB (5.75 kB gzip), `UiCatalogPage-*.js` 5.12 kB, `PlaceholderPage-*.js` 0.18 kB |
| Security Checks      | PASS    | `npm audit`: 0 vulnerabilities. Manual check: no `dangerouslySetInnerHTML`, `innerHTML`, `eval` or `style=` in `src/`                                         |
| Accessibility        | PARTIAL | Behavioral a11y tests pass. No automated audit and no screen-reader testing (§12)                                                                             |

---

## 14. Acceptance Criteria Verification

Acceptance criteria from `/docs/roadmap-frontend.md` §8:

| Acceptance Criterion                                                                                        | Status | Evidence                                                                                                                                                                                                                                                                                                               |
| ----------------------------------------------------------------------------------------------------------- | ------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| UI-Komponenten sind konsistent. (UI components are consistent.)                                             | PASS   | One token set; one `focus-ring`; shared `controlClasses` and tone maps in `shared.ts`; shared `Field`; one `Button` reused by Dialog, ConfirmDialog, DropdownMenu and toasts; shell migrated (§3); visual check in both themes                                                                                         |
| Keine unnötigen Inline-Styles. (No unnecessary inline styles.)                                              | PASS   | `grep -rn "style=" frontend/src` finds nothing; every style is a Tailwind utility or lives in `index.css`                                                                                                                                                                                                              |
| Dark/Light Theme kann vorbereitet bzw. unterstützt werden. (Dark/light theme can be prepared or supported.) | PASS   | The dark token set is active through `prefers-color-scheme`, with a `data-theme="light"` opt-out. The E2E tests `theme tokens follow the light/dark color scheme` pass                                                                                                                                                 |
| Accessibility-Grundlagen sind vorhanden. (Accessibility basics are in place.)                               | PASS   | Native semantic elements; APG keyboard models; labels and descriptions; `aria-invalid`; live regions; focus return; focus on navigation; `motion-reduce` on animations; color is never the only signal. Verified by the tests in §12. An automated audit is deferred to Phase 25 and is not required by this criterion |
| Komponenten sind isoliert testbar. (Components can be tested in isolation.)                                 | PASS   | Every primitive is rendered alone in its test file without the app or router. `TextLink` needs a router context, like any `Link`. Toasts need `ToastProvider`, which is supplied as a `renderHook` wrapper                                                                                                             |

Coverage of the prompt and roadmap scope list: every listed item has an implementation (§3). One item is not a separate component: "status indicators" are `Badge` with `dot` (§22).

---

## 15. Security Considerations

- **XSS and output encoding:**
  - All content renders as React text nodes.
  - No `dangerouslySetInnerHTML`, `innerHTML`, `eval` or `Function`.
  - `Tooltip` content is typed as `string`.
- **Error disclosure:**
  - `ErrorState` and `ConfirmDialog.error` are documented in JSDoc as user-facing only.
  - `RouteErrorBoundary` still never renders `error.message`; this is verified by the unchanged Phase 01 test.
- **Exposed route:**
  - `/dev/ui` contains only static demo strings: no secrets, no API calls, no user data.
  - It is reachable without authentication, like every route at this stage. Auth arrives in Phase 21.
- **Secrets:** nothing is stored in `localStorage` or `sessionStorage`. Toasts are held in memory only.
- **Not affected in this phase:** authentication, authorization, CSRF, SSRF, file handling, WebSockets.

---

## 16. Performance Considerations

- **Main chunk:** 316.01 kB in Phase 01 → 332.42 kB now (+16.41 kB; gzip +4.57 kB). The shell imports the primitives through the index, so they are part of the initial chunk.
- **CSS:** 8.88 kB → 23.95 kB. The cause is the token variables plus the utilities used by the catalog and the primitives.
- **Catalog page:** a separate lazy chunk (5.12 kB), loaded only on `/dev/ui`.
- **Rendering:**
  - The toast context value is memoized and holds no toast list, so `notify` and `dismiss` callers do not re-render when toasts change.
  - Toast timers are per toast and are cleared on unmount.
  - Menu and tooltip `document` listeners are attached only while open and are removed on close.
- **`Table`:** no virtualization. Server-side pagination is expected (`frontend/AGENTS.md` §91).
- **Measurements:** no performance measurements beyond bundle sizes were taken.

---

## 17. Documentation Changes

- **`frontend/README.md`:**
  - The structure tree now lists `components/ui/` and `lib/`.
  - The note about future directories no longer mentions `components/` and `lib/`.
  - The Tailwind decision now names the token file.
  - New "Design system" section:
    - import rule and the layout-only `className` rule
    - the `/dev/ui` reference
    - the token vocabulary and dark-mode mechanism
    - a conventions table: page structure, sections, typography, spacing, control sizes, content width, status
    - a usage guide for each primitive
- **`docs/reports/frontend/phase-02-design-system.md`:** this report.
- **Future update.** If Phase 21 adds a theme toggle, update the README's token paragraph, along with the `:root[data-theme='dark']` selector. `/docs/architecture.md` was not changed, because these are frontend-internal conventions.

---

## 18. Dependencies / Infrastructure Changes

| Change                                 | Type                                         | Why                                                                                                                                                                                     |
| -------------------------------------- | -------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `@testing-library/user-event` `14.6.7` | New devDependency, pinned exactly (`.npmrc`) | Realistic keyboard tests (Tab order, Enter/Space activation, arrow keys, hover). Phase 01 F5 anticipated it for Phase 02. Same Testing Library family as the existing test dependencies |
| `src/test/setup.ts` dialog stand-in    | Test infrastructure                          | F1                                                                                                                                                                                      |

- No runtime dependencies were added. I deliberately did not add a headless UI library, `clsx`/`tailwind-merge`, an icon set or axe.
- No environment variables, Docker or external-service changes.

---

## 19. Deferred Work

| What                                                | Why deferred                                            | Intended phase                          | Dependency / blocker      | Affects completion |
| --------------------------------------------------- | ------------------------------------------------------- | --------------------------------------- | ------------------------- | ------------------ |
| Manual theme toggle; `data-theme="dark"` forcing    | Settings feature                                        | Phase 21                                | Settings UI               | No                 |
| Automated a11y audit (axe in Vitest or Playwright)  | Needs a new dependency                                  | Phase 25                                | Dependency decision       | No                 |
| Screen-reader testing (NVDA, VoiceOver)             | Manual and outside the tooling                          | Phase 25                                | —                         | No                 |
| Firefox and WebKit E2E workflows                     | Only Chromium is configured since Phase 00              | Phase 26                                | —                         | No                 |
| Table sorting, selection, virtualization            | No consumer yet; server-side pagination expected        | When a feature needs it                 | —                         | No                 |
| Menu typeahead, submenus, icon-only menu trigger    | No consumer yet                                         | When a feature needs it                 | —                         | No                 |
| Drawer; Combobox / searchable select                | Not in the roadmap §8 scope; native `<select>` suffices | Node Library / Inspector (Phases 08–09) | Possible library decision | No                 |
| Global toast for unhandled rejections (Phase 01 O1) | Needs normalized API errors                             | Phase 03                                | API client                | No                 |
| Mobile navigation drawer (Phase 01 limitation)      | Responsive hardening                                    | Phase 25                                | —                         | No                 |

---

## 20. Known Limitations

- **L1 — Overlay clipping.** `DropdownMenu` and `Tooltip` are absolutely positioned, with no top layer and no collision or flip handling. Inside an `overflow` container, such as the scroll wrapper of `Table`, they can be clipped. This is marked with a `ponytail:` comment in `DropdownMenu.tsx`. Upgrade path: Popover API plus CSS anchor positioning.
- **L2 — Tooltips on disabled triggers.** `Tooltip` cannot show on a disabled button, because disabled buttons get no hover or focus events. This is documented in its JSDoc.
- **L3 — Layout-only `className`.** `cx` does not merge conflicting Tailwind classes, so `className` on primitives is for layout only. This is documented in `cx.ts` and the README.
- **L4 — Unmeasured contrast.** Token contrast was chosen with known Tailwind shade pairs (for example slate-600 on slate-50, and the 200-level tone text on the dark tone backgrounds) and checked by eye. It was not measured with a tool.
- **L5 — Untested Escape edge case.** The repeated-Escape close of a non-dismissible dialog (F4) is handled but not covered by a test.
- **L6 — Public catalog route.** `/dev/ui` is publicly reachable in production builds (§5.4).
- **L7 — Fake native dialog in unit tests.** Unit tests run against a stand-in for the native dialog. Native semantics are covered only by Chromium E2E tests.

---

## 21. Open Issues

| #   | Issue                                                | Impact                                         | Severity | Area           | Recommended next action                                                                                                             | Target                    |
| --- | ---------------------------------------------------- | ---------------------------------------------- | -------- | -------------- | ----------------------------------------------------------------------------------------------------------------------------------- | ------------------------- |
| O1  | (Phase 01) Non-render errors are not shown in the UI | Failed async actions give no feedback          | MEDIUM   | Error handling | Toasts now exist: normalize API errors and surface them through query/mutation states and `notify`                                  | Phase 03                  |
| O2  | (Phase 01) `npm install` re-indents the lockfile     | Noisy diffs; fixed by hand again (F5)          | LOW      | Tooling        | Set Prettier `overrides` for `package.json` to 2 spaces, or accept a one-time reformat commit                                       | Any, as a separate commit |
| O3  | (Phase 01) No CI pipeline                            | Regressions are caught only by manual runs     | MEDIUM   | Tooling        | Add a CI workflow running format, lint, typecheck, test, build and E2E                                                              | Not assigned              |
| O5  | Overlay clipping (L1)                                | A menu inside a scrolling table can be cut off | LOW      | UI primitives  | Popover API plus anchor positioning, or portal-based positioning                                                                    | Phase 24                  |
| O6  | Should `/dev/ui` stay public in production? (L6)     | Exposes an internal page, with no data         | LOW      | Routing        | Decide in the production-readiness phase: keep, gate behind auth, or exclude via a documented build flag while keeping E2E coverage | Phase 27                  |

O4 from Phase 01 (focus on route change) is closed by this phase (§5.5).

---

## 22. Architectural / Product Decisions

- **Native elements over a headless library** (§5.1). Revisit if a Combobox or complex positioning becomes necessary.
- **`Switch` is `Checkbox` with `role="switch"`.** One component, one test surface, and the same native semantics.
- **Status indicators are `Badge dot`,** not a separate component. Features map domain states to a tone and a text label, for example execution states in Phase 13.
- **`ConfirmDialog` is fully controlled** (`pending` and `error` come from the caller), so it composes directly with TanStack Query mutations. It holds no promise state.
- **Dialog content mounts only while open.** Forms inside dialogs reset on every open, and closed dialogs cost nothing to render.
- **A loading `Button` uses `aria-disabled`, not `disabled`,** to keep keyboard focus.
- **Toasts:** errors and warnings persist, success and info time out after 5 s with a pause on hover or focus, and at most 4 are visible. This follows `frontend/AGENTS.md` §53 ("no flood of toasts"): complex errors belong inline (`Alert` or `ErrorState`), not in toasts.
- **The catalog route ships in production,** so E2E tests run against the real bundle (§5.4); revisit in Phase 27 (O6).
- **Non-component helpers live in `.ts` files** (`shared.ts`, `toastContext.ts`), so component files only export components (`react-refresh/only-export-components`).

---

## 23. Repository / Git State

- **Branch:** `main`
- **HEAD:** `591a09d frontend phase 01 - application shell`
- **Working tree:** uncommitted Phase 02 changes only. Nothing has been committed for this phase.
- **Modified (13 tracked files, +275 / −58):**
  - `frontend/README.md`
  - `frontend/package-lock.json`, `frontend/package.json`
  - `frontend/src/app/{App,AppLayout,AppLoading,NotFoundPage,PlaceholderPage,RouteErrorBoundary,routes}.tsx`
  - `frontend/src/app/AppLayout.test.tsx`
  - `frontend/src/styles/index.css`
  - `frontend/src/test/setup.ts`
- **Untracked:**
  - `frontend/src/components/` (the `ui/` directory)
  - `frontend/src/lib/`
  - `frontend/src/app/UiCatalogPage.tsx`
  - `frontend/tests/e2e/ui.spec.ts`
  - `docs/reports/frontend/phase-02-design-system.md`
- **Unrelated changes:** none. The tree was clean at the start of the phase, and the temporary `.claude/launch.json` was removed.

---

## 24. Phase Completion Assessment

### Implemented

- Semantic tokens with a dark theme and a single focus ring.
- 20 primitives covering every roadmap §8 scope item and every prompt item.
- Shell migrated to the primitives; route-change focus management.
- `/dev/ui` catalog page; README design-system documentation.

### Verified

- Format, lint, typecheck, build and audit pass.
- 65/65 Vitest and 11/11 Playwright tests pass.
- 6/6 manual mutations were caught.
- Visual check in light, dark and at 375 px.

### Not Implemented

- Automated a11y audit, screen-reader testing, contrast measurement.
- Overlay collision handling.

### Deferred

See §19: theme toggle, axe audit, cross-browser E2E, table and menu extensions, Drawer and Combobox, O1 wiring.

### Open

O1, O2, O3, O5, O6 (§21).

### Blocking Issues

None.

**Phase Status:** COMPLETE WITH LIMITATIONS

All five acceptance criteria are PASS (§14). The status is "with limitations" because accessibility verification is behavioral only and overlays can be clipped (§20). Neither blocks Phase 03.

---

## 25. Handoff to Next Phase (Phase 03 — API Client + Backend Integration Foundation)

### What Phase 03 can assume

- `ToastProvider` is mounted in `App.tsx`; `useToast()` works anywhere under it.
- All UI is imported from `src/components/ui` (index).
- `/dev/ui` shows every primitive and state.

### Patterns to use for API states

| State                 | Primitive                                                                                                                      |
| --------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| Region failed to load | `ErrorState` with a retry `Button`                                                                                             |
| Inline failure        | `Alert tone="danger"`                                                                                                          |
| Mutation outcome      | `useToast().notify({ tone: 'success' \| 'danger', title })`. Use the toast only for short outcomes; show complex errors inline |
| Loading               | `LoadingState`, or `Skeleton` inside an `aria-busy` container                                                                  |
| Empty collection      | `EmptyState`                                                                                                                   |
| Destructive mutation  | `ConfirmDialog` with `pending={m.isPending}` and `error={…user-facing message…}`; close it on success                          |
| Form                  | `Input`/`Select`/… with a per-field `error` (map backend validation details to fields) and `Button type="submit" loading`      |

All messages must be user-facing and never raw error text (`frontend/AGENTS.md` §20).

### Must not be changed casually

- the semantic token names (all primitives and the README depend on them)
- the `Dialog` `open`/`onClose` contract and `[data-autofocus]`
- the `Button` loading semantics (`aria-disabled`, blocked submit)
- the jsdom dialog stand-in in `src/test/setup.ts`; native behavior belongs in Playwright
- the Phase 01 boundary rules (unchanged)

### Constraints

- `className` on primitives is for layout only.
- Do not use raw palette classes in feature code.
- Check the lockfile diff after `npm install` (O2).
- Node `^22.22.0 || >=24`.

### Prerequisites

None outstanding from this phase.
