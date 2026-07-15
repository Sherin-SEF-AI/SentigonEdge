"""Async + sync engines and session factories.

Async is the runtime path (FastAPI, consumers). Sync is used by Alembic and the
seed CLI, which are simpler to run without an event loop.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, sessionmaker

from ..config import settings

_async_engine: AsyncEngine = create_async_engine(
    settings.database_url, pool_pre_ping=True, future=True
)
async_session_factory = async_sessionmaker(
    _async_engine, expire_on_commit=False, class_=AsyncSession
)

_sync_engine: Engine = create_engine(settings.database_url_sync, pool_pre_ping=True, future=True)
sync_session_factory = sessionmaker(_sync_engine, expire_on_commit=False, class_=Session)


def get_async_engine() -> AsyncEngine:
    return _async_engine


def get_sync_engine() -> Engine:
    return _sync_engine


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: one session per request."""
    async with async_session_factory() as session:
        yield session
