"""FastAPI 三层鉴权依赖、请求上下文提取与 403 同事务审计记录。

严格执行 `docs/API.md` §1.2 次序：
1. 认证 (`401 TOKEN_MISSING / TOKEN_INVALID / TOKEN_EXPIRED`)
2. 账号状态 (`403 ACCOUNT_DISABLED`)
3. 角色 (`403 FORBIDDEN_ROLE`)
4. 区域授权 (`403 FORBIDDEN_REGION_SCOPE`)
全部 403 拒绝必须落盘 `audit_logs(action='FORBIDDEN_ACCESS_DENIED')`。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tomori.db.session import get_session
from tomori.models.account import Account
from tomori.services.auth import (
    decode_access_token,
    get_account_role_code,
    has_valid_region_scope,
    record_audit_independently,
)
from tomori.services.exceptions import (
    ACCOUNT_DISABLED,
    FORBIDDEN_REGION_SCOPE,
    FORBIDDEN_ROLE,
    TOKEN_INVALID,
    TOKEN_MISSING,
    AppError,
)


@dataclass(frozen=True)
class RequestContext:
    """当前请求的可审计上下文。"""

    request_id: str
    method: str
    path: str
    client_ip: str | None
    user_agent: str | None


@dataclass(frozen=True)
class CurrentPrincipal:
    """认证通过的操作人主体与唯一角色。"""

    account: Account
    role_code: str


def get_request_context(request: Request) -> RequestContext:
    request_id = getattr(request.state, "request_id", "unknown")
    client_ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")
    return RequestContext(
        request_id=request_id,
        method=request.method.upper(),
        path=request.url.path,
        client_ip=client_ip,
        user_agent=user_agent,
    )


async def audit_forbidden_attempt(
    ctx: RequestContext,
    *,
    account: Account | None,
    role_code: str | None,
    error_code: str,
    resource_type: str = "AUTH",
    resource_id: int | None = None,
    ticket_id: int | None = None,
    reason: str,
) -> None:
    """独立提交一条 403 FORBIDDEN_ACCESS_DENIED 审计行（不随业务回滚而丢失）。"""
    await record_audit_independently(
        request_id=ctx.request_id,
        actor_account_id=account.id if account else None,
        actor_username=account.username if account else None,
        actor_role_code=role_code,
        action="FORBIDDEN_ACCESS_DENIED",
        resource_type=resource_type,
        resource_id=resource_id,
        ticket_id=ticket_id,
        method=ctx.method,
        path=ctx.path,
        response_status=403,
        error_code=error_code,
        business_snapshot_json={"reason": reason},
        client_ip=ctx.client_ip,
        user_agent=ctx.user_agent,
    )


async def get_current_principal(
    request: Request,
    session: AsyncSession = Depends(get_session),  # noqa: B008
    ctx: RequestContext = Depends(get_request_context),  # noqa: B008
) -> CurrentPrincipal:
    """层 1 + 层 2：JWT 认证 + `tv` 吊销核验 + `accounts.status == 'ACTIVE'` 检查。"""
    auth_header = request.headers.get("authorization")
    if not auth_header:
        raise AppError(TOKEN_MISSING, "缺少 Authorization Bearer 登录凭证")

    parts = auth_header.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        raise AppError(TOKEN_INVALID, "Authorization 头格式无效，应为 Bearer <token>")

    claims = decode_access_token(parts[1].strip())
    account_id = int(claims["sub"])
    token_tv = int(claims["tv"])

    account = await session.scalar(select(Account).where(Account.id == account_id))
    if account is None or account.token_version != token_tv:
        raise AppError(TOKEN_INVALID, "登录凭证已失效或被吊销，请重新登录")

    role_code = await get_account_role_code(session, account.id)
    if role_code is None:
        raise AppError(TOKEN_INVALID, "账号角色信息缺失")

    if account.status != "ACTIVE":
        await audit_forbidden_attempt(
            ctx,
            account=account,
            role_code=role_code,
            error_code=ACCOUNT_DISABLED,
            reason=f"account status is {account.status}",
        )
        raise AppError(ACCOUNT_DISABLED, "当前账号已停用或锁定")

    return CurrentPrincipal(account=account, role_code=role_code)


def require_roles(*allowed_roles: str) -> Callable[..., CurrentPrincipal]:
    """层 3：RBAC 角色校验（403 FORBIDDEN_ROLE + 审计）。"""

    async def _checker(
        principal: CurrentPrincipal = Depends(get_current_principal),  # noqa: B008
        ctx: RequestContext = Depends(get_request_context),  # noqa: B008
    ) -> CurrentPrincipal:
        if principal.role_code not in allowed_roles:
            await audit_forbidden_attempt(
                ctx,
                account=principal.account,
                role_code=principal.role_code,
                error_code=FORBIDDEN_ROLE,
                reason=f"role {principal.role_code} not in {sorted(allowed_roles)}",
            )
            raise AppError(FORBIDDEN_ROLE, "当前角色无权执行此操作")
        return principal

    return _checker  # type: ignore[return-value]


async def ensure_region_scope(
    session: AsyncSession,
    *,
    ctx: RequestContext,
    principal: CurrentPrincipal,
    region_id: int,
    scope_type: str,
    allow_admin_bypass: bool = False,
    resource_type: str = "FAULT_TICKET",
    resource_id: int | None = None,
    ticket_id: int | None = None,
) -> int | None:
    """层 4：校验操作人是否持有指定区域对应 `scope_type` 授权，失败记审计并抛 403。"""
    if allow_admin_bypass and principal.role_code == "ADMIN":
        return None

    scope = await has_valid_region_scope(
        session,
        account_id=principal.account.id,
        region_id=region_id,
        scope_type=scope_type,
    )
    if scope is None:
        await audit_forbidden_attempt(
            ctx,
            account=principal.account,
            role_code=principal.role_code,
            error_code=FORBIDDEN_REGION_SCOPE,
            resource_type=resource_type,
            resource_id=resource_id,
            ticket_id=ticket_id,
            reason=f"missing {scope_type} scope for region_id={region_id}",
        )
        raise AppError(
            FORBIDDEN_REGION_SCOPE,
            "您没有该运维区域的对应业务范围授权",
            {"region_id": region_id, "required_scope": scope_type},
        )
    return scope.id
