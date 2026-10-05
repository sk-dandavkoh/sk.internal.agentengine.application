# ADR 0003: One-command Compose stack

Status: accepted (2026-10-05)

## Context

Before this decision, a working installation needed manual steps outside
Compose:

- **Secrets.** Generate two secrets and paste them into `.env`. Compose
  refused to start without them.
- **UI.** Build the UI with Node, copy it to a web server, and configure
  that server to serve it next to a proxied `/api` (WebSocket upgrade,
  `Host`, security headers, caching).
- **First admin.** Create it with `curl` against `/auth/setup`.

The API was published on port 8000, a port other self-hosted tools often
occupy. The goal: `docker compose up -d --build`, open the browser, done.

## Decision

1. **UI service.** `frontend/Dockerfile` builds the bundle from the
   lockfile (`node:24.14.0-alpine`) and serves it with
   `nginxinc/nginx-unprivileged:1.29.1-alpine` (non-root).
   `frontend/nginx.conf` proxies `/api/` (WebSocket included), `/health`,
   `/ready` and `/metrics` to `api`. It forwards `$http_host`, port
   included, because the live stream's Origin check compares host:port.
   It re-resolves `api` through Docker's DNS, so a recreated API container
   is found. It leaves body size limits to the backend.
2. **One published port.** Only `ui` is published, on
   `${AKASHA_BIND_ADDRESS:-127.0.0.1}:${AKASHA_HTTP_PORT:-8080}`, the same
   variables as before. The API is internal. The same-origin rule of the UI
   (no CORS) holds by construction.
3. **Generated secrets.** A one-shot `secrets` service writes three files
   into the `secrets` volume (`/run/akasha-secrets`) on first start:
   - the PostgreSQL password (hex), used by PostgreSQL through
     `POSTGRES_PASSWORD_FILE`;
   - `AKASHA_DATABASE_URL`;
   - `AKASHA_CREDENTIAL_ENCRYPTION_KEY`.

   Later starts keep the files. Values set in `.env` win and are written
   to the volume, so installations made before this ADR keep their
   secrets. The backend reads the two `AKASHA_*` files through
   pydantic-settings' `secrets_dir`. Environment variables still take
   precedence, and the directory is used only where it exists. This way
   `docker compose exec` (backups) sees them too, which a shell-wrapper
   entrypoint would not.
4. **First-run setup in the UI.** The public `GET /api/v1/auth/setup`
   returns `{"required": bool}`, true while no account exists. While it is
   true, the login page shows "Set up Akasha". The form creates the first
   admin with the existing `POST /auth/setup` and signs in. The backend
   still decides: the POST is refused (409) once any account exists.

## Alternatives

- **Keep `.env` mandatory.** Rejected: manual setup is the problem this
  ADR solves.
- **Generate an admin with a random password and print it to the logs.**
  No product change, but the user must find the password in
  `docker compose logs`, and it ends up in log storage.
- **Publish both the UI and the API.** Two ports to choose and to keep
  free, for no browser benefit. A port can be added with a Compose
  override when direct API access is needed.
- **`POSTGRES_HOST_AUTH_METHOD=trust` instead of a generated password.**
  Rejected: every container on the network could then read and write the
  database.

## Consequences

- An installation is one command plus choosing an admin in the browser.
- **Breaking for existing installations:** the API moved from port 8000
  to the UI's port 8080, under the same paths. Scripts that called
  `http://host:8000/...` use `http://host:8080/...`. A reverse proxy in
  front now targets the `ui` port.
- The generated encryption key lives in a Docker volume, and
  `docker compose down -v` deletes it with the data. The operator must back
  it up (documented in `docs/deployment.md` §3). Restoring onto a fresh
  installation needs it in `.env`.
- `GET /auth/setup` is public and tells an anonymous caller whether the
  installation has any account. A fresh installation can therefore be
  claimed by whoever reaches it first, exactly as with the earlier `curl`
  step. The default loopback binding and the documented "set up right
  away" keep that window small.
- `backend/tests/deployment/test_compose_lifecycle.py` reaches the stack
  only through the published port, and covers the zero-config path.
