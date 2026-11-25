from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator


class TZDateTime(TypeDecorator[datetime]):
    """PostgreSQL 原生 TIMESTAMPTZ（TIMESTAMP WITH TIME ZONE）类型。

    强制要求绑定值为时区感知的 datetime（防止 naive datetime 跨时区静默偏移），
    并在读回时确保带 tzinfo（UTC）。
    """

    impl = TIMESTAMP(timezone=True)
    cache_ok = True

    def process_bind_param(
        self,
        value: datetime | None,
        dialect: Dialect,
    ) -> datetime | None:
        if value is not None:
            if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
                raise TypeError("TZDateTime requires a timezone-aware datetime")
            return value.astimezone(UTC)
        return None

    def process_result_value(
        self,
        value: Any,
        dialect: Dialect,
    ) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
                return value.replace(tzinfo=UTC)
            return value
        raise TypeError(f"Expected datetime from DB, got {type(value)!r}")
