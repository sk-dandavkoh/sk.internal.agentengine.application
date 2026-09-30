"""The Testcontainers gate itself (AGENTS §110: fixtures must not create
false confidence)."""

from collections.abc import Iterator

import pytest

from tests import support


class _Client:
    def __init__(self, fail: bool) -> None:
        self.fail = fail
        self.pings = 0

    def ping(self) -> None:
        self.pings += 1
        if self.fail:
            raise ConnectionError("refused")


@pytest.fixture
def fake_docker(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Client]:
    client = _Client(fail=True)

    class FakeDockerClient:
        def __init__(self, timeout: int) -> None:
            assert timeout == support._DOCKER_PING_TIMEOUT_S
            self.client = client

    monkeypatch.setattr(support, "DockerClient", FakeDockerClient)
    monkeypatch.delenv("AKASHA_TESTS_REQUIRE_DOCKER", raising=False)
    support.docker_available.cache_clear()
    yield client
    support.docker_available.cache_clear()  # the real probe decides again


def test_unreachable_daemon_skips_by_default(fake_docker: _Client) -> None:
    assert support.docker_available() is False


def test_unreachable_daemon_fails_when_docker_is_required(
    fake_docker: _Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AKASHA_TESTS_REQUIRE_DOCKER", "1")
    with pytest.raises(pytest.fail.Exception, match="unreachable: ConnectionError"):
        support.docker_available()


def test_the_decision_is_made_once_per_session(fake_docker: _Client) -> None:
    """Every fixture must see the same answer, even if the daemon changes
    state mid-run (the mixed skip/run behaviour this replaced)."""
    assert support.docker_available() is False
    fake_docker.fail = False  # the daemon wakes up
    assert support.docker_available() is False
    assert fake_docker.pings == 1
