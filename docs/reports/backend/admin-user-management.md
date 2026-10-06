# Admin User Management — Phase Report

## 1. Objective

Let an administrator manage the installation's users from the frontend:
list, search and filter, create, edit (display name, role), disable and
re-enable, and reset passwords. All rules are enforced by the backend.
The existing authentication and authorization system (Phase 20) is
extended, not replaced, and MCP is not widened.

## 2. What already existed

Phase 20 had already built most of the foundation:

- users with two roles (`admin`, `member`);
- scrypt password hashes;
- server-side sessions and personal access tokens, stored only as hashes;
- `UserService` with create, list and `PATCH` (disable or reset
  password);
- revocation of sessions and tokens on disable or reset;
- a last-admin guard for disabling, using row locks;
- the admin-only `/users` router behind `require_admin`;
- default-deny route protection.

This phase fills the gaps instead of building a second system.

| Gap | Fix |
|---|---|
| No display name, no last-login time | migration `0016_user_profile` (two nullable columns); login records `last_login_at` in the same transaction as the session |
| Role could not be changed | `PATCH /users/{id}` takes `role`; the last-admin guard now also covers demotion |
| An admin could disable themselves (with a second admin present) and reset their own password without the current one | `OWN_ACCOUNT` (409) for self-disable, self-demotion and self-reset through `/users` |
| List had no search or filters | `q` (username or display name, literal substring, any case), `role`, `status=active\|disabled` |
| No single-user read | `GET /users/{id}` |
| Audit carried no details and no refusals | `user_updated` lists the changed field names and the resulting role and status; new events `user_create_refused`, `user_update_refused` (with the error code) and `admin_required_denied` |
| No UI | `/users` page (admin only), dialogs, typed API client, query keys, mutations |

## 3. Architecture

```text
UsersPage ─ userQueries / useCreateUser / useUpdateUser ─ api/users.ts
      │ HTTP (cookie + X-Akasha-CSRF)
api/auth.py  /users router  (current_identity → require_admin, router-wide)
      ↓
application/auth.py  UserService  (require_admin again; every invariant)
      ↓
domain/identity.py  User, Role, validate_display_name
      ↓
infrastructure/persistence.py  SqlAlchemyUserRepository
```

The router stays thin. The rules live in `UserService._update`:

- no self-lockout;
- the last active admin cannot be disabled or demoted
  (`lock_active_admins`, `SELECT … FOR UPDATE`);
- revocation on disable or reset.

The domain validates display names. Pydantic only bounds the input sizes.

The API keeps the existing convention: one typed `PATCH` with optional
fields. It does not add `/enable`, `/disable` and `/reset-password`
endpoints, which would duplicate one transaction three times. The audit
event still distinguishes the operations through `changes`.

## 4. Files changed

Backend:
- `migrations/versions/0016_user_profile.py` (new)
- `src/akasha/domain/identity.py`: `display_name`, `last_login_at`,
  `validate_display_name`
- `src/akasha/application/persistence.py`, `infrastructure/db_models.py`,
  `infrastructure/persistence.py`: the new columns, `record_login`, list
  filters, `update` writes the role and display name
- `src/akasha/application/auth.py`: `UserService` (get, filters, role,
  display name, `OwnAccountError`, audit), `record_login` on login,
  `admin_required_denied`
- `src/akasha/api/auth.py`: schemas (`UserDetail` gains `display_name`
  and `last_login_at`; `UserStatus`), `GET /users/{id}`, list query
  parameters
- tests: `tests/test_auth_units.py`,
  `tests/integration/test_auth_api.py`,
  `tests/integration/test_persistence.py`
- `backend/README.md`, `docs/architecture.md` §59

Frontend:
- `src/api/users.ts` (new), `src/api/auth.ts` (`userRoleSchema`, the
  new user fields), `src/api/queries.ts` (`queryKeys.users`,
  `userQueries`), `src/api/mutations.ts` (`useCreateUser`,
  `useUpdateUser`), `src/api/openapi.json` (regenerated)
- `src/features/users/` (new): `UsersPage.tsx`, `UserDialogs.tsx`,
  `labels.ts`, `UsersPage.test.tsx`
- `src/app/routes.tsx` (`/users`), `src/app/navigation.ts` (`adminOnly`),
  `src/app/AppLayout.tsx` (hides admin-only entries from members)
- `src/test/app.tsx`, `src/api/client.node.test.ts`,
  `src/api/contract.node.test.ts`: fixtures and contract coverage
- `tests/fullstack/users.spec.ts` (new)

## 5. Database changes

`0016_user_profile` adds two columns to `users`:

- `display_name VARCHAR NULL`
- `last_login_at TIMESTAMPTZ NULL`

Both are additive and nullable, so the migration takes no table rewrite
and existing rows stay valid. Existing users get no display name (the UI
shows the username) and no last login. The last login is not guessed
from sessions, which are purged when they expire.

The migration is covered by the existing tests:

- upgrade from empty to head, with head bumped to `0016`;
- the full downgrade to base and upgrade again;
- the readiness test, which requires the schema at head.

## 6. API changes (all under `/api/v1`, all admin-only)

| Method | Path | Change |
|---|---|---|
| GET | `/users` | new: `q` (≤ 64 chars), `role`, `status`; still paged (`offset`, `limit` ≤ 100), ordered by username |
| GET | `/users/{id}` | new |
| POST | `/users` | new optional field: `display_name` |
| PATCH | `/users/{id}` | new fields: `display_name` (`""` clears it) and `role`; `username` is ignored (immutable) |

Every user response now also carries `display_name` and `last_login_at`.
It never carries a hash, session or token; a test asserts the exact key
set. Error codes:

- `OWN_ACCOUNT` (409, new)
- `LAST_ADMIN` (409, now also for demotion)
- `USERNAME_TAKEN` (409)
- `USER_NOT_FOUND` (404)
- `PERMISSION_DENIED` (403)
- `AUTHENTICATION_REQUIRED` (401)
- 422 for validation errors

Compatibility: these are additive response fields and optional request
fields. Existing clients keep working. The only behaviour change: an
admin disabling their own account used to succeed when a second admin
existed. It is now refused, as this phase requires.

OpenAPI: the snapshot `frontend/src/api/openapi.json` was regenerated.
`test_openapi_contract.py` passes; it checks that the snapshot is current
and that every 4xx/5xx response uses the error envelope. The frontend
contract test checks every `api/users.ts` call against the document.

## 7. Authorization and session behaviour

- **Admin-only.** The router requires an admin for every route
  (`require_admin`), and every `UserService` method checks again. A
  member gets 403 before any lookup, so there is no user enumeration and
  no IDOR. An unauthenticated caller gets 401. The default-deny test
  covers the new `GET /users/{id}`.
- **Disabled while signed in.** All sessions and access tokens are
  deleted in the same transaction as the disable. Authentication also
  filters out disabled users, so there is no window. Re-enabling restores
  password login only; the revoked tokens stay revoked.
- **Password reset while signed in.** All of the user's sessions and
  tokens are revoked. Only the new password works.
- **Role changed while signed in.** No revocation is needed: each request
  re-reads the user with its session, so a promotion or demotion applies
  from the next request. A test proves both directions.
- **Self-protection.** An admin cannot disable or demote their own
  account, nor reset their own password through `/users`. They use
  `POST /auth/password`, which requires the current password. This holds
  even when other admins exist: another admin has to do it.
- **Last admin.** The last *active* admin can be neither disabled nor
  demoted. With the self rule in place, this guard is reached only when
  two admins act on each other at the same time. Tests race both disable
  and demote: exactly one admin remains.
- **MCP.** No user-management tools exist. `test_mcp.py` asserts the exact
  tool set (`EXPECTED_TOOLS`), so adding one would fail CI. PAT callers
  reach `/users` over REST with exactly their user's role, as before.

## 8. Frontend

- **Route and navigation.** `/users` is a lazy route under
  `RequireSession`. The "Users" nav entry is `adminOnly` and hidden from
  members. A member who opens `/users` directly sees "Admin access
  required" and the page sends no request. A 403 from the backend (for
  example, a role removed in the meantime) shows the same state.
- **List.** The table shows name (display name or username, "(you)" for
  the caller), username, role, status, created and last login. Search,
  role and status filters live in the URL. Pagination is server-side
  (50 per page). The page has loading, error with retry, stale-data,
  empty and no-match states.
- **Dialogs.**
  - Create: username, display name, role, initial password with
    confirmation.
  - Edit: display name and role. A role change shows "Change
    administrator role?" and the submit button becomes "Change role and
    save". The role cannot be changed for one's own account.
  - Reset password: new password with confirmation; explains that the old
    password and all sessions end.
  - Enable/disable: confirmation dialog.
  - On one's own row, no disable or reset actions are offered. This only
    mirrors the backend rule.
- **Secrets.** Password inputs are uncontrolled, masked and
  `autocomplete=new-password`. Mutations use `gcTime: 0`, and closing a
  dialog calls `reset()`. Tests assert the password is not in the DOM,
  the URLs or the mutation cache.
- **Server state.** Mutations invalidate `['users','list']`, write
  `['users','detail',id]`, and refetch the session, since the edited user
  may be the caller. User data is dropped on a user switch along with all
  other non-auth state.

## 9. Security review

| Concern | Result |
|---|---|
| Privilege escalation | Only admins reach `/users`; members cannot change any role, including their own (403). Tested per method, including `PATCH role=admin`. |
| IDOR / enumeration | Members get 403 before the id is looked up; the 404 for unknown ids is visible to admins only. |
| Password reset abuse | Admin-only; self-reset is refused; reset revokes all credentials; the new password goes through the same length rules and scrypt. |
| Brute force | Login throttling is unchanged; a disabled user's attempt counts as a failure and returns the same `INVALID_CREDENTIALS`. |
| Disabled-account access | Sessions and tokens are deleted, and authentication filters out disabled users. Tested over HTTP and E2E. |
| Lockout | `OWN_ACCOUNT` plus the row-locked last-admin guard; tested under concurrency. |
| Sensitive data | Responses have an exact key set; the repository's `_PUBLIC` columns exclude `password_hash`; the logs were checked for the password, the reset password and `scrypt$`. |
| Search injection | `icontains(..., autoescape=True)`: `%` and `_` are literals (tested). |
| Timing | The user endpoints are admin-only, so the timing of existence checks reveals nothing to non-admins; login keeps its dummy-hash path. |
| Audit | Covered by events (see §2); ids, field names and codes only; the request id comes from the log context (tested). |

## 10. Tests

These counts are the results of the runs in this session.

- **Backend unit** (`tests/test_auth_units.py`): display-name
  normalization and validation, 4 new cases.
- **Backend API/integration** (`tests/integration/test_auth_api.py`, real
  PostgreSQL through Testcontainers, real HTTP stack): create, edit, role
  and display name; immutable username; 422 validation; `last_login_at`
  set on success and not on failure; search (case, display name, LIKE
  wildcards), filters, paging; exact response keys; audit content and
  secret absence; self-lockout (disable, demote, reset); another admin
  disables an admin; role changes on live sessions; concurrent
  disable/demote races; 404s; members refused on every method.
- **Migration**: head is now `0016_user_profile`; upgrade, full downgrade
  and re-upgrade.
- **OpenAPI**: snapshot and error-envelope tests pass.
- **Targeted run**: auth, persistence and OpenAPI files: 137 passed.
- **Full backend suite**: 2240 passed, 12 skipped, 9 failed, 5 errors.
  - All 9 failures and 5 errors are outside this change. 5 failures and
    3 errors reproduce on clean `HEAD` with the change stashed. They come
    from Windows symlink privileges and POSIX-only process groups in the
    command and file node tests, and from three example projects.
  - The remaining 4 MCP failures and 2 example errors pass when their
    files are run alone with this change applied (40 passed). They are
    caused by the local `.env` (see §13).
- **Frontend unit/component** (Vitest): 39 files, 507 tests passed,
  including 14 new `UsersPage` tests:
  - rendering, loading, error and retry;
  - filters sent to the backend;
  - create with password mismatch and secret absence;
  - a taken username shown at the field;
  - edit with the role-change call-out;
  - own role locked;
  - disable/enable confirmations;
  - `LAST_ADMIN` shown;
  - reset password;
  - members refused without a request;
  - a 403 from the backend;
  - admin-only nav.
  The contract test covers `api/users.ts`.
- **Frontend quality gates**: `tsc -b`, `eslint .` and
  `prettier --check .` all pass.

## 11. E2E results

Full-stack Playwright runs against the real backend, PostgreSQL and Redis
(`npm run test:fullstack`): **26 passed** (whole suite).
`tests/fullstack/users.spec.ts`:

1. **Admin journey.**
   1. The admin signs in through the UI and opens Users from the nav.
   2. Creates a user; the user appears.
   3. Edits the display name and promotes to Administrator, through the
      role-change confirmation.
   4. Disables the user; the status shows Disabled.
   5. A UI sign-in as that user gets 401.
   6. Re-enables the user; sign-in works again (200), and Last login is
      no longer "Never".
   7. Resets the password; the old one gets 401 and the new one 200.
   8. The admin's own row has no Disable action, and the backend answers
      409 to a direct self-disable.
2. **Member.**
   1. No Users nav entry.
   2. `/users` shows "Admin access required" and no table.
   3. `GET /users` and `POST /users` from the page with the session
      cookie both get 403.

Synchronization uses web-first assertions and `waitForResponse`, with no
sleeps.

## 12. Decisions

- Keep the single typed `PATCH /users/{id}`: it is the existing
  convention, and one transaction per change. No `/enable`, `/disable`
  or `/reset-password` endpoints.
- Admin-supplied passwords for creation and reset; no generated
  passwords or reset tokens, which would need a delivery channel
  (email, invitations) that is out of scope.
- Self-reset through `/users` is refused, so the admin path can never
  skip the current-password check of `POST /auth/password`.
- No revocation on a role change: authorization reads the live user per
  request, so the change is already immediate.
- The nav hides only Users from members. Credentials and Plugins keep
  their existing behaviour (visible, then "Admin access required"); that
  is unchanged from earlier phases.

## 13. Limitations

- Audit events are structured logs, not persisted records (§61 of the
  architecture is still future work, as before).
- The search is an unindexed substring scan (marked `ponytail:`); add a
  `pg_trgm` index if installations grow large.
- **Backend type check.** `mypy src` reports 2 errors in
  `nodes/command.py` (`os.killpg`, `signal.SIGKILL`). They appear only
  on a Windows host and are unrelated to this change; lint and format
  pass.
- **Environment-dependent failures (pre-existing, not caused by this
  change).** In the full backend suite on the developer machine, 4 MCP
  tests fail and 5 example tests error.
  - Cause: importing `litellm` runs `load_dotenv()`, which loads the
    git-ignored repository-root `.env` into the test process. That
    developer `.env` sets `AKASHA_MCP_ENABLED`, so every test that runs
    after the first `litellm` import sees MCP turned on. The examples then
    error ("Attempted to exit cancel scope in a different task") because
    of the MCP session manager.
  - Evidence: a clean checkout of `HEAD` has no `.env` and passes these
    tests (5 failed, 2223 passed; only the Windows-specific node tests
    fail). The same files pass in isolation with this change applied
    (40 passed). The fix belongs in the test setup, not in this phase.

## 14. Deferred (explicitly not implemented)

- SSO, OAuth, LDAP, SCIM
- invitations, email verification, self-service reset
- groups, teams, organizations, RBAC beyond the two roles
- user deletion (users are disabled; projects reference their owners)
- persisted audit log
- user-management tools over MCP
