"""验收、返修单、人工复核、通知任务、操作审计与 SLA 扫描路由（12 个端点，docs/API.md §2.5）。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tomori.api.deps import (
    CurrentPrincipal,
    RequestContext,
    ensure_region_scope,
    get_current_principal,
    get_request_context,
    require_roles,
)
from tomori.api.idempotency import IdempotentOutcome, hash_json_payload, run_idempotent
from tomori.db.session import get_session
from tomori.models.acceptance_record import AcceptanceRecord
from tomori.models.audit_log import AuditLog
from tomori.models.fault_ticket import FaultTicket
from tomori.models.notification_task import NotificationTask
from tomori.models.rework_order import ReworkOrder
from tomori.schemas.common import AcceptanceDecision, PaginatedResponse, ReworkStatus
from tomori.schemas.operations import (
    AcceptanceRecordOut,
    AcceptanceResponseOut,
    AcceptanceSubmitIn,
    AuditLogOut,
    NotificationTaskOut,
    ReviewResolveIn,
    ReworkOrderOut,
    SlaScanResponseOut,
)
from tomori.schemas.ticket import FaultTicketOut
from tomori.services.auth import get_authorized_region_ids
from tomori.services.exceptions import (
    FORBIDDEN_ROLE,
    INVALID_STATE_TRANSITION,
    RESOURCE_NOT_FOUND,
    AppError,
)
from tomori.services.rework import review_resolve_ticket, submit_acceptance
from tomori.services.sla import run_sla_scan
from tomori.services.state_machine import transition_notification
from tomori.services.ticket_service import ticket_to_out

router = APIRouter(tags=["operations"])


# 1. POST /tickets/{id}/acceptance (幂等 #11)
@router.post(
    "/tickets/{ticket_id}/acceptance",
    response_model=AcceptanceResponseOut,
    summary="巡查员验收通过或驳回（同事务创建返修单 / 超阈值强制人工复核）",
)
async def submit_acceptance_route(
    ticket_id: int,
    payload: AcceptanceSubmitIn,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: CurrentPrincipal = Depends(require_roles("INSPECTOR", "ADMIN")),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
    ctx: RequestContext = Depends(get_request_context),  # noqa: B008
) -> JSONResponse:
    async def _handler(tx: AsyncSession) -> IdempotentOutcome:
        ticket = await tx.scalar(
            select(FaultTicket).where(FaultTicket.id == ticket_id).with_for_update()
        )
        if ticket is None:
            raise AppError(
                RESOURCE_NOT_FOUND,
                "故障工单不存在",
                {"resource_type": "FAULT_TICKET", "resource_id": ticket_id},
            )
        await ensure_region_scope(
            tx,
            ctx=ctx,
            principal=principal,
            region_id=ticket.region_id,
            scope_type="INSPECT",
            allow_admin_bypass=True,
            resource_type="ACCEPTANCE",
            resource_id=ticket.id,
            ticket_id=ticket.id,
        )
        acceptance, updated_ticket, rework, audits = await submit_acceptance(
            tx,
            ticket=ticket,
            payload=payload,
            actor=principal.account,
            actor_role_code=principal.role_code,
        )
        await tx.flush()
        await tx.refresh(updated_ticket)
        if rework is not None:
            await tx.refresh(rework)
        resp_model = AcceptanceResponseOut(
            acceptance_id=acceptance.id,
            decision=acceptance.decision,  # type: ignore[arg-type]
            ticket=ticket_to_out(updated_ticket),
            rework_order=ReworkOrderOut.model_validate(rework) if rework is not None else None,
        )
        primary_audit = audits[0]
        return IdempotentOutcome(
            status_code=200,
            body=resp_model.model_dump(mode="json"),
            action=primary_audit.action,
            resource_type="ACCEPTANCE",
            resource_id=acceptance.id,
            ticket_id=updated_ticket.id,
            business_snapshot_json=primary_audit.business_snapshot_json,
            sanitized_request_payload=payload.model_dump(mode="json"),
            extra_audits=audits[1:],
        )

    return await run_idempotent(
        session,
        actor=principal.account,
        actor_role_code=principal.role_code,
        method=ctx.method,
        path=ctx.path,
        idempotency_key=idempotency_key,
        payload_hash=hash_json_payload(payload.model_dump(mode="json")),
        request_id=ctx.request_id,
        client_ip=ctx.client_ip,
        user_agent=ctx.user_agent,
        handler=_handler,
    )


# 2. GET /acceptances
@router.get(
    "/acceptances",
    response_model=PaginatedResponse[AcceptanceRecordOut],
    summary="查询验收记录（按区域授权过滤）",
)
async def list_acceptances(
    ticket_id: int | None = Query(default=None, gt=0),
    decision: AcceptanceDecision | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    principal: CurrentPrincipal = Depends(get_current_principal),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> PaginatedResponse[AcceptanceRecordOut]:
    stmt = select(AcceptanceRecord).join(FaultTicket, FaultTicket.id == AcceptanceRecord.ticket_id)
    if principal.role_code != "ADMIN":
        # 验收记录属巡查域：须持该区域 INSPECT 或 MANAGE 有效授权
        allowed = await get_authorized_region_ids(
            session, principal.account.id, scope_types={"INSPECT", "MANAGE"}
        )
        if not allowed:
            return PaginatedResponse[AcceptanceRecordOut](
                items=[], total=0, page=page, page_size=page_size
            )
        stmt = stmt.where(FaultTicket.region_id.in_(allowed))
    if ticket_id is not None:
        stmt = stmt.where(AcceptanceRecord.ticket_id == ticket_id)
    if decision is not None:
        stmt = stmt.where(AcceptanceRecord.decision == decision)

    total = (await session.scalar(select(func.count()).select_from(stmt.subquery()))) or 0
    rows = (
        await session.scalars(
            stmt.order_by(AcceptanceRecord.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    return PaginatedResponse[AcceptanceRecordOut](
        items=[AcceptanceRecordOut.model_validate(r) for r in rows],
        total=int(total),
        page=page,
        page_size=page_size,
    )


# 3. GET /rework-orders
@router.get(
    "/rework-orders",
    response_model=PaginatedResponse[ReworkOrderOut],
    summary="查询返修单列表（按区域授权过滤）",
)
async def list_rework_orders(
    rework_status: ReworkStatus | None = Query(default=None, alias="status"),
    ticket_id: int | None = Query(default=None, gt=0),
    requires_manual_review: bool | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    principal: CurrentPrincipal = Depends(get_current_principal),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> PaginatedResponse[ReworkOrderOut]:
    stmt = select(ReworkOrder)
    if principal.role_code != "ADMIN":
        # 返修单跨巡查/维修两域：按角色映射类型 + MANAGE 兜底
        required_types = (
            {"INSPECT", "MANAGE"} if principal.role_code == "INSPECTOR" else {"REPAIR", "MANAGE"}
        )
        allowed = await get_authorized_region_ids(
            session, principal.account.id, scope_types=required_types
        )
        if not allowed:
            return PaginatedResponse[ReworkOrderOut](
                items=[], total=0, page=page, page_size=page_size
            )
        stmt = stmt.where(ReworkOrder.region_id.in_(allowed))
    if rework_status is not None:
        stmt = stmt.where(ReworkOrder.status == rework_status)
    if ticket_id is not None:
        stmt = stmt.where(ReworkOrder.ticket_id == ticket_id)
    if requires_manual_review is not None:
        stmt = stmt.where(ReworkOrder.requires_manual_review.is_(requires_manual_review))

    total = (await session.scalar(select(func.count()).select_from(stmt.subquery()))) or 0
    rows = (
        await session.scalars(
            stmt.order_by(ReworkOrder.id.desc()).offset((page - 1) * page_size).limit(page_size)
        )
    ).all()
    return PaginatedResponse[ReworkOrderOut](
        items=[ReworkOrderOut.model_validate(r) for r in rows],
        total=int(total),
        page=page,
        page_size=page_size,
    )


# 4. GET /rework-orders/{id}
@router.get(
    "/rework-orders/{rework_order_id}",
    response_model=ReworkOrderOut,
    summary="查询单个返修单详情",
)
async def get_rework_order(
    rework_order_id: int,
    principal: CurrentPrincipal = Depends(get_current_principal),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
    ctx: RequestContext = Depends(get_request_context),  # noqa: B008
) -> ReworkOrderOut:
    rework = await session.scalar(select(ReworkOrder).where(ReworkOrder.id == rework_order_id))
    if rework is None:
        raise AppError(
            RESOURCE_NOT_FOUND,
            "返修单不存在",
            {"resource_type": "REWORK", "resource_id": rework_order_id},
        )
    if principal.role_code != "ADMIN":
        # 类型守卫不可被任意类型授权绕过：须持本区角色对应类型或 MANAGE
        required = "INSPECT" if principal.role_code == "INSPECTOR" else "REPAIR"
        allowed = await get_authorized_region_ids(
            session, principal.account.id, scope_types={required, "MANAGE"}
        )
        if rework.region_id not in allowed:
            await ensure_region_scope(
                session,
                ctx=ctx,
                principal=principal,
                region_id=rework.region_id,
                scope_type=required,
                resource_type="REWORK",
                resource_id=rework.id,
                ticket_id=rework.ticket_id,
            )
    return ReworkOrderOut.model_validate(rework)


# 5. POST /tickets/{id}/review-resolve (幂等 #12)
@router.post(
    "/tickets/{ticket_id}/review-resolve",
    response_model=FaultTicketOut,
    summary="管理员人工复核裁决三出口（APPROVE_REASSIGN / SPECIAL_CLOSE / TERMINATE_CANCEL）",
)
async def review_resolve_route(
    ticket_id: int,
    payload: ReviewResolveIn,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: CurrentPrincipal = Depends(require_roles("ADMIN")),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
    ctx: RequestContext = Depends(get_request_context),  # noqa: B008
) -> JSONResponse:
    async def _handler(tx: AsyncSession) -> IdempotentOutcome:
        ticket = await tx.scalar(
            select(FaultTicket).where(FaultTicket.id == ticket_id).with_for_update()
        )
        if ticket is None:
            raise AppError(
                RESOURCE_NOT_FOUND,
                "故障工单不存在",
                {"resource_type": "FAULT_TICKET", "resource_id": ticket_id},
            )
        updated, audits = await review_resolve_ticket(
            tx,
            ticket=ticket,
            payload=payload,
            actor=principal.account,
            actor_role_code=principal.role_code,
        )
        await tx.flush()
        await tx.refresh(updated)
        out = ticket_to_out(updated)
        primary_audit = audits[0]
        return IdempotentOutcome(
            status_code=200,
            body=out.model_dump(mode="json"),
            action=primary_audit.action,
            resource_type="FAULT_TICKET",
            resource_id=updated.id,
            ticket_id=updated.id,
            business_snapshot_json=primary_audit.business_snapshot_json,
            sanitized_request_payload=payload.model_dump(mode="json"),
            extra_audits=audits[1:],
        )

    return await run_idempotent(
        session,
        actor=principal.account,
        actor_role_code=principal.role_code,
        method=ctx.method,
        path=ctx.path,
        idempotency_key=idempotency_key,
        payload_hash=hash_json_payload(payload.model_dump(mode="json")),
        request_id=ctx.request_id,
        client_ip=ctx.client_ip,
        user_agent=ctx.user_agent,
        handler=_handler,
    )


# 6. GET /notifications
@router.get(
    "/notifications",
    response_model=PaginatedResponse[NotificationTaskOut],
    summary="查询当前用户的站内通知列表",
)
async def list_notifications(
    is_read: bool | None = Query(default=None),
    event_type: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    principal: CurrentPrincipal = Depends(get_current_principal),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> PaginatedResponse[NotificationTaskOut]:
    stmt = select(NotificationTask).where(
        NotificationTask.recipient_account_id == principal.account.id
    )
    if is_read is not None:
        stmt = stmt.where(NotificationTask.is_read.is_(is_read))
    if event_type and event_type.strip():
        stmt = stmt.where(NotificationTask.event_type == event_type.strip())

    total = (await session.scalar(select(func.count()).select_from(stmt.subquery()))) or 0
    rows = (
        await session.scalars(
            stmt.order_by(NotificationTask.created_at.desc(), NotificationTask.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    return PaginatedResponse[NotificationTaskOut](
        items=[NotificationTaskOut.model_validate(r) for r in rows],
        total=int(total),
        page=page,
        page_size=page_size,
    )


# 7. POST /notifications/{id}/read
@router.post(
    "/notifications/{notification_id}/read",
    response_model=NotificationTaskOut,
    summary="将个人站内通知标记为已读",
)
async def mark_notification_read(
    notification_id: int,
    principal: CurrentPrincipal = Depends(get_current_principal),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> NotificationTaskOut:
    task = await session.scalar(
        select(NotificationTask).where(NotificationTask.id == notification_id)
    )
    if task is None:
        raise AppError(
            RESOURCE_NOT_FOUND,
            "通知任务不存在",
            {"resource_type": "NOTIFICATION", "resource_id": notification_id},
        )
    if task.recipient_account_id != principal.account.id and principal.role_code != "ADMIN":
        raise AppError(FORBIDDEN_ROLE, "无权操作他人通知")

    if not task.is_read:
        task.is_read = True
        task.read_at = datetime.now(UTC)
        await session.commit()
        await session.refresh(task)
    return NotificationTaskOut.model_validate(task)


# 8. GET /notifications/dead-letters
@router.get(
    "/notifications/dead-letters",
    response_model=PaginatedResponse[NotificationTaskOut],
    summary="查询进入死信队列的通知任务（仅 ADMIN）",
)
async def list_dead_letter_notifications(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    _principal: CurrentPrincipal = Depends(require_roles("ADMIN")),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> PaginatedResponse[NotificationTaskOut]:
    stmt = select(NotificationTask).where(NotificationTask.status == "DEAD_LETTER")
    total = (await session.scalar(select(func.count()).select_from(stmt.subquery()))) or 0
    rows = (
        await session.scalars(
            stmt.order_by(NotificationTask.created_at.desc(), NotificationTask.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    return PaginatedResponse[NotificationTaskOut](
        items=[NotificationTaskOut.model_validate(r) for r in rows],
        total=int(total),
        page=page,
        page_size=page_size,
    )


# 9. POST /notifications/{id}/retry
@router.post(
    "/notifications/{notification_id}/retry",
    response_model=NotificationTaskOut,
    summary="重投死信通知任务（DEAD_LETTER -> PENDING，仅 ADMIN）",
)
async def retry_dead_letter_notification(
    notification_id: int,
    principal: CurrentPrincipal = Depends(require_roles("ADMIN")),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> NotificationTaskOut:
    task = await session.scalar(
        select(NotificationTask).where(NotificationTask.id == notification_id).with_for_update()
    )
    if task is None:
        raise AppError(
            RESOURCE_NOT_FOUND,
            "通知任务不存在",
            {"resource_type": "NOTIFICATION", "resource_id": notification_id},
        )
    # HTTP 契约仅允许 DEAD_LETTER->PENDING；PROCESSING->PENDING 归 worker 自愈、
    # FAILED->PENDING 归自动重试，均不经此端点（D7）。
    if task.status != "DEAD_LETTER":
        raise AppError(
            INVALID_STATE_TRANSITION,
            f"仅 DEAD_LETTER 通知任务可重试，当前为 {task.status}",
            {
                "entity": "notification_tasks",
                "from_status": task.status,
                "to_status": "PENDING",
            },
        )
    await transition_notification(
        session,
        task,
        to_status="PENDING",
        actor_account_id=principal.account.id,
        action="NOTIFICATION_RETRIED",
        persist_audit=True,
    )
    await session.commit()
    await session.refresh(task)
    return NotificationTaskOut.model_validate(task)


# 10. POST /notifications/{id}/acknowledge
@router.post(
    "/notifications/{notification_id}/acknowledge",
    response_model=NotificationTaskOut,
    summary="人工确认死信通知已阅处（DEAD_LETTER -> SENT，仅 ADMIN）",
)
async def acknowledge_dead_letter_notification(
    notification_id: int,
    principal: CurrentPrincipal = Depends(require_roles("ADMIN")),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> NotificationTaskOut:
    task = await session.scalar(
        select(NotificationTask).where(NotificationTask.id == notification_id).with_for_update()
    )
    if task is None:
        raise AppError(
            RESOURCE_NOT_FOUND,
            "通知任务不存在",
            {"resource_type": "NOTIFICATION", "resource_id": notification_id},
        )
    await transition_notification(
        session,
        task,
        to_status="SENT",
        actor_account_id=principal.account.id,
        action="NOTIFICATION_ACKNOWLEDGED",
        persist_audit=True,
    )
    await session.commit()
    await session.refresh(task)
    return NotificationTaskOut.model_validate(task)


# 11. GET /audit-logs
@router.get(
    "/audit-logs",
    response_model=PaginatedResponse[AuditLogOut],
    summary="多维检索全量操作审计日志（仅 ADMIN）",
)
async def list_audit_logs(
    actor_account_id: int | None = Query(default=None, gt=0),
    action: str | None = Query(default=None),
    resource_type: str | None = Query(default=None),
    resource_id: str | None = Query(default=None),
    ticket_id: int | None = Query(default=None, gt=0),
    created_from: datetime | None = Query(default=None),
    created_to: datetime | None = Query(default=None),
    response_status: int | None = Query(default=None, ge=100, le=599),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    _principal: CurrentPrincipal = Depends(require_roles("ADMIN")),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> PaginatedResponse[AuditLogOut]:
    stmt = select(AuditLog)
    if actor_account_id is not None:
        stmt = stmt.where(AuditLog.actor_account_id == actor_account_id)
    if action and action.strip():
        stmt = stmt.where(AuditLog.action == action.strip())
    if resource_type and resource_type.strip():
        stmt = stmt.where(AuditLog.resource_type == resource_type.strip())
    if resource_id and resource_id.strip().isdigit():
        stmt = stmt.where(AuditLog.resource_id == int(resource_id.strip()))
    if ticket_id is not None:
        stmt = stmt.where(AuditLog.ticket_id == ticket_id)
    if created_from is not None:
        stmt = stmt.where(AuditLog.created_at >= created_from)
    if created_to is not None:
        stmt = stmt.where(AuditLog.created_at <= created_to)
    if response_status is not None:
        stmt = stmt.where(AuditLog.response_status == response_status)

    total = (await session.scalar(select(func.count()).select_from(stmt.subquery()))) or 0
    rows = (
        await session.scalars(
            stmt.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    return PaginatedResponse[AuditLogOut](
        items=[AuditLogOut.model_validate(r) for r in rows],
        total=int(total),
        page=page,
        page_size=page_size,
    )


# 12. POST /sla/scan
@router.post(
    "/sla/scan",
    response_model=SlaScanResponseOut,
    summary="手动触发三段幂等 SLA 超时扫描（含 MANUAL_REVIEW 与超时派工回收，仅 ADMIN）",
)
async def trigger_sla_scan(
    principal: CurrentPrincipal = Depends(require_roles("ADMIN")),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
    ctx: RequestContext = Depends(get_request_context),  # noqa: B008
) -> SlaScanResponseOut:
    try:
        req_uuid = uuid.UUID(ctx.request_id)
    except ValueError:
        req_uuid = uuid.uuid4()
    counts = await run_sla_scan(
        session,
        actor_account_id=principal.account.id,
        request_id=req_uuid,
    )
    await session.commit()
    return SlaScanResponseOut(**counts)
