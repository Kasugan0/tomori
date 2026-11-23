from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from tomori.db.base import Base
from tomori.db.types import TZDateTime


class MaterialUsage(Base):
    """⑮ 材料使用表 ORM 模型（单次维修消耗的材料明细）。"""

    __tablename__ = "material_usages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    repair_process_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("repair_processes.id", ondelete="CASCADE"),
        nullable=False,
    )
    ticket_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("fault_tickets.id", ondelete="RESTRICT"),
        nullable=False,
    )
    material_code: Mapped[str] = mapped_column(String(64), nullable=False)
    material_name: Mapped[str] = mapped_column(String(128), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(10, 3), nullable=False)
    unit: Mapped[str] = mapped_column(String(16), nullable=False)
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime,
        nullable=False,
        server_default=text("now()"),
    )

    __table_args__ = (
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("unit IN ('PCS','M','KG','SET')", name="unit_enum"),
        CheckConstraint(
            "unit_price IS NULL OR unit_price >= 0",
            name="unit_price_non_negative",
        ),
        UniqueConstraint(
            "repair_process_id",
            "material_code",
            name="uq_material_usages_repair_code",
        ),
        Index("ix_material_usages_ticket_id", "ticket_id"),
    )
