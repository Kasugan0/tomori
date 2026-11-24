"""派工流转、改派、维修过程与耗材使用路由（10 个端点，docs/API.md §2.4）。"""

from __future__ import annotations

import uuid
from typing import cast

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tomori.api.deps import (
    CurrentPrincipal,
    RequestContext,
    ensure_region_scope,
    get_request_context,
    require_roles,
)
from tomori.api.idempotency import IdempotentOutcome, hash_json_payload, run_idempotent
from tomori.db.session import get_session
from tomori.models.account import Account
from tomori.models.audit_log import AuditLog
from tomori.models.dispatch_record import DispatchRecord
from tomori.models.fault_ticket import FaultTicket
from tomori.models.lighting_point import LightingPoint
from tomori.models.maintenance_region import MaintenanceRegion
from tomori.models.material_usage import MaterialUsage
from tomori.models.repair_process import RepairProcess
from tomori.schemas.common import (
    DispatchStatus,
    FaultType,
    PaginatedResponse,
    PriorityCode,
    TicketStatus,
)
from tomori.schemas.dispatch import (
    CompleteRepairIn,
    CompleteRepairResponseOut,
    DispatchEscalateIn,
    DispatchRecordOut,
    DispatchRejectIn,
    MaterialUsageOut,
    RepairProcessOut,
    RepairProcessPatchIn,
    TicketReassignIn,
)
from tomori.services.dispatch import (
    accept_dispatch,
    complete_dispatch,
    escalate_dispatch,
    patch_repair_process,
    reassign_ticket,
    reject_dispatch,
)
from tomori.services.exceptions import FORBIDDEN_ROLE, RESOURCE_NOT_FOUND, AppError
from tomori.services.ticket_service import ticket_to_out

router = APIRouter(tags=["dispatches"])


async def _enrich_dispatch_out(
    session: AsyncSession,
    dispatch: DispatchRecord,
    *,
    ticket: FaultTicket | None = None,
) -> DispatchRecordOut:
    t = ticket or await session.scalar(
        select(FaultTicket).where(FaultTicket.id == dispatch.ticket_id)
    )
    tech = await session.scalar(select(Account).where(Account.id == dispatch.technician_account_id))
    reg_name = await session.scalar(
        select(MaintenanceRegion.name).where(MaintenanceRegion.id == dispatch.region_id)
    )
    pt = (
        await session.scalar(select(LightingPoint).where(LightingPoint.id == t.lighting_point_id))
        if t is not None
        else None
    )
    await session.flush()
    await session.refresh(dispatch)
    out = DispatchRecordOut.model_validate(dispatch)
    if t is not None:
        out.ticket_no = t.ticket_no
        out.ticket_status = cast(TicketStatus, t.status)
        out.ticket_priority = cast("PriorityCode | None", t.priority)
        out.ticket_fault_type = cast(FaultType, t.fault_type)
        out.ticket_title = t.title
        out.repair_deadline_at = t.repair_deadline_at
    if pt is not None:
        out.point_code = pt.point_code
        out.point_name = pt.name
    out.region_name = reg_name
    if tech is not None:
        out.technician_username = tech.username
        out.technician_full_name = tech.full_name
    return out


async def _build_repair_process_out(
    session: AsyncSession,
    rp: RepairProcess,
) -> RepairProcessOut:
    await session.flush()
    await session.refresh(rp)
    mats = (
        await session.scalars(
            select(MaterialUsage)
            .where(MaterialUsage.repair_process_id == rp.id)
            .order_by(MaterialUsage.id.asc())
        )
    ).all()
    out = RepairProcessOut.model_validate(rp)
    out.materials = [
        MaterialUsageOut(
            id=m.id,
            repair_process_id=m.repair_process_id,
            ticket_id=m.ticket_id,
            material_code=m.material_code,
            material_name=m.material_name,
            quantity=float(m.quantity),
            unit=m.unit,  # type: ignore[arg-type]
            unit_price=float(m.unit_price) if m.unit_price is not None else None,
            created_at=m.created_at,
        )
        for m in mats
    ]
    return out


# 1. POST /tickets/{id}/reassign (幂等 #7)
@router.post(
    "/tickets/{ticket_id}/reassign",
    response_model=DispatchRecordOut,
    summary="管理员改派 / 升级改派（仅 ADMIN）",
)
async def reassign_ticket_route(
    ticket_id: int,
    payload: TicketReassignIn,
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
        await ensure_region_scope(
            tx,
            ctx=ctx,
            principal=principal,
            region_id=ticket.region_id,
            scope_type="MANAGE",
            allow_admin_bypass=True,
            resource_type="FAULT_TICKET",
            resource_id=ticket.id,
            ticket_id=ticket.id,
        )
        new_disp, updated_ticket, audits = await reassign_ticket(
            tx,
            ticket=ticket,
            payload=payload,
            actor=principal.account,
            actor_role_code=principal.role_code,
        )
        d_out = await _enrich_dispatch_out(tx, new_disp, ticket=updated_ticket)
        primary_audit = audits[0]
        return IdempotentOutcome(
            status_code=200,
            body=d_out.model_dump(mode="json"),
            action=primary_audit.action,
            resource_type="DISPATCH",
            resource_id=new_disp.id,
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


# 2. GET /dispatches
@router.get(
    "/dispatches",
    response_model=PaginatedResponse[DispatchRecordOut],
    summary="查询派工记录（维修员看自己，管理员看全量或筛选）",
)
async def list_dispatches(
    dispatch_status: DispatchStatus | None = Query(default=None, alias="status"),
    ticket_id: int | None = Query(default=None, gt=0),
    technician_account_id: int | None = Query(default=None, gt=0),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    principal: CurrentPrincipal = Depends(require_roles("ADMIN", "TECHNICIAN")),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> PaginatedResponse[DispatchRecordOut]:
    stmt = (
        select(
            DispatchRecord,
            FaultTicket,
            MaintenanceRegion.name,
            LightingPoint.point_code,
            LightingPoint.name,
            Account.username,
            Account.full_name,
        )
        .join(FaultTicket, FaultTicket.id == DispatchRecord.ticket_id)
        .join(MaintenanceRegion, MaintenanceRegion.id == DispatchRecord.region_id)
        .join(LightingPoint, LightingPoint.id == FaultTicket.lighting_point_id)
        .join(Account, Account.id == DispatchRecord.technician_account_id)
    )
    if principal.role_code == "TECHNICIAN":
        stmt = stmt.where(DispatchRecord.technician_account_id == principal.account.id)
    elif technician_account_id is not None:
        stmt = stmt.where(DispatchRecord.technician_account_id == technician_account_id)

    if dispatch_status is not None:
        stmt = stmt.where(DispatchRecord.status == dispatch_status)
    if ticket_id is not None:
        stmt = stmt.where(DispatchRecord.ticket_id == ticket_id)

    total = (await session.scalar(select(func.count()).select_from(stmt.subquery()))) or 0
    rows = (
        await session.execute(
            stmt.order_by(DispatchRecord.id.desc()).offset((page - 1) * page_size).limit(page_size)
        )
    ).all()
    items: list[DispatchRecordOut] = []
    for disp, t, rname, pcode, pname, uname, fname in rows:
        out = DispatchRecordOut.model_validate(disp)
        out.ticket_no = t.ticket_no
        out.ticket_status = cast(TicketStatus, t.status)
        out.ticket_priority = cast("PriorityCode | None", t.priority)
        out.ticket_fault_type = cast(FaultType, t.fault_type)
        out.ticket_title = t.title
        out.repair_deadline_at = t.repair_deadline_at
        out.region_name = rname
        out.point_code = pcode
        out.point_name = pname
        out.technician_username = uname
        out.technician_full_name = fname
        items.append(out)
    return PaginatedResponse[DispatchRecordOut](
        items=items, total=int(total), page=page, page_size=page_size
    )


# 3. POST /dispatches/{id}/accept (幂等 #8)
@router.post(
    "/dispatches/{dispatch_id}/accept",
    response_model=DispatchRecordOut,
    summary="维修员接单（二次校验有效 REPAIR 授权）",
)
async def accept_dispatch_route(
    dispatch_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: CurrentPrincipal = Depends(require_roles("TECHNICIAN")),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
    ctx: RequestContext = Depends(get_request_context),  # noqa: B008
) -> JSONResponse:
    async def _handler(tx: AsyncSession) -> IdempotentOutcome:
        disp = await tx.scalar(
            select(DispatchRecord).where(DispatchRecord.id == dispatch_id).with_for_update()
        )
        if disp is None:
            raise AppError(
                RESOURCE_NOT_FOUND,
                "派工记录不存在",
                {"resource_type": "DISPATCH", "resource_id": dispatch_id},
            )
        ticket = await tx.scalar(
            select(FaultTicket).where(FaultTicket.id == disp.ticket_id).with_for_update()
        )
        if ticket is None:
            raise AppError(RESOURCE_NOT_FOUND, "关联工单不存在")
        updated_disp, updated_ticket, audits = await accept_dispatch(
            tx,
            dispatch=disp,
            ticket=ticket,
            actor=principal.account,
            actor_role_code=principal.role_code,
        )
        out = await _enrich_dispatch_out(tx, updated_disp, ticket=updated_ticket)
        primary_audit = audits[0]
        return IdempotentOutcome(
            status_code=200,
            body=out.model_dump(mode="json"),
            action=primary_audit.action,
            resource_type="DISPATCH",
            resource_id=updated_disp.id,
            ticket_id=updated_ticket.id,
            business_snapshot_json=primary_audit.business_snapshot_json,
            sanitized_request_payload={},
            extra_audits=audits[1:],
        )

    return await run_idempotent(
        session,
        actor=principal.account,
        actor_role_code=principal.role_code,
        method=ctx.method,
        path=ctx.path,
        idempotency_key=idempotency_key,
        payload_hash=hash_json_payload({"dispatch_id": dispatch_id, "action": "accept"}),
        request_id=ctx.request_id,
        client_ip=ctx.client_ip,
        user_agent=ctx.user_agent,
        handler=_handler,
    )


# 4. POST /dispatches/{id}/reject (幂等 #9)
@router.post(
    "/dispatches/{dispatch_id}/reject",
    response_model=DispatchRecordOut,
    summary="维修员拒单（累计 >= 3 次自动进 MANUAL_REVIEW）",
)
async def reject_dispatch_route(
    dispatch_id: int,
    payload: DispatchRejectIn,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: CurrentPrincipal = Depends(require_roles("TECHNICIAN")),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
    ctx: RequestContext = Depends(get_request_context),  # noqa: B008
) -> JSONResponse:
    async def _handler(tx: AsyncSession) -> IdempotentOutcome:
        disp = await tx.scalar(
            select(DispatchRecord).where(DispatchRecord.id == dispatch_id).with_for_update()
        )
        if disp is None:
            raise AppError(
                RESOURCE_NOT_FOUND,
                "派工记录不存在",
                {"resource_type": "DISPATCH", "resource_id": dispatch_id},
            )
        ticket = await tx.scalar(
            select(FaultTicket).where(FaultTicket.id == disp.ticket_id).with_for_update()
        )
        if ticket is None:
            raise AppError(RESOURCE_NOT_FOUND, "关联工单不存在")
        updated_disp, updated_ticket, audits = await reject_dispatch(
            tx,
            dispatch=disp,
            ticket=ticket,
            payload=payload,
            actor=principal.account,
            actor_role_code=principal.role_code,
        )
        out = await _enrich_dispatch_out(tx, updated_disp, ticket=updated_ticket)
        primary_audit = audits[0]
        return IdempotentOutcome(
            status_code=200,
            body=out.model_dump(mode="json"),
            action=primary_audit.action,
            resource_type="DISPATCH",
            resource_id=updated_disp.id,
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


# 5. POST /dispatches/{id}/escalate
@router.post(
    "/dispatches/{dispatch_id}/escalate",
    response_model=DispatchRecordOut,
    summary="维修员在修中异常升级退回人工复核（防死路）",
)
async def escalate_dispatch_route(
    dispatch_id: int,
    payload: DispatchEscalateIn,
    principal: CurrentPrincipal = Depends(require_roles("TECHNICIAN")),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> DispatchRecordOut:
    disp = await session.scalar(
        select(DispatchRecord).where(DispatchRecord.id == dispatch_id).with_for_update()
    )
    if disp is None:
        raise AppError(
            RESOURCE_NOT_FOUND,
            "派工记录不存在",
            {"resource_type": "DISPATCH", "resource_id": dispatch_id},
        )
    ticket = await session.scalar(
        select(FaultTicket).where(FaultTicket.id == disp.ticket_id).with_for_update()
    )
    if ticket is None:
        raise AppError(RESOURCE_NOT_FOUND, "关联工单不存在")

    updated_disp, updated_ticket = await escalate_dispatch(
        session,
        dispatch=disp,
        ticket=ticket,
        escalate_reason=payload.escalate_reason,
        actor=principal.account,
        actor_role_code=principal.role_code,
    )
    out = await _enrich_dispatch_out(session, updated_disp, ticket=updated_ticket)
    await session.commit()
    return out


# 6. POST /dispatches/{id}/complete (幂等 #10)
@router.post(
    "/dispatches/{dispatch_id}/complete",
    response_model=CompleteRepairResponseOut,
    summary="维修员提交完工（强制六件套电气安全检查 + 写维修过程与耗材）",
)
async def complete_dispatch_route(
    dispatch_id: int,
    payload: CompleteRepairIn,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: CurrentPrincipal = Depends(require_roles("TECHNICIAN")),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
    ctx: RequestContext = Depends(get_request_context),  # noqa: B008
) -> JSONResponse:
    async def _handler(tx: AsyncSession) -> IdempotentOutcome:
        disp = await tx.scalar(
            select(DispatchRecord).where(DispatchRecord.id == dispatch_id).with_for_update()
        )
        if disp is None:
            raise AppError(
                RESOURCE_NOT_FOUND,
                "派工记录不存在",
                {"resource_type": "DISPATCH", "resource_id": dispatch_id},
            )
        ticket = await tx.scalar(
            select(FaultTicket).where(FaultTicket.id == disp.ticket_id).with_for_update()
        )
        if ticket is None:
            raise AppError(RESOURCE_NOT_FOUND, "关联工单不存在")
        updated_disp, updated_ticket, repair, mats, audits = await complete_dispatch(
            tx,
            dispatch=disp,
            ticket=ticket,
            payload=payload,
            actor=principal.account,
            actor_role_code=principal.role_code,
        )
        await tx.flush()
        await tx.refresh(updated_ticket)
        d_out = await _enrich_dispatch_out(tx, updated_disp, ticket=updated_ticket)
        t_out = ticket_to_out(updated_ticket)
        resp_body = CompleteRepairResponseOut(
            dispatch=d_out,
            ticket=t_out,
            repair_process_id=repair.id,
            material_usage_ids=[m.id for m in mats],
        ).model_dump(mode="json")
        # 兼容 stage1-backend-spec-api.md:266 中的历史别名字段
        resp_body["dispatcher"] = resp_body["dispatch"]
        primary_audit = audits[0]
        return IdempotentOutcome(
            status_code=200,
            body=resp_body,
            action=primary_audit.action,
            resource_type="REPAIR",
            resource_id=repair.id,
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


# 7. GET /repair-processes
@router.get(
    "/repair-processes",
    response_model=PaginatedResponse[RepairProcessOut],
    summary="查询维修过程记录列表（ADMIN 或本人 TECHNICIAN）",
)
async def list_repair_processes(
    ticket_id: int | None = Query(default=None, gt=0),
    technician_account_id: int | None = Query(default=None, gt=0),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    principal: CurrentPrincipal = Depends(require_roles("ADMIN", "TECHNICIAN")),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> PaginatedResponse[RepairProcessOut]:
    stmt = select(RepairProcess)
    if principal.role_code == "TECHNICIAN":
        stmt = stmt.where(RepairProcess.technician_account_id == principal.account.id)
    elif technician_account_id is not None:
        stmt = stmt.where(RepairProcess.technician_account_id == technician_account_id)
    if ticket_id is not None:
        stmt = stmt.where(RepairProcess.ticket_id == ticket_id)

    total = (await session.scalar(select(func.count()).select_from(stmt.subquery()))) or 0
    rows = (
        await session.scalars(
            stmt.order_by(RepairProcess.id.desc()).offset((page - 1) * page_size).limit(page_size)
        )
    ).all()
    items = [await _build_repair_process_out(session, rp) for rp in rows]
    return PaginatedResponse[RepairProcessOut](
        items=items, total=int(total), page=page, page_size=page_size
    )


# 8. GET /repair-processes/{id}
@router.get(
    "/repair-processes/{repair_process_id}",
    response_model=RepairProcessOut,
    summary="获取维修过程详情（含材料清单）",
)
async def get_repair_process(
    repair_process_id: int,
    principal: CurrentPrincipal = Depends(require_roles("ADMIN", "TECHNICIAN")),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> RepairProcessOut:
    rp = await session.scalar(select(RepairProcess).where(RepairProcess.id == repair_process_id))
    if rp is None:
        raise AppError(
            RESOURCE_NOT_FOUND,
            "维修过程记录不存在",
            {"resource_type": "REPAIR", "resource_id": repair_process_id},
        )
    if principal.role_code != "ADMIN" and rp.technician_account_id != principal.account.id:
        raise AppError(FORBIDDEN_ROLE, "仅 ADMIN 或本人维修人员可查看此维修过程")
    return await _build_repair_process_out(session, rp)


# 9. PATCH /repair-processes/{id}
@router.patch(
    "/repair-processes/{repair_process_id}",
    response_model=RepairProcessOut,
    summary="在修期间更新维修过程与全量覆盖式材料清单",
)
async def patch_repair_process_route(
    repair_process_id: int,
    payload: RepairProcessPatchIn,
    principal: CurrentPrincipal = Depends(require_roles("ADMIN", "TECHNICIAN")),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
    ctx: RequestContext = Depends(get_request_context),  # noqa: B008
) -> RepairProcessOut:
    rp = await session.scalar(
        select(RepairProcess).where(RepairProcess.id == repair_process_id).with_for_update()
    )
    if rp is None:
        raise AppError(
            RESOURCE_NOT_FOUND,
            "维修过程记录不存在",
            {"resource_type": "REPAIR", "resource_id": repair_process_id},
        )
    ticket = await session.scalar(
        select(FaultTicket).where(FaultTicket.id == rp.ticket_id).with_for_update()
    )
    if ticket is None:
        raise AppError(RESOURCE_NOT_FOUND, "关联工单不存在")

    updated = await patch_repair_process(
        session,
        repair=rp,
        ticket=ticket,
        payload=payload,
        actor=principal.account,
        actor_role_code=principal.role_code,
    )
    try:
        req_uuid = uuid.UUID(ctx.request_id)
    except ValueError:
        req_uuid = uuid.uuid4()
    session.add(
        AuditLog(
            request_id=req_uuid,
            actor_account_id=principal.account.id,
            actor_username=principal.account.username,
            actor_role_code=principal.role_code,
            action="REPAIR_COMPLETED",
            resource_type="REPAIR",
            resource_id=updated.id,
            ticket_id=ticket.id,
            method=ctx.method,
            path=ctx.path[:256],
            response_status=200,
            business_snapshot_json={"patched": True},
            client_ip=ctx.client_ip,
            user_agent=ctx.user_agent[:256] if ctx.user_agent else None,
        )
    )
    await session.commit()
    await session.refresh(updated)
    return await _build_repair_process_out(session, updated)


# 10. GET /material-usages
@router.get(
    "/material-usages",
    response_model=PaginatedResponse[MaterialUsageOut],
    summary="查询维修材料耗材明细（ADMIN 或本人 TECHNICIAN）",
)
async def list_material_usages(
    ticket_id: int | None = Query(default=None, gt=0),
    repair_process_id: int | None = Query(default=None, gt=0),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    principal: CurrentPrincipal = Depends(require_roles("ADMIN", "TECHNICIAN")),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> PaginatedResponse[MaterialUsageOut]:
    stmt = select(MaterialUsage).join(
        RepairProcess, RepairProcess.id == MaterialUsage.repair_process_id
    )
    if principal.role_code == "TECHNICIAN":
        stmt = stmt.where(RepairProcess.technician_account_id == principal.account.id)
    if ticket_id is not None:
        stmt = stmt.where(MaterialUsage.ticket_id == ticket_id)
    if repair_process_id is not None:
        stmt = stmt.where(MaterialUsage.repair_process_id == repair_process_id)

    total = (await session.scalar(select(func.count()).select_from(stmt.subquery()))) or 0
    rows = (
        await session.scalars(
            stmt.order_by(MaterialUsage.id.desc()).offset((page - 1) * page_size).limit(page_size)
        )
    ).all()
    items = [
        MaterialUsageOut(
            id=m.id,
            repair_process_id=m.repair_process_id,
            ticket_id=m.ticket_id,
            material_code=m.material_code,
            material_name=m.material_name,
            quantity=float(m.quantity),
            unit=m.unit,  # type: ignore[arg-type]
            unit_price=float(m.unit_price) if m.unit_price is not None else None,
            created_at=m.created_at,
        )
        for m in rows
    ]
    return PaginatedResponse[MaterialUsageOut](
        items=items, total=int(total), page=page, page_size=page_size
    )
