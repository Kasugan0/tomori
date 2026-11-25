from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from tomori.db.base import Base
from tomori.db.types import TZDateTime


class ReworkOrder(Base):
    """⑰ 返修单表 ORM 模型（每轮返修一行，超阈值强制人工复核）。"""

    __tablename__ = "rework_orders"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    rework_no: Mapped[str] = mapped_column(String(32), nullable=False)
    ticket_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("fault_tickets.id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_acceptance_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("acceptance_records.id", ondelete="RESTRICT"),
        nullable=False,
    )
    round_no: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    region_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="PENDING_DISPATCH",
        server_default=text("'PENDING_DISPATCH'"),
    )
    reject_reason: Mapped[str] = mapped_column(String(512), nullable=False)
    requires_manual_review: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    manual_review_decided_by_account_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("accounts.id", ondelete="SET NULL"),
        nullable=True,
    )
    manual_review_decided_at: Mapped[datetime | None] = mapped_column(
        TZDateTime,
        nullable=True,
    )
    manual_review_decision: Mapped[str | None] = mapped_column(String(32), nullable=True)
    manual_review_note: Mapped[str | None] = mapped_column(String(512), nullable=True)
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
        UniqueConstraint("rework_no", name="uq_rework_orders_rework_no"),
        UniqueConstraint("ticket_id", "round_no", name="uq_rework_orders_ticket_round"),
        CheckConstraint(
            "status IN ('PENDING_DISPATCH','IN_MANUAL_REVIEW','DISPATCHED','IN_REPAIR',"
            "'PENDING_ACCEPTANCE','PASSED','CLOSED_BY_REVIEW','CANCELLED')",
            name="status_enum",
        ),
        CheckConstraint("round_no >= 1", name="round_positive"),
        CheckConstraint(
            "manual_review_decision IS NULL "
            "OR manual_review_decision IN ('APPROVE_REASSIGN','SPECIAL_CLOSE','TERMINATE_CANCEL')",
            name="manual_review_decision_enum",
        ),
        CheckConstraint(
            "(requires_manual_review = TRUE "
            "AND status IN ('IN_MANUAL_REVIEW','CLOSED_BY_REVIEW','CANCELLED')) "
            "OR (requires_manual_review = FALSE "
            "AND status NOT IN ('IN_MANUAL_REVIEW','CLOSED_BY_REVIEW'))",
            name="manual_review_state_consistent",
        ),
        CheckConstraint(
            "(manual_review_decision IS NULL "
            "AND manual_review_decided_by_account_id IS NULL "
            "AND manual_review_decided_at IS NULL) "
            "OR (manual_review_decision IS NOT NULL "
            "AND manual_review_decided_by_account_id IS NOT NULL "
            "AND manual_review_decided_at IS NOT NULL "
            "AND status IN ('CLOSED_BY_REVIEW','CANCELLED'))",
            name="manual_review_fields_consistent",
        ),
        Index(
            "uq_rework_orders_active_ticket",
            "ticket_id",
            unique=True,
            postgresql_where=text("status NOT IN ('PASSED','CLOSED_BY_REVIEW','CANCELLED')"),
        ),
        Index("ix_rework_orders_status_created", "status", "created_at"),
        Index("ix_rework_orders_region_id", "region_id", created_at.desc()),
    )
