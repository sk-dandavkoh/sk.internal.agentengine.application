# Deployment and Operations

How to install, run, upgrade, back up, restore and recover a self-hosted
Akasha installation with Docker Compose. Everything here is exercised end
to end by `backend/tests/deployment/test_compose_lifecycle.py` (see
[Verifying a deployment](#11-verifying-a-deployment)).

## 1. Topology

`docker-compose.yml` (repository root) runs:

| Service | Image | Role | Persistent data |
|---|---|---|---|
| `secrets` | `postgres:17.2` | generates the secrets on first start, then exits 0 | volume `secrets` |
| `postgres` | `postgres:17.2` | system of record | volume `pgdata` |
| `redis` | `redis:8.2.1-alpine` | execution queue (AOF on) | volume `redisdata` |
| `migrate` | `akasha-backend:0.1.0` | `akasha migrate`, then exits 0 | — |
| `api` | `akasha-backend:0.1.0` | HTTP API + trigger scheduler | volume `plugins` |
| `worker` | `akasha-backend:0.1.0` | runs executions (`akasha worker`) | volume `plugins` |
| `ui` | `akasha-ui:0.1.0` | the UI; proxies the API | — |

Start order is enforced: `secrets` completed → `postgres` healthy →
`migrate` completed successfully → `api` and `worker` (with `redis`
healthy) → `ui` once `api` is healthy. The API never serves an unmigrated
schema: `/ready` stays `503` until the schema is at the engine's head
revision.

**One port.** Only `ui` is published, on `127.0.0.1:8080` by default. It
serves the UI and proxies `/api/` (the live WebSocket included), `/health`,
`/ready` and `/metrics` to `api`. Browser and API therefore share one origin
and the backend needs no CORS. PostgreSQL, Redis and the API itself are
reachable only on the internal Compose network.

The images are built locally from the checkout, from pinned bases:

- `backend/Dockerfile`: `python:3.14.0-slim-trixie` and `uv 0.10.10`. It
  installs exactly `uv.lock` and runs as the unprivileged user `akasha`
  (uid 10001).
- `frontend/Dockerfile`: `node:24.14.0-alpine` builds the bundle from
  `package-lock.json`. `nginxinc/nginx-unprivileged:1.29.1-alpine` serves
  it with `frontend/nginx.conf`, as a non-root user.

Run exactly one `api` replica: each API process runs the in-process
trigger scheduler. Scale executions with more `worker` replicas
(`docker compose up -d --scale worker=3`) or `AKASHA_WORKER_CONCURRENCY`.

## 2. Requirements

- Docker Engine with Compose v2.24 or newer (`docker compose version`)
- the repository checkout (both images are built locally)

Nothing else: no `.env`, no generated values, no separate web server.

For access from other machines, put a TLS-terminating reverse proxy in
front of the `ui` port. It must:

- forward WebSocket upgrades. The live execution view uses a WebSocket at
  `/api/v1/executions/{id}/live` (`docs/architecture.md` §23). An idle
  stream carries uvicorn's WebSocket ping every 20 s, so any proxy idle
  timeout above that (nginx's default is 60 s) keeps it open;
- forward the host the browser used, port included, as `Host` (nginx:
  `$http_host`, not `$host`). The live stream accepts the session cookie
  only when the handshake's `Origin` matches that host (ADR 0002).

Without upgrade support, live updates stay on "Reconnecting" in the UI;
everything else keeps working. With the wrong host, live updates fail with
"unavailable". nginx example:

```nginx
location / {
    proxy_pass http://127.0.0.1:8080;
    proxy_http_version 1.1;
    proxy_set_header Host $http_host;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection $connection_upgrade;  # map, see nginx docs
}
```

Serve the UI over HTTPS: the session cookie is `Secure`. Browsers accept
it on `http://localhost` but drop it on any other plain-HTTP address. A
plain-HTTP rig on another address can set
`AKASHA_SESSION_COOKIE_SECURE=false`.

### The UI

The `ui` image builds the bundle reproducibly from the lockfile (two builds
of one commit are byte-identical). The bundle has no runtime configuration
and must be served at the root of its host (asset URLs are absolute,
`/assets/...`). No source maps are built.

Supported browsers: Chrome/Edge 116+, Firefox 128+, Safari 17.4+.

`frontend/nginx.conf` is the reference for serving it:

- The bundle carries its Content Security Policy as a `<meta>` tag. A
  `<meta>` cannot forbid framing, so the server adds headers that stop
  clickjacking and MIME sniffing.
- `index.html` is not cached, so a new release takes effect on reload.
- Files in `/assets/` have content-hashed names and are cached forever.
- A missing asset is a 404, never `index.html`.

To serve the bundle from your own web server instead, build it with
`cd frontend && npm ci && npm run build` and use the same rules. Copy
releases without deleting the old assets, so tabs opened before an upgrade
can still load pages; if those assets are gone, the page shows "Reload".

When the backend is unreachable or the proxy answers 502/503/504, the UI
retries reads twice, then says so and offers a retry. It never retries
writes.

## 3. Installation

```bash
docker compose up -d --build
```

Then open <http://localhost:8080>. A fresh installation shows **Set up
Akasha**: choose the first administrator's username and password, and you
are signed in. The form exists only while no account exists. Do it right
away, before others can reach the port. Admins create further accounts
(API `/api/v1/users`).

On first start the `secrets` service generates the PostgreSQL password and
the credential encryption key into the `secrets` volume. Later starts keep
them. **Back up the encryption key** and keep it off the host (password
manager, secret store). It is not part of any backup; without it, stored
credentials are unrecoverable:

```bash
docker compose run --rm --no-deps secrets 'cat /run/akasha-secrets/AKASHA_CREDENTIAL_ENCRYPTION_KEY'
```

**Own values instead.** Set `POSTGRES_PASSWORD` (hex or otherwise
URL-safe: it goes into a URL) and/or `AKASHA_CREDENTIAL_ENCRYPTION_KEY`
(32 bytes, URL-safe base64) in `.env` (`.env.example`). Set values always
win and are written into the volume. An installation created before this
mechanism keeps working with its existing `.env`. Do not change either
value on an existing installation: a new database password locks the
backend out of the existing database, and a new key makes stored
credentials undecryptable.

## 4. Configuration

All configuration is environment variables, validated at startup. Invalid
values stop the container with a `CONFIGURATION_ERROR` naming the setting.

- `.env` (optional) holds any `AKASHA_*` setting. Compose passes the whole
  file to the backend containers. Every setting is documented in
  `backend/README.md` and `backend/src/akasha/config.py`.
- `AKASHA_HTTP_PORT` / `AKASHA_BIND_ADDRESS` choose the published host port
  and address (default `127.0.0.1:8080`).
- Set by Compose, do not override: `AKASHA_REDIS_URL` and
  `AKASHA_WORKER_ENABLED=false` (executions run in the `worker` service).
- Read from the `secrets` volume (`/run/akasha-secrets`, one file per
  setting): `AKASHA_DATABASE_URL` and `AKASHA_CREDENTIAL_ENCRYPTION_KEY`.
  Set them in `.env` only as described in §3.
- Set by the image: `AKASHA_ENVIRONMENT=production`, `AKASHA_HOST=0.0.0.0`,
  `AKASHA_PORT=8000`, `AKASHA_PLUGINS_DIR=/var/lib/akasha/plugins`.

Apply a changed `.env` with `docker compose up -d` (recreates the affected
containers).

## 5. Health, readiness, logs, metrics

| Endpoint | Meaning | Use for |
|---|---|---|
| `GET /health` | the process is alive (`{"status": "ok", "version"}`) | container healthcheck, liveness |
| `GET /ready` | `200` when the database is reachable, the schema is at head and Redis answers; otherwise `503` with `failures` per check | load balancer / readiness |
| `GET /metrics` | Prometheus metrics; bearer `AKASHA_METRICS_TOKEN` or an admin session | monitoring |

All three are reached through the published port
(`curl -fsS http://127.0.0.1:8080/ready`).

`docker compose ps` shows the `api` healthcheck (`/health`) and the `ui`
healthcheck (it serves the UI). The `worker`
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

`docker compose down -v` **deletes all data volumes**, the generated
encryption key included. Take a backup, and keep the key (§3), first.

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

- the credential encryption key (§3), and `.env` if you have one
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
encryption key and the same engine version that wrote the backup. A fresh
installation would generate a new key, so put the saved one in `.env`
first, as `AKASHA_CREDENTIAL_ENCRYPTION_KEY=<saved key>` (§3):

```bash
docker compose up -d                 # fresh installation (empty volumes)
docker compose stop api worker       # nothing writes while restoring
docker compose run --rm -T --no-deps api restore - < akasha-2026-10-01-0300.backup
docker compose up -d
curl -fsS http://127.0.0.1:8080/ready
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
| host lost | — | new host: the saved key in `.env` (§3), install, then restore (§8) |
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

A plugin may declare Python libraries (`python_requirements`, ADR 0004).
The backend installs them with uv into the `plugins` volume, at install
time and again on startup when they are missing or were installed for an
older engine. So installation, and the first start after an upgrade, need
the package index. PyPI is the default. To use a private index or an
offline wheelhouse, set uv's variables in `.env`, for example
`UV_DEFAULT_INDEX=https://pypi.example/simple`, or `UV_FIND_LINKS=/wheels`
with `UV_OFFLINE=1` and the wheels mounted. A failed install reports
`PLUGIN_DEPENDENCIES_FAILED` with uv's explanation. A plugin that cannot
get its libraries at startup is listed as `failed`. Developing plugins
against a local stack: `docs/sdk.md` §11.

Admins can also enable, disable and remove plugins in the UI (`/plugins`).
Uploading a package there is off by default. Setting
`AKASHA_PLUGINS_HTTP_INSTALL=true` turns it on, and then every admin account
can run code on the host (ADR 0001). Changes made in the UI reach dedicated
workers only after `docker compose restart worker`.

## 11. Verifying a deployment

The deployment tests run against the real images and `docker-compose.yml`,
each in an isolated Compose project that is deleted afterwards, and reach
the stack only through its one published port:

- the operator lifecycle: clean install → migrate → first admin,
  credential, executions → graceful stop (exit 0) → restart → container
  recreation → backup → `down -v` → fresh install → restore → verification
  (login, history, credentials, a new run) → worker killed mid-execution
  and resumed → PostgreSQL restarted under a running API;
- the zero-config install: no secrets given, the UI is served (headers,
  404 for missing assets), setup is offered and then closed, and the
  generated secrets survive a recreation.

```bash
cd backend
AKASHA_TESTS_DEPLOYMENT=1 uv run pytest tests/deployment
```

It needs Docker and takes a few minutes; ordinary test runs skip it.
