from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from tomori.db.base import Base
from tomori.db.types import TZDateTime

__all__ = ["FacilityAsset"]


class FacilityAsset(Base):
    """⑩ 设施资产台账表（点位上的具体设施；rated_power_w 仅作铭牌属性，不用于能耗计算/预测）。"""

    __tablename__ = "facility_assets"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    asset_code: Mapped[str] = mapped_column(String(64), nullable=False)
    lighting_point_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("lighting_points.id", ondelete="RESTRICT"),
        nullable=False,
    )
    asset_type: Mapped[str] = mapped_column(String(24), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    model_spec: Mapped[str | None] = mapped_column(String(128), nullable=True)
    rated_power_w: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    importance: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="NORMAL",
        server_default=text("'NORMAL'"),
    )
    installed_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="IN_SERVICE",
        server_default=text("'IN_SERVICE'"),
    )
    description: Mapped[str | None] = mapped_column(String(512), nullable=True)
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
        UniqueConstraint("asset_code", name="uq_facility_assets_asset_code"),
        CheckConstraint(
            "asset_type IN ('LUMINAIRE', 'POLE', 'BRACKET', 'CONTROL_BOX', 'CABLE', 'OTHER')",
            name="asset_type_enum",
        ),
        CheckConstraint(
            "importance IN ('CRITICAL', 'HIGH', 'NORMAL')",
            name="importance_enum",
        ),
        CheckConstraint(
            "status IN ('IN_SERVICE', 'FAULTY', 'REPAIRING', 'RETIRED')",
            name="status_enum",
        ),
        CheckConstraint(
            "rated_power_w IS NULL OR rated_power_w >= 0",
            name="rated_power_non_negative",
        ),
        Index("ix_facility_assets_lighting_point_id", "lighting_point_id"),
    )
