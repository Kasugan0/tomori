"""通知队列异步投递、退避重试与 PROCESSING 卡死自愈 worker（状态机 D）。

严格遵循 `docs/DESIGN.md` §2.4 与 `docs/API.md` §2.5：
- `D2`: `PENDING` 或 `FAILED (attempt_count < max_attempts)` 且到期任务经
  `FOR UPDATE SKIP LOCKED` 抢占后转为 `PROCESSING`。
- `D3`: `IN_APP`（V1 默认通道）立即成功并置 `SENT` + `sent_at`。
- `D4`: 投递失败递增 `attempt_count`、记录 `last_error`，若 `attempt_count < max_attempts`
  置 `FAILED` 并按 `(60, 300, 900)` 秒（1m / 5m / 15m）计算 `next_retry_at`。
- `D5`/`D6`: 若 `attempt_count >= max_attempts` 置 `DEAD_LETTER` 并向全部活跃 `ADMIN`
  生成 `NOTIFICATION_DEAD_LETTER` 告警通知。
- `PROCESSING` 10 分钟卡死自愈：扫描 `updated_at < now - 10min` 的任务，若
  `attempt_count + 1 < max_attempts` 则退回 `PENDING`；否则直入 `DEAD_LETTER`
  （`last_error="worker_processing_timeout_10m"`）并通知全部活跃 `ADMIN`，杜绝死路。
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Final

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tomori.db.session import get_async_sessionmaker
from tomori.models.account import Account
from tomori.models.account_role import AccountRole
from tomori.models.notification_task import NotificationTask
from tomori.models.role import Role

logger = logging.getLogger(__name__)

RETRY_BACKOFF_SECONDS: Final[tuple[int, ...]] = (60, 300, 900)
PROCESSING_TIMEOUT_MINUTES: Final[int] = 10
WORKER_PROCESSING_TIMEOUT_ERROR: Final[str] = "worker_processing_timeout_10m"
DEFAULT_NOTIFIER_INTERVAL_SECONDS: Final[float] = 5.0
DEFAULT_NOTIFIER_BATCH_SIZE: Final[int] = 50
MAX_LAST_ERROR_LENGTH: Final[int] = 512

DeliveryResult = bool | None
ChannelDispatcher = Callable[[NotificationTask], Awaitable[DeliveryResult] | DeliveryResult]

__all__ = [
    "DEFAULT_NOTIFIER_BATCH_SIZE",
    "DEFAULT_NOTIFIER_INTERVAL_SECONDS",
    "PROCESSING_TIMEOUT_MINUTES",
    "RETRY_BACKOFF_SECONDS",
    "WORKER_PROCESSING_TIMEOUT_ERROR",
    "ChannelDispatcher",
    "DeliveryResult",
    "compute_backoff_seconds",
    "default_channel_dispatcher",
    "notifier_loop",
    "process_due_notifications_once",
    "recover_stuck_processing_tasks",
]


def _ensure_utc(dt: datetime | None) -> datetime:
    if dt is None:
        return datetime.now(UTC)
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("Expected timezone-aware datetime")
    return dt.astimezone(UTC)


def _truncate_error(raw: str) -> str:
    cleaned = raw.strip() or "delivery_failed"
    return cleaned[:MAX_LAST_ERROR_LENGTH]


def compute_backoff_seconds(attempt_count: int) -> int:
    """计算 D4 失败退避秒数：第 1/2/3+ 次失败分别为 60 / 300 / 900 秒。"""
    if attempt_count <= 1:
        return RETRY_BACKOFF_SECONDS[0]
    index = min(attempt_count - 1, len(RETRY_BACKOFF_SECONDS) - 1)
    return RETRY_BACKOFF_SECONDS[index]


def default_channel_dispatcher(task: NotificationTask) -> None:
    """V1 默认通道投递器：`IN_APP` 立即成功；`SMS`/`EMAIL` 未接网关时抛错触发重试与死信。"""
    if task.channel == "IN_APP":
        return
    raise RuntimeError(f"channel_{task.channel.lower()}_not_configured_in_v1")


async def _get_active_admin_ids(session: AsyncSession) -> list[int]:
    stmt = (
        select(Account.id)
        .join(AccountRole, AccountRole.account_id == Account.id)
        .join(Role, Role.id == AccountRole.role_id)
        .where(
            Account.status == "ACTIVE",
            Role.code == "ADMIN",
        )
        .order_by(Account.id.asc())
    )
    return list((await session.scalars(stmt)).all())


def _build_dead_letter_alert(task: NotificationTask, *, admin_id: int) -> NotificationTask:
    title = f"通知投递进入死信队列 (#{task.id})"[:128]
    body = (
        f"通知任务 #{task.id}（事件 {task.event_type}，通道 {task.channel}）"
        f"已达最大重试次数（{task.attempt_count}/{task.max_attempts}），"
        f"最后错误：{task.last_error or 'unknown'}。"
    )
    return NotificationTask(
        recipient_account_id=admin_id,
        event_type="NOTIFICATION_DEAD_LETTER",
        channel="IN_APP",
        title=title,
        body=body,
        payload_json={
            "dead_letter_task_id": task.id,
            "original_event_type": task.event_type,
            "original_channel": task.channel,
            "original_recipient_account_id": task.recipient_account_id,
            "attempt_count": task.attempt_count,
            "max_attempts": task.max_attempts,
            "last_error": task.last_error,
            "deep_link": "/notifications/dead-letters",
        },
        related_ticket_id=task.related_ticket_id,
        related_dispatch_id=task.related_dispatch_id,
        status="PENDING",
        attempt_count=0,
        max_attempts=3,
        next_retry_at=None,
        sent_at=None,
        is_read=False,
        read_at=None,
    )


async def _fan_out_dead_letter_alerts(
    session: AsyncSession,
    dead_tasks: Sequence[NotificationTask],
) -> int:
    eligible = [task for task in dead_tasks if task.event_type != "NOTIFICATION_DEAD_LETTER"]
    if not eligible:
        return 0

    admin_ids = await _get_active_admin_ids(session)
    if not admin_ids:
        logger.warning(
            "Dead-lettered %d notification task(s), but no active ADMIN account exists",
            len(eligible),
        )
        return 0

    created = 0
    for task in eligible:
        for admin_id in admin_ids:
            session.add(_build_dead_letter_alert(task, admin_id=admin_id))
            created += 1
    return created


async def recover_stuck_processing_tasks(
    session: AsyncSession,
    *,
    timeout_minutes: int = PROCESSING_TIMEOUT_MINUTES,
    now: datetime | None = None,
) -> int:
    """回收卡在 `PROCESSING` 超过 10 分钟的通知任务（`DESIGN.md` §2.4）。

    - 若 `attempt_count + 1 < max_attempts`：置回 `PENDING`，递增 `attempt_count`，
      置 `next_retry_at = now` 立即可重试。
    - 若 `attempt_count + 1 >= max_attempts`：直接转入 `DEAD_LETTER`，
      置 `last_error="worker_processing_timeout_10m"`，并向全部活跃 `ADMIN` 发出
      `NOTIFICATION_DEAD_LETTER` 告警，确保不存在卡死状态。
    """
    if timeout_minutes < 1:
        raise ValueError("timeout_minutes must be >= 1")

    async def _run() -> int:
        current_time = _ensure_utc(now)
        cutoff = current_time - timedelta(minutes=timeout_minutes)

        stmt = (
            select(NotificationTask)
            .where(
                NotificationTask.status == "PROCESSING",
                NotificationTask.updated_at < cutoff,
            )
            .order_by(NotificationTask.updated_at.asc(), NotificationTask.id.asc())
            .with_for_update(skip_locked=True)
        )
        stuck_tasks = list((await session.scalars(stmt)).all())
        if not stuck_tasks:
            return 0

        dead_lettered: list[NotificationTask] = []
        for task in stuck_tasks:
            next_attempt = task.attempt_count + 1
            task.sent_at = None
            task.updated_at = current_time
            if next_attempt < task.max_attempts:
                task.status = "PENDING"
                task.attempt_count = next_attempt
                task.next_retry_at = current_time
                task.last_error = WORKER_PROCESSING_TIMEOUT_ERROR
            else:
                task.status = "DEAD_LETTER"
                task.attempt_count = min(next_attempt, task.max_attempts)
                task.next_retry_at = None
                task.last_error = WORKER_PROCESSING_TIMEOUT_ERROR
                dead_lettered.append(task)

        if dead_lettered:
            await _fan_out_dead_letter_alerts(session, dead_lettered)

        await session.flush()
        return len(stuck_tasks)

    if session.in_transaction():
        return await _run()
    async with session.begin():
        return await _run()


async def process_due_notifications_once(
    session: AsyncSession,
    *,
    batch_size: int = DEFAULT_NOTIFIER_BATCH_SIZE,
    dispatcher: ChannelDispatcher | None = None,
    now: datetime | None = None,
) -> dict[str, int]:
    """抢占并投递一批到期通知任务，返回单轮计数摘要。"""
    if batch_size < 1:
        raise ValueError("batch_size must be >= 1")

    async def _run() -> dict[str, int]:
        current_time = _ensure_utc(now)
        effective_dispatcher = dispatcher if dispatcher is not None else default_channel_dispatcher

        claim_stmt = (
            select(NotificationTask)
            .where(
                or_(
                    NotificationTask.status == "PENDING",
                    and_(
                        NotificationTask.status == "FAILED",
                        NotificationTask.attempt_count < NotificationTask.max_attempts,
                    ),
                ),
                or_(
                    NotificationTask.next_retry_at.is_(None),
                    NotificationTask.next_retry_at <= current_time,
                ),
            )
            .order_by(NotificationTask.created_at.asc(), NotificationTask.id.asc())
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
        tasks = list((await session.scalars(claim_stmt)).all())
        if not tasks:
            return {
                "claimed": 0,
                "sent": 0,
                "failed": 0,
                "dead_lettered": 0,
                "dead_letter_alerts_created": 0,
            }

        # D2: 先将抢占行原子置为 PROCESSING
        for task in tasks:
            task.status = "PROCESSING"
            task.updated_at = current_time
        await session.flush()

        sent_count = 0
        failed_count = 0
        dead_lettered_tasks: list[NotificationTask] = []

        for task in tasks:
            try:
                outcome = effective_dispatcher(task)
                if inspect.isawaitable(outcome):
                    outcome = await outcome
                if outcome is False:
                    raise RuntimeError(f"channel_{task.channel.lower()}_delivery_returned_false")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                next_attempt = min(task.attempt_count + 1, task.max_attempts)
                task.attempt_count = next_attempt
                task.last_error = _truncate_error(str(exc) or type(exc).__name__)
                task.sent_at = None
                task.updated_at = current_time
                if next_attempt < task.max_attempts:
                    backoff_seconds = compute_backoff_seconds(next_attempt)
                    task.status = "FAILED"
                    task.next_retry_at = current_time + timedelta(seconds=backoff_seconds)
                    failed_count += 1
                else:
                    task.status = "DEAD_LETTER"
                    task.next_retry_at = None
                    dead_lettered_tasks.append(task)
            else:
                task.status = "SENT"
                task.sent_at = current_time
                task.next_retry_at = None
                task.updated_at = current_time
                sent_count += 1

        alerts_created = await _fan_out_dead_letter_alerts(session, dead_lettered_tasks)
        await session.flush()
        return {
            "claimed": len(tasks),
            "sent": sent_count,
            "failed": failed_count,
            "dead_lettered": len(dead_lettered_tasks),
            "dead_letter_alerts_created": alerts_created,
        }

    if session.in_transaction():
        return await _run()
    async with session.begin():
        return await _run()


async def notifier_loop(
    stop_event: asyncio.Event,
    *,
    interval_seconds: float = DEFAULT_NOTIFIER_INTERVAL_SECONDS,
    batch_size: int = DEFAULT_NOTIFIER_BATCH_SIZE,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    dispatcher: ChannelDispatcher | None = None,
) -> None:
    """周期性通知投递与卡死自愈后台循环，响应 `stop_event` 或 `CancelledError` 优雅退出。"""
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be > 0")

    factory = session_factory or get_async_sessionmaker()
    while not stop_event.is_set():
        try:
            async with factory() as session, session.begin():
                recovered = await recover_stuck_processing_tasks(session)

            async with factory() as session, session.begin():
                summary = await process_due_notifications_once(
                    session,
                    batch_size=batch_size,
                    dispatcher=dispatcher,
                )

            if recovered > 0 or summary["claimed"] > 0:
                logger.debug(
                    "Notifier tick completed: recovered=%d summary=%s",
                    recovered,
                    summary,
                )
        except asyncio.CancelledError:
            logger.info("Notifier loop cancelled; shutting down cleanly")
            raise
        except Exception:
            logger.exception("Notifier tick failed; will retry on next interval")

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
        except TimeoutError:
            continue
        except asyncio.CancelledError:
            logger.info("Notifier loop cancelled while waiting; shutting down cleanly")
            raise
