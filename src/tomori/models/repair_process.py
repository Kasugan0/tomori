from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
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


class RepairProcess(Base):
    """⑭ 维修过程表 ORM 模型（硬门禁 2：电气安全检查的数据库兜底载体）。"""

    __tablename__ = "repair_processes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    dispatch_record_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("dispatch_records.id", ondelete="RESTRICT"),
        nullable=False,
    )
    ticket_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("fault_tickets.id", ondelete="RESTRICT"),
        nullable=False,
    )
    technician_account_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    arrived_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    electrical_safety_checked: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    safety_checklist_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    safety_note: Mapped[str] = mapped_column(String(512), nullable=False)
    root_cause: Mapped[str | None] = mapped_column(String(256), nullable=True)
    repair_summary: Mapped[str] = mapped_column(Text, nullable=False)
    repair_started_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    repair_finished_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
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
        UniqueConstraint(
            "dispatch_record_id",
            name="uq_repair_processes_dispatch_record_id",
        ),
        CheckConstraint(
            "electrical_safety_checked = FALSE OR ("
            "(safety_checklist_json->>'power_isolated')::boolean IS TRUE "
            "AND (safety_checklist_json->>'voltage_verified_zero')::boolean IS TRUE "
            "AND (safety_checklist_json->>'insulation_intact')::boolean IS TRUE "
            "AND (safety_checklist_json->>'grounding_connected')::boolean IS TRUE "
            "AND (safety_checklist_json->>'ppe_worn')::boolean IS TRUE"
            ")",
            name="safety_checklist_complete",
        ),
        CheckConstraint(
            "length(btrim(safety_note)) > 0",
            name="safety_note_not_blank",
        ),
        CheckConstraint(
            "length(btrim(repair_summary)) > 0",
            name="repair_summary_not_blank",
        ),
        CheckConstraint(
            "repair_finished_at IS NULL OR repair_started_at IS NULL "
            "OR repair_finished_at >= repair_started_at",
            name="time_order",
        ),
        Index("ix_repair_processes_ticket_id", "ticket_id"),
        Index(
            "ix_repair_processes_technician",
            "technician_account_id",
            created_at.desc(),
        ),
    )
