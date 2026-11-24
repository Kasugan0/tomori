"""郑州市令第 152 号 48h 法定 SLA 计算与三段幂等扫描（`POST /sla/scan`）。

严格遵循 `docs/DESIGN.md` §3.3 与 `docs/API.md` §2.5 / §9.1 / §9.2：
- 唯一法定时限 = 48 小时（2880 分钟），自 `T_discover` (`confirmed_at`) 起算；
- `response_deadline_at` 在现行基线下默认 `None`（仅配置正整数 `response_minutes` 时计算）；
- `P4`「观察」同等受 48h 约束，不得免除（`MUT-17`）；`regrade` / 改派不顺延 `repair_deadline_at`；
- `run_sla_scan` 三段处理：
  1) 响应阶段超时标记（`is_sla_overdue=false` 幂等守卫，`DISPATCH_RESPONSE_TIMEOUT`
     通知 + `SLA_TIMEOUT_MARKED` 审计）；
  2) 修复阶段 48h 超时标记（覆盖 `PENDING_DISPATCH` / `REWORK_PENDING_DISPATCH` /
     `DISPATCHED` / `IN_REPAIR` / `MANUAL_REVIEW`，`is_sla_overdue=false` 幂等守卫 `MUT-15`，
     `REPAIR_SLA_TIMEOUT` 通知区域 ADMIN 与在途维修员 + `SLA_TIMEOUT_MARKED` 审计）；
  3) 派工接单超 `accept_deadline_at` 防死锁回收（`state_machine.transition_dispatch` 置
     `TIMEOUT_REASSIGNED` + `state_machine.transition_ticket` 推回 `PENDING_DISPATCH`）。
- 严禁依赖 `fastapi`，所有时间戳统一采用 tz-aware `datetime.now(UTC)`。
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tomori.models.dispatch_record import DispatchRecord
from tomori.models.fault_ticket import FaultTicket
from tomori.models.rework_order import ReworkOrder
from tomori.services.exceptions import INVALID_RULE_PAYLOAD, AppError
from tomori.services.grading import get_active_rule
from tomori.services.state_machine import (
    build_audit_entry,
    enqueue_notifications,
    list_region_admin_ids,
    ticket_deep_link,
    transition_dispatch,
    transition_rework,
    transition_ticket,
)

__all__ = [
    "DEFAULT_SLA_POLICY",
    "compute_accept_deadline",
    "compute_deadlines",
    "get_ticket_deadlines",
    "run_sla_scan",
]

DEFAULT_SLA_POLICY: Final[dict[str, Any]] = {
    "statutory_deadline_hours": 48,
    "response_minutes": None,
    "emergency_notice_hours": 24,
    "applies_to_priorities": ["P1", "P2", "P3", "P4"],
}

_RESPONSE_OVERDUE_STATUSES: Final[tuple[str, ...]] = (
    "PENDING_DISPATCH",
    "REWORK_PENDING_DISPATCH",
    "DISPATCHED",
)
_REPAIR_OVERDUE_STATUSES: Final[tuple[str, ...]] = (
    "PENDING_DISPATCH",
    "REWORK_PENDING_DISPATCH",
    "DISPATCHED",
    "IN_REPAIR",
    "MANUAL_REVIEW",
)


def _ensure_utc(dt: datetime | None = None) -> datetime:
    if dt is None:
        return datetime.now(UTC)
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("Timezone-aware datetime required")
    return dt.astimezone(UTC)


def compute_deadlines(
    confirmed_at: datetime,
    sla_policy: Mapping[str, Any] | None = None,
) -> tuple[datetime | None, datetime]:
    """根据 `sla_policy` 计算 `(response_deadline_at, repair_deadline_at)`（全等级统一适用）。"""
    base_time = _ensure_utc(confirmed_at)
    policy = sla_policy if sla_policy is not None else DEFAULT_SLA_POLICY

    raw_hours = policy.get("statutory_deadline_hours", 48)
    if not isinstance(raw_hours, int) or isinstance(raw_hours, bool) or raw_hours <= 0:
        raise AppError(
            INVALID_RULE_PAYLOAD,
            "sla_policy.statutory_deadline_hours 必须为正整数",
            {"statutory_deadline_hours": raw_hours},
        )

    response_minutes = policy.get("response_minutes")
    response_deadline: datetime | None = None
    if response_minutes is not None:
        if (
            not isinstance(response_minutes, int)
            or isinstance(response_minutes, bool)
            or response_minutes <= 0
        ):
            raise AppError(
                INVALID_RULE_PAYLOAD,
                "sla_policy.response_minutes 必须为 null 或正整数",
                {"response_minutes": response_minutes},
            )
        response_deadline = base_time + timedelta(minutes=response_minutes)

    repair_deadline = base_time + timedelta(hours=raw_hours)
    return response_deadline, repair_deadline


def compute_accept_deadline(
    dispatched_at: datetime,
    repair_deadline_at: datetime,
    sla_policy: Mapping[str, Any] | None = None,
) -> datetime:
    """计算派工接单防死锁时限（`response_minutes=null` 时取所属票 `repair_deadline_at`）。"""
    disp_utc = _ensure_utc(dispatched_at)
    repair_utc = _ensure_utc(repair_deadline_at)
    policy = sla_policy if sla_policy is not None else DEFAULT_SLA_POLICY
    response_minutes = policy.get("response_minutes")
    if (
        isinstance(response_minutes, int)
        and not isinstance(response_minutes, bool)
        and response_minutes > 0
    ):
        return min(disp_utc + timedelta(minutes=response_minutes), repair_utc)
    return repair_utc


async def get_ticket_deadlines(
    session: AsyncSession,
    *,
    confirmed_at: datetime,
) -> tuple[datetime | None, datetime]:
    """读取活跃 `sla_policy` 并计算 `(response_deadline_at, repair_deadline_at)`。"""
    _, policy = await get_active_rule(session, "sla_policy")
    return compute_deadlines(confirmed_at, policy)


async def run_sla_scan(
    session: AsyncSession,
    *,
    actor_account_id: int | None = None,
    request_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> dict[str, int]:
    """执行三段幂等 SLA 扫描，返回三项统计计数。"""
    current_time = _ensure_utc(now)
    scan_request_id = request_id or uuid.uuid4()
    marked_overdue = 0
    timeout_reassigned = 0
    notifications_created = 0

    # 段 1：响应阶段超时（现行郑州基线下 response_deadline_at 为 NULL，零命中；保留兼容）
    resp_stmt = (
        select(FaultTicket)
        .where(
            FaultTicket.status.in_(_RESPONSE_OVERDUE_STATUSES),
            FaultTicket.response_deadline_at.is_not(None),
            FaultTicket.response_deadline_at < current_time,
            FaultTicket.is_sla_overdue.is_(False),
        )
        .order_by(FaultTicket.id.asc())
        .with_for_update(skip_locked=True)
    )
    resp_tickets = list((await session.scalars(resp_stmt)).all())
    for ticket in resp_tickets:
        ticket.is_sla_overdue = True
        ticket.overdue_stage = "RESPONSE"
        ticket.overdue_marked_at = current_time
        marked_overdue += 1

        admin_ids = await list_region_admin_ids(session, ticket.region_id, now=current_time)
        tasks = enqueue_notifications(
            session,
            admin_ids,
            "DISPATCH_RESPONSE_TIMEOUT",
            f"工单 {ticket.ticket_no} 响应阶段超时",
            f"工单 {ticket.ticket_no} 已超过响应截止时间，请及时关注。",
            related_ticket_id=ticket.id,
            payload_json={
                "ticket_id": ticket.id,
                "overdue_stage": "RESPONSE",
                "deep_link": ticket_deep_link(ticket.id),
            },
        )
        notifications_created += len(tasks)
        session.add(
            build_audit_entry(
                action="SLA_TIMEOUT_MARKED",
                resource_type="FAULT_TICKET",
                resource_id=ticket.id,
                ticket_id=ticket.id,
                actor_account_id=actor_account_id,
                path="/api/v1/sla/scan",
                request_id=scan_request_id,
                snapshot={
                    "ticket_no": ticket.ticket_no,
                    "status": ticket.status,
                    "priority": ticket.priority,
                    "overdue_stage": "RESPONSE",
                    "response_deadline_at": ticket.response_deadline_at,
                },
            )
        )
    if resp_tickets:
        await session.flush()

    # 段 2：法定 48h 维修养护/督促超时（含 MANUAL_REVIEW、含 P4 观察 —— MUT-15 / MUT-17 守卫）
    repair_stmt = (
        select(FaultTicket)
        .where(
            FaultTicket.status.in_(_REPAIR_OVERDUE_STATUSES),
            FaultTicket.repair_deadline_at.is_not(None),
            FaultTicket.repair_deadline_at < current_time,
            FaultTicket.is_sla_overdue.is_(False),
        )
        .order_by(FaultTicket.id.asc())
        .with_for_update(skip_locked=True)
    )
    repair_tickets = list((await session.scalars(repair_stmt)).all())
    for ticket in repair_tickets:
        ticket.is_sla_overdue = True
        ticket.overdue_stage = "REPAIR"
        ticket.overdue_marked_at = current_time
        marked_overdue += 1

        admin_ids = await list_region_admin_ids(session, ticket.region_id, now=current_time)
        active_dispatch = await session.scalar(
            select(DispatchRecord).where(
                DispatchRecord.ticket_id == ticket.id,
                DispatchRecord.status.in_(("PENDING_ACCEPT", "ACCEPTED")),
            )
        )
        recipients = list(admin_ids)
        if active_dispatch is not None:
            recipients.append(active_dispatch.technician_account_id)

        tasks = enqueue_notifications(
            session,
            recipients,
            "REPAIR_SLA_TIMEOUT",
            f"工单 {ticket.ticket_no} 超过法定 48h 时限",
            f"工单 {ticket.ticket_no} 已超过 48 小时法定维修养护/督促时限，请尽快处理。",
            related_ticket_id=ticket.id,
            related_dispatch_id=active_dispatch.id if active_dispatch else None,
            payload_json={
                "ticket_id": ticket.id,
                "overdue_stage": "REPAIR",
                "deep_link": ticket_deep_link(ticket.id),
            },
        )
        notifications_created += len(tasks)
        session.add(
            build_audit_entry(
                action="SLA_TIMEOUT_MARKED",
                resource_type="FAULT_TICKET",
                resource_id=ticket.id,
                ticket_id=ticket.id,
                actor_account_id=actor_account_id,
                path="/api/v1/sla/scan",
                request_id=scan_request_id,
                snapshot={
                    "ticket_no": ticket.ticket_no,
                    "status": ticket.status,
                    "priority": ticket.priority,
                    "overdue_stage": "REPAIR",
                    "repair_deadline_at": ticket.repair_deadline_at,
                },
            )
        )
    if repair_tickets:
        await session.flush()

    # 段 3：派工接单超时防死锁回收（PENDING_ACCEPT -> TIMEOUT_REASSIGNED + 票推回 PENDING_DISPATCH）
    timeout_dispatches_stmt = (
        select(DispatchRecord)
        .where(
            DispatchRecord.status == "PENDING_ACCEPT",
            DispatchRecord.accept_deadline_at < current_time,
        )
        .order_by(DispatchRecord.id.asc())
        .with_for_update(skip_locked=True)
    )
    expired_dispatches = list((await session.scalars(timeout_dispatches_stmt)).all())
    for disp in expired_dispatches:
        disp_ticket = await session.scalar(
            select(FaultTicket).where(FaultTicket.id == disp.ticket_id).with_for_update()
        )
        ticket_is_dispatched = disp_ticket is not None and disp_ticket.status == "DISPATCHED"

        await transition_dispatch(
            session,
            disp,
            "TIMEOUT_REASSIGNED",
            actor_account_id=actor_account_id,
            action="DISPATCH_TIMEOUT_REASSIGNED",
            reason="accept_deadline_at expired",
            now=current_time,
            request_id=scan_request_id,
            audit_meta={"path": "/api/v1/sla/scan"},
            persist_audit=not ticket_is_dispatched,
        )
        timeout_reassigned += 1

        if disp.rework_order_id is not None:
            rework = await session.scalar(
                select(ReworkOrder).where(ReworkOrder.id == disp.rework_order_id).with_for_update()
            )
            if rework is not None and rework.status == "DISPATCHED":
                await transition_rework(
                    session,
                    rework,
                    "PENDING_DISPATCH",
                    actor_account_id=actor_account_id,
                    action="DISPATCH_TIMEOUT_REASSIGNED",
                    now=current_time,
                    request_id=scan_request_id,
                    persist_audit=False,
                )

        if ticket_is_dispatched and disp_ticket is not None:
            admin_ids = await list_region_admin_ids(
                session, disp_ticket.region_id, now=current_time
            )
            unique_recipients = list(dict.fromkeys([*admin_ids, disp.technician_account_id]))
            await transition_ticket(
                session,
                disp_ticket,
                "PENDING_DISPATCH",
                actor_account_id=actor_account_id,
                action="DISPATCH_TIMEOUT_REASSIGNED",
                now=current_time,
                request_id=scan_request_id,
                audit_meta={"path": "/api/v1/sla/scan"},
                snapshot_extra={
                    "dispatch_id": disp.id,
                    "timed_out_technician_id": disp.technician_account_id,
                },
                notify=[("DISPATCH_TIMEOUT_REASSIGNED", unique_recipients)],
                persist_audit=True,
            )
            notifications_created += len(unique_recipients)

    await session.flush()
    return {
        "marked_overdue": marked_overdue,
        "timeout_reassigned": timeout_reassigned,
        "notifications_created": notifications_created,
    }
