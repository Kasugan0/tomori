"""Regression coverage for the 0003 expected-table guard on an isolated container."""

from __future__ import annotations

import json
import os
import re
import subprocess

import psycopg
import pytest
from sqlalchemy.engine import make_url


@pytest.fixture(scope="session", autouse=True)
def serialize_integration_pg_session() -> None:
    """This module uses its own container, not the shared integration database."""


def isolated_target() -> str:
    raw = os.environ.get("MIGRATION_GUARD_DSN")
    container_id = os.environ.get("MIGRATION_GUARD_CONTAINER_ID", "")
    token = os.environ.get("MIGRATION_GUARD_TOKEN", "")
    if not raw:
        pytest.skip("MIGRATION_GUARD_DSN explicitly enables isolated PostgreSQL tests")
    if not re.fullmatch(r"[0-9a-f]{32}", token) or not re.fullmatch(r"[0-9a-f]{64}", container_id):
        pytest.fail("isolated container ID and token are required")
    url = make_url(raw)
    name = f"migration_guard_{token}"
    if (
        url.drivername != "postgresql+psycopg"
        or url.host != "127.0.0.1"
        or not url.port
        or url.port == 25432
        or url.database != name
        or url.username != name
        or url.password != token
        or url.query
    ):
        pytest.fail("invalid isolated migration guard DSN")
    result = subprocess.run(
        ["docker", "inspect", container_id], capture_output=True, text=True, check=True
    )
    container = json.loads(result.stdout)[0]
    bindings = container["NetworkSettings"]["Ports"].get("5432/tcp")
    mounts = container["Mounts"]
    if (
        container["Id"] != container_id
        or container["Config"]["Labels"].get("migration-guard-token") != token
        or bindings != [{"HostIp": "127.0.0.1", "HostPort": str(url.port)}]
        or len(mounts) != 1
        or mounts[0]["Name"] != name
        or mounts[0]["Destination"] != "/var/lib/postgresql/data"
        or not container["State"]["Running"]
    ):
        pytest.fail("container identity, volume, or port mismatch")
    with psycopg.connect(raw.replace("postgresql+psycopg://", "postgresql://")) as conn:
        assert conn.execute("SELECT current_database(), current_user").fetchone() == (name, name)
    return raw


def migrate(revision: str) -> subprocess.CompletedProcess[str]:
    dsn = isolated_target()
    env = os.environ.copy()
    env["DATABASE_URL"] = dsn
    return subprocess.run(
        ["uv", "run", "alembic", "-c", "alembic.ini", "upgrade", revision],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def execute(sql: str) -> None:
    dsn = isolated_target().replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(dsn) as conn:
        conn.execute(sql)


def test_0003_allows_extra_table_but_rejects_missing_expected_table() -> None:
    initial = migrate("0002_partial_unique_indexes")
    assert initial.returncode == 0, initial.stderr
    execute("CREATE TABLE unrelated_extra_table (id integer)")
    upgrade = migrate("0003_circular_fk_scheme_b")
    assert upgrade.returncode == 0, upgrade.stderr
    dsn = isolated_target().replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "0003_circular_fk_scheme_b",
        )
        extra_table = conn.execute("SELECT to_regclass('unrelated_extra_table')").fetchone()
        assert extra_table is not None and extra_table[0]
    env = os.environ.copy()
    env["DATABASE_URL"] = isolated_target()
    downgrade = subprocess.run(
        ["uv", "run", "alembic", "-c", "alembic.ini", "downgrade", "0002_partial_unique_indexes"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert downgrade.returncode == 0, downgrade.stderr
    execute("DROP TABLE notification_tasks")
    failure = migrate("0003_circular_fk_scheme_b")
    assert failure.returncode != 0
    assert "Integrity assertion 5 failed: missing tables ['notification_tasks']" in failure.stderr
    with psycopg.connect(
        isolated_target().replace("postgresql+psycopg://", "postgresql://")
    ) as conn:
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "0002_partial_unique_indexes",
        )
