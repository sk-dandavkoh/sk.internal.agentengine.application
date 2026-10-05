"""Authentication and authorization use cases (roadmap Phase 20).

- ``AuthenticationService``: first-run setup, login, logout, resolving a
  bearer token to an ``Identity``, changing one's own password.
- ``UserService``: admin-managed local accounts.
- ``AuthorizationService``: what an ``Identity`` may reach. Resources a
  caller may not see answer "not found", never "forbidden", so ids cannot
  be probed for existence.

Sessions are opaque random tokens; only their SHA-256 is stored. They expire
absolutely (no sliding renewal) and are revoked by logout, password change,
password reset and disabling the user.
"""

import asyncio
import hashlib
import logging
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

from akasha.application.executions import ExecutionNotFoundError, load_execution
from akasha.application.projects import (
    Page,
    ProjectNotFoundError,
    UowFactory,
    check_page,
    load_project,
    to_page,
)
from akasha.domain.identity import (
    Identity,
    Role,
    User,
    UserSession,
    may_access_project,
    validate_password,
)
from akasha.domain.primitives import Timestamps, utcnow
from akasha.errors import AkashaError
from akasha.security.passwords import PasswordHasher

logger = logging.getLogger("akasha.application.auth")

Clock = Callable[[], datetime]


class AuthenticationRequiredError(AkashaError):
    """No, an unknown, an expired or a revoked session token."""

    code = "AUTHENTICATION_REQUIRED"
    http_status = 401


class InvalidCredentialsError(AkashaError):
    """Wrong username or password. Deliberately does not say which."""

    code = "INVALID_CREDENTIALS"
    http_status = 401


class LoginThrottledError(AkashaError):
    code = "LOGIN_THROTTLED"
    http_status = 429


class PermissionDeniedError(AkashaError):
    code = "PERMISSION_DENIED"
    http_status = 403


class SetupCompletedError(AkashaError):
    code = "SETUP_ALREADY_COMPLETED"
    http_status = 409


class UsernameTakenError(AkashaError):
    code = "USERNAME_TAKEN"
    http_status = 409


class UserNotFoundError(AkashaError):
    code = "USER_NOT_FOUND"
    http_status = 404


class LastAdminError(AkashaError):
    code = "LAST_ADMIN"
    http_status = 409


def token_hash(token: str) -> str:
    # A plain hash suffices: tokens carry 256 random bits, so there is
    # nothing to brute-force that a slow hash would protect.
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass
class LoginThrottle:
    """At most ``max_failures`` failed attempts per username within
    ``window``; further attempts are refused until the oldest one ages out.

    ``attempt`` counts an attempt as failed *before* the password is
    checked, and ``reset`` clears the count after a success. Counting after
    the (slow) check would let parallel requests all pass the limit.

    Per username, not per client address: behind a reverse proxy the
    address is not trustworthy without configuration this phase does not
    have. The cost is that an attacker can lock a known username out for
    one window.

    ponytail: in-process memory, so each API process counts on its own and
    a restart forgets. Move to Redis with the worker architecture (Phase 22)
    if several API processes serve logins.
    """

    max_failures: int = 5
    window: timedelta = timedelta(minutes=15)
    max_tracked: int = 10_000
    _failures: dict[str, list[datetime]] = field(default_factory=dict)

    def attempt(self, key: str, now: datetime) -> None:
        recent = self._recent(key, now)
        if len(recent) >= self.max_failures:
            retry = (recent[0] + self.window - now).total_seconds()
            raise LoginThrottledError(
                "Too many failed login attempts; try again later.",
                details={"retry_after_seconds": max(1, int(retry) + 1)},
            )
        if len(self._failures) >= self.max_tracked:
            # Bound memory against username spraying: forget stale keys.
            for stale in [k for k in self._failures if not self._recent(k, now)]:
                del self._failures[stale]
        self._failures[key] = [*recent, now]

    def reset(self, key: str) -> None:
        self._failures.pop(key, None)

    def _recent(self, key: str, now: datetime) -> list[datetime]:
        return [t for t in self._failures.get(key, ()) if now - t < self.window]


@dataclass(frozen=True)
class LoginResult:
    token: str  # shown once; only its hash is stored
    session: UserSession
    user: User


class AuthenticationService:
    def __init__(
        self,
        uow: UowFactory,
        *,
        session_ttl: timedelta,
        hasher: PasswordHasher | None = None,
        clock: Clock = utcnow,
        throttle: LoginThrottle | None = None,
    ) -> None:
        self._uow = uow
        self._ttl = session_ttl
        self._hasher = hasher or PasswordHasher()
        self._clock = clock
        self._throttle = throttle or LoginThrottle()
        # Verified against when the username is unknown, so a miss costs
        # the same time as a wrong password (no username enumeration).
        self._dummy_hash = self._hasher.hash(secrets.token_urlsafe(16))

    async def hash_password(self, password: str) -> str:
        validate_password(password)
        return await asyncio.to_thread(self._hasher.hash, password)

    async def _verify(self, password: str, stored: str) -> bool:
        return await asyncio.to_thread(self._hasher.verify, password, stored)

    async def setup_required(self) -> bool:
        """Whether no account exists yet, i.e. ``setup`` would succeed."""
        async with self._uow() as uow:
            return not await uow.users.list(limit=1)

    async def setup(self, username: str, password: str) -> User:
        """Create the first account, an admin. Only possible while no user
        exists: afterwards accounts are created by admins."""
        user = User.create(username, role=Role.ADMIN)
        password_hash = await self.hash_password(password)
        async with self._uow() as uow:
            if not await uow.users.add_first(user, password_hash):
                raise SetupCompletedError("The initial admin account already exists.")
            await uow.commit()
        _audit("setup_completed", user_id=user.id)
        return user

    async def login(self, username: str, password: str) -> LoginResult:
        key = username.lower()
        now = self._clock()
        self._throttle.attempt(key, now)
        async with self._uow() as uow:
            found = await uow.users.get_with_password(key)
        if found is None:
            await self._verify(password, self._dummy_hash)
            user = None
        else:
            user, stored = found
            if not await self._verify(password, stored) or user.disabled:
                user = None
        if user is None:
            _audit("login_failed", user_id=None if found is None else found[0].id)
            raise InvalidCredentialsError("Invalid username or password.")
        self._throttle.reset(key)
        token = secrets.token_urlsafe(32)
        session = UserSession(token_hash(token), user.id, now, now + self._ttl)
        async with self._uow() as uow:
            # Housekeeping piggybacks on logins; expired rows are already
            # inert (get_active filters them).
            await uow.sessions.delete_expired(now)
            await uow.sessions.add(session)
            await uow.commit()
        _audit("login_succeeded", user_id=user.id)
        return LoginResult(token, session, user)

    async def authenticate(self, token: str) -> Identity:
        async with self._uow() as uow:
            found = await uow.sessions.get_active(token_hash(token), self._clock())
        if found is None:
            raise AuthenticationRequiredError("Authentication required.")
        session, user = found
        return Identity(user=user, session=session)

    async def logout(self, identity: Identity) -> None:
        async with self._uow() as uow:
            await uow.sessions.delete(identity.session.token_hash)
            await uow.commit()
        _audit("logout", user_id=identity.user.id)

    async def change_password(
        self, identity: Identity, current_password: str, new_password: str
    ) -> None:
        """Requires the current password (a stolen session alone must not
        take over the account) and revokes every other session."""
        user = identity.user
        now = self._clock()
        new_hash = await self.hash_password(new_password)
        self._throttle.attempt(user.username, now)
        async with self._uow() as uow:
            found = await uow.users.get_with_password(user.username)
            if found is None or not await self._verify(current_password, found[1]):
                raise InvalidCredentialsError("The current password is wrong.")
            self._throttle.reset(user.username)
            await uow.users.update(_touched(found[0], now), new_hash)
            await uow.sessions.delete_for_user(
                user.id, keep=identity.session.token_hash
            )
            await uow.commit()
        _audit("password_changed", user_id=user.id)


class UserService:
    """Account management; every method is admin-only."""

    def __init__(
        self, uow: UowFactory, auth: AuthenticationService, *, clock: Clock = utcnow
    ) -> None:
        self._uow = uow
        self._auth = auth
        self._clock = clock

    async def create_user(
        self, admin: Identity, username: str, password: str, *, role: Role
    ) -> User:
        require_admin(admin)
        user = User.create(username, role=role)
        password_hash = await self._auth.hash_password(password)
        async with self._uow() as uow:
            if not await uow.users.add(user, password_hash):
                raise UsernameTakenError(
                    "The username is taken.", details={"username": username}
                )
            await uow.commit()
        _audit("user_created", user_id=user.id, actor_id=admin.user.id)
        return user

    async def list_users(
        self, admin: Identity, *, offset: int = 0, limit: int = 50
    ) -> Page[User]:
        require_admin(admin)
        check_page(offset, limit)
        async with self._uow() as uow:
            rows = await uow.users.list(offset=offset, limit=limit + 1)
        return to_page(rows, offset, limit)

    async def update_user(
        self,
        admin: Identity,
        user_id: str,
        *,
        disabled: bool | None = None,
        password: str | None = None,
    ) -> User:
        """Disable/enable an account or reset its password. Either revokes
        all of the user's sessions. The last active admin cannot be
        disabled, so the installation cannot lock itself out."""
        require_admin(admin)
        password_hash = (
            None if password is None else await self._auth.hash_password(password)
        )
        async with self._uow() as uow:
            user = await uow.users.get(user_id)
            if user is None:
                raise UserNotFoundError("User not found.", details={"user_id": user_id})
            if disabled and not user.disabled and user.is_admin:
                if await uow.users.lock_active_admins() <= 1:
                    raise LastAdminError("The last active admin cannot be disabled.")
            if disabled is not None:
                user = replace(user, disabled=disabled)
            user = _touched(user, self._clock())
            await uow.users.update(user, password_hash)
            if disabled or password_hash is not None:
                await uow.sessions.delete_for_user(user.id)
            await uow.commit()
        _audit("user_updated", user_id=user.id, actor_id=admin.user.id)
        return user


class AuthorizationService:
    """Resource access checks for an authenticated identity."""

    def __init__(self, uow: UowFactory) -> None:
        self._uow = uow

    async def require_project(self, identity: Identity, project_id: str) -> None:
        async with self._uow() as uow:
            project = await load_project(uow, project_id)
        if not may_access_project(identity.user, project.owner_id):
            raise _project_not_found(project_id)

    async def require_execution(self, identity: Identity, execution_id: str) -> None:
        """An execution is visible exactly when its project is."""
        async with self._uow() as uow:
            execution = await load_execution(uow, execution_id)
            project = await uow.projects.get(execution.project_id)
        owner = None if project is None else project.owner_id
        if not may_access_project(identity.user, owner):
            # The same answer as an unknown id.
            raise ExecutionNotFoundError(
                "Execution not found.", details={"execution_id": execution_id}
            )

    @staticmethod
    def owner_filter(identity: Identity) -> str | None:
        """The owner a project listing is limited to; None = all projects."""
        return None if identity.user.is_admin else identity.user.id


def require_admin(identity: Identity) -> None:
    if not identity.user.is_admin:
        raise PermissionDeniedError("This operation requires an admin.")


def _project_not_found(project_id: str) -> ProjectNotFoundError:
    return ProjectNotFoundError(
        "Project not found.", details={"project_id": project_id}
    )


def _touched(user: User, at: datetime) -> User:
    return replace(
        user,
        timestamps=Timestamps(created_at=user.timestamps.created_at, updated_at=at),
    )


def _audit(event: str, **extra: object) -> None:
    # Ids only: usernames are what attackers type into the field, and a
    # password pasted there by mistake must not reach the logs.
    logger.info(event, extra={"event": event, **extra})
