# ADR 0007: Personal access tokens

Status: accepted (2026-10-06)

## Context

AI agents (MCP, ADR 0006) and scripts need a credential. The only one was
the login session: it expires after `AKASHA_SESSION_TTL_SECONDS` (12 h by
default, 30 days at most), with no renewal. An agent's configuration
therefore needed a fresh login and an edited client config at least
monthly, and there was no way to tell agents' credentials apart or revoke
one without logging out everything.

## Decision

1. **Personal access tokens** (`akasha_pat_` + 256 random bits): named,
   owned by one user, optional expiry (1–3650 days, or none), sent as the
   usual `Authorization: Bearer`. They work everywhere a session does (REST,
   MCP, `/metrics` for admins). The prefix tells `authenticate` which table
   to check, and lets log redaction and secret scanners recognize leaks.
2. **Same identity, same rules.** A token resolves to its user's
   `Identity`; authorization is untouched. `Identity` now carries either a
   session or a token. There are no per-token scopes in V1 (§Alternatives).
3. **Storage like sessions:** only the SHA-256 (`access_tokens`, migration
   0015). The value is returned once, in the creation response. The list
   shows name, first characters (`hint`), dates and `last_used_at`
   (written at most hourly, so authentication is not a write per request).
4. **Issuing needs the account password.** A stolen session or token
   cannot mint a credential that outlives it.
5. **Revocation:** the owner revokes individually; logging out with a
   token revokes it; an admin password reset or disabling the user revokes
   all of the user's tokens (and a disabled user's tokens are refused at
   authentication anyway). Changing one's own password keeps them: that
   user can see and revoke each one.
6. **Bounded:** 50 tokens per user.
7. **UI:** Settings → Access tokens (list, create with one-time display and
   copy, revoke with confirmation). The list query lives outside the
   `auth` cache scope, so it is dropped on a user switch.

## Alternatives

- *Longer session TTLs*: one setting for every browser and agent, no
  naming, no individual revocation. Rejected.
- *Scoped tokens* (read-only, no plugins, per workflow): useful, but the
  authorization model is role- and owner-based; scopes would need checks
  in every REST route and MCP tool. Deferred. Today, a dedicated
  least-privileged user per agent gives the same effect.
- *OAuth 2.1 authorization server for MCP*: the standard for public MCP
  servers, heavy for a self-hosted engine with local accounts. Deferred.
- *Revoking tokens on one's own password change*: would silently break
  every agent on a routine password rotation. The explicit list and
  revoke cover the compromise case; admin reset covers recovery.

## Consequences

- Agents get stable credentials. Operators can see which ones are used and
  revoke them one by one.
- A token is as powerful as its user (including admin). The docs
  recommend dedicated agent users.
- `GET /auth/session` may now return `expires_at: null` (token without
  expiry). The frontend schema accepts it; browser sessions always expire.
