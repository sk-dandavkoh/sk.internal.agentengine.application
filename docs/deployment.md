# Deployment and Operations

How to install, run, upgrade, back up, restore and recover a self-hosted
Akasha backend with Docker Compose. Everything here is exercised end to end
by `backend/tests/deployment/test_compose_lifecycle.py` (see
[Verifying a deployment](#verifying-a-deployment)).

## 1. Topology

`docker-compose.yml` (repository root) runs:

| Service | Image | Role | Persistent data |
|---|---|---|---|
| `postgres` | `postgres:17.2` | system of record | volume `pgdata` |
| `redis` | `redis:8.2.1-alpine` | execution queue (AOF on) | volume `redisdata` |
| `migrate` | `akasha-backend:0.1.0` | `akasha migrate`, then exits 0 | — |
| `api` | `akasha-backend:0.1.0` | HTTP API + trigger scheduler | volume `plugins` |
| `worker` | `akasha-backend:0.1.0` | runs executions (`akasha worker`) | volume `plugins` |

Start order is enforced: `postgres` healthy → `migrate` completed
successfully → `api` and `worker` (with `redis` healthy). The API never
serves an unmigrated schema: `/ready` stays `503` until the schema is at the
engine's head revision.

The backend image (`backend/Dockerfile`) uses pinned base images
(`python:3.14.0-slim-trixie`, `uv 0.10.10`), installs exactly `uv.lock`,
and runs as the unprivileged user `akasha` (uid 10001). Only the API port is
published, on `127.0.0.1` by default; PostgreSQL and Redis are reachable
only on the internal Compose network.

Run exactly one `api` replica: each API process runs the in-process
trigger scheduler. Scale executions with more `worker` replicas
(`docker compose up -d --scale worker=3`) or `AKASHA_WORKER_CONCURRENCY`.

## 2. Requirements

- Docker Engine with Compose v2.24 or newer (`docker compose version`)
- the repository checkout (the image is built locally from `backend/`)
- for remote access: a TLS-terminating reverse proxy in front of the API
  that also forwards WebSocket upgrades (see below)

The live execution view uses a WebSocket at
`/api/v1/executions/{id}/live` (`docs/architecture.md` §23). The proxy must
pass the `Upgrade` and `Connection` headers through for `/api/`. An idle
stream carries uvicorn's WebSocket ping every 20 s, so any proxy idle
timeout above that (nginx's default is 60 s) keeps it open. Without upgrade
support live updates stay on "Reconnecting" in the UI; everything else
keeps working. nginx example:

```nginx
location /api/ {
    proxy_pass http://127.0.0.1:8000;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection $connection_upgrade;  # map, see nginx docs
}
```

## 3. Installation

```bash
cp .env.example .env
python3 -c "import secrets; print(secrets.token_hex(24))"                              # → POSTGRES_PASSWORD
python3 -c "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())" # → AKASHA_CREDENTIAL_ENCRYPTION_KEY
# edit .env and paste both values
docker compose up -d --build
curl -fsS http://127.0.0.1:8000/ready
```

Compose refuses to start while either required value is missing.

Store `AKASHA_CREDENTIAL_ENCRYPTION_KEY` outside the host as well (password
manager, secret store). It is **not** part of any backup; without it stored
credentials are unrecoverable.

Create the first admin immediately, before the API is reachable by others
(it works only while no account exists):

```bash
curl -fsS -X POST http://127.0.0.1:8000/api/v1/auth/setup \
  -H 'content-type: application/json' \
  -d '{"username": "admin", "password": "<a long password>"}'
```

## 4. Configuration

All configuration is environment variables, validated at startup; invalid
values stop the container with a `CONFIGURATION_ERROR` naming the setting.

- `.env` holds the required secrets and any optional `AKASHA_*` setting;
  Compose passes the whole file to the backend containers. Every setting is
  documented in `backend/README.md` and `backend/src/akasha/config.py`.
- Set by Compose, do not override: `AKASHA_DATABASE_URL`,
  `AKASHA_REDIS_URL`, `AKASHA_WORKER_ENABLED=false` (executions run in the
  `worker` service).
- Set by the image: `AKASHA_ENVIRONMENT=production`, `AKASHA_HOST=0.0.0.0`,
  `AKASHA_PORT=8000`, `AKASHA_PLUGINS_DIR=/var/lib/akasha/plugins`.
- `AKASHA_HTTP_PORT` / `AKASHA_BIND_ADDRESS` choose the published host port
  and address (default `127.0.0.1:8000`).

Apply a changed `.env` with `docker compose up -d` (recreates the affected
containers).

## 5. Health, readiness, logs, metrics

| Endpoint | Meaning | Use for |
|---|---|---|
| `GET /health` | the process is alive (`{"status": "ok", "version"}`) | container healthcheck, liveness |
| `GET /ready` | `200` when the database is reachable, the schema is at head and Redis answers; otherwise `503` with `failures` per check | load balancer / readiness |
| `GET /metrics` | Prometheus metrics; bearer `AKASHA_METRICS_TOKEN` or an admin session | monitoring |

`docker compose ps` shows the `api` healthcheck (`/health`). The `worker`
has no HTTP endpoint and no container healthcheck; a dead worker's
executions are recovered by the next worker (§9).

Logs are structured JSON lines on stdout (`docker compose logs -f api
worker`), with `request_id`, `execution_id` and secrets redacted. Use
Docker's logging driver for shipping and rotation.

## 6. Start, stop, restart

```bash
docker compose up -d          # start (or apply changes); runs migrate first
docker compose stop           # graceful stop
docker compose start          # start the stopped containers again
docker compose down           # remove containers; volumes (data) are kept
```

Graceful stop: Docker sends `SIGTERM` to `akasha` (PID 1). The API stops
accepting requests, closes open live WebSockets with 1012 (clients
reconnect to the restarted API and catch up), stops the scheduler and
closes its pools; the worker
stops taking jobs and gives running executions
`AKASHA_WORKER_SHUTDOWN_GRACE_SECONDS` (30 s) to finish, then cancels them
cooperatively. `stop_grace_period` is 60 s, so `SIGKILL` never interrupts
that grace period. Both exit with code 0.

`docker compose down -v` **deletes all data volumes.** Take a backup first.

## 7. Upgrade

```bash
docker compose exec -T api akasha backup -o - > "akasha-$(date +%F).backup"
git pull
docker compose up -d --build
```

`migrate` upgrades the schema before the new API and worker start; if it
fails, they do not start and the old data is untouched. There is no
in-place downgrade: to go back, restore the pre-upgrade backup with the
previous engine version.

## 8. Backup and restore

### What a backup contains

`akasha backup` writes one gzip JSON document: a manifest (format, engine
version, schema revision, enabled plugins) and every row of every table
from one consistent snapshot — projects, versions, executions, events,
users and **encrypted** credentials. Login sessions are left out.

Not included, keep them yourself:

- `.env`, above all `AKASHA_CREDENTIAL_ENCRYPTION_KEY`
- plugin packages (volume `plugins`); install them again before restoring
- Redis: the queue is rebuilt from PostgreSQL

A backup holds password hashes and encrypted credentials: store it like a
database dump (restricted permissions, encrypted storage).

### Taking a backup

The backup streams out of the running API container (no downtime):

```bash
umask 077
docker compose exec -T api akasha backup -o - > "akasha-$(date +%F-%H%M).backup"
```

`-T` is required: it keeps the binary stream intact. Schedule it with cron
or a systemd timer, and copy the files off the host.

### Restoring

Restore into a fresh or destroyed installation, with the **same**
`AKASHA_CREDENTIAL_ENCRYPTION_KEY` and the same engine version that wrote
the backup:

```bash
docker compose up -d                 # fresh installation (empty volumes)
docker compose stop api worker       # nothing writes while restoring
docker compose run --rm -T --no-deps api restore - < akasha-2026-10-01-0300.backup
docker compose up -d
curl -fsS http://127.0.0.1:8000/ready
```

Restore validates everything before writing: the format, the enabled
plugins, that the configured key decrypts every stored credential, and
that the backup's schema revision equals the engine's. Then it loads all
rows in one transaction. Any failure leaves the installation unchanged.

A non-empty installation is refused with `RESTORE_TARGET_NOT_EMPTY`. To
overwrite one deliberately, add `--replace` (truncate and reload, in the
same transaction).

After a restore, users log in again (sessions are not restored). Verify:
log in, list projects and credentials, and run a project.

## 9. Recovery

| Failure | What happens | Operator action |
|---|---|---|
| worker crashes or is killed | its executions stop heart-beating; after `AKASHA_EXECUTION_STALE_SECONDS` (60 s) the next worker's recovery pass resumes them (finished nodes are not re-run) | `docker compose up -d` (restart policy does it automatically) |
| API crashes | Docker restarts it (`restart: unless-stopped`); state lives in PostgreSQL | none |
| PostgreSQL restarts | `/ready` reports `database` until it is back; pooled connections are revived | none |
| Redis loses its data | executions still `QUEUED` in PostgreSQL are re-dispatched after `AKASHA_QUEUE_RECOVERY_SECONDS` (30 s) | none |
| host lost | — | new host: install (§3) with the saved `.env`, then restore (§8) |
| encryption key lost | stored credentials cannot be decrypted; restore refuses | restore with the right key, or recreate the credentials |
| `/ready` says `schema` | the database is not at the engine's revision | `docker compose run --rm migrate` and check its logs |

Side-effecting nodes (HTTP POST, SMTP) may run again when a node was
interrupted mid-attempt: execution is at-least-once.

## 10. Plugins

Plugins are trusted code. Install one into the shared `plugins` volume,
then restart the backend so both API and worker load it:

```bash
docker compose run --rm -v "$PWD/my-plugin:/tmp/plugin:ro" api plugin install /tmp/plugin
docker compose restart api worker
```

## 11. Verifying a deployment

The deployment test runs the whole operator lifecycle against the real
image and `docker-compose.yml`, in an isolated Compose project that is
deleted afterwards: clean install → migrate → first admin, credential,
executions → graceful stop (exit 0) → restart → container recreation →
backup → `down -v` → fresh install → restore → verification (login,
history, credentials, a new run) → worker killed mid-execution and resumed
→ PostgreSQL restarted under a running API.

```bash
cd backend
AKASHA_TESTS_DEPLOYMENT=1 uv run pytest tests/deployment
```

It needs Docker and takes about two minutes; ordinary test runs skip it.
