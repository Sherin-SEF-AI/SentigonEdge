"""Authentication + RBAC: JWT issue/verify, password hashing, and role checks.

Users authenticate at /auth/login for a bearer JWT. Internal service-to-service
calls use a shared service token (X-Service-Token). Roles: viewer < operator <
investigator < admin. Writes require operator+; admin passes any check.
"""
from __future__ import annotations

import hmac
import uuid
from datetime import UTC, datetime, timedelta

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select

from .config import settings
from .db import async_session_factory
from .db.models import User
from .schemas.enums import UserRole

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
_bearer = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)
_jwks_cache: dict = {}


async def _verify_oidc(token: str) -> dict | None:
    """Verify a bearer token against the configured OIDC issuer (RS256 via JWKS)."""
    if not settings.oidc_issuer:
        return None
    try:
        jwks = _jwks_cache.get("keys")
        if jwks is None:
            async with httpx.AsyncClient(timeout=5.0) as c:
                conf = (await c.get(f"{settings.oidc_issuer}/.well-known/openid-configuration")).json()
                jwks = (await c.get(conf["jwks_uri"])).json()
            _jwks_cache["keys"] = jwks
        return jwt.decode(
            token, jwks, algorithms=["RS256"], options={"verify_aud": False}, issuer=settings.oidc_issuer
        )
    except Exception:  # noqa: BLE001
        return None


def _role_from_oidc(claims: dict) -> UserRole:
    roles = (claims.get("realm_access") or {}).get("roles", [])
    for name in ("admin", "investigator", "operator"):
        if name in roles:
            return UserRole(name)
    return UserRole.VIEWER


async def _user_from_oidc(claims: dict) -> User | None:
    email = claims.get("email") or claims.get("preferred_username")
    if not email:
        return None
    async with async_session_factory() as session:
        user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
        if user is None:
            # JIT provisioning: first SSO login creates the Sentigon user
            user = User(
                email=email,
                full_name=claims.get("name", email),
                hashed_password="!oidc-no-local-password",
                role=_role_from_oidc(claims),
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
    return user if user.is_active else None

WRITER_ROLES = {UserRole.OPERATOR, UserRole.INVESTIGATOR, UserRole.ADMIN}


def secure_compare(provided: str | None, expected: str | None) -> bool:
    """Constant-time secret/token comparison (avoids timing side-channels).

    Use for the shared internal X-Service-Token check in every service instead of
    a plain ``==``. Returns False if either side is empty."""
    if not provided or not expected:
        return False
    return hmac.compare_digest(provided, expected)


def hash_password(plain: str) -> str:
    return _pwd.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _pwd.verify(plain, hashed)
    except Exception:  # noqa: BLE001
        return False


def create_access_token(subject: str, role: str) -> str:
    expires = datetime.now(UTC) + timedelta(minutes=settings.jwt_access_token_expire_minutes)
    return jwt.encode(
        {"sub": subject, "role": role, "exp": expires},
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )


async def user_from_token(token: str | None) -> User | None:
    if not token:
        return None
    # 1. local HS256 token (password login)
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        sub = payload.get("sub")
        if sub:
            async with async_session_factory() as session:
                user = await session.get(User, uuid.UUID(sub))
            if user and user.is_active:
                return user
    except (JWTError, ValueError):
        pass
    # 2. SSO: a token issued by the OIDC provider (Keycloak). JIT-provision.
    claims = await _verify_oidc(token)
    if claims:
        return await _user_from_oidc(claims)
    return None


async def get_current_user(token: str | None = Depends(_bearer)) -> User:
    user = await user_from_token(token)
    if user is None:
        raise HTTPException(401, "not authenticated")
    return user


def require_role(*roles: UserRole):
    async def dependency(user: User = Depends(get_current_user)) -> User:
        if user.role != UserRole.ADMIN and user.role not in roles:
            raise HTTPException(403, f"requires role {[r.value for r in roles]}")
        return user

    return dependency


def is_writer(user: User) -> bool:
    return user.role in WRITER_ROLES


_DEFAULT_PUBLIC_PATHS = {"/healthz", "/readyz", "/metrics", "/docs", "/openapi.json"}
_WRITE_METHODS = {"POST", "PATCH", "PUT", "DELETE"}


def _bearer_from(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    return auth[7:].strip() if auth.lower().startswith("bearer ") else None


def cors_headers_for(request: Request) -> dict[str, str]:
    """CORS headers to attach to an auth error response. The auth middleware runs
    OUTSIDE CORSMiddleware, so its 401/403 short-circuits never get CORS headers —
    the browser then blocks the response and JS can't even read the 401 to prompt a
    login. Echo the allowed Origin here so the status is readable cross-origin."""
    origin = request.headers.get("origin")
    if origin and origin in settings.cors_origin_list:
        return {
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Credentials": "true",
            "Vary": "Origin",
        }
    return {}


def install_auth_middleware(
    app: FastAPI,
    *,
    protect_reads: bool = True,
    public_paths: set[str] | None = None,
) -> None:
    """Attach a uniform auth gate to a service app so no service is wide open.

    Writes always require operator+ (or the internal X-Service-Token); reads
    require viewer+ when ``protect_reads`` is True (leave False for live-video
    services whose reads the browser hits directly without a token). Health,
    metrics, docs and any ``public_paths`` stay open. Resolves identity once and
    stashes it on ``request.state.user`` / ``request.state.service``. Constant-time
    token compare. CORS preflight (OPTIONS) is always allowed."""
    public = _DEFAULT_PUBLIC_PATHS | (public_paths or set())

    @app.middleware("http")
    async def _auth(request: Request, call_next):  # noqa: ANN001, ANN202
        request.state.user = None
        request.state.service = False
        path = request.url.path
        if request.method == "OPTIONS" or path in public or path.startswith("/health"):
            return await call_next(request)

        is_write = request.method in _WRITE_METHODS
        service_ok = secure_compare(request.headers.get("x-service-token"), settings.service_token)
        if not service_ok and not is_write and not protect_reads:
            return await call_next(request)  # deliberately-open read

        user = None if service_ok else await user_from_token(_bearer_from(request))
        request.state.user = user
        request.state.service = service_ok
        if not service_ok:
            if user is None:
                return JSONResponse(
                    {"detail": "authentication required"},
                    status_code=401,
                    headers=cors_headers_for(request),
                )
            if is_write and not is_writer(user):
                return JSONResponse(
                    {"detail": "insufficient role (operator+ required)"},
                    status_code=403,
                    headers=cors_headers_for(request),
                )
        return await call_next(request)
