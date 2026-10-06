"""Authentication and authorization use cases (roadmap Phase 20).

- ``AuthenticationService``: first-run setup, login, logout, resolving a
  bearer token to an ``Identity``, changing one's own password.
- ``UserService``: admin-managed local accounts (create, edit, role,
  enable/disable, password reset) and the self-lockout invariants.
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
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

from akasha.application.executions import ExecutionNotFoundError, load_execution
from akasha.application.persistence import UnitOfWork
from akasha.application.projects import (
    Page,
    ProjectNotFoundError,
    UowFactory,
    check_page,
    load_project,
    to_page,
)
from akasha.domain.identity import (
    PAT_MARKER,
    Access,
    AccessToken,
    Identity,
    Role,
    User,
    UserSession,
    project_access,
    validate_password,
    validate_token_name,
)
from akasha.domain.primitives import Timestamps, new_id, utcnow
from akasha.domain.trigger import EVENT_SOURCE_FIELD, Trigger, TriggerType
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


class TriggerSourceNotFoundError(AkashaError):
    """An event trigger names a source project the caller cannot see."""

    code = "TRIGGER_SOURCE_NOT_FOUND"
    http_status = 422


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


class OwnAccountError(AkashaError):
    """An admin tried to lock themselves out (disable, demote) or to reset
    their own password without the current one."""

    code = "OWN_ACCOUNT"
    http_status = 409


class AccessTokenNotFoundError(AkashaError):
    code = "ACCESS_TOKEN_NOT_FOUND"
    http_status = 404


class AccessTokenLimitError(AkashaError):
    code = "ACCESS_TOKEN_LIMIT"
    http_status = 409


# Bounds the rows one account can create: tokens are for a few agents and
# scripts, not one per request.
MAX_ACCESS_TOKENS = 50
# ``last_used_at`` is recorded at most this often per token.
_LAST_USED_RESOLUTION = timedelta(hours=1)


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
            await uow.users.record_login(user.id, now)
            await uow.commit()
        user = replace(user, last_login_at=now)
        _audit("login_succeeded", user_id=user.id)
        return LoginResult(token, session, user)

    async def authenticate(self, token: str) -> Identity:
        """The identity behind a session token or a personal access token
        (recognized by ``PAT_MARKER``)."""
        now = self._clock()
        if token.startswith(PAT_MARKER):
            async with self._uow() as uow:
                pat = await uow.access_tokens.get_active(token_hash(token), now)
                if pat is not None:
                    await uow.access_tokens.mark_used(
                        pat[0].id, now, every=_LAST_USED_RESOLUTION
                    )
                    await uow.commit()
            if pat is None:
                raise AuthenticationRequiredError("Authentication required.")
            return Identity(user=pat[1], access_token=pat[0])
        async with self._uow() as uow:
            found = await uow.sessions.get_active(token_hash(token), now)
        if found is None:
            raise AuthenticationRequiredError("Authentication required.")
        session, user = found
        return Identity(user=user, session=session)

    async def logout(self, identity: Identity) -> None:
        """End the credential the caller used: its session, or (logging out
        with an access token) that token."""
        async with self._uow() as uow:
            if identity.session is not None:
                await uow.sessions.delete(identity.session.token_hash)
            if identity.access_token is not None:
                await uow.access_tokens.delete(
                    identity.access_token.id, user_id=identity.user.id
                )
            await uow.commit()
        _audit("logout", user_id=identity.user.id)

    async def create_access_token(
        self,
        identity: Identity,
        *,
        name: str,
        password: str,
        expires_in: timedelta | None,
    ) -> tuple[str, AccessToken]:
        """Issue a personal access token for the caller. The value is
        returned once; only its hash is stored.

        Requires the account password, like a password change: a stolen
        session (or token) alone must not mint credentials that outlive it.
        ``expires_in`` None: valid until revoked.
        """
        validate_token_name(name)
        user = identity.user
        now = self._clock()
        await self._require_password(user, password, now)
        value = PAT_MARKER + secrets.token_urlsafe(32)
        token = AccessToken(
            id=new_id("pat"),
            user_id=user.id,
            name=name,
            token_hash=token_hash(value),
            hint=value[: len(PAT_MARKER) + 4],
            created_at=now,
            expires_at=None if expires_in is None else now + expires_in,
        )
        async with self._uow() as uow:
            held = await uow.access_tokens.list_for_user(user.id)
            if len(held) >= MAX_ACCESS_TOKENS:
                raise AccessTokenLimitError(
                    f"A user can hold at most {MAX_ACCESS_TOKENS} access tokens;"
                    " revoke one first.",
                    details={"limit": MAX_ACCESS_TOKENS},
                )
            await uow.access_tokens.add(token)
            await uow.commit()
        _audit("access_token_created", user_id=user.id, token_id=token.id)
        return value, token

    async def list_access_tokens(self, identity: Identity) -> Sequence[AccessToken]:
        """The caller's own tokens (metadata only), newest first."""
        async with self._uow() as uow:
            return await uow.access_tokens.list_for_user(identity.user.id)

    async def revoke_access_token(self, identity: Identity, token_id: str) -> None:
        """Revoke one of the caller's own tokens; another user's token
        answers not found."""
        async with self._uow() as uow:
            mine = await uow.access_tokens.delete(token_id, user_id=identity.user.id)
            if not mine:
                raise AccessTokenNotFoundError(
                    "Access token not found.", details={"token_id": token_id}
                )
            await uow.commit()
        _audit("access_token_revoked", user_id=identity.user.id, token_id=token_id)

    async def _require_password(self, user: User, password: str, now: datetime) -> None:
        """Check ``user``'s password, throttled like logins."""
        self._throttle.attempt(user.username, now)
        async with self._uow() as uow:
            found = await uow.users.get_with_password(user.username)
        if found is None or not await self._verify(password, found[1]):
            raise InvalidCredentialsError("The current password is wrong.")
        self._throttle.reset(user.username)

    async def change_password(
        self, identity: Identity, current_password: str, new_password: str
    ) -> None:
        """Requires the current password (a stolen session alone must not
        take over the account) and revokes every other session. Access
        tokens stay: they are listed and revoked individually."""
        user = identity.user
        now = self._clock()
        new_hash = await self.hash_password(new_password)
        await self._require_password(user, current_password, now)
        keep = None if identity.session is None else identity.session.token_hash
        async with self._uow() as uow:
            await uow.users.update(_touched(user, now), new_hash)
            await uow.sessions.delete_for_user(user.id, keep=keep)
            await uow.commit()
        _audit("password_changed", user_id=user.id)


class UserService:
    """Account management; every method is admin-only.

    Invariants, enforced here and not in the UI: an admin cannot disable or
    demote their own account, nor reset their own password here (that needs
    the current one: ``change_password``); the last active admin can be
    neither disabled nor demoted, so the installation cannot lock itself
    out. Usernames are immutable.
    """

    def __init__(
        self, uow: UowFactory, auth: AuthenticationService, *, clock: Clock = utcnow
    ) -> None:
        self._uow = uow
        self._auth = auth
        self._clock = clock

    async def create_user(
        self,
        admin: Identity,
        username: str,
        password: str,
        *,
        role: Role,
        display_name: str | None = None,
    ) -> User:
        require_admin(admin)
        user = User.create(username, role=role, display_name=display_name)
        password_hash = await self._auth.hash_password(password)
        async with self._uow() as uow:
            if not await uow.users.add(user, password_hash):
                _audit("user_create_refused", actor_id=admin.user.id, reason="taken")
                raise UsernameTakenError(
                    "The username is taken.", details={"username": username}
                )
            await uow.commit()
        _audit("user_created", user_id=user.id, actor_id=admin.user.id, role=role)
        return user

    async def get_user(self, admin: Identity, user_id: str) -> User:
        require_admin(admin)
        async with self._uow() as uow:
            user = await uow.users.get(user_id)
        if user is None:
            raise UserNotFoundError("User not found.", details={"user_id": user_id})
        return user

    async def list_users(
        self,
        admin: Identity,
        *,
        offset: int = 0,
        limit: int = 50,
        search: str | None = None,
        role: Role | None = None,
        disabled: bool | None = None,
    ) -> Page[User]:
        require_admin(admin)
        check_page(offset, limit)
        async with self._uow() as uow:
            rows = await uow.users.list(
                offset=offset,
                limit=limit + 1,
                search=search,
                role=role,
                disabled=disabled,
            )
        return to_page(rows, offset, limit)

    async def update_user(
        self,
        admin: Identity,
        user_id: str,
        *,
        display_name: str | None = None,
        role: Role | None = None,
        disabled: bool | None = None,
        password: str | None = None,
    ) -> User:
        """Omitted (None) fields stay unchanged; ``display_name=""`` clears
        it. Disabling or a password reset revokes every session and access
        token of the user; a role change applies from their next request,
        since each request re-reads the user."""
        require_admin(admin)
        try:
            return await self._update(
                admin,
                user_id,
                display_name=display_name,
                role=role,
                disabled=disabled,
                password=password,
            )
        except AkashaError as error:
            _audit(
                "user_update_refused",
                user_id=user_id,
                actor_id=admin.user.id,
                reason=error.code,
            )
            raise

    async def _update(
        self,
        admin: Identity,
        user_id: str,
        *,
        display_name: str | None,
        role: Role | None,
        disabled: bool | None,
        password: str | None,
    ) -> User:
        if user_id == admin.user.id:
            if disabled or (role is not None and role is not Role.ADMIN):
                raise OwnAccountError(
                    "You cannot disable your own account or remove your own"
                    " admin role; another admin has to."
                )
            if password is not None:
                raise OwnAccountError(
                    "Change your own password in Settings: it needs the current"
                    " password."
                )
        password_hash = (
            None if password is None else await self._auth.hash_password(password)
        )
        async with self._uow() as uow:
            before = await uow.users.get(user_id)
            if before is None:
                raise UserNotFoundError("User not found.", details={"user_id": user_id})
            user = replace(
                before,
                role=before.role if role is None else role,
                disabled=before.disabled if disabled is None else disabled,
                display_name=(
                    before.display_name if display_name is None else display_name
                ),
            )
            loses_admin = (
                before.is_admin
                and not before.disabled
                and (user.disabled or not user.is_admin)
            )
            if loses_admin and await uow.users.lock_active_admins() <= 1:
                raise LastAdminError(
                    "The last active admin cannot be disabled or demoted."
                )
            user = _touched(user, self._clock())
            await uow.users.update(user, password_hash)
            if (user.disabled and not before.disabled) or password_hash is not None:
                # Recovery: an admin reset or disable ends every credential.
                await uow.sessions.delete_for_user(user.id)
                await uow.access_tokens.delete_for_user(user.id)
            await uow.commit()
        changes = [
            name
            for name, changed in (
                ("display_name", user.display_name != before.display_name),
                ("role", user.role is not before.role),
                ("disabled", user.disabled != before.disabled),
                ("password", password_hash is not None),
            )
            if changed
        ]
        # Ids, field names and the resulting role/status; never a password.
        _audit(
            "user_updated",
            user_id=user.id,
            actor_id=admin.user.id,
            changes=changes,
            role=user.role,
            disabled=user.disabled,
        )
        return user


class AuthorizationService:
    """Resource access checks for an authenticated identity."""

    def __init__(self, uow: UowFactory) -> None:
        self._uow = uow

    async def require_project(
        self, identity: Identity, project_id: str, need: Access = Access.VIEW
    ) -> Access:
        """The caller's access to the project, if at least ``need``. No
        access at all answers not found; too little answers forbidden (the
        caller already knows the project exists)."""
        async with self._uow() as uow:
            project = await load_project(uow, project_id)
            access = await _access(uow, identity.user, project.id, project.owner_id)
        if access is None:
            raise _project_not_found(project_id)
        _require(identity, access, need, project_id=project_id)
        return access

    async def require_execution(
        self, identity: Identity, execution_id: str, need: Access = Access.VIEW
    ) -> Access:
        """An execution is reachable exactly as its project is."""
        async with self._uow() as uow:
            execution = await load_execution(uow, execution_id)
            project = await uow.projects.get(execution.project_id)
            access = (
                None
                if project is None
                else await _access(uow, identity.user, project.id, project.owner_id)
            )
        if access is None:
            # The same answer as an unknown id.
            raise ExecutionNotFoundError(
                "Execution not found.", details={"execution_id": execution_id}
            )
        _require(identity, access, need, execution_id=execution_id)
        return access

    async def require_trigger_sources(
        self,
        identity: Identity,
        before: Sequence[Trigger],
        after: Sequence[Trigger],
    ) -> None:
        """Every event-trigger source the caller adds must be a project they
        can see. Without this, an editor of a shared project could point a
        trigger at a project only its owner sees and read that project's
        outputs through the runs it starts. Sources already in the draft
        (set by someone allowed to) stay as they are."""
        existing = {s for t in before if (s := _event_source(t)) is not None}
        added = {s for t in after if (s := _event_source(t)) is not None} - existing
        for source in sorted(added):
            try:
                await self.require_project(identity, source)
            except ProjectNotFoundError:
                raise TriggerSourceNotFoundError(
                    "An event trigger's source project does not exist or is not"
                    " visible to you.",
                    details={"source_project_id": source},
                ) from None

    @staticmethod
    def visible_to(identity: Identity) -> str | None:
        """Whose visible projects a listing is limited to (owned or shared);
        None = all projects (admins)."""
        return None if identity.user.is_admin else identity.user.id


async def _access(
    uow: UnitOfWork, user: User, project_id: str, owner_id: str | None
) -> Access | None:
    owns = user.is_admin or owner_id == user.id
    role = None if owns else await uow.members.role(project_id, user.id)
    return project_access(user, owner_id, role)


def _require(identity: Identity, access: Access, need: Access, **ids: str) -> None:
    if access < need:
        _audit(
            "project_access_denied",
            user_id=identity.user.id,
            access=access.name.lower(),
            need=need.name.lower(),
            **ids,
        )
        raise PermissionDeniedError(
            "Your access to this project does not allow this operation.",
            details={"access": access.name.lower(), "required": need.name.lower()},
        )


def _event_source(trigger: Trigger) -> str | None:
    if trigger.trigger_type is not TriggerType.EVENT:
        return None
    source = trigger.config.get(EVENT_SOURCE_FIELD)
    return source if isinstance(source, str) else None


def require_admin(identity: Identity) -> None:
    if not identity.user.is_admin:
        _audit("admin_required_denied", user_id=identity.user.id)
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
