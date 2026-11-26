"""Add Scheme B delayed foreign keys and verify 5 migration integrity assertions.

Revision ID: 0003_circular_fk_scheme_b
Revises: 0002_partial_unique_indexes
Create Date: 2026-11-24

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003_circular_fk_scheme_b"
down_revision: str | Sequence[str] | None = "0002_partial_unique_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _run_integrity_assertions() -> None:
    """Verify Scheme B circular/forward FKs and 5 DESIGN.md §4.5 integrity assertions."""
    conn = op.get_bind()

    # Verify Scheme B circular FK pair (1 inline + 1 delayed ALTER = 2 rows in pg_constraint)
    circular_fks = set(
        conn.execute(
            sa.text(
                """
                SELECT con.conname
                FROM pg_constraint con
                JOIN pg_class c ON c.oid = con.conrelid
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = current_schema()
                  AND con.contype = 'f'
                  AND con.conname IN (
                    'fk_acceptance_records_rework_order_id_rework_orders',
                    'fk_rework_orders_source_acceptance_id_acceptance_records'
                  )
                """
            )
        )
        .scalars()
        .all()
    )
    expected_circular_fks = {
        "fk_acceptance_records_rework_order_id_rework_orders",
        "fk_rework_orders_source_acceptance_id_acceptance_records",
    }
    if circular_fks != expected_circular_fks:
        raise RuntimeError(
            f"Circular FK verification failed: expected {sorted(expected_circular_fks)}, "
            f"got {sorted(circular_fks)}"
        )

    # Verify forward delayed FK from dispatch_records -> rework_orders (1 row in pg_constraint)
    forward_fks = (
        conn.execute(
            sa.text(
                """
                SELECT con.conname
                FROM pg_constraint con
                JOIN pg_class c ON c.oid = con.conrelid
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = current_schema()
                  AND con.contype = 'f'
                  AND con.conname = 'fk_dispatch_records_rework_order_id_rework_orders'
                """
            )
        )
        .scalars()
        .all()
    )
    if forward_fks != ["fk_dispatch_records_rework_order_id_rework_orders"]:
        raise RuntimeError(
            f"Forward FK verification failed: expected "
            f"['fk_dispatch_records_rework_order_id_rework_orders'], got {forward_fks}"
        )

    # Assertion 1: fault_tickets.region_id matches lighting_points.region_id
    bad_region_rows = conn.execute(
        sa.text(
            """
            SELECT t.id, t.region_id, p.region_id
            FROM fault_tickets t
            JOIN lighting_points p ON p.id = t.lighting_point_id
            WHERE t.region_id <> p.region_id
            """
        )
    ).fetchall()
    if bad_region_rows:
        raise RuntimeError(
            f"Integrity assertion 1 failed: fault_tickets/lighting_points region_id mismatch "
            f"({len(bad_region_rows)} rows)"
        )

    # Assertion 2: dispatch_records technician holds valid REPAIR scope at dispatch time
    bad_dispatch_scope_rows = conn.execute(
        sa.text(
            """
            SELECT d.id
            FROM dispatch_records d
            LEFT JOIN scope_authorizations s
              ON s.id = d.scope_authorization_id
             AND s.account_id = d.technician_account_id
             AND s.region_id = d.region_id
             AND s.scope_type = 'REPAIR'
             AND (s.valid_from IS NULL OR s.valid_from <= d.dispatched_at)
             AND (s.valid_to IS NULL OR s.valid_to >= d.dispatched_at)
            WHERE s.id IS NULL
            """
        )
    ).fetchall()
    if bad_dispatch_scope_rows:
        raise RuntimeError(
            f"Integrity assertion 2 failed: dispatch_records without matching REPAIR scope "
            f"({len(bad_dispatch_scope_rows)} rows)"
        )

    # Assertion 3: at most 1 active dispatch per ticket
    duplicate_active_dispatches = conn.execute(
        sa.text(
            """
            SELECT ticket_id, count(*)
            FROM dispatch_records
            WHERE status IN ('PENDING_ACCEPT', 'ACCEPTED')
            GROUP BY ticket_id
            HAVING count(*) > 1
            """
        )
    ).fetchall()
    if duplicate_active_dispatches:
        raise RuntimeError(
            f"Integrity assertion 3 failed: multiple active dispatches on ticket "
            f"({len(duplicate_active_dispatches)} rows)"
        )

    # Assertion 4: fault_tickets.rework_count == count(rework_orders)
    rework_mismatch_rows = conn.execute(
        sa.text(
            """
            SELECT t.id, t.rework_count, count(r.id) AS actual
            FROM fault_tickets t
            LEFT JOIN rework_orders r ON r.ticket_id = t.id
            GROUP BY t.id, t.rework_count
            HAVING t.rework_count <> count(r.id)
            """
        )
    ).fetchall()
    if rework_mismatch_rows:
        raise RuntimeError(
            f"Integrity assertion 4 failed: fault_tickets.rework_count mismatch "
            f"({len(rework_mismatch_rows)} rows)"
        )

    # 固定 0003 时点的表集合，后续新增表由新迁移负责。
    expected_tables = frozenset(
        {
            "acceptance_records",
            "account_roles",
            "accounts",
            "audit_logs",
            "business_rules",
            "dispatch_records",
            "facility_assets",
            "fault_evidences",
            "fault_tickets",
            "lighting_points",
            "maintenance_regions",
            "material_usages",
            "notification_tasks",
            "repair_processes",
            "rework_orders",
            "roles",
            "scope_authorizations",
        }
    )
    found_tables = set(
        conn.execute(
            sa.text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = current_schema() AND table_type = 'BASE TABLE' "
                "AND table_name IN :names"
            ).bindparams(sa.bindparam("names", expanding=True)),
            {"names": sorted(expected_tables)},
        ).scalars()
    )
    missing_tables = expected_tables - found_tables
    if missing_tables:
        raise RuntimeError(f"Integrity assertion 5 failed: missing tables {sorted(missing_tables)}")


def upgrade() -> None:
    """Create delayed circular and forward FKs, then run integrity assertions."""
    # 1. Scheme B circular reverse FK: rework_orders.source_acceptance_id -> acceptance_records.id
    op.create_foreign_key(
        "fk_rework_orders_source_acceptance_id_acceptance_records",
        "rework_orders",
        "acceptance_records",
        ["source_acceptance_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    # 2. Forward delayed FK: dispatch_records.rework_order_id -> rework_orders.id
    op.create_foreign_key(
        "fk_dispatch_records_rework_order_id_rework_orders",
        "dispatch_records",
        "rework_orders",
        ["rework_order_id"],
        ["id"],
    )

    if not op.get_context().as_sql:
        _run_integrity_assertions()


def downgrade() -> None:
    """Drop delayed forward and circular FKs in reverse order."""
    op.drop_constraint(
        "fk_dispatch_records_rework_order_id_rework_orders",
        "dispatch_records",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_rework_orders_source_acceptance_id_acceptance_records",
        "rework_orders",
        type_="foreignkey",
    )
