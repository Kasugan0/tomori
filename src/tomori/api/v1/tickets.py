"""故障单、AI 候选识别、静态证据读取与派工触发路由（14 个端点，docs/API.md §2.3）。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, Query, Request, status
from fastapi.responses import FileResponse, JSONResponse
from pydantic import ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from tomori.api.deps import (
    CurrentPrincipal,
    RequestContext,
    ensure_region_scope,
    get_current_principal,
    get_request_context,
    require_roles,
)
from tomori.api.idempotency import (
    IdempotentOutcome,
    hash_json_payload,
    hash_multipart_payload,
    run_idempotent,
)
from tomori.db.session import get_session
from tomori.models.dispatch_record import DispatchRecord
from tomori.models.fault_evidence import FaultEvidence
from tomori.models.fault_ticket import FaultTicket
from tomori.models.lighting_point import LightingPoint
from tomori.models.maintenance_region import MaintenanceRegion
from tomori.schemas.common import FaultType, PaginatedResponse, PriorityCode, TicketStatus
from tomori.schemas.dispatch import (
    DispatchCandidatesResponseOut,
    DispatchRecordOut,
    TicketDispatchIn,
)
from tomori.schemas.ticket import (
    AiCandidatesResponseOut,
    FaultEvidenceOut,
    FaultTicketDetailOut,
    FaultTicketOut,
    TicketCancelIn,
    TicketConfirmIn,
    TicketCreateJsonIn,
    TicketRegradeIn,
    TicketUpdateDraftIn,
)
from tomori.services.auth import get_authorized_region_ids
from tomori.services.dispatch import compute_dispatch_candidates, dispatch_ticket
from tomori.services.exceptions import (
    EVIDENCE_NOT_ANNOTATED,
    RESOURCE_NOT_FOUND,
    VALIDATION_ERROR,
    AppError,
)
from tomori.services.media import (
    MediaPathTraversalError,
    reject_stream_fields,
    resolve_evidence_paths,
    validate_and_normalize_image,
    validate_upload_count,
)
from tomori.services.ticket_service import (
    analyze_ai_candidates_flow,
    build_ai_evidence_out,
    build_ticket_detail,
    cancel_ticket_flow,
    confirm_ticket_flow,
    create_manual_ticket_flow,
    evidence_to_out,
    patch_ticket_draft_flow,
    regrade_ticket_flow,
    ticket_to_out,
    upload_ticket_evidences_flow,
)
from tomori.settings import get_settings

router = APIRouter(tags=["tickets"])


async def _parse_optional_int(raw: Any, field_name: str) -> int | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, int):
        return raw
    try:
        return int(str(raw).strip())
    except ValueError as exc:
        raise AppError(
            VALIDATION_ERROR, f"字段 {field_name} 必须为整数", {"field": field_name}
        ) from exc


async def _enrich_ticket_out(session: AsyncSession, ticket: FaultTicket) -> FaultTicketOut:
    # `fault_tickets.updated_at` 是 `onupdate=func.now()` 的服务端表达式，UPDATE 后该属性
    # 处于 expired 状态；异步上下文内必须 flush 后显式 refresh（refresh 会丢弃未 flush 的
    # 挂起修改），否则同步序列化会触发隐式 IO 或读到旧状态。
    await session.flush()
    await session.refresh(ticket)
    reg_name = await session.scalar(
        select(MaintenanceRegion.name).where(MaintenanceRegion.id == ticket.region_id)
    )
    pt = await session.scalar(
        select(LightingPoint).where(LightingPoint.id == ticket.lighting_point_id)
    )
    return ticket_to_out(
        ticket,
        region_name=reg_name,
        point_code=pt.point_code if pt else None,
        point_name=pt.name if pt else None,
    )


async def _check_ticket_read_access(
    session: AsyncSession,
    *,
    ctx: RequestContext,
    principal: CurrentPrincipal,
    ticket: FaultTicket,
) -> None:
    if principal.role_code == "ADMIN":
        return
    if principal.role_code == "INSPECTOR":
        await ensure_region_scope(
            session,
            ctx=ctx,
            principal=principal,
            region_id=ticket.region_id,
            scope_type="INSPECT",
            resource_type="FAULT_TICKET",
            resource_id=ticket.id,
            ticket_id=ticket.id,
        )
        return
    # TECHNICIAN：本人被派工的工单或拥有该区 REPAIR 授权
    assigned = await session.scalar(
        select(DispatchRecord.id).where(
            DispatchRecord.ticket_id == ticket.id,
            DispatchRecord.technician_account_id == principal.account.id,
        )
    )
    if assigned is not None:
        return
    await ensure_region_scope(
        session,
        ctx=ctx,
        principal=principal,
        region_id=ticket.region_id,
        scope_type="REPAIR",
        resource_type="FAULT_TICKET",
        resource_id=ticket.id,
        ticket_id=ticket.id,
    )


# 1. POST /tickets/ai-candidates (幂等 #1)
@router.post(
    "/tickets/ai-candidates",
    response_model=AiCandidatesResponseOut,
    summary="巡查图片上传与 AI 故障候选生成（12 步顺序，含 D1–D3 降级与 D-30 参照回传）",
)
async def create_ai_candidates(
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: CurrentPrincipal = Depends(require_roles("INSPECTOR", "ADMIN")),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
    ctx: RequestContext = Depends(get_request_context),  # noqa: B008
) -> JSONResponse:
    settings = get_settings()
    form = await request.form()
    reject_stream_fields(form)

    raw_files: list[tuple[bytes, str | None, str | None]] = []
    file_digests: list[tuple[str, str, int]] = []
    for item in form.getlist("files"):
        if isinstance(item, str):
            reject_stream_fields({"files": item})
            raise AppError(VALIDATION_ERROR, "files 字段必须为文件上传项")
        raw = await item.read()
        fname = item.filename
        ctype = item.content_type
        raw_files.append((raw, fname, ctype))
        file_digests.append((fname or "upload", hashlib.sha256(raw).hexdigest(), len(raw)))

    # 先执行 Step 2–6 纯内存白名单校验（不落盘、不依赖视觉），再进咨询锁与推理
    validate_upload_count(len(raw_files), min_count=1, max_count=10)
    for idx, (raw, fname, ctype) in enumerate(raw_files):
        validate_and_normalize_image(
            raw,
            filename=fname,
            content_type=ctype,
            file_index=idx,
            max_bytes=settings.max_upload_size_bytes,
        )

    lighting_point_id = await _parse_optional_int(
        form.get("lighting_point_id"), "lighting_point_id"
    )
    region_id = await _parse_optional_int(form.get("region_id"), "region_id")
    scene_hint = str(form.get("scene_lighting_hint") or "auto").strip() or "auto"
    if scene_hint not in {"auto", "day", "night", "twilight"}:
        raise AppError(
            VALIDATION_ERROR,
            "scene_lighting_hint 仅支持 auto/day/night/twilight",
            {"field": "scene_lighting_hint"},
        )

    payload_hash = hash_multipart_payload(
        fields={
            "lighting_point_id": lighting_point_id,
            "region_id": region_id,
            "scene_lighting_hint": scene_hint,
        },
        file_digests=file_digests,
    )

    async def _ensure_inspect(target_region_id: int) -> None:
        await ensure_region_scope(
            session,
            ctx=ctx,
            principal=principal,
            region_id=target_region_id,
            scope_type="INSPECT",
            allow_admin_bypass=True,
            resource_type="FAULT_TICKET",
        )

    async def _handler(tx: AsyncSession) -> IdempotentOutcome:
        ticket, evidences, audits = await analyze_ai_candidates_flow(
            tx,
            raw_files=raw_files,
            lighting_point_id=lighting_point_id,
            region_id=region_id,
            scene_lighting_hint=scene_hint,
            actor=principal.account,
            actor_role_code=principal.role_code,
            has_inspect_scope_fn=_ensure_inspect,
            upload_dir=settings.upload_dir,
            max_upload_bytes=settings.max_upload_size_bytes,
        )
        ticket_out = await _enrich_ticket_out(tx, ticket) if ticket is not None else None
        evs_out = [build_ai_evidence_out(ev, settings.upload_dir) for ev in evidences]
        resp_model = AiCandidatesResponseOut(ticket=ticket_out, evidences=evs_out)
        primary_audit = audits[0]
        status_code = 201 if ticket is not None else 200
        return IdempotentOutcome(
            status_code=status_code,
            body=resp_model.model_dump(mode="json"),
            action=primary_audit.action,
            resource_type=primary_audit.resource_type,
            resource_id=primary_audit.resource_id,
            ticket_id=primary_audit.ticket_id,
            business_snapshot_json=primary_audit.business_snapshot_json,
            sanitized_request_payload={
                "lighting_point_id": lighting_point_id,
                "region_id": region_id,
                "scene_lighting_hint": scene_hint,
                "file_count": len(raw_files),
            },
            location=f"/api/v1/tickets/{ticket.id}" if ticket is not None else None,
            extra_audits=audits[1:],
        )

    return await run_idempotent(
        session,
        actor=principal.account,
        actor_role_code=principal.role_code,
        method=ctx.method,
        path=ctx.path,
        idempotency_key=idempotency_key,
        payload_hash=payload_hash,
        request_id=ctx.request_id,
        client_ip=ctx.client_ip,
        user_agent=ctx.user_agent,
        handler=_handler,
    )


# 2. POST /tickets (幂等 #2，支持 JSON 与 multipart/form-data)
@router.post(
    "/tickets",
    response_model=FaultTicketOut,
    status_code=status.HTTP_201_CREATED,
    summary="人工报障（D1 永不依赖视觉，支持 JSON 或 multipart 0..10 张照片）",
)
async def create_manual_ticket(
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: CurrentPrincipal = Depends(require_roles("INSPECTOR", "ADMIN")),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
    ctx: RequestContext = Depends(get_request_context),  # noqa: B008
) -> JSONResponse:
    settings = get_settings()
    ctype = (request.headers.get("content-type") or "").lower()
    raw_files: list[tuple[bytes, str | None, str | None]] = []
    file_digests: list[tuple[str, str, int]] = []

    if "multipart/form-data" in ctype:
        form = await request.form()
        reject_stream_fields(form)
        for item in form.getlist("files"):
            if isinstance(item, str):
                reject_stream_fields({"files": item})
                raise AppError(VALIDATION_ERROR, "files 字段必须为文件上传项")
            raw = await item.read()
            raw_files.append((raw, item.filename, item.content_type))
            file_digests.append(
                (item.filename or "upload", hashlib.sha256(raw).hexdigest(), len(raw))
            )

        ev_ids_raw = form.getlist("evidence_ids")
        parsed_ev_ids: list[int] = []
        for entry in ev_ids_raw:
            s = str(entry).strip()
            if not s:
                continue
            if s.startswith("["):
                loaded = json.loads(s)
                if isinstance(loaded, list):
                    parsed_ev_ids.extend(int(x) for x in loaded)
            else:
                parsed_ev_ids.extend(int(x) for x in s.split(",") if x.strip())

        raw_body: dict[str, Any] = {
            "lighting_point_id": await _parse_optional_int(
                form.get("lighting_point_id"), "lighting_point_id"
            ),
            "facility_asset_id": await _parse_optional_int(
                form.get("facility_asset_id"), "facility_asset_id"
            ),
            "fault_type": form.get("fault_type"),
            "title": form.get("title"),
            "description": form.get("description"),
            "safety_flag": str(form.get("safety_flag") or "false").lower() == "true",
            "manual_note": form.get("manual_note"),
            "evidence_ids": parsed_ev_ids,
        }
        try:
            payload = TicketCreateJsonIn.model_validate(raw_body)
        except ValidationError as exc:
            raise AppError(
                VALIDATION_ERROR,
                "人工报障参数校验失败",
                {
                    "errors": [
                        {"loc": [str(p) for p in e.get("loc", ())], "msg": str(e.get("msg", ""))}
                        for e in exc.errors()
                    ]
                },
            ) from exc
        payload_hash = hash_multipart_payload(
            fields=payload.model_dump(mode="json"),
            file_digests=file_digests,
        )
    else:
        try:
            body_json = await request.json()
        except Exception as exc:
            raise AppError(VALIDATION_ERROR, "请求体必须为合法 JSON") from exc
        if isinstance(body_json, dict):
            reject_stream_fields(body_json)
        try:
            payload = TicketCreateJsonIn.model_validate(body_json)
        except ValidationError as exc:
            raise AppError(
                VALIDATION_ERROR,
                "人工报障参数校验失败",
                {
                    "errors": [
                        {"loc": [str(p) for p in e.get("loc", ())], "msg": str(e.get("msg", ""))}
                        for e in exc.errors()
                    ]
                },
            ) from exc
        payload_hash = hash_json_payload(payload.model_dump(mode="json"))

    async def _handler(tx: AsyncSession) -> IdempotentOutcome:
        pt_region_id = await tx.scalar(
            select(LightingPoint.region_id).where(LightingPoint.id == payload.lighting_point_id)
        )
        if pt_region_id is None:
            raise AppError(
                RESOURCE_NOT_FOUND,
                "照明点位不存在",
                {"resource_type": "LIGHTING_POINT", "resource_id": payload.lighting_point_id},
            )
        await ensure_region_scope(
            tx,
            ctx=ctx,
            principal=principal,
            region_id=pt_region_id,
            scope_type="INSPECT",
            allow_admin_bypass=True,
            resource_type="FAULT_TICKET",
        )
        ticket, audits = await create_manual_ticket_flow(
            tx,
            lighting_point_id=payload.lighting_point_id,
            facility_asset_id=payload.facility_asset_id,
            fault_type=payload.fault_type,
            title=payload.title,
            description=payload.description,
            safety_flag=payload.safety_flag,
            manual_note=payload.manual_note,
            evidence_ids=payload.evidence_ids,
            raw_files=raw_files,
            actor=principal.account,
            actor_role_code=principal.role_code,
            upload_dir=settings.upload_dir,
            max_upload_bytes=settings.max_upload_size_bytes,
        )
        out = await _enrich_ticket_out(tx, ticket)
        primary_audit = audits[0]
        return IdempotentOutcome(
            status_code=201,
            body=out.model_dump(mode="json"),
            action=primary_audit.action,
            resource_type=primary_audit.resource_type,
            resource_id=ticket.id,
            ticket_id=ticket.id,
            business_snapshot_json=primary_audit.business_snapshot_json,
            sanitized_request_payload=payload.model_dump(mode="json"),
            location=f"/api/v1/tickets/{ticket.id}",
            extra_audits=audits[1:],
        )

    return await run_idempotent(
        session,
        actor=principal.account,
        actor_role_code=principal.role_code,
        method=ctx.method,
        path=ctx.path,
        idempotency_key=idempotency_key,
        payload_hash=payload_hash,
        request_id=ctx.request_id,
        client_ip=ctx.client_ip,
        user_agent=ctx.user_agent,
        handler=_handler,
    )


# 3. GET /tickets
@router.get(
    "/tickets",
    response_model=PaginatedResponse[FaultTicketOut],
    summary="故障单列表（按角色与区域权限自动隔离）",
)
async def list_tickets(
    ticket_status: TicketStatus | None = Query(default=None, alias="status"),
    priority: PriorityCode | None = Query(default=None),
    fault_type: FaultType | None = Query(default=None),
    region_id: int | None = Query(default=None, gt=0),
    lighting_point_id: int | None = Query(default=None, gt=0),
    reporter_account_id: int | None = Query(default=None, gt=0),
    technician_account_id: int | None = Query(default=None, gt=0),
    is_sla_overdue: bool | None = Query(default=None),
    keyword: str | None = Query(default=None),
    created_from: datetime | None = Query(default=None),
    created_to: datetime | None = Query(default=None),
    sort: str = Query(default="created_at"),
    order: str = Query(default="desc"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    principal: CurrentPrincipal = Depends(get_current_principal),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> PaginatedResponse[FaultTicketOut]:
    stmt = (
        select(
            FaultTicket,
            MaintenanceRegion.name,
            LightingPoint.point_code,
            LightingPoint.name,
        )
        .join(MaintenanceRegion, MaintenanceRegion.id == FaultTicket.region_id)
        .join(LightingPoint, LightingPoint.id == FaultTicket.lighting_point_id)
    )
    if principal.role_code != "ADMIN":
        scope_type = "INSPECT" if principal.role_code == "INSPECTOR" else "REPAIR"
        allowed_regions = await get_authorized_region_ids(
            session, principal.account.id, scope_type=scope_type
        )
        if principal.role_code == "TECHNICIAN":
            assigned_tickets = select(DispatchRecord.ticket_id).where(
                DispatchRecord.technician_account_id == principal.account.id
            )
            if allowed_regions:
                stmt = stmt.where(
                    or_(
                        FaultTicket.region_id.in_(allowed_regions),
                        FaultTicket.id.in_(assigned_tickets),
                    )
                )
            else:
                stmt = stmt.where(FaultTicket.id.in_(assigned_tickets))
        else:
            if not allowed_regions:
                return PaginatedResponse[FaultTicketOut](
                    items=[], total=0, page=page, page_size=page_size
                )
            stmt = stmt.where(FaultTicket.region_id.in_(allowed_regions))

    if ticket_status is not None:
        stmt = stmt.where(FaultTicket.status == ticket_status)
    if priority is not None:
        stmt = stmt.where(FaultTicket.priority == priority)
    if fault_type is not None:
        stmt = stmt.where(FaultTicket.fault_type == fault_type)
    if region_id is not None:
        stmt = stmt.where(FaultTicket.region_id == region_id)
    if lighting_point_id is not None:
        stmt = stmt.where(FaultTicket.lighting_point_id == lighting_point_id)
    if reporter_account_id is not None:
        stmt = stmt.where(FaultTicket.reporter_account_id == reporter_account_id)
    if technician_account_id is not None:
        tech_tickets = select(DispatchRecord.ticket_id).where(
            DispatchRecord.technician_account_id == technician_account_id
        )
        stmt = stmt.where(FaultTicket.id.in_(tech_tickets))
    if is_sla_overdue is not None:
        stmt = stmt.where(FaultTicket.is_sla_overdue.is_(is_sla_overdue))
    if created_from is not None:
        stmt = stmt.where(FaultTicket.created_at >= created_from)
    if created_to is not None:
        stmt = stmt.where(FaultTicket.created_at <= created_to)
    if keyword and keyword.strip():
        kw = f"%{keyword.strip()}%"
        stmt = stmt.where(
            or_(
                FaultTicket.ticket_no.ilike(kw),
                FaultTicket.title.ilike(kw),
                LightingPoint.point_code.ilike(kw),
                LightingPoint.name.ilike(kw),
            )
        )

    total = (await session.scalar(select(func.count()).select_from(stmt.subquery()))) or 0
    sort_col = {
        "created_at": FaultTicket.created_at,
        "updated_at": FaultTicket.updated_at,
        "repair_deadline_at": FaultTicket.repair_deadline_at,
        "priority": FaultTicket.priority,
        "id": FaultTicket.id,
    }.get(sort, FaultTicket.created_at)
    order_expr = sort_col.asc() if order.lower() == "asc" else sort_col.desc()
    rows = (
        await session.execute(
            stmt.order_by(order_expr, FaultTicket.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    items = [
        ticket_to_out(t, region_name=rname, point_code=pcode, point_name=pname)
        for t, rname, pcode, pname in rows
    ]
    return PaginatedResponse[FaultTicketOut](
        items=items, total=int(total), page=page, page_size=page_size
    )


# 4. GET /tickets/{id}
@router.get("/tickets/{ticket_id}", response_model=FaultTicketDetailOut, summary="工单详情")
async def get_ticket_detail(
    ticket_id: int,
    principal: CurrentPrincipal = Depends(get_current_principal),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
    ctx: RequestContext = Depends(get_request_context),  # noqa: B008
) -> FaultTicketDetailOut:
    ticket = await session.scalar(select(FaultTicket).where(FaultTicket.id == ticket_id))
    if ticket is None:
        raise AppError(
            RESOURCE_NOT_FOUND,
            "故障工单不存在",
            {"resource_type": "FAULT_TICKET", "resource_id": ticket_id},
        )
    await _check_ticket_read_access(session, ctx=ctx, principal=principal, ticket=ticket)
    settings = get_settings()
    return await build_ticket_detail(session, ticket_id=ticket.id, upload_dir=settings.upload_dir)


# 5. PATCH /tickets/{id}
@router.patch(
    "/tickets/{ticket_id}",
    response_model=FaultTicketOut,
    summary="编辑 AI 待确认草稿（仅 AI_CANDIDATE_PENDING）",
)
async def patch_ticket_draft(
    ticket_id: int,
    payload: TicketUpdateDraftIn,
    principal: CurrentPrincipal = Depends(require_roles("INSPECTOR", "ADMIN")),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
    ctx: RequestContext = Depends(get_request_context),  # noqa: B008
) -> FaultTicketOut:
    ticket = await session.scalar(
        select(FaultTicket).where(FaultTicket.id == ticket_id).with_for_update()
    )
    if ticket is None:
        raise AppError(
            RESOURCE_NOT_FOUND,
            "故障工单不存在",
            {"resource_type": "FAULT_TICKET", "resource_id": ticket_id},
        )
    await ensure_region_scope(
        session,
        ctx=ctx,
        principal=principal,
        region_id=ticket.region_id,
        scope_type="INSPECT",
        allow_admin_bypass=True,
        resource_type="FAULT_TICKET",
        resource_id=ticket.id,
        ticket_id=ticket.id,
    )
    await patch_ticket_draft_flow(session, ticket=ticket, payload=payload)
    await session.commit()
    await session.refresh(ticket)
    return await _enrich_ticket_out(session, ticket)


# 6. POST /tickets/{id}/confirm (幂等 #3)
@router.post(
    "/tickets/{ticket_id}/confirm",
    response_model=FaultTicketOut,
    summary="巡查员人工确认 AI 候选并自动分级/写入 48h 时限",
)
async def confirm_ticket(
    ticket_id: int,
    payload: TicketConfirmIn,
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
            resource_type="FAULT_TICKET",
            resource_id=ticket.id,
            ticket_id=ticket.id,
        )
        updated, audits = await confirm_ticket_flow(
            tx,
            ticket=ticket,
            payload=payload,
            actor=principal.account,
            actor_role_code=principal.role_code,
        )
        out = await _enrich_ticket_out(tx, updated)
        primary_audit = audits[0]
        return IdempotentOutcome(
            status_code=200,
            body=out.model_dump(mode="json"),
            action=primary_audit.action,
            resource_type=primary_audit.resource_type,
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


# 7. POST /tickets/{id}/cancel (幂等 #4)
@router.post(
    "/tickets/{ticket_id}/cancel",
    response_model=FaultTicketOut,
    summary="撤销误报/待派工工单（仅 AI_CANDIDATE_PENDING / PENDING_DISPATCH）",
)
async def cancel_ticket(
    ticket_id: int,
    payload: TicketCancelIn,
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
            resource_type="FAULT_TICKET",
            resource_id=ticket.id,
            ticket_id=ticket.id,
        )
        updated, audits = await cancel_ticket_flow(
            tx,
            ticket=ticket,
            payload=payload,
            actor=principal.account,
            actor_role_code=principal.role_code,
        )
        out = await _enrich_ticket_out(tx, updated)
        primary_audit = audits[0]
        return IdempotentOutcome(
            status_code=200,
            body=out.model_dump(mode="json"),
            action=primary_audit.action,
            resource_type=primary_audit.resource_type,
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


# 8. POST /tickets/{id}/regrade (幂等 #5)
@router.post(
    "/tickets/{ticket_id}/regrade",
    response_model=FaultTicketOut,
    summary="管理员手工调级（不重置/顺延法定 48h 时限）",
)
async def regrade_ticket(
    ticket_id: int,
    payload: TicketRegradeIn,
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
        updated, audits = await regrade_ticket_flow(
            tx,
            ticket=ticket,
            payload=payload,
            actor=principal.account,
            actor_role_code=principal.role_code,
        )
        out = await _enrich_ticket_out(tx, updated)
        primary_audit = audits[0]
        return IdempotentOutcome(
            status_code=200,
            body=out.model_dump(mode="json"),
            action=primary_audit.action,
            resource_type=primary_audit.resource_type,
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


# 9. GET /tickets/{id}/evidences
@router.get(
    "/tickets/{ticket_id}/evidences",
    response_model=list[FaultEvidenceOut],
    summary="查询工单关联全部证据列表",
)
async def list_ticket_evidences(
    ticket_id: int,
    principal: CurrentPrincipal = Depends(get_current_principal),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
    ctx: RequestContext = Depends(get_request_context),  # noqa: B008
) -> list[FaultEvidenceOut]:
    ticket = await session.scalar(select(FaultTicket).where(FaultTicket.id == ticket_id))
    if ticket is None:
        raise AppError(
            RESOURCE_NOT_FOUND,
            "故障工单不存在",
            {"resource_type": "FAULT_TICKET", "resource_id": ticket_id},
        )
    await _check_ticket_read_access(session, ctx=ctx, principal=principal, ticket=ticket)
    settings = get_settings()
    evs = (
        await session.scalars(
            select(FaultEvidence)
            .where(FaultEvidence.ticket_id == ticket.id)
            .order_by(FaultEvidence.id.asc())
        )
    ).all()
    return [evidence_to_out(ev, settings.upload_dir) for ev in evs]


# 10. POST /tickets/{id}/evidences
@router.post(
    "/tickets/{ticket_id}/evidences",
    response_model=list[FaultEvidenceOut],
    status_code=status.HTTP_201_CREATED,
    summary="为工单补传静态照片证据（巡查/维修/验收）",
)
async def upload_ticket_evidences(
    ticket_id: int,
    request: Request,
    principal: CurrentPrincipal = Depends(get_current_principal),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
    ctx: RequestContext = Depends(get_request_context),  # noqa: B008
) -> list[FaultEvidenceOut]:
    ticket = await session.scalar(select(FaultTicket).where(FaultTicket.id == ticket_id))
    if ticket is None:
        raise AppError(
            RESOURCE_NOT_FOUND,
            "故障工单不存在",
            {"resource_type": "FAULT_TICKET", "resource_id": ticket_id},
        )
    await _check_ticket_read_access(session, ctx=ctx, principal=principal, ticket=ticket)

    settings = get_settings()
    form = await request.form()
    reject_stream_fields(form)
    ev_type = str(form.get("evidence_type") or "REPAIR_PHOTO").strip()
    if ev_type not in {
        "INSPECTION_PHOTO",
        "ANNOTATED_PHOTO",
        "REPAIR_PHOTO",
        "ACCEPTANCE_PHOTO",
    }:
        raise AppError(VALIDATION_ERROR, "evidence_type 不合法", {"field": "evidence_type"})

    raw_files: list[tuple[bytes, str | None, str | None]] = []
    for item in form.getlist("files"):
        if isinstance(item, str):
            reject_stream_fields({"files": item})
            raise AppError(VALIDATION_ERROR, "files 字段必须为文件上传项")
        raw = await item.read()
        raw_files.append((raw, item.filename, item.content_type))

    evs = await upload_ticket_evidences_flow(
        session,
        ticket=ticket,
        raw_files=raw_files,
        evidence_type=ev_type,
        actor=principal.account,
        upload_dir=settings.upload_dir,
        max_upload_bytes=settings.max_upload_size_bytes,
    )
    out = [evidence_to_out(ev, settings.upload_dir) for ev in evs]
    await session.commit()
    return out


async def _serve_evidence_file(
    *,
    evidence_id: int,
    annotated: bool,
    principal: CurrentPrincipal,
    session: AsyncSession,
    ctx: RequestContext,
) -> FileResponse:
    ev = await session.scalar(select(FaultEvidence).where(FaultEvidence.id == evidence_id))
    if ev is None:
        raise AppError(
            RESOURCE_NOT_FOUND,
            "证据图片不存在",
            {"resource_type": "FAULT_EVIDENCE", "resource_id": evidence_id},
        )
    if ev.ticket_id is not None:
        ticket = await session.scalar(select(FaultTicket).where(FaultTicket.id == ev.ticket_id))
        if ticket is not None:
            await _check_ticket_read_access(session, ctx=ctx, principal=principal, ticket=ticket)
    elif principal.role_code != "ADMIN" and ev.uploaded_by_account_id != principal.account.id:
        await ensure_region_scope(
            session,
            ctx=ctx,
            principal=principal,
            region_id=0,
            scope_type="INSPECT",
            resource_type="FAULT_EVIDENCE",
            resource_id=ev.id,
        )

    settings = get_settings()
    try:
        raw_path, ann_path = resolve_evidence_paths(
            ev.storage_path, ev.analysis_id, settings.upload_dir
        )
    except MediaPathTraversalError as exc:
        raise AppError(VALIDATION_ERROR, "证据文件路径非法") from exc

    if annotated:
        if ann_path is None or not ann_path.is_file():
            raise AppError(
                EVIDENCE_NOT_ANNOTATED,
                "该证据尚未生成 AI 标注图",
                {"evidence_id": evidence_id},
            )
        return FileResponse(
            path=ann_path,
            media_type="image/jpeg",
            headers={"Cache-Control": "private, max-age=3600"},
        )

    if not raw_path.is_file():
        raise AppError(
            RESOURCE_NOT_FOUND,
            "原始证据文件缺失",
            {"resource_type": "FAULT_EVIDENCE", "resource_id": evidence_id},
        )
    return FileResponse(
        path=raw_path,
        media_type=ev.media_mime,
        headers={"Cache-Control": "private, max-age=3600"},
    )


# 11. GET /evidence/{id}/raw
@router.get(
    "/evidence/{evidence_id}/raw",
    summary="鉴权读取原始静态照片二进制流（供 <AuthImage> 消费）",
)
async def get_evidence_raw_file(
    evidence_id: int,
    principal: CurrentPrincipal = Depends(get_current_principal),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
    ctx: RequestContext = Depends(get_request_context),  # noqa: B008
) -> FileResponse:
    return await _serve_evidence_file(
        evidence_id=evidence_id,
        annotated=False,
        principal=principal,
        session=session,
        ctx=ctx,
    )


# 12. GET /evidence/{id}/annotated
@router.get(
    "/evidence/{evidence_id}/annotated",
    summary="鉴权读取 AI 标注图二进制流（未标注返 404 EVIDENCE_NOT_ANNOTATED）",
)
async def get_evidence_annotated_file(
    evidence_id: int,
    principal: CurrentPrincipal = Depends(get_current_principal),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
    ctx: RequestContext = Depends(get_request_context),  # noqa: B008
) -> FileResponse:
    return await _serve_evidence_file(
        evidence_id=evidence_id,
        annotated=True,
        principal=principal,
        session=session,
        ctx=ctx,
    )


# 13. GET /tickets/{id}/dispatch-candidates
@router.get(
    "/tickets/{ticket_id}/dispatch-candidates",
    response_model=DispatchCandidatesResponseOut,
    summary="获取工单区域合格维修员推荐列表与排除名单（仅 ADMIN）",
)
async def get_dispatch_candidates(
    ticket_id: int,
    principal: CurrentPrincipal = Depends(require_roles("ADMIN")),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
    ctx: RequestContext = Depends(get_request_context),  # noqa: B008
) -> DispatchCandidatesResponseOut:
    ticket = await session.scalar(select(FaultTicket).where(FaultTicket.id == ticket_id))
    if ticket is None:
        raise AppError(
            RESOURCE_NOT_FOUND,
            "故障工单不存在",
            {"resource_type": "FAULT_TICKET", "resource_id": ticket_id},
        )
    await ensure_region_scope(
        session,
        ctx=ctx,
        principal=principal,
        region_id=ticket.region_id,
        scope_type="MANAGE",
        allow_admin_bypass=True,
        resource_type="FAULT_TICKET",
        resource_id=ticket.id,
        ticket_id=ticket.id,
    )
    return await compute_dispatch_candidates(session, ticket)


# 14. POST /tickets/{id}/dispatch (幂等 #6)
@router.post(
    "/tickets/{ticket_id}/dispatch",
    response_model=DispatchRecordOut,
    status_code=status.HTTP_201_CREATED,
    summary="管理员区域派工（仅 ADMIN）",
)
async def create_dispatch(
    ticket_id: int,
    payload: TicketDispatchIn,
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
        dispatch, updated_ticket, audits = await dispatch_ticket(
            tx,
            ticket=ticket,
            payload=payload,
            actor=principal.account,
            actor_role_code=principal.role_code,
        )
        d_out = DispatchRecordOut.model_validate(dispatch)
        d_out.ticket_no = updated_ticket.ticket_no
        d_out.ticket_status = updated_ticket.status  # type: ignore[assignment]
        d_out.ticket_priority = updated_ticket.priority  # type: ignore[assignment]
        d_out.ticket_fault_type = updated_ticket.fault_type  # type: ignore[assignment]
        d_out.ticket_title = updated_ticket.title
        d_out.repair_deadline_at = updated_ticket.repair_deadline_at
        primary_audit = audits[0]
        return IdempotentOutcome(
            status_code=201,
            body=d_out.model_dump(mode="json"),
            action=primary_audit.action,
            resource_type="DISPATCH",
            resource_id=dispatch.id,
            ticket_id=updated_ticket.id,
            business_snapshot_json=primary_audit.business_snapshot_json,
            sanitized_request_payload=payload.model_dump(mode="json"),
            location=f"/api/v1/dispatches/{dispatch.id}",
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
