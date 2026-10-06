# Phase 13 — Credentials + Secrets

## 1. Executive Summary

**What the phase had to achieve.** Roadmap §17 (Phase 13) asks for secure credential management. The scope is:
- `Credential`, `CredentialReference` and `CredentialService`;
- secret encryption, decryption and redaction;
- support for API keys, SMTP credentials, provider credentials and generic secret values;
- workflow configuration that contains references, not raw secrets.

Required tests: encryption/decryption, access control, reference resolution, redaction, export exclusion and log exclusion. The acceptance criterion is: *"Secrets never appear in Workflow JSON, normal API responses, logs or execution reports."*

The phase prompt adds requirements:
- encrypted persistence, secret resolution and credential access boundaries;
- secret-safe serialization;
- leak checks across API responses, logs, events, reports, workflow exports, exceptions, database serialization and debug output;
- encryption keys from secure configuration, never hard-coded.

**What was implemented.**
- **Domain model** (`akasha.domain.credentials`): `Credential`, `CredentialKind`, the `Secret` wrapper, `CredentialReference` (format `credential:<name>`) and the config scanners `plaintext_secret_paths` and `credential_references`.
- **Encryption** (`akasha.security.crypto.SecretCipher`): AES-256-GCM via the new dependency `cryptography~=46.0`. The key comes from `AKASHA_CREDENTIAL_ENCRYPTION_KEY`.
- **Application service** (`akasha.application.credentials.CredentialService`): create, get, list, update/rotate, delete and resolve.
- **Persistence:** repository `SqlAlchemyCredentialRepository`, table `credentials`, migration `0006_credentials`.
- **REST API:** `/api/v1/credentials`, where values can be written but never read back.
- **Runtime access boundary** (`akasha.runtime.credentials.NodeCredentials`): a node may resolve only references contained in its own frozen config snapshot.
- **Known-value redaction:** a task-scoped `ContextVar` in `akasha.security.redaction` masks every resolved value in the resolving node's logs, errors and outputs.
- **Workflow config guard:** secret values in node or trigger config are rejected when a draft is saved (`PLAINTEXT_SECRET`), and reported as a validation issue on publish and run.
- **HTTP node:** `core.http` gains `header_credentials`. `core.smtp` now uses the (now async) resolver.

**Resulting state.**
- Secrets are stored only as ciphertext.
- Workflow data can hold only references.
- No API endpoint returns a secret value.
- Resolved values are masked in every persisted or logged artefact of the resolving node.
- Full backend suite: 1068 passed, 0 skipped, in two consecutive runs (973 at the end of Phase 12). A third run skipped the 207 Docker-backed tests because of a pre-existing Docker-probe flake (§9, F6).
- Format, lint, strict mypy and `uv build` pass.

**Acceptance.** The criterion is met for every channel that exists today (§14). Two parts can only be checked in their current form:
- "execution reports" (the Reports subsystem is Phase 19);
- "workflow export" (Export is Phase 18).

In both cases the underlying data, meaning persisted execution state and workflow JSON, is verified secret-free. "Access control" is implemented structurally rather than per user, because authentication and authorization are Phase 20. Production code does not yet construct `ExecutionRuntime`; this was already open before this phase (Phase 11/12). Until that wiring exists, credential resolution works in tests and library use but not from the API process.

**Phase Status:** COMPLETE WITH LIMITATIONS

---

## 2. Phase Objective

**Roadmap §17, Phase 13 — Credentials + Secrets**
- **Goal:** "Introduce secure credential management."
- **Scope:** `Credential`, `CredentialReference`, `CredentialService`, secret encryption, secret decryption, secret redaction.
- **Support:** API keys, SMTP credentials, provider credentials, generic secret values.
- **Constraint:** "Workflow configuration must contain references rather than raw secrets."
- **Tests:** encryption/decryption, access control, reference resolution, redaction, export exclusion, log exclusion.
- **Acceptance criterion:** "Secrets never appear in Workflow JSON, normal API responses, logs or execution reports."

**Phase prompt additions**
- Implement credential model, `CredentialReference`, credential service, encrypted persistence, secret resolution, credential access boundaries, redaction and secret-safe serialization.
- Verify no leakage through API responses, logs, events, reports, workflow exports, exceptions, database serialization and debug output.
- Encryption keys come from secure configuration and are never hard-coded.
- Loop: implement → attack → test → review → fix, actively searching for leaks.

**Governing rules:** backend/AGENTS §28 (credentials), §29 (structural redaction); AGENTS §35/§36 (secrets, credential references); architecture §32 (secrets and credentials), §101 (runtime-only resolution), §109 (redaction), §110 (key handled separately from backups).

**Explicitly not this phase's responsibility**
- User authentication and authorization (Phase 20).
- The export/import package format (Phase 18).
- Report generation (Phase 19).
- LLM provider consumption of credentials (Phases 14/15).
- Key rotation operations (Phase 26).
- Runtime/worker wiring in the API process (Phases 21/22).

---

## 3. Scope Implemented

### Domain
- `Credential` dataclass: `name` (immutable identity), `kind: CredentialKind`, `description`, `timestamps`; `reference` property; `touch()`.
- `CredentialKind` StrEnum: `api_key`, `smtp`, `provider`, `generic`. These cover the four roadmap categories.
- `CredentialReference`, tightened: the value must match `credential:<name>` with name `^[a-z0-9][a-z0-9._-]{0,63}$`. It gains a `name` property and a `to(name)` constructor.
- `Secret`: a slotted wrapper with `reveal()`. `str`/`repr`/`format` return `Secret([REDACTED])`; `__reduce__` raises `TypeError`; `vars()` fails. The value must be non-empty and at most 64 KiB (`MAX_SECRET_LENGTH`).
- `plaintext_secret_paths(config)`: the locations of secret material in a config document.
- `credential_references(config)`: all references anywhere in a config document.
- `PlaintextSecretError` (code `PLAINTEXT_SECRET`, HTTP 422).
- `Workflow.replace_draft` scans node and trigger configs and raises `PlaintextSecretError` before mutating the workflow.
- `ValidationCode.PLAINTEXT_SECRET` is emitted by `_check_node_config` in `graph_validation.py`, for both drafts and published versions.
- `CredentialResolver.resolve` in `domain/node.py` is now `async`.

### Security
- `security/crypto.py`: `SecretCipher` (AES-256-GCM, version byte, 12-byte random nonce, name bound as associated data), `SecretCipher.from_config` (URL-safe or standard base64, exactly 32 bytes), `SecretDecryptionError` (code `CREDENTIAL_DECRYPTION_FAILED`, carries no detail).
- `security/redaction.py`:
  - `register_secret`, `mask_known_secrets` and a task-scoped `ContextVar` `_known_secrets`;
  - `redact_text` masks known values before the pattern rules;
  - `MIN_KNOWN_SECRET_LENGTH = 4`;
  - `passwords` and `secrets` added to the sensitive key words;
  - the key/value text rule no longer masks `credential:<name>` references.

### Application
- `application/credentials.py`: `CredentialService` (`create`, `get`, `list`, `update`, `delete`, `resolve`), `CredentialNotFoundError` (404), `CredentialAlreadyExistsError` (409), and audit log lines `credential_created`, `credential_updated` (with `rotated`), `credential_deleted`, `credential_resolved`.
- `application/persistence.py`: `CredentialRepository` protocol; `UnitOfWork.credentials`.
- `application/workflows.py`: the private helpers `_check_page`/`_page` became public `check_page`/`to_page` so the credential service can reuse them. The behaviour is unchanged.

### Persistence
- `infrastructure/db_models.py`: table `credentials`.
- `infrastructure/persistence.py`: `SqlAlchemyCredentialRepository`. Metadata queries select explicit columns and exclude `ciphertext`. Insert uses `ON CONFLICT DO NOTHING`, so a duplicate name returns `False`. It is bound on `SqlAlchemyUnitOfWork`.
- `migrations/versions/0006_credentials.py`: reversible, purely additive.

### Runtime
- `runtime/credentials.py`: the `SecretSource` protocol and `NodeCredentials`, the per-attempt resolver with scope check, error mapping and value registration.
- `runtime/runtime.py`: `ExecutionRuntime(..., credentials: SecretSource | None = None)`. Each launched node gets `NodeCredentials(source, instance.config)` or `None`.
- `runtime/executor.py`: `execute_node` returns outputs passed through `mask_known_secrets`. This runs inside the node task, where the registered values are visible.

### Nodes
- `nodes/http.py`: optional config `header_credentials` (object: header name → reference). Each value is resolved and merged into the request headers. Without a resolver the node fails with `HTTP_CREDENTIALS_UNAVAILABLE`.
- `nodes/smtp.py`: `_password` is async and awaits `context.credentials.resolve`.

### API
- `api/credentials.py`: router, request/response models, dependency `_service` (raises `ConfigurationError` if the store is not configured). Registered in `api/routes.py`.

### Composition / Configuration
- `config.py`: `credential_encryption_key: SecretStr | None`.
- `bootstrap.py`: `Container.credentials`. `create_container` builds the `SecretCipher` before the database check, so a malformed key fails startup. `CredentialService` exists only when both database and key are configured.

### Testing
- New: `tests/test_credentials.py` (58 test cases) and `tests/integration/test_credentials.py` (29 test cases in 20 test functions).
- Adjusted existing tests (§4).

### Documentation
- `docs/architecture.md` §32 (implementation notes) and §109 (limit statement), plus this report.

---

## 4. Files Changed

| File | Change | Reason |
|---|---|---|
| `backend/src/akasha/domain/credentials.py` | Modified (+151/−10) | Credential model, `Secret`, stricter `CredentialReference`, config scanners, `PlaintextSecretError` |
| `backend/src/akasha/security/crypto.py` | Created (75 lines) | AES-GCM cipher and key parsing |
| `backend/src/akasha/security/redaction.py` | Modified (+51/−4) | Known-value masking, reference exemption, plural sensitive words |
| `backend/src/akasha/application/credentials.py` | Created (153 lines) | `CredentialService` and its errors |
| `backend/src/akasha/application/persistence.py` | Modified (+24) | `CredentialRepository` protocol, `UnitOfWork.credentials` |
| `backend/src/akasha/application/workflows.py` | Modified (+6/−6) | Page helpers made public for reuse |
| `backend/src/akasha/infrastructure/db_models.py` | Modified (+17) | `credentials` table definition |
| `backend/src/akasha/infrastructure/persistence.py` | Modified (+109) | `SqlAlchemyCredentialRepository`, UoW binding |
| `backend/migrations/versions/0006_credentials.py` | Created (41 lines) | Schema migration |
| `backend/src/akasha/runtime/credentials.py` | Created (56 lines) | `NodeCredentials`, `SecretSource` |
| `backend/src/akasha/runtime/runtime.py` | Modified (+7) | Optional `credentials` source; per-node resolver |
| `backend/src/akasha/runtime/executor.py` | Modified (+10/−3) | Mask resolved values in outputs |
| `backend/src/akasha/domain/node.py` | Modified (+3/−2) | Async `CredentialResolver` |
| `backend/src/akasha/domain/workflow.py` | Modified (+19/−1) | Plaintext guard in `replace_draft` |
| `backend/src/akasha/domain/graph_validation.py` | Modified (+12) | `PLAINTEXT_SECRET` validation issue |
| `backend/src/akasha/nodes/http.py` | Modified (+15) | `header_credentials` |
| `backend/src/akasha/nodes/smtp.py` | Modified (+3/−3) | Async resolution |
| `backend/src/akasha/api/credentials.py` | Created (139 lines) | REST endpoints and schemas |
| `backend/src/akasha/api/routes.py` | Modified (+2) | Router registration |
| `backend/src/akasha/config.py` | Modified (+6/−1) | Encryption key setting |
| `backend/src/akasha/bootstrap.py` | Modified (+9) | Cipher and service wiring |
| `backend/pyproject.toml`, `backend/uv.lock` | Modified | `cryptography~=46.0` (locks `cryptography 46.0.7`, `cffi 2.1.1`, `pycparser 3.0`) |
| `backend/tests/test_credentials.py` | Created (445 lines) | Unit and security tests |
| `backend/tests/integration/test_credentials.py` | Created (574 lines) | PostgreSQL, API, runtime and Mailpit tests |
| `backend/tests/domain/test_trigger_credentials_plugin.py` | Modified | Reference tests follow the stricter format (8 malformed cases) |
| `backend/tests/nodes/test_smtp_node.py` | Modified | `FakeCredentials.resolve` is async |
| `backend/tests/integration/test_persistence.py` | Modified | Migration head `0006_credentials`; `credentials` table created and dropped |
| `backend/tests/integration/conftest.py` | Modified | `clean_db` truncates `credentials` |
| `backend/tests/application/test_trigger_service.py` | Modified | `FakeUow.credentials` (the protocol grew an attribute) |
| `backend/tests/test_redaction.py` | Modified | Regression cases `secrets`, `user_passwords` |
| `docs/architecture.md` | Modified (+35/−3) | §32 implementation notes, §109 limit update |
| `docs/reports/backend/phase-13-credentials-secrets.md` | Created | This report |

No new directories; no files deleted; no generated files except `uv.lock`.

---

## 5. Architecture Changes

### 5.1 References name credentials; the name is the identity

- **Decision:** A reference is `credential:<name>`. `Credential` has no separate id, and the name is immutable.
- **Context:** The docs disagree on the reference format. backend/AGENTS §28 uses `credential:openai-production`; architecture §32 uses `credential_123`. The existing Phase-02 test already used the `credential:` form.
- **Chosen approach:** Name-based references. The `credentials.name` column is the primary key. `PATCH` cannot rename.
- **Alternatives considered:**
  - Opaque ids (`cred_<hex>`), consistent with other entities. Rejected: a workflow exported to another installation would reference ids that cannot exist there.
  - An id plus a mutable name. Rejected: renaming would silently break every reference.
- **Reason:**
  - Portability for Phase 18.
  - Rotation (new value) never touches workflows.
  - Consistent with backend/AGENTS §28.
- **Consequences:** Phase 18 import can require "a credential named X" on the target installation. Renaming a credential is only possible as delete plus recreate. `Credential` does not extend `Entity`.

### 5.2 Encryption in the application layer, before persistence

- **Decision:** `CredentialService` encrypts before calling the repository. The repository stores and returns bytes only.
- **Context:** Plaintext must not reach SQL, where it could surface in SQLAlchemy error parameters, logs or backups.
- **Chosen approach:** `SecretCipher` in the security layer, injected into the service. Associated data is the credential name.
- **Alternatives considered:**
  - Database-side encryption (`pgcrypto`). Rejected: the plaintext and key would travel to the database.
  - Fernet. Rejected: it offers no associated data, so a ciphertext copied onto another row would decrypt.
- **Reason:** Plaintext exists only in process memory, and row swapping is detected.
- **Consequences:** Backups contain ciphertext only; the key must be backed up separately (architecture §110). Rotating the key requires a re-encrypt procedure (not implemented).

### 5.3 Node-scoped resolution as the access boundary

- **Decision:** A node resolves only references that literally appear in its own config snapshot.
- **Context:** Without authentication, the meaningful boundary is "which code may unlock which secret". Runtime data (ports, execution input, webhook payloads) is caller-controlled. If a node resolved a reference received through a port, any caller could choose the secret.
- **Chosen approach:** `NodeCredentials` takes `credential_references(instance.config)` as its allow-list. Anything else raises `NodeError(code="CREDENTIAL_NOT_REFERENCED")` before the store is contacted.
- **Alternatives considered:**
  - An unrestricted resolver. Rejected: exfiltration via data flow.
  - Per-workflow credential ACLs. Deferred: that needs principals (Phase 20).
- **Consequences:**
  - `SECRET_REFERENCE` ports can carry references, but a receiving node cannot resolve them. That is intentional.
  - Phase 16 (Python Node SDK) must document that custom nodes resolve via `context.credentials` and only for their own config references.
  - Custom Python nodes are trusted code (backend/AGENTS §24), so this boundary protects against data-driven misuse, not against malicious node code.

### 5.4 Known-value masking through a task-scoped `ContextVar`

- **Decision:** Resolved values are registered in a `ContextVar` that redaction consults.
- **Context:** Pattern redaction (Phase 12) cannot recognise shapeless secrets; architecture §109 explicitly deferred exact-value masking to this phase.
- **Chosen approach:** `register_secret` adds the value to a frozenset in the current context. The runtime creates each node attempt as its own task, which copies the context, so the value is visible only within that node's task and threads started via `asyncio.to_thread`. The JSON formatter and `redact_text` mask it; `execute_node` masks outputs.
- **Alternatives considered:**
  - A process-global registry. Rejected: it is a hidden singleton, and it would mask one execution's secrets in unrelated executions' data.
  - Masking only in the executor. Rejected: log lines emitted by node code would still leak.
- **Consequences:** Masking is exactly as wide as the node task (limitations in §20). It needs no cleanup, because the context dies with the task.

### 5.5 Workflow configuration is structurally secret-free

- **Decision:** Secret material is rejected at `Workflow.replace_draft`, the domain method every draft save goes through, and reported by graph validation for legacy data.
- **Context:** The acceptance criterion requires that secrets never appear in Workflow JSON. Previously nothing prevented `headers: {"Authorization": "Bearer …"}` in a draft.
- **Chosen approach:** `plaintext_secret_paths` reuses the Phase-12 redaction rules:
  - a string below a sensitive key must be a reference;
  - no other string may be credential-shaped.
- **Alternatives considered:**
  - Marking secret fields in node config schemas (e.g. a `format`). Rejected for now: it only protects schemas that declare it, and open schemas (`headers: {"type": "object"}`) would still accept secrets.
- **Reason:** The same rules protect logs and workflow data, so the definition of "looks like a secret" lives in one module.
- **Consequences:**
  - Drafts that were acceptable before are now rejected (breaking by design).
  - Data stored earlier can still be loaded, but can be neither published nor run until it is fixed.
  - Nodes needing secret headers must use `header_credentials`.

---

## 6. Domain / Data Model Changes

**Entities and value objects**
- `Credential` (new): `name`, `kind`, `description`, `timestamps`. It holds no value field; the secret is never part of the domain object.
- `Secret` (new): an in-memory plaintext wrapper that is not serializable.
- `CredentialReference` (changed): stricter validation. Previously any non-blank string without whitespace was accepted. Only tests used it, so there was no persisted data to migrate.
- `CredentialKind` (new enum): `api_key`, `smtp`, `provider`, `generic`.
- `ValidationCode.PLAINTEXT_SECRET` (new enum member).

**Database table `credentials`** (migration `0006_credentials`, `down_revision = 0005_events`)

| Column | Type | Constraints |
|---|---|---|
| `name` | `VARCHAR` | primary key |
| `kind` | `VARCHAR` | not null; `ck_credentials_kind` (`kind IN ('api_key','smtp','provider','generic')`) |
| `description` | `VARCHAR` | not null, default `''` |
| `ciphertext` | `BYTEA` | not null; format `0x01 ‖ nonce(12) ‖ ciphertext+tag` |
| `created_at`, `updated_at` | `TIMESTAMPTZ` | not null |

- There are no foreign keys: workflows reference credentials by name inside JSONB config, and a dangling reference is detected at resolution time.
- The downgrade drops the table.
- Compatibility: purely additive; existing rows are untouched.

---

## 7. API / Contract Changes

### REST (`/api/v1/credentials`, tag `credentials`; error bodies use the existing `ErrorResponse`)

| Endpoint | Request | Response | Errors |
|---|---|---|---|
| `POST /api/v1/credentials` | `CreateCredentialRequest {name, kind="generic", description="" (≤2000), value: SecretStr (1..65536)}` | 201 `CredentialDetail` | 422 `VALIDATION_ERROR` / `DOMAIN_VALIDATION_ERROR` (bad name), 409 `CREDENTIAL_ALREADY_EXISTS`, 500 `CONFIGURATION_ERROR` (store not configured) |
| `GET /api/v1/credentials?offset&limit` | `limit` 1..100 | 200 `CredentialPage {items, page{offset,limit,has_more}}`, ordered by name | 422, 500 |
| `GET /api/v1/credentials/{name}` | — | 200 `CredentialDetail` | 404 `CREDENTIAL_NOT_FOUND` |
| `PATCH /api/v1/credentials/{name}` | `UpdateCredentialRequest {description?, value?}`; omitted/null fields stay unchanged; `value` rotates | 200 `CredentialDetail` | 404, 422 |
| `DELETE /api/v1/credentials/{name}` | — | 204 | 404 |

`CredentialDetail = {name, reference, kind, description, created_at, updated_at}`. No response model has a value field. In OpenAPI, `value` is `writeOnly`.

### Changed existing contracts
- **Workflow draft save** (`PUT /api/v1/workflows/{id}`): a new error, 422 `PLAINTEXT_SECRET`, with `details.paths` (e.g. `nodes.<id>.config.headers.Authorization`). Drafts with secret material that were accepted before are now rejected.
- **Validation responses** (`POST …/validate`, publish failure): a new issue code, `plaintext_secret`. Frontend enums must accept it.
- **Node contract:** `CredentialResolver.resolve` is now `async def resolve(reference: str) -> str`. Runtime node errors: `CREDENTIAL_NOT_REFERENCED` and `CREDENTIAL_NOT_FOUND` (kind `configuration`), `CREDENTIAL_UNAVAILABLE` (kind `permanent`), `HTTP_CREDENTIALS_UNAVAILABLE` (kind `configuration`).
- **`core.http` config schema:** optional `header_credentials: object`. The node version stays `1.0.0` (§20).
- **Events:** no change. There are no credential event types; audit is by log line (§22).

---

## 8. Implementation Details

**Create flow.** API validates the body (`SecretStr`), then `Secret(value)`, then `CredentialService.create`:
1. Build `Credential`; the name is validated here, giving `DomainValidationError` on a bad name.
2. `cipher.encrypt(value, context=name)`.
3. `uow.credentials.add`. `False` means `CredentialAlreadyExistsError`.
4. Commit, then write the audit log line.

**Update flow.** Load the credential (404 if missing), then set the description and/or encrypt the new value, then `touch()`, then `update(credential, ciphertext | None)`. A `False` result means it was deleted concurrently, which gives 404. Single-statement updates are last-write-wins; there is no revision column.

**Resolution flow at runtime.**
1. `ExecutionRuntime._launch` builds `NodeCredentials(self._credentials, instance.config)` for each attempt.
2. The node calls `await context.credentials.resolve(ref)`.
3. The scope check runs.
4. `CredentialService.resolve` parses the reference, loads only the ciphertext in its own short transaction, decrypts, logs `credential_resolved` and returns a `Secret`.
5. `reveal()`, then `register_secret(value)`, then the plaintext `str` goes to the node.

Resolution happens while the node runs, never at publish or queue time, and the value is never written back to config. A rotated value is picked up by the next execution without republishing (tested).

**Error mapping.**
- `CredentialNotFoundError` → `CREDENTIAL_NOT_FOUND`.
- `SecretDecryptionError` (wrong key, tampering, corruption, unknown version byte) → `CREDENTIAL_UNAVAILABLE`.
- Both use `from None`, so no cause chain is persisted.
- Other exceptions (e.g. a database outage during resolution) fall through to the executor's existing `NODE_UNEXPECTED_ERROR` path, which records the type name only.

**Redaction order.** `redact_text` masks registered values first (longest first, so overlapping values are masked whole), then applies the pattern rules. `mask_known_secrets` walks JSON-like values and masks strings and string mapping keys; it is a no-op when nothing is registered.

**Configuration and startup.**
- The key is read once via `Settings` (`SecretStr`, not shown by `repr`/`model_dump`). `SecretCipher.from_config` raises `ConfigurationError` without chaining the decode exception, so no key fragments appear in tracebacks.
- Without a key the engine boots and the credential endpoints answer `CONFIGURATION_ERROR`, mirroring the existing "no database" behaviour.

**Dependency injection.** `CredentialService(uow_factory, cipher)`; `ExecutionRuntime(..., credentials=<SecretSource>)`. Tests use the real service against PostgreSQL, or `FakeSource` for unit tests of `NodeCredentials`.

---

## 9. Findings

### F1 — Plural secret keys were not sensitive
- **Finding:** `is_sensitive_key("secrets")` and `is_sensitive_key("passwords")` returned `False` (the word list only had singular forms and `credentials`). This is pre-existing Phase-12 behaviour: `{"secrets": [...]}` in log `extra` was not masked. The new config guard inherited the gap; the first run of `test_plaintext_secrets_are_located[{"secrets": ...}]` failed.
- **Impact:** MEDIUM
- **Resolution:** FIXED
- **Notes:** `tokens` stays deliberately non-sensitive (Phase-12 decision: counts such as `tokens: 42`).

### F2 — Reference syntax collided with a redaction pattern
- **Finding:** The Phase-12 key/value text rule matched `credential:<name>`, so an error such as `credential not found: credential:crm` would have been rendered as `credential not found: [REDACTED]`.
- **Impact:** LOW (diagnosability, not a leak)
- **Resolution:** FIXED (negative lookahead `(?!credential:[a-z0-9])`).
- **Notes:** `credential: hunter2` (with a space) and `password:hunter2` stay masked (`test_references_stay_readable_in_redacted_text`). Trade-off: `credential:<slug>` without a space is never masked, because it is by definition reference syntax.

### F3 — Output masking initially covered values only
- **Finding:** The first `mask_known_secrets` did not mask mapping keys, so a node could emit `{"<secret>": ...}`.
- **Impact:** MEDIUM
- **Resolution:** FIXED
- **Notes:** Found in review; covered by the `LeakyNode` test (`doc` output with the secret as a key).

### F4 — Forbidding plaintext headers removed HTTP authentication
- **Finding:** With the config guard, `core.http` had no way left to send an `Authorization` or API-key header.
- **Impact:** MEDIUM (functional regression the phase would otherwise have introduced)
- **Resolution:** FIXED (`header_credentials`).
- **Notes:** Verified end to end against the local echo server.

### F5 — The runtime is not constructed by production code
- **Finding:** `Container` still does not create an `ExecutionRuntime` (open since Phase 11, noted in Phase 12 F7). The credential source is accepted by the runtime but cannot be wired in the API process.
- **Impact:** MEDIUM
- **Resolution:** DEFERRED (Phases 21/22)
- **Notes:** `Container.credentials` carries a comment that the runtime must receive the same service.

### F6 — Docker probe flake skips the integration suite
- **Finding:** During final verification, one of three full-suite runs reported `861 passed, 207 skipped`. `tests/support.docker_available()` pings the daemon with `timeout=2`; a slow response makes every Testcontainers-backed test skip. The same 207 tests passed immediately in an isolated run, and two further full runs passed 1068/1068.
- **Impact:** MEDIUM (a false-green risk: a CI run could pass with integration tests skipped)
- **Resolution:** OPEN (pre-existing, same as Phase 12 F6)
- **Notes:** Not changed in this phase (out of scope). Recommendation: fail instead of skip when CI requires Docker, or raise the timeout.

---

## 10. Bugs / Problems Found

| # | Problem | Root cause | Fix | Regression test | Affected area | Origin |
|---|---|---|---|---|---|---|
| 1 | `secrets` / `passwords` keys not redacted | Word list singular-only | Added plurals to `_SENSITIVE_WORDS` | `test_redaction.py::test_sensitive_keys[secrets, user_passwords]`, `test_plaintext_secrets_are_located[config8]` | Logging, config guard | Pre-existing (Phase 12) |
| 2 | References masked in errors and logs | Text rule matched `credential:` | Lookahead exemption | `test_references_stay_readable_in_redacted_text` | Redaction | Introduced by this phase's reference format |
| 3 | Secret as output mapping key not masked | `mask_known_secrets` ignored keys | Mask string keys | `test_outputs_and_logs_never_carry_a_resolved_secret` | Executor | Introduced and fixed in this phase |
| 4 | HTTP node could no longer authenticate | Guard rejects secret headers | `header_credentials` | `test_http_node_sends_the_resolved_secret_but_nothing_else_keeps_it` | `core.http` | Introduced and fixed in this phase |
| 5 | Existing tests broke on the new contracts (`CredentialReference` "blank" test, sync `FakeCredentials`, migration head, `FakeUow` missing `credentials`) | Intentional contract changes | Tests updated to the new contract | — | Tests | Consequence of this phase |
| 6 | Integration tests intermittently skipped | 2 s Docker ping | None (out of scope) | — | Test infrastructure | Pre-existing, environmental |

---

## 11. Fixes Applied

1. **Plural sensitive keys.**
   - *Symptom:* a parametrized guard test failed.
   - *Root cause:* F1.
   - *Implementation:* `"passwords"` and `"secrets"` added in `security/redaction.py`.
   - *Verification:* `tests/test_credentials.py` and `tests/test_redaction.py` pass (103 tests at that point).
2. **Reference exemption in the text rule.**
   - *Symptom:* found in design review of the error messages.
   - *Implementation:* lookahead in `_TEXT_RULES`.
   - *Verification:* the dedicated test, plus the whole `test_redaction.py` suite unchanged.
3. **Mapping-key masking.**
   - *Symptom:* found in review.
   - *Implementation:* dict comprehension masks string keys.
   - *Verification:* `LeakyNode` asserts `{MASK: "as key"}`.
4. **HTTP credential headers.**
   - *Symptom:* functional gap.
   - *Implementation:* `header_credentials` in `nodes/http.py`.
   - *Verification:* the echo-server test asserts the header reached the server and the persisted output is masked.
5. **Type and lint fixes in new tests** (not product bugs): `SecretStr` arguments in `Settings(...)`, a `blob: object` annotation, removal of an unused unpacked variable, and `cast(CredentialRepository, None)` in `FakeUow`.

---

## 12. Testing & Verification

### Unit Tests — `tests/test_credentials.py` (58 cases, no infrastructure)
- **Cipher:**
  - round trip;
  - ciphertext differs for equal plaintexts (random nonce);
  - 5 tamper variants (tag bit, ciphertext bit, version byte, truncation, empty) raise `SecretDecryptionError`;
  - associated-data binding (other context fails);
  - wrong key fails without detail or cause;
  - URL-safe and standard base64 accepted;
  - 4 bad keys raise `ConfigurationError`;
  - `repr` holds no key material;
  - a malformed key fails `create_container` without echoing it;
  - `Settings` `repr`/`model_dump`/`model_dump_json` do not contain the key;
  - no key means no service.
- **`Secret`:** masked in `str`, `repr`, f-strings, `format()`, container reprs and `redact()`; `pickle` and `json.dumps` raise; `vars()` raises; empty and oversize values rejected.
- **Model:** 6 invalid names; the reference round trip; `credential_references` finds nested references and ignores prose and empty references.
- **Guard:** 9 configurations with located paths; 7 clean configurations (references, ordinary strings, `*_id`/`*_count`, non-string values); `replace_draft` rejects without storing and without echoing; legacy data produces a `PLAINTEXT_SECRET` validation issue without the value.
- **Known-value masking:** masked within the resolving task only; the caller context is unaffected; secrets under 4 characters are not registered; references stay readable.
- **Scope:** an unreferenced reference is refused before the store is called; not-found and decryption failures map to node errors without a cause.
- **Executor attack (`LeakyNode`):** the node logs the value (message and `extra`), logs an exception with a traceback, and returns it in a string, a nested list and as a mapping key. Also run with a `NodeError` containing it and with an unexpected `ValueError(value)`. Outputs, the error message, details and `repr`, and all JSON log lines are canary-free.
- **Result:** 58 passed.

### Integration Tests — `tests/integration/test_credentials.py` (29 cases, PostgreSQL `postgres:17.2`, Mailpit `axllent/mailpit:v1.21.8`, local HTTP server)
- **Persistence:**
  - encryption at rest (the raw `ciphertext` does not contain the canary bytes; a text dump of all tables is canary-free);
  - paging and name ordering;
  - duplicate conflict keeps the original value;
  - description-only update keeps the value; rotation changes it and the old value is gone from the database;
  - delete (get/resolve/delete/update then raise not-found);
  - row-swap tampering fails to decrypt;
  - a service with a different key cannot decrypt;
  - audit log sequence without the value.
- **Runtime end to end:**
  - `core.http` with `header_credentials` delivers the secret to the server, while the persisted output, all database rows (including events) and all logs are canary-free;
  - a missing credential gives a FAILED execution with `CREDENTIAL_NOT_FOUND`;
  - rotation takes effect on the next execution without republishing;
  - `core.smtp` authenticates against Mailpit with a stored credential.
- **Result:** 29 passed.

### API Tests (in the integration file, real ASGI app)
- Full lifecycle (create/list/get/patch rotate/patch description/delete) with the canary absent from every response body and every log line.
- 7 invalid bodies plus 1 malformed JSON body never echo the value.
- Duplicate returns 409; an unconfigured store returns 500 `CONFIGURATION_ERROR`.
- OpenAPI: `CredentialDetail` has no `value`; the request `value` is `writeOnly`.
- The workflows API rejects 4 plaintext shapes with 422 `PLAINTEXT_SECRET` (nothing stored); a reference-only workflow is stored and returned with the reference only.
- **Result:** PASS.

### Infrastructure Tests
- Migration test (`test_migrations_upgrade_downgrade_upgrade_on_empty_database`) now asserts head `0006_credentials`, the `credentials` table present after upgrade and absent after downgrade. PASS.

### Security Tests
- All tests above are leak and attack tests.
- **Mutation check** (manual; each mutation applied, the credential suites run, then the file restored; the final gate runs confirm the restored state):

  | Disabled protection | Failing tests |
  |---|---|
  | output masking | 2 |
  | known-value text masking | 4 |
  | scope check | 1 |
  | secret registration | 5 |
  | draft guard | 5 |
  | associated data on encrypt | 10 |
  | encryption | 8 |
  | masked `Secret.__repr__` | 3 |

- No dependency audit tool (e.g. `pip-audit`) was run; none is configured in the repository.

Not performed: component, E2E, accessibility and performance tests (not applicable, or no such suites exist).

---

## 13. Quality Gates

Commands were run in `backend/` on the final working tree.

| Quality Gate | Result | Notes |
|---|---|---|
| Formatter | PASS | `uv run ruff format --check .`: 121 files already formatted |
| Linter | PASS | `uv run ruff check .`. One documented suppression in tests: `# noqa: S608` on the table-dump query (table names come from `pg_catalog`, not user input) |
| Type Checker | PASS | `uv run mypy .`: strict, 119 source files. Earlier in the phase, 4 errors in new or changed tests were fixed |
| Unit Tests | PASS | Part of the full run |
| Integration Tests | PASS | 207 Docker-backed tests; see Other |
| API Tests | PASS | Part of the integration suite |
| E2E Tests | N/A | No E2E suite exists |
| Build | PASS | `uv build`: wheel built (artifact removed afterwards) |
| Security Checks | PASS | Leak/attack tests and mutation check (§12); no dependency audit configured |
| Accessibility | N/A | Backend phase |
| Other: full suite | PASS | `uv run pytest`: 1068 passed, 0 skipped in two consecutive runs. An earlier run reported 861 passed / 207 skipped (Docker probe flake, F6); the isolated integration run passed 207/207. One warning: `StarletteDeprecationWarning` from `fastapi.testclient` (third-party, pre-existing) |

---

## 14. Acceptance Criteria Verification

The roadmap states one acceptance criterion; it is evaluated per channel it names, plus the required test areas.

| Acceptance Criterion | Status | Evidence |
|---|---|---|
| Secrets never appear in Workflow JSON | PASS | `Workflow.replace_draft` raises `PLAINTEXT_SECRET`; `test_workflow_api_rejects_plaintext_secrets` (4 shapes, database dump clean); `test_workflow_stores_and_returns_only_the_reference`; legacy data blocked by the `PLAINTEXT_SECRET` validation issue |
| Secrets never appear in normal API responses | PASS | No value field in any response model (OpenAPI test); `test_api_lifecycle_never_returns_the_value`; `test_invalid_requests_never_echo_the_value`; malformed-JSON test |
| Secrets never appear in logs | PASS | Service audit-log test; API lifecycle log capture; `LeakyNode` log attempts including traceback; runtime end-to-end log capture |
| Secrets never appear in execution reports | PARTIAL | No report generator exists (Phase 19). The data a report would draw from is verified: persisted node outputs, errors and events contain no resolved value (database dump in the runtime test). Not blocking: the subsystem does not exist yet. |
| Test: encryption/decryption | PASS | Cipher unit tests; encryption-at-rest integration test |
| Test: access control | PARTIAL | Structural boundary tested (node scope; no read-back endpoint; wrong key). User/role authorization does not exist before Phase 20. Not blocking per roadmap order. |
| Test: reference resolution | PASS | `NodeCredentials` unit tests; HTTP, SMTP, missing-credential and rotation runtime tests |
| Test: redaction | PASS | Known-value masking tests; `test_redaction.py` extended |
| Test: export exclusion | PARTIAL | No export feature (Phase 18). The workflow JSON that export will serialize is proven reference-only. Not blocking. |
| Test: log exclusion | PASS | See the logs row |
| Prompt: events, exceptions, DB serialization, debug output | PASS | Database dump includes the `events` table; exception tests (`NodeError`, `ValueError`, `SecretDecryptionError`, key errors); `Secret`/`Settings`/`SecretCipher` repr tests |
| Prompt: keys from secure configuration, never hard-coded | PASS | `Settings.credential_encryption_key: SecretStr`; no key in the source tree (tests generate random keys with `os.urandom`) |

The PARTIAL rows concern subsystems scheduled for later phases, not missing work in this phase's scope. Hence COMPLETE WITH LIMITATIONS.

---

## 15. Security Considerations

- **Secrets at rest:** AES-256-GCM with a random 96-bit nonce per encryption. The associated data binds the ciphertext to the credential name. A version byte allows future key or format changes. Decryption errors are deliberately undifferentiated.
- **Key handling:**
  - From environment only, held as `SecretStr`, never logged.
  - A malformed key fails startup; an absent key disables the store.
  - Losing the key makes credentials unrecoverable, so it must be backed up separately from the database (architecture §110).
- **Authentication/authorization:** none (Phase 20). The credential endpoints are as open as all other v1 endpoints. Anyone with API access can create, rotate or delete credentials, but cannot read values.
- **Access boundary:** node-scoped resolution (§5.3). It protects against data-driven secret selection, not against malicious trusted node code.
- **Input validation:**
  - name regex;
  - `kind` enum;
  - value 1..65536 characters (API and domain);
  - description ≤ 2000 characters;
  - pagination bounds.
- **Error disclosure:** validation errors expose only `loc` and `msg` (pydantic's `input` is dropped by the existing handler; verified). Domain errors name locations, never values.
- **Logging:** audit lines carry name and kind. Resolution is logged, which is useful for auditing secret use.
- **Residual risks:** masking gaps listed in §20; the Docker-probe false-green risk (F6).
- **SSRF:** unchanged. `core.http`'s destination policy still applies before credential headers are sent to any destination.

---

## 16. Performance Considerations

- Each `resolve` opens its own unit of work: one `SELECT` of the ciphertext by primary key, plus an AES-GCM decrypt (microseconds). A node resolving the same reference several times repeats this; there is no cache.
- Credential resolution does not hold a transaction across node execution.
- `mask_known_secrets` is a no-op when nothing was registered. Otherwise it walks the whole output once, which is proportional to output size.
- `redact_text` does one `str.replace` per registered value per call; the set is per node task and typically has one or two entries.
- `plaintext_secret_paths` walks every node and trigger config on each draft save, and again on validation. This is linear in config size and runs regex rules per string. No measurements were taken.
- The metadata list uses `ORDER BY name` with `LIMIT/OFFSET` on the primary-key index.

---

## 17. Documentation Changes

| File | Reason | Content |
|---|---|---|
| `docs/architecture.md` §32 | Record the implementation | Reference format and rationale, storage and key handling, API, config guard, resolution boundary, known-value masking |
| `docs/architecture.md` §109 | The Phase-12 limit statement said exact-value masking was pending | Updated limit: resolved values are masked in the resolving node's context; secrets outside the store remain pattern-only |
| `docs/reports/backend/phase-13-credentials-secrets.md` | Phase report | This document |
| Module docstrings | In-code design notes | `domain/credentials.py`, `security/crypto.py`, `application/credentials.py`, `runtime/credentials.py`, `api/credentials.py` |

**Should be updated later:**
- An operations guide for generating, storing and backing up `AKASHA_CREDENTIAL_ENCRYPTION_KEY` (Phase 26).
- Python Node SDK documentation on `context.credentials` and scope rules (Phase 16).
- `README.md` does not yet list the new environment variable.

---

## 18. Dependencies / Infrastructure Changes

- **New runtime dependency:** `cryptography~=46.0` (locked `46.0.7`, bringing `cffi 2.1.1` and `pycparser 3.0`). The standard library has no authenticated encryption, and implementing AES-GCM in-house is not acceptable (AGENTS §53). `cryptography` is maintained by the PyCA, dual Apache-2.0/BSD licensed, and the de-facto standard.
- **New environment variable:** `AKASHA_CREDENTIAL_ENCRYPTION_KEY` (optional; 32 bytes, base64). Generate with `openssl rand -base64 32`.
- **Database:** migration `0006_credentials`.
- **Test infrastructure:** `clean_db` truncates `credentials`. The integration tests reuse the existing PostgreSQL container, the Mailpit helper (`run_mailpit(auth=True)`) and `serve_http`. No new images.
- No Docker, Redis or Compose changes.

---

## 19. Deferred Work

| What | Why deferred | Intended phase | Blocks this phase? |
|---|---|---|---|
| Passing `container.credentials` to a production `ExecutionRuntime` | No production runtime construction exists yet | 21/22 | No (pre-existing gap) |
| User/role authorization on credentials and per-workflow scoping | Needs principals | 20 | No |
| Export exclusion test on the real export package | Export does not exist | 18 | No |
| Report exclusion test on real reports | Reports do not exist | 19 | No |
| Key rotation (key id per blob, re-encrypt job, multi-key decrypt) | Operational feature; the format reserves a version byte | 26 | No |
| Credential audit events (instead of log lines) | Event types are execution/workflow scoped; an audit stream belongs to authentication/audit | 20 | No |
| LLM provider credentials usage | Provider architecture not built | 14/15 | No |
| Fixing the Docker-probe flake | Pre-existing, out of scope | Test infrastructure / CI | No |

---

## 20. Known Limitations

- **Masking gaps.** Known-value masking does not cover:
  - tasks spawned by a node before it resolved the value;
  - `bytes` outputs (`BINARY` ports);
  - transformed forms of the value (base64, URL-encoding, hashes; `Basic …` is still pattern-masked);
  - values shorter than 4 characters.
- **Out-of-band secrets.** Secrets supplied outside the store (execution input, webhook payloads) are masked only if credential-shaped.
- **Guard false positives.** Any string under a key that looks sensitive (e.g. `auth_mode`, `token_limit` as a string) must be a reference; renaming the key is the remedy.
- **Guard ceiling.** The guard cannot detect a shapeless secret under an innocuous key (e.g. `{"note": "hunter2"}`).
- **Deleting credentials.** Delete does not check for referencing workflows; their executions fail with `CREDENTIAL_NOT_FOUND`.
- **Concurrent updates.** No revision on credentials: concurrent `PATCH` calls are last-write-wins.
- **Node version.** `core.http` version stays `1.0.0` despite the additive `header_credentials` key.
- **No caching.** Each `resolve` call hits the database.
- **Resolution failures are not retryable.** A database outage during resolution becomes `NODE_UNEXPECTED_ERROR`, whose `PERMANENT` kind disables retries even though the cause may be transient.

---

## 21. Open Issues

| Issue | Impact | Severity | Area | Recommended next action | Target |
|---|---|---|---|---|---|
| Runtime not wired with the credential store in production | Credential-using nodes cannot run from the API process | MEDIUM | Composition | Construct `ExecutionRuntime(..., credentials=container.credentials)` | 21/22 |
| Docker probe can skip all integration tests | False-green test runs | MEDIUM | Test infrastructure | Fail instead of skip in CI (e.g. an env flag), or raise the ping timeout | Next CI/infrastructure work |
| No authorization on `/api/v1/credentials` | Any API caller can create, rotate or delete credentials | HIGH (for deployment) | Security | Router-level auth dependency | 20 |
| Resolution infrastructure errors are not retryable | A transient database outage fails the node permanently | LOW | Runtime | Map infrastructure errors in `NodeCredentials` to `TRANSIENT` | 23 |
| No key-rotation procedure | Cannot replace a compromised key | MEDIUM | Operations | Key id plus re-encrypt command | 26 |

---

## 22. Architectural / Product Decisions

- **Name-based references (`credential:<name>`), immutable names, no credential id.** Portability and rotation without workflow changes (§5.1).
- **Encrypt in the application layer with AES-GCM and the name as associated data; `cryptography` over Fernet or `pgcrypto`.** Plaintext never reaches the database; row swaps are detected (§5.2).
- **No read-back API for values.** Only the runtime decrypts. A future "reveal" feature would need an explicit decision plus Phase-20 authorization.
- **Node-scoped resolution.** References from runtime data are never resolvable. Keep this invariant when building the SDK (Phase 16) and LLM nodes (Phase 15) (§5.3).
- **Task-scoped known-value masking instead of a global registry** (§5.4).
- **The plaintext guard reuses the redaction rules** (one definition of "looks secret") and raises in the domain rather than as a validation issue. Storing is the harm, so "drafts may be invalid" does not apply (§5.5).
- **`CredentialResolver` is async,** because resolution is I/O. All future nodes must `await` it.
- **Audit is by structured log line, not events,** for this phase.
- **A missing key disables the store; it does not fail startup.** This mirrors the optional database; only a malformed key fails fast.

---

## 23. Repository / Git State

- **Branch:** `main`. Last commit: `c6b3045 backend phase 12 - observability`. Phase 13 is uncommitted.
- **Working tree:** 25 modified tracked files (+658/−44 lines, including `uv.lock` +149) and 8 untracked files:
  - `backend/migrations/versions/0006_credentials.py`
  - `backend/src/akasha/api/credentials.py`
  - `backend/src/akasha/application/credentials.py`
  - `backend/src/akasha/runtime/credentials.py`
  - `backend/src/akasha/security/crypto.py`
  - `backend/tests/integration/test_credentials.py`
  - `backend/tests/test_credentials.py`
  - `docs/reports/backend/phase-13-credentials-secrets.md`
- **Unrelated changes:** none detected. Every modified file belongs to this phase (§4).
- `backend/dist/` was created by `uv build` during verification and removed afterwards.

---

## 24. Phase Completion Assessment

### Implemented
- Credential model, reference, `Secret`, cipher, service, repository, migration and REST API.
- Node-scoped resolver, known-value redaction, workflow-config guard, HTTP `header_credentials`, async SMTP resolution.

### Verified
- 87 new tests (58 unit, 29 integration/API/runtime); a mutation check of 8 protections; all quality gates pass.
- 1068/1068 tests in two consecutive full runs.

### Not Implemented
- User-level authorization; export and report exclusion on the real features (the features do not exist yet).

### Deferred
- Runtime wiring (21/22), authorization (20), export (18), reports (19), key rotation (26), audit events (20).

### Open
- Runtime wiring; Docker-probe flake; unauthenticated credential API; non-retryable infrastructure errors during resolution; key rotation (§21).

### Blocking Issues
- None for this phase.

**Phase Status:** COMPLETE WITH LIMITATIONS

---

## 25. Handoff to Next Phase

**Phase 14 — AI Provider Architecture, and later phases**

**The next phase can assume:**
- A `CredentialService` with `resolve("credential:<name>") -> Secret`.
- `CredentialKind.PROVIDER` exists for provider API keys.
- Nodes get secrets only through `await context.credentials.resolve(ref)`, and only for references in their own config.
- Resolved values are masked in that node's logs, errors and outputs.

**Contracts available:**
- `/api/v1/credentials`;
- `CredentialResolver` (async);
- node error codes `CREDENTIAL_NOT_REFERENCED`, `CREDENTIAL_NOT_FOUND`, `CREDENTIAL_UNAVAILABLE`;
- validation code `plaintext_secret`;
- workflow error `PLAINTEXT_SECRET`.

**Provider adapters (Phase 14/15):**
- Take the API key as a resolved string passed in by the LLM node, which resolves its own `api_key_credential`-style config reference.
- An adapter must not look up credentials itself, or the node-scope boundary is bypassed.
- Provider error mapping must not include request headers or the key. Provider SDK exceptions often embed request details, so map them to messages without them.
- Adapter calls that run in other tasks, or outside the node's task, are not covered by known-value masking; keep them within the node task or register the value there.

**Must not be changed casually:**
- the `credential:<name>` format and immutable names (workflows and exports depend on them);
- the associated-data binding and blob layout (stored data depends on them);
- the node-scope rule;
- write-only values in the API;
- the order in which `redact_text` applies known values before patterns.

**Prerequisites:** `AKASHA_CREDENTIAL_ENCRYPTION_KEY` must be set wherever credentials are used; tests generate random keys. Whoever constructs the production `ExecutionRuntime` must pass `credentials=container.credentials`.
