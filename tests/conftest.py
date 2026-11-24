"""Stage 2 test suite configuration and real PostgreSQL 17 isolation fixtures.

Strict rules:
- Never fall back to SQLite; always connect to isolated `tomori_test` @ 127.0.0.1:25432.
- Never touch `tomori_dev`.
- Never include `alembic_version` in business table TRUNCATE lists.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

DEFAULT_TEST_DATABASE_URL = (
    "postgresql+psycopg://tomori:tomori_dev_password@127.0.0.1:25432/tomori_test"
)
REPO_ROOT = Path(__file__).resolve().parent.parent
ALEMBIC_INI_PATH = REPO_ROOT / "alembic.ini"

_DEFAULT_ROLES: tuple[tuple[str, str, str], ...] = (
    (
        "ADMIN",
        "运维管理员",
        "区域/点位/资产/规则配置、分级派工、超时改派、返修强制复核、审计与通知",
    ),
    (
        "INSPECTOR",
        "巡查员",
        "点位巡查、静态图片上传与 AI 候选确认、人工报障、误报撤销、维修后验收",
    ),
    (
        "TECHNICIAN",
        "维修人员",
        "接单/拒单、到场、电气安全检查、维修与耗材登记、完成提交",
    ),
)


def validate_test_database_url(raw_url: str) -> str:
    """Ensure the test database URL targets `tomori_test` and never `tomori_dev`."""
    parsed = urlsplit(raw_url)
    if not parsed.scheme.startswith("postgresql"):
        raise RuntimeError(f"禁止使用非 PostgreSQL 测试库（严禁 SQLite 替代）: {parsed.scheme!r}")

    sa_url = make_url(raw_url)
    db_name = sa_url.database or parsed.path.lstrip("/")
    if db_name != "tomori_test" or db_name == "tomori_dev":
        raise RuntimeError(f"测试库防误连拦截：期望数据库名为 'tomori_test'，实际为 {db_name!r}")
    return raw_url


def resolve_test_database_url() -> str:
    """Resolve and validate the isolated `tomori_test` connection URL."""
    raw = os.environ.get("TEST_DATABASE_URL") or DEFAULT_TEST_DATABASE_URL
    return validate_test_database_url(raw)


def _configure_test_env(test_db_url: str) -> None:
    """Pin process environment to `tomori_test` and ensure test JWT secret exists."""
    os.environ["APP_ENV"] = "test"
    os.environ["TEST_DATABASE_URL"] = test_db_url
    os.environ["DATABASE_URL"] = test_db_url
    os.environ.setdefault(
        "JWT_SECRET_KEY", "tomori-stage2-isolated-test-jwt-secret-key-not-for-prod"
    )

    from tomori.db import session as db_session_mod
    from tomori.settings import get_settings

    get_settings.cache_clear()
    db_session_mod.get_engine.cache_clear()
    db_session_mod.get_async_sessionmaker.cache_clear()


# Pin test env during pytest import/collection so module-level `tomori.main` imports
# never touch `tomori_dev` or fail on missing `.env` JWT_SECRET_KEY.
_configure_test_env(resolve_test_database_url())


@pytest.fixture(scope="session")
def test_database_url() -> str:
    """Validated `tomori_test` DSN for the test session."""
    url = resolve_test_database_url()
    _configure_test_env(url)
    return url


@pytest.fixture(scope="session")
def migrated_test_db(test_database_url: str) -> Iterator[str]:
    """Run Alembic `upgrade head` synchronously against `tomori_test` once per session."""
    if not ALEMBIC_INI_PATH.is_file():
        raise RuntimeError(f"找不到 Alembic 配置文件: {ALEMBIC_INI_PATH}")

    alembic_cfg = Config(str(ALEMBIC_INI_PATH))
    alembic_cfg.set_main_option("sqlalchemy.url", test_database_url.replace("%", "%%"))
    command.upgrade(alembic_cfg, "head")
    yield test_database_url


@pytest_asyncio.fixture(scope="function")
async def async_engine(migrated_test_db: str) -> AsyncIterator[AsyncEngine]:
    """Function-scoped NullPool AsyncEngine aligned with `function` test loop scope."""
    engine = create_async_engine(
        migrated_test_db,
        echo=False,
        poolclass=NullPool,
    )
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def db_session(async_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """Mode A: SQLAlchemy 2.0 external-transaction savepoint rollback isolation."""
    conn = await async_engine.connect()
    outer_trans = await conn.begin()
    session = AsyncSession(
        bind=conn,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    )
    try:
        yield session
    finally:
        await session.close()
        await outer_trans.rollback()
        await conn.close()


def _ordered_business_table_names() -> list[str]:
    """Read the 17 business tables dynamically from SQLAlchemy metadata (no `alembic_version`)."""
    import tomori.models  # noqa: F401
    from tomori.db.base import Base

    names = sorted(name for name in Base.metadata.tables if name != "alembic_version")
    if len(names) != 17:
        raise RuntimeError(f"期望 17 张业务表，实际元数据扫描到 {len(names)} 张: {names}")
    return names


def _default_business_rule_seeds() -> tuple[tuple[str, dict[str, Any], str], ...]:
    """Return baseline v1 business rules consistent with `0004_business_rules_seed`."""
    from importlib import import_module

    seed_mod = import_module("migrations.versions.0004_business_rules_seed")
    seeds: tuple[tuple[str, dict[str, Any], str], ...] = seed_mod._SEED_RULES
    return seeds


@pytest_asyncio.fixture(scope="function")
async def real_commit_session(async_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """Mode B: real-commit AsyncSession with dynamic 17-table TRUNCATE + baseline reseed."""
    session = AsyncSession(bind=async_engine, expire_on_commit=False)
    try:
        yield session
    finally:
        await session.close()
        table_names = _ordered_business_table_names()
        quoted = ", ".join(f'"{name}"' for name in table_names)
        async with async_engine.begin() as cleanup_conn:
            await cleanup_conn.execute(text(f"TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE"))
            for code, name, desc in _DEFAULT_ROLES:
                await cleanup_conn.execute(
                    text(
                        "INSERT INTO roles (code, name, description) "
                        "VALUES (:code, :name, :description)"
                    ),
                    {"code": code, "name": name, "description": desc},
                )
            for rule_code, rule_value, description in _default_business_rule_seeds():
                await cleanup_conn.execute(
                    text(
                        "INSERT INTO business_rules "
                        "(rule_code, version, rule_value, description, is_active) "
                        "VALUES (:rule_code, 1, CAST(:rule_value AS jsonb), :description, true)"
                    ),
                    {
                        "rule_code": rule_code,
                        "rule_value": json.dumps(rule_value, ensure_ascii=False),
                        "description": description,
                    },
                )
