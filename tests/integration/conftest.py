"""Shared ASGI + real PostgreSQL 17 integration test fixtures and helpers."""

from __future__ import annotations

import fcntl
import io
import json
import os
import struct
import uuid
import zlib
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from PIL import Image, ImageDraw
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.conftest import (
    _DEFAULT_ROLES,
    _default_business_rule_seeds,
    _ordered_business_table_names,
)

DEMO_CREDENTIALS: dict[str, tuple[str, str]] = {
    "admin": ("admin", "Tomori#Admin2026"),
    "inspector1": ("inspector1", "Tomori#Inspect2026"),
    "tech1": ("tech1", "Tomori#Tech2026"),
    "tech2": ("tech2", "Tomori#Tech2026"),
}


@pytest.fixture(scope="session", autouse=True)
def serialize_integration_pg_session() -> Iterator[None]:
    """Serialize multi-process integration runs on `tomori_test` to avoid TRUNCATE deadlocks."""
    lock_path = Path("/tmp/tomori-integration-pg.lock")
    with lock_path.open("w", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


async def reset_to_post_migration_baseline(engine: AsyncEngine) -> None:
    """Truncate 17 business tables and restore baseline roles + v1 business rules."""
    table_names = _ordered_business_table_names()
    quoted = ", ".join(f'"{name}"' for name in table_names)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE"))
        for code, name, desc in _DEFAULT_ROLES:
            await conn.execute(
                text(
                    "INSERT INTO roles (code, name, description) "
                    "VALUES (:code, :name, :description)"
                ),
                {"code": code, "name": name, "description": desc},
            )
        for rule_code, rule_value, description in _default_business_rule_seeds():
            await conn.execute(
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


@pytest.fixture
def isolated_upload_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point `UPLOAD_DIR` to a per-test temporary directory."""
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("UPLOAD_DIR", str(upload_dir))
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("YOLO_INFERENCE_ENABLED", "true")
    monkeypatch.setenv("YOLO_INFERENCE_MODE", "auto")
    monkeypatch.setenv("YOLO_WEIGHTS_PATH", str(tmp_path / "missing_weights.pt"))
    from tomori.settings import get_settings

    get_settings.cache_clear()
    return upload_dir


@pytest_asyncio.fixture(scope="function")
async def asgi_app(
    migrated_test_db: str,
    async_engine: AsyncEngine,
    isolated_upload_dir: Path,
) -> AsyncIterator[FastAPI]:
    """Create a real FastAPI app bound to `tomori_test` with full lifespan seeding."""
    _ = (migrated_test_db, isolated_upload_dir)
    from tomori.db import session as db_session_mod
    from tomori.main import create_app
    from tomori.settings import get_settings

    await reset_to_post_migration_baseline(async_engine)
    get_settings.cache_clear()
    db_session_mod.get_engine.cache_clear()
    db_session_mod.get_async_sessionmaker.cache_clear()

    app = create_app()
    try:
        async with LifespanManager(app):
            yield app
    finally:
        await db_session_mod.get_engine().dispose()
        db_session_mod.get_engine.cache_clear()
        db_session_mod.get_async_sessionmaker.cache_clear()
        get_settings.cache_clear()
        await reset_to_post_migration_baseline(async_engine)


@pytest_asyncio.fixture(scope="function")
async def api_client(asgi_app: FastAPI) -> AsyncIterator[AsyncClient]:
    """HTTPX AsyncClient connected directly to the real ASGI app + PostgreSQL 17."""
    async with AsyncClient(
        transport=ASGITransport(app=asgi_app),
        base_url="http://test",
    ) as client:
        yield client


def new_idem_key(prefix: str = "tomori-l2") -> str:
    """Generate a valid Idempotency-Key matching `^[A-Za-z0-9_-]{8,128}$`."""
    return f"{prefix}-{uuid.uuid4().hex[:16]}"


async def login_user(client: AsyncClient, account_key: str) -> dict[str, Any]:
    """Login using seeded demo credentials and return the full login response dict."""
    username, secret = DEMO_CREDENTIALS[account_key]
    resp = await client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": secret},
    )
    assert resp.status_code == 200, f"login failed for {username}: {resp.text}"
    data: dict[str, Any] = resp.json()
    return data


async def auth_headers_for(client: AsyncClient, account_key: str) -> dict[str, str]:
    """Return Bearer Authorization headers for a seeded demo account."""
    data = await login_user(client, account_key)
    return {"Authorization": f"Bearer {data['access_token']}"}


def make_static_image(
    fmt: str = "JPEG",
    *,
    size: tuple[int, int] = (160, 160),
    bg_color: tuple[int, int, int] = (25, 45, 70),
) -> bytes:
    """Encode a clean single-frame static image in memory."""
    buf = io.BytesIO()
    img = Image.new("RGB", size, bg_color)
    draw = ImageDraw.Draw(img)
    draw.line((0, 0, size[0] - 1, size[1] - 1), fill=(90, 130, 170), width=2)
    img.save(buf, format=fmt)
    return buf.getvalue()


def make_night_normal_lit_png() -> bytes:
    """Night scene with bright white lamp blob -> D-30 NORMAL_LIT (is_fault_candidate=False)."""
    img = Image.new("RGB", (400, 400), (10, 10, 15))
    draw = ImageDraw.Draw(img)
    draw.rectangle((185, 185, 215, 215), fill=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def make_occluded_fault_png() -> bytes:
    """Night scene with medium-green foliage square -> baseline detects streetlight_occluded."""
    img = Image.new("RGB", (400, 400), (10, 10, 15))
    draw = ImageDraw.Draw(img)
    draw.rectangle((180, 180, 220, 220), fill=(0, 140, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def make_animated_bytes(fmt: str) -> bytes:
    """Encode a real 2-frame animated PNG (APNG), WebP, or GIF."""
    buf = io.BytesIO()
    f1 = Image.new("RGB", (16, 16), (255, 0, 0))
    f2 = Image.new("RGB", (16, 16), (0, 255, 0))
    f1.save(buf, format=fmt, save_all=True, append_images=[f2], duration=50, loop=0)
    return buf.getvalue()


def make_minimal_png_with_dimensions(width: int, height: int) -> bytes:
    """Craft a minimal valid PNG whose IHDR claims `width x height` (>40MP guard test)."""

    def _chunk(tag: bytes, payload: bytes) -> bytes:
        crc = zlib.crc32(tag + payload) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + tag + payload + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    idat = zlib.compress(b"\x00\x00")
    return (
        b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", idat) + _chunk(b"IEND", b"")
    )


def clear_settings_cache() -> None:
    """Clear `tomori.settings.get_settings` lru_cache after env changes."""
    from tomori.settings import get_settings

    get_settings.cache_clear()
    _ = os.environ.get("APP_ENV")
