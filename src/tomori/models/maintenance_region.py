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
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from tomori.db.base import Base
from tomori.db.types import TZDateTime

__all__ = ["MaintenanceRegion"]


class MaintenanceRegion(Base):
    """⑧ 运维区域表（承载区域派单行政/运维边界树，scope_authorizations 作用域根）。"""

    __tablename__ = "maintenance_regions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    region_code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    parent_region_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("maintenance_regions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    level: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=3,
        server_default=text("3"),
    )
    boundary_geojson: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    description: Mapped[str | None] = mapped_column(String(512), nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )
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
        UniqueConstraint("region_code", name="uq_maintenance_regions_region_code"),
        CheckConstraint("level BETWEEN 1 AND 3", name="level_range"),
        CheckConstraint(
            "parent_region_id IS NULL OR parent_region_id <> id",
            name="no_self_parent",
        ),
        Index(
            "ix_maintenance_regions_is_active",
            "is_active",
            postgresql_where=text("is_active = true"),
        ),
        Index("ix_maintenance_regions_parent_region_id", "parent_region_id"),
    )
