from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from tomori.db.base import Base
from tomori.db.types import TZDateTime

__all__ = ["Account"]


class Account(Base):
    """① 账号表（三角色统一账号，靠 token_version 做 O(1) 吊销）。"""

    __tablename__ = "accounts"
    __table_args__ = (
        UniqueConstraint("username", name="uq_accounts_username"),
        UniqueConstraint("employee_no", name="uq_accounts_employee_no"),
        CheckConstraint(
            "status IN ('ACTIVE', 'DISABLED', 'LOCKED')",
            name="status_enum",
        ),
        CheckConstraint("token_version >= 1", name="token_version_positive"),
        CheckConstraint(
            "max_active_dispatch BETWEEN 1 AND 20",
            name="max_active_dispatch_range",
        ),
        CheckConstraint(
            "username ~ '^[a-z0-9][a-z0-9_.-]{2,63}$'",
            name="username_format",
        ),
        Index(
            "ix_accounts_status",
            "status",
            postgresql_where=text("status = 'ACTIVE'"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(64), nullable=False)
    employee_no: Mapped[str | None] = mapped_column(String(32), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    email: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'ACTIVE'"),
    )
    skills: Mapped[list[str]] = mapped_column(
        postgresql.ARRAY(Text),
        nullable=False,
        server_default=text("'{}'::text[]"),
    )
    max_active_dispatch: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        server_default=text("3"),
    )
    token_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("1"),
    )
    manager_account_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("accounts.id", ondelete="SET NULL"),
        nullable=True,
    )
    last_login_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
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
