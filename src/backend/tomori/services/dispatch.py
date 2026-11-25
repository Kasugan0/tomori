"""区域授权派工、候选人启发式排序推荐、接单/拒单/升级/完工与维修过程服务。

严格遵循 `docs/DESIGN.md` §2.1 (A6–A10, A14) / §2.2 (B1–B7) / §3.2 与
`docs/API.md` §2.3 / §2.4 / §7.2 / §7.3：
- 全部状态机表 (`fault_tickets`, `dispatch_records`, `rework_orders`) 的 `status`
  仅通过 `tomori.services.state_machine` 修改，本模块绝不直接赋值 `.status`。
- 403（操作人无权 / 区域授权失效）与 422（被指派维修员不合格
  `DISPATCH_TECHNICIAN_NOT_ELIGIBLE` / `TECHNICIAN_OVERLOADED`）严格分界。
- 完工及安全检查开启时，一次性收集电气安全检查全部缺失项并抛出
  `ELECTRICAL_SAFETY_CHECK_REQUIRED`。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Final

from sqlalchemy import and_, case, delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tomori.db.types import ensure_utc
from tomori.models.account import Account
from tomori.models.account_role import AccountRole
from tomori.models.audit_log import AuditLog
from tomori.models.dispatch_record import DispatchRecord
from tomori.models.fault_ticket import FaultTicket
from tomori.models.material_usage import MaterialUsage
from tomori.models.repair_process import RepairProcess
from tomori.models.rework_order import ReworkOrder
from tomori.models.role import Role
from tomori.models.scope_authorization import ScopeAuthorization
from tomori.schemas.common import NotEligibleReason
from tomori.schemas.dispatch import (
    CompleteRepairIn,
    DispatchCandidateItem,
    DispatchCandidatesResponseOut,
    DispatchEscalateIn,
    DispatchExcludedItem,
    DispatchRejectIn,
    RepairProcessPatchIn,
    TicketDispatchIn,
    TicketReassignIn,
)
from tomori.services.auth import get_account_role_code, has_valid_region_scope
from tomori.services.exceptions import (
    AI_CANDIDATE_NOT_CONFIRMED,
    DISPATCH_TECHNICIAN_NOT_ELIGIBLE,
    ELECTRICAL_SAFETY_CHECK_REQUIRED,
    FORBIDDEN_REGION_SCOPE,
    FORBIDDEN_ROLE,
    INVALID_STATE_TRANSITION,
    RESOURCE_NOT_FOUND,
    TECHNICIAN_OVERLOADED,
    VALIDATION_ERROR,
    AppError,
)
from tomori.services.grading import get_active_rule
from tomori.services.state_machine import (
    build_audit_entry,
    create_dispatch_record,
    enqueue_notifications,
    list_all_active_admin_ids,
    list_region_admin_ids,
    ticket_deep_link,
    transition_dispatch,
    transition_rework,
    transition_ticket,
)

__all__ = [
    "accept_dispatch",
    "collect_missing_safety_items",
    "complete_dispatch",
    "compute_dispatch_candidates",
    "dispatch_ticket",
    "escalate_dispatch",
    "patch_repair_process",
    "reassign_ticket",
    "reject_dispatch",
    "score_candidate",
    "verify_technician_eligible",
]

DEFAULT_DISPATCH_SCORING_WEIGHTS: Final[dict[str, Any]] = {
    "weights": {
        "workload": 0.40,
        "skill": 0.35,
        "on_time_rate": 0.25,
    },
    "max_recommendations": 10,
    "overload_penalty_factor": 0.0,
}
_MIN_UTC_DT: Final[datetime] = datetime.min.replace(tzinfo=UTC)
_DISPATCH_REJECT_ESCALATION_THRESHOLD: Final[int] = 3
_SAFETY_CHECKLIST_FIELDS: Final[tuple[str, ...]] = (
    "power_isolated",
    "voltage_verified_zero",
    "insulation_intact",
    "grounding_connected",
    "ppe_worn",
)


def _normalize_optional_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise AppError(
            VALIDATION_ERROR,
            "时间字段必须携带明确时区偏移",
            {"reason": "NAIVE_DATETIME_FORBIDDEN"},
        )
    return dt.astimezone(UTC)


def _is_scope_valid_at(scope: ScopeAuthorization, at: datetime) -> bool:
    if not scope.is_active:
        return False
    valid_from = ensure_utc(scope.valid_from)
    if valid_from > at:
        return False
    if scope.valid_to is not None:
        valid_to = ensure_utc(scope.valid_to)
        if valid_to <= at:
            return False
    return True


def score_candidate(
    *,
    active_dispatch_count: int,
    max_active_dispatch: int,
    skill_match: bool,
    on_time_rate: float,
    workload_weight: float = 0.40,
    skill_weight: float = 0.35,
    on_time_weight: float = 0.25,
) -> float:
    """纯函数计算候选维修员推荐分（四舍五入至 4 位小数）。"""
    effective_cap = max(max_active_dispatch, 1)
    workload_score = max(0.0, 1.0 - (active_dispatch_count / effective_cap))
    skill_score = 1.0 if skill_match else 0.0
    total = (
        workload_weight * workload_score
        + skill_weight * skill_score
        + on_time_weight * on_time_rate
    )
    return round(total, 4)


def collect_missing_safety_items(
    *,
    electrical_safety_checked: bool,
    checklist: Mapping[str, bool],
    safety_note: str | None,
) -> list[str]:
    """收集电气安全检查全部缺失项（总开关 + 5 项清单 + 非空安全备注）。"""
    missing: list[str] = []
    if not electrical_safety_checked:
        missing.append("electrical_safety_checked")
    for key in _SAFETY_CHECKLIST_FIELDS:
        if not bool(checklist.get(key, False)):
            missing.append(key)
    if not safety_note or not safety_note.strip():
        missing.append("safety_note")
    return missing


async def _load_scoring_config(
    session: AsyncSession,
) -> tuple[int, float, float, float, int]:
    loaded = await get_active_rule(session, "dispatch_scoring_weights")
    if loaded is None:
        rule_version = 1
        rule_value = DEFAULT_DISPATCH_SCORING_WEIGHTS
    else:
        rule_version, rule_value = loaded
    raw_weights = rule_value.get("weights")
    weights: Mapping[str, Any] = (
        raw_weights
        if isinstance(raw_weights, Mapping)
        else DEFAULT_DISPATCH_SCORING_WEIGHTS["weights"]
    )
    w_workload = float(weights.get("workload", 0.40))
    w_skill = float(weights.get("skill", 0.35))
    w_on_time = float(weights.get("on_time_rate", 0.25))
    max_rec = int(rule_value.get("max_recommendations", 10))
    return rule_version, w_workload, w_skill, w_on_time, max_rec


async def _count_active_dispatches(session: AsyncSession, account_id: int) -> int:
    cnt = await session.scalar(
        select(func.count(DispatchRecord.id)).where(
            DispatchRecord.technician_account_id == account_id,
            DispatchRecord.status.in_(("PENDING_ACCEPT", "ACCEPTED")),
        )
    )
    return int(cnt or 0)


async def _compute_technician_on_time_rate(
    session: AsyncSession,
    technician_account_id: int,
) -> float:
    late_case = case(
        (
            and_(
                FaultTicket.repair_deadline_at.is_not(None),
                DispatchRecord.completed_at.is_not(None),
                DispatchRecord.completed_at > FaultTicket.repair_deadline_at,
            ),
            1,
        ),
        else_=0,
    )
    row = (
        await session.execute(
            select(
                func.count(DispatchRecord.id),
                func.coalesce(func.sum(late_case), 0),
            )
            .join(FaultTicket, FaultTicket.id == DispatchRecord.ticket_id)
            .where(
                DispatchRecord.technician_account_id == technician_account_id,
                DispatchRecord.status == "COMPLETED",
            )
        )
    ).one()
    completed_count = int(row[0] or 0)
    late_count = int(row[1] or 0)
    if completed_count == 0:
        return 0.5
    return (completed_count - late_count) / completed_count


async def _build_candidate_snapshot(
    session: AsyncSession,
    *,
    ticket: FaultTicket,
    tech_account: Account,
    scope: ScopeAuthorization,
    active_count: int,
    max_active: int,
    dispatch_mode: str,
    extra: dict[str, Any] | None = None,
) -> tuple[Decimal, dict[str, Any]]:
    rule_version, w_workload, w_skill, w_on_time, _ = await _load_scoring_config(session)
    skill_match = ticket.fault_type in (tech_account.skills or [])
    on_time_rate = await _compute_technician_on_time_rate(session, tech_account.id)
    score_val = score_candidate(
        active_dispatch_count=active_count,
        max_active_dispatch=max_active,
        skill_match=skill_match,
        on_time_rate=on_time_rate,
        workload_weight=w_workload,
        skill_weight=w_skill,
        on_time_weight=w_on_time,
    )
    snapshot: dict[str, Any] = {
        "rule_code": "dispatch_scoring_weights",
        "rule_version": rule_version,
        "weights": {
            "workload": w_workload,
            "skill": w_skill,
            "on_time_rate": w_on_time,
        },
        "assigned_technician": {
            "account_id": tech_account.id,
            "username": tech_account.username,
            "full_name": tech_account.full_name,
            "scope_authorization_id": scope.id,
            "active_dispatch_count": active_count,
            "max_active_dispatch": max_active,
            "skill_match": skill_match,
            "on_time_rate": round(on_time_rate, 4),
            "recommend_score": score_val,
        },
        "dispatch_mode": dispatch_mode,
    }
    if extra:
        snapshot.update(extra)
    return Decimal(f"{score_val:.4f}"), snapshot


async def _list_region_inspector_ids(
    session: AsyncSession,
    region_id: int,
    *,
    at: datetime,
) -> list[int]:
    stmt = (
        select(Account.id)
        .join(AccountRole, AccountRole.account_id == Account.id)
        .join(Role, Role.id == AccountRole.role_id)
        .join(ScopeAuthorization, ScopeAuthorization.account_id == Account.id)
        .where(
            Account.status == "ACTIVE",
            Role.code == "INSPECTOR",
            ScopeAuthorization.region_id == region_id,
            ScopeAuthorization.scope_type == "INSPECT",
            ScopeAuthorization.is_active.is_(True),
            ScopeAuthorization.valid_from <= at,
            (ScopeAuthorization.valid_to.is_(None)) | (ScopeAuthorization.valid_to > at),
        )
        .distinct()
        .order_by(Account.id.asc())
    )
    return list((await session.scalars(stmt)).all())


def _validate_unique_material_codes(items: list[Any]) -> None:
    seen: set[str] = set()
    for item in items:
        code = item.material_code
        if code in seen:
            raise AppError(
                VALIDATION_ERROR,
                f"耗材编码 {code} 在同一维修过程中重复提交",
                {"field": "materials", "material_code": code},
            )
        seen.add(code)


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


async def _resolve_locked_dispatch_and_ticket(
    session: AsyncSession,
    *,
    dispatch_id: int | None,
    dispatch: DispatchRecord | None,
    ticket: FaultTicket | None,
) -> tuple[DispatchRecord, FaultTicket]:
    if dispatch is None:
        if dispatch_id is None:
            raise ValueError("Either dispatch_id or dispatch must be provided")
        dispatch = await session.scalar(
            select(DispatchRecord).where(DispatchRecord.id == dispatch_id).with_for_update()
        )
        if dispatch is None:
            raise AppError(
                RESOURCE_NOT_FOUND,
                "派工记录不存在",
                {"resource_type": "DISPATCH", "resource_id": dispatch_id},
            )
    if ticket is None:
        ticket = await session.scalar(
            select(FaultTicket).where(FaultTicket.id == dispatch.ticket_id).with_for_update()
        )
        if ticket is None:
            raise AppError(
                RESOURCE_NOT_FOUND,
                "关联故障工单不存在",
                {"resource_type": "FAULT_TICKET", "resource_id": dispatch.ticket_id},
            )
    return dispatch, ticket


async def compute_dispatch_candidates(
    session: AsyncSession,
    ticket: FaultTicket,
    *,
    now: datetime | None = None,
) -> DispatchCandidatesResponseOut:
    """计算工单推荐维修员列表与不合格排除清单（5 值原因 + 确定性三级并列裁决）。"""
    ts = ensure_utc(now)
    _, w_workload, w_skill, w_on_time, max_recommendations = await _load_scoring_config(session)

    tech_stmt = (
        select(Account, Role.code)
        .join(AccountRole, AccountRole.account_id == Account.id)
        .join(Role, Role.id == AccountRole.role_id)
        .where(Role.code == "TECHNICIAN")
    )
    scoped_stmt = (
        select(Account, Role.code)
        .outerjoin(AccountRole, AccountRole.account_id == Account.id)
        .outerjoin(Role, Role.id == AccountRole.role_id)
        .join(ScopeAuthorization, ScopeAuthorization.account_id == Account.id)
        .where(
            ScopeAuthorization.region_id == ticket.region_id,
            ScopeAuthorization.scope_type == "REPAIR",
        )
    )

    pool: dict[int, tuple[Account, str | None]] = {}
    for acc, role_code in (await session.execute(tech_stmt)).all():
        pool[acc.id] = (acc, role_code)
    for acc, role_code in (await session.execute(scoped_stmt)).all():
        pool.setdefault(acc.id, (acc, role_code))

    if not pool:
        return DispatchCandidatesResponseOut(
            ticket_id=ticket.id,
            generated_at=ts,
            candidates=[],
            excluded=[],
        )

    account_ids = sorted(pool)

    active_rows = (
        await session.execute(
            select(DispatchRecord.technician_account_id, func.count(DispatchRecord.id))
            .where(
                DispatchRecord.technician_account_id.in_(account_ids),
                DispatchRecord.status.in_(("PENDING_ACCEPT", "ACCEPTED")),
            )
            .group_by(DispatchRecord.technician_account_id)
        )
    ).all()
    active_counts: dict[int, int] = {int(aid): int(cnt) for aid, cnt in active_rows}

    last_dispatch_rows = (
        await session.execute(
            select(DispatchRecord.technician_account_id, func.max(DispatchRecord.dispatched_at))
            .where(DispatchRecord.technician_account_id.in_(account_ids))
            .group_by(DispatchRecord.technician_account_id)
        )
    ).all()
    last_dispatched_map: dict[int, datetime] = {
        int(aid): ensure_utc(last_dt) for aid, last_dt in last_dispatch_rows if last_dt is not None
    }

    late_case = case(
        (
            and_(
                FaultTicket.repair_deadline_at.is_not(None),
                DispatchRecord.completed_at.is_not(None),
                DispatchRecord.completed_at > FaultTicket.repair_deadline_at,
            ),
            1,
        ),
        else_=0,
    )
    perf_rows = (
        await session.execute(
            select(
                DispatchRecord.technician_account_id,
                func.count(DispatchRecord.id),
                func.coalesce(func.sum(late_case), 0),
            )
            .join(FaultTicket, FaultTicket.id == DispatchRecord.ticket_id)
            .where(
                DispatchRecord.technician_account_id.in_(account_ids),
                DispatchRecord.status == "COMPLETED",
            )
            .group_by(DispatchRecord.technician_account_id)
        )
    ).all()
    on_time_map: dict[int, float] = {}
    for aid, total_cnt, late_cnt in perf_rows:
        total_i = int(total_cnt or 0)
        late_i = int(late_cnt or 0)
        on_time_map[int(aid)] = (total_i - late_i) / total_i if total_i > 0 else 0.5

    scope_rows = list(
        (
            await session.scalars(
                select(ScopeAuthorization)
                .where(
                    ScopeAuthorization.account_id.in_(account_ids),
                    ScopeAuthorization.region_id == ticket.region_id,
                    ScopeAuthorization.scope_type == "REPAIR",
                )
                .order_by(ScopeAuthorization.id.asc())
            )
        ).all()
    )
    scopes_by_account: dict[int, list[ScopeAuthorization]] = {}
    for sc in scope_rows:
        scopes_by_account.setdefault(sc.account_id, []).append(sc)

    ranked: list[tuple[tuple[float, int, datetime, int], DispatchCandidateItem]] = []
    excluded: list[DispatchExcludedItem] = []

    for acc_id in account_ids:
        acc, role_code = pool[acc_id]
        active_cnt = active_counts.get(acc_id, 0)

        def _make_excluded(
            reason: NotEligibleReason,
            *,
            account: Account = acc,
            cnt: int = active_cnt,
        ) -> DispatchExcludedItem:
            return DispatchExcludedItem(
                account_id=account.id,
                username=account.username,
                full_name=account.full_name,
                reason=reason,
                active_dispatch_count=cnt,
                max_active_dispatch=account.max_active_dispatch,
            )

        if role_code != "TECHNICIAN":
            excluded.append(_make_excluded("WRONG_ROLE"))
            continue

        if acc.status != "ACTIVE":
            excluded.append(_make_excluded("ACCOUNT_INACTIVE"))
            continue

        acct_scopes = scopes_by_account.get(acc_id, [])
        valid_scope = next((s for s in acct_scopes if _is_scope_valid_at(s, ts)), None)
        if valid_scope is None:
            reason: NotEligibleReason = "SCOPE_EXPIRED" if acct_scopes else "NO_REGION_SCOPE"
            excluded.append(_make_excluded(reason))
            continue

        if active_cnt >= acc.max_active_dispatch:
            excluded.append(_make_excluded("OVERLOADED"))
            continue

        skill_match = ticket.fault_type in (acc.skills or [])
        on_time_rate = on_time_map.get(acc_id, 0.5)
        rec_score = score_candidate(
            active_dispatch_count=active_cnt,
            max_active_dispatch=acc.max_active_dispatch,
            skill_match=skill_match,
            on_time_rate=on_time_rate,
            workload_weight=w_workload,
            skill_weight=w_skill,
            on_time_weight=w_on_time,
        )
        sort_key = (
            -rec_score,
            active_cnt,
            last_dispatched_map.get(acc_id, _MIN_UTC_DT),
            acc_id,
        )
        ranked.append(
            (
                sort_key,
                DispatchCandidateItem(
                    account_id=acc.id,
                    username=acc.username,
                    full_name=acc.full_name,
                    scope_authorization_id=valid_scope.id,
                    active_dispatch_count=active_cnt,
                    max_active_dispatch=acc.max_active_dispatch,
                    skill_match=skill_match,
                    on_time_rate=round(on_time_rate, 4),
                    recommend_score=rec_score,
                ),
            )
        )

    ranked.sort(key=lambda pair: pair[0])
    candidates = [item for _, item in ranked[:max_recommendations]]
    return DispatchCandidatesResponseOut(
        ticket_id=ticket.id,
        generated_at=ts,
        candidates=candidates,
        excluded=excluded,
    )


async def verify_technician_eligible(
    session: AsyncSession,
    *,
    ticket: FaultTicket,
    technician_account_id: int,
    scope_authorization_id: int,
    now: datetime | None = None,
) -> tuple[Account, ScopeAuthorization, int, int]:
    """派工/改派时全量复核目标维修员资格。

    返回 `(Account, ScopeAuthorization, active_dispatch_count, max_active_dispatch)`。
    """
    ts = ensure_utc(now)

    account = await session.scalar(select(Account).where(Account.id == technician_account_id))
    if account is None:
        raise AppError(
            RESOURCE_NOT_FOUND,
            "目标维修人员账号不存在",
            {"resource_type": "ACCOUNT", "resource_id": technician_account_id},
        )

    role_code = await get_account_role_code(session, account.id)
    if role_code != "TECHNICIAN":
        raise AppError(
            DISPATCH_TECHNICIAN_NOT_ELIGIBLE,
            "目标账号不是维修人员角色",
            {
                "reason": "WRONG_ROLE",
                "account_id": account.id,
                "role_code": role_code,
            },
        )

    if account.status != "ACTIVE":
        raise AppError(
            DISPATCH_TECHNICIAN_NOT_ELIGIBLE,
            "目标维修人员账号已停用或锁定",
            {
                "reason": "ACCOUNT_INACTIVE",
                "account_id": account.id,
                "status": account.status,
            },
        )

    provided = await session.scalar(
        select(ScopeAuthorization).where(ScopeAuthorization.id == scope_authorization_id)
    )
    scope: ScopeAuthorization | None = None
    if (
        provided is not None
        and provided.account_id == technician_account_id
        and provided.region_id == ticket.region_id
        and provided.scope_type == "REPAIR"
    ):
        if not _is_scope_valid_at(provided, ts):
            raise AppError(
                DISPATCH_TECHNICIAN_NOT_ELIGIBLE,
                "目标维修人员在该运维区域的维修授权已停用或已过期",
                {
                    "reason": "SCOPE_EXPIRED",
                    "account_id": technician_account_id,
                    "region_id": ticket.region_id,
                    "scope_authorization_id": provided.id,
                },
            )
        scope = provided
    else:
        pair_scopes = list(
            (
                await session.scalars(
                    select(ScopeAuthorization)
                    .where(
                        ScopeAuthorization.account_id == technician_account_id,
                        ScopeAuthorization.region_id == ticket.region_id,
                        ScopeAuthorization.scope_type == "REPAIR",
                    )
                    .order_by(ScopeAuthorization.id.asc())
                )
            ).all()
        )
        valid_pair = next((s for s in pair_scopes if _is_scope_valid_at(s, ts)), None)
        if valid_pair is not None:
            scope = valid_pair
        elif pair_scopes:
            raise AppError(
                DISPATCH_TECHNICIAN_NOT_ELIGIBLE,
                "目标维修人员在该运维区域的维修授权已停用或已过期",
                {
                    "reason": "SCOPE_EXPIRED",
                    "account_id": technician_account_id,
                    "region_id": ticket.region_id,
                },
            )
        else:
            raise AppError(
                DISPATCH_TECHNICIAN_NOT_ELIGIBLE,
                "目标维修人员未获此运维区域的维修授权",
                {
                    "reason": "NO_REGION_SCOPE",
                    "account_id": technician_account_id,
                    "region_id": ticket.region_id,
                },
            )

    active_count = await _count_active_dispatches(session, technician_account_id)
    if active_count >= account.max_active_dispatch:
        raise AppError(
            TECHNICIAN_OVERLOADED,
            "目标维修人员当前在途工单数已达上限",
            {
                "reason": "OVERLOADED",
                "account_id": technician_account_id,
                "active_dispatch_count": active_count,
                "max_active_dispatch": account.max_active_dispatch,
            },
        )

    return account, scope, active_count, account.max_active_dispatch


async def dispatch_ticket(
    session: AsyncSession,
    *,
    ticket_id: int | None = None,
    ticket: FaultTicket | None = None,
    payload: TicketDispatchIn,
    actor: Account,
    actor_role_code: str,
    now: datetime | None = None,
) -> tuple[DispatchRecord, FaultTicket, list[AuditLog]]:
    """执行工单派工（A6 / A14 + B1 + C3）。"""
    ts = ensure_utc(now)
    target_ticket = await _resolve_locked_ticket(session, ticket_id=ticket_id, ticket=ticket)

    if not target_ticket.confirmed_by_inspector:
        raise AppError(
            AI_CANDIDATE_NOT_CONFIRMED,
            "AI 故障候选尚未经巡查员人工确认，禁止派工",
            {"ticket_id": target_ticket.id, "status": target_ticket.status},
        )

    if target_ticket.status not in {"PENDING_DISPATCH", "REWORK_PENDING_DISPATCH"}:
        raise AppError(
            INVALID_STATE_TRANSITION,
            f"当前工单状态 {target_ticket.status} 不允许派工",
            {
                "entity": "fault_tickets",
                "from_status": target_ticket.status,
                "to_status": "DISPATCHED",
                "allowed_from": ["PENDING_DISPATCH", "REWORK_PENDING_DISPATCH"],
            },
        )

    tech_account, scope, active_count, max_active = await verify_technician_eligible(
        session,
        ticket=target_ticket,
        technician_account_id=payload.technician_account_id,
        scope_authorization_id=payload.scope_authorization_id,
        now=ts,
    )

    existing_active = await session.scalar(
        select(DispatchRecord).where(
            DispatchRecord.ticket_id == target_ticket.id,
            DispatchRecord.status.in_(("PENDING_ACCEPT", "ACCEPTED")),
        )
    )
    if existing_active is not None:
        raise AppError(
            INVALID_STATE_TRANSITION,
            "该工单已存在活跃派工记录，请使用改派流程",
            {
                "entity": "dispatch_records",
                "ticket_id": target_ticket.id,
                "existing_dispatch_id": existing_active.id,
                "existing_status": existing_active.status,
            },
        )

    rec_score, cand_snapshot = await _build_candidate_snapshot(
        session,
        ticket=target_ticket,
        tech_account=tech_account,
        scope=scope,
        active_count=active_count,
        max_active=max_active,
        dispatch_mode=payload.dispatch_mode,
        extra={"remark": payload.remark} if payload.remark is not None else None,
    )

    rework_order_id: int | None = None
    if target_ticket.status == "REWORK_PENDING_DISPATCH":
        rework = await session.scalar(
            select(ReworkOrder)
            .where(
                ReworkOrder.ticket_id == target_ticket.id,
                ReworkOrder.status == "PENDING_DISPATCH",
                ReworkOrder.requires_manual_review.is_(False),
            )
            .with_for_update()
        )
        if rework is None:
            raise AppError(
                INVALID_STATE_TRANSITION,
                "返修待派工单缺少可用的活跃返修单",
                {"entity": "rework_orders", "ticket_id": target_ticket.id},
            )
        rework_order_id = rework.id
        await transition_rework(
            session,
            rework,
            to_status="DISPATCHED",
            actor_account_id=actor.id,
            action="TICKET_DISPATCHED",
            now=ts,
            persist_audit=False,
        )

    accept_deadline_at = target_ticket.repair_deadline_at or (ts + timedelta(hours=48))
    try:
        dispatch = await create_dispatch_record(
            session,
            ticket=target_ticket,
            technician_account_id=tech_account.id,
            scope_authorization_id=scope.id,
            dispatched_by_account_id=actor.id,
            dispatch_mode=payload.dispatch_mode,
            accept_deadline_at=accept_deadline_at,
            rework_order_id=rework_order_id,
            recommend_score=rec_score,
            candidate_snapshot_json=cand_snapshot,
            dispatched_at=ts,
        )
    except IntegrityError as exc:
        raise AppError(
            INVALID_STATE_TRANSITION,
            "该工单当前已存在活跃派工记录",
            {"entity": "dispatch_records", "ticket_id": target_ticket.id},
        ) from exc

    snapshot_extra: dict[str, Any] = {
        "dispatch_id": dispatch.id,
        "technician_account_id": tech_account.id,
        "scope_authorization_id": scope.id,
        "dispatch_mode": payload.dispatch_mode,
    }
    if rework_order_id is not None:
        snapshot_extra["rework_order_id"] = rework_order_id

    ticket_audit = await transition_ticket(
        session,
        target_ticket,
        to_status="DISPATCHED",
        actor_account_id=actor.id,
        actor_username=actor.username,
        actor_role_code=actor_role_code,
        action="TICKET_DISPATCHED",
        now=ts,
        snapshot_extra=snapshot_extra,
        notify=[("TICKET_DISPATCHED", [tech_account.id])],
        persist_audit=False,
    )
    return dispatch, target_ticket, [ticket_audit]


async def reassign_ticket(
    session: AsyncSession,
    *,
    ticket_id: int | None = None,
    ticket: FaultTicket | None = None,
    payload: TicketReassignIn,
    actor: Account,
    actor_role_code: str,
    now: datetime | None = None,
) -> tuple[DispatchRecord, FaultTicket, list[AuditLog]]:
    """管理员改派 / 升级改派（B6 / B7 + B1；保持工单处于 DISPATCHED 且不顺延法定 48h 时限）。"""
    ts = ensure_utc(now)
    target_ticket = await _resolve_locked_ticket(session, ticket_id=ticket_id, ticket=ticket)

    if target_ticket.status != "DISPATCHED":
        raise AppError(
            INVALID_STATE_TRANSITION,
            f"当前工单状态 {target_ticket.status} 不允许改派（仅 DISPATCHED 状态可改派）",
            {
                "entity": "fault_tickets",
                "from_status": target_ticket.status,
                "to_status": "DISPATCHED",
                "allowed_from": ["DISPATCHED"],
            },
        )

    tech_account, scope, active_count, max_active = await verify_technician_eligible(
        session,
        ticket=target_ticket,
        technician_account_id=payload.technician_account_id,
        scope_authorization_id=payload.scope_authorization_id,
        now=ts,
    )

    old_dispatch = await session.scalar(
        select(DispatchRecord)
        .where(
            DispatchRecord.ticket_id == target_ticket.id,
            DispatchRecord.status.in_(("PENDING_ACCEPT", "ACCEPTED")),
        )
        .with_for_update()
    )
    if old_dispatch is None:
        raise AppError(
            INVALID_STATE_TRANSITION,
            "当前工单缺少可改派的活跃派工记录",
            {"entity": "dispatch_records", "ticket_id": target_ticket.id},
        )

    old_tech_id = old_dispatch.technician_account_id
    target_old_status = "ESCALATED" if payload.escalate else "CANCELLED"
    old_action = "DISPATCH_ESCALATED" if payload.escalate else "DISPATCH_CANCELLED"
    await transition_dispatch(
        session,
        old_dispatch,
        to_status=target_old_status,
        actor_account_id=actor.id,
        action=old_action,
        reason=payload.reason,
        now=ts,
        persist_audit=False,
    )
    old_dispatch.accepted_at = None
    await session.flush()

    rec_score, cand_snapshot = await _build_candidate_snapshot(
        session,
        ticket=target_ticket,
        tech_account=tech_account,
        scope=scope,
        active_count=active_count,
        max_active=max_active,
        dispatch_mode="REASSIGN",
        extra={
            "reassign_reason": payload.reason,
            "escalate": payload.escalate,
            "previous_dispatch_id": old_dispatch.id,
        },
    )

    accept_deadline_at = target_ticket.repair_deadline_at or (ts + timedelta(hours=48))
    new_dispatch = await create_dispatch_record(
        session,
        ticket=target_ticket,
        technician_account_id=tech_account.id,
        scope_authorization_id=scope.id,
        dispatched_by_account_id=actor.id,
        dispatch_mode="REASSIGN",
        accept_deadline_at=accept_deadline_at,
        rework_order_id=old_dispatch.rework_order_id,
        recommend_score=rec_score,
        candidate_snapshot_json=cand_snapshot,
        dispatched_at=ts,
    )

    enqueue_notifications(
        session,
        notify=[
            ("TICKET_DISPATCHED", [tech_account.id]),
            ("DISPATCH_TIMEOUT_REASSIGNED", [old_tech_id]),
        ],
        title=f"工单 {target_ticket.ticket_no} 派工已改派",
        body=f"工单 {target_ticket.ticket_no} 已改派：{payload.reason}",
        related_ticket_id=target_ticket.id,
        related_dispatch_id=new_dispatch.id,
        payload_json={
            "ticket_id": target_ticket.id,
            "old_dispatch_id": old_dispatch.id,
            "new_dispatch_id": new_dispatch.id,
            "deep_link": ticket_deep_link(target_ticket.id),
        },
    )

    ticket_audit = build_audit_entry(
        action="TICKET_REASSIGNED",
        resource_type="FAULT_TICKET",
        resource_id=target_ticket.id,
        ticket_id=target_ticket.id,
        actor_account_id=actor.id,
        actor_username=actor.username,
        actor_role_code=actor_role_code,
        snapshot={
            "from_status": target_ticket.status,
            "to_status": target_ticket.status,
            "old_dispatch_id": old_dispatch.id,
            "old_dispatch_status": old_dispatch.status,
            "old_technician_account_id": old_tech_id,
            "new_dispatch_id": new_dispatch.id,
            "new_technician_account_id": tech_account.id,
            "reason": payload.reason,
            "escalate": payload.escalate,
        },
    )
    return new_dispatch, target_ticket, [ticket_audit]


async def accept_dispatch(
    session: AsyncSession,
    *,
    dispatch_id: int | None = None,
    dispatch: DispatchRecord | None = None,
    ticket: FaultTicket | None = None,
    actor: Account,
    actor_role_code: str,
    now: datetime | None = None,
) -> tuple[DispatchRecord, FaultTicket, list[AuditLog]]:
    """维修员接单（A7 + B2 + C4，接单时再次强校验区域 REPAIR 授权与接单时限）。"""
    ts = ensure_utc(now)
    target_dispatch, target_ticket = await _resolve_locked_dispatch_and_ticket(
        session,
        dispatch_id=dispatch_id,
        dispatch=dispatch,
        ticket=ticket,
    )

    if target_dispatch.technician_account_id != actor.id:
        raise AppError(
            FORBIDDEN_ROLE,
            "仅被指派维修人员本人可执行接单操作",
            {
                "dispatch_id": target_dispatch.id,
                "expected_technician_id": target_dispatch.technician_account_id,
                "actor_account_id": actor.id,
            },
        )

    valid_scope = await has_valid_region_scope(
        session,
        account_id=actor.id,
        region_id=target_ticket.region_id,
        scope_type="REPAIR",
        at=ts,
    )
    if valid_scope is None:
        raise AppError(
            FORBIDDEN_REGION_SCOPE,
            "您在该运维区域的维修授权已被停用或已过期，无法接单",
            {
                "region_id": target_ticket.region_id,
                "required_scope": "REPAIR",
                "dispatch_id": target_dispatch.id,
            },
        )

    if target_dispatch.status != "PENDING_ACCEPT":
        raise AppError(
            INVALID_STATE_TRANSITION,
            f"派工记录当前状态 {target_dispatch.status} 不允许接单",
            {
                "entity": "dispatch_records",
                "from_status": target_dispatch.status,
                "to_status": "ACCEPTED",
                "allowed_from": ["PENDING_ACCEPT"],
            },
        )

    accept_deadline = ensure_utc(target_dispatch.accept_deadline_at)
    if ts > accept_deadline:
        raise AppError(
            INVALID_STATE_TRANSITION,
            "已超过派工接单截止时间，请等待 SLA 扫描回收或管理员改派",
            {
                "entity": "dispatch_records",
                "from_status": target_dispatch.status,
                "to_status": "ACCEPTED",
                "accept_deadline_at": accept_deadline.isoformat(),
                "now": ts.isoformat(),
            },
        )

    await transition_dispatch(
        session,
        target_dispatch,
        to_status="ACCEPTED",
        actor_account_id=actor.id,
        action="DISPATCH_ACCEPTED",
        now=ts,
        persist_audit=False,
    )

    if target_dispatch.rework_order_id is not None:
        rework = await session.scalar(
            select(ReworkOrder)
            .where(ReworkOrder.id == target_dispatch.rework_order_id)
            .with_for_update()
        )
        if rework is not None and rework.status == "DISPATCHED":
            await transition_rework(
                session,
                rework,
                to_status="IN_REPAIR",
                actor_account_id=actor.id,
                action="DISPATCH_ACCEPTED",
                now=ts,
                persist_audit=False,
            )

    region_admin_ids = await list_region_admin_ids(session, target_ticket.region_id)
    snapshot_extra: dict[str, Any] = {"dispatch_id": target_dispatch.id}
    if target_dispatch.rework_order_id is not None:
        snapshot_extra["rework_order_id"] = target_dispatch.rework_order_id

    ticket_audit = await transition_ticket(
        session,
        target_ticket,
        to_status="IN_REPAIR",
        actor_account_id=actor.id,
        actor_username=actor.username,
        actor_role_code=actor_role_code,
        action="DISPATCH_ACCEPTED",
        now=ts,
        snapshot_extra=snapshot_extra,
        notify=[("DISPATCH_ACCEPTED", [*region_admin_ids, target_ticket.reporter_account_id])],
        persist_audit=False,
    )
    return target_dispatch, target_ticket, [ticket_audit]


async def reject_dispatch(
    session: AsyncSession,
    *,
    dispatch_id: int | None = None,
    dispatch: DispatchRecord | None = None,
    ticket: FaultTicket | None = None,
    payload: DispatchRejectIn,
    actor: Account,
    actor_role_code: str,
    now: datetime | None = None,
) -> tuple[DispatchRecord, FaultTicket, list[AuditLog]]:
    """维修员拒单（A8 + B3；累计拒单 >= 3 次强制流转 MANUAL_REVIEW）。"""
    ts = ensure_utc(now)
    target_dispatch, target_ticket = await _resolve_locked_dispatch_and_ticket(
        session,
        dispatch_id=dispatch_id,
        dispatch=dispatch,
        ticket=ticket,
    )

    if target_dispatch.technician_account_id != actor.id:
        raise AppError(
            FORBIDDEN_ROLE,
            "仅被指派维修人员本人可执行拒单操作",
            {
                "dispatch_id": target_dispatch.id,
                "expected_technician_id": target_dispatch.technician_account_id,
                "actor_account_id": actor.id,
            },
        )

    if target_dispatch.status != "PENDING_ACCEPT":
        raise AppError(
            INVALID_STATE_TRANSITION,
            f"派工记录当前状态 {target_dispatch.status} 不允许拒单",
            {
                "entity": "dispatch_records",
                "from_status": target_dispatch.status,
                "to_status": "REJECTED",
                "allowed_from": ["PENDING_ACCEPT"],
            },
        )

    reject_reason = payload.reject_reason.strip()
    if not reject_reason:
        raise AppError(
            VALIDATION_ERROR,
            "拒单原因 reject_reason 不能为空白",
            {"field": "reject_reason"},
        )

    await transition_dispatch(
        session,
        target_dispatch,
        to_status="REJECTED",
        actor_account_id=actor.id,
        action="DISPATCH_REJECTED",
        reason=reject_reason,
        now=ts,
        persist_audit=False,
    )

    target_ticket.dispatch_reject_count += 1
    exceeded = target_ticket.dispatch_reject_count >= _DISPATCH_REJECT_ESCALATION_THRESHOLD

    region_admin_ids = await list_region_admin_ids(session, target_ticket.region_id)
    notify_list: list[tuple[str, list[int]]] = [("DISPATCH_REJECTED", region_admin_ids)]
    if exceeded:
        all_admin_ids = await list_all_active_admin_ids(session)
        notify_list.append(("MANUAL_REVIEW_REQUIRED", all_admin_ids))

    ticket_audit = await transition_ticket(
        session,
        target_ticket,
        to_status="MANUAL_REVIEW" if exceeded else "PENDING_DISPATCH",
        actor_account_id=actor.id,
        actor_username=actor.username,
        actor_role_code=actor_role_code,
        action="DISPATCH_REJECTED",
        manual_review_reason="dispatch_reject_threshold_exceeded" if exceeded else None,
        now=ts,
        snapshot_extra={
            "dispatch_id": target_dispatch.id,
            "reject_reason": reject_reason,
            "dispatch_reject_count": target_ticket.dispatch_reject_count,
            "exceeded_threshold": exceeded,
        },
        notify=notify_list,
        persist_audit=False,
    )
    return target_dispatch, target_ticket, [ticket_audit]


async def escalate_dispatch(
    session: AsyncSession,
    *,
    dispatch_id: int | None = None,
    dispatch: DispatchRecord | None = None,
    ticket: FaultTicket | None = None,
    payload: DispatchEscalateIn | None = None,
    escalate_reason: str | None = None,
    actor: Account,
    actor_role_code: str,
    now: datetime | None = None,
) -> tuple[DispatchRecord, FaultTicket]:
    """维修员异常升级（A10 + B6；直接持久化审计与通知）。"""
    ts = ensure_utc(now)
    target_dispatch, target_ticket = await _resolve_locked_dispatch_and_ticket(
        session,
        dispatch_id=dispatch_id,
        dispatch=dispatch,
        ticket=ticket,
    )

    if target_dispatch.technician_account_id != actor.id:
        raise AppError(
            FORBIDDEN_ROLE,
            "仅被指派维修人员本人可发起异常升级",
            {
                "dispatch_id": target_dispatch.id,
                "expected_technician_id": target_dispatch.technician_account_id,
                "actor_account_id": actor.id,
            },
        )

    if target_dispatch.status != "ACCEPTED" or target_ticket.status != "IN_REPAIR":
        raise AppError(
            INVALID_STATE_TRANSITION,
            (
                f"仅已接单且维修中的工单允许异常升级"
                f"（派工={target_dispatch.status}, 工单={target_ticket.status}）"
            ),
            {
                "entity": "dispatch_records",
                "from_status": target_dispatch.status,
                "ticket_status": target_ticket.status,
                "to_status": "ESCALATED",
            },
        )

    raw_reason = payload.escalate_reason if payload is not None else (escalate_reason or "")
    resolved_reason = raw_reason.strip()
    if not resolved_reason:
        raise AppError(
            VALIDATION_ERROR,
            "异常升级原因 escalate_reason 不能为空白",
            {"field": "escalate_reason"},
        )

    await transition_dispatch(
        session,
        target_dispatch,
        to_status="ESCALATED",
        actor_account_id=actor.id,
        action="DISPATCH_ESCALATED",
        reason=resolved_reason,
        now=ts,
        persist_audit=False,
    )
    # 满足 ck_dispatch_records_accepted_at_required：仅 ACCEPTED/COMPLETED 允许 accepted_at 非空
    target_dispatch.accepted_at = None

    all_admin_ids = await list_all_active_admin_ids(session)
    await transition_ticket(
        session,
        target_ticket,
        to_status="MANUAL_REVIEW",
        actor_account_id=actor.id,
        actor_username=actor.username,
        actor_role_code=actor_role_code,
        action="DISPATCH_ESCALATED",
        manual_review_reason=resolved_reason,
        now=ts,
        snapshot_extra={
            "dispatch_id": target_dispatch.id,
            "escalate_reason": resolved_reason,
        },
        notify=[
            ("TICKET_ESCALATED", all_admin_ids),
            ("MANUAL_REVIEW_REQUIRED", all_admin_ids),
        ],
        persist_audit=True,
    )
    return target_dispatch, target_ticket


async def complete_dispatch(
    session: AsyncSession,
    *,
    dispatch_id: int | None = None,
    dispatch: DispatchRecord | None = None,
    ticket: FaultTicket | None = None,
    payload: CompleteRepairIn,
    actor: Account,
    actor_role_code: str,
    now: datetime | None = None,
) -> tuple[DispatchRecord, FaultTicket, RepairProcess, list[MaterialUsage], list[AuditLog]]:
    """维修完工提交（A9 + B5 + C5 + 电气安全 6 件套强制收集全部缺失项）。"""
    ts = ensure_utc(now)
    target_dispatch, target_ticket = await _resolve_locked_dispatch_and_ticket(
        session,
        dispatch_id=dispatch_id,
        dispatch=dispatch,
        ticket=ticket,
    )

    if target_dispatch.technician_account_id != actor.id:
        raise AppError(
            FORBIDDEN_ROLE,
            "仅被指派维修人员本人可提交维修完工",
            {
                "dispatch_id": target_dispatch.id,
                "expected_technician_id": target_dispatch.technician_account_id,
                "actor_account_id": actor.id,
            },
        )

    if target_dispatch.status != "ACCEPTED" or target_ticket.status != "IN_REPAIR":
        raise AppError(
            INVALID_STATE_TRANSITION,
            (
                f"仅维修中工单允许提交完工"
                f"（派工={target_dispatch.status}, 工单={target_ticket.status}）"
            ),
            {
                "entity": "fault_tickets",
                "from_status": target_ticket.status,
                "dispatch_status": target_dispatch.status,
                "to_status": "PENDING_ACCEPTANCE",
            },
        )

    checklist_dict: dict[str, bool] = {
        key: bool(value) for key, value in payload.safety_checklist.model_dump().items()
    }
    missing = collect_missing_safety_items(
        electrical_safety_checked=payload.electrical_safety_checked,
        checklist=checklist_dict,
        safety_note=payload.safety_note,
    )
    if missing:
        raise AppError(
            ELECTRICAL_SAFETY_CHECK_REQUIRED,
            "未完成现场电气安全检查六件套及安全备注，严禁提交完工",
            {"missing_items": missing},
        )

    _validate_unique_material_codes(payload.materials)

    arrived_at = _normalize_optional_utc(payload.arrived_at)
    assert arrived_at is not None
    repair_started_at = _normalize_optional_utc(payload.repair_started_at)
    repair_finished_at = _normalize_optional_utc(payload.repair_finished_at) or ts
    if repair_started_at is not None and repair_finished_at < repair_started_at:
        raise AppError(
            VALIDATION_ERROR,
            "repair_finished_at 不得早于 repair_started_at",
            {
                "repair_started_at": repair_started_at.isoformat(),
                "repair_finished_at": repair_finished_at.isoformat(),
            },
        )

    await transition_dispatch(
        session,
        target_dispatch,
        to_status="COMPLETED",
        actor_account_id=actor.id,
        action="REPAIR_COMPLETED",
        now=ts,
        persist_audit=False,
    )

    repair_process = await session.scalar(
        select(RepairProcess)
        .where(RepairProcess.dispatch_record_id == target_dispatch.id)
        .with_for_update()
    )
    if repair_process is None:
        repair_process = RepairProcess(
            dispatch_record_id=target_dispatch.id,
            ticket_id=target_ticket.id,
            technician_account_id=actor.id,
            arrived_at=arrived_at,
            electrical_safety_checked=payload.electrical_safety_checked,
            safety_checklist_json=checklist_dict,
            safety_note=payload.safety_note.strip(),
            root_cause=payload.root_cause,
            repair_summary=payload.repair_summary.strip(),
            repair_started_at=repair_started_at,
            repair_finished_at=repair_finished_at,
        )
        session.add(repair_process)
        await session.flush()
    else:
        repair_process.arrived_at = arrived_at
        repair_process.electrical_safety_checked = payload.electrical_safety_checked
        repair_process.safety_checklist_json = checklist_dict
        repair_process.safety_note = payload.safety_note.strip()
        repair_process.root_cause = payload.root_cause
        repair_process.repair_summary = payload.repair_summary.strip()
        repair_process.repair_started_at = repair_started_at
        repair_process.repair_finished_at = repair_finished_at
        await session.execute(
            delete(MaterialUsage).where(MaterialUsage.repair_process_id == repair_process.id)
        )
        await session.flush()

    material_usages: list[MaterialUsage] = []
    for item in payload.materials:
        mat = MaterialUsage(
            repair_process_id=repair_process.id,
            ticket_id=target_ticket.id,
            material_code=item.material_code,
            material_name=item.material_name,
            quantity=item.quantity,
            unit=item.unit,
            unit_price=item.unit_price,
        )
        session.add(mat)
        material_usages.append(mat)
    if material_usages:
        await session.flush()

    if target_dispatch.rework_order_id is not None:
        rework = await session.scalar(
            select(ReworkOrder)
            .where(ReworkOrder.id == target_dispatch.rework_order_id)
            .with_for_update()
        )
        if rework is not None and rework.status == "IN_REPAIR":
            await transition_rework(
                session,
                rework,
                to_status="PENDING_ACCEPTANCE",
                actor_account_id=actor.id,
                action="REPAIR_COMPLETED",
                now=ts,
                persist_audit=False,
            )

    inspector_ids = await _list_region_inspector_ids(session, target_ticket.region_id, at=ts)
    notify_recipients = [target_ticket.reporter_account_id, *inspector_ids]

    snapshot_extra: dict[str, Any] = {
        "dispatch_id": target_dispatch.id,
        "repair_process_id": repair_process.id,
        "material_count": len(material_usages),
    }
    if target_dispatch.rework_order_id is not None:
        snapshot_extra["rework_order_id"] = target_dispatch.rework_order_id

    ticket_audit = await transition_ticket(
        session,
        target_ticket,
        to_status="PENDING_ACCEPTANCE",
        actor_account_id=actor.id,
        actor_username=actor.username,
        actor_role_code=actor_role_code,
        action="REPAIR_COMPLETED",
        now=ts,
        snapshot_extra=snapshot_extra,
        notify=[("REPAIR_COMPLETED", notify_recipients)],
        persist_audit=False,
    )
    return target_dispatch, target_ticket, repair_process, material_usages, [ticket_audit]


async def patch_repair_process(
    session: AsyncSession,
    *,
    repair_process_id: int | None = None,
    repair: RepairProcess | None = None,
    ticket: FaultTicket | None = None,
    payload: RepairProcessPatchIn,
    actor: Account,
    actor_role_code: str,
) -> RepairProcess:
    """IN_REPAIR 期间更新维修过程并全量覆盖耗材明细。"""
    if repair is None:
        if repair_process_id is None:
            raise ValueError("Either repair_process_id or repair must be provided")
        repair = await session.scalar(
            select(RepairProcess).where(RepairProcess.id == repair_process_id).with_for_update()
        )
        if repair is None:
            raise AppError(
                RESOURCE_NOT_FOUND,
                "维修过程记录不存在",
                {"resource_type": "REPAIR_PROCESS", "resource_id": repair_process_id},
            )

    if actor_role_code != "ADMIN" and repair.technician_account_id != actor.id:
        raise AppError(
            FORBIDDEN_ROLE,
            "仅运维管理员或该维修单所属维修人员可修改维修过程记录",
            {
                "repair_process_id": repair.id,
                "technician_account_id": repair.technician_account_id,
                "actor_account_id": actor.id,
                "actor_role_code": actor_role_code,
            },
        )

    if ticket is None:
        ticket = await session.scalar(
            select(FaultTicket).where(FaultTicket.id == repair.ticket_id).with_for_update()
        )
        if ticket is None:
            raise AppError(
                RESOURCE_NOT_FOUND,
                "关联故障工单不存在",
                {"resource_type": "FAULT_TICKET", "resource_id": repair.ticket_id},
            )

    if ticket.status != "IN_REPAIR":
        raise AppError(
            INVALID_STATE_TRANSITION,
            f"仅在工单处于 IN_REPAIR 时可修改维修过程，当前状态为 {ticket.status}",
            {
                "entity": "fault_tickets",
                "from_status": ticket.status,
                "allowed_statuses": ["IN_REPAIR"],
            },
        )

    new_checked = (
        payload.electrical_safety_checked
        if payload.electrical_safety_checked is not None
        else repair.electrical_safety_checked
    )
    new_checklist: dict[str, bool] = (
        {key: bool(val) for key, val in payload.safety_checklist.model_dump().items()}
        if payload.safety_checklist is not None
        else {key: bool(val) for key, val in dict(repair.safety_checklist_json).items()}
    )
    new_safety_note = payload.safety_note if payload.safety_note is not None else repair.safety_note

    if new_checked:
        missing = collect_missing_safety_items(
            electrical_safety_checked=True,
            checklist=new_checklist,
            safety_note=new_safety_note,
        )
        if missing:
            raise AppError(
                ELECTRICAL_SAFETY_CHECK_REQUIRED,
                "启用电气安全检查时必须完成全部 6 项安全检查并填写安全备注",
                {"missing_items": missing},
            )
    elif not new_safety_note or not new_safety_note.strip():
        raise AppError(
            VALIDATION_ERROR,
            "safety_note 不能为空白",
            {"field": "safety_note"},
        )

    new_started_at = (
        _normalize_optional_utc(payload.repair_started_at)
        if "repair_started_at" in payload.model_fields_set
        else repair.repair_started_at
    )
    new_finished_at = (
        _normalize_optional_utc(payload.repair_finished_at)
        if "repair_finished_at" in payload.model_fields_set
        else repair.repair_finished_at
    )
    if (
        new_started_at is not None
        and new_finished_at is not None
        and new_finished_at < new_started_at
    ):
        raise AppError(
            VALIDATION_ERROR,
            "repair_finished_at 不得早于 repair_started_at",
            {
                "repair_started_at": new_started_at.isoformat(),
                "repair_finished_at": new_finished_at.isoformat(),
            },
        )

    if payload.materials is not None:
        _validate_unique_material_codes(payload.materials)

    if payload.arrived_at is not None:
        arrived_at = _normalize_optional_utc(payload.arrived_at)
        assert arrived_at is not None
        repair.arrived_at = arrived_at
    if "root_cause" in payload.model_fields_set:
        repair.root_cause = payload.root_cause
    if payload.repair_summary is not None:
        summary_text = payload.repair_summary.strip()
        if not summary_text:
            raise AppError(
                VALIDATION_ERROR,
                "repair_summary 不能为空白",
                {"field": "repair_summary"},
            )
        repair.repair_summary = summary_text

    repair.repair_started_at = new_started_at
    repair.repair_finished_at = new_finished_at
    repair.electrical_safety_checked = new_checked
    repair.safety_checklist_json = new_checklist
    repair.safety_note = new_safety_note.strip()

    if payload.materials is not None:
        await session.execute(
            delete(MaterialUsage).where(MaterialUsage.repair_process_id == repair.id)
        )
        await session.flush()
        for item in payload.materials:
            session.add(
                MaterialUsage(
                    repair_process_id=repair.id,
                    ticket_id=repair.ticket_id,
                    material_code=item.material_code,
                    material_name=item.material_name,
                    quantity=item.quantity,
                    unit=item.unit,
                    unit_price=item.unit_price,
                )
            )

    await session.flush()
    return repair
