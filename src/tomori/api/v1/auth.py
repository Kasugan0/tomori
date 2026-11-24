"""认证、账号、角色与业务范围授权路由（12 个端点，docs/API.md §2.1）。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Response, status
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
from tomori.models.account import Account
from tomori.models.account_role import AccountRole
from tomori.models.audit_log import AuditLog
from tomori.models.maintenance_region import MaintenanceRegion
from tomori.models.role import Role
from tomori.models.scope_authorization import ScopeAuthorization
from tomori.schemas.auth import (
    AccountCreateIn,
    AccountOut,
    AccountUpdateIn,
    AuthMeOut,
    ChangePasswordIn,
    LoginIn,
    LoginOut,
    ResetPasswordIn,
    RoleOut,
    ScopeAuthorizationCreateIn,
    ScopeAuthorizationOut,
)
from tomori.schemas.common import AccountStatus, PaginatedResponse, RoleCode, ScopeType, StatusOut
from tomori.services.auth import (
    authenticate_account,
    create_access_token,
    get_account_role_code,
    hash_password,
    list_active_scopes,
    record_audit_independently,
    verify_password,
)
from tomori.services.exceptions import (
    INVALID_CREDENTIALS,
    RESOURCE_NOT_FOUND,
    VALIDATION_ERROR,
    AppError,
)

router = APIRouter(tags=["auth"])


def _to_scope_out(
    scope: ScopeAuthorization,
    region_code: str | None = None,
    region_name: str | None = None,
) -> ScopeAuthorizationOut:
    return ScopeAuthorizationOut(
        id=scope.id,
        account_id=scope.account_id,
        region_id=scope.region_id,
        region_code=region_code,
        region_name=region_name,
        scope_type=scope.scope_type,  # type: ignore[arg-type]
        is_active=scope.is_active,
        valid_from=scope.valid_from,
        valid_to=scope.valid_to,
        granted_by_account_id=scope.granted_by_account_id,
        remark=scope.remark,
        created_at=scope.created_at,
    )


async def _build_account_out(
    session: AsyncSession,
    account: Account,
    *,
    role_code: str | None = None,
    include_scopes: bool = False,
) -> AccountOut:
    resolved_role = role_code or await get_account_role_code(session, account.id)
    if resolved_role is None:
        resolved_role = "TECHNICIAN"
    scopes_out: list[ScopeAuthorizationOut] | None = None
    if include_scopes:
        rows = await list_active_scopes(session, account.id)
        scopes_out = [_to_scope_out(s, rc, rn) for s, rc, rn in rows]
    return AccountOut(
        id=account.id,
        username=account.username,
        full_name=account.full_name,
        employee_no=account.employee_no,
        phone=account.phone,
        email=account.email,
        status=account.status,  # type: ignore[arg-type]
        role_code=resolved_role,  # type: ignore[arg-type]
        skills=list(account.skills or []),
        max_active_dispatch=account.max_active_dispatch,
        token_version=account.token_version,
        manager_account_id=account.manager_account_id,
        last_login_at=account.last_login_at,
        created_at=account.created_at,
        scope_authorizations=scopes_out,
    )


def _add_audit(
    session: AsyncSession,
    ctx: RequestContext,
    *,
    actor: Account | None,
    role_code: str | None,
    action: str,
    resource_type: str,
    resource_id: int | None,
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
            actor_account_id=actor.id if actor else None,
            actor_username=actor.username if actor else None,
            actor_role_code=role_code,
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


# 1. POST /auth/login
@router.post("/auth/login", response_model=LoginOut, summary="账号密码登录签发 JWT")
async def login(
    payload: LoginIn,
    session: AsyncSession = Depends(get_session),  # noqa: B008
    ctx: RequestContext = Depends(get_request_context),  # noqa: B008
) -> LoginOut:
    try:
        account, role_code = await authenticate_account(
            session,
            username=payload.username,
            secret=payload.password,
        )
    except AppError as err:
        await record_audit_independently(
            request_id=ctx.request_id,
            actor_account_id=None,
            actor_username=payload.username,
            actor_role_code=None,
            action="LOGIN_FAILED",
            resource_type="AUTH",
            method=ctx.method,
            path=ctx.path,
            response_status=err.http_status,
            error_code=err.code,
            business_snapshot_json={"username": payload.username},
            client_ip=ctx.client_ip,
            user_agent=ctx.user_agent,
        )
        raise

    token, expires_in = create_access_token(
        account_id=account.id,
        token_version=account.token_version,
        role_code=role_code,
    )
    _add_audit(
        session,
        ctx,
        actor=account,
        role_code=role_code,
        action="LOGIN_SUCCEEDED",
        resource_type="AUTH",
        resource_id=account.id,
        response_status=200,
    )
    account_out = await _build_account_out(
        session,
        account,
        role_code=role_code,
        include_scopes=True,
    )
    await session.commit()
    return LoginOut(
        access_token=token,
        token_type="bearer",
        expires_in=expires_in,
        account=account_out,
    )


# 2. POST /auth/logout
@router.post("/auth/logout", response_model=StatusOut, summary="退出登录（token_version 吊销）")
async def logout(
    principal: CurrentPrincipal = Depends(get_current_principal),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
    ctx: RequestContext = Depends(get_request_context),  # noqa: B008
) -> StatusOut:
    principal.account.token_version += 1
    _add_audit(
        session,
        ctx,
        actor=principal.account,
        role_code=principal.role_code,
        action="LOGOUT",
        resource_type="AUTH",
        resource_id=principal.account.id,
        response_status=200,
        snapshot={"token_version": principal.account.token_version},
    )
    await session.commit()
    return StatusOut(status="ok")


# 3. GET /auth/me
@router.get("/auth/me", response_model=AuthMeOut, summary="获取当前登录账号与授权范围")
async def get_me(
    principal: CurrentPrincipal = Depends(get_current_principal),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> AuthMeOut:
    scopes_rows = await list_active_scopes(session, principal.account.id)
    scopes_out = [_to_scope_out(s, rc, rn) for s, rc, rn in scopes_rows]
    account_out = await _build_account_out(
        session,
        principal.account,
        role_code=principal.role_code,
        include_scopes=False,
    )
    account_out.scope_authorizations = scopes_out
    return AuthMeOut(
        account=account_out,
        role_code=principal.role_code,  # type: ignore[arg-type]
        scope_authorizations=scopes_out,
    )


# 4. POST /auth/change-password
@router.post(
    "/auth/change-password",
    response_model=StatusOut,
    summary="当前用户修改密码并吊销旧凭证",
)
async def change_password(
    payload: ChangePasswordIn,
    principal: CurrentPrincipal = Depends(get_current_principal),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
    ctx: RequestContext = Depends(get_request_context),  # noqa: B008
) -> StatusOut:
    if not verify_password(payload.old_password, principal.account.password_hash):
        raise AppError(INVALID_CREDENTIALS, "原密码错误")

    principal.account.password_hash = hash_password(payload.new_password)
    principal.account.token_version += 1
    _add_audit(
        session,
        ctx,
        actor=principal.account,
        role_code=principal.role_code,
        action="PASSWORD_CHANGED",
        resource_type="ACCOUNT",
        resource_id=principal.account.id,
        response_status=200,
    )
    await session.commit()
    return StatusOut(status="ok")


# 5. GET /accounts
@router.get(
    "/accounts",
    response_model=PaginatedResponse[AccountOut],
    summary="查询账号列表（仅 ADMIN）",
)
async def list_accounts(
    role_code: RoleCode | None = Query(default=None),
    region_id: int | None = Query(default=None, gt=0),
    account_status: AccountStatus | None = Query(default=None, alias="status"),
    keyword: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    _principal: CurrentPrincipal = Depends(require_roles("ADMIN")),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> PaginatedResponse[AccountOut]:
    stmt = (
        select(Account, Role.code)
        .join(AccountRole, AccountRole.account_id == Account.id)
        .join(Role, Role.id == AccountRole.role_id)
    )
    if role_code is not None:
        stmt = stmt.where(Role.code == role_code)
    if account_status is not None:
        stmt = stmt.where(Account.status == account_status)
    if region_id is not None:
        now = datetime.now(UTC)
        scope_sub = select(ScopeAuthorization.account_id).where(
            ScopeAuthorization.region_id == region_id,
            ScopeAuthorization.is_active.is_(True),
            ScopeAuthorization.valid_from <= now,
            (ScopeAuthorization.valid_to.is_(None)) | (ScopeAuthorization.valid_to > now),
        )
        stmt = stmt.where(Account.id.in_(scope_sub))
    if keyword and keyword.strip():
        kw = f"%{keyword.strip()}%"
        stmt = stmt.where(
            or_(
                Account.username.ilike(kw),
                Account.full_name.ilike(kw),
                Account.employee_no.ilike(kw),
            )
        )

    total = (await session.scalar(select(func.count()).select_from(stmt.subquery()))) or 0
    rows = (
        await session.execute(
            stmt.order_by(Account.id.desc()).offset((page - 1) * page_size).limit(page_size)
        )
    ).all()
    items = [await _build_account_out(session, acc, role_code=rc) for acc, rc in rows]
    return PaginatedResponse[AccountOut](
        items=items,
        total=int(total),
        page=page,
        page_size=page_size,
    )


# 6. POST /accounts
@router.post(
    "/accounts",
    response_model=AccountOut,
    status_code=status.HTTP_201_CREATED,
    summary="创建账号并绑定单一角色（仅 ADMIN）",
)
async def create_account(
    payload: AccountCreateIn,
    response: Response,
    principal: CurrentPrincipal = Depends(require_roles("ADMIN")),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
    ctx: RequestContext = Depends(get_request_context),  # noqa: B008
) -> AccountOut:
    role = await session.scalar(select(Role).where(Role.code == payload.role_code))
    if role is None:
        raise AppError(RESOURCE_NOT_FOUND, "角色不存在", {"resource_type": "ROLE"})

    acc = Account(
        username=payload.username,
        full_name=payload.full_name,
        employee_no=payload.employee_no,
        phone=payload.phone,
        email=payload.email,
        status="ACTIVE",
        skills=payload.skills,
        max_active_dispatch=payload.max_active_dispatch,
        manager_account_id=payload.manager_account_id,
    )
    acc.password_hash = hash_password(payload.password)
    session.add(acc)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise AppError(
            VALIDATION_ERROR,
            "用户名或工号已存在",
            {"username": payload.username},
        ) from exc

    session.add(
        AccountRole(
            account_id=acc.id,
            role_id=role.id,
            granted_by_account_id=principal.account.id,
        )
    )
    _add_audit(
        session,
        ctx,
        actor=principal.account,
        role_code=principal.role_code,
        action="ACCOUNT_CREATED",
        resource_type="ACCOUNT",
        resource_id=acc.id,
        response_status=201,
        snapshot={"username": acc.username, "role_code": payload.role_code},
    )
    _add_audit(
        session,
        ctx,
        actor=principal.account,
        role_code=principal.role_code,
        action="ACCOUNT_ROLE_GRANTED",
        resource_type="ACCOUNT",
        resource_id=acc.id,
        response_status=201,
        snapshot={"role_code": payload.role_code},
    )
    await session.commit()
    await session.refresh(acc)
    response.headers["Location"] = f"/api/v1/accounts/{acc.id}"
    return await _build_account_out(session, acc, role_code=payload.role_code)


# 7. PATCH /accounts/{id}
@router.patch("/accounts/{account_id}", response_model=AccountOut, summary="编辑账号（仅 ADMIN）")
async def update_account(
    account_id: int,
    payload: AccountUpdateIn,
    principal: CurrentPrincipal = Depends(require_roles("ADMIN")),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
    ctx: RequestContext = Depends(get_request_context),  # noqa: B008
) -> AccountOut:
    acc = await session.scalar(select(Account).where(Account.id == account_id))
    if acc is None:
        raise AppError(
            RESOURCE_NOT_FOUND,
            "账号不存在",
            {"resource_type": "ACCOUNT", "resource_id": account_id},
        )

    if (
        account_id == principal.account.id
        and payload.status is not None
        and payload.status != acc.status
    ):
        raise AppError(
            VALIDATION_ERROR,
            "管理员禁止修改自身账号状态以防自锁死",
            {"field": "status"},
        )

    prev_status = acc.status
    if payload.full_name is not None:
        acc.full_name = payload.full_name
    if "employee_no" in payload.model_fields_set:
        acc.employee_no = payload.employee_no
    if "phone" in payload.model_fields_set:
        acc.phone = payload.phone
    if "email" in payload.model_fields_set:
        acc.email = payload.email
    if payload.skills is not None:
        acc.skills = payload.skills
    if payload.max_active_dispatch is not None:
        acc.max_active_dispatch = payload.max_active_dispatch
    if "manager_account_id" in payload.model_fields_set:
        acc.manager_account_id = payload.manager_account_id

    disabled_now = False
    if payload.status is not None and payload.status != acc.status:
        acc.status = payload.status
        if payload.status != "ACTIVE":
            acc.token_version += 1
            disabled_now = True

    _add_audit(
        session,
        ctx,
        actor=principal.account,
        role_code=principal.role_code,
        action="ACCOUNT_DISABLED" if disabled_now else "ACCOUNT_UPDATED",
        resource_type="ACCOUNT",
        resource_id=acc.id,
        response_status=200,
        snapshot={"from_status": prev_status, "to_status": acc.status},
    )
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise AppError(VALIDATION_ERROR, "账号更新触发唯一性约束冲突") from exc
    await session.refresh(acc)
    return await _build_account_out(session, acc)


# 8. POST /accounts/{id}/reset-password
@router.post(
    "/accounts/{account_id}/reset-password",
    response_model=StatusOut,
    summary="管理员重置账号密码（仅 ADMIN）",
)
async def reset_account_password(
    account_id: int,
    payload: ResetPasswordIn,
    principal: CurrentPrincipal = Depends(require_roles("ADMIN")),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
    ctx: RequestContext = Depends(get_request_context),  # noqa: B008
) -> StatusOut:
    acc = await session.scalar(select(Account).where(Account.id == account_id))
    if acc is None:
        raise AppError(
            RESOURCE_NOT_FOUND,
            "账号不存在",
            {"resource_type": "ACCOUNT", "resource_id": account_id},
        )
    acc.password_hash = hash_password(payload.new_password)
    acc.token_version += 1
    _add_audit(
        session,
        ctx,
        actor=principal.account,
        role_code=principal.role_code,
        action="PASSWORD_CHANGED",
        resource_type="ACCOUNT",
        resource_id=acc.id,
        response_status=200,
        snapshot={"reset_by": principal.account.username},
    )
    await session.commit()
    return StatusOut(status="ok")


# 9. GET /roles
@router.get("/roles", response_model=list[RoleOut], summary="角色字典只读列表（仅 ADMIN）")
async def list_roles(
    _principal: CurrentPrincipal = Depends(require_roles("ADMIN")),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[RoleOut]:
    roles = (await session.scalars(select(Role).order_by(Role.id.asc()))).all()
    return [RoleOut.model_validate(r) for r in roles]


# 10. GET /scope-authorizations
@router.get(
    "/scope-authorizations",
    response_model=PaginatedResponse[ScopeAuthorizationOut],
    summary="查询业务范围授权列表（仅 ADMIN）",
)
async def list_scope_authorizations(
    account_id: int | None = Query(default=None, gt=0),
    region_id: int | None = Query(default=None, gt=0),
    scope_type: ScopeType | None = Query(default=None),
    is_active: bool | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    _principal: CurrentPrincipal = Depends(require_roles("ADMIN")),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> PaginatedResponse[ScopeAuthorizationOut]:
    stmt = select(ScopeAuthorization, MaintenanceRegion.region_code, MaintenanceRegion.name).join(
        MaintenanceRegion, MaintenanceRegion.id == ScopeAuthorization.region_id
    )
    if account_id is not None:
        stmt = stmt.where(ScopeAuthorization.account_id == account_id)
    if region_id is not None:
        stmt = stmt.where(ScopeAuthorization.region_id == region_id)
    if scope_type is not None:
        stmt = stmt.where(ScopeAuthorization.scope_type == scope_type)
    if is_active is not None:
        stmt = stmt.where(ScopeAuthorization.is_active.is_(is_active))

    total = (await session.scalar(select(func.count()).select_from(stmt.subquery()))) or 0
    rows = (
        await session.execute(
            stmt.order_by(ScopeAuthorization.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    items = [_to_scope_out(s, rc, rn) for s, rc, rn in rows]
    return PaginatedResponse[ScopeAuthorizationOut](
        items=items,
        total=int(total),
        page=page,
        page_size=page_size,
    )


# 11. POST /scope-authorizations
@router.post(
    "/scope-authorizations",
    response_model=ScopeAuthorizationOut,
    status_code=status.HTTP_201_CREATED,
    summary="授予业务范围授权（仅 ADMIN；同人同区同类型先停旧再授新）",
)
async def create_scope_authorization(
    payload: ScopeAuthorizationCreateIn,
    response: Response,
    principal: CurrentPrincipal = Depends(require_roles("ADMIN")),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
    ctx: RequestContext = Depends(get_request_context),  # noqa: B008
) -> ScopeAuthorizationOut:
    acc = await session.scalar(select(Account).where(Account.id == payload.account_id))
    if acc is None:
        raise AppError(
            RESOURCE_NOT_FOUND,
            "目标账号不存在",
            {"resource_type": "ACCOUNT", "resource_id": payload.account_id},
        )
    region = await session.scalar(
        select(MaintenanceRegion).where(MaintenanceRegion.id == payload.region_id)
    )
    if region is None:
        raise AppError(
            RESOURCE_NOT_FOUND,
            "目标运维区域不存在",
            {"resource_type": "REGION", "resource_id": payload.region_id},
        )

    # 先停用既有同人同区同类型活跃授权，保留因果链不物理删除
    old_active = (
        await session.scalars(
            select(ScopeAuthorization).where(
                ScopeAuthorization.account_id == payload.account_id,
                ScopeAuthorization.region_id == payload.region_id,
                ScopeAuthorization.scope_type == payload.scope_type,
                ScopeAuthorization.is_active.is_(True),
            )
        )
    ).all()
    for prev in old_active:
        prev.is_active = False

    await session.flush()

    new_scope = ScopeAuthorization(
        account_id=payload.account_id,
        region_id=payload.region_id,
        scope_type=payload.scope_type,
        is_active=True,
        valid_from=payload.valid_from or datetime.now(UTC),
        valid_to=payload.valid_to,
        granted_by_account_id=principal.account.id,
        remark=payload.remark,
    )
    session.add(new_scope)
    await session.flush()
    _add_audit(
        session,
        ctx,
        actor=principal.account,
        role_code=principal.role_code,
        action="SCOPE_AUTHORIZATION_GRANTED",
        resource_type="SCOPE_AUTHORIZATION",
        resource_id=new_scope.id,
        response_status=201,
        snapshot={
            "account_id": payload.account_id,
            "region_id": payload.region_id,
            "scope_type": payload.scope_type,
        },
    )
    region_code = region.region_code
    region_name = region.name
    await session.commit()
    await session.refresh(new_scope)
    response.headers["Location"] = f"/api/v1/scope-authorizations/{new_scope.id}"
    return _to_scope_out(new_scope, region_code, region_name)


# 12. DELETE /scope-authorizations/{id}
@router.delete(
    "/scope-authorizations/{scope_id}",
    response_model=ScopeAuthorizationOut,
    summary="软停用业务范围授权（不物理删除）",
)
async def deactivate_scope_authorization(
    scope_id: int,
    principal: CurrentPrincipal = Depends(require_roles("ADMIN")),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
    ctx: RequestContext = Depends(get_request_context),  # noqa: B008
) -> ScopeAuthorizationOut:
    row = (
        await session.execute(
            select(ScopeAuthorization, MaintenanceRegion.region_code, MaintenanceRegion.name)
            .join(MaintenanceRegion, MaintenanceRegion.id == ScopeAuthorization.region_id)
            .where(ScopeAuthorization.id == scope_id)
        )
    ).first()
    if row is None:
        raise AppError(
            RESOURCE_NOT_FOUND,
            "授权记录不存在",
            {"resource_type": "SCOPE_AUTHORIZATION", "resource_id": scope_id},
        )
    scope, rcode, rname = row
    scope.is_active = False
    _add_audit(
        session,
        ctx,
        actor=principal.account,
        role_code=principal.role_code,
        action="SCOPE_AUTHORIZATION_REVOKED",
        resource_type="SCOPE_AUTHORIZATION",
        resource_id=scope.id,
        response_status=200,
        snapshot={"account_id": scope.account_id, "region_id": scope.region_id},
    )
    await session.commit()
    await session.refresh(scope)
    return _to_scope_out(scope, rcode, rname)
