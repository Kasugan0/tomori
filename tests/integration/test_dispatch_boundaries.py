"""P0-3 dispatch boundaries: accept_deadline, reject>=3, complete detail, reassign/timeout.

All flows use the real API + real PG17; `accept_deadline_at` is backdated via ORM column
update (a timestamp field, not a status field) exactly like existing SLA tests.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from tests.integration.conftest import auth_headers_for, new_idem_key
from tomori.models.account import Account
from tomori.models.audit_log import AuditLog
from tomori.models.dispatch_record import DispatchRecord
from tomori.models.fault_ticket import FaultTicket
from tomori.models.notification_task import NotificationTask

SAFETY_OK: dict[str, Any] = {
    "electrical_safety_checked": True,
    "safety_checklist": {
        "power_isolated": True,
        "voltage_verified_zero": True,
        "insulation_intact": True,
        "grounding_connected": True,
        "ppe_worn": True,
    },
    "safety_note": "已断电验电并完成个人防护穿戴",
}


def _err(resp: Any) -> dict[str, Any]:
    err: dict[str, Any] = resp.json()["error"]
    return err


async def _create_ticket_and_dispatch(
    api_client: AsyncClient,
    *,
    point_code: str = "SL-ZZ-JS-0001",
    username: str = "tech1",
    title: str = "P0-3 边界工单",
    fault_type: str = "LAMP_OUT",
) -> tuple[int, int]:
    """Create manual ticket -> dispatch to `username`. Returns (ticket_id, dispatch_id)."""
    admin_hdr = await auth_headers_for(api_client, "admin")
    insp_hdr = await auth_headers_for(api_client, "inspector1")
    pts = (await api_client.get("/api/v1/lighting-points", headers=admin_hdr)).json()["items"]
    pt = next(p for p in pts if p["point_code"] == point_code)
    r_t = await api_client.post(
        "/api/v1/tickets",
        json={
            "lighting_point_id": pt["id"],
            "fault_type": fault_type,
            "title": title,
            "safety_flag": False,
        },
        headers={**insp_hdr, "Idempotency-Key": new_idem_key("p03t")},
    )
    assert r_t.status_code == 201, r_t.text
    tid = r_t.json()["id"]
    cands = (
        await api_client.get(f"/api/v1/tickets/{tid}/dispatch-candidates", headers=admin_hdr)
    ).json()["candidates"]
    cand = next(c for c in cands if c["username"] == username)
    r_d = await api_client.post(
        f"/api/v1/tickets/{tid}/dispatch",
        json={
            "technician_account_id": cand["account_id"],
            "scope_authorization_id": cand["scope_authorization_id"],
        },
        headers={**admin_hdr, "Idempotency-Key": new_idem_key("p03d")},
    )
    assert r_d.status_code == 201, r_d.text
    return tid, int(r_d.json()["id"])


async def test_dispatch_accept_after_accept_deadline_returns_422_exact_code(
    api_client: AsyncClient,
    async_engine: AsyncEngine,
) -> None:
    """accept_deadline_at elapsed -> accept must return 422 INVALID_STATE_TRANSITION."""
    tech1_hdr = await auth_headers_for(api_client, "tech1")
    tid, did = await _create_ticket_and_dispatch(api_client, title="P0-3 接单超时边界")

    # Backdate accept_deadline_at (column, not status) 1 second past
    past = datetime.now(UTC) - timedelta(seconds=1)
    async with AsyncSession(async_engine, expire_on_commit=False) as s:
        await s.execute(
            text("UPDATE dispatch_records SET accept_deadline_at = :past WHERE id = :did"),
            {"past": past, "did": did},
        )
        await s.commit()

    r = await api_client.post(
        f"/api/v1/dispatches/{did}/accept",
        headers={**tech1_hdr, "Idempotency-Key": new_idem_key("p03-acc")},
    )
    assert r.status_code == 422, r.text
    err = _err(r)
    assert err["code"] == "INVALID_STATE_TRANSITION"
    assert err["details"]["accept_deadline_at"] is not None


async def test_dispatch_reject_count_three_escalates_to_manual_review(
    api_client: AsyncClient,
    async_engine: AsyncEngine,
) -> None:
    """3 consecutive rejects -> ticket MANUAL_REVIEW, count==3, MANUAL_REVIEW_REQUIRED notif."""
    admin_hdr = await auth_headers_for(api_client, "admin")
    tech1_hdr = await auth_headers_for(api_client, "tech1")

    tid, did = await _create_ticket_and_dispatch(api_client, title="P0-3 三拒升级")

    for round_no in range(1, 4):
        if round_no > 1:
            # PENDING_DISPATCH after previous reject -> dispatch again
            cands = (
                await api_client.get(
                    f"/api/v1/tickets/{tid}/dispatch-candidates", headers=admin_hdr
                )
            ).json()["candidates"]
            cand = next(c for c in cands if c["username"] == "tech1")
            r_d = await api_client.post(
                f"/api/v1/tickets/{tid}/dispatch",
                json={
                    "technician_account_id": cand["account_id"],
                    "scope_authorization_id": cand["scope_authorization_id"],
                },
                headers={**admin_hdr, "Idempotency-Key": new_idem_key(f"p03-r{round_no}")},
            )
            did = int(r_d.json()["id"])
        # Reject is only legal from PENDING_ACCEPT (dispatch state machine B edges) — do NOT accept.
        r_rej = await api_client.post(
            f"/api/v1/dispatches/{did}/reject",
            json={"reject_reason": f"第{round_no}次现场不具备维修条件"},
            headers={**tech1_hdr, "Idempotency-Key": new_idem_key(f"p03-rj{round_no}")},
        )
        assert r_rej.status_code == 200, r_rej.text
        body = r_rej.json()
        assert body["status"] == "REJECTED"

    async with AsyncSession(async_engine, expire_on_commit=False) as s:
        ticket = await s.scalar(select(FaultTicket).where(FaultTicket.id == tid))
        assert ticket is not None
        assert ticket.status == "MANUAL_REVIEW"
        assert ticket.dispatch_reject_count == 3
        assert ticket.manual_review_reason == "dispatch_reject_threshold_exceeded"

        mrv_notifs = (
            await s.scalars(
                select(NotificationTask).where(
                    NotificationTask.related_ticket_id == tid,
                    NotificationTask.event_type == "MANUAL_REVIEW_REQUIRED",
                )
            )
        ).all()
        assert len(mrv_notifs) >= 1

        audits = (
            await s.scalars(
                select(AuditLog).where(
                    AuditLog.ticket_id == tid,
                    AuditLog.action == "DISPATCH_REJECTED",
                )
            )
        ).all()
        assert len(audits) == 3
        assert any(
            a.business_snapshot_json and a.business_snapshot_json.get("exceeded_threshold") is True
            for a in audits
        )


async def test_dispatch_complete_timing_fields_and_material_rows(
    api_client: AsyncClient,
    async_engine: AsyncEngine,
) -> None:
    """complete: timing fields + 2 material rows -> repair_process + material_usage_ids detail."""
    admin_hdr = await auth_headers_for(api_client, "admin")
    tech1_hdr = await auth_headers_for(api_client, "tech1")
    tid, did = await _create_ticket_and_dispatch(api_client, title="P0-3 完工明细")

    await api_client.post(
        f"/api/v1/dispatches/{did}/accept",
        headers={**tech1_hdr, "Idempotency-Key": new_idem_key("p03c-a")},
    )
    now = datetime.now(UTC)
    r_c = await api_client.post(
        f"/api/v1/dispatches/{did}/complete",
        json={
            **SAFETY_OK,
            "arrived_at": (now - timedelta(minutes=45)).isoformat(),
            "repair_started_at": (now - timedelta(minutes=40)).isoformat(),
            "repair_finished_at": now.isoformat(),
            "root_cause": "驱动电源烧毁",
            "repair_summary": "更换驱动电源并复测正常",
            "materials": [
                {
                    "material_code": "MAT-DRV-150",
                    "material_name": "150W 恒流驱动",
                    "quantity": 1,
                    "unit": "PCS",
                    "unit_price": 120.0,
                },
                {
                    "material_code": "MAT-CABLE-5M",
                    "material_name": "5米耐温电缆",
                    "quantity": 2,
                    "unit": "M",
                    "unit_price": 15.5,
                },
            ],
        },
        headers={**tech1_hdr, "Idempotency-Key": new_idem_key("p03c")},
    )
    assert r_c.status_code == 200, r_c.text
    body = r_c.json()
    assert body["dispatch"]["status"] == "COMPLETED"
    assert body["ticket"]["status"] == "PENDING_ACCEPTANCE"
    assert len(body["material_usage_ids"]) == 2
    assert body["repair_process_id"] > 0

    # Verify persisted detail via real GET /repair-processes/{id}
    rp_id = body["repair_process_id"]
    r_rp = await api_client.get(f"/api/v1/repair-processes/{rp_id}", headers=admin_hdr)
    assert r_rp.status_code == 200, r_rp.text
    rp = r_rp.json()
    assert rp["arrived_at"] is not None
    assert rp["root_cause"] == "驱动电源烧毁"
    assert len(rp["materials"]) == 2
    assert {m["material_code"] for m in rp["materials"]} == {"MAT-DRV-150", "MAT-CABLE-5M"}


async def test_reassign_creates_new_dispatch_escalates_old_and_notifies_with_deep_link(
    api_client: AsyncClient,
    async_engine: AsyncEngine,
) -> None:
    """reassign tech1->tech2 escalate=True: old ESCALATED, new dispatch, deep_link notif."""
    admin_hdr = await auth_headers_for(api_client, "admin")
    tid, did = await _create_ticket_and_dispatch(api_client, title="P0-3 改派")
    # NOTE: reassign is only legal in DISPATCHED (not ACCEPTED/IN_REPAIR) — escalate directly.

    cands = (
        await api_client.get(f"/api/v1/tickets/{tid}/dispatch-candidates", headers=admin_hdr)
    ).json()["candidates"]
    cand2 = next(c for c in cands if c["username"] == "tech2")

    r_reassign = await api_client.post(
        f"/api/v1/tickets/{tid}/reassign",
        json={
            "technician_account_id": cand2["account_id"],
            "scope_authorization_id": cand2["scope_authorization_id"],
            "reason": "原维修员请病假，紧急改派",
            "escalate": True,
        },
        headers={**admin_hdr, "Idempotency-Key": new_idem_key("p03-rs")},
    )
    assert r_reassign.status_code in {200, 201}, r_reassign.text
    new_did = int(r_reassign.json()["id"])
    assert new_did != did

    async with AsyncSession(async_engine, expire_on_commit=False) as s:
        old_d = await s.scalar(select(DispatchRecord).where(DispatchRecord.id == did))
        new_d = await s.scalar(select(DispatchRecord).where(DispatchRecord.id == new_did))
        assert old_d is not None and old_d.status == "ESCALATED"
        assert new_d is not None and new_d.status == "PENDING_ACCEPT"
        assert new_d.ticket_id == tid

        # Notifications: TICKET_DISPATCHED to tech2 + DISPATCH_TIMEOUT_REASSIGNED to tech1
        notifs = (
            await s.scalars(
                select(NotificationTask).where(NotificationTask.related_ticket_id == tid)
            )
        ).all()
        tech1 = await s.scalar(select(Account).where(Account.username == "tech1"))
        tech2 = await s.scalar(select(Account).where(Account.username == "tech2"))
        assert tech1 is not None and tech2 is not None
        by_type = {n.event_type: n for n in notifs}
        assert "TICKET_DISPATCHED" in by_type
        assert "DISPATCH_TIMEOUT_REASSIGNED" in by_type
        t_disp = by_type["TICKET_DISPATCHED"]
        t_rea = by_type["DISPATCH_TIMEOUT_REASSIGNED"]
        assert t_disp.recipient_account_id == tech2.id
        assert t_rea.recipient_account_id == tech1.id
        # Side-effect assertions: payload_json contains deep_link and both dispatch ids
        payload = t_rea.payload_json or {}
        assert payload.get("deep_link") is not None
        assert payload.get("new_dispatch_id") == new_did
        assert payload.get("old_dispatch_id") == did


async def test_sla_scan_covers_rework_pending_dispatch_and_manual_review_branches(
    api_client: AsyncClient,
    async_engine: AsyncEngine,
) -> None:
    """Second `/sla/scan` pass covers REWORK_PENDING_DISPATCH + MANUAL_REVIEW overdue tickets."""
    admin_hdr = await auth_headers_for(api_client, "admin")
    tech1_hdr = await auth_headers_for(api_client, "tech1")
    insp_hdr = await auth_headers_for(api_client, "inspector1")

    # Branch A: drive ticket to REWORK_PENDING_DISPATCH (accept->complete->reject)
    tid_rework, did_r = await _create_ticket_and_dispatch(api_client, title="P0-3 SLA 返修分支")
    await api_client.post(
        f"/api/v1/dispatches/{did_r}/accept",
        headers={**tech1_hdr, "Idempotency-Key": new_idem_key("p03s-a")},
    )
    now = datetime.now(UTC)
    await api_client.post(
        f"/api/v1/dispatches/{did_r}/complete",
        json={
            **SAFETY_OK,
            "arrived_at": (now - timedelta(minutes=30)).isoformat(),
            "repair_summary": "初修完成",
            "materials": [],
        },
        headers={**tech1_hdr, "Idempotency-Key": new_idem_key("p03s-c")},
    )
    r_rej = await api_client.post(
        f"/api/v1/tickets/{tid_rework}/acceptance",
        json={"decision": "REJECTED", "comment": "验收不合格返修"},
        headers={**insp_hdr, "Idempotency-Key": new_idem_key("p03s-r")},
    )
    assert r_rej.status_code == 200
    assert r_rej.json()["ticket"]["status"] == "REWORK_PENDING_DISPATCH"

    # Branch B: drive another ticket to MANUAL_REVIEW (3 rejects)
    tid_mrv, did_i = await _create_ticket_and_dispatch(
        api_client, title="P0-3 SLA 复核分支", fault_type="OCCLUDED"
    )
    for i in range(3):
        if i > 0:
            # after reject ticket -> PENDING_DISPATCH; reject only legal from PENDING_ACCEPT
            cands = (
                await api_client.get(
                    f"/api/v1/tickets/{tid_mrv}/dispatch-candidates", headers=admin_hdr
                )
            ).json()["candidates"]
            cand = next(c for c in cands if c["username"] == "tech1")
            r_d = await api_client.post(
                f"/api/v1/tickets/{tid_mrv}/dispatch",
                json={
                    "technician_account_id": cand["account_id"],
                    "scope_authorization_id": cand["scope_authorization_id"],
                },
                headers={**admin_hdr, "Idempotency-Key": new_idem_key(f"p03m-d{i}")},
            )
            did_i = int(r_d.json()["id"])
        await api_client.post(
            f"/api/v1/dispatches/{did_i}/reject",
            json={"reject_reason": f"复核分支第{i + 1}次拒单"},
            headers={**tech1_hdr, "Idempotency-Key": new_idem_key(f"p03m-r{i}")},
        )

    async with AsyncSession(async_engine, expire_on_commit=False) as s:
        t_mrv = await s.scalar(select(FaultTicket).where(FaultTicket.id == tid_mrv))
        assert t_mrv is not None and t_mrv.status == "MANUAL_REVIEW"

    # Backdate repair_deadline_at on both to force overdue marking
    past = datetime.now(UTC) - timedelta(hours=49)
    async with AsyncSession(async_engine, expire_on_commit=False) as s:
        await s.execute(
            text(
                "UPDATE fault_tickets SET repair_deadline_at = :past WHERE id IN (:t1, :t2)"
            ).bindparams(t1=tid_rework, t2=tid_mrv),
            {"past": past},
        )
        await s.commit()

    r_scan = await api_client.post("/api/v1/sla/scan", headers=admin_hdr)
    assert r_scan.status_code == 200, r_scan.text
    scan = r_scan.json()
    assert scan["marked_overdue"] >= 2

    async with AsyncSession(async_engine, expire_on_commit=False) as s:
        t_rw = await s.scalar(select(FaultTicket).where(FaultTicket.id == tid_rework))
        t_mr = await s.scalar(select(FaultTicket).where(FaultTicket.id == tid_mrv))
        assert t_rw is not None and t_rw.is_sla_overdue is True
        assert t_mr is not None and t_mr.is_sla_overdue is True
        assert t_rw.overdue_stage == "REPAIR"
        assert t_mr.overdue_stage == "REPAIR"

        audits_rw = (
            await s.scalars(
                select(AuditLog.action).where(
                    AuditLog.ticket_id == tid_rework,
                    AuditLog.action == "SLA_TIMEOUT_MARKED",
                )
            )
        ).all()
        assert len(audits_rw) >= 1
        audits_mrv = (
            await s.scalars(
                select(AuditLog.action).where(
                    AuditLog.ticket_id == tid_mrv,
                    AuditLog.action == "SLA_TIMEOUT_MARKED",
                )
            )
        ).all()
        assert len(audits_mrv) >= 1
