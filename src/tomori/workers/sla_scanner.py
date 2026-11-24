"""SLA 定时扫描、AI 候选过期回收与孤儿上传文件清理后台循环。

严格遵循 `docs/DESIGN.md` §2.1 / §3.3 / §4.5 与 `docs/API.md` §2.5：
- `run_sla_scan_once`: 在独立事务内委托 `tomori.services.sla.run_sla_scan` 执行三段 SLA 扫描。
- `expire_stale_ai_candidates_once`: 从 `business_rules['ai_candidate_ttl_hours']` 读取 TTL
  （默认 72h），将超期未确认的 `AI_CANDIDATE_PENDING` 工单通过
  `state_machine.transition_ticket` 置为 `CANCELLED` 并向报障人发送 `AI_CANDIDATE_EXPIRED` 通知。
- `cleanup_orphan_uploads_once`: 扫描 `UPLOAD_DIR` 中无对应 `fault_evidences` 记录且
  `mtime` 超过 24 小时的孤儿文件并按批（默认 500）删除（`D-25`）。
- `sla_scanner_loop`: 响应 `stop_event` 与 `asyncio.CancelledError` 的异步后台循环。
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tomori.db.session import get_async_sessionmaker
from tomori.models.business_rule import BusinessRule
from tomori.models.fault_evidence import FaultEvidence
from tomori.models.fault_ticket import FaultTicket
from tomori.services.sla import run_sla_scan
from tomori.services.state_machine import transition_ticket
from tomori.settings import SLA_SCAN_INTERVAL_SECONDS, get_settings

logger = logging.getLogger(__name__)

DEFAULT_AI_CANDIDATE_TTL_HOURS: Final[int] = 72
DEFAULT_ORPHAN_CLEANUP_AGE_HOURS: Final[int] = 24
DEFAULT_ORPHAN_CLEANUP_BATCH_SIZE: Final[int] = 500

__all__ = [
    "DEFAULT_AI_CANDIDATE_TTL_HOURS",
    "DEFAULT_ORPHAN_CLEANUP_AGE_HOURS",
    "DEFAULT_ORPHAN_CLEANUP_BATCH_SIZE",
    "cleanup_orphan_uploads_once",
    "expire_stale_ai_candidates_once",
    "run_sla_scan_once",
    "sla_scanner_loop",
]


def _ensure_utc(dt: datetime | None) -> datetime:
    if dt is None:
        return datetime.now(UTC)
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("Expected timezone-aware datetime")
    return dt.astimezone(UTC)


async def _get_ai_candidate_ttl_hours(session: AsyncSession) -> int:
    rule_value = await session.scalar(
        select(BusinessRule.rule_value).where(
            BusinessRule.rule_code == "ai_candidate_ttl_hours",
            BusinessRule.is_active.is_(True),
        )
    )
    if isinstance(rule_value, dict):
        raw_ttl = rule_value.get("ttl_hours")
        if isinstance(raw_ttl, int) and not isinstance(raw_ttl, bool) and raw_ttl > 0:
            return raw_ttl
    return DEFAULT_AI_CANDIDATE_TTL_HOURS


async def run_sla_scan_once(
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> dict[str, int]:
    """在单次数据库事务内执行 `tomori.services.sla.run_sla_scan`。"""
    factory = session_factory or get_async_sessionmaker()
    async with factory() as session, session.begin():
        return await run_sla_scan(session)


async def expire_stale_ai_candidates_once(
    session: AsyncSession,
    *,
    now: datetime | None = None,
) -> int:
    """清理超期未确认的 `AI_CANDIDATE_PENDING` 工单并发送 `AI_CANDIDATE_EXPIRED` 通知。"""

    async def _run() -> int:
        current_time = _ensure_utc(now)
        ttl_hours = await _get_ai_candidate_ttl_hours(session)
        cutoff = current_time - timedelta(hours=ttl_hours)

        stmt = (
            select(FaultTicket)
            .where(
                FaultTicket.status == "AI_CANDIDATE_PENDING",
                FaultTicket.created_at <= cutoff,
            )
            .order_by(FaultTicket.created_at.asc(), FaultTicket.id.asc())
            .with_for_update(skip_locked=True)
        )
        stale_tickets = list((await session.scalars(stmt)).all())
        if not stale_tickets:
            return 0

        reason = f"AI candidate expired after {ttl_hours}h TTL"
        for ticket in stale_tickets:
            await transition_ticket(
                session,
                ticket,
                to_status="CANCELLED",
                actor_account_id=None,
                action="TICKET_CANCELLED",
                cancel_reason=reason,
                notify=[("AI_CANDIDATE_EXPIRED", [ticket.reporter_account_id])],
            )

        await session.flush()
        return len(stale_tickets)

    if session.in_transaction():
        return await _run()
    async with session.begin():
        return await _run()


def _normalize_rel_key(raw_path: str) -> str:
    return raw_path.replace("\\", "/").lstrip("./")


async def cleanup_orphan_uploads_once(
    session: AsyncSession,
    *,
    upload_dir: Path | None = None,
    min_age_hours: int = DEFAULT_ORPHAN_CLEANUP_AGE_HOURS,
    batch_size: int = DEFAULT_ORPHAN_CLEANUP_BATCH_SIZE,
    now: datetime | None = None,
) -> int:
    """删除 `upload_dir` 中无 `fault_evidences` 记录且 mtime 早于 24h 的孤儿文件（`D-25`）。"""
    if min_age_hours < 0:
        raise ValueError("min_age_hours must be >= 0")
    if batch_size < 1:
        raise ValueError("batch_size must be >= 1")

    root = (upload_dir or get_settings().upload_dir).resolve()
    if not root.is_dir():
        return 0

    current_time = _ensure_utc(now)
    cutoff = current_time - timedelta(hours=min_age_hours)

    rows = (
        await session.execute(
            select(
                FaultEvidence.storage_path,
                FaultEvidence.analysis_id,
                FaultEvidence.ai_result_json,
            )
        )
    ).all()
    known_keys: set[str] = set()
    root_prefix = f"{root.name}/"
    for storage_path, analysis_id, ai_result in rows:
        candidates: list[str] = [storage_path]
        if analysis_id is not None:
            candidates.append(f"vision/{analysis_id}_annotated.jpg")
        if isinstance(ai_result, dict):
            annotated_url = ai_result.get("annotated_image_url")
            if isinstance(annotated_url, str) and annotated_url:
                candidates.append(annotated_url)
        for candidate in candidates:
            if not candidate:
                continue
            norm = _normalize_rel_key(candidate)
            known_keys.add(norm)
            if norm.startswith(root_prefix):
                known_keys.add(norm.removeprefix(root_prefix))

    deleted = 0
    for path in sorted(root.rglob("*")):
        if deleted >= batch_size:
            break
        if path.is_symlink() or not path.is_file():
            continue
        try:
            resolved = path.resolve()
            rel_posix = resolved.relative_to(root).as_posix()
        except (OSError, ValueError):
            continue

        if (
            rel_posix in known_keys
            or f"{root.name}/{rel_posix}" in known_keys
            or resolved.as_posix() in known_keys
        ):
            continue

        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
        except OSError:
            continue
        if mtime >= cutoff:
            continue

        try:
            path.unlink(missing_ok=True)
            deleted += 1
        except OSError:
            logger.warning("Failed to remove orphan upload file %s", resolved)

    return deleted


async def sla_scanner_loop(
    stop_event: asyncio.Event,
    *,
    interval_seconds: float = SLA_SCAN_INTERVAL_SECONDS,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    include_orphan_cleanup: bool = False,
    orphan_cleanup_interval_seconds: float = 86_400.0,
) -> None:
    """周期性 SLA 扫描与 AI 候选 TTL 清理后台循环，响应 `stop_event` / `CancelledError` 退出。"""
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be > 0")

    factory = session_factory or get_async_sessionmaker()
    last_cleanup_monotonic = time.monotonic()

    while not stop_event.is_set():
        try:
            scan_summary = await run_sla_scan_once(session_factory=factory)
            async with factory() as session, session.begin():
                expired_count = await expire_stale_ai_candidates_once(session)

            cleaned_count = 0
            if (
                include_orphan_cleanup
                and (time.monotonic() - last_cleanup_monotonic) >= orphan_cleanup_interval_seconds
            ):
                async with factory() as session, session.begin():
                    cleaned_count = await cleanup_orphan_uploads_once(session)
                last_cleanup_monotonic = time.monotonic()

            if any(scan_summary.values()) or expired_count > 0 or cleaned_count > 0:
                logger.info(
                    "SLA scanner tick completed: scan=%s expired=%d orphan_deleted=%d",
                    scan_summary,
                    expired_count,
                    cleaned_count,
                )
        except asyncio.CancelledError:
            logger.info("SLA scanner loop cancelled; shutting down cleanly")
            raise
        except Exception:
            logger.exception("SLA scanner tick failed; will retry on next interval")

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
        except TimeoutError:
            continue
        except asyncio.CancelledError:
            logger.info("SLA scanner loop cancelled while waiting; shutting down cleanly")
            raise
