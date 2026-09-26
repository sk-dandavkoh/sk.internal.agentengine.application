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
uv run akasha          # or: uv run python -m akasha
```

## Quality gates

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy .
uv run pytest
```

## Layout

```text
src/akasha/     application package (layers added per /docs/architecture.md)
tests/          pytest suite
migrations/     Alembic migrations (introduced in Phase 05)
```

See `/backend/AGENTS.md` and `/docs/roadmap-backend.md` for the phase plan.
