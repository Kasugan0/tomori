from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from tomori.db.base import Base
from tomori.db.types import TZDateTime

__all__ = ["NotificationTask"]


class NotificationTask(Base):
    """⑥ 通知任务表（站内通知与异步投递重试队列）。"""

    __tablename__ = "notification_tasks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    recipient_account_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(48), nullable=False)
    channel: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="IN_APP",
        server_default=text("'IN_APP'"),
    )
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    payload_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    related_ticket_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("fault_tickets.id", ondelete="CASCADE"),
        nullable=True,
    )
    related_dispatch_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("dispatch_records.id", ondelete="CASCADE"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="PENDING",
        server_default=text("'PENDING'"),
    )
    attempt_count: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    max_attempts: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=3,
        server_default=text("3"),
    )
    next_retry_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(512), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    is_read: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    read_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    acknowledged_by_account_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("accounts.id", ondelete="SET NULL"),
        nullable=True,
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
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
        CheckConstraint(
            "status IN ('PENDING', 'SENT', 'PROCESSING', 'FAILED', 'DEAD_LETTER')",
            name="status_enum",
        ),
        CheckConstraint(
            "channel IN ('IN_APP', 'SMS', 'EMAIL')",
            name="channel_enum",
        ),
        CheckConstraint(
            "attempt_count >= 0 AND max_attempts >= 1 AND attempt_count <= max_attempts",
            name="attempts_non_negative",
        ),
        CheckConstraint(
            "(status = 'SENT') = (sent_at IS NOT NULL)",
            name="sent_at_required",
        ),
        CheckConstraint(
            "status <> 'DEAD_LETTER' OR last_error IS NOT NULL",
            name="dead_letter_error_required",
        ),
        CheckConstraint(
            "status NOT IN ('FAILED', 'PENDING') "
            "OR next_retry_at IS NULL "
            "OR attempt_count < max_attempts",
            name="failed_next_retry",
        ),
        CheckConstraint(
            "(is_read = TRUE) = (read_at IS NOT NULL)",
            name="read_consistent",
        ),
        Index(
            "ix_notification_tasks_pending_due",
            "next_retry_at",
            "created_at",
            postgresql_where=text("status IN ('PENDING', 'FAILED')"),
        ),
        Index(
            "ix_notification_tasks_recipient_unread",
            "recipient_account_id",
            created_at.desc(),
            postgresql_where=text("is_read = false"),
        ),
        Index(
            "ix_notification_tasks_dead_letter",
            created_at.desc(),
            postgresql_where=text("status = 'DEAD_LETTER'"),
        ),
        Index("ix_notification_tasks_related_ticket_id", "related_ticket_id"),
    )
