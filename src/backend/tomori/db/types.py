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


def ensure_utc(dt: datetime | None = None) -> datetime:
    """归一时区感知 UTC：传 `None` 返回当前 UTC，naive datetime 抛 `ValueError`。

    由 `state_machine`/`sla`/`ticket_service`/`rework`/`dispatch`/`notifier`/`sla_scanner`
    七处共用一份实现（`task-213` 收敛 `_ensure_utc` 私有拷贝）。
    """
    if dt is None:
        return datetime.now(UTC)
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("timezone-aware datetime is required")
    return dt.astimezone(UTC)


def utc_now() -> datetime:
    """当前 UTC 时间（`ensure_utc()` 语义别名）。"""
    return datetime.now(UTC)
