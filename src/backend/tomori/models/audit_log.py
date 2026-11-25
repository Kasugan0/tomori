from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CHAR,
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from tomori.db.base import Base
from tomori.db.types import TZDateTime

__all__ = ["AuditLog"]


class AuditLog(Base):
    """⑦ 操作审计表（严格 append-only：无 updated_at、无 deleted_at，兼承载 2xx 幂等重放记录）。"""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    actor_account_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("accounts.id", ondelete="SET NULL"),
        nullable=True,
    )
    actor_username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    actor_role_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    ticket_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    method: Mapped[str] = mapped_column(String(8), nullable=False)
    path: Mapped[str] = mapped_column(String(256), nullable=False)
    request_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    request_payload_hash: Mapped[str | None] = mapped_column(CHAR(64), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    response_status: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    business_snapshot_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    response_body_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    client_ip: Mapped[str | None] = mapped_column(INET, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime,
        nullable=False,
        server_default=text("now()"),
    )

    __table_args__ = (
        CheckConstraint(
            "response_status BETWEEN 100 AND 599",
            name="response_status_valid",
        ),
        CheckConstraint(
            "method IN ('GET', 'POST', 'PUT', 'PATCH', 'DELETE')",
            name="method_enum",
        ),
        CheckConstraint(
            "request_payload_hash IS NULL OR request_payload_hash ~ '^[0-9a-f]{64}$'",
            name="payload_hash_format",
        ),
        CheckConstraint(
            "(response_status < 400) OR error_code IS NOT NULL",
            name="error_code_iff_failure",
        ),
        Index(
            "uq_audit_logs_idempotency",
            "actor_account_id",
            "method",
            "path",
            "idempotency_key",
            unique=True,
            postgresql_where=text(
                "idempotency_key IS NOT NULL AND response_status >= 200 AND response_status < 300"
            ),
        ),
        Index("ix_audit_logs_created_at", created_at.desc()),
        Index("ix_audit_logs_actor_created", "actor_account_id", created_at.desc()),
        Index(
            "ix_audit_logs_ticket_id",
            "ticket_id",
            "created_at",
            postgresql_where=text("ticket_id IS NOT NULL"),
        ),
        Index("ix_audit_logs_resource", "resource_type", "resource_id"),
        Index("ix_audit_logs_action_created", "action", created_at.desc()),
        Index("ix_audit_logs_request_id", "request_id"),
    )
