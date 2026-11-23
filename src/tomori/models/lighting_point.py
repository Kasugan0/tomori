from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from tomori.db.base import Base
from tomori.db.types import TZDateTime

__all__ = ["LightingPoint"]


class LightingPoint(Base):
    """⑨ 照明点位表（故障发生的最小地理单元，区域防重与派单归属挂载点）。"""

    __tablename__ = "lighting_points"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    point_code: Mapped[str] = mapped_column(String(64), nullable=False)
    region_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("maintenance_regions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    road_name: Mapped[str] = mapped_column(String(128), nullable=False)
    road_level: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="BRANCH",
        server_default=text("'BRANCH'"),
    )
    longitude: Mapped[Decimal] = mapped_column(Numeric(10, 7), nullable=False)
    latitude: Mapped[Decimal] = mapped_column(Numeric(10, 7), nullable=False)
    install_height_m: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    lamp_count: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=1,
        server_default=text("1"),
    )
    power_supply_type: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="GRID",
        server_default=text("'GRID'"),
    )
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
        UniqueConstraint("point_code", name="uq_lighting_points_point_code"),
        CheckConstraint(
            "road_level IN ('EXPRESS', 'MAIN', 'BRANCH')",
            name="road_level_enum",
        ),
        CheckConstraint(
            "status IN ('IN_SERVICE', 'SUSPENDED', 'RETIRED')",
            name="status_enum",
        ),
        CheckConstraint(
            "power_supply_type IN ('GRID', 'SOLAR', 'OTHER')",
            name="power_supply_type_enum",
        ),
        CheckConstraint(
            "longitude BETWEEN -180 AND 180",
            name="longitude_range",
        ),
        CheckConstraint(
            "latitude BETWEEN -90 AND 90",
            name="latitude_range",
        ),
        CheckConstraint(
            "lamp_count >= 1",
            name="lamp_count_positive",
        ),
        Index("ix_lighting_points_region_id_status", "region_id", "status"),
        Index("ix_lighting_points_road_name", "road_name"),
    )
