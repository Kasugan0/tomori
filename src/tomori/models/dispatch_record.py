from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    Numeric,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from tomori.db.base import Base
from tomori.db.types import TZDateTime


class DispatchRecord(Base):
    """⑬ 派工记录表 ORM 模型（完整派工/改派历史，同票同时最多一条活跃派工）。"""

    __tablename__ = "dispatch_records"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("fault_tickets.id", ondelete="RESTRICT"),
        nullable=False,
    )
    rework_order_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("rework_orders.id"),
        nullable=True,
    )
    region_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    technician_account_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    scope_authorization_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("scope_authorizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    dispatched_by_account_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    dispatch_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="PENDING_ACCEPT",
        server_default=text("'PENDING_ACCEPT'"),
    )
    recommend_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    candidate_snapshot_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
    )
    dispatched_at: Mapped[datetime] = mapped_column(
        TZDateTime,
        nullable=False,
        server_default=text("now()"),
    )
    accept_deadline_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    rejected_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    reject_reason: Mapped[str | None] = mapped_column(String(256), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    escalated_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    escalate_reason: Mapped[str | None] = mapped_column(String(256), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime,
        nullable=False,
        server_default=text("now()"),
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING_ACCEPT','ACCEPTED','REJECTED','TIMEOUT_REASSIGNED',"
            "'COMPLETED','ESCALATED','CANCELLED')",
            name="status_enum",
        ),
        CheckConstraint(
            "dispatch_mode IN ('MANUAL','AUTO_RECOMMENDED','REASSIGN')",
            name="dispatch_mode_enum",
        ),
        CheckConstraint(
            "recommend_score IS NULL OR recommend_score >= 0",
            name="recommend_score_range",
        ),
        CheckConstraint(
            "status <> 'REJECTED' OR (reject_reason IS NOT NULL AND rejected_at IS NOT NULL)",
            name="reject_reason_required",
        ),
        CheckConstraint(
            "status NOT IN ('ESCALATED','TIMEOUT_REASSIGNED') OR escalated_at IS NOT NULL",
            name="escalate_reason_required",
        ),
        CheckConstraint(
            "(status = 'COMPLETED') = (completed_at IS NOT NULL)",
            name="completed_at_required",
        ),
        CheckConstraint(
            "(status IN ('ACCEPTED','COMPLETED')) = (accepted_at IS NOT NULL)",
            name="accepted_at_required",
        ),
        Index(
            "uq_dispatch_records_active_ticket",
            "ticket_id",
            unique=True,
            postgresql_where=text("status IN ('PENDING_ACCEPT','ACCEPTED')"),
        ),
        Index(
            "ix_dispatch_records_technician_active",
            "technician_account_id",
            "status",
            "accept_deadline_at",
            postgresql_where=text("status IN ('PENDING_ACCEPT','ACCEPTED')"),
        ),
        Index(
            "ix_dispatch_records_ticket_id",
            "ticket_id",
            dispatched_at.desc(),
        ),
        Index(
            "ix_dispatch_records_region_id",
            "region_id",
            dispatched_at.desc(),
        ),
    )
