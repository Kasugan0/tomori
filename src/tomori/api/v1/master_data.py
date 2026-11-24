"""基础档案（运维区域 / 照明点位 / 设施资产）与版本化业务规则路由（12 个端点，docs/API.md §2.2）。

注意：业务规则仅提供 GET / POST / PATCH，严禁注册 PUT；
PATCH /business-rules/{id} 携带 rule_value 即抛 422 RULE_VALUE_IMMUTABLE。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tomori.api.deps import (
    CurrentPrincipal,
    RequestContext,
    get_current_principal,
    get_request_context,
    require_roles,
)
from tomori.db.session import get_session
from tomori.models.audit_log import AuditLog
from tomori.models.business_rule import BusinessRule
from tomori.models.facility_asset import FacilityAsset
from tomori.models.fault_ticket import FaultTicket
from tomori.models.lighting_point import LightingPoint
from tomori.models.maintenance_region import MaintenanceRegion
from tomori.schemas.common import (
    AssetImportance,
    AssetType,
    LightingPointStatus,
    PaginatedResponse,
    RuleCode,
)
from tomori.schemas.master_data import (
    BusinessRuleCreateIn,
    BusinessRuleOut,
    BusinessRulePatchIn,
    FacilityAssetCreateIn,
    FacilityAssetOut,
    FacilityAssetUpdateIn,
    LightingPointCreateIn,
    LightingPointOut,
    LightingPointUpdateIn,
    MaintenanceRegionOut,
    RegionCreateIn,
    RegionUpdateIn,
    validate_rule_value,
)
from tomori.services.auth import get_authorized_region_ids
from tomori.services.exceptions import (
    INVALID_RULE_PAYLOAD,
    POINT_HAS_ACTIVE_TICKET,
    RESOURCE_NOT_FOUND,
    RULE_VALUE_IMMUTABLE,
    VALIDATION_ERROR,
    AppError,
)

router = APIRouter(tags=["master-data"])


def _add_audit(
    session: AsyncSession,
    ctx: RequestContext,
    *,
    principal: CurrentPrincipal,
    action: str,
    resource_type: str,
    resource_id: int,
    response_status: int,
    snapshot: dict[str, object] | None = None,
) -> None:
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
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            ticket_id=None,
            method=ctx.method,
            path=ctx.path[:256],
            request_payload=None,
            request_payload_hash=None,
            idempotency_key=None,
            response_status=response_status,
            error_code=None,
            business_snapshot_json=snapshot,
            response_body_json=None,
            client_ip=ctx.client_ip,
            user_agent=ctx.user_agent[:256] if ctx.user_agent else None,
        )
    )


# 1. GET /regions
@router.get("/regions", response_model=list[MaintenanceRegionOut], summary="运维区域列表")
async def list_regions(
    flat: bool = Query(default=True),
    principal: CurrentPrincipal = Depends(get_current_principal),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[MaintenanceRegionOut]:
    _ = flat
    stmt = select(MaintenanceRegion)
    if principal.role_code != "ADMIN":
        allowed_ids = await get_authorized_region_ids(session, principal.account.id)
        if not allowed_ids:
            return []
        stmt = stmt.where(MaintenanceRegion.id.in_(allowed_ids))
    regions = (
        await session.scalars(
            stmt.order_by(MaintenanceRegion.level.asc(), MaintenanceRegion.id.asc())
        )
    ).all()
    return [MaintenanceRegionOut.model_validate(r) for r in regions]


# 2. POST /regions
@router.post(
    "/regions",
    response_model=MaintenanceRegionOut,
    status_code=status.HTTP_201_CREATED,
    summary="创建运维区域（仅 ADMIN）",
)
async def create_region(
    payload: RegionCreateIn,
    response: Response,
    principal: CurrentPrincipal = Depends(require_roles("ADMIN")),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
    ctx: RequestContext = Depends(get_request_context),  # noqa: B008
) -> MaintenanceRegionOut:
    if payload.parent_region_id is not None:
        parent = await session.scalar(
            select(MaintenanceRegion).where(MaintenanceRegion.id == payload.parent_region_id)
        )
        if parent is None:
            raise AppError(
                RESOURCE_NOT_FOUND,
                "父级区域不存在",
                {"resource_type": "REGION", "resource_id": payload.parent_region_id},
            )

    region = MaintenanceRegion(
        region_code=payload.region_code,
        name=payload.name,
        parent_region_id=payload.parent_region_id,
        level=payload.level,
        boundary_geojson=payload.boundary_geojson,
        description=payload.description,
        is_active=True,
    )
    session.add(region)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise AppError(
            VALIDATION_ERROR,
            "区域编码已存在",
            {"field": "region_code", "region_code": payload.region_code},
        ) from exc

    _add_audit(
        session,
        ctx,
        principal=principal,
        action="REGION_CREATED",
        resource_type="REGION",
        resource_id=region.id,
        response_status=201,
        snapshot={"region_code": region.region_code},
    )
    await session.commit()
    await session.refresh(region)
    response.headers["Location"] = f"/api/v1/regions/{region.id}"
    return MaintenanceRegionOut.model_validate(region)


# 3. PATCH /regions/{id}
@router.patch(
    "/regions/{region_id}",
    response_model=MaintenanceRegionOut,
    summary="更新运维区域（仅 ADMIN）",
)
async def update_region(
    region_id: int,
    payload: RegionUpdateIn,
    principal: CurrentPrincipal = Depends(require_roles("ADMIN")),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
    ctx: RequestContext = Depends(get_request_context),  # noqa: B008
) -> MaintenanceRegionOut:
    region = await session.scalar(
        select(MaintenanceRegion).where(MaintenanceRegion.id == region_id)
    )
    if region is None:
        raise AppError(
            RESOURCE_NOT_FOUND,
            "运维区域不存在",
            {"resource_type": "REGION", "resource_id": region_id},
        )
    if "parent_region_id" in payload.model_fields_set:
        if payload.parent_region_id == region_id:
            raise AppError(
                VALIDATION_ERROR, "禁止将区域自身设为父区域", {"field": "parent_region_id"}
            )
        if payload.parent_region_id is not None:
            parent = await session.scalar(
                select(MaintenanceRegion).where(MaintenanceRegion.id == payload.parent_region_id)
            )
            if parent is None:
                raise AppError(
                    RESOURCE_NOT_FOUND,
                    "父级区域不存在",
                    {"resource_type": "REGION", "resource_id": payload.parent_region_id},
                )
        region.parent_region_id = payload.parent_region_id
    if payload.name is not None:
        region.name = payload.name
    if payload.level is not None:
        region.level = payload.level
    if "boundary_geojson" in payload.model_fields_set:
        region.boundary_geojson = payload.boundary_geojson
    if "description" in payload.model_fields_set:
        region.description = payload.description
    if payload.is_active is not None:
        region.is_active = payload.is_active

    _add_audit(
        session,
        ctx,
        principal=principal,
        action="REGION_UPDATED",
        resource_type="REGION",
        resource_id=region.id,
        response_status=200,
        snapshot={"is_active": region.is_active},
    )
    await session.commit()
    await session.refresh(region)
    return MaintenanceRegionOut.model_validate(region)


# 4. GET /lighting-points
@router.get(
    "/lighting-points",
    response_model=PaginatedResponse[LightingPointOut],
    summary="查询照明点位列表",
)
async def list_lighting_points(
    region_id: int | None = Query(default=None, gt=0),
    road_name: str | None = Query(default=None),
    point_status: LightingPointStatus | None = Query(default=None, alias="status"),
    keyword: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    principal: CurrentPrincipal = Depends(get_current_principal),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> PaginatedResponse[LightingPointOut]:
    stmt = select(LightingPoint, MaintenanceRegion.name).join(
        MaintenanceRegion, MaintenanceRegion.id == LightingPoint.region_id
    )
    if principal.role_code != "ADMIN":
        allowed_ids = await get_authorized_region_ids(session, principal.account.id)
        if not allowed_ids:
            return PaginatedResponse[LightingPointOut](
                items=[], total=0, page=page, page_size=page_size
            )
        stmt = stmt.where(LightingPoint.region_id.in_(allowed_ids))
    if region_id is not None:
        stmt = stmt.where(LightingPoint.region_id == region_id)
    if road_name and road_name.strip():
        stmt = stmt.where(LightingPoint.road_name.ilike(f"%{road_name.strip()}%"))
    if point_status is not None:
        stmt = stmt.where(LightingPoint.status == point_status)
    if keyword and keyword.strip():
        kw = f"%{keyword.strip()}%"
        stmt = stmt.where(
            or_(
                LightingPoint.point_code.ilike(kw),
                LightingPoint.name.ilike(kw),
                LightingPoint.road_name.ilike(kw),
            )
        )

    total = (await session.scalar(select(func.count()).select_from(stmt.subquery()))) or 0
    rows = (
        await session.execute(
            stmt.order_by(LightingPoint.id.desc()).offset((page - 1) * page_size).limit(page_size)
        )
    ).all()
    items: list[LightingPointOut] = []
    for pt, rname in rows:
        out = LightingPointOut.model_validate(pt)
        out.region_name = rname
        items.append(out)
    return PaginatedResponse[LightingPointOut](
        items=items,
        total=int(total),
        page=page,
        page_size=page_size,
    )


# 5. POST /lighting-points
@router.post(
    "/lighting-points",
    response_model=LightingPointOut,
    status_code=status.HTTP_201_CREATED,
    summary="创建照明点位（仅 ADMIN）",
)
async def create_lighting_point(
    payload: LightingPointCreateIn,
    response: Response,
    principal: CurrentPrincipal = Depends(require_roles("ADMIN")),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
    ctx: RequestContext = Depends(get_request_context),  # noqa: B008
) -> LightingPointOut:
    region = await session.scalar(
        select(MaintenanceRegion).where(MaintenanceRegion.id == payload.region_id)
    )
    if region is None:
        raise AppError(
            RESOURCE_NOT_FOUND,
            "所属运维区域不存在",
            {"resource_type": "REGION", "resource_id": payload.region_id},
        )
    pt = LightingPoint(
        point_code=payload.point_code,
        region_id=payload.region_id,
        name=payload.name,
        road_name=payload.road_name,
        road_level=payload.road_level,
        longitude=payload.longitude,
        latitude=payload.latitude,
        install_height_m=payload.install_height_m,
        lamp_count=payload.lamp_count,
        power_supply_type=payload.power_supply_type,
        status="IN_SERVICE",
        description=payload.description,
    )
    session.add(pt)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise AppError(
            VALIDATION_ERROR,
            "点位编码已存在",
            {"field": "point_code", "point_code": payload.point_code},
        ) from exc

    _add_audit(
        session,
        ctx,
        principal=principal,
        action="LIGHTING_POINT_CREATED",
        resource_type="LIGHTING_POINT",
        resource_id=pt.id,
        response_status=201,
        snapshot={"point_code": pt.point_code, "region_id": pt.region_id},
    )
    region_name = region.name
    await session.commit()
    await session.refresh(pt)
    response.headers["Location"] = f"/api/v1/lighting-points/{pt.id}"
    out = LightingPointOut.model_validate(pt)
    out.region_name = region_name
    return out


# 6. PATCH /lighting-points/{id}
@router.patch(
    "/lighting-points/{point_id}",
    response_model=LightingPointOut,
    summary="编辑照明点位（改 region_id 时若有活跃单返回 422 POINT_HAS_ACTIVE_TICKET）",
)
async def update_lighting_point(
    point_id: int,
    payload: LightingPointUpdateIn,
    principal: CurrentPrincipal = Depends(require_roles("ADMIN")),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
    ctx: RequestContext = Depends(get_request_context),  # noqa: B008
) -> LightingPointOut:
    pt = await session.scalar(select(LightingPoint).where(LightingPoint.id == point_id))
    if pt is None:
        raise AppError(
            RESOURCE_NOT_FOUND,
            "照明点位不存在",
            {"resource_type": "LIGHTING_POINT", "resource_id": point_id},
        )

    region_changed = payload.region_id is not None and payload.region_id != pt.region_id
    if region_changed:
        assert payload.region_id is not None
        active_ticket_id = await session.scalar(
            select(FaultTicket.id).where(
                FaultTicket.lighting_point_id == pt.id,
                FaultTicket.status.notin_(("CLOSED", "CANCELLED")),
            )
        )
        if active_ticket_id is not None:
            raise AppError(
                POINT_HAS_ACTIVE_TICKET,
                "该点位尚有未关闭活跃故障单，禁止迁移所属运维区域",
                {"lighting_point_id": pt.id, "active_ticket_id": active_ticket_id},
            )
        new_region = await session.scalar(
            select(MaintenanceRegion).where(MaintenanceRegion.id == payload.region_id)
        )
        if new_region is None:
            raise AppError(
                RESOURCE_NOT_FOUND,
                "目标运维区域不存在",
                {"resource_type": "REGION", "resource_id": payload.region_id},
            )
        pt.region_id = payload.region_id

    if payload.name is not None:
        pt.name = payload.name
    if payload.road_name is not None:
        pt.road_name = payload.road_name
    if payload.road_level is not None:
        pt.road_level = payload.road_level
    if payload.longitude is not None:
        pt.longitude = payload.longitude
    if payload.latitude is not None:
        pt.latitude = payload.latitude
    if "install_height_m" in payload.model_fields_set:
        pt.install_height_m = payload.install_height_m
    if payload.lamp_count is not None:
        pt.lamp_count = payload.lamp_count
    if payload.power_supply_type is not None:
        pt.power_supply_type = payload.power_supply_type
    if payload.status is not None:
        pt.status = payload.status
    if "description" in payload.model_fields_set:
        pt.description = payload.description

    _add_audit(
        session,
        ctx,
        principal=principal,
        action="POINT_REGION_CHANGED" if region_changed else "LIGHTING_POINT_UPDATED",
        resource_type="LIGHTING_POINT",
        resource_id=pt.id,
        response_status=200,
        snapshot={"region_id": pt.region_id},
    )
    await session.commit()
    await session.refresh(pt)
    rname = await session.scalar(
        select(MaintenanceRegion.name).where(MaintenanceRegion.id == pt.region_id)
    )
    out = LightingPointOut.model_validate(pt)
    out.region_name = rname
    return out


# 7. GET /facility-assets
@router.get(
    "/facility-assets",
    response_model=PaginatedResponse[FacilityAssetOut],
    summary="查询设施资产列表",
)
async def list_facility_assets(
    lighting_point_id: int | None = Query(default=None, gt=0),
    asset_type: AssetType | None = Query(default=None),
    importance: AssetImportance | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    principal: CurrentPrincipal = Depends(get_current_principal),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> PaginatedResponse[FacilityAssetOut]:
    stmt = select(FacilityAsset, LightingPoint.point_code).join(
        LightingPoint, LightingPoint.id == FacilityAsset.lighting_point_id
    )
    if principal.role_code != "ADMIN":
        allowed_ids = await get_authorized_region_ids(session, principal.account.id)
        if not allowed_ids:
            return PaginatedResponse[FacilityAssetOut](
                items=[], total=0, page=page, page_size=page_size
            )
        stmt = stmt.where(LightingPoint.region_id.in_(allowed_ids))
    if lighting_point_id is not None:
        stmt = stmt.where(FacilityAsset.lighting_point_id == lighting_point_id)
    if asset_type is not None:
        stmt = stmt.where(FacilityAsset.asset_type == asset_type)
    if importance is not None:
        stmt = stmt.where(FacilityAsset.importance == importance)

    total = (await session.scalar(select(func.count()).select_from(stmt.subquery()))) or 0
    rows = (
        await session.execute(
            stmt.order_by(FacilityAsset.id.desc()).offset((page - 1) * page_size).limit(page_size)
        )
    ).all()
    items: list[FacilityAssetOut] = []
    for asset, pcode in rows:
        out = FacilityAssetOut.model_validate(asset)
        out.point_code = pcode
        items.append(out)
    return PaginatedResponse[FacilityAssetOut](
        items=items,
        total=int(total),
        page=page,
        page_size=page_size,
    )


# 8. POST /facility-assets
@router.post(
    "/facility-assets",
    response_model=FacilityAssetOut,
    status_code=status.HTTP_201_CREATED,
    summary="创建设施资产（仅 ADMIN）",
)
async def create_facility_asset(
    payload: FacilityAssetCreateIn,
    response: Response,
    principal: CurrentPrincipal = Depends(require_roles("ADMIN")),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
    ctx: RequestContext = Depends(get_request_context),  # noqa: B008
) -> FacilityAssetOut:
    pt = await session.scalar(
        select(LightingPoint).where(LightingPoint.id == payload.lighting_point_id)
    )
    if pt is None:
        raise AppError(
            RESOURCE_NOT_FOUND,
            "所属照明点位不存在",
            {"resource_type": "LIGHTING_POINT", "resource_id": payload.lighting_point_id},
        )
    asset = FacilityAsset(
        asset_code=payload.asset_code,
        lighting_point_id=payload.lighting_point_id,
        asset_type=payload.asset_type,
        name=payload.name,
        model_spec=payload.model_spec,
        rated_power_w=payload.rated_power_w,
        importance=payload.importance,
        installed_at=payload.installed_at,
        status="IN_SERVICE",
        description=payload.description,
    )
    session.add(asset)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise AppError(
            VALIDATION_ERROR,
            "资产编码已存在",
            {"field": "asset_code", "asset_code": payload.asset_code},
        ) from exc

    _add_audit(
        session,
        ctx,
        principal=principal,
        action="FACILITY_ASSET_CREATED",
        resource_type="FACILITY_ASSET",
        resource_id=asset.id,
        response_status=201,
        snapshot={"asset_code": asset.asset_code, "importance": asset.importance},
    )
    point_code = pt.point_code
    await session.commit()
    await session.refresh(asset)
    response.headers["Location"] = f"/api/v1/facility-assets/{asset.id}"
    out = FacilityAssetOut.model_validate(asset)
    out.point_code = point_code
    return out


# 9. PATCH /facility-assets/{id}
@router.patch(
    "/facility-assets/{asset_id}",
    response_model=FacilityAssetOut,
    summary="编辑设施资产（仅 ADMIN）",
)
async def update_facility_asset(
    asset_id: int,
    payload: FacilityAssetUpdateIn,
    principal: CurrentPrincipal = Depends(require_roles("ADMIN")),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
    ctx: RequestContext = Depends(get_request_context),  # noqa: B008
) -> FacilityAssetOut:
    asset = await session.scalar(select(FacilityAsset).where(FacilityAsset.id == asset_id))
    if asset is None:
        raise AppError(
            RESOURCE_NOT_FOUND,
            "设施资产不存在",
            {"resource_type": "FACILITY_ASSET", "resource_id": asset_id},
        )
    if payload.name is not None:
        asset.name = payload.name
    if payload.asset_type is not None:
        asset.asset_type = payload.asset_type
    if "model_spec" in payload.model_fields_set:
        asset.model_spec = payload.model_spec
    if "rated_power_w" in payload.model_fields_set:
        asset.rated_power_w = payload.rated_power_w
    if payload.importance is not None:
        asset.importance = payload.importance
    if payload.status is not None:
        asset.status = payload.status
    if "description" in payload.model_fields_set:
        asset.description = payload.description

    _add_audit(
        session,
        ctx,
        principal=principal,
        action="FACILITY_ASSET_UPDATED",
        resource_type="FACILITY_ASSET",
        resource_id=asset.id,
        response_status=200,
        snapshot={"importance": asset.importance, "status": asset.status},
    )
    await session.commit()
    await session.refresh(asset)
    pcode = await session.scalar(
        select(LightingPoint.point_code).where(LightingPoint.id == asset.lighting_point_id)
    )
    out = FacilityAssetOut.model_validate(asset)
    out.point_code = pcode
    return out


# 10. GET /business-rules
@router.get(
    "/business-rules",
    response_model=PaginatedResponse[BusinessRuleOut],
    summary="查询业务规则版本列表（仅 ADMIN）",
)
async def list_business_rules(
    rule_code: RuleCode | None = Query(default=None),
    is_active: bool | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    _principal: CurrentPrincipal = Depends(require_roles("ADMIN")),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> PaginatedResponse[BusinessRuleOut]:
    stmt = select(BusinessRule)
    if rule_code is not None:
        stmt = stmt.where(BusinessRule.rule_code == rule_code)
    if is_active is not None:
        stmt = stmt.where(BusinessRule.is_active.is_(is_active))
    total = (await session.scalar(select(func.count()).select_from(stmt.subquery()))) or 0
    rows = (
        await session.scalars(
            stmt.order_by(BusinessRule.rule_code.asc(), BusinessRule.version.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    return PaginatedResponse[BusinessRuleOut](
        items=[BusinessRuleOut.model_validate(r) for r in rows],
        total=int(total),
        page=page,
        page_size=page_size,
    )


# 11. POST /business-rules
@router.post(
    "/business-rules",
    response_model=BusinessRuleOut,
    status_code=status.HTTP_201_CREATED,
    summary="发布业务规则新版本（仅 ADMIN，同事务停用旧活跃版并递增 version）",
)
async def create_business_rule(
    payload: BusinessRuleCreateIn,
    response: Response,
    principal: CurrentPrincipal = Depends(require_roles("ADMIN")),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
    ctx: RequestContext = Depends(get_request_context),  # noqa: B008
) -> BusinessRuleOut:
    try:
        validate_rule_value(payload.rule_code, payload.rule_value)
    except ValidationError as exc:
        raise AppError(
            INVALID_RULE_PAYLOAD,
            f"业务规则 {payload.rule_code} 载荷语义校验失败",
            {
                "rule_code": payload.rule_code,
                "errors": [
                    {
                        "loc": [str(part) for part in e.get("loc", ())],
                        "msg": str(e.get("msg", "")),
                    }
                    for e in exc.errors()
                ],
            },
        ) from exc

    # 行锁该 rule_code 历史版本，避免并发发布算出相同 version 或撞 uq_business_rules_code_active
    existing_rows = (
        await session.scalars(
            select(BusinessRule)
            .where(BusinessRule.rule_code == payload.rule_code)
            .with_for_update()
        )
    ).all()
    max_version = max((r.version for r in existing_rows), default=0)
    for row in existing_rows:
        if row.is_active:
            row.is_active = False
    await session.flush()

    new_rule = BusinessRule(
        rule_code=payload.rule_code,
        version=max_version + 1,
        rule_value=payload.rule_value,
        description=payload.description,
        is_active=True,
        effective_from=datetime.now(UTC),
        created_by_account_id=principal.account.id,
    )
    session.add(new_rule)
    await session.flush()
    _add_audit(
        session,
        ctx,
        principal=principal,
        action="BUSINESS_RULE_CREATED",
        resource_type="BUSINESS_RULE",
        resource_id=new_rule.id,
        response_status=201,
        snapshot={"rule_code": new_rule.rule_code, "version": new_rule.version},
    )
    await session.commit()
    await session.refresh(new_rule)
    response.headers["Location"] = f"/api/v1/business-rules/{new_rule.id}"
    return BusinessRuleOut.model_validate(new_rule)


# 12. PATCH /business-rules/{id}
@router.patch(
    "/business-rules/{rule_id}",
    response_model=BusinessRuleOut,
    summary="更新业务规则元信息（仅可改 is_active/description，改 rule_value 返 422）",
)
async def patch_business_rule(
    rule_id: int,
    payload: BusinessRulePatchIn,
    principal: CurrentPrincipal = Depends(require_roles("ADMIN")),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
    ctx: RequestContext = Depends(get_request_context),  # noqa: B008
) -> BusinessRuleOut:
    if "rule_value" in payload.model_fields_set:
        raise AppError(
            RULE_VALUE_IMMUTABLE,
            "禁止原地修改历史版本规则载荷 (rule_value)，请调用 POST /business-rules 发布新版本",
            {"rule_id": rule_id},
        )

    rule = await session.scalar(
        select(BusinessRule).where(BusinessRule.id == rule_id).with_for_update()
    )
    if rule is None:
        raise AppError(
            RESOURCE_NOT_FOUND,
            "业务规则版本不存在",
            {"resource_type": "BUSINESS_RULE", "resource_id": rule_id},
        )

    if payload.is_active is True and not rule.is_active:
        others = (
            await session.scalars(
                select(BusinessRule)
                .where(
                    BusinessRule.rule_code == rule.rule_code,
                    BusinessRule.id != rule.id,
                    BusinessRule.is_active.is_(True),
                )
                .with_for_update()
            )
        ).all()
        for other in others:
            other.is_active = False
        await session.flush()
        rule.is_active = True
    elif payload.is_active is False:
        rule.is_active = False

    if "description" in payload.model_fields_set:
        rule.description = payload.description

    _add_audit(
        session,
        ctx,
        principal=principal,
        action="BUSINESS_RULE_UPDATED",
        resource_type="BUSINESS_RULE",
        resource_id=rule.id,
        response_status=200,
        snapshot={
            "rule_code": rule.rule_code,
            "version": rule.version,
            "is_active": rule.is_active,
        },
    )
    await session.commit()
    await session.refresh(rule)
    return BusinessRuleOut.model_validate(rule)
