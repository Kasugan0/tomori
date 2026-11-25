from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from tomori.db.base import Base
from tomori.db.types import TZDateTime

__all__ = ["BusinessRule"]


class BusinessRule(Base):
    """⑤ 业务规则表（可配置、可版本化、可审计的系统规则参数）。"""

    __tablename__ = "business_rules"
    __table_args__ = (
        UniqueConstraint(
            "rule_code",
            "version",
            name="uq_business_rules_code_version",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(
            "jsonb_typeof(rule_value) = 'object'",
            name="rule_value_is_object",
        ),
        Index(
            "uq_business_rules_code_active",
            "rule_code",
            unique=True,
            postgresql_where=text("is_active = true"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    rule_code: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("1"),
    )
    rule_value: Mapped[dict[str, Any]] = mapped_column(
        postgresql.JSONB,
        nullable=False,
    )
    description: Mapped[str | None] = mapped_column(String(512), nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("true"),
    )
    effective_from: Mapped[datetime] = mapped_column(
        TZDateTime,
        nullable=False,
        server_default=text("now()"),
    )
    created_by_account_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("accounts.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime,
        nullable=False,
        server_default=text("now()"),
    )
