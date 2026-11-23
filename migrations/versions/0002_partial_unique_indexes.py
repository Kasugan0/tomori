"""Create 6 partial unique indexes and 12 partial non-unique indexes.

Revision ID: 0002_partial_unique_indexes
Revises: 0001_initial_schema
Create Date: 2026-11-24

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_partial_unique_indexes"
down_revision: str | Sequence[str] | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create all 6 partial unique indexes and 12 partial non-unique indexes."""
    # ── 6 Partial Unique Indexes (2 config uniqueness + 4 business deduplication) ──
    op.create_index(
        "uq_scope_authorizations_account_region_type",
        "scope_authorizations",
        ["account_id", "region_id", "scope_type"],
        unique=True,
        postgresql_where=sa.text("is_active = true"),
    )
    op.create_index(
        "uq_business_rules_code_active",
        "business_rules",
        ["rule_code"],
        unique=True,
        postgresql_where=sa.text("is_active = true"),
    )
    op.create_index(
        "uq_fault_tickets_active_point_type",
        "fault_tickets",
        ["lighting_point_id", "fault_type"],
        unique=True,
        postgresql_where=sa.text("status NOT IN ('CLOSED', 'CANCELLED')"),
    )
    op.create_index(
        "uq_dispatch_records_active_ticket",
        "dispatch_records",
        ["ticket_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('PENDING_ACCEPT', 'ACCEPTED')"),
    )
    op.create_index(
        "uq_rework_orders_active_ticket",
        "rework_orders",
        ["ticket_id"],
        unique=True,
        postgresql_where=sa.text("status NOT IN ('PASSED', 'CLOSED_BY_REVIEW', 'CANCELLED')"),
    )
    op.create_index(
        "uq_audit_logs_idempotency",
        "audit_logs",
        ["actor_account_id", "method", "path", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text(
            "idempotency_key IS NOT NULL AND response_status >= 200 AND response_status < 300"
        ),
    )

    # ── 12 Partial Non-Unique Indexes ──
    op.create_index(
        "ix_maintenance_regions_is_active",
        "maintenance_regions",
        ["is_active"],
        unique=False,
        postgresql_where=sa.text("is_active = true"),
    )
    op.create_index(
        "ix_accounts_status",
        "accounts",
        ["status"],
        unique=False,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )
    op.create_index(
        "ix_scope_authorizations_account_id_active",
        "scope_authorizations",
        ["account_id"],
        unique=False,
        postgresql_where=sa.text("is_active = true"),
    )
    op.create_index(
        "ix_fault_tickets_pending_candidates",
        "fault_tickets",
        ["region_id", sa.text("ai_max_confidence DESC")],
        unique=False,
        postgresql_where=sa.text("status = 'AI_CANDIDATE_PENDING'"),
    )
    op.create_index(
        "ix_fault_tickets_pending_dispatch",
        "fault_tickets",
        ["region_id", "priority", "created_at"],
        unique=False,
        postgresql_where=sa.text("status IN ('PENDING_DISPATCH', 'REWORK_PENDING_DISPATCH')"),
    )
    op.create_index(
        "ix_fault_tickets_sla_scan",
        "fault_tickets",
        ["is_sla_overdue", "response_deadline_at", "repair_deadline_at"],
        unique=False,
        postgresql_where=sa.text("status NOT IN ('CLOSED', 'CANCELLED', 'AI_CANDIDATE_PENDING')"),
    )
    op.create_index(
        "ix_fault_tickets_manual_review",
        "fault_tickets",
        ["region_id", "updated_at"],
        unique=False,
        postgresql_where=sa.text("status = 'MANUAL_REVIEW'"),
    )
    op.create_index(
        "ix_dispatch_records_technician_active",
        "dispatch_records",
        ["technician_account_id", "status", "accept_deadline_at"],
        unique=False,
        postgresql_where=sa.text("status IN ('PENDING_ACCEPT', 'ACCEPTED')"),
    )
    op.create_index(
        "ix_audit_logs_ticket_id",
        "audit_logs",
        ["ticket_id", "created_at"],
        unique=False,
        postgresql_where=sa.text("ticket_id IS NOT NULL"),
    )
    op.create_index(
        "ix_notification_tasks_pending_due",
        "notification_tasks",
        ["next_retry_at", "created_at"],
        unique=False,
        postgresql_where=sa.text("status IN ('PENDING', 'FAILED')"),
    )
    op.create_index(
        "ix_notification_tasks_recipient_unread",
        "notification_tasks",
        ["recipient_account_id", sa.text("created_at DESC")],
        unique=False,
        postgresql_where=sa.text("is_read = false"),
    )
    op.create_index(
        "ix_notification_tasks_dead_letter",
        "notification_tasks",
        [sa.text("created_at DESC")],
        unique=False,
        postgresql_where=sa.text("status = 'DEAD_LETTER'"),
    )


def downgrade() -> None:
    """Drop all 12 partial non-unique indexes and 6 partial unique indexes."""
    # Drop 12 partial non-unique indexes in reverse order
    op.drop_index("ix_notification_tasks_dead_letter", table_name="notification_tasks")
    op.drop_index("ix_notification_tasks_recipient_unread", table_name="notification_tasks")
    op.drop_index("ix_notification_tasks_pending_due", table_name="notification_tasks")
    op.drop_index("ix_audit_logs_ticket_id", table_name="audit_logs")
    op.drop_index("ix_dispatch_records_technician_active", table_name="dispatch_records")
    op.drop_index("ix_fault_tickets_manual_review", table_name="fault_tickets")
    op.drop_index("ix_fault_tickets_sla_scan", table_name="fault_tickets")
    op.drop_index("ix_fault_tickets_pending_dispatch", table_name="fault_tickets")
    op.drop_index("ix_fault_tickets_pending_candidates", table_name="fault_tickets")
    op.drop_index("ix_scope_authorizations_account_id_active", table_name="scope_authorizations")
    op.drop_index("ix_accounts_status", table_name="accounts")
    op.drop_index("ix_maintenance_regions_is_active", table_name="maintenance_regions")

    # Drop 6 partial unique indexes in reverse order
    op.drop_index("uq_audit_logs_idempotency", table_name="audit_logs")
    op.drop_index("uq_rework_orders_active_ticket", table_name="rework_orders")
    op.drop_index("uq_dispatch_records_active_ticket", table_name="dispatch_records")
    op.drop_index("uq_fault_tickets_active_point_type", table_name="fault_tickets")
    op.drop_index("uq_business_rules_code_active", table_name="business_rules")
    op.drop_index("uq_scope_authorizations_account_region_type", table_name="scope_authorizations")
