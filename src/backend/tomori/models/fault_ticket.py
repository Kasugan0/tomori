from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from tomori.db.base import Base
from tomori.db.types import TZDateTime


class FaultTicket(Base):
    """故障单主表（⑪ fault_tickets）。"""

    __tablename__ = "fault_tickets"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ticket_no: Mapped[str] = mapped_column(String(32), nullable=False)
    region_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("maintenance_regions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    lighting_point_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("lighting_points.id", ondelete="RESTRICT"),
        nullable=False,
    )
    facility_asset_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("facility_assets.id", ondelete="SET NULL"),
        nullable=True,
    )
    report_source: Mapped[str] = mapped_column(String(24), nullable=False)
    fault_type: Mapped[str] = mapped_column(String(24), nullable=False)
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    priority: Mapped[str | None] = mapped_column(String(4), nullable=True)
    grading_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    grading_snapshot_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    reporter_account_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    confirmed_by_inspector: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    confirmed_by_inspector_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("accounts.id", ondelete="RESTRICT"),
        nullable=True,
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    ai_suggestion_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    ai_max_confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    response_deadline_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    repair_deadline_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    is_sla_overdue: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    overdue_stage: Mapped[str | None] = mapped_column(String(16), nullable=True)
    overdue_marked_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    rework_count: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    dispatch_reject_count: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    manual_review_reason: Mapped[str | None] = mapped_column(String(256), nullable=True)
    cancel_reason: Mapped[str | None] = mapped_column(String(256), nullable=True)
    cancelled_by_account_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("accounts.id", ondelete="SET NULL"),
        nullable=True,
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime,
        nullable=False,
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime,
        nullable=False,
        server_default=text("now()"),
        onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("ticket_no", name="uq_fault_tickets_ticket_no"),
        CheckConstraint(
            "report_source IN ('AI_CANDIDATE', 'MANUAL_INSPECT', 'MANUAL_ADMIN')",
            name="report_source_enum",
        ),
        CheckConstraint(
            "fault_type IN "
            "('LAMP_OUT', 'FIXTURE_DAMAGED', 'OCCLUDED', 'ELECTRIC_HAZARD', 'FLICKERING', 'OTHER')",
            name="fault_type_enum",
        ),
        CheckConstraint(
            "status IN ("
            "'AI_CANDIDATE_PENDING', 'PENDING_DISPATCH', 'DISPATCHED', 'IN_REPAIR', "
            "'PENDING_ACCEPTANCE', 'REWORK_PENDING_DISPATCH', 'MANUAL_REVIEW', "
            "'CLOSED', 'CANCELLED'"
            ")",
            name="status_enum",
        ),
        CheckConstraint(
            "priority IS NULL OR priority IN ('P1', 'P2', 'P3', 'P4')",
            name="priority_enum",
        ),
        CheckConstraint(
            "rework_count >= 0 AND dispatch_reject_count >= 0",
            name="counters_non_negative",
        ),
        CheckConstraint(
            "grading_score IS NULL OR (grading_score >= 0 AND grading_score <= 100)",
            name="grading_score_range",
        ),
        CheckConstraint(
            "ai_max_confidence IS NULL OR (ai_max_confidence >= 0 AND ai_max_confidence <= 1)",
            name="ai_max_confidence_range",
        ),
        CheckConstraint(
            "("
            "confirmed_by_inspector = false "
            "AND confirmed_by_inspector_id IS NULL "
            "AND confirmed_at IS NULL "
            "AND status = 'AI_CANDIDATE_PENDING'"
            ") OR ("
            "confirmed_by_inspector = true "
            "AND confirmed_by_inspector_id IS NOT NULL "
            "AND confirmed_at IS NOT NULL "
            "AND status <> 'AI_CANDIDATE_PENDING'"
            ")",
            name="inspector_confirmation_serialized",
        ),
        CheckConstraint(
            "NOT (report_source = 'AI_CANDIDATE' "
            "AND fault_type IN ('FLICKERING', 'ELECTRIC_HAZARD'))",
            name="ai_source_excludes_unsupported_types",
        ),
        CheckConstraint(
            "status <> 'MANUAL_REVIEW' OR manual_review_reason IS NOT NULL",
            name="manual_review_reason_required",
        ),
        CheckConstraint(
            "("
            "status = 'CANCELLED' "
            "AND cancel_reason IS NOT NULL "
            "AND cancelled_by_account_id IS NOT NULL "
            "AND cancelled_at IS NOT NULL"
            ") OR ("
            "status <> 'CANCELLED' "
            "AND cancel_reason IS NULL "
            "AND cancelled_by_account_id IS NULL "
            "AND cancelled_at IS NULL"
            ")",
            name="cancel_fields_consistent",
        ),
        CheckConstraint(
            "(status = 'CLOSED') = (closed_at IS NOT NULL)",
            name="closed_at_consistent",
        ),
        CheckConstraint(
            "("
            "is_sla_overdue = true "
            "AND overdue_stage IS NOT NULL "
            "AND overdue_marked_at IS NOT NULL"
            ") OR ("
            "is_sla_overdue = false "
            "AND overdue_stage IS NULL "
            "AND overdue_marked_at IS NULL"
            ")",
            name="overdue_consistent",
        ),
        CheckConstraint(
            "overdue_stage IS NULL OR overdue_stage IN ('RESPONSE', 'REPAIR')",
            name="overdue_stage_enum",
        ),
        Index(
            "uq_fault_tickets_active_point_type",
            "lighting_point_id",
            "fault_type",
            unique=True,
            postgresql_where=text("status NOT IN ('CLOSED', 'CANCELLED')"),
        ),
        Index(
            "ix_fault_tickets_pending_candidates",
            "region_id",
            ai_max_confidence.desc(),
            postgresql_where=text("status = 'AI_CANDIDATE_PENDING'"),
        ),
        Index(
            "ix_fault_tickets_pending_dispatch",
            "region_id",
            "priority",
            "created_at",
            postgresql_where=text("status IN ('PENDING_DISPATCH', 'REWORK_PENDING_DISPATCH')"),
        ),
        Index(
            "ix_fault_tickets_sla_scan",
            "is_sla_overdue",
            "response_deadline_at",
            "repair_deadline_at",
            postgresql_where=text("status NOT IN ('CLOSED', 'CANCELLED', 'AI_CANDIDATE_PENDING')"),
        ),
        Index(
            "ix_fault_tickets_manual_review",
            "region_id",
            "updated_at",
            postgresql_where=text("status = 'MANUAL_REVIEW'"),
        ),
        Index("ix_fault_tickets_lighting_point_id", "lighting_point_id"),
        Index("ix_fault_tickets_reporter_account_id", "reporter_account_id"),
        Index("ix_fault_tickets_created_at", created_at.desc()),
    )
