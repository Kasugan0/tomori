from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import MetaData
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import DeclarativeBase

from tomori.db.types import TZDateTime

# 五套命名约定模板（保证 pk_ / fk_ / uq_ / ix_ / ck_ 确定性生成，且支持 drop_all 自动拓扑解环）
# 注意：ck 模板含 %(constraint_name)s，因此在 CheckConstraint(..., name="...") 中只写后缀名，
#       SQLAlchemy 会自动渲染为 ck_<table>_<suffix>（如 name="status_enum" ->
#       ck_fault_tickets_status_enum）。
#       ix/uq/fk/pk 显式传入完整约束名时因模板不含 %(constraint_name)s 而原样保留。
NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(AsyncAttrs, DeclarativeBase):
    """全项目 17 张业务表的声明式 ORM 基类（SQLAlchemy 2.0 Async）。"""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = {
        datetime: TZDateTime,
        dict[str, Any]: JSONB,
    }
