"""Authentication endpoints and the request guards every protected route uses.

Clients send ``Authorization: Bearer <token>`` (from ``POST /auth/login``).
No cookies, so there is no ambient credential a cross-site request could
ride on (no CSRF surface).

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
from akasha.errors import ConfigurationError

_MAX_TOKEN_LENGTH = 256

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


def bearer_token(request: Request) -> str | None:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token or len(token) > _MAX_TOKEN_LENGTH:
        return None
    return token


Auth = Annotated[AuthenticationService, Depends(auth_service)]


async def current_identity(request: Request, auth: Auth) -> Identity:
    token = bearer_token(request)
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


class LoginResponse(BaseModel):
    token: str
    expires_at: datetime
    user: UserDetail


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


def _errors(*codes: int) -> dict[int | str, dict[str, Any]]:
    return {code: {"model": ErrorResponse} for code in codes}


# -- routers ------------------------------------------------------------------


def create_auth_router() -> APIRouter:
    """/auth: setup and login are public; the rest need a session."""
    router = APIRouter(prefix="/auth", tags=["auth"])

    @router.post(
        "/setup", status_code=status.HTTP_201_CREATED, responses=_errors(409, 422)
    )
    async def setup(body: LoginRequest, auth: Auth) -> UserDetail:
        """Create the first admin; refused once any account exists."""
        user = await auth.setup(body.username, body.password.get_secret_value())
        return UserDetail.from_domain(user)

    @router.post("/login", responses=_errors(401, 422, 429))
    async def login(body: LoginRequest, auth: Auth) -> LoginResponse:
        result = await auth.login(body.username, body.password.get_secret_value())
        return LoginResponse(
            token=result.token,
            expires_at=result.session.expires_at,
            user=UserDetail.from_domain(result.user),
        )

    @router.post(
        "/logout", status_code=status.HTTP_204_NO_CONTENT, responses=_errors(401)
    )
    async def logout(identity: CurrentIdentity, auth: Auth) -> Response:
        await auth.logout(identity)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.get("/me", responses=_errors(401))
    async def me(identity: CurrentIdentity) -> UserDetail:
        return UserDetail.from_domain(identity.user)

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
