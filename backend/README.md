# Akasha Engine — Backend

Self-hosted AI Agent Orchestration Engine backend.

## Requirements

- Python >= 3.12
- [uv](https://docs.astral.sh/uv/)

## Setup

```bash
uv sync
```

## Run

```bash
uv run akasha serve    # serves http://127.0.0.1:8000
AKASHA_PORT=9000 uv run akasha serve
```

Configuration is loaded from `AKASHA_*` environment variables
(`src/akasha/config.py`) and validated at startup — invalid config fails fast.

Built-in node settings:

| Variable | Default | Meaning |
|---|---|---|
| `AKASHA_NETWORK_ALLOW_PRIVATE` | `false` | let core.http/core.smtp reach private/loopback addresses |
| `AKASHA_NETWORK_ALLOWED_HOSTS` | `[]` | JSON list of hosts allowed despite resolving privately |
| `AKASHA_HTTP_MAX_RESPONSE_BYTES` | `5242880` | response cap for core.http/core.web_search |
| `AKASHA_WEB_SEARCH_URL` | unset | SearXNG base URL for core.web_search |

Authentication settings:

| Variable | Default | Meaning |
|---|---|---|
| `AKASHA_SESSION_TTL_SECONDS` | `43200` | session lifetime after login (absolute, 60 s – 30 days) |
| `AKASHA_METRICS_TOKEN` | unset | bearer token Prometheus uses for `/metrics` (≥ 32 chars); unset → admin session only |

First run: `POST /api/v1/auth/setup` with `{"username", "password"}` creates
the initial admin. It works only while no account exists, so complete it
before exposing the engine to a network. Then `POST /api/v1/auth/login` and
send `Authorization: Bearer <token>` on every request.

## Quality gates

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy .
uv run pytest
```

`tests/integration/` and the SMTP node tests need a running Docker daemon
(Testcontainers starts throwaway `postgres:17.2` and `axllent/mailpit:v1.21.8`;
unix socket, Windows named pipe and `DOCKER_HOST` all work). Without Docker
those tests are skipped, not faked. Set `AKASHA_TESTS_REQUIRE_DOCKER=1` (CI)
to make an unreachable daemon fail the run instead of skipping them. HTTP and web-search tests use an
in-process loopback server; no test touches the public internet.

## CLI

`akasha` is also the operator CLI. It reads the same `AKASHA_*` variables as
the server and calls the same application services. It acts with operator
authority, with no login: whoever can run it already holds the database
credentials. `akasha COMMAND --help` documents every option. Add `--json`
for machine-readable output.

| Command | Does |
|---|---|
| `project create NAME` / `project list` | create an empty project / list projects |
| `project validate ID` | validate the draft (exit 1 if invalid) |
| `project publish ID [--expected-revision N]` | publish the draft as the next version |
| `project run ID [--version N] [--input JSON\|@file]` | run a published version in this process; Ctrl-C cancels |
| `project import FILE` / `project export ID [--version N] -o FILE` | project packages |
| `node list` / `node validate T` / `node test T [--config] [--inputs] [--execution-input]` | `T` = node type id or `file.py:Class` / `module:Class` |
| `plugin list` / `plugin install PATH` / `plugin remove ID [--force]` | needs `AKASHA_PLUGINS_DIR`; remove is blocked while projects use the plugin |
| `execution get ID` / `execution cancel ID [--reason]` | inspect / cancel |
| `backup -o FILE` | snapshot the installation (new file, mode 0600) |
| `restore FILE [--replace]` | restore into an empty installation, or replace one |

A backup holds password hashes and encrypted credentials; store it like a
database dump. It does **not** contain `AKASHA_CREDENTIAL_ENCRYPTION_KEY`
(restore refuses unless the configured key decrypts the credentials) or
plugin code (install the plugins before restoring). Restore migrates the
target database and requires the backup's schema revision to match.

Exit codes: `0` success, `1` failure (engine error, invalid project, run not
completed), `2` usage error, `70` unexpected internal error.

## Project API (`/api/v1/projects`)

| Method | Path | Use case |
|---|---|---|
| POST | `/api/v1/projects` | CreateProject → 201 |
| GET | `/api/v1/projects?offset=&limit=` | ListProjects (summaries, `limit` ≤ 100) |
| GET | `/api/v1/projects/{id}` | GetProject (incl. draft) |
| PUT | `/api/v1/projects/{id}` | UpdateProjectDraft — full replace, requires `expected_revision` |
| DELETE | `/api/v1/projects/{id}` | DeleteProject → 204; 409 once versions exist |
| POST | `/api/v1/projects/{id}/validate` | ValidateProject → `{valid, issues}` |
| POST | `/api/v1/projects/{id}/publish` | PublishProject → 201 immutable version; 422 if invalid |
| GET | `/api/v1/projects/{id}/versions` | ListProjectVersions (summaries) |
| GET | `/api/v1/projects/{id}/versions/{n}` | GetProjectVersion (read-only; no write methods exist) |

Every write is optimistic-concurrency controlled: a stale `expected_revision`
(or a concurrent writer) yields `409 CONCURRENT_MODIFICATION`. Errors are
`{code, message, details, request_id}`. Interactive schema: `/docs`.

## Layout

```text
src/akasha/
├── config.py           central Settings (AKASHA_* env), validated at startup
├── bootstrap.py        composition root (Container, create_container)
├── errors.py           AkashaError base with stable codes / HTTP status
├── api/                FastAPI app factory, routers (/health, /ready, /api/v1), error handlers
├── application/        use-case services (projects: Phase 06)
├── domain/             domain model (Phase 02+) — no FastAPI/SQLAlchemy/infra imports
├── infrastructure/     PostgreSQL/Redis/storage adapters (Phase 05+)
├── runtime/            execution runtime (Phase 08)
├── nodes/              built-in nodes (Phase 09)
├── providers/          LLM provider adapters (Phase 14)
├── plugins/            plugin system (Phase 17)
├── security/           credentials/redaction/guards (Phase 13+)
├── observability/      structured JSON logging, metrics (Phase 12)
└── cli.py              operator CLI (Phase 21)

tests/                  pytest suite (incl. dependency-direction guard)
migrations/             Alembic migrations (introduced in Phase 05)
```

Layer boundaries are documented in each package `__init__.py` and enforced by
`tests/test_architecture.py`.

See `/backend/AGENTS.md` and `/docs/roadmap-backend.md` for the phase plan.
