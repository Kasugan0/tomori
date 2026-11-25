"""验收通过 / 驳回返修 7 步原子事务与人工复核三出口裁决服务。

严格遵循 `docs/DESIGN.md` §2.1 (A11–A17) / §2.3 (C1–C10) / §3.4 与 `docs/API.md` §2.5 / §7：
- `max_rework_rounds` 读取 `business_rules['rework_policy']`（默认 2），
  `round_no > max_rework_rounds` 时生成 `IN_MANUAL_REVIEW` 返修单并将票推入 `MANUAL_REVIEW`。
- 驳回返修同事务处理 `acceptance_records` ↔ `rework_orders` 双向非延迟外键与
  `ck_acceptance_records_rework_linked_on_reject` 检查约束：
  先以 `decision='APPROVED', rework_order_id=None` flush 取自增主键，创建 `ReworkOrder` 后
  同事务原子更新 `decision='REJECTED'` 与 `rework_order_id=rework.id`。
- 全部状态字段变更统一通过 `tomori.services.state_machine` 执行，本模块绝不直接赋值 `.status`。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Final
from zoneinfo import ZoneInfo

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from tomori.db.types import ensure_utc
from tomori.models.acceptance_record import AcceptanceRecord
from tomori.models.account import Account
from tomori.models.audit_log import AuditLog
from tomori.models.dispatch_record import DispatchRecord
from tomori.models.fault_ticket import FaultTicket
from tomori.models.repair_process import RepairProcess
from tomori.models.rework_order import ReworkOrder
from tomori.schemas.operations import AcceptanceSubmitIn, ReviewResolveIn
from tomori.services.exceptions import (
    INVALID_STATE_TRANSITION,
    RESOURCE_NOT_FOUND,
    VALIDATION_ERROR,
    AppError,
)
from tomori.services.grading import get_active_rule
from tomori.services.state_machine import (
    build_audit_entry,
    create_rework_order,
    list_all_active_admin_ids,
    list_region_admin_ids,
    transition_dispatch,
    transition_rework,
    transition_ticket,
)

__all__ = [
    "review_resolve_ticket",
    "submit_acceptance",
]

_SHANGHAI_TZ: Final[ZoneInfo] = ZoneInfo("Asia/Shanghai")
_DEFAULT_MAX_REWORK_ROUNDS: Final[int] = 2


async def _get_max_rework_rounds(session: AsyncSession) -> int:
    loaded = await get_active_rule(session, "rework_policy")
    if loaded is None:
        return _DEFAULT_MAX_REWORK_ROUNDS
    raw = loaded[1].get("max_rework_rounds", _DEFAULT_MAX_REWORK_ROUNDS)
    if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0:
        return raw
    return _DEFAULT_MAX_REWORK_ROUNDS


async def _next_rework_no(session: AsyncSession, *, now: datetime) -> str:
    """并发安全生成 `RW-YYYYMMDD-NNNN` 返修单号（事务级咨询锁 max+1 + UUID 后缀兜底）。"""
    day_str = now.astimezone(_SHANGHAI_TZ).strftime("%Y%m%d")
    prefix = f"RW-{day_str}-"

    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
        {"lock_key": f"rework_no:{day_str}"},
    )

    existing_nos = list(
        (
            await session.scalars(
                select(ReworkOrder.rework_no).where(ReworkOrder.rework_no.startswith(prefix))
            )
        ).all()
    )
    max_seq = 0
    for code in existing_nos:
        suffix = code[len(prefix) :]
        if suffix.isdigit():
            seq_val = int(suffix)
            if seq_val > max_seq:
                max_seq = seq_val

    next_seq = max_seq + 1
    if next_seq <= 9999:
        candidate = f"{prefix}{next_seq:04d}"
        if candidate not in existing_nos:
            return candidate
    return f"{prefix}{uuid.uuid4().hex[:8].upper()}"


async def _resolve_locked_ticket(
    session: AsyncSession,
    *,
    ticket_id: int | None,
    ticket: FaultTicket | None,
) -> FaultTicket:
    if ticket is not None:
        return ticket
    if ticket_id is None:
        raise ValueError("Either ticket_id or ticket must be provided")
    loaded = await session.scalar(
        select(FaultTicket).where(FaultTicket.id == ticket_id).with_for_update()
    )
    if loaded is None:
        raise AppError(
            RESOURCE_NOT_FOUND,
            "故障工单不存在",
            {"resource_type": "FAULT_TICKET", "resource_id": ticket_id},
        )
    return loaded


async def submit_acceptance(
    session: AsyncSession,
    *,
    ticket_id: int | None = None,
    ticket: FaultTicket | None = None,
    payload: AcceptanceSubmitIn,
    actor: Account,
    actor_role_code: str,
    now: datetime | None = None,
) -> tuple[AcceptanceRecord, FaultTicket, ReworkOrder | None, list[AuditLog]]:
    """巡查员现场验收（A11 通过 / A12 正常返修 / A13 超阈值强进人工复核）。"""
    ts = ensure_utc(now)
    target_ticket = await _resolve_locked_ticket(session, ticket_id=ticket_id, ticket=ticket)

    if target_ticket.status != "PENDING_ACCEPTANCE":
        raise AppError(
            INVALID_STATE_TRANSITION,
            f"工单当前状态 {target_ticket.status} 不允许提交验收（仅 PENDING_ACCEPTANCE 可验收）",
            {
                "entity": "fault_tickets",
                "from_status": target_ticket.status,
                "to_status": (
                    "CLOSED" if payload.decision == "APPROVED" else "REWORK_PENDING_DISPATCH"
                ),
                "allowed_from": ["PENDING_ACCEPTANCE"],
            },
        )

    accepted_rp_ids = select(AcceptanceRecord.repair_process_id).where(
        AcceptanceRecord.ticket_id == target_ticket.id
    )
    repair_process = await session.scalar(
        select(RepairProcess)
        .where(
            RepairProcess.ticket_id == target_ticket.id,
            RepairProcess.id.notin_(accepted_rp_ids),
        )
        .order_by(RepairProcess.created_at.desc(), RepairProcess.id.desc())
        .limit(1)
    )
    if repair_process is None:
        raise AppError(
            INVALID_STATE_TRANSITION,
            "工单缺少待验收的维修过程记录",
            {"entity": "repair_processes", "ticket_id": target_ticket.id},
        )

    completed_dispatch = await session.scalar(
        select(DispatchRecord).where(
            DispatchRecord.id == repair_process.dispatch_record_id,
            DispatchRecord.status == "COMPLETED",
        )
    )
    tech_id = (
        completed_dispatch.technician_account_id
        if completed_dispatch is not None
        else repair_process.technician_account_id
    )

    if payload.decision == "APPROVED":
        acceptance = AcceptanceRecord(
            ticket_id=target_ticket.id,
            repair_process_id=repair_process.id,
            rework_order_id=None,
            inspector_account_id=actor.id,
            decision="APPROVED",
            comment=payload.comment,
            accepted_at=ts,
        )
        session.add(acceptance)
        await session.flush()

        active_rework = await session.scalar(
            select(ReworkOrder)
            .where(
                ReworkOrder.ticket_id == target_ticket.id,
                ReworkOrder.status == "PENDING_ACCEPTANCE",
            )
            .with_for_update()
        )
        if active_rework is not None:
            await transition_rework(
                session,
                active_rework,
                to_status="PASSED",
                actor_account_id=actor.id,
                action="ACCEPTANCE_APPROVED",
                now=ts,
                persist_audit=False,
            )

        snapshot_extra: dict[str, Any] = {
            "acceptance_id": acceptance.id,
            "repair_process_id": repair_process.id,
            "decision": "APPROVED",
        }
        if active_rework is not None:
            snapshot_extra["rework_order_id"] = active_rework.id
        if payload.evidence_ids:
            snapshot_extra["evidence_ids"] = list(payload.evidence_ids)

        ticket_audit = await transition_ticket(
            session,
            target_ticket,
            to_status="CLOSED",
            actor_account_id=actor.id,
            actor_username=actor.username,
            actor_role_code=actor_role_code,
            action="ACCEPTANCE_APPROVED",
            now=ts,
            snapshot_extra=snapshot_extra,
            notify=[
                ("ACCEPTANCE_APPROVED", [tech_id]),
                ("TICKET_CLOSED", [target_ticket.reporter_account_id, tech_id]),
            ],
            persist_audit=False,
        )
        return acceptance, target_ticket, active_rework, [ticket_audit]

    # ── REJECTED：7 步原子事务（A12 / A13 + C1 / C2 / C8）──
    comment_text = (payload.comment or "").strip()
    if not comment_text:
        raise AppError(
            VALIDATION_ERROR,
            "驳回返修 (REJECTED) 时必须填写非空 comment",
            {"field": "comment"},
        )

    # 步骤 2：先落行取得自增主键（避开非延迟 ck_acceptance_records_rework_linked_on_reject，
    # 待步骤 6 与 rework_order_id 同事务原子翻转为 REJECTED）
    acceptance = AcceptanceRecord(
        ticket_id=target_ticket.id,
        repair_process_id=repair_process.id,
        rework_order_id=None,
        inspector_account_id=actor.id,
        decision="APPROVED",
        comment=comment_text,
        accepted_at=ts,
    )
    session.add(acceptance)
    await session.flush()

    # 步骤 3：递增返修轮次
    target_ticket.rework_count += 1
    round_no = target_ticket.rework_count

    # 步骤 4：关闭上一张活跃返修单并立即 flush 释放 uq_rework_orders_active_ticket
    prev_rework = await session.scalar(
        select(ReworkOrder)
        .where(
            ReworkOrder.ticket_id == target_ticket.id,
            ReworkOrder.status.notin_(("PASSED", "CLOSED_BY_REVIEW", "CANCELLED")),
        )
        .with_for_update()
    )
    if prev_rework is not None:
        await transition_rework(
            session,
            prev_rework,
            to_status="CANCELLED",
            actor_account_id=actor.id,
            action="ACCEPTANCE_REJECTED",
            now=ts,
            persist_audit=False,
        )
        await session.flush()

    # 步骤 5：strictly > max_rework_rounds 才触发强制人工复核（MUT-06b）
    max_rework_rounds = await _get_max_rework_rounds(session)
    exceed = round_no > max_rework_rounds
    rework_no = await _next_rework_no(session, now=ts)
    rework = await create_rework_order(
        session,
        rework_no=rework_no,
        ticket=target_ticket,
        source_acceptance_id=acceptance.id,
        round_no=round_no,
        reject_reason=comment_text,
        requires_manual_review=exceed,
    )

    # 步骤 6：回填循环外键并原子更新 decision='REJECTED'
    acceptance.decision = "REJECTED"
    acceptance.rework_order_id = rework.id
    await session.flush()

    # 步骤 7：状态机推票 + 审计 + 通知
    region_admin_ids = await list_region_admin_ids(session, target_ticket.region_id)
    tech_and_admins = [tech_id, *region_admin_ids]
    notify_list: list[tuple[str, list[int]]] = [
        ("ACCEPTANCE_REJECTED", tech_and_admins),
        ("REWORK_CREATED", tech_and_admins),
    ]
    if exceed:
        all_admin_ids = await list_all_active_admin_ids(session)
        notify_list.append(("REWORK_MANUAL_REVIEW_REQUIRED", all_admin_ids))

    reject_snapshot: dict[str, Any] = {
        "acceptance_id": acceptance.id,
        "repair_process_id": repair_process.id,
        "decision": "REJECTED",
        "rework_order_id": rework.id,
        "rework_no": rework.rework_no,
        "round_no": round_no,
        "max_rework_rounds": max_rework_rounds,
        "exceed_threshold": exceed,
    }
    if payload.evidence_ids:
        reject_snapshot["evidence_ids"] = list(payload.evidence_ids)

    ticket_audit = await transition_ticket(
        session,
        target_ticket,
        to_status="MANUAL_REVIEW" if exceed else "REWORK_PENDING_DISPATCH",
        actor_account_id=actor.id,
        actor_username=actor.username,
        actor_role_code=actor_role_code,
        action="ACCEPTANCE_REJECTED",
        manual_review_reason="rework_threshold_exceeded" if exceed else None,
        now=ts,
        snapshot_extra=reject_snapshot,
        notify=notify_list,
        persist_audit=False,
    )

    rework_created_audit = build_audit_entry(
        action="REWORK_CREATED",
        resource_type="REWORK",
        resource_id=rework.id,
        ticket_id=target_ticket.id,
        actor_account_id=actor.id,
        actor_username=actor.username,
        actor_role_code=actor_role_code,
        snapshot={
            "rework_no": rework.rework_no,
            "round_no": round_no,
            "requires_manual_review": exceed,
            "status": rework.status,
            "source_acceptance_id": acceptance.id,
        },
    )
    audits: list[AuditLog] = [ticket_audit, rework_created_audit]

    if exceed:
        manual_review_audit = build_audit_entry(
            action="REWORK_MANUAL_REVIEW_REQUIRED",
            resource_type="FAULT_TICKET",
            resource_id=target_ticket.id,
            ticket_id=target_ticket.id,
            actor_account_id=actor.id,
            actor_username=actor.username,
            actor_role_code=actor_role_code,
            snapshot={
                "rework_order_id": rework.id,
                "rework_no": rework.rework_no,
                "round_no": round_no,
                "max_rework_rounds": max_rework_rounds,
                "manual_review_reason": "rework_threshold_exceeded",
            },
        )
        audits.append(manual_review_audit)

    return acceptance, target_ticket, rework, audits


async def review_resolve_ticket(
    session: AsyncSession,
    *,
    ticket_id: int | None = None,
    ticket: FaultTicket | None = None,
    payload: ReviewResolveIn,
    actor: Account,
    actor_role_code: str,
    now: datetime | None = None,
) -> tuple[FaultTicket, list[AuditLog]]:
    """管理员人工复核裁决三出口（A15 APPROVE_REASSIGN / A16 SPECIAL_CLOSE / A17）。"""
    ts = ensure_utc(now)
    target_ticket = await _resolve_locked_ticket(session, ticket_id=ticket_id, ticket=ticket)

    if target_ticket.status != "MANUAL_REVIEW":
        raise AppError(
            INVALID_STATE_TRANSITION,
            f"仅 MANUAL_REVIEW 状态的工单允许人工复核裁决（当前为 {target_ticket.status}）",
            {
                "entity": "fault_tickets",
                "from_status": target_ticket.status,
                "allowed_from": ["MANUAL_REVIEW"],
            },
        )

    reason_text = payload.reason.strip()
    if not reason_text:
        raise AppError(VALIDATION_ERROR, "人工复核裁决理由 reason 不能为空白", {"field": "reason"})
    if payload.resolution == "SPECIAL_CLOSE" and len(reason_text) < 10:
        raise AppError(
            VALIDATION_ERROR,
            "特批关单 (SPECIAL_CLOSE) 理由长度不少于 10 个字符",
            {"field": "reason", "min_length": 10, "actual_length": len(reason_text)},
        )

    active_rework = await session.scalar(
        select(ReworkOrder)
        .where(
            ReworkOrder.ticket_id == target_ticket.id,
            ReworkOrder.status.notin_(("PASSED", "CLOSED_BY_REVIEW", "CANCELLED")),
        )
        .with_for_update()
    )

    lingering_dispatches = list(
        (
            await session.scalars(
                select(DispatchRecord)
                .where(
                    DispatchRecord.ticket_id == target_ticket.id,
                    DispatchRecord.status.in_(("PENDING_ACCEPT", "ACCEPTED")),
                )
                .with_for_update()
            )
        ).all()
    )
    for disp in lingering_dispatches:
        await transition_dispatch(
            session,
            disp,
            to_status="CANCELLED",
            actor_account_id=actor.id,
            action="DISPATCH_CANCELLED",
            reason=reason_text,
            now=ts,
            persist_audit=False,
        )
        disp.accepted_at = None
    if lingering_dispatches:
        await session.flush()

    region_admin_ids = await list_region_admin_ids(session, target_ticket.region_id)

    if payload.resolution == "APPROVE_REASSIGN":
        rework_snapshot: dict[str, Any] = {}
        if active_rework is not None and active_rework.status == "IN_MANUAL_REVIEW":
            await transition_rework(
                session,
                active_rework,
                to_status="CLOSED_BY_REVIEW",
                actor_account_id=actor.id,
                action="MANUAL_REVIEW_RESOLVED",
                decision="APPROVE_REASSIGN",
                note=reason_text,
                now=ts,
                persist_audit=False,
            )
            rework_snapshot["rework_order_id"] = active_rework.id

        ticket_audit = await transition_ticket(
            session,
            target_ticket,
            to_status="PENDING_DISPATCH",
            actor_account_id=actor.id,
            actor_username=actor.username,
            actor_role_code=actor_role_code,
            action="MANUAL_REVIEW_RESOLVED",
            now=ts,
            snapshot_extra={
                "resolution": "APPROVE_REASSIGN",
                "reason": reason_text,
                **rework_snapshot,
            },
            notify=[("TICKET_CONFIRMED", region_admin_ids)],
            persist_audit=False,
        )
        return target_ticket, [ticket_audit]

    if payload.resolution == "SPECIAL_CLOSE":
        rework_snapshot = {}
        if active_rework is not None and active_rework.status == "IN_MANUAL_REVIEW":
            await transition_rework(
                session,
                active_rework,
                to_status="CLOSED_BY_REVIEW",
                actor_account_id=actor.id,
                action="MANUAL_REVIEW_RESOLVED",
                decision="SPECIAL_CLOSE",
                note=reason_text,
                now=ts,
                persist_audit=False,
            )
            rework_snapshot["rework_order_id"] = active_rework.id

        ticket_audit = await transition_ticket(
            session,
            target_ticket,
            to_status="CLOSED",
            actor_account_id=actor.id,
            actor_username=actor.username,
            actor_role_code=actor_role_code,
            action="MANUAL_REVIEW_RESOLVED",
            now=ts,
            snapshot_extra={
                "resolution": "SPECIAL_CLOSE",
                "reason": reason_text,
                **rework_snapshot,
            },
            notify=[("TICKET_CLOSED", [target_ticket.reporter_account_id, *region_admin_ids])],
            persist_audit=False,
        )
        return target_ticket, [ticket_audit]

    # TERMINATE_CANCEL (A17 + C10)
    rework_snapshot = {}
    if active_rework is not None and active_rework.status == "IN_MANUAL_REVIEW":
        await transition_rework(
            session,
            active_rework,
            to_status="CANCELLED",
            actor_account_id=actor.id,
            action="MANUAL_REVIEW_RESOLVED",
            decision="TERMINATE_CANCEL",
            note=reason_text,
            now=ts,
            persist_audit=False,
        )
        rework_snapshot["rework_order_id"] = active_rework.id

    ticket_audit = await transition_ticket(
        session,
        target_ticket,
        to_status="CANCELLED",
        actor_account_id=actor.id,
        actor_username=actor.username,
        actor_role_code=actor_role_code,
        action="MANUAL_REVIEW_RESOLVED",
        cancel_reason=reason_text,
        now=ts,
        snapshot_extra={
            "resolution": "TERMINATE_CANCEL",
            "reason": reason_text,
            **rework_snapshot,
        },
        notify=[("TICKET_CANCELLED", [target_ticket.reporter_account_id, *region_admin_ids])],
        persist_audit=False,
    )
    cancel_audit = build_audit_entry(
        action="TICKET_CANCELLED",
        resource_type="FAULT_TICKET",
        resource_id=target_ticket.id,
        ticket_id=target_ticket.id,
        actor_account_id=actor.id,
        actor_username=actor.username,
        actor_role_code=actor_role_code,
        snapshot={
            "from_status": "MANUAL_REVIEW",
            "to_status": "CANCELLED",
            "resolution": "TERMINATE_CANCEL",
            "cancel_reason": reason_text,
            **rework_snapshot,
        },
    )
    return target_ticket, [ticket_audit, cancel_audit]
