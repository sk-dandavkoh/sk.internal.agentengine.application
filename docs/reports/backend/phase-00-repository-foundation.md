# Phase 00 — Repository Foundation

## 1. Executive Summary

Phase 00 establishes the reproducible backend development foundation for the Agent Orchestration Engine, without implementing any business functionality.

Implemented: a `uv`-managed Python project under `backend/` with src-layout package `akasha`, a locked dependency set (`uv.lock`), pytest/ruff/mypy configuration, a minimal application entry point (`akasha.main:main`, exposed as the `akasha` console script and `python -m akasha`), two baseline tests, a `migrations/` placeholder directory, and a `backend/README.md` documenting setup and quality gates.

Resulting state: `uv sync` creates a working environment from scratch; formatter, linter, strict type checker and test suite all pass; the backend process starts and exits with code 0. No domain, API, persistence, or runtime code exists, as required.

**Phase Status:** COMPLETE

---

## 2. Phase Objective

Per `/docs/roadmap-backend.md` §4, Phase 00:

> Create the backend development foundation without implementing business functionality.

Scope: `backend/pyproject.toml`, `backend/src/`, `backend/tests/`, `backend/migrations/`; configure Python version, uv, linting, formatting, type checking, pytest, test discovery, and a basic application entry point; establish the package naming convention.

Explicitly NOT in scope (deferred to later phases): layer structure (`api/`, `domain/`, ...) and health/readiness endpoints (Phase 01), domain model (Phase 02), Alembic setup and PostgreSQL (Phase 05), and everything beyond.

---

## 3. Scope Implemented

### Project Configuration
- `pyproject.toml` with project metadata (`name = "akasha-engine"`, `version = "0.1.0"`), `requires-python = ">=3.12"`, empty runtime dependency list, `[project.scripts] akasha = "akasha.main:main"`.
- Hatchling build backend with wheel target `src/akasha` (editable-installable via `uv sync`).
- Dependency lock via `uv.lock` (committed; verified with `uv lock --check`).

### Source Layout
- Package `akasha` under `src/` (src layout).
- `src/akasha/__init__.py` — defines `__version__ = "0.1.0"`.
- `src/akasha/main.py` — `main() -> int` entry point: prints `akasha-engine 0.1.0` to stdout, returns 0. Docstring explicitly notes bootstrap/config/HTTP arrive in Phase 01.
- `src/akasha/__main__.py` — enables `python -m akasha`.

### Testing
- `tests/test_foundation.py` with two tests:
  - `test_version_is_defined` — package imports, `__version__` non-empty.
  - `test_application_starts` — `main()` returns 0.
- pytest configured in `pyproject.toml`: `testpaths = ["tests"]`, `addopts = "-v"`.

### Tooling Configuration
- Ruff: `target-version = "py312"`, `src = ["src", "tests"]`, lint rules `E, F, I, UP, B, ASYNC, S, T20, RUF`; per-file ignore of `S101` (assert) for `tests/**` only — pytest idiom.
- mypy: `strict = true`, `mypy_path = "src"`, `packages = ["akasha"]`.

### Documentation
- `backend/README.md` — requirements (Python ≥ 3.12, uv), setup (`uv sync`), run, quality-gate commands, layout description.

### Directories
- `backend/migrations/` created with a `.gitkeep` noting Alembic arrives in Phase 05.

---

## 4. Files Changed

| File | Change | Reason |
|---|---|---|
| `backend/pyproject.toml` | Created | Project/deps/tooling configuration |
| `backend/uv.lock` | Created (generated) | Reproducible dependency lock (13 packages resolved) |
| `backend/src/akasha/__init__.py` | Created | Package root, `__version__` |
| `backend/src/akasha/main.py` | Created | Application entry point |
| `backend/src/akasha/__main__.py` | Created | `python -m akasha` support |
| `backend/tests/test_foundation.py` | Created | Baseline tests required by roadmap |
| `backend/README.md` | Created | Developer onboarding documentation |
| `backend/migrations/.gitkeep` | Created | Keep `migrations/` tracked; Alembic deferred to Phase 05 |
| `backend/.gitignore` | Unchanged | Existing GitHub Python template already covers `.env`, `.venv`, `.ruff_cache`, `.mypy_cache`, `.pytest_cache` |
| `backend/AGENTS.md` | Unchanged | — |

New directories: `backend/src/akasha/`, `backend/tests/`, `backend/migrations/`.

All changes are untracked in git (working tree, `git add`/commit not performed — user did not request a commit).

---

## 5. Architecture Changes

### Decision
Runtime dependency list is empty in Phase 00.

#### Context
FastAPI, Pydantic, SQLAlchemy etc. are the documented stack (`/backend/AGENTS.md` §3), but Phase 00 scope is the environment only; the HTTP skeleton is Phase 01.

#### Chosen Approach
Zero runtime dependencies; only dev group (`pytest`, `ruff`, `mypy`).

#### Alternatives Considered
Pre-installing FastAPI now — rejected: AGENTS scope discipline (§3.1) and YAGNI.

#### Reason
Adding dependencies before their consuming code exists produces an untestable, unmotivated lock state.

#### Consequences
Phase 01 must add FastAPI/Pydantic and re-lock. No other impact.

### Decision
Package name `akasha`, distribution name `akasha-engine`.

#### Context
Roadmap requires establishing a package naming convention; repository is named `akasha-agent-engine`.

#### Chosen Approach
Short import package `akasha` under `src/`; PyPI-style dist name `akasha-engine`.

#### Alternatives Considered
`agent_engine`, `akasha_engine` — longer without added clarity.

#### Reason
Project branding is "Akasha"; short unique import name reads cleanly (`from akasha.main import main`).

#### Consequences
All future backend code lives under `akasha.*`. Renaming later would touch everything; treat as fixed.

No other architectural changes were introduced during this phase.

---

## 6. Domain / Data Model Changes

Not applicable — no domain model, schemas, DTOs, or persistence exist in Phase 00 (per scope).

---

## 7. API / Contract Changes

No API or contract changes. No HTTP layer exists (`/health`, `/ready` are Phase 01). The only public surface is the `akasha` console script and `python -m akasha`, both producing `akasha-engine 0.1.0` on stdout and exit code 0.

---

## 8. Implementation Details

- **Entry point**: `akasha.main.main() -> int` writes the dist name and `__version__` to stdout and returns 0. It exists only to satisfy the "backend process starts" acceptance criterion and to prove the installed console script wiring works. It contains no configuration loading, logging, or server bootstrap — that is Phase 01.
- **Version source of truth**: `akasha.__version__` (package) must be kept in sync with `[project].version` in `pyproject.toml`. ponytail: no single-source mechanism (e.g. `importlib.metadata`) wired up yet; add when the version is consumed by more than the startup banner.
- **Build config**: hatchling wheel packages = `src/akasha`; editable install via `uv sync` makes `import akasha` work in tests without `PYTHONPATH` hacks.

---

## 9. Findings

### Finding
`backend/.gitignore` (existing GitHub Python template) already covers all relevant artifacts (`.env`, `.venv`, caches).

### Impact
LOW

### Resolution
FIXED (no action needed — verified instead of duplicating rules)

### Notes
Phase prompt suggested ".gitignore additions where appropriate"; none were appropriate.

### Finding
Ruff `S` ruleset flags `assert` (`S101`) in tests.

### Impact
LOW

### Resolution
FIXED — scoped `per-file-ignores` for `tests/**` only, with an inline comment justifying it (AGENTS §55: no suppressions without documented reason).

### Notes
Alternative (disabling `S` globally) would have weakened security linting of future application code.

---

## 10. Bugs / Problems Found

No pre-existing or introduced bugs. One tooling conflict (S101 vs pytest idiom) found and fixed during the first lint run; see §9 and §11.

---

## 11. Fixes Applied

1. **Symptom**: `ruff check` failed with 2 × `S101 Use of assert detected` in `tests/test_foundation.py`.
   **Root cause**: `S` (flake8-bandit) rules apply to tests where `assert` is the correct idiom.
   **Fix**: Added `[tool.ruff.lint.per-file-ignores]` restricting `S101` to `tests/**`.
   **Verification**: `ruff check` passes; application code remains fully covered by `S`.

2. **Symptom**: Initial draft of `test_application_starts` used an untyped `capsys: object` fixture parameter.
   **Root cause**: mypy strict + pytest fixtures without type stubs; the capture wasn't needed.
   **Fix**: Dropped the unused fixture instead of weakening typing.
   **Verification**: mypy strict passes.

---

## 12. Testing & Verification

### Unit Tests (baseline)
- Package import + `__version__` defined — PASS
- `main()` returns 0 — PASS
- Result: `2 passed in 0.01s` (pytest 9.1.1, Python 3.14.3)

### Build / Install
- `uv sync` from clean state: venv created, `akasha-engine==0.1.0` built and editable-installed, 12 packages — PASS
- `uv lock --check` — PASS

### Process Startup
- `uv run akasha` → prints `akasha-engine 0.1.0`, exit 0 — PASS
- `uv run python -m akasha` → same — PASS

No integration/API/E2E/security/performance tests exist — no such functionality exists in Phase 00.

---

## 13. Quality Gates

| Quality Gate | Result | Notes |
|---|---|---|
| Formatter (`ruff format --check .`) | PASS | 6 files already formatted |
| Linter (`ruff check .`) | PASS | After S101 per-file-ignore fix |
| Type Checker (`mypy .`, strict) | PASS | 4 source files, no issues |
| Unit Tests (`pytest`) | PASS | 2 passed |
| Integration Tests | N/A | No infrastructure exists yet |
| API Tests | N/A | No API exists yet (Phase 01) |
| E2E Tests | N/A | — |
| Build (`uv sync` + wheel build) | PASS | hatchling build of `akasha-engine` succeeded |
| Security Checks | N/A | No attack surface exists; no secrets in repo (verified: no files with secrets created) |
| Lock check (`uv lock --check`) | PASS | Lock consistent with pyproject |

---

## 14. Acceptance Criteria Verification

From `/docs/roadmap-backend.md` §4:

| Acceptance Criterion | Status | Evidence |
|---|---|---|
| `uv sync` works | PASS | Clean venv created, 12 packages installed, project built |
| test suite runs | PASS | `pytest`: 2 passed |
| linting works | PASS | `ruff check .` — all checks passed |
| formatting works | PASS | `ruff format --check .` — 6 files already formatted |
| type checking works | PASS | `mypy .` strict — no issues |
| backend process starts | PASS | `uv run akasha` and `python -m akasha` exit 0 with banner |
| no business logic exists yet | PASS | Package contains only `__version__` + `main()` banner; no api/domain/runtime modules |

Additionally from the phase prompt: package naming convention established (`akasha`); configuration files in `pyproject.toml`; `migrations/` placeholder present; `.gitignore` verified adequate; `uv.lock` committed-present for reproducibility.

---

## 15. Security Considerations

- No secrets introduced; no `.env` files created; `.env` is already covered by `backend/.gitignore`.
- Ruff security ruleset (`S`) enabled at the strictest scope for all non-test code.
- No network, file-system, expression-evaluation, or credential functionality exists — no attack surface to harden yet.

---

## 16. Performance Considerations

None applicable. Test suite completes in ~0.01 s; `uv sync` from scratch ~2 s with cache. No claims beyond these measurements.

---

## 17. Documentation Changes

| File | Reason | Content |
|---|---|---|
| `backend/README.md` | Created (required by roadmap structure and onboarding criterion) | Requirements, setup, run, quality gates, layout |
| `backend/migrations/.gitkeep` | Created | Notes Alembic arrives in Phase 05 |
| This report | Created | Phase documentation |

No changes to `/docs/*` or `*/AGENTS.md` were required — the structure matches the documented plan (`README.md` §26, `architecture.md` §5).

---

## 18. Dependencies / Infrastructure Changes

New dev dependencies (dev group only, `~=` compat-pinned, exact versions in `uv.lock`):

| Dependency | Constraint | Locked | Purpose |
|---|---|---|---|
| pytest | `~=9.0` | 9.1.1 | Test runner |
| ruff | `~=0.14` | 0.16.9 | Linter + formatter |
| mypy | `~=1.18` | 1.20.2 | Type checker |

Build backend: hatchling (locked, build-time only). Runtime dependencies: none.

Environment: `uv 0.10.10`, interpreter resolved to CPython 3.14.3 (satisfies `>=3.12`).

No Docker, database, Redis, or external-service changes.

---

## 19. Deferred Work

| Item | Why deferred | Target phase | Blocks Phase 00? |
|---|---|---|---|
| Layer packages (`api/`, `application/`, `domain/`, ...) | Phase 01 scope | 01 | No |
| `GET /health`, `GET /ready` | Phase 01 scope | 01 | No |
| Configuration/logging bootstrap | Phase 01 scope | 01 | No |
| FastAPI/Pydantic runtime deps | No consuming code yet | 01 | No |
| Alembic init + migrations | Phase 05 scope (only `migrations/` dir asked for) | 05 | No |
| Testcontainers | Phase 05+ (first real infrastructure) | 05 | No |
| Single-sourcing `__version__` via `importlib.metadata` | No consumer beyond banner | When needed | No |

---

## 20. Known Limitations

- The "backend process" prints a version banner and exits; it is not yet a server. This is exactly the Phase 00 criterion ("process starts") and stops being a limitation in Phase 01.
- `akasha.__version__` and `pyproject.toml` version can drift (no single-source wiring).
- No CI pipeline configuration exists in the repository; quality gates were run locally only. (No phase of the backend roadmap assigns CI setup explicitly; flag for Phase 26 Operations if still absent.)
- `uv.lock` is platform-resolved on macOS/darwin; uv locks are cross-platform by default, but this has only been verified on darwin.

---

## 21. Open Issues

1. **Issue**: No CI configuration (`.github/workflows` or equivalent) exists to enforce quality gates automatically.
   **Impact**: Gates depend on developer discipline.
   **Severity**: MEDIUM
   **Affected area**: Repository operations.
   **Recommended next action**: Introduce a minimal backend CI job (format/lint/mypy/pytest) at the next phase boundary; aim no later than Phase 26.
   **Target phase**: Not currently assigned (AGENTS §93 requires CI; roadmap has no explicit CI phase).

No other known open issues resulting from this phase.

---

## 22. Architectural / Product Decisions

1. **Import package is `akasha`** (dist `akasha-engine`, src layout). Fixed convention for all future backend code.
2. **Zero runtime dependencies until a phase consumes them.** Dependencies are added by the phase that needs them, keeping each phase's diff and lock changes reviewable.
3. **Strict mypy from the first file** (`strict = true`), so no legacy untyped code ever accumulates.
4. **`S101` suppression is scoped to tests only** — application code keeps full bandit-style checks.
5. **`migrations/` tracked as an empty placeholder** instead of initializing Alembic now — Alembic configuration without a database configuration service (Phase 05) would be speculative structure.

---

## 23. Repository / Git State

- Branch: `main`, up to date with `origin/main`; last commit `0cf5c03 first commit`.
- All Phase 00 work is **untracked** (not staged/committed — no commit was requested):
  - `backend/README.md`, `backend/migrations/`, `backend/pyproject.toml`, `backend/src/`, `backend/tests/`, `backend/uv.lock`
- `git diff` empty (no tracked files modified).
- No unrelated changes detected; no files deleted; pre-existing `backend/.gitignore` and `backend/AGENTS.md` untouched.
- Byte-compiled `__pycache__` artifacts exist locally under `src/` and `tests/`; covered by `.gitignore`.

---

## 24. Phase Completion Assessment

### Implemented
- uv project, lock, src-layout package `akasha`, entry point (`akasha`, `python -m akasha`), pytest/ruff/mypy-strict configuration, baseline tests, migrations placeholder, backend README.

### Verified
- All four quality gates + lock check + wheel build + process startup, executed and re-run against the final tree: all PASS.

### Not Implemented
- Everything beyond Phase 00 by design (layers, API, health endpoints, config, persistence).

### Deferred
- See §19 — all deferred items have an owning roadmap phase except CI (§21).

### Open
- CI pipeline absence (§21) — non-blocking for Phase 00.

### Blocking Issues
- None.

**Phase Status:** COMPLETE

---

## 25. Handoff to Next Phase

**Next phase: Phase 01 — Backend Application Skeleton.**

Phase 01 can assume:
- `uv sync` yields a working editable install; add dependencies via `uv add fastapi pydantic` (etc.) and let uv re-lock.
- Import root is `akasha` (src layout); create layer packages under `src/akasha/` (e.g. `akasha/api/`, `akasha/application/`, `akasha/domain/`, ... per `architecture.md` §5 / `backend/AGENTS.md` §5).
- Entry point to extend: `src/akasha/main.py::main()` — replace the banner with the real bootstrap; keep `__main__.py` delegating to it.
- Quality gate commands are documented in `backend/README.md`; any new code must keep `mypy strict` green and must not add `noqa`/`type: ignore` without documented reason.
- pytest discovers `tests/**`; the S101 test exemption is already in place.

Constraints / MUST NOT change casually:
- Do not rename the `akasha` package or change the src layout without an ADR.
- Do not weaken mypy `strict` or broaden the ruff per-file-ignores.
- Keep runtime dependencies intentional and locked; `uv lock --check` must pass after any `pyproject.toml` dependency edit.
- Health/readiness endpoints (`GET /health`, `GET /ready`) belong to Phase 01 — do not implement them within other work, and do not skip them in Phase 01.

Prerequisites for Phase 01: none beyond `uv sync` (no external services; PostgreSQL/Redis arrive in Phase 05).
