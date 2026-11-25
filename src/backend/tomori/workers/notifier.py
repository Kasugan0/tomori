"""通知队列异步投递、退避重试与 PROCESSING 卡死自愈 worker（状态机 D）。

严格遵循 `docs/DESIGN.md` §2.4 / §3.1 与 `docs/API.md` §2.5：
- 全部状态变更统一收敛经由 `tomori.services.state_machine.transition_notification` 执行，
  绝不在 worker 内直写 `task.status`。
- `FAILED` 重试抢占严格遵循 `FAILED → PENDING`（`D5`）→ `PENDING → PROCESSING`（`D2`）两步合法边；
- `D3`: `IN_APP`（V1 默认通道）立即成功并经 `transition_notification(..., "SENT")` 置为 `SENT`。
- `D4`: 投递失败递增 `attempt_count`、记录 `last_error`，若 `attempt_count < max_attempts`
  经 `transition_notification(..., "FAILED")` 置 `FAILED` 并按 `(60, 300, 900)` 秒退避。
- `D4 → D6`: 若 `attempt_count >= max_attempts`，依次经 `FAILED`（`D4`）→ `DEAD_LETTER`（`D6`），
  写 `NOTIFICATION_DEAD_LETTER` 审计行，并经 `create_notification_task` 向全部活跃 `ADMIN`
  生成 `NOTIFICATION_DEAD_LETTER` 告警通知。
- `PROCESSING` 10 分钟卡死自愈：扫描 `updated_at < now - 10min` 的任务，若
  `attempt_count + 1 < max_attempts` 则经 `transition_notification(..., "PENDING")` 退回；
  否则经 `transition_notification(..., "DEAD_LETTER")`（`worker_processing_timeout_10m`）
  并向全部活跃 `ADMIN` 发出 `NOTIFICATION_DEAD_LETTER` 告警。
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime, timedelta
from typing import Final

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tomori.db.session import get_async_sessionmaker
from tomori.db.types import ensure_utc
from tomori.models.notification_task import NotificationTask
from tomori.services.state_machine import (
    compute_notification_backoff,
    create_notification_task,
    list_all_admin_ids,
    transition_notification,
)

logger = logging.getLogger(__name__)

PROCESSING_TIMEOUT_MINUTES: Final[int] = 10
WORKER_PROCESSING_TIMEOUT_ERROR: Final[str] = "worker_processing_timeout_10m"
DEFAULT_NOTIFIER_INTERVAL_SECONDS: Final[float] = 5.0
DEFAULT_NOTIFIER_BATCH_SIZE: Final[int] = 50
MAX_LAST_ERROR_LENGTH: Final[int] = 512
_NOTIFIER_AUDIT_META: Final[dict[str, str]] = {"path": "/internal/workers/notifier"}

DeliveryResult = bool | None
ChannelDispatcher = Callable[[NotificationTask], Awaitable[DeliveryResult] | DeliveryResult]

__all__ = [
    "DEFAULT_NOTIFIER_BATCH_SIZE",
    "DEFAULT_NOTIFIER_INTERVAL_SECONDS",
    "PROCESSING_TIMEOUT_MINUTES",
    "WORKER_PROCESSING_TIMEOUT_ERROR",
    "ChannelDispatcher",
    "DeliveryResult",
    "compute_backoff_seconds",
    "default_channel_dispatcher",
    "notifier_loop",
    "process_due_notifications_once",
    "recover_stuck_processing_tasks",
]


def _truncate_error(raw: str) -> str:
    cleaned = raw.strip() or "delivery_failed"
    return cleaned[:MAX_LAST_ERROR_LENGTH]


def compute_backoff_seconds(attempt_count: int) -> int:
    """D4 失败退避秒数（薄壳委托 `state_machine.compute_notification_backoff`，表只留一份）。"""
    return int(compute_notification_backoff(attempt_count).total_seconds())


def default_channel_dispatcher(task: NotificationTask) -> None:
    """V1 默认通道投递器：`IN_APP` 立即成功；`SMS`/`EMAIL` 未接网关时抛错触发重试与死信。"""
    if task.channel == "IN_APP":
        return
    raise RuntimeError(f"channel_{task.channel.lower()}_not_configured_in_v1")


async def _fan_out_dead_letter_alerts(
    session: AsyncSession,
    dead_tasks: Sequence[NotificationTask],
) -> int:
    eligible = [task for task in dead_tasks if task.event_type != "NOTIFICATION_DEAD_LETTER"]
    if not eligible:
        return 0

    admin_ids = await list_all_admin_ids(session)
    if not admin_ids:
        logger.warning(
            "Dead-lettered %d notification task(s), but no active ADMIN account exists",
            len(eligible),
        )
        return 0

    created = 0
    for task in eligible:
        title = f"通知投递进入死信队列 (#{task.id})"[:128]
        body = (
            f"通知任务 #{task.id}（事件 {task.event_type}，通道 {task.channel}）"
            f"已达最大重试次数（{task.attempt_count}/{task.max_attempts}），"
            f"最后错误：{task.last_error or 'unknown'}。"
        )
        payload = {
            "dead_letter_task_id": task.id,
            "original_event_type": task.event_type,
            "original_channel": task.channel,
            "original_recipient_account_id": task.recipient_account_id,
            "attempt_count": task.attempt_count,
            "max_attempts": task.max_attempts,
            "last_error": task.last_error,
            "deep_link": "/notifications/dead-letters",
        }
        for admin_id in admin_ids:
            create_notification_task(
                session,
                recipient_account_id=admin_id,
                event_type="NOTIFICATION_DEAD_LETTER",
                channel="IN_APP",
                title=title,
                body=body,
                payload_json=payload,
                related_ticket_id=task.related_ticket_id,
                related_dispatch_id=task.related_dispatch_id,
            )
            created += 1
    return created


async def recover_stuck_processing_tasks(
    session: AsyncSession,
    *,
    timeout_minutes: int = PROCESSING_TIMEOUT_MINUTES,
    now: datetime | None = None,
) -> int:
    """回收卡在 `PROCESSING` 超过 10 分钟的通知任务（`DESIGN.md` §2.4）。

    - 若 `attempt_count + 1 < max_attempts`：经 `transition_notification(..., "PENDING")` 退回，
      递增 `attempt_count`，置 `next_retry_at = now` 立即可重试（`write_audit=False`）。
    - 若 `attempt_count + 1 >= max_attempts`：经 `transition_notification(..., "DEAD_LETTER")`，
      置 `last_error="worker_processing_timeout_10m"`，记死信审计并向活跃 `ADMIN` 告警。
    """
    if timeout_minutes < 1:
        raise ValueError("timeout_minutes must be >= 1")

    async def _run() -> int:
        current_time = ensure_utc(now)
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
            if next_attempt < task.max_attempts:
                await transition_notification(
                    session,
                    task,
                    "PENDING",
                    attempt_count=next_attempt,
                    last_error=WORKER_PROCESSING_TIMEOUT_ERROR,
                    next_retry_at=current_time,
                    now=current_time,
                    write_audit=False,
                )
            else:
                await transition_notification(
                    session,
                    task,
                    "DEAD_LETTER",
                    attempt_count=min(next_attempt, task.max_attempts),
                    last_error=WORKER_PROCESSING_TIMEOUT_ERROR,
                    action="NOTIFICATION_DEAD_LETTER",
                    now=current_time,
                    audit_meta=_NOTIFIER_AUDIT_META,
                    write_audit=True,
                )
                dead_lettered.append(task)
            task.updated_at = current_time

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
        current_time = ensure_utc(now)
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

        # 严格遵循状态机 D 合法边：FAILED 须先经 D5 (FAILED → PENDING)，再走 D2 (→ PROCESSING)
        for task in tasks:
            if task.status == "FAILED":
                await transition_notification(
                    session,
                    task,
                    "PENDING",
                    now=current_time,
                    write_audit=False,
                )
            await transition_notification(
                session,
                task,
                "PROCESSING",
                now=current_time,
                write_audit=False,
            )
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
                err_msg = _truncate_error(str(exc) or type(exc).__name__)
                if next_attempt < task.max_attempts:
                    backoff_seconds = compute_backoff_seconds(next_attempt)
                    await transition_notification(
                        session,
                        task,
                        "FAILED",
                        attempt_count=next_attempt,
                        last_error=err_msg,
                        next_retry_at=current_time + timedelta(seconds=backoff_seconds),
                        now=current_time,
                        write_audit=False,
                    )
                    failed_count += 1
                else:
                    # D4 (PROCESSING → FAILED) -> D6 (FAILED → DEAD_LETTER)
                    await transition_notification(
                        session,
                        task,
                        "FAILED",
                        attempt_count=next_attempt,
                        last_error=err_msg,
                        next_retry_at=None,
                        now=current_time,
                        write_audit=False,
                    )
                    await transition_notification(
                        session,
                        task,
                        "DEAD_LETTER",
                        last_error=err_msg,
                        action="NOTIFICATION_DEAD_LETTER",
                        now=current_time,
                        audit_meta=_NOTIFIER_AUDIT_META,
                        write_audit=True,
                    )
                    dead_lettered_tasks.append(task)
                task.updated_at = current_time
            else:
                await transition_notification(
                    session,
                    task,
                    "SENT",
                    now=current_time,
                    write_audit=False,
                )
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
