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
- ``authorize_path``: a route whose path names a ``project_id`` or an
  ``execution_id`` needs access to that resource, else 404.
- ``require_admin``: admin-only routers (credentials, users), else 403.
"""

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response, status
from pydantic import BaseModel, Field, SecretStr

from akasha.api.project_schemas import PageInfo
from akasha.api.schemas import ErrorResponse
from akasha.application.auth import (
    AuthenticationRequiredError,
    AuthenticationService,
    AuthorizationService,
    UserService,
)
from akasha.application.auth import (
    require_admin as require_admin_identity,
)
from akasha.application.projects import MAX_PAGE_SIZE, Page
from akasha.domain.identity import MAX_PASSWORD_LENGTH, Identity, Role, User
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


async def authorize_path(request: Request, identity: CurrentIdentity) -> None:
    params = request.path_params
    if "project_id" in params:
        await _authorization(request).require_project(identity, params["project_id"])
    if "execution_id" in params:
        await _authorization(request).require_execution(
            identity, params["execution_id"]
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
    id: str
    username: str
    role: Role
    disabled: bool
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, user: User) -> "UserDetail":
        return cls(
            id=user.id,
            username=user.username,
            role=user.role,
            disabled=user.disabled,
            created_at=user.timestamps.created_at,
            updated_at=user.timestamps.updated_at,
        )


class SessionDetail(BaseModel):
    expires_at: datetime
    user: UserDetail

    @classmethod
    def from_domain(cls, identity: Identity) -> "SessionDetail":
        return cls(
            expires_at=identity.session.expires_at,
            user=UserDetail.from_domain(identity.user),
        )


class LoginResponse(SessionDetail):
    # Null for a cookie login: the token never reaches page scripts.
    token: str | None


class SetupStatus(BaseModel):
    required: bool


class ChangePasswordRequest(BaseModel):
    current_password: Password
    new_password: Password


class CreateUserRequest(LoginRequest):
    role: Role = Role.MEMBER


class UpdateUserRequest(BaseModel):
    """Omitted fields stay unchanged; ``password`` resets it."""

    disabled: bool | None = None
    password: Password | None = None


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
        )
        return UserDetail.from_domain(user)

    @router.get("")
    async def list_users(
        identity: CurrentIdentity,
        users: Users,
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 50,
    ) -> UserPage:
        return UserPage.from_page(
            await users.list_users(identity, offset=offset, limit=limit)
        )

    @router.patch("/{user_id}")
    async def update_user(
        user_id: str, body: UpdateUserRequest, identity: CurrentIdentity, users: Users
    ) -> UserDetail:
        user = await users.update_user(
            identity,
            user_id,
            disabled=body.disabled,
            password=None
            if body.password is None
            else body.password.get_secret_value(),
        )
        return UserDetail.from_domain(user)

    return router
