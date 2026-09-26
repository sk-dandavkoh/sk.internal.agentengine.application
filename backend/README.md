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

## Layout

```text
src/akasha/
├── config.py           central Settings (AKASHA_* env), validated at startup
├── bootstrap.py        composition root (Container, create_container)
├── errors.py           AkashaError base with stable codes / HTTP status
├── api/                FastAPI app factory, routers (/health, /ready, /api/v1), error handlers
├── application/        use-case services (Phase 06+)
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
