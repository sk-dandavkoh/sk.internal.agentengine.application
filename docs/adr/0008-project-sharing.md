# ADR 0008: Project sharing

Status: accepted (2026-10-06)

## Context

Until now, a member could reach only the projects they own; admins could
reach every project. A team could not work on a project together unless
everyone involved was made an admin, which also hands out user,
credential and plugin management. Phase 20 kept the data model ready for
grants (every project has an `owner_id`) and named per-resource grants as
future work (architecture §60).

## Decision

1. **Owner plus members.** A project has exactly one owner. The owner or
   an admin can add members with one of two roles:
   - `viewer`: read the draft, versions, runs, reports and logs; export,
     duplicate (the copy is the caller's) and validate.
   - `editor`: everything a viewer can, plus change the draft (including
     triggers), restore a version, publish, run, invoke triggers and
     cancel runs.

   Only the owner and admins share a project or delete it. Members are a
   new table, `project_members` (migration 0017), keyed by project and
   user.
2. **One rule, three levels.** `domain.identity.project_access(user,
   owner_id, role)` returns `MANAGE` for admins and the owner, `EDIT` for
   editors, `VIEW` for viewers, and none otherwise. Every entry point uses
   it through `AuthorizationService`:
   - REST, via `authorize_path`;
   - MCP;
   - the live WebSocket;
   - event triggers.
3. **Levels are declared on routes, with safe defaults.** A route under
   `project_id` or `execution_id` needs view access for `GET` and edit
   access for every other method, unless it declares otherwise
   (`requires(Access.X)`). The declaration appears in OpenAPI as
   `x-akasha-access`. A new write route therefore never opens to viewers
   by accident.
4. **Errors.** No access at all is still 404, so ids cannot be probed.
   Too little access is 403 `PERMISSION_DENIED`, with `access` and
   `required` in the details: the caller already knows the project exists.
5. **Listings show what you can reach.** Project lists, cross-project
   execution lists, status counts and monitoring include shared projects
   (`visible_to`, which replaces `owner_filter`).
6. **Event triggers cannot widen access.** At fire time, a trigger reacts
   to a source only if the trigger project's owner can view that source.
   At save time, every event-trigger source the caller adds must be
   visible to the caller (`require_trigger_sources`). Without that check,
   an editor could point a trigger at a project only the owner sees, and
   read its outputs through the runs it starts. Sources already in the
   draft are kept, so editors can still save a draft the owner wired to a
   private source. An unknown source is refused the same way, so the
   check does not reveal whether a project exists.
7. **Sharing is by username.** People know each other by username, and
   usernames are immutable. Anyone who manages a project can therefore
   tell whether a username exists; this needs a session, and on a
   self-hosted installation that is acceptable.
8. **Access is evaluated per request.** Adding, changing or removing a
   member takes effect on that member's next request. No revocation step
   is needed.
9. **Ownership transfer.** `PUT /projects/{id}/owner {username}` (manage)
   hands a project to another enabled user. The previous owner stays on
   as an editor, so a transfer never locks out the person who made it;
   they can leave afterwards. The new owner's member row is dropped,
   because owning includes it. The project row is locked first, so
   concurrent transfers serialize and every intermediate owner stays a
   member. Event triggers keep running under the owner's visibility, so
   they fire only for sources the new owner can see.
10. **Leaving.** `DELETE /projects/{id}/membership` (any access) removes
    the caller's own membership. The owner cannot leave
    (`PROJECT_OWNER`): someone must manage the project, so they transfer
    ownership first. Admins are not members and have nothing to leave.

## Alternatives

- **Teams or groups.** More flexible, but a second hierarchy to model,
  audit and display. Members per project cover the stated need. Groups
  can be added later as another source of a role.
- **Transfer that drops the previous owner.** Simpler, but a mistyped
  username would lock the person who made the transfer out of their own
  project. Keeping them as an editor is reversible, because they can leave.
- **Method-only rules with no per-route declaration.** Wrong for the read
  operations that are `POST` (validate, duplicate) and for delete, which
  needs more than edit.
- **Per-project credential grants.** Credentials stay installation-wide,
  as before (§60). Sharing does not change who can use them: any project
  could already reference any credential.

## Consequences

- One extra indexed lookup (the caller's role) per project-scoped request
  for non-owners. Owners and admins skip it.
- Membership changes are immediate for REST and MCP. A live WebSocket that
  is already open keeps streaming the execution it subscribed to until it
  closes, because authorization runs when it subscribes. The same was
  already true for a disabled user's stream.
- Restoring an old version brings back its triggers as they were, even if
  the restorer could not add those sources themselves. That version was
  already part of the project they were given.
