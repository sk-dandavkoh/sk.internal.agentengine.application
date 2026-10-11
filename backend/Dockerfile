# Akasha Engine backend image. One image, three roles (Compose services):
#   akasha migrate   one-shot schema upgrade, before anything else starts
#   akasha serve     HTTP API + trigger scheduler (default command)
#   akasha worker    dedicated execution worker
# See docs/deployment.md.

FROM ghcr.io/astral-sh/uv:0.10.10 AS uv

FROM python:3.14.0-slim-trixie AS build
COPY --from=uv /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# Dependencies first: the layer survives source-only changes.
COPY pyproject.toml uv.lock ./
# With the browser extra (Playwright): core.browser and core.browser_agent.
RUN uv sync --frozen --no-dev --extra browser --no-install-project
COPY README.md alembic.ini ./
COPY migrations ./migrations
COPY src ./src
# Editable on purpose: migrations are located relative to the source tree
# (akasha.infrastructure.database), which stays at /app/src.
RUN uv sync --frozen --no-dev --extra browser

FROM python:3.14.0-slim-trixie
# Installs the Python libraries plugins declare (ADR 0004).
COPY --from=uv /uv /bin/uv
RUN useradd --system --uid 10001 --user-group --home-dir /app --no-create-home akasha \
    && mkdir -p /var/lib/akasha/plugins /var/lib/akasha/workspace \
    && chown -R akasha:akasha /var/lib/akasha
COPY --from=build --chown=root:root /app /app
# Chromium for the browser nodes, with the system libraries it needs, as
# root and outside the app user's home: readable by it, never writable.
# Headless shell only (the nodes never open a window). Its version is the
# one the locked Playwright release pins.
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright
RUN /app/.venv/bin/playwright install --with-deps --only-shell chromium \
    && rm -rf /var/lib/apt/lists/*
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    AKASHA_ENVIRONMENT=production \
    AKASHA_HOST=0.0.0.0 \
    AKASHA_PORT=8000 \
    AKASHA_PLUGINS_DIR=/var/lib/akasha/plugins \
    AKASHA_WORKSPACE_DIR=/var/lib/akasha/workspace \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app
USER akasha
EXPOSE 8000
# Liveness of the API role; the worker and migrate roles override it.
HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"AKASHA_PORT\"]}/health', timeout=4)"
# Exec form: akasha is PID 1 and receives SIGTERM for graceful shutdown.
ENTRYPOINT ["akasha"]
CMD ["serve"]
