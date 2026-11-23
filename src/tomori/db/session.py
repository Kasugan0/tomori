from __future__ import annotations

from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from tomori.settings import get_settings


@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    """获取进程级 AsyncEngine（postgresql+psycopg:// 自动走异步驱动）。"""
    settings = get_settings()
    return create_async_engine(
        settings.database_url,
        echo=False,
        pool_pre_ping=True,
    )


@lru_cache(maxsize=1)
def get_async_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """获取 async_sessionmaker（必须设置 expire_on_commit=False 防序列化 MissingGreenlet）。"""
    return async_sessionmaker(
        bind=get_engine(),
        class_=AsyncSession,
        expire_on_commit=False,
    )


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI 请求级 AsyncSession 依赖（不跨并发任务共享）。"""
    session_factory = get_async_sessionmaker()
    async with session_factory() as session:
        yield session
