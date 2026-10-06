# Project Sharing — Phase Report

## 1. Objective

Let users collaborate on a project without making them admins. The owner
(or an admin) shares a project with other users as **viewer** or
**editor**. The backend enforces every level on every entry point: REST,
MCP, the live WebSocket and event triggers. Decision record:
`docs/adr/0008-project-sharing.md`.

## 2. Access model

| Who | Access | May |
|---|---|---|
| Admin, owner | `manage` | everything, including delete and sharing |
| Editor | `edit` | read; change the draft and triggers, restore, publish, run, invoke, cancel |
| Viewer | `view` | read the draft, versions, runs, reports, logs; export, duplicate (the copy is theirs), validate |
| Anyone else | none | nothing; the project answers 404 |

- One rule, `domain.identity.project_access`, decides access.
  `AuthorizationService.require_project` and `require_execution` apply it
  with a required level.
- No access at all is 404, so ids cannot be probed. Too little access is
  403 `PERMISSION_DENIED`, with `{access, required}` in the details.

## 3. Architecture

- **Domain** (`domain/identity.py`): `ProjectRole` (viewer, editor),
  `Access` (VIEW < EDIT < MANAGE), `project_access()`, `ProjectMember`,
  `MAX_PROJECT_MEMBERS = 100`.
- **Persistence**:
  - migration `0017_project_members` creates `project_members`, with
    primary key (project, user), CASCADE when the project is deleted,
    RESTRICT on the user, a role check constraint and an index on
    `user_id`;
  - new `ProjectMemberRepository` (`role`, `list`, `count`, `put` as an
    upsert that keeps `created_at`, `delete`);
  - new `UserRepository.get_by_username`, which never reads the password
    hash.
- **Listings**: the `owner_id` filter became `visible_to` (owned **or**
  member) for project lists, cross-project execution lists, status counts
  and monitoring. `AuthorizationService.owner_filter` became
  `visible_to`.
- **Application**: `application/sharing.py` `SharingService` (`get`,
  `put_member`, `remove_member`). Each method re-checks access through
  `AuthorizationService`.
- **HTTP guard** (`api/auth.py`): `authorize_path` now asks for the level
  the route declares with `requires(Access.X)`, visible in OpenAPI as
  `x-akasha-access`. Undeclared routes need `view` for GET and `edit`
  otherwise. Declared routes:
  - `validate`: view;
  - `duplicate`: view;
  - `DELETE /projects/{id}`: manage;
  - member changes: manage.
- **API** (`api/sharing.py`):
  - `GET /projects/{id}/members` returns `{access, owner, members}`;
  - `PUT /projects/{id}/members/{username}` with `{role}`;
  - `DELETE /projects/{id}/members/{username}`;
  - `PUT /projects/{id}/owner` with `{username}` (ownership transfer);
  - `DELETE /projects/{id}/membership` (leave).
- **Ownership transfer**: needs manage access, and the new owner must be
  an enabled user (`USER_DISABLED` otherwise). The project row is locked
  (`lock_owner`); the new owner's member row is dropped; the previous
  owner stays on as an editor; `set_owner` does not advance the draft
  revision.
- **Leaving**: needs any access. The owner gets `PROJECT_OWNER` (transfer
  first); an admin who is not a member gets `MEMBER_NOT_FOUND`.
- **MCP** (`mcp/tools.py`): the same levels.
  - Edit: graph and draft tools, triggers, publish, `execution_start`,
    `execution_cancel`.
  - Manage: `project_delete`.
  - Everything else: view.
- **Event triggers**:
  - At fire time (`runtime/event_triggers.py`), a trigger reacts to a
    source only if the trigger project's owner can view it, and shared
    projects count.
  - At save time, `AuthorizationService.require_trigger_sources` checks
    every source the caller *adds* (REST `PUT`, and MCP draft and trigger
    tools). An unknown or invisible source is refused with
    `TRIGGER_SOURCE_NOT_FOUND` (422).
- **Frontend**:
  - `api/sharing.ts`, the `projects.sharing` query key (below the
    project), and `usePutProjectMember` / `useRemoveProjectMember`.
  - `SharingCard` on the project page: owner, members, your access; share
    by username, change a role, stop sharing with confirmation.
  - Project page actions follow access: Edit details needs `edit`,
    Delete needs `manage`.
  - The Sharing card offers "Transfer ownership" to managers (a dialog
    explaining that the previous owner stays an editor) and "Leave
    project" to members; after leaving, the page returns to the project
    list.
  - The Triggers page shows "View-only access" to viewers and hides New,
    Run, Enable/Disable, Edit and Delete.
  - The editor shows "View-only access" instead of an editor a viewer
    could never save.
  - The project list marks "Shared with you".

## 4. Security review

| Concern | Result |
|---|---|
| Viewer writes | Every scoped write route needs `edit` by default; tested over the full request catalogue for both roles, and over MCP |
| Sharing escalation | Only `manage` may share; editors cannot add members or promote themselves (403); non-members get 404 |
| Data exfiltration via event triggers | Sources a caller adds must be visible to them; the fire-time check uses the owner's live access; removing access stops the trigger (tested) |
| Existence probing | 404 for no access; unknown and invisible trigger sources get the same error; usernames are discoverable to project managers (accepted, ADR 0008 §7) |
| Revocation | Access is evaluated per request; removal is immediate for REST and MCP (tested) |
| Unbounded data | At most 100 members per project (`MEMBER_LIMIT`); the member list is therefore unpaginated |
| Audit | `project_member_set` (role, previous role), `project_member_removed`, `project_access_denied` (access, need); ids only |
| Credentials | Unchanged: installation-wide, referenced by name, as before (§60) |

## 5. Tests

- **Unit** (`tests/test_auth_units.py`): `project_access` truth table
  (9 cases) and level ordering.
- **Integration and API** (`tests/integration/test_sharing_api.py`, 11
  tests, real PostgreSQL):
  - each role against every project- and execution-scoped request;
  - listings including shared projects;
  - access following changes on the next request;
  - the members API (owner, members, own access, `created_at` kept on a
    role change);
  - only the owner or an admin can share;
  - invariants: unknown user, the owner as a member, removing a
    non-member, an invalid role, the member limit;
  - cascade on project delete;
  - an editor cannot add a private trigger source but can keep an
    existing one;
  - audit events;
  - the role check constraint.
- **Event triggers** (`test_event_triggers.py`):
  - a member cannot save a trigger on another user's project;
  - a shared source fires until the share is removed, then stops;
  - an unknown source is refused at save.
- **MCP** (`test_mcp.py`): a viewer reads but cannot publish, run, delete
  or edit; an editor runs but cannot delete; listings include shared
  projects.
- **Migration**: head is now `0017_project_members`; the full
  downgrade/upgrade round trip checks the table.
- **OpenAPI**: the snapshot was regenerated; `access` is an enum in the
  contract.
- **Full backend suite** (clean worktree, no local `.env`): 2266 passed,
  12 skipped, 5 failed. The 5 failures are the same Windows-specific node
  tests that fail on clean `HEAD`.
- **Frontend** (Vitest): 39 files, 515 tests passed. New tests:
  - sharing card: share, change role, stop sharing;
  - unknown username shown at the field;
  - viewer sees no edit or delete;
  - "Shared with you" marker;
  - editor view-only state.

  Existing project-page tests now stub the members endpoint. The contract
  test covers `api/sharing.ts`.
- **Full-stack E2E** (`tests/fullstack/sharing.spec.ts`, real backend):
  1. An owner shares as viewer from the UI.
  2. The viewer sees "Shared with you" and cannot edit or delete; the
     editor shows View-only; the backend answers 403 to a direct publish.
  3. The owner promotes the viewer to editor; the editor opens.
  4. The owner stops sharing; the project is "not found" for the former
     member.
- **Quality gates**: ruff check and format pass; prettier, eslint and
  `tsc -b` pass. mypy shows only the 2 pre-existing, Windows-only errors
  in `nodes/command.py`.

## 6. Decisions

- The previous owner stays on as an editor after a transfer, so a mistyped
  username never locks them out of their own project. Leaving is
  explicit.
- Leaving has its own path (`/membership`) rather than a special case of
  `DELETE /members/{username}`, which needs manage access.

- Members per project; no teams or groups (ADR 0008, Alternatives).
- Share by username, since usernames are immutable as of the user
  administration phase.
- Validate and duplicate need only view access, because neither changes
  the project. Delete needs manage.
- The caller's access is served by the members endpoint, not added to
  every project response. This keeps `ProjectDetail` unchanged; the UI
  reads `access` where it needs it.
- Unknown event-trigger sources are refused at save. This changes earlier
  behaviour, where such a source was stored and only reported by
  validation. Accepting unknown ids while refusing invisible ones would
  reveal which ids exist.

## 7. Limitations

- A live WebSocket that is already open keeps streaming its execution
  after a member is removed, until it closes. Authorization runs when it
  subscribes; this was already true for disabled users.
- Restoring an old version brings back its triggers as they were (ADR
  0008, Consequences).
- A transfer always keeps the previous owner as an editor; there is no
  "transfer and leave" in one step (leaving is a second click).

## 8. Deferred

- groups or teams as a source of project roles
- per-project credential grants
- notifications when something is shared with you
