from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from tomori.db.base import Base
from tomori.db.types import TZDateTime


class AcceptanceRecord(Base):
    """⑯ 验收记录表 ORM 模型（一次维修过程恰好一次验收，驳回同事务关联返修单）。"""

    __tablename__ = "acceptance_records"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("fault_tickets.id", ondelete="RESTRICT"),
        nullable=False,
    )
    repair_process_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("repair_processes.id", ondelete="RESTRICT"),
        nullable=False,
    )
    rework_order_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("rework_orders.id", ondelete="RESTRICT"),
        nullable=True,
    )
    inspector_account_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    comment: Mapped[str | None] = mapped_column(String(512), nullable=True)
    accepted_at: Mapped[datetime] = mapped_column(
        TZDateTime,
        nullable=False,
        server_default=text("now()"),
    )
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime,
        nullable=False,
        server_default=text("now()"),
    )

    __table_args__ = (
        UniqueConstraint(
            "repair_process_id",
            name="uq_acceptance_records_repair_process_id",
        ),
        CheckConstraint(
            "decision IN ('APPROVED','REJECTED')",
            name="decision_enum",
        ),
        CheckConstraint(
            "decision <> 'REJECTED' OR (comment IS NOT NULL AND length(btrim(comment)) > 0)",
            name="reject_comment_required",
        ),
        CheckConstraint(
            "(decision = 'REJECTED') = (rework_order_id IS NOT NULL)",
            name="rework_linked_on_reject",
        ),
        Index(
            "ix_acceptance_records_ticket_id",
            "ticket_id",
            accepted_at.desc(),
        ),
        Index(
            "ix_acceptance_records_inspector",
            "inspector_account_id",
            accepted_at.desc(),
        ),
    )
