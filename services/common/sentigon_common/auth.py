"""Authentication + RBAC: JWT issue/verify, password hashing, and role checks.

Users authenticate at /auth/login for a bearer JWT. Internal service-to-service
calls use a shared service token (X-Service-Token). Roles: viewer < operator <
investigator < admin. Writes require operator+; admin passes any check.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import httpx
from fastapi import Depends, HTTPException
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
