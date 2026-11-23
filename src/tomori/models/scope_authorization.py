from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from tomori.db.base import Base
from tomori.db.types import TZDateTime

__all__ = ["ScopeAuthorization"]


class ScopeAuthorization(Base):
    """④ 业务范围授权表（区域派单的授权事实唯一来源）。"""

    __tablename__ = "scope_authorizations"
    __table_args__ = (
        CheckConstraint(
            "scope_type IN ('INSPECT', 'REPAIR', 'MANAGE')",
            name="scope_type_enum",
        ),
        CheckConstraint(
            "valid_to IS NULL OR valid_to > valid_from",
            name="valid_window",
        ),
        Index(
            "uq_scope_authorizations_account_region_type",
            "account_id",
            "region_id",
            "scope_type",
            unique=True,
            postgresql_where=text("is_active = true"),
        ),
        Index(
            "ix_scope_authorizations_region_type_active",
            "region_id",
            "scope_type",
            "is_active",
        ),
        Index(
            "ix_scope_authorizations_account_id_active",
            "account_id",
            postgresql_where=text("is_active = true"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    account_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    region_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("maintenance_regions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    scope_type: Mapped[str] = mapped_column(String(16), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("true"),
    )
    valid_from: Mapped[datetime] = mapped_column(
        TZDateTime,
        nullable=False,
        server_default=text("now()"),
    )
    valid_to: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    granted_by_account_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("accounts.id", ondelete="SET NULL"),
        nullable=True,
    )
    remark: Mapped[str | None] = mapped_column(String(256), nullable=True)
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
