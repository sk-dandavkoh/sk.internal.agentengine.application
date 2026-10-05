"""The operator lifecycle on the shipped image and docker-compose.yml
(roadmap Phase 26, AGENTS §100/§101):

clean install → start → migrate → run → stop → restart → recreate →
backup → destroy → restore → verify → recover (worker crash, database
restart).

Opt-in, because it builds the image and takes minutes:

    AKASHA_TESTS_DEPLOYMENT=1 uv run pytest tests/deployment

Every run is its own Compose project (own volumes, network and a random
loopback port) configured only by its own env file — never the
developer's .env or shell — and is removed with its volumes afterwards.
"""

import base64
import gzip
import json
import os
import secrets
import subprocess
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import pytest

from akasha.domain.primitives import new_id
from tests.support import docker_available

REPO = Path(__file__).resolve().parents[3]
PASSWORD = "deployment-test-admin"
SECRET = "sk-deployment-secret-value"

pytestmark = pytest.mark.skipif(
    os.environ.get("AKASHA_TESTS_DEPLOYMENT") != "1",
    reason="AKASHA_TESTS_DEPLOYMENT=1 not set (builds the image, slow)",
)


class Deployment:
    def __init__(self, tmp_path: Path, *, given_secrets: bool = True) -> None:
        self.project = f"akasha-e2e-{secrets.token_hex(4)}"
        self.env_file = tmp_path / "deployment.env"
        # Without them, the stack generates its own (the zero-config path).
        settings = (
            {
                "POSTGRES_PASSWORD": secrets.token_hex(16),
                "AKASHA_CREDENTIAL_ENCRYPTION_KEY": base64.urlsafe_b64encode(
                    os.urandom(32)
                ).decode(),
            }
            if given_secrets
            else {}
        )
        settings |= {
            "AKASHA_ENV_FILE": self.env_file.as_posix(),
            "AKASHA_IMAGE_TAG": "e2e",
            "AKASHA_HTTP_PORT": "0",  # Docker picks a free port
            # Fast crash recovery, so the recovery step takes seconds.
            "AKASHA_WORKER_HEARTBEAT_SECONDS": "1",
            "AKASHA_EXECUTION_STALE_SECONDS": "3",
            "AKASHA_QUEUE_RECOVERY_SECONDS": "2",
        }
        self.env_file.write_text("".join(f"{k}={v}\n" for k, v in settings.items()))
        # Shell variables would override the env file in interpolation.
        self.process_env = {
            k: v
            for k, v in os.environ.items()
            if k not in settings and not k.startswith("AKASHA_")
        }

    def compose(
        self, *args: str, stdin: bytes | None = None, check: bool = True
    ) -> subprocess.CompletedProcess[bytes]:
        argv = ["docker", "compose", "-p", self.project]
        argv += ["--env-file", str(self.env_file), *args]
        result = subprocess.run(
            argv,
            cwd=REPO,
            env=self.process_env,
            input=stdin,
            capture_output=True,
            timeout=900,
            check=False,
        )
        if check and result.returncode != 0:
            pytest.fail(
                f"{' '.join(args)} exited {result.returncode}:\n"
                + result.stderr.decode(errors="replace")[-3000:],
                pytrace=False,
            )
        return result

    def logs(self, *services: str) -> str:
        return self.compose("logs", "--no-color", *services).stdout.decode()

    def exit_codes(self) -> dict[str, int]:
        out = self.compose("ps", "-a", "--format", "json").stdout.decode()
        rows = [json.loads(line) for line in out.splitlines() if line.strip()]
        return {row["Service"]: row["ExitCode"] for row in rows}

    def wait_until[T](
        self, what: str, probe: Callable[[], T | None], timeout: float = 120
    ) -> T:
        """Bounded polling of an observable condition, never a blind sleep."""
        deadline = time.monotonic() + timeout
        while True:
            value = probe()
            if value is not None:
                return value
            if time.monotonic() > deadline:
                pytest.fail(
                    f"timed out waiting for {what}\n{self.logs()[-6000:]}",
                    pytrace=False,
                )
            time.sleep(0.5)

    def api(self, headers: dict[str, str] | None = None) -> httpx.Client:
        """A client for the stack's one published port (the UI, proxying
        the API), once /ready passes through it."""
        address = self.compose("port", "ui", "8080").stdout.decode().strip()
        client = httpx.Client(
            base_url=f"http://{address}", headers=headers or {}, timeout=10
        )

        def ready() -> bool | None:
            try:
                return client.get("/ready").status_code == 200 or None
            except httpx.TransportError:
                return None  # not listening yet

        self.wait_until("/ready", ready)
        return client


def _deployment(tmp_path: Path, *, given_secrets: bool) -> Iterator[Deployment]:
    if not docker_available():
        pytest.skip("Docker daemon not available")
    d = Deployment(tmp_path, given_secrets=given_secrets)
    try:
        yield d
    finally:
        d.compose("down", "-v", "--remove-orphans", check=False)


@pytest.fixture
def deployment(tmp_path: Path) -> Iterator[Deployment]:
    yield from _deployment(tmp_path, given_secrets=True)


@pytest.fixture
def zero_config(tmp_path: Path) -> Iterator[Deployment]:
    yield from _deployment(tmp_path, given_secrets=False)


def login(api: httpx.Client) -> dict[str, str]:
    response = api.post(
        "/api/v1/auth/login", json={"username": "admin", "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def publish(api: httpx.Client, name: str, delay_seconds: float) -> tuple[str, int]:
    """input(title) → delay → output, published as version 1."""
    project = api.post("/api/v1/projects", json={"name": name})
    assert project.status_code == 201, project.text
    pid = project.json()["id"]
    source, wait, sink = new_id("node"), new_id("node"), new_id("node")
    nodes = [
        {"id": source, "node_type": "core.input", "config": {"field": "title"}},
        {"id": wait, "node_type": "core.delay", "config": {"seconds": delay_seconds}},
        {"id": sink, "node_type": "core.output", "config": {}},
    ]
    edges = [
        {
            "source_node_id": a,
            "source_port": "value",
            "target_node_id": b,
            "target_port": "value",
        }
        for a, b in ((source, wait), (wait, sink))
    ]
    draft = {"graph": {"nodes": nodes, "edges": edges}, "triggers": []}
    body = {"expected_revision": 1, "name": name, "draft": draft}
    updated = api.put(f"/api/v1/projects/{pid}", json=body)
    assert updated.status_code == 200, updated.text
    published = api.post(
        f"/api/v1/projects/{pid}/publish", json={"expected_revision": 2}
    )
    assert published.status_code == 201, published.text
    return pid, published.json()["version"]


def start(api: httpx.Client, pid: str, version: int, title: str) -> str:
    response = api.post(
        f"/api/v1/projects/{pid}/versions/{version}/execute", json={"title": title}
    )
    assert response.status_code == 201, response.text
    execution_id: str = response.json()["id"]
    return execution_id


def status(api: httpx.Client, execution_id: str) -> str:
    response = api.get(f"/api/v1/executions/{execution_id}")
    assert response.status_code == 200, response.text
    value: str = response.json()["status"]
    return value


def wait_for(d: Deployment, api: httpx.Client, execution_id: str, wanted: str) -> None:
    terminal = {"completed", "failed", "cancelled", "timeout"}

    def reached() -> bool | None:
        current = status(api, execution_id)
        assert current == wanted or current not in terminal, current
        return current == wanted or None

    d.wait_until(f"execution {execution_id} {wanted}", reached)


def test_install_run_restart_backup_restore_recover(deployment: Deployment) -> None:
    d = deployment

    # -- clean install: build, start, migrate, available --------------------
    d.compose("up", "-d", "--build")
    api = d.api()
    assert api.get("/health").json()["status"] == "ok"
    assert d.exit_codes()["migrate"] == 0
    first_migration = d.logs("migrate")
    rerun = d.compose("run", "--rm", "migrate", "migrate", "--json")
    revision = json.loads(rerun.stdout)["schema_revision"]
    assert revision and f"database at schema {revision}" in first_migration

    # -- run: first admin, a credential, a project executed by the worker ---
    setup = api.post(
        "/api/v1/auth/setup", json={"username": "admin", "password": PASSWORD}
    )
    assert setup.status_code == 201, setup.text
    api.headers.update(login(api))
    credential = api.post(
        "/api/v1/credentials",
        json={"name": "ops-check", "kind": "api_key", "value": SECRET},
    )
    assert credential.status_code == 201, credential.text
    pid, version = publish(api, "echo", delay_seconds=0)
    slow_pid, slow_version = publish(api, "slow", delay_seconds=8)
    first = start(api, pid, version, "before backup")
    wait_for(d, api, first, "completed")
    headers = dict(api.headers)

    # -- stop: graceful, exit 0, shutdown logged ----------------------------
    d.compose("stop")
    codes = d.exit_codes()
    assert codes["api"] == 0 and codes["worker"] == 0, codes
    assert "application_stopped" in d.logs("api")

    # -- restart, then recreate the containers: state survives both --------
    for cycle in (("start",), ("down",)):
        d.compose(*cycle)
        if cycle == ("down",):
            d.compose("up", "-d")
        api = d.api(headers)  # the session itself is persisted
        assert status(api, first) == "completed"
        assert api.get(f"/api/v1/projects/{pid}").status_code == 200

    # -- backup: streamed out of the running API container -----------------
    backup = d.compose("exec", "-T", "api", "akasha", "backup", "-o", "-").stdout
    document = json.loads(gzip.decompress(backup))
    assert document["schema_revision"] == revision
    assert SECRET.encode() not in gzip.decompress(backup)

    # -- destroy: containers and every volume ------------------------------
    d.compose("down", "-v")
    volumes = subprocess.run(
        ["docker", "volume", "ls", "-q", "--filter", f"name={d.project}_"],
        capture_output=True,
        check=True,
    ).stdout
    assert volumes.strip() == b""

    # -- restore into a fresh installation ---------------------------------
    d.compose("up", "-d")
    fresh = d.api()
    rejected = fresh.post(
        "/api/v1/auth/login", json={"username": "admin", "password": PASSWORD}
    )
    assert rejected.status_code == 401  # really empty: the admin is gone
    d.compose("stop", "api", "worker")  # no writer while restoring
    restored = d.compose(
        "run", "--rm", "-T", "--no-deps", "api", "restore", "-", stdin=backup
    )
    assert b"restored backup" in restored.stdout
    d.compose("up", "-d")

    # -- verify: accounts, projects, history, credentials, and it runs -----
    api = d.api()
    api.headers.update(login(api))
    assert status(api, first) == "completed"
    assert api.get(f"/api/v1/projects/{pid}/versions/{version}").status_code == 200
    names = [c["name"] for c in api.get("/api/v1/credentials").json()["items"]]
    assert names == ["ops-check"]
    after_restore = start(api, pid, version, "after restore")
    wait_for(d, api, after_restore, "completed")

    # -- recover: a worker killed mid-run; its execution is resumed --------
    slow = start(api, slow_pid, slow_version, "crash")
    d.wait_until("slow run", lambda: status(api, slow) == "running" or None)
    d.compose("kill", "worker")  # SIGKILL: a crash, no graceful stop
    assert status(api, slow) == "running"
    d.compose("start", "worker")
    wait_for(d, api, slow, "completed")

    # -- recover: the database restarts under a running API ----------------
    d.compose("restart", "postgres")
    api = d.api(dict(api.headers))
    after_db_restart = start(api, pid, version, "after database restart")
    wait_for(d, api, after_db_restart, "completed")


def test_zero_config_install_serves_ui_and_keeps_generated_secrets(
    zero_config: Deployment,
) -> None:
    """No secrets given: the stack generates them, serves the UI and the API
    on one port, offers first-run setup, and keeps its secrets across a
    recreation (or stored credentials and the database would be lost)."""
    d = zero_config
    d.compose("up", "-d", "--build")
    api = d.api()
    page = api.get("/projects")  # a client-side route: index.html
    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    assert page.headers["x-content-type-options"] == "nosniff"
    assert api.get("/assets/missing.js").status_code == 404
    assert api.get("/api/v1/auth/setup").json() == {"required": True}

    setup = api.post(
        "/api/v1/auth/setup", json={"username": "admin", "password": PASSWORD}
    )
    assert setup.status_code == 201, setup.text
    assert api.get("/api/v1/auth/setup").json() == {"required": False}
    api.headers.update(login(api))
    credential = api.post(
        "/api/v1/credentials",
        json={"name": "generated-key", "kind": "api_key", "value": SECRET},
    )
    assert credential.status_code == 201, credential.text

    def key() -> bytes:
        path = "/run/akasha-secrets/AKASHA_CREDENTIAL_ENCRYPTION_KEY"
        return d.compose("exec", "-T", "api", "cat", path).stdout

    generated = key()
    assert len(base64.urlsafe_b64decode(generated)) == 32

    d.compose("down")  # containers gone, volumes (and secrets) kept
    d.compose("up", "-d")
    api = d.api()
    api.headers.update(login(api))  # same database password
    assert key() == generated  # stored credentials stay decryptable
    names = [c["name"] for c in api.get("/api/v1/credentials").json()["items"]]
    assert names == ["generated-key"]
    pid, version = publish(api, "echo", delay_seconds=0)
    wait_for(d, api, start(api, pid, version, "zero config"), "completed")
