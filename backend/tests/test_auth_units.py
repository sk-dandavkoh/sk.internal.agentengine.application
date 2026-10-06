"""Phase 20 units: password hashing, identity rules, the login throttle.
No database: these are the deterministic building blocks."""

from datetime import UTC, datetime, timedelta

import pytest

from akasha.application.auth import LoginThrottle, LoginThrottledError, token_hash
from akasha.domain.identity import (
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    Access,
    Role,
    User,
    WorkflowRole,
    validate_display_name,
    validate_password,
    validate_username,
    workflow_access,
)
from akasha.errors import DomainValidationError
from akasha.security.passwords import PasswordHasher

FAST = PasswordHasher(n=2**4, r=8, p=1)
PASSWORD = "correct horse battery"


def test_production_defaults_are_an_owasp_scrypt_setting() -> None:
    assert PasswordHasher() == PasswordHasher(n=2**14, r=8, p=5)
    assert PasswordHasher().hash(PASSWORD).startswith("scrypt$16384$8$5$")


def test_hash_verifies_and_never_contains_the_password() -> None:
    stored = FAST.hash(PASSWORD)
    assert PASSWORD not in stored
    assert FAST.verify(PASSWORD, stored)
    assert not FAST.verify(PASSWORD + "x", stored)
    assert not FAST.verify("", stored)


def test_hashes_are_salted() -> None:
    assert FAST.hash(PASSWORD) != FAST.hash(PASSWORD)


def test_hash_carries_its_parameters() -> None:
    """Raising the defaults later must not break existing hashes."""
    stored = FAST.hash(PASSWORD)
    assert PasswordHasher(n=2**15, r=8, p=2).verify(PASSWORD, stored)


@pytest.mark.parametrize(
    "stored",
    [
        "",
        "plaintext",
        "bcrypt$1$2$3$c2FsdA==$aGFzaA==",
        "scrypt$x$8$1$c2FsdA==$aGFzaA==",
        "scrypt$16$8$1$not-base64!$aGFzaA==",
        "scrypt$3$8$1$c2FsdA==$aGFzaA==",  # n not a power of two
        "scrypt$1073741824$8$1$c2FsdA==$aGFzaA==",  # would need 1 TiB
        "scrypt$16$8$1$c2FsdA==",
    ],
)
def test_malformed_stored_hash_never_matches(stored: str) -> None:
    assert FAST.verify(PASSWORD, stored) is False


@pytest.mark.parametrize("name", ["admin", "a.b-c_d", "abc", "x" * 64, "0day"])
def test_valid_usernames(name: str) -> None:
    assert validate_username(name) == name


@pytest.mark.parametrize(
    "name", ["", "ab", "Admin", "x" * 65, "-lead", "has space", "ümlaut", "a/b"]
)
def test_invalid_usernames(name: str) -> None:
    with pytest.raises(DomainValidationError):
        validate_username(name)


def test_password_length_bounds() -> None:
    validate_password("x" * MIN_PASSWORD_LENGTH)
    validate_password("x" * MAX_PASSWORD_LENGTH)
    for bad in ("x" * (MIN_PASSWORD_LENGTH - 1), "x" * (MAX_PASSWORD_LENGTH + 1)):
        with pytest.raises(DomainValidationError):
            validate_password(bad)


def test_display_name_is_trimmed_and_blank_means_none() -> None:
    assert validate_display_name("  Alice Example ") == "Alice Example"
    assert validate_display_name("   ") is None
    assert validate_display_name(None) is None
    assert User.create("alice", display_name="").display_name is None


@pytest.mark.parametrize("name", ["x" * 101, "tab\there", "new\nline"])
def test_invalid_display_names(name: str) -> None:
    with pytest.raises(DomainValidationError):
        validate_display_name(name)


V, E = WorkflowRole.VIEWER, WorkflowRole.EDITOR


@pytest.mark.parametrize(
    ("who", "owner", "role", "expected"),
    [
        # The owner and admins manage, whatever a stray member row says.
        ("alice", "alice", None, Access.MANAGE),
        ("alice", "alice", V, Access.MANAGE),
        ("root", "alice", None, Access.MANAGE),
        ("root", None, None, Access.MANAGE),
        # Members get exactly their role.
        ("bob", "alice", V, Access.VIEW),
        ("bob", "alice", E, Access.EDIT),
        # Everyone else: nothing, also for ownerless (pre-accounts) workflows.
        ("bob", "alice", None, None),
        ("alice", None, None, None),
        # A shared ownerless workflow is reachable by its members only.
        ("alice", None, V, Access.VIEW),
    ],
)
def test_workflow_access_rules(
    who: str, owner: str | None, role: WorkflowRole | None, expected: Access | None
) -> None:
    users = {
        "root": User.create("root", role=Role.ADMIN),
        "alice": User.create("alice"),
        "bob": User.create("bob"),
    }
    owner_id = None if owner is None else users[owner].id
    assert workflow_access(users[who], owner_id, role) == expected


def test_access_levels_are_ordered() -> None:
    assert Access.VIEW < Access.EDIT < Access.MANAGE


def test_token_hash_is_stable_and_not_the_token() -> None:
    assert token_hash("t") == token_hash("t") != "t"
    assert len(token_hash("t")) == 64


T0 = datetime(2026, 1, 1, tzinfo=UTC)


def test_throttle_blocks_after_max_failures_until_window_passes() -> None:
    throttle = LoginThrottle(max_failures=3, window=timedelta(minutes=10))
    for minute in range(3):
        throttle.attempt("alice", T0 + timedelta(minutes=minute))
    with pytest.raises(LoginThrottledError) as caught:
        throttle.attempt("alice", T0 + timedelta(minutes=3))
    # The oldest failure (T0) ages out at T0+10min: 7 minutes from now.
    assert caught.value.details["retry_after_seconds"] == 7 * 60 + 1
    throttle.attempt("bob", T0 + timedelta(minutes=3))  # per username
    throttle.attempt("alice", T0 + timedelta(minutes=10))  # T0 aged out
    # A refused attempt is not counted: it did not reach the password check.
    assert len(throttle._failures["alice"]) == 3


def test_throttle_reset_on_success() -> None:
    throttle = LoginThrottle(max_failures=2)
    throttle.attempt("alice", T0)
    throttle.reset("alice")
    throttle.attempt("alice", T0)
    throttle.attempt("alice", T0)


def test_throttle_memory_is_bounded() -> None:
    throttle = LoginThrottle(max_tracked=10, window=timedelta(minutes=1))
    for i in range(10):
        throttle.attempt(f"old{i}", T0)
    throttle.attempt("new", T0 + timedelta(minutes=5))
    assert set(throttle._failures) == {"new"}
