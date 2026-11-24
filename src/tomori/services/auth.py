"""认证、密码哈希（pwdlib Argon2）、JWT（PyJWT HS256）、授权范围查询与独立审计辅助。

严格遵循 `docs/DESIGN.md` §1.2 / §3.2 与 `docs/API.md` §1.2 / §2.1：
- 无状态 JWT：载荷含 `sub` (str(account_id)), `tv` (token_version), `role`, `exp`。
- 403 / 401 审计 (`FORBIDDEN_ACCESS_DENIED` / `LOGIN_FAILED`) 通过独立短事务提交，
  确保即使业务事务回滚也能真实持久化到 `audit_logs`。
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import jwt
from pwdlib import PasswordHash
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tomori.db.session import get_async_sessionmaker
from tomori.models.account import Account
from tomori.models.account_role import AccountRole
from tomori.models.audit_log import AuditLog
from tomori.models.facility_asset import FacilityAsset
from tomori.models.lighting_point import LightingPoint
from tomori.models.maintenance_region import MaintenanceRegion
from tomori.models.role import Role
from tomori.models.scope_authorization import ScopeAuthorization
from tomori.services.exceptions import (
    INVALID_CREDENTIALS,
    TOKEN_EXPIRED,
    TOKEN_INVALID,
    AppError,
)
from tomori.settings import get_settings

_PASSWORD_HASHER = PasswordHash.recommended()


def hash_password(plain: str) -> str:
    """使用 pwdlib[argon2] 计算密码哈希。"""
    return _PASSWORD_HASHER.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """校验明文与 Argon2 哈希；格式非法时安全返回 False。"""
    try:
        return _PASSWORD_HASHER.verify(plain, hashed)
    except Exception:
        return False


def create_access_token(
    *,
    account_id: int,
    token_version: int,
    role_code: str,
    expires_delta: timedelta | None = None,
) -> tuple[str, int]:
    """签发 HS256 JWT，返回 `(access_token, expires_in_seconds)`。"""
    settings = get_settings()
    ttl = expires_delta or timedelta(minutes=settings.jwt_access_token_expire_minutes)
    now = datetime.now(UTC)
    exp = now + ttl
    payload: dict[str, Any] = {
        "sub": str(account_id),  # RFC 7519 + PyJWT 2.x 强制 sub 为字符串
        "tv": token_version,
        "role": role_code,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    token = jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    return token, int(ttl.total_seconds())


def decode_access_token(token: str) -> dict[str, Any]:
    """解码并验证 JWT 签名、过期时间与必填声明，失败映射为 401 TOKEN_EXPIRED / TOKEN_INVALID。"""
    settings = get_settings()
    try:
        decoded = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["sub", "tv", "role", "exp"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise AppError(TOKEN_EXPIRED, "登录凭证已过期，请重新登录") from exc
    except jwt.InvalidTokenError as exc:
        raise AppError(TOKEN_INVALID, "登录凭证无效或已损坏") from exc

    if not isinstance(decoded.get("sub"), str) or not decoded["sub"].isdigit():
        raise AppError(TOKEN_INVALID, "登录凭证主体标识格式无效")
    if not isinstance(decoded.get("tv"), int):
        raise AppError(TOKEN_INVALID, "登录凭证版本声明无效")
    return dict(decoded)


async def get_account_role_code(session: AsyncSession, account_id: int) -> str | None:
    """读取账号唯一角色编码 (`ADMIN` / `INSPECTOR` / `TECHNICIAN`)。"""
    stmt = (
        select(Role.code)
        .join(AccountRole, AccountRole.role_id == Role.id)
        .where(AccountRole.account_id == account_id)
    )
    return (await session.scalars(stmt)).first()


async def list_active_scopes(
    session: AsyncSession,
    account_id: int,
    *,
    at: datetime | None = None,
) -> list[tuple[ScopeAuthorization, str, str]]:
    """返回账号当前有效的全部区域授权及对应区域 code/name。"""
    now = at or datetime.now(UTC)
    stmt = (
        select(ScopeAuthorization, MaintenanceRegion.region_code, MaintenanceRegion.name)
        .join(MaintenanceRegion, MaintenanceRegion.id == ScopeAuthorization.region_id)
        .where(
            ScopeAuthorization.account_id == account_id,
            ScopeAuthorization.is_active.is_(True),
            ScopeAuthorization.valid_from <= now,
            (ScopeAuthorization.valid_to.is_(None)) | (ScopeAuthorization.valid_to > now),
        )
        .order_by(ScopeAuthorization.id.asc())
    )
    rows = (await session.execute(stmt)).all()
    return [(row[0], row[1], row[2]) for row in rows]


async def get_authorized_region_ids(
    session: AsyncSession,
    account_id: int,
    *,
    scope_type: str | None = None,
    at: datetime | None = None,
) -> set[int]:
    """查询操作人当前有效授权的全部 region_id 集合。"""
    now = at or datetime.now(UTC)
    stmt = select(ScopeAuthorization.region_id).where(
        ScopeAuthorization.account_id == account_id,
        ScopeAuthorization.is_active.is_(True),
        ScopeAuthorization.valid_from <= now,
        (ScopeAuthorization.valid_to.is_(None)) | (ScopeAuthorization.valid_to > now),
    )
    if scope_type is not None:
        stmt = stmt.where(ScopeAuthorization.scope_type == scope_type)
    ids = (await session.scalars(stmt)).all()
    return set(ids)


async def has_valid_region_scope(
    session: AsyncSession,
    *,
    account_id: int,
    region_id: int,
    scope_type: str,
    at: datetime | None = None,
) -> ScopeAuthorization | None:
    """检查账号在指定区域于此刻是否具备对应 scope_type 有效授权，返回授权行或 None。"""
    now = at or datetime.now(UTC)
    stmt = select(ScopeAuthorization).where(
        ScopeAuthorization.account_id == account_id,
        ScopeAuthorization.region_id == region_id,
        ScopeAuthorization.scope_type == scope_type,
        ScopeAuthorization.is_active.is_(True),
        ScopeAuthorization.valid_from <= now,
        (ScopeAuthorization.valid_to.is_(None)) | (ScopeAuthorization.valid_to > now),
    )
    return (await session.scalars(stmt)).first()


async def record_audit_independently(
    *,
    request_id: str,
    actor_account_id: int | None,
    actor_username: str | None,
    actor_role_code: str | None,
    action: str,
    resource_type: str,
    resource_id: int | None = None,
    ticket_id: int | None = None,
    method: str,
    path: str,
    response_status: int,
    error_code: str | None = None,
    request_payload: dict[str, Any] | None = None,
    business_snapshot_json: dict[str, Any] | None = None,
    client_ip: str | None = None,
    user_agent: str | None = None,
) -> None:
    """使用独立短事务写入审计记录，保证外层业务事务回滚或抛出 401/403 时审计不丢失。"""
    try:
        req_uuid = uuid.UUID(request_id)
    except ValueError:
        req_uuid = uuid.uuid4()

    factory = get_async_sessionmaker()
    async with factory() as audit_session, audit_session.begin():
        row = AuditLog(
            request_id=req_uuid,
            actor_account_id=actor_account_id,
            actor_username=actor_username,
            actor_role_code=actor_role_code,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            ticket_id=ticket_id,
            method=method.upper(),
            path=path[:256],
            request_payload=request_payload,
            request_payload_hash=None,
            idempotency_key=None,
            response_status=response_status,
            error_code=error_code,
            business_snapshot_json=business_snapshot_json,
            response_body_json=None,
            client_ip=client_ip,
            user_agent=user_agent[:256] if user_agent else None,
        )
        audit_session.add(row)


async def authenticate_account(
    session: AsyncSession,
    *,
    username: str,
    secret: str,
) -> tuple[Account, str]:
    """校验用户名密码，返回 `(Account, role_code)`；失败一律抛 401 INVALID_CREDENTIALS。"""
    account = await session.scalar(select(Account).where(Account.username == username))
    if account is None or not verify_password(secret, account.password_hash):
        raise AppError(INVALID_CREDENTIALS, "用户名或密码错误")

    if account.status != "ACTIVE":
        from tomori.services.exceptions import ACCOUNT_DISABLED

        raise AppError(ACCOUNT_DISABLED, "当前账号已停用或锁定，请联系运维管理员")

    role_code = await get_account_role_code(session, account.id)
    if role_code is None:
        raise AppError(INVALID_CREDENTIALS, "用户名或密码错误")

    account.last_login_at = datetime.now(UTC)
    await session.flush()
    return account, role_code


async def seed_demo_data(session: AsyncSession) -> dict[str, int]:
    """幂等写入本地演示/E2E 初始化账号、角色、区域、授权、照明点位与设施资产。

    - 若对应编码/用户名已存在则跳过，可安全重复调用。
    - 账号密码均由 pwdlib[argon2] 加密存储。
    """
    roles = {r.code: r for r in (await session.scalars(select(Role))).all()}
    created_counts = {"regions": 0, "accounts": 0, "scopes": 0, "points": 0, "assets": 0}

    # 1. 运维区域
    hq = await session.scalar(
        select(MaintenanceRegion).where(MaintenanceRegion.region_code == "ZZ-HQ-01")
    )
    if hq is None:
        hq = MaintenanceRegion(
            region_code="ZZ-HQ-01",
            name="郑州市城市照明运维中心",
            level=1,
            description="市级总管辖区",
            is_active=True,
        )
        session.add(hq)
        await session.flush()
        created_counts["regions"] += 1

    jinshui = await session.scalar(
        select(MaintenanceRegion).where(MaintenanceRegion.region_code == "ZZ-JS-01")
    )
    if jinshui is None:
        jinshui = MaintenanceRegion(
            region_code="ZZ-JS-01",
            name="金水区主干道路段",
            parent_region_id=hq.id,
            level=2,
            description="金水区核心照明维护区域",
            is_active=True,
        )
        session.add(jinshui)
        await session.flush()
        created_counts["regions"] += 1

    zhengdong = await session.scalar(
        select(MaintenanceRegion).where(MaintenanceRegion.region_code == "ZZ-ZD-01")
    )
    if zhengdong is None:
        zhengdong = MaintenanceRegion(
            region_code="ZZ-ZD-01",
            name="郑东新区维护片区",
            parent_region_id=hq.id,
            level=2,
            description="郑东新区公共照明维护区域",
            is_active=True,
        )
        session.add(zhengdong)
        await session.flush()
        created_counts["regions"] += 1

    # 2. 初始三角色演示账号
    demo_accounts: Sequence[tuple[str, str, str, str, list[str]]] = (
        ("admin", "Tomori#Admin2026", "系统运维管理员", "ADMIN", []),
        ("inspector1", "Tomori#Inspect2026", "巡查员李明", "INSPECTOR", []),
        (
            "tech1",
            "Tomori#Tech2026",
            "维修员张伟",
            "TECHNICIAN",
            ["LAMP_OUT", "FIXTURE_DAMAGED", "ELECTRIC_HAZARD"],
        ),
        (
            "tech2",
            "Tomori#Tech2026",
            "维修员王强",
            "TECHNICIAN",
            ["OCCLUDED", "FLICKERING", "LAMP_OUT"],
        ),
    )

    accounts_by_name: dict[str, Account] = {}
    for uname, raw_secret, full_name, role_code, skills in demo_accounts:
        acc = await session.scalar(select(Account).where(Account.username == uname))
        if acc is None:
            acc = Account(
                username=uname,
                full_name=full_name,
                status="ACTIVE",
                skills=skills,
                max_active_dispatch=3,
                token_version=1,
            )
            acc.password_hash = hash_password(raw_secret)
            session.add(acc)
            await session.flush()
            if role_code in roles:
                session.add(
                    AccountRole(
                        account_id=acc.id,
                        role_id=roles[role_code].id,
                        granted_by_account_id=None,
                    )
                )
            created_counts["accounts"] += 1
        accounts_by_name[uname] = acc

    await session.flush()

    # 3. 区域范围授权
    admin_acc = accounts_by_name["admin"]
    scope_specs = [
        (accounts_by_name["inspector1"].id, jinshui.id, "INSPECT"),
        (accounts_by_name["tech1"].id, jinshui.id, "REPAIR"),
        (accounts_by_name["tech2"].id, jinshui.id, "REPAIR"),
        (accounts_by_name["tech2"].id, zhengdong.id, "REPAIR"),
        (admin_acc.id, jinshui.id, "MANAGE"),
        (admin_acc.id, zhengdong.id, "MANAGE"),
    ]
    for acc_id, reg_id, stype in scope_specs:
        existing_scope = await session.scalar(
            select(ScopeAuthorization).where(
                ScopeAuthorization.account_id == acc_id,
                ScopeAuthorization.region_id == reg_id,
                ScopeAuthorization.scope_type == stype,
                ScopeAuthorization.is_active.is_(True),
            )
        )
        if existing_scope is None:
            session.add(
                ScopeAuthorization(
                    account_id=acc_id,
                    region_id=reg_id,
                    scope_type=stype,
                    is_active=True,
                    granted_by_account_id=admin_acc.id,
                    remark="Initial demo bootstrap",
                )
            )
            created_counts["scopes"] += 1

    # 4. 照明点位与设施资产
    point_specs = (
        (
            "SL-ZZ-JS-0001",
            jinshui.id,
            "花园路农业路口西北侧第3杆",
            "花园路",
            "MAIN",
            Decimal("113.6812340"),
            Decimal("34.7891230"),
        ),
        (
            "SL-ZZ-JS-0002",
            jinshui.id,
            "中州大道金水立交北侧匝道第1杆",
            "中州大道",
            "EXPRESS",
            Decimal("113.7112340"),
            Decimal("34.7791230"),
        ),
        (
            "SL-ZZ-ZD-0001",
            zhengdong.id,
            "商务内环路如意湖东侧支路第5杆",
            "商务内环路",
            "BRANCH",
            Decimal("113.7312340"),
            Decimal("34.7721230"),
        ),
    )
    points_by_code: dict[str, LightingPoint] = {}
    for pcode, reg_id, pname, rname, rlvl, lon, lat in point_specs:
        pt = await session.scalar(select(LightingPoint).where(LightingPoint.point_code == pcode))
        if pt is None:
            pt = LightingPoint(
                point_code=pcode,
                region_id=reg_id,
                name=pname,
                road_name=rname,
                road_level=rlvl,
                longitude=lon,
                latitude=lat,
                lamp_count=2,
                power_supply_type="GRID",
                status="IN_SERVICE",
            )
            session.add(pt)
            await session.flush()
            created_counts["points"] += 1
        points_by_code[pcode] = pt

    asset_specs = (
        ("AST-ZZ-JS-0001-L1", "SL-ZZ-JS-0001", "LUMINAIRE", "150W LED 主路灯具", "HIGH"),
        ("AST-ZZ-JS-0002-C1", "SL-ZZ-JS-0002", "CONTROL_BOX", "快速路配电箱 A1", "CRITICAL"),
        ("AST-ZZ-ZD-0001-L1", "SL-ZZ-ZD-0001", "LUMINAIRE", "80W LED 景观支路灯具", "NORMAL"),
    )
    for acode, pcode, atype, aname, imp in asset_specs:
        asset = await session.scalar(select(FacilityAsset).where(FacilityAsset.asset_code == acode))
        if asset is None:
            session.add(
                FacilityAsset(
                    asset_code=acode,
                    lighting_point_id=points_by_code[pcode].id,
                    asset_type=atype,
                    name=aname,
                    rated_power_w=Decimal("150.00"),
                    importance=imp,
                    status="IN_SERVICE",
                )
            )
            created_counts["assets"] += 1

    await session.flush()
    return created_counts
