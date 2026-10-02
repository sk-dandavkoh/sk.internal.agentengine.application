# Phase 06 — Project Management

## Summary

**Goal.** Roadmap §12: manage projects through the UI (list, search, filter, sort, details, create, rename, delete, duplicate, open editor, status) without bypassing backend rules. Graph editing is out of scope.

**Status: complete.** A user can create a project, open it, edit its name and description, duplicate it, and delete it. Every rule is decided by the backend.

## Backend changes

The existing API had no search, filter, sort or duplicate. These were added:

| Change                                          | Detail                                                                                                                                                                                                                                                                                                                                        |
| ----------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `GET /api/v1/projects?q=&status=&sort=`         | `q`: case-insensitive name substring, matched literally (`%`, `_`, `\` escaped), 1–200 characters. `status`: repeatable `ProjectStatus`. `sort`: `created_at` (default, unchanged behavior), `updated_at` or `name` (case-insensitive), with a `-` prefix for descending. The id breaks ties, so pages are stable. Invalid values return 422. |
| `POST /api/v1/projects/{id}/duplicate` `{name}` | Creates a new draft-only project owned by the caller. It copies the draft: graph, triggers, dependencies, description and files. Published versions are not copied. The copy is written through `Project.replace_draft`, so every draft rule applies. Access to the source is checked by the existing path guard: other users get 404.        |

Layers touched: `ProjectSort` and the extended `ProjectRepository.list` protocol (application), the SQL implementation (infrastructure), `ProjectService.list_projects` and `ProjectService.duplicate_project`, and the router and schemas. No migration was needed. The OpenAPI snapshot was regenerated. The changes are documented in `backend/README.md` and `docs/architecture.md` §22.

## Frontend

| Area               | Implementation                                                                                                                                                                                                                                                                          |
| ------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `/projects`        | Table with name and description, status badge, latest version and last update. Search form, status select and sort select. Previous/Next paging (25 per page) driven by the backend's `has_more`. All list state is kept in the URL.                                                    |
| Empty states       | "No projects yet" with a New project button. "No matching projects" with a Clear filters button.                                                                                                                                                                                        |
| Loading and errors | `QuerySection`, moved from the dashboard to `components/` so both features share it: a loading state, an error with Retry, and stale data with a warning on a failed refresh. While the next page or filter loads, the previous rows stay visible (`keepPreviousData`, `aria-busy`).    |
| Create             | Dialog with name and description. On success it opens the new project and shows a toast. Backend field errors (`details["body.name"]`) appear at the field. Any other error appears as a form alert.                                                                                    |
| `/projects/:id`    | Details: status, latest version, draft revision, created and updated times, id, draft contents (counts) and the description as plain text. A not-found or inaccessible project gets its own page. Open editor links to `/projects/:id/editor`, which is still the Phase 07 placeholder. |
| Edit details       | Uses PUT with the draft sent back unchanged plus the `revision` it was read at. A 409 is shown and the project is reloaded.                                                                                                                                                             |
| Duplicate          | The name defaults to "Copy of …". On success it opens the copy.                                                                                                                                                                                                                         |
| Delete             | ConfirmDialog. A refusal from the backend (409 `PROJECT_HAS_VERSIONS`) is shown in the dialog. On success it returns to the list.                                                                                                                                                       |

**State policy.** Every write waits for the backend's answer; nothing is updated optimistically (AGENTS §85). Mutations invalidate `['projects','list']`, and Edit details stores the returned project.

Shared helpers moved to shared locations:

- `errorMessage` moved to `api/errors.ts`.
- `formatDateTime` moved to `lib/format.ts`.
- `formText` was added in `lib/form.ts`.
- `projectStatusLabels` now lives in `features/projects/status.ts`, and the dashboard imports it from there.

## Tests

**Backend integration tests** (real PostgreSQL via Testcontainers, real HTTP stack):

- Search: case-insensitive, with LIKE wildcards and backslashes matched literally.
- Status filter: single and repeated values.
- Every sort order, ascending and descending.
- Invalid query values return 422.
- Duplicate copies the draft and produces an independent, unpublished, deletable project.
- Duplicate rejects malformed bodies (422) and unknown projects (404).
- Authorization: duplicate is part of the cross-member 404 matrix, and a copy belongs to the caller.

**Frontend Vitest:** 16 screen tests plus one server-state test.

- Display: projects listed, a project opened from the list.
- Empty and filtered-empty states.
- Search, filter and sort sent to the backend and kept in the URL; clearing filters.
- Paging.
- Loading, then a server error, then a successful Retry.
- Create: success, 422 field error, 500 form error, and a clean form when reopened.
- Detail metadata and the not-found page.
- Edit details:
  - while the request is in flight, the old name stays (authoritative, not optimistic)
  - the request body carries the draft and `expected_revision`
  - on a 409, the server state is kept and the project is reloaded
- Duplicate.
- Delete: success, and a backend 409 refusal.

**Contract tests:**

- `duplicateProject` and the list params were added to the OpenAPI coverage.
- The `ProjectSort` enum is checked against the backend.

**Full-stack Playwright (`npm run test:fullstack`, 4 tests)**, the production bundle against the real backend and a fresh PostgreSQL:

- Create (with the real 422 for a blank name), search, rename, duplicate and delete. Afterwards it checks through the API that the backend holds exactly what the UI showed.
- Deleting a published project (arranged via the API from `backend/examples/projects/hello-world.json`) is refused with 409.
- An edit based on a stale revision is refused, and the page reloads the newer state.
- Without a session, the backend answers 401 and the UI shows the error.
- Mutation check: breaking the duplicate URL in the API client fails the suite.

**Playwright (3 new):**

- The full lifecycle in a real browser, against an in-memory fake that enforces revisions and the delete rule. It checks native dialog focus, Escape, and focus returning to the trigger.
- Deleting a published project is refused.
- At 375 px, the list and detail pages never scroll sideways.

## Quality gates

| Gate                                                          | Result                               |
| ------------------------------------------------------------- | ------------------------------------ |
| Frontend `prettier --check`, `eslint`, `tsc -b`, `vite build` | PASS                                 |
| Frontend Vitest                                               | 181/181                              |
| Frontend Playwright                                           | 19/19                                |
| Frontend full-stack Playwright                                | 4/4                                  |
| Backend `ruff format --check`, `ruff check`, `mypy`           | PASS                                 |
| Backend pytest                                                | 1870 passed, 6 skipped, **1 failed** |

The backend failure was already there before this phase: it also fails with this phase's changes stashed. `test_round_trip_of_arbitrary_json_configuration` fails because Hypothesis found that `is_storable` (`domain/primitives.py`) rejects the literal text `\u0000` (a backslash followed by `u0000`, not a NUL character). It is flagged as a separate task.

## Known limitations

- The frontend has no login yet (Phase 21). The full-stack suite (`npm run test:fullstack`) therefore creates the admin through the API and has the browser send its token as a request header. Everything else in that suite is real: production bundle, backend from this checkout, fresh PostgreSQL.
- Name search is an unindexed `ILIKE '%…%'` scan (marked with a `ponytail:` comment). Add a `pg_trgm` index if project counts make it slow.
- The status filter in the UI selects one status; the API accepts several.
- The list loads full project rows, drafts included, and returns summaries. This was already the case before this phase.
- On narrow screens the table scrolls horizontally inside its own region, which is the existing `Table` behavior.

## Not implemented (future phases)

- The graph editor (Phase 07+). "Open editor" leads to its placeholder.
- Versions UI (Phase 12), publish workflow (Phase 11) and import/export UI.
