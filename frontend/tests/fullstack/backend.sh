#!/usr/bin/env bash
# A throwaway backend for the full-stack suite: the backend from this
# checkout (uv) on a fresh PostgreSQL container whose data lives in tmpfs,
# so every run starts empty, and a Redis queue for the in-process worker,
# so executions really run. The containers carry the akasha-e2e label;
# Playwright's globalTeardown (teardown.ts) removes it, and leftovers of a
# crashed run are removed here before starting.
set -euo pipefail

port=${AKASHA_E2E_API_PORT:?}
label=akasha-e2e=fullstack
backend="$(cd "$(dirname "$0")/../../../backend" && pwd)"

stale=$(docker ps -aq --filter "label=$label")
[ -z "$stale" ] || docker rm -f $stale >/dev/null

cid=$(docker run -d --rm --label "$label" \
    --tmpfs /var/lib/postgresql/data \
    -e POSTGRES_USER=akasha -e POSTGRES_PASSWORD=akasha -e POSTGRES_DB=akasha \
    -p 127.0.0.1::5432 postgres:17.2)
redis=$(docker run -d --rm --label "$label" -p 127.0.0.1::6379 redis:8.2.1-alpine)

# -h: the image's init phase serves only the unix socket; TCP means ready.
until docker exec "$cid" pg_isready -q -h 127.0.0.1 -U akasha -d akasha; do
    sleep 0.5
done
db_port=$(docker port "$cid" 5432/tcp | head -1 | sed 's/.*://')
redis_port=$(docker port "$redis" 6379/tcp | head -1 | sed 's/.*://')
until docker exec "$redis" redis-cli ping >/dev/null 2>&1; do sleep 0.2; done

# Only these settings: the developer's AKASHA_* shell variables must not leak in.
run=(env -i PATH="$PATH" HOME="$HOME"
    AKASHA_ENVIRONMENT=test
    AKASHA_DATABASE_URL="postgresql://akasha:akasha@127.0.0.1:$db_port/akasha"
    AKASHA_REDIS_URL="redis://127.0.0.1:$redis_port/0"
    AKASHA_HOST=127.0.0.1 AKASHA_PORT="$port"
    # Node logs are stored down to the process log level (architecture §52).
    AKASHA_LOG_LEVEL=INFO
    # The worker runs executions; the scheduler task also dispatches event
    # triggers, which the suite fires. Like the worker, it logs failed polls
    # once teardown has removed the database.
    # Throwaway, like the database: credentials die with the run.
    AKASHA_CREDENTIAL_ENCRYPTION_KEY="$(openssl rand -base64 32 | tr '+/' '-_')"
    uv run --directory "$backend" --frozen akasha)
"${run[@]}" migrate
# exec: Playwright stops the server by signalling this process.
exec "${run[@]}" serve
