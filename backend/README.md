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
uv run akasha          # serves http://127.0.0.1:8000
AKASHA_PORT=9000 uv run akasha
```

Configuration is loaded from `AKASHA_*` environment variables
(`src/akasha/config.py`) and validated at startup — invalid config fails fast.

## Quality gates

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy .
uv run pytest
```

`tests/integration/` needs a running Docker daemon (Testcontainers starts a
throwaway `postgres:17.2`; unix socket, Windows named pipe and `DOCKER_HOST`
all work). Without Docker those tests are skipped, not faked.

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
└── observability/      structured JSON logging, metrics (Phase 12)

tests/                  pytest suite (incl. dependency-direction guard)
migrations/             Alembic migrations (introduced in Phase 05)
```

Layer boundaries are documented in each package `__init__.py` and enforced by
`tests/test_architecture.py`.

See `/backend/AGENTS.md` and `/docs/roadmap-backend.md` for the phase plan.
