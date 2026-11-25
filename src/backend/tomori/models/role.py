from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, SmallInteger, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from tomori.db.base import Base
from tomori.db.types import TZDateTime

__all__ = ["Role"]


class Role(Base):
    """② 角色字典表（ADMIN / INSPECTOR / TECHNICIAN，系统只读种子数据）。"""

    __tablename__ = "roles"
    __table_args__ = (
        UniqueConstraint("code", name="uq_roles_code"),
        CheckConstraint(
            "code IN ('ADMIN', 'INSPECTOR', 'TECHNICIAN')",
            name="code_enum",
        ),
    )

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime,
        nullable=False,
        server_default=text("now()"),
    )
