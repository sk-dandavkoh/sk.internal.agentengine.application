# Phase 25 — Security Hardening

## 1. Executive Summary

**Supposed to achieve.** Roadmap §29 (Phase 25): a dedicated backend
security pass over authentication, authorization, credential storage,
secret redaction, file access, HTTP access/SSRF, path traversal, input
validation, plugin handling, expression evaluation, API limits, webhooks,
error messages and logging. Acceptance criterion: "All documented security
boundaries are tested and no known critical secret-leak or arbitrary-code
paths remain."

**Method.** For each surface: threat → trust boundary → reproduce →
mitigate → regression test → review → full suite. Every mitigation below
has a regression test, and each fix's test was shown to **fail on the code
before the fix** (mutation check: the original module was restored from
`HEAD`, the new tests run, then the fix put back).

**Findings fixed (5) and hardening added (4):**

| # | Finding | Severity | Status |
|---|---|---|---|
| F1 | Webhooks unauthenticated: anyone knowing two ids starts executions (Phase 20 open issue) | MEDIUM | Fixed: HMAC-SHA256 signature per trigger, fail-closed |
| F2 | DNS rebinding: guard resolved, client resolved again (Phase 09 known gap) | MEDIUM | Fixed: connect to vetted addresses only (HTTP and SMTP) |
| F3 | `header_credentials` forwarded to a cross-origin redirect target | MEDIUM | Fixed: dropped when a redirect leaves the origin |
| F4 | Webhook body read whole into memory before the size check | MEDIUM (DoS) | Fixed: streamed with cap |
| F5 | `core.smtp` sends the password unencrypted with `security: none` | MEDIUM | Fixed: refused to untrusted destinations |
| H1 | No request-body cap for JSON API routes (§107) | LOW | Global streaming cap, 413 |
| H2 | No guard against eval/exec/pickle/shell creeping into `src` | — | AST architecture test |
| H3 | API responses cacheable / sniffable (session tokens in bodies) | LOW | `Cache-Control: no-store`, `nosniff` |
| H4 | `cryptography` 46.0.7: 4 advisories incl. bundled OpenSSL | MEDIUM (dependency) | Upgraded to 50.0.2; audit clean |

**Phase Status:** COMPLETE WITH LIMITATIONS (§20).

---

## 2. Threat Model per Surface

| Surface | Trust boundary | Threat | Finding / verdict | Evidence |
|---|---|---|---|---|
| Authentication | Client → API | brute force, enumeration, token theft | No new finding. scrypt, per-username throttle, constant-time unknown-user path, 256-bit opaque tokens hashed at rest, absolute expiry (Phase 20) | `test_auth_api.py`, `test_auth_units.py` |
| Authorization | Session → resource | IDOR, privilege escalation | No new finding. Default-deny router, 404 for foreign resources, admin-only routers; OpenAPI walk asserts every non-public route needs a session | `test_every_non_public_route_requires_authentication` |
| Webhooks | Internet → engine | unauthenticated execution, forged/tampered payloads | **F1**, **F4** | §3.1 |
| Credentials | Store → node | exfiltration, plaintext in projects | No new finding. AES-256-GCM with name as AAD; references only; per-node resolution boundary. Webhook secrets reuse the same store | `test_credentials.py` (unit + integration) |
| Secret handling / logging | Engine → logs, errors, API | secret echo | No new finding; webhook signature header is neither logged nor in the trigger context (allowlist) | `test_redaction.py`, `test_logging.py`, `test_request_observability.py` |
| SSRF | Node config → network | internal services, cloud metadata, DNS rebinding | **F2**, **F3** | §3.2 |
| SMTP | Node → mail server | credential sniffing | **F5** | §3.3 |
| File access / path traversal | Package → engine | zip-slip, symlinks, bombs | No new finding: path regex, regular files only, declared and real size caps. One test was Windows-specific (fixed, §10) | `test_project_package.py`, `test_project_files.py` |
| Plugins | Operator → engine | malicious code | Trusted-code model (§65) unchanged; install via CLI only, manifests validated, symlinks refused. Signature/hash verification remains deferred (§19) | `test_plugins.py` |
| Expressions | User config → evaluator | code execution | No eval anywhere; templates resolve mapping/list keys only; conditions are declarative. **H2** makes this a standing guard | `test_llm_node.py` (dunder/`__import__` cases), `test_no_arbitrary_code_execution_primitives` |
| API limits | Client → API | memory exhaustion | **H1**, **F4** | §3.4 |
| Error disclosure | Engine → client | stack traces, internals | No new finding: one redacted error funnel, generic 500 | `test_errors.py`, `test_request_observability.py` |
| Input validation | Client → domain | deep JSON, invalid UTF-8 | No new finding (Phase 24 D5 tests) | `test_unparseable_or_too_deep_payloads_are_422_not_500` |
| Dependencies | Supply chain | known CVEs | **H4** | §13 |

---

## 3. Scope Implemented

### 3.1 Webhook authentication (F1, F4)

- `domain/trigger.py`: `WEBHOOK_SIGNING_FIELD = "secret_credential"`.
- `domain/project.py` `validate_triggers`: a WEBHOOK trigger without a
  credential reference there is the new issue `WEBHOOK_SECRET_REQUIRED`
  (blocks validate/publish). A plaintext value under the key is already
  `PLAINTEXT_SECRET` (sensitive-key rule).
- `application/triggers.py`: `webhook_signature`, `verify_webhook` and
  `WebhookSignatureError` (401 `WEBHOOK_SIGNATURE_INVALID`). HMAC-SHA256
  of the raw body, `hmac.compare_digest`, one message for every cause. The
  cause goes to the `webhook_rejected` log event only. Fail-closed for
  versions published before this phase and for deleted credentials.
- `api/triggers.py`: body streamed with the webhook cap
  (`read_limited_body`), then verified, then parsed. An unsigned body is
  never interpreted.

### 3.2 SSRF (F2, F3)

- `security/network.py`: `check_destination` returns the vetted addresses
  (deduplicated, resolver order).
- `nodes/http.py`: `_GuardedBackend`, an `httpcore.AsyncNetworkBackend`
  that resolves through the guard and connects to the vetted addresses
  (fallback in order). Every connection uses it, including redirect hops.
  TLS still verifies the URL host name. The per-hop scheme/userinfo checks
  remain in `_check_url`.
- `fetch(..., secret_headers=...)`: credential headers are dropped from the
  first hop that leaves the original (scheme, host, port) and never come
  back.

### 3.3 SMTP (F2, F5)

- Destination checked first (no secret decrypted for a host never
  contacted). `security: none` with a username is refused
  (`SMTP_INSECURE_AUTH`) unless the operator trusts the destination
  (private networks allowed, or host allowlisted).
- `_connect` connects to the vetted addresses, with the TLS server name
  kept (smtplib's `_host`, see the `ponytail:` comment).

### 3.4 API limits (H1)

- `RequestSizeLimit` ASGI middleware, `AKASHA_API_MAX_REQUEST_BYTES`
  (default 20 MiB), counted while streaming → 413 `PAYLOAD_TOO_LARGE`.
  Installed innermost: inside `BaseHTTPMiddleware` its exception would be
  wrapped into an `ExceptionGroup` and turned into a 400 (found while
  testing).
- `read_limited_body` is now shared by package import and webhooks.

### 3.5 Other

- `tests/test_architecture.py`: AST guard. No `eval`/`exec`/`compile`/
  `__import__`, no `pickle`/`marshal`/`shelve`/`yaml`/`subprocess`, no
  `.system()`, no `shell=`, `importlib` only in the plugin loader and the
  CLI.
- Response headers `Cache-Control: no-store`, `X-Content-Type-Options:
  nosniff`.
- `is_credential_reference` is now a `TypeGuard[str]`.

---

## 4. Files Changed

Source: `api/app.py`, `api/projects.py`, `api/schemas.py`,
`api/triggers.py`, `application/triggers.py`, `config.py`,
`domain/credentials.py`, `domain/graph_validation.py`, `domain/project.py`,
`domain/trigger.py`, `nodes/http.py`, `nodes/smtp.py`,
`security/network.py`.
Tests: `tests/integration/test_triggers_api.py`,
`tests/integration/test_auth_api.py`, `tests/nodes/test_http_node.py`,
`tests/nodes/test_smtp_node.py`, `tests/domain/test_project.py`,
`tests/test_architecture.py`, `tests/test_request_observability.py`,
`tests/application/test_project_package.py`.
Dependencies: `pyproject.toml`, `uv.lock`.
Docs: `docs/architecture.md` (§60, §63, §107, §108, §127), this report.

---

## 7. API / Contract Changes (breaking)

- **Webhooks require a signature.** `POST /api/v1/webhooks/{project_id}/{trigger_id}`
  needs `X-Akasha-Signature: sha256=<hex HMAC-SHA256(secret, raw body)>`,
  else 401 `WEBHOOK_SIGNATURE_INVALID`. The OpenAPI document lists 401.
- **Publishing a WEBHOOK trigger requires** `config.secret_credential =
  "credential:<name>"`. New `ValidationCode`: `webhook_secret_required`.
- **Upgrade impact:** existing webhook triggers refuse every call until
  the operator creates a credential, sets it on the trigger, republishes,
  and gives the secret to the caller. This is intentional (fail-closed).
  The frontend has no webhook UI and does not reference validation codes.
- New error code `SMTP_INSECURE_AUTH` (node error).
- New 413 on any route for bodies over `AKASHA_API_MAX_REQUEST_BYTES`.

---

## 10. Bugs / Problems Found during the Phase

1. `test_unexpected_or_traversal_entries_rejected[docs\\..\\evil.txt]`
   failed on Windows (listed as an environment failure in Phase 24). The
   entry *was* rejected; the assertion ignored that `zipfile` stores
   `os.sep` as `/`. The assertion now compares with
   `zipfile.ZipInfo(name).filename`, which is portable. No product change.
2. The first body-cap version raised inside `BaseHTTPMiddleware`'s task
   group. FastAPI turned the `ExceptionGroup` into a 400. Fixed by middleware
   order (§3.4).

---

## 12. Testing & Verification

New or changed regression tests (each listed fix failed before it):

| Test | Proves |
|---|---|
| `test_webhook_requires_a_valid_signature` | unsigned, empty, zero, wrong-secret, tampered-body, prefix-less and non-ASCII signatures → 401; no execution row; correct one → 202 |
| `test_webhook_with_deleted_secret_refuses_every_call` | fail-closed on missing credential |
| `test_webhook_without_a_secret_cannot_be_published` | `webhook_secret_required`, publish 422 |
| `test_webhook_secret_must_be_a_reference` | plaintext secret refused, not echoed |
| `test_oversized_webhook_body_is_rejected_while_streaming` | chunked 10 MiB stream stopped after < 64 KiB; 413 |
| `test_every_request_body_is_capped` | global cap 413; body within the cap accepted |
| `test_webhooks_stay_public` (changed) | no session needed, but unsigned → 401, signed → 202 |
| `TestTriggerValidation::test_webhooks_must_name_their_signing_credential` | domain rule incl. non-string value |
| `test_connection_uses_the_addresses_the_guard_vetted` | rebinding resolver: one lookup, connects only to vetted IPs in order |
| `test_one_private_answer_blocks_the_whole_name` | mixed public/private answer blocked |
| `test_credential_headers_do_not_follow_cross_origin_redirects` | same origin keeps, cross origin drops; ordinary headers follow |
| `test_password_is_never_sent_in_plaintext_to_public_hosts` | `SMTP_INSECURE_AUTH`; credential never resolved |
| `test_smtp_connects_to_the_vetted_address` | SMTP send works only via the pinned address (Mailpit) |
| `test_no_arbitrary_code_execution_primitives` | AST guard |
| `test_responses_are_never_cached_or_sniffed` | headers on 200 and 404 |

Mutation checks: with `nodes/http.py` from `HEAD`, both new HTTP tests
fail. With `nodes/smtp.py` from `HEAD`, both new SMTP tests fail.

---

## 13. Quality Gates

Run in `backend/` on Windows 11 (Python 3.14, uv), after the final change:

| Gate | Result | Notes |
|---|---|---|
| `uv run ruff format --check .` | PASS | 185 files |
| `uv run ruff check .` | PASS | one documented `noqa: ASYNC109` (httpcore's interface names the parameter `timeout`) |
| `uv run mypy` | PASS | 85 source files; one documented `type: ignore[attr-defined]` (smtplib `_host`) |
| Full `uv run pytest` | FAIL (pre-existing) | 1803 passed, 5 skipped, 6 failed, 4 errors (256 s). None caused by this phase, see below |
| Dependency audit (`pip-audit`, runtime and dev, from `uv export`) | PASS | after `cryptography` 46.0.7 → 50.0.2 |
| Build | N/A | no container change |

**Full-suite failures, all present on clean `HEAD`:**

- Windows environment (same as Phase 24): no symlink privilege
  (`test_plugins.py::test_symlinks_in_packages_are_rejected`), POSIX file
  modes (`test_cli.py::test_backup_file_mode_helper`,
  `integration/test_cli.py::test_backup_destroy_restore_run`), POSIX
  signals (`…::test_ctrl_c_cancels_the_run_cooperatively`,
  `…::test_worker_process_registers_and_stops_gracefully_on_sigterm`), and
  4 ERRORs from parametrized test ids exceeding the Windows env-var limit
  (`PYTEST_CURRENT_TEST`).
- Flaky: `integration/test_reliability.py::test_backoff_is_exponential_and_recorded`
  (`InvalidStateTransitionError: cannot complete`) failed in 4 of 5 runs on
  clean `HEAD`, with all changes stashed. This is a pre-existing race and a
  defect (AGENTS §95). It is outside this phase's scope and is listed as an
  open issue.
- One Phase 24 environment failure is gone: the backslash-entry assertion
  (§10 #1).

---

## 19. Deferred Work

| Item | Reason | Target |
|---|---|---|
| Webhook replay protection (signed timestamp) | needs a caller-side scheme change | future trigger work |
| Request-rate limiting (webhooks/API) | reverse proxy is the V1 control point; per-project active-execution cap bounds load | Phase 26 (deployment guide) |
| Plugin package hash/signature verification | V1 trusted-code model (§65) | future plugin work |
| Owner-aware credential resolution | members' projects may use any credential by name (Phase 20) | future authorization work |
| Restore input size cap | operator-supplied file (Phase 21 open issue) | Phase 26 |

---

## 20. Known Limitations

- Credentials are installation-wide: a member can *use* any credential
  through a node (documented since Phase 20). A member can also use any
  credential as a webhook signing secret. That gives no read access to it.
- `allowed_hosts` / `allow_private_networks` disable the private-address
  check entirely for that name/installation, as before; pinning still
  applies.
- Two private-attribute dependencies, each marked `ponytail:` and covered
  by a test that fails if they break: httpx `transport._pool` and smtplib
  `_host`.
- Self-triggering via the engine's own webhook is now much harder (the
  caller needs the secret, and private networks must be allowed), but a
  lineage-based depth cap still does not exist (Phase 24 §9.6).
- Full-suite environment failures on Windows (§13) need confirmation on
  Linux CI.
- Open issue: the flaky reliability backoff test (§13).

---

## 22. Architectural Decisions

- **Fail-closed webhooks** over opt-in secrets. A public endpoint that
  starts executions must authenticate. The price is a one-time
  reconfiguration of existing webhooks.
- **HMAC over a bearer secret.** The secret never travels, and the body
  cannot be altered in transit or by an intermediary that logs it.
- **The signing secret lives in the Credential Store,** referenced like any
  other secret: projects stay secret-free and portable (§32/§36).
- **Pinning at the connection layer** (network backend), not by rewriting
  URLs: redirects, Host, SNI and certificate checks keep working unchanged.

---

## 24. Phase Completion Report (AGENTS §115)

```text
Phase: 25 — Security Hardening
Status: COMPLETE WITH LIMITATIONS
Typecheck: PASS
Tests: 1803 passed; 6 failed + 4 errors pre-existing (Windows env / flaky)
Lint: PASS
Format: PASS
Build: N/A
```
