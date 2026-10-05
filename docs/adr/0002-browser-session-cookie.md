# ADR 0002: Browser sessions in an HttpOnly cookie

Status: accepted (frontend Phase 21, 2026-10-05)

## Context

Backend Phase 20 issues opaque session tokens that clients send as
`Authorization: Bearer`. The live WebSocket takes the token in its first
frame. There are no cookies, so there is no CSRF surface (architecture §59).

Frontend Phase 21 needs login, logout and a session that survives a reload.
With bearer tokens only, the browser would have to keep the token where page
scripts can read it:

- **localStorage or sessionStorage.** Forbidden for tokens (AGENTS §35/§62,
  frontend AGENTS §21/§62). One XSS bug would read the token and use it
  from anywhere until it expires.
- **Memory only.** Every reload or new tab signs the user out. That breaks
  the "refresh/reload" and "session restoration" requirements.

## Decision

1. **Cookie login, opt-in.** `POST /api/v1/auth/login` takes
   `"cookie": true`. The session token then goes into a cookie and the
   response's `token` is `null`, so page scripts never see it. API clients
   keep the default bearer flow unchanged. The cookie is
   `akasha_session`, `HttpOnly`, `SameSite=Strict`, `Path=/api/v1`,
   `Secure`, and expires with the session (absolute, no sliding renewal).
   `AKASHA_SESSION_COOKIE_SECURE=false` drops `Secure` for plain-HTTP rigs.
   Browsers already allow `Secure` cookies on localhost and 127.0.0.1.
2. **CSRF: a required custom header.** The backend accepts the cookie only
   on requests that carry `X-Akasha-CSRF`. HTML forms and `<img>` cannot
   set the header. A cross-origin `fetch` that sets it needs a CORS
   preflight, and the API never grants one (no CORS). A cookie login without
   the header answers 403 `CSRF_HEADER_REQUIRED`, which blocks login CSRF.
   A bearer token, when sent, always wins over the cookie.
3. **WebSocket: Origin check.** A browser cannot set headers on a WebSocket,
   so the cookie counts there only when the handshake's `Origin` host equals
   `Host` or `X-Forwarded-Host`. This blocks cross-site WebSocket hijacking,
   including from other origins on the same site. A token in the subscribe
   frame still works as before.
4. **Logout** revokes the session server-side and expires the cookie. A
   copied cookie value is dead after logout, password reset or disable.
5. **New read:** `GET /api/v1/auth/session` returns `{expires_at, user}`,
   so the UI can restore and show a session after a reload.

## Alternatives

- **Token in sessionStorage.** Rejected. An XSS bug can exfiltrate the
  token, and repository rules forbid it.
- **Double-submit CSRF token.** Rejected. It needs state the UI must read,
  and the custom header gives the same protection without a second secret.
- **Always set the cookie on login.** Rejected. Bearer API clients would get
  an ambient credential they never asked for.

## Consequences

- Cookie-authenticated requests have a CSRF surface. It is closed by
  `SameSite=Strict` plus the required header. Tests cover a missing header
  on reads, logout and login.
- A reverse proxy must forward the browser's host (`proxy_set_header Host
  $host` or `X-Forwarded-Host`), or live updates in the UI fail with 4401.
  `docs/deployment.md` shows this.
- The cookie is scoped to `/api/v1` on the UI's origin. The UI must keep
  being served from the same origin as the API (already the case,
  frontend README).
- XSS can still act as the user while the page is open, because requests
  carry the cookie. It cannot steal the session for later use. Content
  Security Policy hardening is frontend Phase 23.
