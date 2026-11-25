from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, Index, SmallInteger, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from tomori.db.base import Base
from tomori.db.types import TZDateTime

__all__ = ["AccountRole"]


class AccountRole(Base):
    """③ 账号角色关联表（本设计约束一账号恰好一角色，保留关联结构以便未来扩展）。"""

    __tablename__ = "account_roles"
    __table_args__ = (
        UniqueConstraint("account_id", name="uq_account_roles_account_id"),
        UniqueConstraint(
            "account_id",
            "role_id",
            name="uq_account_roles_account_id_role_id",
        ),
        Index("ix_account_roles_role_id", "role_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    account_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    role_id: Mapped[int] = mapped_column(
        SmallInteger,
        ForeignKey("roles.id", ondelete="RESTRICT"),
        nullable=False,
    )
    granted_by_account_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("accounts.id", ondelete="SET NULL"),
        nullable=True,
    )
    granted_at: Mapped[datetime] = mapped_column(
        TZDateTime,
        nullable=False,
        server_default=text("now()"),
    )
