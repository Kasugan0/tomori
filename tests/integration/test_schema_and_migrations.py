"""Real PostgreSQL 17.12 schema, constraint, index, seed, and Alembic parity tests."""

from __future__ import annotations

import os
import subprocess
from collections import Counter

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import ALEMBIC_INI_PATH, REPO_ROOT, validate_test_database_url

EXPECTED_17_BUSINESS_TABLES: frozenset[str] = frozenset(
    {
        "maintenance_regions",
        "roles",
        "lighting_points",
        "accounts",
        "account_roles",
        "scope_authorizations",
        "business_rules",
        "facility_assets",
        "fault_tickets",
        "fault_evidences",
        "dispatch_records",
        "repair_processes",
        "material_usages",
        "rework_orders",
        "acceptance_records",
        "audit_logs",
        "notification_tasks",
    }
)

EXPECTED_CHECK_BY_TABLE: dict[str, int] = {
    "accounts": 4,
    "roles": 1,
    "account_roles": 0,
    "scope_authorizations": 2,
    "business_rules": 2,
    "notification_tasks": 7,
    "audit_logs": 4,
    "maintenance_regions": 2,
    "lighting_points": 6,
    "facility_assets": 4,
    "fault_tickets": 14,
    "fault_evidences": 8,
    "dispatch_records": 7,
    "repair_processes": 4,
    "material_usages": 3,
    "acceptance_records": 3,
    "rework_orders": 5,
}

EXPECTED_PARTIAL_UNIQUE_INDEXES: frozenset[str] = frozenset(
    {
        "uq_scope_authorizations_account_region_type",
        "uq_business_rules_code_active",
        "uq_fault_tickets_active_point_type",
        "uq_dispatch_records_active_ticket",
        "uq_rework_orders_active_ticket",
        "uq_audit_logs_idempotency",
    }
)

EXPECTED_PARTIAL_PLAIN_INDEXES: frozenset[str] = frozenset(
    {
        "ix_maintenance_regions_is_active",
        "ix_accounts_status",
        "ix_scope_authorizations_account_id_active",
        "ix_fault_tickets_pending_candidates",
        "ix_fault_tickets_pending_dispatch",
        "ix_fault_tickets_sla_scan",
        "ix_fault_tickets_manual_review",
        "ix_dispatch_records_technician_active",
        "ix_audit_logs_ticket_id",
        "ix_notification_tasks_pending_due",
        "ix_notification_tasks_recipient_unread",
        "ix_notification_tasks_dead_letter",
    }
)


def test_conftest_rejects_tomori_dev_and_sqlite() -> None:
    """Safety guard must strictly reject `tomori_dev` and any SQLite URL."""
    with pytest.raises(RuntimeError, match="tomori_test"):
        validate_test_database_url("postgresql+psycopg://tomori:pwd@127.0.0.1:25432/tomori_dev")
    with pytest.raises(RuntimeError, match="PostgreSQL"):
        validate_test_database_url("sqlite+aiosqlite:///:memory:")


async def test_connected_to_isolated_tomori_test_pg17(db_session: AsyncSession) -> None:
    """Confirm tests execute against real PostgreSQL 17 on `tomori_test`, never `tomori_dev`."""
    current_db = (await db_session.execute(text("SELECT current_database()"))).scalar_one()
    server_version = (await db_session.execute(text("SHOW server_version"))).scalar_one()
    assert current_db == "tomori_test"
    assert str(server_version).startswith("17.")


async def test_17_business_tables_and_alembic_version(db_session: AsyncSession) -> None:
    """Verify exact 17 business tables plus `alembic_version`."""
    rows = (
        await db_session.execute(
            text(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_type = 'BASE TABLE'
                """
            )
        )
    ).scalars()
    all_tables = set(rows)
    assert "alembic_version" in all_tables
    business_tables = all_tables - {"alembic_version"}
    assert len(business_tables) == 17
    assert business_tables == EXPECTED_17_BUSINESS_TABLES


async def test_76_check_constraints_and_per_table_breakdown(db_session: AsyncSession) -> None:
    """Verify total 76 CHECK constraints and exact per-table counts across 17 tables."""
    rows = (
        await db_session.execute(
            text(
                """
                SELECT c.relname AS table_name, count(*) AS cnt
                FROM pg_constraint con
                JOIN pg_class c ON c.oid = con.conrelid
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public'
                  AND c.relname <> 'alembic_version'
                  AND con.contype = 'c'
                GROUP BY c.relname
                """
            )
        )
    ).all()
    actual_counts = {table: int(cnt) for table, cnt in rows}
    for tbl in EXPECTED_17_BUSINESS_TABLES:
        actual_counts.setdefault(tbl, 0)

    assert sum(actual_counts.values()) == 76
    assert actual_counts == EXPECTED_CHECK_BY_TABLE


async def test_41_foreign_keys_and_confdeltype_distribution(db_session: AsyncSession) -> None:
    """Verify 41 FKs, exact `confdeltype` distribution `{r:24, n:9, c:7, a:1}`, and Scheme B FKs."""
    rows = (
        await db_session.execute(
            text(
                """
                SELECT con.conname, con.confdeltype::text
                FROM pg_constraint con
                JOIN pg_class c ON c.oid = con.conrelid
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public'
                  AND c.relname <> 'alembic_version'
                  AND con.contype = 'f'
                """
            )
        )
    ).all()
    fk_names = {str(name) for name, _ in rows}
    dist = Counter(str(deltype) for _, deltype in rows)

    assert len(rows) == 41
    assert dict(dist) == {"r": 24, "n": 9, "c": 7, "a": 1}

    # Scheme B bidirectional circular FKs + forward delayed FK
    assert {
        "fk_acceptance_records_rework_order_id_rework_orders",
        "fk_rework_orders_source_acceptance_id_acceptance_records",
        "fk_dispatch_records_rework_order_id_rework_orders",
    }.issubset(fk_names)


async def test_22_unique_and_37_plain_indexes(db_session: AsyncSession) -> None:
    """Verify 22 non-PK UNIQUE (16 full + 6 partial) and 37 plain indexes (25 full + 12 partial)."""
    rows = (
        await db_session.execute(
            text(
                """
                SELECT
                    idx_cls.relname AS index_name,
                    idx.indisunique,
                    idx.indisprimary,
                    (idx.indpred IS NOT NULL) AS is_partial
                FROM pg_index idx
                JOIN pg_class tbl ON tbl.oid = idx.indrelid
                JOIN pg_class idx_cls ON idx_cls.oid = idx.indexrelid
                JOIN pg_namespace n ON n.oid = tbl.relnamespace
                WHERE n.nspname = 'public'
                  AND tbl.relname <> 'alembic_version'
                """
            )
        )
    ).all()

    pks = [r[0] for r in rows if r[2]]
    non_pk_uniques = [r for r in rows if r[1] and not r[2]]
    plains = [r for r in rows if not r[1]]

    assert len(pks) == 17
    assert len(non_pk_uniques) == 22
    assert sum(1 for r in non_pk_uniques if not r[3]) == 16
    partial_unique_names = {str(r[0]) for r in non_pk_uniques if r[3]}
    assert partial_unique_names == EXPECTED_PARTIAL_UNIQUE_INDEXES

    assert len(plains) == 37
    assert sum(1 for r in plains if not r[3]) == 25
    partial_plain_names = {str(r[0]) for r in plains if r[3]}
    assert partial_plain_names == EXPECTED_PARTIAL_PLAIN_INDEXES


async def test_54_timestamptz_zero_timestamp_without_tz(db_session: AsyncSession) -> None:
    """MUT-14 defense: all 54 timestamp columns use `timestamptz`, 0 `timestamp without tz`."""
    rows = (
        await db_session.execute(
            text(
                """
                SELECT data_type, count(*)
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name <> 'alembic_version'
                  AND data_type LIKE 'timestamp%'
                GROUP BY data_type
                """
            )
        )
    ).all()
    counts = {str(dt): int(cnt) for dt, cnt in rows}
    assert counts.get("timestamp with time zone", 0) == 54
    assert counts.get("timestamp without time zone", 0) == 0


async def test_seeded_roles_and_business_rules_v1(db_session: AsyncSession) -> None:
    """Verify `0001` roles (3) and `0004_business_rules_seed` active v1 rules (5)."""
    role_codes = set(
        (await db_session.execute(text("SELECT code FROM roles ORDER BY code"))).scalars()
    )
    assert role_codes == {"ADMIN", "INSPECTOR", "TECHNICIAN"}

    rule_rows = (
        await db_session.execute(
            text(
                """
                SELECT rule_code, version, is_active, rule_value
                FROM business_rules
                WHERE is_active = true
                ORDER BY rule_code
                """
            )
        )
    ).all()
    by_code = {str(code): (int(ver), bool(active), val) for code, ver, active, val in rule_rows}
    assert set(by_code) == {
        "ai_candidate_ttl_hours",
        "dispatch_scoring_weights",
        "grading_rules",
        "rework_policy",
        "sla_policy",
    }
    assert all(ver == 1 and active for ver, active, _ in by_code.values())

    sla_policy = by_code["sla_policy"][2]
    assert sla_policy == {
        "statutory_deadline_hours": 48,
        "response_minutes": None,
        "emergency_notice_hours": 24,
        "applies_to_priorities": ["P1", "P2", "P3", "P4"],
    }

    grading_rules = by_code["grading_rules"][2]
    assert [r["rule_id"] for r in grading_rules["rules"]] == [
        "R1_SAFETY_OR_EXPRESS_CRITICAL",
        "R2_EXPRESS_OR_MAIN_CORE_OR_CRITICAL_ASSET",
        "R3_BRANCH_OR_MAIN_MINOR",
    ]
    assert grading_rules["default_priority"] == "P4"
    assert grading_rules["default_rule_id"] == "R4_FALLBACK_OBSERVATION"

    assert by_code["rework_policy"][2] == {
        "max_rework_rounds": 2,
        "on_exceed_action": "FORCE_ADMIN_REVIEW",
    }
    assert by_code["ai_candidate_ttl_hours"][2] == {"ttl_hours": 72}
    weights = by_code["dispatch_scoring_weights"][2]["weights"]
    assert sum(float(v) for v in weights.values()) == pytest.approx(1.0)


def test_alembic_check_zero_model_drift(migrated_test_db: str) -> None:
    """Execute `uv run alembic -c alembic.ini check` on `tomori_test` and assert zero drift."""
    env = os.environ.copy()
    env["DATABASE_URL"] = migrated_test_db
    env.setdefault(
        "JWT_SECRET_KEY",
        "tomori-stage2-isolated-test-jwt-secret-key-not-for-prod",
    )
    proc = subprocess.run(
        ["uv", "run", "alembic", "-c", str(ALEMBIC_INI_PATH), "check"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    combined = f"{proc.stdout}\n{proc.stderr}"
    assert proc.returncode == 0, f"alembic check failed (rc={proc.returncode}):\n{combined}"
    assert "No new upgrade operations detected." in combined


@pytest.mark.real_commit
async def test_mode_b_real_commit_truncates_and_reseeds_preserving_alembic(
    real_commit_session: AsyncSession,
) -> None:
    """Verify Mode B real-commit fixture connects to `tomori_test` and keeps seeds intact."""
    db_name = (await real_commit_session.execute(text("SELECT current_database()"))).scalar_one()
    ver = (
        await real_commit_session.execute(text("SELECT version_num FROM alembic_version"))
    ).scalar_one()
    role_cnt = (await real_commit_session.execute(text("SELECT count(*) FROM roles"))).scalar_one()
    rule_cnt = (
        await real_commit_session.execute(text("SELECT count(*) FROM business_rules"))
    ).scalar_one()

    assert db_name == "tomori_test"
    assert ver == "0004_business_rules_seed"
    assert role_cnt == 3
    assert rule_cnt == 5
