"""Authentication endpoints and the request guards every protected route uses.

Clients send ``Authorization: Bearer <token>`` (from ``POST /auth/login``).
The web UI instead logs in with ``"cookie": true`` and never sees the token:
it rides in an HttpOnly cookie (ADR 0002). The cookie counts only on
requests that also carry the ``X-Akasha-CSRF`` header, which a cross-origin
page cannot add without a CORS preflight this API never grants, and a form
or an ``<img>`` cannot add at all (CSRF).

Guards, applied router-wide in ``routes.create_api_v1_router`` so a new
route is protected by default:

- ``current_identity``: a valid session, else 401.
- ``authorize_path``: a route whose path names a ``workflow_id`` or an
  ``execution_id`` needs access to that resource (else 404), at the level
  the route declares with ``requires`` (else 403). Undeclared: reads
  (GET) need view access, everything else edit access, so a new write
  route never opens to viewers by accident.
- ``require_admin``: admin-only routers (credentials, users), else 403.
"""

from datetime import datetime, timedelta
from enum import StrEnum
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response, status
from pydantic import BaseModel, Field, SecretStr

from akasha.api.schemas import ErrorResponse
from akasha.api.workflow_schemas import PageInfo
from akasha.application.auth import (
    AuthenticationRequiredError,
    AuthenticationService,
    AuthorizationService,
    UserService,
)
from akasha.application.auth import (
    require_admin as require_admin_identity,
)
from akasha.application.workflows import MAX_PAGE_SIZE, Page
from akasha.domain.identity import (
    MAX_DISPLAY_NAME_LENGTH,
    MAX_PASSWORD_LENGTH,
    MAX_TOKEN_NAME_LENGTH,
    Access,
    AccessToken,
    Identity,
    Role,
    User,
)
from akasha.errors import AkashaError, ConfigurationError

_MAX_TOKEN_LENGTH = 256
SESSION_COOKIE = "akasha_session"
# Covers /api/v1 including the live WebSocket, nothing else on the origin.
SESSION_COOKIE_PATH = "/api/v1"
CSRF_HEADER = "X-Akasha-CSRF"


class CsrfHeaderRequiredError(AkashaError):
    """A cookie login without the anti-CSRF header."""

    code = "CSRF_HEADER_REQUIRED"
    http_status = 403


Username = Annotated[str, Field(min_length=1, max_length=64)]
Password = Annotated[SecretStr, Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)]


def auth_service(request: Request) -> AuthenticationService:
    service: AuthenticationService | None = request.app.state.container.auth
    if service is None:
        raise ConfigurationError("Authentication is not configured (database).")
    return service


def _authorization(request: Request) -> AuthorizationService:
    service: AuthorizationService | None = request.app.state.container.authorization
    if service is None:
        raise ConfigurationError("Authentication is not configured (database).")
    return service


def _users(request: Request) -> UserService:
    service: UserService | None = request.app.state.container.users
    if service is None:
        raise ConfigurationError("Authentication is not configured (database).")
    return service


def _bounded(token: str | None) -> str | None:
    return token if token and len(token) <= _MAX_TOKEN_LENGTH else None


def bearer_token(request: Request) -> str | None:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    return _bounded(token.strip()) if scheme.lower() == "bearer" else None


def session_token(request: Request) -> str | None:
    """The bearer token, else the session cookie if the request proves it
    comes from the UI's own origin (``X-Akasha-CSRF``)."""
    token = bearer_token(request)
    if token is None and CSRF_HEADER.lower() in request.headers:
        token = _bounded(request.cookies.get(SESSION_COOKIE))
    return token


Auth = Annotated[AuthenticationService, Depends(auth_service)]


async def current_identity(request: Request, auth: Auth) -> Identity:
    token = session_token(request)
    if token is None:
        raise AuthenticationRequiredError("Authentication required.")
    identity = await auth.authenticate(token)
    # Read by the access log middleware: who made the request (audit).
    request.state.user_id = identity.user.id
    return identity


CurrentIdentity = Annotated[Identity, Depends(current_identity)]
Authorization = Annotated[AuthorizationService, Depends(_authorization)]


# The workflow access a route needs, in its OpenAPI operation (documented).
ACCESS_EXTENSION = "x-akasha-access"


def requires(access: Access) -> dict[str, Any]:
    """Route options declaring the workflow access the route needs:
    ``@router.post(..., **requires(Access.VIEW))``."""
    return {"openapi_extra": {ACCESS_EXTENSION: access.name.lower()}}


def _needed(request: Request) -> Access:
    extra = getattr(request.scope.get("route"), "openapi_extra", None) or {}
    declared = extra.get(ACCESS_EXTENSION)
    if declared is not None:
        return Access[declared.upper()]
    return Access.VIEW if request.method in ("GET", "HEAD") else Access.EDIT


async def authorize_path(request: Request, identity: CurrentIdentity) -> None:
    params = request.path_params
    if "workflow_id" in params:
        await _authorization(request).require_workflow(
            identity, params["workflow_id"], _needed(request)
        )
    if "execution_id" in params:
        await _authorization(request).require_execution(
            identity, params["execution_id"], _needed(request)
        )


async def require_admin(identity: CurrentIdentity) -> None:
    require_admin_identity(identity)


# -- schemas ------------------------------------------------------------------


class LoginRequest(BaseModel):
    username: Username
    password: Password


class SessionLoginRequest(LoginRequest):
    # True: the session goes into an HttpOnly cookie, not the response body
    # (browsers; needs the X-Akasha-CSRF header).
    cookie: bool = False


class UserDetail(BaseModel):
    """A user's public metadata: never a password hash, session or token."""

    id: str
    username: str
    # Null: none set; show the username.
    display_name: str | None
    role: Role
    disabled: bool
    created_at: datetime
    updated_at: datetime
    # Null: never logged in since this was recorded (migration 0016).
    last_login_at: datetime | None

    @classmethod
    def from_domain(cls, user: User) -> "UserDetail":
        return cls(
            id=user.id,
            username=user.username,
            display_name=user.display_name,
            role=user.role,
            disabled=user.disabled,
            created_at=user.timestamps.created_at,
            updated_at=user.timestamps.updated_at,
            last_login_at=user.last_login_at,
        )


class SessionDetail(BaseModel):
    # When the caller's credential stops working. Always set for a login
    # session; null for a personal access token without expiry.
    expires_at: datetime | None
    user: UserDetail

    @classmethod
    def from_domain(cls, identity: Identity) -> "SessionDetail":
        return cls(
            expires_at=identity.expires_at,
            user=UserDetail.from_domain(identity.user),
        )


class LoginResponse(SessionDetail):
    # Null for a cookie login: the token never reaches page scripts.
    token: str | None


class AccessTokenDetail(BaseModel):
    """A personal access token's metadata; the value is never returned
    again after creation."""

    id: str
    name: str
    # The token's first characters, to tell tokens apart.
    hint: str
    created_at: datetime
    expires_at: datetime | None  # null: valid until revoked
    last_used_at: datetime | None  # recorded at most hourly

    @classmethod
    def from_domain(cls, token: AccessToken) -> "AccessTokenDetail":
        return cls(
            id=token.id,
            name=token.name,
            hint=token.hint,
            created_at=token.created_at,
            expires_at=token.expires_at,
            last_used_at=token.last_used_at,
        )


class AccessTokenList(BaseModel):
    # Unpaginated: at most MAX_ACCESS_TOKENS per user.
    items: list[AccessTokenDetail]


class CreateAccessTokenRequest(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=MAX_TOKEN_NAME_LENGTH)]
    # The caller's account password: a stolen session alone must not mint
    # long-lived credentials.
    password: Password
    # Days until it expires; null: valid until revoked.
    expires_in_days: Annotated[int | None, Field(ge=1, le=3650)] = 90


class CreatedAccessToken(AccessTokenDetail):
    # Shown once: store it now, it cannot be retrieved later.
    token: str


class SetupStatus(BaseModel):
    required: bool


class ChangePasswordRequest(BaseModel):
    current_password: Password
    new_password: Password


DisplayName = Annotated[str, Field(max_length=MAX_DISPLAY_NAME_LENGTH)]


class CreateUserRequest(LoginRequest):
    role: Role = Role.MEMBER
    display_name: DisplayName | None = None


class UpdateUserRequest(BaseModel):
    """Omitted fields stay unchanged. ``display_name`` "" clears it;
    ``password`` resets it (revoking the user's sessions and tokens). The
    username cannot be changed. An admin cannot disable, demote or reset
    the password of their own account (409 OWN_ACCOUNT), and the last
    active admin cannot be disabled or demoted (409 LAST_ADMIN)."""

    display_name: DisplayName | None = None
    role: Role | None = None
    disabled: bool | None = None
    password: Password | None = None


class UserStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class UserPage(BaseModel):
    items: list[UserDetail]
    page: PageInfo

    @classmethod
    def from_page(cls, page: Page[User]) -> "UserPage":
        return cls(
            items=[UserDetail.from_domain(u) for u in page.items],
            page=PageInfo(offset=page.offset, limit=page.limit, has_more=page.has_more),
        )


def _cookie_secure(request: Request) -> bool:
    secure: bool = request.app.state.container.settings.session_cookie_secure
    return secure


def _errors(*codes: int) -> dict[int | str, dict[str, Any]]:
    return {code: {"model": ErrorResponse} for code in codes}


# -- routers ------------------------------------------------------------------


def create_auth_router() -> APIRouter:
    """/auth: setup and login are public; the rest need a session."""
    router = APIRouter(prefix="/auth", tags=["auth"])

    @router.get("/setup")
    async def setup_status(auth: Auth) -> SetupStatus:
        """Whether the first admin still has to be created (a fresh
        installation); the UI then offers setup instead of sign-in."""
        return SetupStatus(required=await auth.setup_required())

    @router.post(
        "/setup", status_code=status.HTTP_201_CREATED, responses=_errors(409, 422)
    )
    async def setup(body: LoginRequest, auth: Auth) -> UserDetail:
        """Create the first admin; refused once any account exists."""
        user = await auth.setup(body.username, body.password.get_secret_value())
        return UserDetail.from_domain(user)

    @router.post("/login", responses=_errors(401, 403, 422, 429))
    async def login(
        body: SessionLoginRequest, request: Request, response: Response, auth: Auth
    ) -> LoginResponse:
        if body.cookie and CSRF_HEADER.lower() not in request.headers:
            # Else a cross-site form could log the victim into the
            # attacker's account (login CSRF).
            raise CsrfHeaderRequiredError(f"A cookie login needs {CSRF_HEADER}.")
        result = await auth.login(body.username, body.password.get_secret_value())
        if body.cookie:
            response.set_cookie(
                SESSION_COOKIE,
                result.token,
                expires=result.session.expires_at,
                path=SESSION_COOKIE_PATH,
                secure=_cookie_secure(request),
                httponly=True,
                samesite="strict",
            )
        return LoginResponse(
            token=None if body.cookie else result.token,
            expires_at=result.session.expires_at,
            user=UserDetail.from_domain(result.user),
        )

    @router.post(
        "/logout", status_code=status.HTTP_204_NO_CONTENT, responses=_errors(401)
    )
    async def logout(
        request: Request, identity: CurrentIdentity, auth: Auth
    ) -> Response:
        await auth.logout(identity)
        response = Response(status_code=status.HTTP_204_NO_CONTENT)
        response.delete_cookie(
            SESSION_COOKIE,
            path=SESSION_COOKIE_PATH,
            secure=_cookie_secure(request),
            httponly=True,
            samesite="strict",
        )
        return response

    @router.get("/me", responses=_errors(401))
    async def me(identity: CurrentIdentity) -> UserDetail:
        return UserDetail.from_domain(identity.user)

    @router.get("/session", responses=_errors(401))
    async def session(identity: CurrentIdentity) -> SessionDetail:
        """The caller's session: who, and until when (absolute expiry)."""
        return SessionDetail.from_domain(identity)

    @router.get("/tokens", responses=_errors(401))
    async def list_access_tokens(
        identity: CurrentIdentity, auth: Auth
    ) -> AccessTokenList:
        """The caller's personal access tokens (metadata only)."""
        tokens = await auth.list_access_tokens(identity)
        return AccessTokenList(items=[AccessTokenDetail.from_domain(t) for t in tokens])

    @router.post(
        "/tokens",
        status_code=status.HTTP_201_CREATED,
        responses=_errors(401, 409, 422, 429),
    )
    async def create_access_token(
        body: CreateAccessTokenRequest, identity: CurrentIdentity, auth: Auth
    ) -> CreatedAccessToken:
        """Issue a personal access token for scripts and AI agents (bearer
        token; acts with the caller's role). Needs the account password.
        The token value is in this response only."""
        days = body.expires_in_days
        value, token = await auth.create_access_token(
            identity,
            name=body.name,
            password=body.password.get_secret_value(),
            expires_in=None if days is None else timedelta(days=days),
        )
        detail = AccessTokenDetail.from_domain(token)
        return CreatedAccessToken(**detail.model_dump(), token=value)

    @router.delete(
        "/tokens/{token_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        responses=_errors(401, 404, 422),
    )
    async def revoke_access_token(
        token_id: str, identity: CurrentIdentity, auth: Auth
    ) -> Response:
        """Revoke one of the caller's tokens; it stops working at once."""
        await auth.revoke_access_token(identity, token_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.post(
        "/password",
        status_code=status.HTTP_204_NO_CONTENT,
        responses=_errors(401, 422, 429),
    )
    async def change_password(
        body: ChangePasswordRequest, identity: CurrentIdentity, auth: Auth
    ) -> Response:
        """Change one's own password; other sessions are revoked."""
        await auth.change_password(
            identity,
            body.current_password.get_secret_value(),
            body.new_password.get_secret_value(),
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return router


Users = Annotated[UserService, Depends(_users)]


def create_users_router() -> APIRouter:
    """/users: account management. Mounted behind ``require_admin``."""
    router = APIRouter(
        prefix="/users", tags=["users"], responses=_errors(401, 403, 404, 409, 422)
    )

    @router.post("", status_code=status.HTTP_201_CREATED)
    async def create_user(
        body: CreateUserRequest, identity: CurrentIdentity, users: Users
    ) -> UserDetail:
        user = await users.create_user(
            identity,
            body.username,
            body.password.get_secret_value(),
            role=body.role,
            display_name=body.display_name,
        )
        return UserDetail.from_domain(user)

    @router.get("")
    async def list_users(
        identity: CurrentIdentity,
        users: Users,
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 50,
        q: Annotated[
            str | None,
            Query(max_length=64, description="Username or display name contains"),
        ] = None,
        role: Role | None = None,
        status: UserStatus | None = None,
    ) -> UserPage:
        """Ordered by username."""
        page = await users.list_users(
            identity,
            offset=offset,
            limit=limit,
            search=q,
            role=role,
            disabled=None if status is None else status is UserStatus.DISABLED,
        )
        return UserPage.from_page(page)

    @router.get("/{user_id}")
    async def get_user(
        user_id: str, identity: CurrentIdentity, users: Users
    ) -> UserDetail:
        return UserDetail.from_domain(await users.get_user(identity, user_id))

    @router.patch("/{user_id}")
    async def update_user(
        user_id: str, body: UpdateUserRequest, identity: CurrentIdentity, users: Users
    ) -> UserDetail:
        user = await users.update_user(
            identity,
            user_id,
            display_name=body.display_name,
            role=body.role,
            disabled=body.disabled,
            password=None
            if body.password is None
            else body.password.get_secret_value(),
        )
        return UserDetail.from_domain(user)

    return router
