"""task-23 回归：逾期重派接单期限 / 返修三路径终态一致 / 并发派工上限。

复现基线（只读参考，不外层加锁，依赖现有 api_client/async_engine fixture + 真 PG17）：
- `.team/notes/tester/sla-run/test_sla_reject.py`：逾期拒单重派
- `.team/notes/tester/rework-run/test_rework_run.py`：返修拒单 -> 重派 -> 验收通过
- `.team/notes/auditor/concurrent_dispatch_repro.py` +
  `.team/notes/auditor/logs/v010/concurrent-dispatch.log`：并发派工在途 4 > 上限 3

被回归的行为语义（`src/backend/tomori/services/dispatch.py` / `sla.py`）：
- `_resolve_accept_deadline`：票 `repair_deadline_at` 已逾期时回退 `ts + 接单窗口`
  （`sla_policy.response_minutes=None` -> `statutory_deadline_hours=48`），不得沿用
  已过期时间，否则「接单 422 -> SLA 回收 -> 再派工」死循环；
- 返修派工被拒 / 升级 / 接单超时回收时，返修单与工单状态联动回退（断链修复）；
- `verify_technician_eligible` 对目标维修员 Account 行 `SELECT ... FOR UPDATE`
  串行化在途计数校验，并发派工不超 `max_active_dispatch`。
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

import tomori.services.dispatch as dispatch_service
from tests.integration.conftest import auth_headers_for, new_idem_key
from tests.integration.test_api_state_machine import (
    _accept_and_complete,
    _create_manual_ticket,
    _dispatch_to_tech1,
)
from tests.integration.test_dispatch_boundaries import _create_ticket_and_dispatch
from tomori.models.dispatch_record import DispatchRecord
from tomori.models.fault_ticket import FaultTicket
from tomori.models.rework_order import ReworkOrder
from tomori.services.state_machine import (
    DISPATCH_TERMINAL_STATES,
    REWORK_TERMINAL_STATES,
)


async def _backdate(
    async_engine: AsyncEngine,
    *,
    ticket_id: int | None = None,
    dispatch_id: int | None = None,
    past: datetime,
) -> None:
    """Backdate `repair_deadline_at` / `accept_deadline_at` columns (not status)."""
    async with AsyncSession(async_engine, expire_on_commit=False) as s:
        if ticket_id is not None:
            await s.execute(
                text("UPDATE fault_tickets SET repair_deadline_at = :past WHERE id = :tid"),
                {"past": past, "tid": ticket_id},
            )
        if dispatch_id is not None:
            await s.execute(
                text("UPDATE dispatch_records SET accept_deadline_at = :past WHERE id = :did"),
                {"past": past, "did": dispatch_id},
            )
        await s.commit()


async def _drive_to_rework_pending_dispatch(
    api_client: AsyncClient,
    *,
    admin_hdr: dict[str, str],
    insp_hdr: dict[str, str],
    tech_hdr: dict[str, str],
) -> tuple[int, dict[str, Any]]:
    """建单 -> 派工 tech1 -> 接单完工 -> 验收驳回。返回 (ticket_id, rework_order JSON)。"""
    ticket = await _create_manual_ticket(api_client, insp_hdr)
    tid = int(ticket["id"])
    d0 = await _dispatch_to_tech1(api_client, admin_hdr, tid)
    await _accept_and_complete(api_client, tech_hdr, int(d0["id"]))
    r_rej = await api_client.post(
        f"/api/v1/tickets/{tid}/acceptance",
        json={"decision": "REJECTED", "comment": "验收不合格返修"},
        headers={**insp_hdr, "Idempotency-Key": new_idem_key("t23-ar")},
    )
    assert r_rej.status_code == 200, r_rej.text
    body = r_rej.json()
    assert body["ticket"]["status"] == "REWORK_PENDING_DISPATCH"
    assert body["rework_order"]["status"] == "PENDING_DISPATCH"
    return tid, body["rework_order"]


# ──────────────────────────────────────────────────────────────────────────────
# A. 逾期重派有效接单期限
# ──────────────────────────────────────────────────────────────────────────────


async def test_overdue_reject_redispatch_gets_fresh_accept_deadline(
    api_client: AsyncClient,
    async_engine: AsyncEngine,
) -> None:
    """逾期工单拒单重派：新派工接单时限必须重新起算，不得沿用已过期 repair_deadline。

    修复前沿用票 `repair_deadline_at`（已过期）-> 新派工接单即超时 -> 死锁。
    回归断言：`accept_deadline_at` 为未来且恰为 `dispatched_at + 48h` 回退窗口。
    """
    admin_hdr = await auth_headers_for(api_client, "admin")
    tech1_hdr = await auth_headers_for(api_client, "tech1")
    tech2_hdr = await auth_headers_for(api_client, "tech2")
    tid, did = await _create_ticket_and_dispatch(api_client, title="task-23 逾期拒单重派")

    past = datetime.now(UTC) - timedelta(hours=2)
    await _backdate(async_engine, ticket_id=tid, dispatch_id=did, past=past)

    rejected = await api_client.post(
        f"/api/v1/dispatches/{did}/reject",
        json={"reject_reason": "现场维修条件不足"},
        headers={**tech1_hdr, "Idempotency-Key": new_idem_key("t23-rej")},
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "REJECTED"

    cands = (
        await api_client.get(f"/api/v1/tickets/{tid}/dispatch-candidates", headers=admin_hdr)
    ).json()["candidates"]
    cand = next(c for c in cands if c["username"] == "tech2")
    before = datetime.now(UTC)
    dispatched = await api_client.post(
        f"/api/v1/tickets/{tid}/dispatch",
        json={
            "technician_account_id": cand["account_id"],
            "scope_authorization_id": cand["scope_authorization_id"],
        },
        headers={**admin_hdr, "Idempotency-Key": new_idem_key("t23-disp")},
    )
    assert dispatched.status_code == 201, dispatched.text
    body = dispatched.json()
    did2 = int(body["id"])
    accept_deadline = datetime.fromisoformat(body["accept_deadline_at"])
    dispatched_at = datetime.fromisoformat(body["dispatched_at"])
    # 有效接单期限：必须为未来，不得沿用票上已过期的 repair_deadline_at
    assert accept_deadline > before
    assert accept_deadline != past
    # 逾期回退窗口 = sla_policy.statutory_deadline_hours（48h，response_minutes 为 null）
    assert accept_deadline - dispatched_at == timedelta(hours=48)

    # 接单 200 —— 修复前此处必为 422 INVALID_STATE_TRANSITION（死锁回归断言）
    accepted = await api_client.post(
        f"/api/v1/dispatches/{did2}/accept",
        headers={**tech2_hdr, "Idempotency-Key": new_idem_key("t23-acc")},
    )
    assert accepted.status_code == 200, accepted.text

    async with AsyncSession(async_engine, expire_on_commit=False) as s:
        d1 = await s.scalar(select(DispatchRecord).where(DispatchRecord.id == did))
        d2 = await s.scalar(select(DispatchRecord).where(DispatchRecord.id == did2))
        t = await s.scalar(select(FaultTicket).where(FaultTicket.id == tid))
        assert d1 is not None and d1.status == "REJECTED"
        assert d2 is not None and d2.status == "ACCEPTED"
        assert t is not None and t.status == "IN_REPAIR"


async def test_overdue_timeout_recycle_redispatch_gets_fresh_accept_deadline(
    api_client: AsyncClient,
    async_engine: AsyncEngine,
) -> None:
    """接单超时 SLA 回收后重派：同样必须获得有效接单期限。

    覆盖「回收 -> 重派 -> 再回收」死锁回路：旧实现重派沿用过期时限，
    新派工接单即超时，SLA 扫描无限循环回收。
    """
    admin_hdr = await auth_headers_for(api_client, "admin")
    tech1_hdr = await auth_headers_for(api_client, "tech1")
    tid, did = await _create_ticket_and_dispatch(api_client, title="task-23 超时回收重派")

    past = datetime.now(UTC) - timedelta(hours=2)
    await _backdate(async_engine, ticket_id=tid, dispatch_id=did, past=past)

    r_scan = await api_client.post("/api/v1/sla/scan", headers=admin_hdr)
    assert r_scan.status_code == 200, r_scan.text
    summary = r_scan.json()
    assert summary["timeout_reassigned"] == 1
    assert summary["marked_overdue"] == 1

    async with AsyncSession(async_engine, expire_on_commit=False) as s:
        d1 = await s.scalar(select(DispatchRecord).where(DispatchRecord.id == did))
        t = await s.scalar(select(FaultTicket).where(FaultTicket.id == tid))
        assert d1 is not None and d1.status == "TIMEOUT_REASSIGNED"
        assert d1.status in DISPATCH_TERMINAL_STATES
        assert t is not None and t.status == "PENDING_DISPATCH"
        assert t.is_sla_overdue is True

    # 回收后重派：接单时限必须重新起算（逾期回退窗口 48h）
    second = await _dispatch_to_tech1(api_client, admin_hdr, tid)
    accept_deadline = datetime.fromisoformat(second["accept_deadline_at"])
    dispatched_at = datetime.fromisoformat(second["dispatched_at"])
    assert accept_deadline > datetime.now(UTC)
    assert accept_deadline - dispatched_at == timedelta(hours=48)

    accepted = await api_client.post(
        f"/api/v1/dispatches/{second['id']}/accept",
        headers={**tech1_hdr, "Idempotency-Key": new_idem_key("t23-acc2")},
    )
    assert accepted.status_code == 200, accepted.text


# ──────────────────────────────────────────────────────────────────────────────
# B. 返修拒单 / 升级 / SLA 回收 终态一致
# ──────────────────────────────────────────────────────────────────────────────


async def test_rework_dispatch_reject_recovers_consistent_states(
    api_client: AsyncClient,
    async_engine: AsyncEngine,
) -> None:
    """返修派工被拒：派工进 REJECTED 终态，返修单/工单回退可再派，全链路到 PASSED。

    回归 `.team/notes/tester/rework-run`：拒单回退断链修复（返修单回
    PENDING_DISPATCH 并可在下次派工重新关联，终态收敛 PASSED 而非滞留）。
    """
    admin_hdr = await auth_headers_for(api_client, "admin")
    insp_hdr = await auth_headers_for(api_client, "inspector1")
    tech1_hdr = await auth_headers_for(api_client, "tech1")
    tid, rework = await _drive_to_rework_pending_dispatch(
        api_client, admin_hdr=admin_hdr, insp_hdr=insp_hdr, tech_hdr=tech1_hdr
    )

    first = await _dispatch_to_tech1(api_client, admin_hdr, tid)
    assert first["rework_order_id"] == rework["id"]

    refused = await api_client.post(
        f"/api/v1/dispatches/{first['id']}/reject",
        json={"reject_reason": "工具未带齐"},
        headers={**tech1_hdr, "Idempotency-Key": new_idem_key("t23-rw-ref")},
    )
    assert refused.status_code == 200, refused.text
    assert refused.json()["status"] == "REJECTED"

    # 终态一致：派工 REJECTED 终态；返修单回 PENDING_DISPATCH；工单回 PENDING_DISPATCH
    async with AsyncSession(async_engine, expire_on_commit=False) as s:
        rw = await s.scalar(select(ReworkOrder).where(ReworkOrder.id == rework["id"]))
        t = await s.scalar(select(FaultTicket).where(FaultTicket.id == tid))
        d1 = await s.scalar(select(DispatchRecord).where(DispatchRecord.id == first["id"]))
        assert rw is not None and rw.status == "PENDING_DISPATCH"
        assert t is not None and t.status == "PENDING_DISPATCH"
        assert t.dispatch_reject_count == 1
        assert d1 is not None and d1.status == "REJECTED"
        assert d1.status in DISPATCH_TERMINAL_STATES

    # 再派工重新关联同一返修单（断链修复断言），走完全链路收敛 PASSED 终态
    second = await _dispatch_to_tech1(api_client, admin_hdr, tid)
    assert second["rework_order_id"] == rework["id"]
    await _accept_and_complete(api_client, tech1_hdr, int(second["id"]))
    approved = await api_client.post(
        f"/api/v1/tickets/{tid}/acceptance",
        json={"decision": "APPROVED"},
        headers={**insp_hdr, "Idempotency-Key": new_idem_key("t23-rw-ok")},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["ticket"]["status"] == "CLOSED"
    assert approved.json()["rework_order"]["status"] == "PASSED"

    async with AsyncSession(async_engine, expire_on_commit=False) as s:
        rw = await s.scalar(select(ReworkOrder).where(ReworkOrder.id == rework["id"]))
        d2 = await s.scalar(select(DispatchRecord).where(DispatchRecord.id == second["id"]))
        t = await s.scalar(select(FaultTicket).where(FaultTicket.id == tid))
        assert rw is not None and rw.status == "PASSED"
        assert rw.status in REWORK_TERMINAL_STATES
        assert d2 is not None and d2.status == "COMPLETED"
        assert t is not None and t.status == "CLOSED"


async def test_rework_three_rejections_enter_review_and_cancel(
    api_client: AsyncClient,
    async_engine: AsyncEngine,
) -> None:
    admin_hdr = await auth_headers_for(api_client, "admin")
    insp_hdr = await auth_headers_for(api_client, "inspector1")
    tech_hdr = await auth_headers_for(api_client, "tech1")
    tid, rework = await _drive_to_rework_pending_dispatch(
        api_client, admin_hdr=admin_hdr, insp_hdr=insp_hdr, tech_hdr=tech_hdr
    )
    dispatch_ids = []
    for _ in range(3):
        dispatch = await _dispatch_to_tech1(api_client, admin_hdr, tid)
        assert dispatch["rework_order_id"] == rework["id"]
        dispatch_ids.append(dispatch["id"])
        response = await api_client.post(
            f"/api/v1/dispatches/{dispatch['id']}/reject",
            json={"reject_reason": "现场维修条件不足"},
            headers={**tech_hdr, "Idempotency-Key": new_idem_key("t23-triple")},
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "REJECTED"

    async with AsyncSession(async_engine) as s:
        rw = await s.get(ReworkOrder, rework["id"])
        ticket = await s.get(FaultTicket, tid)
        assert rw is not None and rw.status == "IN_MANUAL_REVIEW"
        assert ticket is not None and ticket.status == "MANUAL_REVIEW"
        assert ticket.dispatch_reject_count == 3

    resolved = await api_client.post(
        f"/api/v1/tickets/{tid}/review-resolve",
        json={"resolution": "TERMINATE_CANCEL", "reason": "现场条件不足终止工单"},
        headers={**admin_hdr, "Idempotency-Key": new_idem_key("t23-cancel")},
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["status"] == "CANCELLED"
    async with AsyncSession(async_engine) as s:
        rw = await s.get(ReworkOrder, rework["id"])
        statuses = list(
            (
                await s.scalars(
                    select(DispatchRecord.status).where(DispatchRecord.id.in_(dispatch_ids))
                )
            ).all()
        )
        assert rw is not None and rw.status == "CANCELLED"
        assert statuses == ["REJECTED"] * 3


async def test_rework_dispatch_escalate_review_resolves_terminal_states(
    api_client: AsyncClient,
    async_engine: AsyncEngine,
) -> None:
    """返修派工升级：派工 ESCALATED、返修单 IN_MANUAL_REVIEW、工单 MANUAL_REVIEW，
    复核 APPROVE_REASSIGN 后返修单 CLOSED_BY_REVIEW 终态、工单回 PENDING_DISPATCH。
    """
    admin_hdr = await auth_headers_for(api_client, "admin")
    insp_hdr = await auth_headers_for(api_client, "inspector1")
    tech1_hdr = await auth_headers_for(api_client, "tech1")
    tid, rework = await _drive_to_rework_pending_dispatch(
        api_client, admin_hdr=admin_hdr, insp_hdr=insp_hdr, tech_hdr=tech1_hdr
    )

    first = await _dispatch_to_tech1(api_client, admin_hdr, tid)
    assert first["rework_order_id"] == rework["id"]

    accepted = await api_client.post(
        f"/api/v1/dispatches/{first['id']}/accept",
        headers={**tech1_hdr, "Idempotency-Key": new_idem_key("t23-es-acc")},
    )
    assert accepted.status_code == 200, accepted.text

    escalated = await api_client.post(
        f"/api/v1/dispatches/{first['id']}/escalate",
        json={"escalate_reason": "现场发现供电安全隐患需特批"},
        headers=tech1_hdr,
    )
    assert escalated.status_code == 200, escalated.text
    assert escalated.json()["status"] == "ESCALATED"
    assert escalated.json()["ticket_status"] == "MANUAL_REVIEW"

    # 终态一致：派工 ESCALATED 终态 + accepted_at 置空（ck 约束）；返修单/工单进复核
    async with AsyncSession(async_engine, expire_on_commit=False) as s:
        rw = await s.scalar(select(ReworkOrder).where(ReworkOrder.id == rework["id"]))
        d1 = await s.scalar(select(DispatchRecord).where(DispatchRecord.id == first["id"]))
        t = await s.scalar(select(FaultTicket).where(FaultTicket.id == tid))
        assert rw is not None and rw.status == "IN_MANUAL_REVIEW"
        assert d1 is not None and d1.status == "ESCALATED"
        assert d1.status in DISPATCH_TERMINAL_STATES
        assert d1.accepted_at is None
        assert t is not None and t.status == "MANUAL_REVIEW"

    resolved = await api_client.post(
        f"/api/v1/tickets/{tid}/review-resolve",
        json={"resolution": "APPROVE_REASSIGN", "reason": "批准重新派工处理升级现场"},
        headers={**admin_hdr, "Idempotency-Key": new_idem_key("t23-rev")},
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["status"] == "PENDING_DISPATCH"

    async with AsyncSession(async_engine, expire_on_commit=False) as s:
        rw = await s.scalar(select(ReworkOrder).where(ReworkOrder.id == rework["id"]))
        t = await s.scalar(select(FaultTicket).where(FaultTicket.id == tid))
        assert rw is not None and rw.status == "CLOSED_BY_REVIEW"
        assert rw.status in REWORK_TERMINAL_STATES
        assert t is not None and t.status == "PENDING_DISPATCH"
        # 无残留活跃派工（复核裁决已清扫）
        lingering = await s.scalar(
            select(func.count(DispatchRecord.id)).where(
                DispatchRecord.ticket_id == tid,
                DispatchRecord.status.in_(("PENDING_ACCEPT", "ACCEPTED")),
            )
        )
        assert lingering == 0

    # 复核后工单可再派：返修单已进终态，不再被新派工关联
    second = await _dispatch_to_tech1(api_client, admin_hdr, tid)
    assert second["rework_order_id"] is None
    await _accept_and_complete(api_client, tech1_hdr, int(second["id"]))
    approved = await api_client.post(
        f"/api/v1/tickets/{tid}/acceptance",
        json={"decision": "APPROVED"},
        headers={**insp_hdr, "Idempotency-Key": new_idem_key("t23-es-ok")},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["ticket"]["status"] == "CLOSED"
    async with AsyncSession(async_engine) as s:
        rw = await s.get(ReworkOrder, rework["id"])
        assert rw is not None and rw.status == "CLOSED_BY_REVIEW"


async def test_rework_dispatch_sla_timeout_recycle_recovers_consistent_states(
    api_client: AsyncClient,
    async_engine: AsyncEngine,
) -> None:
    """返修派工接单超时 SLA 回收：派工 TIMEOUT_REASSIGNED 终态，
    返修单 DISPATCHED -> PENDING_DISPATCH，工单 DISPATCHED -> PENDING_DISPATCH，
    再派工重新关联同一返修单。
    """
    admin_hdr = await auth_headers_for(api_client, "admin")
    insp_hdr = await auth_headers_for(api_client, "inspector1")
    tech1_hdr = await auth_headers_for(api_client, "tech1")
    tid, rework = await _drive_to_rework_pending_dispatch(
        api_client, admin_hdr=admin_hdr, insp_hdr=insp_hdr, tech_hdr=tech1_hdr
    )

    first = await _dispatch_to_tech1(api_client, admin_hdr, tid)
    assert first["rework_order_id"] == rework["id"]

    # 仅回填派工接单时限（票 repair_deadline 保持未来 -> 只触发段 3 回收）
    past = datetime.now(UTC) - timedelta(hours=1)
    await _backdate(async_engine, dispatch_id=int(first["id"]), past=past)

    r_scan = await api_client.post("/api/v1/sla/scan", headers=admin_hdr)
    assert r_scan.status_code == 200, r_scan.text
    summary = r_scan.json()
    assert summary["timeout_reassigned"] == 1
    assert summary["marked_overdue"] == 0

    async with AsyncSession(async_engine, expire_on_commit=False) as s:
        rw = await s.scalar(select(ReworkOrder).where(ReworkOrder.id == rework["id"]))
        d1 = await s.scalar(select(DispatchRecord).where(DispatchRecord.id == first["id"]))
        t = await s.scalar(select(FaultTicket).where(FaultTicket.id == tid))
        assert d1 is not None and d1.status == "TIMEOUT_REASSIGNED"
        assert d1.status in DISPATCH_TERMINAL_STATES
        assert rw is not None and rw.status == "PENDING_DISPATCH"
        assert t is not None and t.status == "PENDING_DISPATCH"

    # 回收后再派工：重新关联同一返修单且旧派工不被复活
    second = await _dispatch_to_tech1(api_client, admin_hdr, tid)
    assert second["rework_order_id"] == rework["id"]

    async with AsyncSession(async_engine, expire_on_commit=False) as s:
        rw = await s.scalar(select(ReworkOrder).where(ReworkOrder.id == rework["id"]))
        d1 = await s.scalar(select(DispatchRecord).where(DispatchRecord.id == first["id"]))
        d2 = await s.scalar(select(DispatchRecord).where(DispatchRecord.id == second["id"]))
        t = await s.scalar(select(FaultTicket).where(FaultTicket.id == tid))
        assert rw is not None and rw.status == "DISPATCHED"
        assert d1 is not None and d1.status == "TIMEOUT_REASSIGNED"
        assert d2 is not None and d2.status == "PENDING_ACCEPT"
        assert t is not None and t.status == "DISPATCHED"

    # 回收返修同样走完全链路收敛 PASSED 终态
    await _accept_and_complete(api_client, tech1_hdr, int(second["id"]))
    approved = await api_client.post(
        f"/api/v1/tickets/{tid}/acceptance",
        json={"decision": "APPROVED"},
        headers={**insp_hdr, "Idempotency-Key": new_idem_key("recycle")},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["rework_order"]["status"] == "PASSED"
    assert approved.json()["ticket"]["status"] == "CLOSED"


# ──────────────────────────────────────────────────────────────────────────────
# C. 并发派工不超上限
# ──────────────────────────────────────────────────────────────────────────────


async def test_concurrent_dispatch_respects_technician_active_cap(
    api_client: AsyncClient,
    async_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """并发派工回归：同一维修员在途派工数不得超过 `max_active_dispatch`。

    基线 `.team/notes/auditor/logs/v010/concurrent-dispatch.log`：barrier 同步下
    两个并发派工均观察到 active=2 -> 双 201 -> FINAL_ACTIVE 4 > MAX 3。
    修复后 `Account` 行 `FOR UPDATE` 串行化：后到者重数得 3 -> 422
    TECHNICIAN_OVERLOADED，在途数恒等上限。
    """
    admin_hdr = await auth_headers_for(api_client, "admin")
    insp_hdr = await auth_headers_for(api_client, "inspector1")

    # 预置 2 个在途派工给 tech1（上限 3 -> 仅剩 1 个名额）
    for point_code, title in (
        ("SL-ZZ-JS-0001", "task-23 并发预置1"),
        ("SL-ZZ-JS-0002", "task-23 并发预置2"),
    ):
        await _create_ticket_and_dispatch(api_client, point_code=point_code, title=title)

    # 新建 2 个金水区点位（2 个种子点位已各挂 1 张 LAMP_OUT 活跃单）
    pts = (await api_client.get("/api/v1/lighting-points", headers=admin_hdr)).json()["items"]
    region_id = next(p for p in pts if p["point_code"] == "SL-ZZ-JS-0001")["region_id"]
    new_points: list[dict[str, Any]] = []
    for code, name in (("SL-REG-01", "回归并发点位1"), ("SL-REG-02", "回归并发点位2")):
        r_pt = await api_client.post(
            "/api/v1/lighting-points",
            json={
                "point_code": code,
                "region_id": region_id,
                "name": name,
                "road_name": "回归路",
                "road_level": "MAIN",
                "longitude": "113.6812340",
                "latitude": "34.7891230",
                "lamp_count": 1,
            },
            headers={**admin_hdr, "Idempotency-Key": new_idem_key("t23-pt")},
        )
        assert r_pt.status_code == 201, r_pt.text
        new_points.append(r_pt.json())

    async def _new_ticket(point_id: int, title: str) -> int:
        r_t = await api_client.post(
            "/api/v1/tickets",
            json={
                "lighting_point_id": point_id,
                "fault_type": "LAMP_OUT",
                "title": title,
                "safety_flag": False,
            },
            headers={**insp_hdr, "Idempotency-Key": new_idem_key("t23-t")},
        )
        assert r_t.status_code == 201, r_t.text
        return int(r_t.json()["id"])

    t1 = await _new_ticket(int(new_points[0]["id"]), "task-23 并发派工1")
    t2 = await _new_ticket(int(new_points[1]["id"]), "task-23 并发派工2")

    cands = (
        await api_client.get(f"/api/v1/tickets/{t1}/dispatch-candidates", headers=admin_hdr)
    ).json()["candidates"]
    cand = next(c for c in cands if c["username"] == "tech1")
    assert cand["active_dispatch_count"] == 2
    assert cand["max_active_dispatch"] == 3

    # 旧实现可在两次计数后同步放行；行锁修复后第二次计数须等第一笔提交，
    # 因此第一笔有限等待后放行，避免测试屏障和生产锁形成死锁。
    observations: list[int] = []
    gate = asyncio.Event()
    original_count = dispatch_service._count_active_dispatches

    async def synchronized_count(session: AsyncSession, account_id: int) -> int:
        count = await original_count(session, account_id)
        observations.append(count)
        if len(observations) == 2:
            gate.set()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(gate.wait(), timeout=1)
        return count

    monkeypatch.setattr(dispatch_service, "_count_active_dispatches", synchronized_count)

    async def _dispatch(tid: int) -> Response:
        return await api_client.post(
            f"/api/v1/tickets/{tid}/dispatch",
            json={
                "technician_account_id": cand["account_id"],
                "scope_authorization_id": cand["scope_authorization_id"],
            },
            headers={**admin_hdr, "Idempotency-Key": new_idem_key("t23-d")},
        )

    results = await asyncio.gather(_dispatch(t1), _dispatch(t2))

    statuses = sorted(r.status_code for r in results)
    assert statuses == [201, 422], [(r.status_code, r.text) for r in results]
    assert observations == [2, 3]
    loser = next(r for r in results if r.status_code != 201)
    assert loser.json()["error"]["code"] == "TECHNICIAN_OVERLOADED"

    async with AsyncSession(async_engine, expire_on_commit=False) as s:
        active_cnt = await s.scalar(
            select(func.count(DispatchRecord.id)).where(
                DispatchRecord.technician_account_id == cand["account_id"],
                DispatchRecord.status.in_(("PENDING_ACCEPT", "ACCEPTED")),
            )
        )
        # 在途数恒等上限 3，绝不越限（修复前实测为 4）
        assert active_cnt == cand["max_active_dispatch"] == 3

        # 被拒一方工单保持 PENDING_DISPATCH 可再派，且未残留任何派工行
        loser_tid = t1 if results[0] is loser else t2
        loser_ticket = await s.scalar(select(FaultTicket).where(FaultTicket.id == loser_tid))
        loser_dispatch_cnt = await s.scalar(
            select(func.count(DispatchRecord.id)).where(DispatchRecord.ticket_id == loser_tid)
        )
        assert loser_ticket is not None and loser_ticket.status == "PENDING_DISPATCH"
        assert loser_dispatch_cnt == 0
