"""4 套核心状态机的唯一权威状态变更引擎（I1–I8 跨表不变量守护）。

严格纪律（`docs/DESIGN.md` §1.1、§2.1–§2.4、§3.1）：
1. 全项目代码仓仅允许在本模块修改以下 4 张表的 `status` 字段：
   - `fault_tickets`（9 态 / 16 边 + 2 入口 / 2 终态）
   - `dispatch_records`（7 态 / 8 边 + 1 入口 / 5 终态）
   - `rework_orders`（8 态 / 14 边 + 2 入口 / 3 终态）
   - `notification_tasks`（5 态 / 9 边 + 1 入口 / 1 终态）
   说明：上表「边数」为按 `(from_status,to_status)` 统计的唯一字面边（合计 47）；
   对应 `docs/DESIGN.md` 设计规则条目编号为 `A3–A17`、`B2–B7`、`C3–C10`、`D2–D8`
   合计 36 条（`A15+B6+C8+D7`），两者为同一转移集的两种口径，勿混用。
2. 严禁在本模块导入 `fastapi`（`Request` 或 `Response` 等），所有异常统一抛 `AppError`。
3. 任何未列入合法转移表的变更一律抛出 `422 INVALID_STATE_TRANSITION`。
4. 状态变更同事务维护数据库 `CHECK` 一致性、`I7`（写 `audit_logs`，严格 append-only）
   与 `I8`（写 `notification_tasks`）。
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tomori.models.account import Account
from tomori.models.account_role import AccountRole
from tomori.models.audit_log import AuditLog
from tomori.models.dispatch_record import DispatchRecord
from tomori.models.fault_ticket import FaultTicket
from tomori.models.notification_task import NotificationTask
from tomori.models.rework_order import ReworkOrder
from tomori.models.role import Role
from tomori.models.scope_authorization import ScopeAuthorization
from tomori.services.exceptions import (
    INVALID_STATE_TRANSITION,
    VALIDATION_ERROR,
    AppError,
)

__all__ = [
    "DISPATCH_ACTIVE_STATES",
    "DISPATCH_INITIAL_STATES",
    "DISPATCH_STATES",
    "DISPATCH_TERMINAL_STATES",
    "DISPATCH_TRANSITIONS",
    "NOTIFICATION_EVENT_TYPES",
    "NOTIFICATION_INITIAL_STATES",
    "NOTIFICATION_STATES",
    "NOTIFICATION_TERMINAL_STATES",
    "NOTIFICATION_TRANSITIONS",
    "REWORK_ACTIVE_STATES",
    "REWORK_INITIAL_STATES",
    "REWORK_STATES",
    "REWORK_TERMINAL_STATES",
    "REWORK_TRANSITIONS",
    "TICKET_ACTIVE_STATES",
    "TICKET_INITIAL_STATES",
    "TICKET_STATES",
    "TICKET_TERMINAL_STATES",
    "TICKET_TRANSITIONS",
    "build_audit_entry",
    "compute_notification_backoff",
    "create_dispatch_record",
    "create_notification_task",
    "create_rework_order",
    "create_ticket_record",
    "enqueue_notifications",
    "list_all_active_admin_ids",
    "list_all_admin_ids",
    "list_region_admin_ids",
    "ticket_deep_link",
    "transition_dispatch",
    "transition_notification",
    "transition_rework",
    "transition_ticket",
    "validate_transition",
]

# ──────────────────────────────────────────────────────────────────────
# 4 套状态机合法转移图与终态集（29 态 / 47 条 `(from,to)` 唯一边 / 11 终态）
# ──────────────────────────────────────────────────────────────────────

TICKET_INITIAL_STATES: Final[frozenset[str]] = frozenset(
    {"AI_CANDIDATE_PENDING", "PENDING_DISPATCH"}
)
TICKET_TERMINAL_STATES: Final[frozenset[str]] = frozenset({"CLOSED", "CANCELLED"})
TICKET_TRANSITIONS: Final[dict[str, frozenset[str]]] = {
    "AI_CANDIDATE_PENDING": frozenset({"PENDING_DISPATCH", "CANCELLED"}),  # A3, A4
    "PENDING_DISPATCH": frozenset({"DISPATCHED", "CANCELLED"}),  # A6, A5
    "DISPATCHED": frozenset({"IN_REPAIR", "PENDING_DISPATCH", "MANUAL_REVIEW"}),  # A7, A8/B4
    "IN_REPAIR": frozenset({"PENDING_ACCEPTANCE", "MANUAL_REVIEW"}),  # A9, A10 (禁直入返修 MUT-09)
    "PENDING_ACCEPTANCE": frozenset(
        {"CLOSED", "REWORK_PENDING_DISPATCH", "MANUAL_REVIEW"}
    ),  # A11, A12, A13
    "REWORK_PENDING_DISPATCH": frozenset({"DISPATCHED"}),  # A14
    "MANUAL_REVIEW": frozenset({"PENDING_DISPATCH", "CLOSED", "CANCELLED"}),  # A15, A16, A17
    "CLOSED": frozenset(),
    "CANCELLED": frozenset(),
}
TICKET_STATES: Final[frozenset[str]] = frozenset(TICKET_TRANSITIONS.keys())
TICKET_ACTIVE_STATES: Final[frozenset[str]] = TICKET_STATES - TICKET_TERMINAL_STATES

_DEFAULT_TICKET_ACTIONS: Final[dict[tuple[str, str], str]] = {
    ("AI_CANDIDATE_PENDING", "PENDING_DISPATCH"): "TICKET_CONFIRMED",
    ("AI_CANDIDATE_PENDING", "CANCELLED"): "TICKET_CANCELLED",
    ("PENDING_DISPATCH", "CANCELLED"): "TICKET_CANCELLED",
    ("PENDING_DISPATCH", "DISPATCHED"): "TICKET_DISPATCHED",
    ("DISPATCHED", "IN_REPAIR"): "DISPATCH_ACCEPTED",
    ("DISPATCHED", "PENDING_DISPATCH"): "DISPATCH_REJECTED",
    ("DISPATCHED", "MANUAL_REVIEW"): "DISPATCH_REJECTED",
    ("IN_REPAIR", "PENDING_ACCEPTANCE"): "REPAIR_COMPLETED",
    ("IN_REPAIR", "MANUAL_REVIEW"): "DISPATCH_ESCALATED",
    ("PENDING_ACCEPTANCE", "CLOSED"): "ACCEPTANCE_APPROVED",
    ("PENDING_ACCEPTANCE", "REWORK_PENDING_DISPATCH"): "ACCEPTANCE_REJECTED",
    ("PENDING_ACCEPTANCE", "MANUAL_REVIEW"): "ACCEPTANCE_REJECTED",
    ("REWORK_PENDING_DISPATCH", "DISPATCHED"): "TICKET_DISPATCHED",
    ("MANUAL_REVIEW", "PENDING_DISPATCH"): "MANUAL_REVIEW_RESOLVED",
    ("MANUAL_REVIEW", "CLOSED"): "MANUAL_REVIEW_RESOLVED",
    ("MANUAL_REVIEW", "CANCELLED"): "MANUAL_REVIEW_RESOLVED",
}

DISPATCH_INITIAL_STATES: Final[frozenset[str]] = frozenset({"PENDING_ACCEPT"})
DISPATCH_TERMINAL_STATES: Final[frozenset[str]] = frozenset(
    {"REJECTED", "COMPLETED", "TIMEOUT_REASSIGNED", "ESCALATED", "CANCELLED"}
)
DISPATCH_TRANSITIONS: Final[dict[str, frozenset[str]]] = {
    # B2, B3, B4, 管理员升级改派 ESCALATED (`API.md` §2.4/§9.2), B7
    "PENDING_ACCEPT": frozenset(
        {"ACCEPTED", "REJECTED", "TIMEOUT_REASSIGNED", "ESCALATED", "CANCELLED"}
    ),
    "ACCEPTED": frozenset({"COMPLETED", "ESCALATED", "CANCELLED"}),  # B5, B6, B7
    "REJECTED": frozenset(),
    "TIMEOUT_REASSIGNED": frozenset(),
    "COMPLETED": frozenset(),
    "ESCALATED": frozenset(),
    "CANCELLED": frozenset(),
}
DISPATCH_STATES: Final[frozenset[str]] = frozenset(DISPATCH_TRANSITIONS.keys())
DISPATCH_ACTIVE_STATES: Final[frozenset[str]] = DISPATCH_STATES - DISPATCH_TERMINAL_STATES

_DEFAULT_DISPATCH_ACTIONS: Final[dict[tuple[str, str], str]] = {
    ("PENDING_ACCEPT", "ACCEPTED"): "DISPATCH_ACCEPTED",
    ("PENDING_ACCEPT", "REJECTED"): "DISPATCH_REJECTED",
    ("PENDING_ACCEPT", "TIMEOUT_REASSIGNED"): "DISPATCH_TIMEOUT_REASSIGNED",
    ("PENDING_ACCEPT", "ESCALATED"): "DISPATCH_ESCALATED",
    ("PENDING_ACCEPT", "CANCELLED"): "DISPATCH_CANCELLED",
    ("ACCEPTED", "COMPLETED"): "REPAIR_COMPLETED",
    ("ACCEPTED", "ESCALATED"): "DISPATCH_ESCALATED",
    ("ACCEPTED", "CANCELLED"): "DISPATCH_CANCELLED",
}

REWORK_INITIAL_STATES: Final[frozenset[str]] = frozenset({"PENDING_DISPATCH", "IN_MANUAL_REVIEW"})
REWORK_TERMINAL_STATES: Final[frozenset[str]] = frozenset(
    {"PASSED", "CLOSED_BY_REVIEW", "CANCELLED"}
)
REWORK_TRANSITIONS: Final[dict[str, frozenset[str]]] = {
    "PENDING_DISPATCH": frozenset({"DISPATCHED", "CANCELLED"}),  # C3 + A17 终止
    "IN_MANUAL_REVIEW": frozenset({"CLOSED_BY_REVIEW", "CANCELLED"}),  # C9, C10
    "DISPATCHED": frozenset(
        {"IN_REPAIR", "PENDING_DISPATCH", "IN_MANUAL_REVIEW", "CANCELLED"}
    ),  # C4 + 拒单/超时回退/升级/终止
    "IN_REPAIR": frozenset(
        {"PENDING_ACCEPTANCE", "IN_MANUAL_REVIEW", "CANCELLED"}
    ),  # C5 + 升级/终止
    "PENDING_ACCEPTANCE": frozenset({"PASSED", "IN_MANUAL_REVIEW", "CANCELLED"}),  # C6, C7, C8
    "PASSED": frozenset(),
    "CLOSED_BY_REVIEW": frozenset(),
    "CANCELLED": frozenset(),
}
REWORK_STATES: Final[frozenset[str]] = frozenset(REWORK_TRANSITIONS.keys())
REWORK_ACTIVE_STATES: Final[frozenset[str]] = REWORK_STATES - REWORK_TERMINAL_STATES

NOTIFICATION_INITIAL_STATES: Final[frozenset[str]] = frozenset({"PENDING"})
NOTIFICATION_TERMINAL_STATES: Final[frozenset[str]] = frozenset({"SENT"})
NOTIFICATION_TRANSITIONS: Final[dict[str, frozenset[str]]] = {
    "PENDING": frozenset({"PROCESSING"}),  # D2
    "PROCESSING": frozenset({"SENT", "FAILED", "PENDING", "DEAD_LETTER"}),  # D3, D4, 自愈
    "FAILED": frozenset({"PENDING", "DEAD_LETTER"}),  # D5, D6
    "DEAD_LETTER": frozenset({"PENDING", "SENT"}),  # D7, D8
    "SENT": frozenset(),
}
NOTIFICATION_STATES: Final[frozenset[str]] = frozenset(NOTIFICATION_TRANSITIONS.keys())

NOTIFICATION_EVENT_TYPES: Final[frozenset[str]] = frozenset(
    {
        "AI_CANDIDATE_CREATED",
        "TICKET_CONFIRMED",
        "TICKET_CANCELLED",
        "TICKET_DISPATCHED",
        "DISPATCH_ACCEPTED",
        "DISPATCH_REJECTED",
        "DISPATCH_RESPONSE_TIMEOUT",
        "REPAIR_COMPLETED",
        "REPAIR_SLA_TIMEOUT",
        "TICKET_ESCALATED",
        "DISPATCH_TIMEOUT_REASSIGNED",
        "ACCEPTANCE_APPROVED",
        "ACCEPTANCE_REJECTED",
        "REWORK_CREATED",
        "REWORK_MANUAL_REVIEW_REQUIRED",
        "MANUAL_REVIEW_REQUIRED",
        "TICKET_CLOSED",
        "AI_CANDIDATE_EXPIRED",
        "NOTIFICATION_DEAD_LETTER",
    }
)

_NOTIFICATION_CHANNELS: Final[frozenset[str]] = frozenset({"IN_APP", "SMS", "EMAIL"})
_AUDIT_METHODS: Final[frozenset[str]] = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})
_REWORK_DECISIONS: Final[frozenset[str]] = frozenset(
    {"APPROVE_REASSIGN", "SPECIAL_CLOSE", "TERMINATE_CANCEL"}
)


def _allowed_from_map(transitions: Mapping[str, frozenset[str]]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for src, targets in transitions.items():
        for dst in targets:
            out.setdefault(dst, []).append(src)
    for vals in out.values():
        vals.sort()
    return out


_TICKET_ALLOWED_FROM: Final = _allowed_from_map(TICKET_TRANSITIONS)
_DISPATCH_ALLOWED_FROM: Final = _allowed_from_map(DISPATCH_TRANSITIONS)
_REWORK_ALLOWED_FROM: Final = _allowed_from_map(REWORK_TRANSITIONS)
_NOTIFICATION_ALLOWED_FROM: Final = _allowed_from_map(NOTIFICATION_TRANSITIONS)


def _assert_transition(
    *,
    entity: str,
    from_status: str,
    to_status: str,
    transitions: Mapping[str, frozenset[str]],
    allowed_from_map: Mapping[str, list[str]],
) -> None:
    allowed_targets = transitions.get(from_status, frozenset())
    if to_status not in allowed_targets:
        raise AppError(
            INVALID_STATE_TRANSITION,
            f"{entity} 不允许从 {from_status} 转移至 {to_status}",
            {
                "entity": entity,
                "from_status": from_status,
                "to_status": to_status,
                "allowed_to": sorted(allowed_targets),
                "allowed_from": list(allowed_from_map.get(to_status, [])),
            },
        )


def ticket_deep_link(ticket_id: int) -> str:
    """工单详情前端路由唯一事实源。

    对齐 `src/frontend/src/router/index.ts` 的 `tickets/:ticketId` 路由。
    """
    return f"/tickets/{ticket_id}"


def validate_transition(entity: str, from_status: str, to_status: str) -> None:
    """纯函数校验任意一套状态机单步转移，越界抛 `422 INVALID_STATE_TRANSITION`。"""
    normalized = entity.lower()
    if normalized in {"fault_ticket", "fault_tickets", "ticket"}:
        _assert_transition(
            entity="fault_tickets",
            from_status=from_status,
            to_status=to_status,
            transitions=TICKET_TRANSITIONS,
            allowed_from_map=_TICKET_ALLOWED_FROM,
        )
    elif normalized in {"dispatch_record", "dispatch_records", "dispatch"}:
        _assert_transition(
            entity="dispatch_records",
            from_status=from_status,
            to_status=to_status,
            transitions=DISPATCH_TRANSITIONS,
            allowed_from_map=_DISPATCH_ALLOWED_FROM,
        )
    elif normalized in {"rework_order", "rework_orders", "rework"}:
        _assert_transition(
            entity="rework_orders",
            from_status=from_status,
            to_status=to_status,
            transitions=REWORK_TRANSITIONS,
            allowed_from_map=_REWORK_ALLOWED_FROM,
        )
    elif normalized in {"notification_task", "notification_tasks", "notification"}:
        _assert_transition(
            entity="notification_tasks",
            from_status=from_status,
            to_status=to_status,
            transitions=NOTIFICATION_TRANSITIONS,
            allowed_from_map=_NOTIFICATION_ALLOWED_FROM,
        )
    else:
        raise ValueError(f"Unknown state machine entity: {entity!r}")


def _ensure_utc(dt: datetime | None = None) -> datetime:
    if dt is None:
        return datetime.now(UTC)
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("Timezone-aware datetime is required")
    return dt.astimezone(UTC)


def compute_notification_backoff(attempt_count: int) -> timedelta:
    """计算状态机 D 失败退避间隔（`DESIGN.md` §2.4：1m / 5m / 15m）。"""
    if attempt_count <= 1:
        return timedelta(minutes=1)
    if attempt_count == 2:
        return timedelta(minutes=5)
    return timedelta(minutes=15)


async def list_all_admin_ids(session: AsyncSession) -> list[int]:
    """查询全部在岗 `ACTIVE` 的 `ADMIN` 账号 ID。"""
    stmt = (
        select(Account.id)
        .join(AccountRole, AccountRole.account_id == Account.id)
        .join(Role, Role.id == AccountRole.role_id)
        .where(Account.status == "ACTIVE", Role.code == "ADMIN")
        .order_by(Account.id.asc())
    )
    return list((await session.scalars(stmt)).all())


list_all_active_admin_ids = list_all_admin_ids


async def list_region_admin_ids(
    session: AsyncSession,
    region_id: int,
    *,
    now: datetime | None = None,
) -> list[int]:
    """优先查询持有该区域有效 `MANAGE` 授权的活跃 `ADMIN`，若无则回退全部活跃 `ADMIN`。"""
    ts = _ensure_utc(now)
    stmt = (
        select(Account.id)
        .join(AccountRole, AccountRole.account_id == Account.id)
        .join(Role, Role.id == AccountRole.role_id)
        .join(ScopeAuthorization, ScopeAuthorization.account_id == Account.id)
        .where(
            Account.status == "ACTIVE",
            Role.code == "ADMIN",
            ScopeAuthorization.region_id == region_id,
            ScopeAuthorization.scope_type == "MANAGE",
            ScopeAuthorization.is_active.is_(True),
            ScopeAuthorization.valid_from <= ts,
            (ScopeAuthorization.valid_to.is_(None)) | (ScopeAuthorization.valid_to > ts),
        )
        .distinct()
        .order_by(Account.id.asc())
    )
    scoped = list((await session.scalars(stmt)).all())
    if scoped:
        return scoped
    return await list_all_admin_ids(session)


def _json_safe(value: Any) -> Any:
    """把审计快照中的 `datetime` / `Decimal` / `UUID` / 嵌套容器递归转为 JSONB 可序列化值。

    `audit_logs.business_snapshot_json` 是 PG `JSONB`，psycopg 用标准 `json.dumps`
    序列化参数；直接塞入 `datetime` 会在 flush 瞬间抛 `TypeError`，因此统一在审计唯一
    入口做净化，避免每个业务调用点各自记得转字符串。
    """
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def build_audit_entry(
    *,
    action: str,
    resource_type: str,
    resource_id: int | None,
    ticket_id: int | None,
    actor_account_id: int | None = None,
    actor_username: str | None = None,
    actor_role_code: str | None = None,
    method: str = "POST",
    path: str = "/internal",
    response_status: int = 200,
    request_id: uuid.UUID | None = None,
    idempotency_key: str | None = None,
    request_payload: dict[str, Any] | None = None,
    request_payload_hash: str | None = None,
    response_body_json: dict[str, Any] | None = None,
    error_code: str | None = None,
    client_ip: str | None = None,
    user_agent: str | None = None,
    snapshot: dict[str, Any] | None = None,
    audit_meta: Mapping[str, Any] | None = None,
) -> AuditLog:
    """构造待写审计对象（严格 append-only，支持幂等包装器在 flush 前绑定幂等列）。"""
    meta = audit_meta or {}
    raw_req_id = request_id or meta.get("request_id")
    if isinstance(raw_req_id, uuid.UUID):
        resolved_req_id = raw_req_id
    elif isinstance(raw_req_id, str):
        try:
            resolved_req_id = uuid.UUID(raw_req_id)
        except ValueError:
            resolved_req_id = uuid.uuid4()
    else:
        resolved_req_id = uuid.uuid4()

    raw_method = str(meta.get("method", method)).upper()
    resolved_method = raw_method if raw_method in _AUDIT_METHODS else "POST"
    resolved_path = str(meta.get("path", path))[:256]
    resolved_status = int(meta.get("response_status", response_status))
    resolved_error_code = error_code or meta.get("error_code")
    if resolved_status >= 400 and not resolved_error_code:
        resolved_error_code = "ERROR"
    raw_ua = user_agent if user_agent is not None else meta.get("user_agent")
    eff_payload = request_payload if request_payload is not None else meta.get("request_payload")
    eff_resp_body = (
        response_body_json if response_body_json is not None else meta.get("response_body_json")
    )

    return AuditLog(
        request_id=resolved_req_id,
        actor_account_id=(
            actor_account_id if actor_account_id is not None else meta.get("actor_account_id")
        ),
        actor_username=(
            actor_username if actor_username is not None else meta.get("actor_username")
        ),
        actor_role_code=(
            actor_role_code if actor_role_code is not None else meta.get("actor_role_code")
        ),
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        ticket_id=ticket_id,
        method=resolved_method,
        path=resolved_path,
        request_payload=_json_safe(eff_payload) if eff_payload is not None else None,
        request_payload_hash=(
            request_payload_hash
            if request_payload_hash is not None
            else meta.get("request_payload_hash")
        ),
        idempotency_key=(
            idempotency_key if idempotency_key is not None else meta.get("idempotency_key")
        ),
        response_status=resolved_status,
        error_code=resolved_error_code,
        business_snapshot_json=_json_safe(snapshot) if snapshot is not None else None,
        response_body_json=_json_safe(eff_resp_body) if eff_resp_body is not None else None,
        client_ip=client_ip if client_ip is not None else meta.get("client_ip"),
        user_agent=str(raw_ua)[:256] if raw_ua else None,
    )


def create_notification_task(
    session: AsyncSession,
    *,
    recipient_account_id: int,
    event_type: str,
    title: str,
    body: str,
    related_ticket_id: int | None = None,
    related_dispatch_id: int | None = None,
    payload_json: dict[str, Any] | None = None,
    channel: str = "IN_APP",
    max_attempts: int = 3,
) -> NotificationTask:
    """D1 入口：在业务事务内同步插入 `PENDING` 通知任务（防静默漏发）。"""
    if event_type not in NOTIFICATION_EVENT_TYPES:
        raise AppError(
            VALIDATION_ERROR,
            f"不支持的通知事件类型: {event_type}",
            {"field": "event_type", "event_type": event_type},
        )
    if channel not in _NOTIFICATION_CHANNELS:
        raise AppError(
            VALIDATION_ERROR,
            f"不支持的通知渠道: {channel}",
            {"field": "channel", "channel": channel},
        )
    if max_attempts < 1:
        raise AppError(
            VALIDATION_ERROR,
            "通知最大重试次数必须 >= 1",
            {"field": "max_attempts", "max_attempts": max_attempts},
        )

    task = NotificationTask(
        recipient_account_id=recipient_account_id,
        event_type=event_type,
        channel=channel,
        title=(title.strip() or event_type)[:128],
        body=body,
        payload_json=_json_safe(payload_json) if payload_json is not None else None,
        related_ticket_id=related_ticket_id,
        related_dispatch_id=related_dispatch_id,
        status="PENDING",
        attempt_count=0,
        max_attempts=max_attempts,
        next_retry_at=None,
        last_error=None,
        sent_at=None,
        is_read=False,
        read_at=None,
    )
    session.add(task)
    return task


def enqueue_notifications(
    session: AsyncSession,
    recipient_ids: Iterable[int] | None = None,
    event_type: str | None = None,
    title: str = "",
    body: str = "",
    *,
    notify: Iterable[tuple[str, Sequence[int]]] | None = None,
    related_ticket_id: int | None = None,
    related_dispatch_id: int | None = None,
    payload_json: dict[str, Any] | None = None,
    channel: str = "IN_APP",
    max_attempts: int = 3,
) -> list[NotificationTask]:
    """I8 统一 fan-out 辅助：支持 `(recipient_ids, event_type)` 与 `notify=[...]` 两种形式。"""
    batches: list[tuple[str, Iterable[int]]] = []
    if event_type is not None and recipient_ids is not None:
        batches.append((event_type, recipient_ids))
    if notify is not None:
        for ev_type, seq_rids in notify:
            batches.append((ev_type, seq_rids))

    created: list[NotificationTask] = []
    for ev_type, batch_rids in batches:
        seen: set[int] = set()
        for rid in batch_rids:
            if rid in seen:
                continue
            seen.add(rid)
            created.append(
                create_notification_task(
                    session,
                    recipient_account_id=rid,
                    event_type=ev_type,
                    title=title or ev_type,
                    body=body or title or ev_type,
                    related_ticket_id=related_ticket_id,
                    related_dispatch_id=related_dispatch_id,
                    payload_json=payload_json,
                    channel=channel,
                    max_attempts=max_attempts,
                )
            )
    return created


async def create_ticket_record(
    session: AsyncSession,
    *,
    ticket_no: str,
    region_id: int,
    lighting_point_id: int,
    report_source: str,
    fault_type: str,
    title: str,
    reporter_account_id: int,
    initial_status: str,
    facility_asset_id: int | None = None,
    description: str | None = None,
    priority: str | None = None,
    grading_snapshot_json: dict[str, Any] | None = None,
    confirmed_by_inspector: bool = False,
    confirmed_by_inspector_id: int | None = None,
    confirmed_at: datetime | None = None,
    ai_suggestion_json: dict[str, Any] | None = None,
    ai_max_confidence: Decimal | None = None,
    response_deadline_at: datetime | None = None,
    repair_deadline_at: datetime | None = None,
    actor_account_id: int | None = None,
    actor_username: str | None = None,
    actor_role_code: str | None = None,
    action: str | None = None,
    extra_actions: Sequence[str] = (),
    idempotency_key: str | None = None,
    audit_meta: Mapping[str, Any] | None = None,
    notify: Iterable[tuple[str, Sequence[int]]] = (),
    persist_audit: bool = True,
    write_audit: bool | None = None,
    now: datetime | None = None,
) -> tuple[FaultTicket, list[AuditLog]]:
    """A1 / A2 入口：创建 `FaultTicket`（仅允许 `AI_CANDIDATE_PENDING` 或 `PENDING_DISPATCH`）。"""
    if initial_status not in TICKET_INITIAL_STATES:
        raise AppError(
            INVALID_STATE_TRANSITION,
            f"新建工单初始状态非法: {initial_status}",
            {
                "entity": "fault_tickets",
                "from_status": None,
                "to_status": initial_status,
                "allowed_to": sorted(TICKET_INITIAL_STATES),
                "allowed_from": [],
            },
        )
    if report_source == "AI_CANDIDATE" and fault_type in {"FLICKERING", "ELECTRIC_HAZARD"}:
        raise AppError(
            VALIDATION_ERROR,
            "AI 候选来源工单不允许直接标记为 FLICKERING 或 ELECTRIC_HAZARD",
            {"report_source": report_source, "fault_type": fault_type},
        )

    ts = _ensure_utc(now)
    effective_actor = actor_account_id if actor_account_id is not None else reporter_account_id

    if initial_status == "AI_CANDIDATE_PENDING":
        resolved_confirmed = False
        resolved_confirmer_id: int | None = None
        resolved_confirmed_at: datetime | None = None
    else:
        resolved_confirmed = True
        resolved_confirmer_id = (
            confirmed_by_inspector_id if confirmed_by_inspector_id is not None else effective_actor
        )
        resolved_confirmed_at = _ensure_utc(confirmed_at) if confirmed_at is not None else ts

    _ = confirmed_by_inspector
    ticket = FaultTicket(
        ticket_no=ticket_no,
        region_id=region_id,
        lighting_point_id=lighting_point_id,
        facility_asset_id=facility_asset_id,
        report_source=report_source,
        fault_type=fault_type,
        title=title,
        description=description,
        status=initial_status,
        priority=priority,
        grading_score=None,
        grading_snapshot_json=grading_snapshot_json,
        reporter_account_id=reporter_account_id,
        confirmed_by_inspector=resolved_confirmed,
        confirmed_by_inspector_id=resolved_confirmer_id,
        confirmed_at=resolved_confirmed_at,
        ai_suggestion_json=ai_suggestion_json,
        ai_max_confidence=ai_max_confidence,
        response_deadline_at=(
            _ensure_utc(response_deadline_at) if response_deadline_at is not None else None
        ),
        repair_deadline_at=(
            _ensure_utc(repair_deadline_at) if repair_deadline_at is not None else None
        ),
    )
    session.add(ticket)
    await session.flush()

    resolved_action = action or (
        "AI_CANDIDATE_GENERATED" if initial_status == "AI_CANDIDATE_PENDING" else "TICKET_CREATED"
    )
    base_snapshot: dict[str, Any] = {"to_status": initial_status, "priority": priority}
    audits: list[AuditLog] = [
        build_audit_entry(
            action=resolved_action,
            resource_type="FAULT_TICKET",
            resource_id=ticket.id,
            ticket_id=ticket.id,
            actor_account_id=effective_actor,
            actor_username=actor_username,
            actor_role_code=actor_role_code,
            idempotency_key=idempotency_key,
            snapshot=base_snapshot,
            audit_meta=audit_meta,
        )
    ]
    for extra_act in extra_actions:
        audits.append(
            build_audit_entry(
                action=extra_act,
                resource_type="FAULT_TICKET",
                resource_id=ticket.id,
                ticket_id=ticket.id,
                actor_account_id=effective_actor,
                actor_username=actor_username,
                actor_role_code=actor_role_code,
                idempotency_key=None,
                snapshot=base_snapshot,
                audit_meta={**(audit_meta or {}), "idempotency_key": None},
            )
        )

    should_persist = persist_audit if write_audit is None else write_audit
    if should_persist:
        for audit_row in audits:
            session.add(audit_row)

    if notify:
        enqueue_notifications(
            session,
            notify=notify,
            title=f"工单 {ticket.ticket_no}: {ticket.title}",
            body=f"工单 {ticket.ticket_no} 已创建，当前状态 {initial_status}。",
            related_ticket_id=ticket.id,
            payload_json={"ticket_id": ticket.id, "deep_link": ticket_deep_link(ticket.id)},
        )

    return ticket, audits


async def transition_ticket(
    session: AsyncSession,
    ticket: FaultTicket,
    to_status: str,
    *,
    actor_account_id: int | None = None,
    action: str | None = None,
    actor_username: str | None = None,
    actor_role_code: str | None = None,
    confirmed_by_inspector_id: int | None = None,
    confirmed_at: datetime | None = None,
    manual_review_reason: str | None = None,
    cancel_reason: str | None = None,
    cancelled_by_account_id: int | None = None,
    now: datetime | None = None,
    snapshot_extra: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
    request_id: uuid.UUID | None = None,
    audit_meta: Mapping[str, Any] | None = None,
    notify: Iterable[tuple[str, Sequence[int]]] = (),
    persist_audit: bool = True,
    write_audit: bool | None = None,
) -> AuditLog:
    """状态机 A 唯一合法转移函数：守卫校验 + CHECK 字段一致性补齐 + I7 审计 + I8 通知。"""
    from_status = ticket.status
    _assert_transition(
        entity="fault_tickets",
        from_status=from_status,
        to_status=to_status,
        transitions=TICKET_TRANSITIONS,
        allowed_from_map=_TICKET_ALLOWED_FROM,
    )

    if ticket.report_source == "AI_CANDIDATE" and ticket.fault_type in {
        "FLICKERING",
        "ELECTRIC_HAZARD",
    }:
        raise AppError(
            VALIDATION_ERROR,
            "AI 候选来源工单改判为闪烁或电气隐患时必须变更为人工来源",
            {"report_source": ticket.report_source, "fault_type": ticket.fault_type},
        )

    effective_manual_reason: str | None = None
    if to_status == "MANUAL_REVIEW":
        raw_manual = (
            manual_review_reason
            if manual_review_reason is not None
            else ticket.manual_review_reason
        )
        if not raw_manual or not raw_manual.strip():
            raise AppError(
                VALIDATION_ERROR,
                "流转至 MANUAL_REVIEW 状态必须提供非空 manual_review_reason",
                {"field": "manual_review_reason"},
            )
        effective_manual_reason = raw_manual.strip()[:256]

    effective_cancel_reason: str | None = None
    effective_cancelled_by: int | None = None
    if to_status == "CANCELLED":
        raw_cancel = cancel_reason if cancel_reason is not None else ticket.cancel_reason
        if not raw_cancel or not raw_cancel.strip():
            raise AppError(
                VALIDATION_ERROR,
                "撤销工单 (CANCELLED) 必须提供非空 cancel_reason",
                {"field": "cancel_reason"},
            )
        effective_cancel_reason = raw_cancel.strip()[:256]
        effective_cancelled_by = (
            cancelled_by_account_id
            if cancelled_by_account_id is not None
            else (actor_account_id if actor_account_id is not None else ticket.reporter_account_id)
        )
        if effective_cancelled_by is None:
            raise AppError(
                VALIDATION_ERROR,
                "撤销工单必须指定有效 cancelled_by_account_id",
                {"field": "cancelled_by_account_id"},
            )

    ts = _ensure_utc(now)

    # 守住 ck_fault_tickets_inspector_confirmation_serialized：
    # 只要 status <> 'AI_CANDIDATE_PENDING'（含 A3 确认与 A4 撤销 / 系统 TTL 过期），
    # confirmed 三字段必须全非空且为 True。
    if from_status == "AI_CANDIDATE_PENDING" and not ticket.confirmed_by_inspector:
        fallback_confirmer = (
            confirmed_by_inspector_id
            if confirmed_by_inspector_id is not None
            else (actor_account_id if actor_account_id is not None else ticket.reporter_account_id)
        )
        ticket.confirmed_by_inspector = True
        ticket.confirmed_by_inspector_id = fallback_confirmer
        ticket.confirmed_at = _ensure_utc(confirmed_at) if confirmed_at is not None else ts

    if to_status == "CLOSED":
        ticket.closed_at = ts
        ticket.manual_review_reason = None
    elif to_status == "CANCELLED":
        ticket.cancel_reason = effective_cancel_reason
        ticket.cancelled_by_account_id = effective_cancelled_by
        ticket.cancelled_at = ts
    elif to_status == "MANUAL_REVIEW":
        ticket.manual_review_reason = effective_manual_reason
    elif from_status == "MANUAL_REVIEW" and to_status == "PENDING_DISPATCH":
        ticket.manual_review_reason = None

    ticket.status = to_status

    snapshot: dict[str, Any] = {"from_status": from_status, "to_status": to_status}
    if snapshot_extra:
        snapshot.update(snapshot_extra)

    resolved_action = action or _DEFAULT_TICKET_ACTIONS.get(
        (from_status, to_status),
        f"TICKET_{to_status}",
    )
    audit = build_audit_entry(
        action=resolved_action,
        resource_type="FAULT_TICKET",
        resource_id=ticket.id,
        ticket_id=ticket.id,
        actor_account_id=actor_account_id,
        actor_username=actor_username,
        actor_role_code=actor_role_code,
        request_id=request_id,
        idempotency_key=idempotency_key,
        snapshot=snapshot,
        audit_meta=audit_meta,
    )
    should_persist = persist_audit if write_audit is None else write_audit
    if should_persist:
        session.add(audit)

    if notify:
        enqueue_notifications(
            session,
            notify=notify,
            title=f"工单 {ticket.ticket_no} 状态更新 ({to_status})",
            body=f"工单 {ticket.ticket_no} 已由 {from_status} 流转至 {to_status}。",
            related_ticket_id=ticket.id,
            payload_json={
                "ticket_id": ticket.id,
                "from_status": from_status,
                "to_status": to_status,
                "deep_link": ticket_deep_link(ticket.id),
            },
        )

    return audit


async def create_dispatch_record(
    session: AsyncSession,
    *,
    technician_account_id: int,
    scope_authorization_id: int,
    dispatched_by_account_id: int,
    dispatch_mode: str,
    accept_deadline_at: datetime,
    ticket: FaultTicket | None = None,
    ticket_id: int | None = None,
    region_id: int | None = None,
    rework_order_id: int | None = None,
    recommend_score: Decimal | None = None,
    candidate_snapshot_json: dict[str, Any] | None = None,
    dispatched_at: datetime | None = None,
) -> DispatchRecord:
    """B1 入口：新建派工记录（`PENDING_ACCEPT`）。"""
    resolved_ticket_id = ticket.id if ticket is not None else ticket_id
    resolved_region_id = ticket.region_id if ticket is not None else region_id
    if resolved_ticket_id is None or resolved_region_id is None:
        raise ValueError("ticket or (ticket_id and region_id) must be provided")
    if dispatch_mode not in {"MANUAL", "AUTO_RECOMMENDED", "REASSIGN"}:
        raise AppError(
            VALIDATION_ERROR,
            f"不支持的派工模式: {dispatch_mode}",
            {"field": "dispatch_mode", "dispatch_mode": dispatch_mode},
        )

    ts = _ensure_utc(dispatched_at)
    record = DispatchRecord(
        ticket_id=resolved_ticket_id,
        rework_order_id=rework_order_id,
        region_id=resolved_region_id,
        technician_account_id=technician_account_id,
        scope_authorization_id=scope_authorization_id,
        dispatched_by_account_id=dispatched_by_account_id,
        dispatch_mode=dispatch_mode,
        status="PENDING_ACCEPT",
        recommend_score=recommend_score,
        candidate_snapshot_json=(
            _json_safe(candidate_snapshot_json) if candidate_snapshot_json is not None else None
        ),
        dispatched_at=ts,
        accept_deadline_at=_ensure_utc(accept_deadline_at),
    )
    session.add(record)
    await session.flush()
    return record


async def transition_dispatch(
    session: AsyncSession,
    dispatch: DispatchRecord,
    to_status: str,
    *,
    actor_account_id: int | None = None,
    action: str | None = None,
    reason: str | None = None,
    reject_reason: str | None = None,
    escalate_reason: str | None = None,
    now: datetime | None = None,
    request_id: uuid.UUID | None = None,
    audit_meta: Mapping[str, Any] | None = None,
    persist_audit: bool = False,
    write_audit: bool | None = None,
) -> AuditLog:
    """状态机 B 唯一合法转移函数（B2..B7），严格维护 `ck_dispatch_records_*` 7 条约束。"""
    from_status = dispatch.status
    _assert_transition(
        entity="dispatch_records",
        from_status=from_status,
        to_status=to_status,
        transitions=DISPATCH_TRANSITIONS,
        allowed_from_map=_DISPATCH_ALLOWED_FROM,
    )

    eff_reject = reject_reason if reject_reason is not None else reason
    if to_status == "REJECTED" and (not eff_reject or not eff_reject.strip()):
        raise AppError(
            VALIDATION_ERROR,
            "派工拒单 (REJECTED) 必须提供非空拒单原因",
            {"field": "reject_reason"},
        )

    eff_escalate = escalate_reason if escalate_reason is not None else reason
    if to_status == "ESCALATED" and (not eff_escalate or not eff_escalate.strip()):
        raise AppError(
            VALIDATION_ERROR,
            "派工升级 (ESCALATED) 必须提供非空升级原因",
            {"field": "escalate_reason"},
        )

    ts = _ensure_utc(now)
    previous_accepted_at = dispatch.accepted_at

    # 守住 ck_dispatch_records_accepted_at_required:
    # (status IN ('ACCEPTED', 'COMPLETED')) = (accepted_at IS NOT NULL)
    if to_status == "ACCEPTED":
        dispatch.accepted_at = dispatch.accepted_at or ts
        dispatch.completed_at = None
    elif to_status == "COMPLETED":
        dispatch.accepted_at = dispatch.accepted_at or ts
        dispatch.completed_at = ts
    elif to_status == "REJECTED":
        assert eff_reject is not None
        dispatch.rejected_at = ts
        dispatch.reject_reason = eff_reject.strip()[:256]
        dispatch.accepted_at = None
        dispatch.completed_at = None
    elif to_status == "ESCALATED":
        assert eff_escalate is not None
        dispatch.escalated_at = ts
        dispatch.escalate_reason = eff_escalate.strip()[:256]
        dispatch.accepted_at = None
        dispatch.completed_at = None
    elif to_status == "TIMEOUT_REASSIGNED":
        dispatch.escalated_at = ts
        if eff_escalate and eff_escalate.strip():
            dispatch.escalate_reason = eff_escalate.strip()[:256]
        dispatch.accepted_at = None
        dispatch.completed_at = None
    elif to_status == "CANCELLED":
        dispatch.cancelled_at = ts
        dispatch.accepted_at = None
        dispatch.completed_at = None

    dispatch.status = to_status

    snapshot: dict[str, Any] = {"from_status": from_status, "to_status": to_status}
    if previous_accepted_at is not None and dispatch.accepted_at is None:
        snapshot["previous_accepted_at"] = previous_accepted_at.isoformat()

    resolved_action = action or _DEFAULT_DISPATCH_ACTIONS.get(
        (from_status, to_status),
        f"DISPATCH_{to_status}",
    )
    audit = build_audit_entry(
        action=resolved_action,
        resource_type="DISPATCH",
        resource_id=dispatch.id,
        ticket_id=dispatch.ticket_id,
        actor_account_id=actor_account_id,
        request_id=request_id,
        snapshot=snapshot,
        audit_meta=audit_meta,
    )
    should_persist = persist_audit if write_audit is None else write_audit
    if should_persist:
        session.add(audit)
    return audit


async def create_rework_order(
    session: AsyncSession,
    *,
    rework_no: str,
    source_acceptance_id: int,
    round_no: int,
    reject_reason: str,
    requires_manual_review: bool,
    ticket: FaultTicket | None = None,
    ticket_id: int | None = None,
    region_id: int | None = None,
) -> ReworkOrder:
    """C1 / C2 入口：新建返修单（`PENDING_DISPATCH` 或 `IN_MANUAL_REVIEW`）。"""
    resolved_ticket_id = ticket.id if ticket is not None else ticket_id
    resolved_region_id = ticket.region_id if ticket is not None else region_id
    if resolved_ticket_id is None or resolved_region_id is None:
        raise ValueError("ticket or (ticket_id and region_id) must be provided")
    if round_no < 1:
        raise AppError(
            VALIDATION_ERROR,
            "返修轮次 round_no 必须 >= 1",
            {"field": "round_no", "round_no": round_no},
        )
    if not reject_reason or not reject_reason.strip():
        raise AppError(
            VALIDATION_ERROR,
            "创建返修单必须提供非空 reject_reason",
            {"field": "reject_reason"},
        )

    initial_status = "IN_MANUAL_REVIEW" if requires_manual_review else "PENDING_DISPATCH"
    rework = ReworkOrder(
        rework_no=rework_no,
        ticket_id=resolved_ticket_id,
        source_acceptance_id=source_acceptance_id,
        round_no=round_no,
        region_id=resolved_region_id,
        status=initial_status,
        reject_reason=reject_reason.strip()[:512],
        requires_manual_review=requires_manual_review,
    )
    session.add(rework)
    await session.flush()
    return rework


async def transition_rework(
    session: AsyncSession,
    rework: ReworkOrder,
    to_status: str,
    *,
    actor_account_id: int | None = None,
    action: str | None = None,
    decision: str | None = None,
    note: str | None = None,
    now: datetime | None = None,
    request_id: uuid.UUID | None = None,
    audit_meta: Mapping[str, Any] | None = None,
    persist_audit: bool = False,
    write_audit: bool | None = None,
) -> AuditLog:
    """状态机 C 唯一合法转移函数（C3..C10），严格维护 `ck_rework_orders_*` 5 条约束。"""
    from_status = rework.status
    _assert_transition(
        entity="rework_orders",
        from_status=from_status,
        to_status=to_status,
        transitions=REWORK_TRANSITIONS,
        allowed_from_map=_REWORK_ALLOWED_FROM,
    )

    if to_status == "CLOSED_BY_REVIEW":
        if decision not in {"APPROVE_REASSIGN", "SPECIAL_CLOSE"}:
            raise AppError(
                VALIDATION_ERROR,
                "CLOSED_BY_REVIEW 必须指定 decision 为 APPROVE_REASSIGN 或 SPECIAL_CLOSE",
                {"field": "decision", "decision": decision},
            )
        if actor_account_id is None:
            raise AppError(
                VALIDATION_ERROR,
                "CLOSED_BY_REVIEW 必须提供裁决管理员账号 actor_account_id",
                {"field": "actor_account_id"},
            )
    elif decision is not None:
        if decision not in _REWORK_DECISIONS:
            raise AppError(
                VALIDATION_ERROR,
                f"非法的返修人工复核决定: {decision}",
                {"field": "decision", "decision": decision},
            )
        if actor_account_id is None:
            raise AppError(
                VALIDATION_ERROR,
                "写入返修人工复核决定时必须提供 actor_account_id",
                {"field": "actor_account_id"},
            )

    ts = _ensure_utc(now)
    if to_status in {"IN_MANUAL_REVIEW", "CLOSED_BY_REVIEW"}:
        rework.requires_manual_review = True
    if to_status in REWORK_TERMINAL_STATES:
        rework.closed_at = ts

    if decision is not None:
        rework.manual_review_decision = decision
        rework.manual_review_decided_by_account_id = actor_account_id
        rework.manual_review_decided_at = ts
    if note is not None:
        rework.manual_review_note = note[:512]

    rework.status = to_status

    resolved_action = action or (
        "MANUAL_REVIEW_RESOLVED" if to_status == "CLOSED_BY_REVIEW" else f"REWORK_{to_status}"
    )
    audit = build_audit_entry(
        action=resolved_action,
        resource_type="REWORK",
        resource_id=rework.id,
        ticket_id=rework.ticket_id,
        actor_account_id=actor_account_id,
        request_id=request_id,
        snapshot={"from_status": from_status, "to_status": to_status},
        audit_meta=audit_meta,
    )
    should_persist = persist_audit if write_audit is None else write_audit
    if should_persist:
        session.add(audit)
    return audit


async def transition_notification(
    session: AsyncSession,
    task: NotificationTask,
    to_status: str,
    *,
    actor_account_id: int | None = None,
    action: str | None = None,
    attempt_count: int | None = None,
    last_error: str | None = None,
    next_retry_at: datetime | None = None,
    now: datetime | None = None,
    request_id: uuid.UUID | None = None,
    audit_meta: Mapping[str, Any] | None = None,
    persist_audit: bool = True,
    write_audit: bool | None = None,
) -> AuditLog:
    """状态机 D 唯一合法转移函数（用于 API retry / acknowledge 及 notifier worker）。"""
    from_status = task.status
    _assert_transition(
        entity="notification_tasks",
        from_status=from_status,
        to_status=to_status,
        transitions=NOTIFICATION_TRANSITIONS,
        allowed_from_map=_NOTIFICATION_ALLOWED_FROM,
    )

    if attempt_count is not None:
        if attempt_count < 0 or attempt_count > task.max_attempts:
            raise AppError(
                VALIDATION_ERROR,
                f"通知重试次数 attempt_count={attempt_count} 超出合法区间 [0, {task.max_attempts}]",
                {
                    "field": "attempt_count",
                    "attempt_count": attempt_count,
                    "max_attempts": task.max_attempts,
                },
            )
        task.attempt_count = attempt_count

    ts = _ensure_utc(now)
    if from_status == "DEAD_LETTER" and to_status == "PENDING":  # D7 retry
        task.attempt_count = 0
        task.last_error = None
        task.next_retry_at = ts
        task.sent_at = None
    elif from_status == "DEAD_LETTER" and to_status == "SENT":  # D8 acknowledge
        task.sent_at = ts
        task.next_retry_at = None
        task.acknowledged_by_account_id = actor_account_id
        task.acknowledged_at = ts
    elif from_status == "PROCESSING" and to_status == "PENDING":  # 10 分钟卡死自愈回退
        task.sent_at = None
        if last_error is not None:
            task.last_error = last_error[:512]
        task.next_retry_at = (
            _ensure_utc(next_retry_at)
            if (next_retry_at is not None and task.attempt_count < task.max_attempts)
            else (None if task.attempt_count >= task.max_attempts else ts)
        )
    elif from_status == "FAILED" and to_status == "PENDING":  # D5 退避到期准备重投
        task.sent_at = None
        if next_retry_at is not None and task.attempt_count < task.max_attempts:
            task.next_retry_at = _ensure_utc(next_retry_at)
        else:
            task.next_retry_at = None
    elif to_status == "SENT":  # D3
        task.sent_at = ts
        task.next_retry_at = None
    elif to_status == "FAILED":  # D4
        task.sent_at = None
        resolved_err = last_error or task.last_error or "delivery_failed"
        task.last_error = resolved_err[:512]
        if task.attempt_count < task.max_attempts:
            task.next_retry_at = (
                _ensure_utc(next_retry_at)
                if next_retry_at is not None
                else (ts + compute_notification_backoff(max(task.attempt_count, 1)))
            )
        else:
            task.next_retry_at = None
    elif to_status == "DEAD_LETTER":  # D6 / 卡死耗尽直入死信
        task.sent_at = None
        task.next_retry_at = None
        resolved_dead_err = last_error or task.last_error or "max_attempts_exceeded"
        task.last_error = resolved_dead_err[:512]
    elif to_status == "PROCESSING":  # D2
        task.sent_at = None

    task.status = to_status

    resolved_action = action or (
        "NOTIFICATION_RETRIED"
        if (from_status, to_status) == ("DEAD_LETTER", "PENDING")
        else (
            "NOTIFICATION_ACKNOWLEDGED"
            if (from_status, to_status) == ("DEAD_LETTER", "SENT")
            else f"NOTIFICATION_{to_status}"
        )
    )
    audit = build_audit_entry(
        action=resolved_action,
        resource_type="NOTIFICATION",
        resource_id=task.id,
        ticket_id=task.related_ticket_id,
        actor_account_id=actor_account_id,
        request_id=request_id,
        snapshot={
            "from_status": from_status,
            "to_status": to_status,
            "attempt_count": task.attempt_count,
        },
        audit_meta=audit_meta,
    )
    should_persist = persist_audit if write_audit is None else write_audit
    if should_persist:
        session.add(audit)
    return audit
