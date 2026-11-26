"""L2 API 4-state-machine coverage: all 11 terminal states, >=3 illegals each, rework & review."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from tests.integration.conftest import auth_headers_for, make_static_image, new_idem_key
from tomori.models.dispatch_record import DispatchRecord
from tomori.models.notification_task import NotificationTask
from tomori.models.rework_order import ReworkOrder
from tomori.services.exceptions import INVALID_STATE_TRANSITION, AppError
from tomori.services.state_machine import (
    DISPATCH_STATES,
    DISPATCH_TERMINAL_STATES,
    NOTIFICATION_STATES,
    NOTIFICATION_TERMINAL_STATES,
    REWORK_STATES,
    REWORK_TERMINAL_STATES,
    TICKET_STATES,
    TICKET_TERMINAL_STATES,
    transition_notification,
    transition_rework,
)

SAFETY_OK: dict[str, object] = {
    "electrical_safety_checked": True,
    "safety_checklist": {
        "power_isolated": True,
        "voltage_verified_zero": True,
        "insulation_intact": True,
        "grounding_connected": True,
        "ppe_worn": True,
    },
    "safety_note": "已断电、验电为零并穿戴绝缘护具",
}


async def _create_manual_ticket(
    client: AsyncClient,
    headers: dict[str, str],
    *,
    point_code: str = "SL-ZZ-JS-0001",
    fault_type: str = "LAMP_OUT",
) -> dict[str, Any]:
    pts = (await client.get("/api/v1/lighting-points", headers=headers)).json()["items"]
    pt = next(p for p in pts if p["point_code"] == point_code)
    r = await client.post(
        "/api/v1/tickets",
        json={
            "lighting_point_id": pt["id"],
            "fault_type": fault_type,
            "title": f"SM-{point_code}-{fault_type}",
            "safety_flag": False,
        },
        headers={**headers, "Idempotency-Key": new_idem_key("sm-new")},
    )
    assert r.status_code == 201, r.text
    res: dict[str, Any] = r.json()
    return res


async def _dispatch_to_tech1(
    client: AsyncClient,
    admin_hdr: dict[str, str],
    ticket_id: int,
    *,
    username: str = "tech1",
) -> dict[str, Any]:
    cands = (
        await client.get(f"/api/v1/tickets/{ticket_id}/dispatch-candidates", headers=admin_hdr)
    ).json()["candidates"]
    cand = next(c for c in cands if c["username"] == username)
    r = await client.post(
        f"/api/v1/tickets/{ticket_id}/dispatch",
        json={
            "technician_account_id": cand["account_id"],
            "scope_authorization_id": cand["scope_authorization_id"],
        },
        headers={**admin_hdr, "Idempotency-Key": new_idem_key("sm-disp")},
    )
    assert r.status_code == 201, r.text
    res: dict[str, Any] = r.json()
    return res


async def _accept_and_complete(
    client: AsyncClient,
    tech_hdr: dict[str, str],
    dispatch_id: int,
) -> None:
    r_acc = await client.post(
        f"/api/v1/dispatches/{dispatch_id}/accept",
        headers={**tech_hdr, "Idempotency-Key": new_idem_key("sm-acc")},
    )
    assert r_acc.status_code == 200, r_acc.text
    now = datetime.now(UTC)
    r_comp = await client.post(
        f"/api/v1/dispatches/{dispatch_id}/complete",
        json={
            **SAFETY_OK,
            "arrived_at": (now - timedelta(minutes=20)).isoformat(),
            "repair_started_at": (now - timedelta(minutes=15)).isoformat(),
            "repair_finished_at": now.isoformat(),
            "root_cause": "LED 驱动电源故障",
            "repair_summary": "已更换电源模块并亮灯复测正常",
            "materials": [
                {
                    "material_code": "MAT-DRV-150",
                    "material_name": "150W 恒流驱动",
                    "quantity": 1,
                    "unit": "PCS",
                    "unit_price": 120.0,
                }
            ],
        },
        headers={**tech_hdr, "Idempotency-Key": new_idem_key("sm-comp")},
    )
    assert r_comp.status_code == 200, r_comp.text


def test_state_machine_constants_match_29_states_and_11_terminals() -> None:
    """Confirm authoritative 29 states and 11 terminals across 4 state machines."""
    assert (
        len(TICKET_STATES) + len(DISPATCH_STATES) + len(REWORK_STATES) + len(NOTIFICATION_STATES)
        == 29
    )
    assert len(TICKET_TERMINAL_STATES) == 2
    assert len(DISPATCH_TERMINAL_STATES) == 5
    assert len(REWORK_TERMINAL_STATES) == 3
    assert len(NOTIFICATION_TERMINAL_STATES) == 1
    assert (
        len(TICKET_TERMINAL_STATES)
        + len(DISPATCH_TERMINAL_STATES)
        + len(REWORK_TERMINAL_STATES)
        + len(NOTIFICATION_TERMINAL_STATES)
    ) == 11


async def test_ticket_and_dispatch_terminals_closed_cancelled_completed_rejected_escalated_timeout(
    api_client: AsyncClient,
    async_engine: AsyncEngine,
) -> None:
    """Cover ticket CLOSED/CANCELLED and dispatch COMPLETED/REJECTED/CANCELLED/ESCALATED/TIMEOUT."""
    admin_hdr = await auth_headers_for(api_client, "admin")
    insp_hdr = await auth_headers_for(api_client, "inspector1")
    tech1_hdr = await auth_headers_for(api_client, "tech1")
    tech2_hdr = await auth_headers_for(api_client, "tech2")

    # 1) Ticket -> CANCELLED via /cancel + illegal transitions on CANCELLED
    t_cancel = await _create_manual_ticket(api_client, insp_hdr, fault_type="OTHER")
    r_c = await api_client.post(
        f"/api/v1/tickets/{t_cancel['id']}/cancel",
        json={"cancel_reason": "现场核实误报撤销"},
        headers={**insp_hdr, "Idempotency-Key": new_idem_key("sm-cancel")},
    )
    assert r_c.status_code == 200
    assert r_c.json()["status"] == "CANCELLED"

    # Ticket illegal transitions >= 3 (on CANCELLED & PENDING_DISPATCH)
    for url, body in (
        (f"/api/v1/tickets/{t_cancel['id']}/cancel", {"cancel_reason": "again"}),
        (f"/api/v1/tickets/{t_cancel['id']}/regrade", {"priority": "P1", "reason": "bad"}),
        (
            f"/api/v1/tickets/{t_cancel['id']}/confirm",
            {
                "fault_type": "LAMP_OUT",
                "safety_flag": False,
                "candidate_decisions": [
                    {"evidence_id": 1, "candidate_id": "x", "decision": "confirmed"}
                ],
            },
        ),
    ):
        r_bad = await api_client.post(
            url,
            json=body,
            headers={**admin_hdr, "Idempotency-Key": new_idem_key("sm-bad-t")},
        )
        assert r_bad.status_code == 422
        assert r_bad.json()["error"]["code"] == "INVALID_STATE_TRANSITION"

    # 2) Dispatch terminal: REJECTED, CANCELLED (reassign escalate=False),
    #    ESCALATED (reassign escalate=True), TIMEOUT_REASSIGNED (sla scan),
    #    COMPLETED -> Ticket CLOSED
    t_main = await _create_manual_ticket(api_client, insp_hdr, fault_type="LAMP_OUT")
    tid = int(t_main["id"])

    # Illegal transition on PENDING_DISPATCH: acceptance & review-resolve
    r_ill_acc = await api_client.post(
        f"/api/v1/tickets/{tid}/acceptance",
        json={"decision": "APPROVED"},
        headers={**insp_hdr, "Idempotency-Key": new_idem_key("sm-ill-acc")},
    )
    assert r_ill_acc.status_code == 422
    assert r_ill_acc.json()["error"]["code"] == "INVALID_STATE_TRANSITION"

    # d1 -> REJECTED
    d1 = await _dispatch_to_tech1(api_client, admin_hdr, tid, username="tech1")
    r_rej = await api_client.post(
        f"/api/v1/dispatches/{d1['id']}/reject",
        json={"reject_reason": "工具未带齐"},
        headers={**tech1_hdr, "Idempotency-Key": new_idem_key("sm-rej")},
    )
    assert r_rej.status_code == 200
    assert r_rej.json()["status"] == "REJECTED"

    # Dispatch illegal transitions >= 3: accept/complete on REJECTED
    for suffix, payload in (
        ("accept", None),
        ("reject", {"reject_reason": "dup"}),
        (
            "complete",
            {
                **SAFETY_OK,
                "arrived_at": datetime.now(UTC).isoformat(),
                "repair_summary": "illegal",
            },
        ),
    ):
        r_d_bad = await api_client.post(
            f"/api/v1/dispatches/{d1['id']}/{suffix}",
            json=payload,
            headers={**tech1_hdr, "Idempotency-Key": new_idem_key("sm-d-bad")},
        )
        assert r_d_bad.status_code == 422
        assert r_d_bad.json()["error"]["code"] == "INVALID_STATE_TRANSITION"

    # d2 -> CANCELLED via reassign(escalate=False)
    d2 = await _dispatch_to_tech1(api_client, admin_hdr, tid, username="tech1")
    cands = (
        await api_client.get(f"/api/v1/tickets/{tid}/dispatch-candidates", headers=admin_hdr)
    ).json()["candidates"]
    t2_cand = next(c for c in cands if c["username"] == "tech2")
    r_re1 = await api_client.post(
        f"/api/v1/tickets/{tid}/reassign",
        json={
            "technician_account_id": t2_cand["account_id"],
            "scope_authorization_id": t2_cand["scope_authorization_id"],
            "reason": "协调王强就近接单",
            "escalate": False,
        },
        headers={**admin_hdr, "Idempotency-Key": new_idem_key("sm-re1")},
    )
    assert r_re1.status_code == 200
    d3 = r_re1.json()

    # d3 -> ESCALATED via reassign(escalate=True)
    t1_cand = next(c for c in cands if c["username"] == "tech1")
    r_re2 = await api_client.post(
        f"/api/v1/tickets/{tid}/reassign",
        json={
            "technician_account_id": t1_cand["account_id"],
            "scope_authorization_id": t1_cand["scope_authorization_id"],
            "reason": "响应超时管理员升级改派",
            "escalate": True,
        },
        headers={**admin_hdr, "Idempotency-Key": new_idem_key("sm-re2")},
    )
    assert r_re2.status_code == 200
    d4 = r_re2.json()

    # d4 -> TIMEOUT_REASSIGNED via POST /sla/scan after expired accept_deadline_at
    async with AsyncSession(async_engine, expire_on_commit=False) as s:
        await s.execute(
            text("UPDATE dispatch_records SET accept_deadline_at = :past WHERE id = :did"),
            {"past": datetime.now(UTC) - timedelta(hours=1), "did": d4["id"]},
        )
        await s.commit()
    r_scan = await api_client.post("/api/v1/sla/scan", headers=admin_hdr)
    assert r_scan.status_code == 200
    assert r_scan.json()["timeout_reassigned"] >= 1

    # d5 -> COMPLETED -> Acceptance APPROVED -> Ticket CLOSED
    d5 = await _dispatch_to_tech1(api_client, admin_hdr, tid, username="tech2")
    await _accept_and_complete(api_client, tech2_hdr, int(d5["id"]))
    r_pass = await api_client.post(
        f"/api/v1/tickets/{tid}/acceptance",
        json={"decision": "APPROVED", "comment": "亮灯恢复正常"},
        headers={**insp_hdr, "Idempotency-Key": new_idem_key("sm-pass")},
    )
    assert r_pass.status_code == 200
    assert r_pass.json()["ticket"]["status"] == "CLOSED"

    async with AsyncSession(async_engine, expire_on_commit=False) as s:
        statuses = {
            row[0]: row[1]
            for row in (
                await s.execute(
                    select(DispatchRecord.id, DispatchRecord.status).where(
                        DispatchRecord.ticket_id == tid
                    )
                )
            ).all()
        }
        assert statuses[int(d1["id"])] == "REJECTED"
        assert statuses[int(d2["id"])] == "CANCELLED"
        assert statuses[int(d3["id"])] == "ESCALATED"
        assert statuses[int(d4["id"])] == "TIMEOUT_REASSIGNED"
        assert statuses[int(d5["id"])] == "COMPLETED"


async def test_rework_round2_threshold_and_round3_manual_review_three_exits(
    api_client: AsyncClient,
    async_engine: AsyncEngine,
) -> None:
    """Rework: round 1 & 2 stay REWORK_PENDING_DISPATCH; round 3 -> MANUAL_REVIEW; 3 exits."""
    admin_hdr = await auth_headers_for(api_client, "admin")
    insp_hdr = await auth_headers_for(api_client, "inspector1")
    tech1_hdr = await auth_headers_for(api_client, "tech1")

    # ── Path A: Rework -> PASSED (rework terminal #1) & Rework -> CANCELLED (terminal #3) ──
    t_a = await _create_manual_ticket(api_client, insp_hdr, fault_type="LAMP_OUT")
    tid_a = int(t_a["id"])

    # Round 0 repair -> Reject 1 -> Rework #1 (round_no=1 <= 2 -> REWORK_PENDING_DISPATCH)
    d0 = await _dispatch_to_tech1(api_client, admin_hdr, tid_a)
    await _accept_and_complete(api_client, tech1_hdr, int(d0["id"]))
    r_rej1 = await api_client.post(
        f"/api/v1/tickets/{tid_a}/acceptance",
        json={"decision": "REJECTED", "comment": "第1次验收仍不亮"},
        headers={**insp_hdr, "Idempotency-Key": new_idem_key("rw-r1")},
    )
    assert r_rej1.status_code == 200
    assert r_rej1.json()["ticket"]["status"] == "REWORK_PENDING_DISPATCH"
    rw1 = r_rej1.json()["rework_order"]
    assert rw1["round_no"] == 1
    assert rw1["requires_manual_review"] is False
    assert rw1["status"] == "PENDING_DISPATCH"

    # Rework illegal transition check while active rework in PENDING_DISPATCH
    r_rw_cancel_bad = await api_client.post(
        f"/api/v1/tickets/{tid_a}/cancel",
        json={"cancel_reason": "返修中不准直接 cancel"},
        headers={**insp_hdr, "Idempotency-Key": new_idem_key("rw-bad-c")},
    )
    assert r_rw_cancel_bad.status_code == 422
    assert r_rw_cancel_bad.json()["error"]["code"] == "INVALID_STATE_TRANSITION"

    # Round 1 repair -> Reject 2 -> Rework #2 (round_no == max_rework_rounds=2 -> NOT exceed!)
    d1 = await _dispatch_to_tech1(api_client, admin_hdr, tid_a)
    await _accept_and_complete(api_client, tech1_hdr, int(d1["id"]))
    r_rej2 = await api_client.post(
        f"/api/v1/tickets/{tid_a}/acceptance",
        json={"decision": "REJECTED", "comment": "第2次验收照度仍不达标"},
        headers={**insp_hdr, "Idempotency-Key": new_idem_key("rw-r2")},
    )
    assert r_rej2.status_code == 200
    assert r_rej2.json()["ticket"]["status"] == "REWORK_PENDING_DISPATCH"
    rw2 = r_rej2.json()["rework_order"]
    assert rw2["round_no"] == 2
    assert rw2["requires_manual_review"] is False
    assert rw2["status"] == "PENDING_DISPATCH"

    # Round 2 repair -> Reject 3 -> round_no=3 > 2 -> MANUAL_REVIEW + rw3 IN_MANUAL_REVIEW!
    d2 = await _dispatch_to_tech1(api_client, admin_hdr, tid_a)
    await _accept_and_complete(api_client, tech1_hdr, int(d2["id"]))
    r_rej3 = await api_client.post(
        f"/api/v1/tickets/{tid_a}/acceptance",
        json={"decision": "REJECTED", "comment": "第3次仍不合格触发强制复核"},
        headers={**insp_hdr, "Idempotency-Key": new_idem_key("rw-r3")},
    )
    assert r_rej3.status_code == 200
    assert r_rej3.json()["ticket"]["status"] == "MANUAL_REVIEW"
    assert r_rej3.json()["ticket"]["rework_count"] == 3
    rw3 = r_rej3.json()["rework_order"]
    assert rw3["round_no"] == 3
    assert rw3["requires_manual_review"] is True
    assert rw3["status"] == "IN_MANUAL_REVIEW"

    # Exit 1: APPROVE_REASSIGN -> ticket PENDING_DISPATCH, rw3 CLOSED_BY_REVIEW (terminal #2)
    r_exit1 = await api_client.post(
        f"/api/v1/tickets/{tid_a}/review-resolve",
        json={"resolution": "APPROVE_REASSIGN", "reason": "批准重新派工检修"},
        headers={**admin_hdr, "Idempotency-Key": new_idem_key("rev-e1")},
    )
    assert r_exit1.status_code == 200
    assert r_exit1.json()["status"] == "PENDING_DISPATCH"

    # Complete after APPROVE_REASSIGN -> APPROVED -> CLOSED
    d_after = await _dispatch_to_tech1(api_client, admin_hdr, tid_a)
    await _accept_and_complete(api_client, tech1_hdr, int(d_after["id"]))
    r_close = await api_client.post(
        f"/api/v1/tickets/{tid_a}/acceptance",
        json={"decision": "APPROVED"},
        headers={**insp_hdr, "Idempotency-Key": new_idem_key("rw-pass")},
    )
    assert r_close.status_code == 200
    assert r_close.json()["ticket"]["status"] == "CLOSED"

    # Rework PASSED terminal path on separate ticket
    t_p = await _create_manual_ticket(api_client, insp_hdr, fault_type="OCCLUDED")
    tid_p = int(t_p["id"])
    dp0 = await _dispatch_to_tech1(api_client, admin_hdr, tid_p)
    await _accept_and_complete(api_client, tech1_hdr, int(dp0["id"]))
    await api_client.post(
        f"/api/v1/tickets/{tid_p}/acceptance",
        json={"decision": "REJECTED", "comment": "未修剪干净"},
        headers={**insp_hdr, "Idempotency-Key": new_idem_key("rwp-1")},
    )
    dp1 = await _dispatch_to_tech1(api_client, admin_hdr, tid_p)
    await _accept_and_complete(api_client, tech1_hdr, int(dp1["id"]))
    r_passed = await api_client.post(
        f"/api/v1/tickets/{tid_p}/acceptance",
        json={"decision": "APPROVED", "comment": "返修验收合格"},
        headers={**insp_hdr, "Idempotency-Key": new_idem_key("rwp-2")},
    )
    assert r_passed.status_code == 200
    assert r_passed.json()["rework_order"]["status"] == "PASSED"

    # ── Path B & C: Enter MANUAL_REVIEW via dispatch escalate -> SPECIAL_CLOSE & TERMINATE_CANCEL
    t_b = await _create_manual_ticket(api_client, insp_hdr, fault_type="FIXTURE_DAMAGED")
    tid_b = int(t_b["id"])
    db = await _dispatch_to_tech1(api_client, admin_hdr, tid_b)
    await api_client.post(
        f"/api/v1/dispatches/{db['id']}/accept",
        headers={**tech1_hdr, "Idempotency-Key": new_idem_key("esc-acc")},
    )
    r_esc = await api_client.post(
        f"/api/v1/dispatches/{db['id']}/escalate",
        json={"escalate_reason": "现场发现供电箱高空作业需特批"},
        headers=tech1_hdr,
    )
    assert r_esc.status_code == 200
    assert r_esc.json()["status"] == "ESCALATED"
    assert r_esc.json()["ticket_status"] == "MANUAL_REVIEW"

    # SPECIAL_CLOSE requires len(reason) >= 10 -> <10 rejected 422 VALIDATION_ERROR
    r_short = await api_client.post(
        f"/api/v1/tickets/{tid_b}/review-resolve",
        json={"resolution": "SPECIAL_CLOSE", "reason": "太短"},
        headers={**admin_hdr, "Idempotency-Key": new_idem_key("rev-short")},
    )
    assert r_short.status_code == 422
    assert r_short.json()["error"]["code"] == "VALIDATION_ERROR"

    # SPECIAL_CLOSE requires urged_at/urged_unit/urge_evidence_ids (evidence on this ticket)
    r_ev = await api_client.post(
        f"/api/v1/tickets/{tid_b}/evidences",
        data={"evidence_type": "INSPECTION_PHOTO"},
        files=[("files", ("urge.jpg", make_static_image("JPEG"), "image/jpeg"))],
        headers=insp_hdr,
    )
    assert r_ev.status_code == 201, r_ev.text
    urge_evidence_id = int(r_ev.json()[0]["id"])

    r_exit2 = await api_client.post(
        f"/api/v1/tickets/{tid_b}/review-resolve",
        json={
            "resolution": "SPECIAL_CLOSE",
            "reason": "经核实已合并市政改造工程特批关单",
            "urged_at": datetime.now(UTC).isoformat(),
            "urged_unit": "郑州市市政设施维护单位",
            "urge_evidence_ids": [urge_evidence_id],
        },
        headers={**admin_hdr, "Idempotency-Key": new_idem_key("rev-e2")},
    )
    assert r_exit2.status_code == 200
    assert r_exit2.json()["status"] == "CLOSED"

    # Exit 3: TERMINATE_CANCEL (and rework_order terminal CANCELLED / PASSED checks)
    t_c = await _create_manual_ticket(api_client, insp_hdr, fault_type="FLICKERING")
    tid_c = int(t_c["id"])
    dc = await _dispatch_to_tech1(api_client, admin_hdr, tid_c)
    await api_client.post(
        f"/api/v1/dispatches/{dc['id']}/accept",
        headers={**tech1_hdr, "Idempotency-Key": new_idem_key("esc-acc2")},
    )
    await api_client.post(
        f"/api/v1/dispatches/{dc['id']}/escalate",
        json={"escalate_reason": "该点位市政拆迁撤销"},
        headers=tech1_hdr,
    )
    r_exit3 = await api_client.post(
        f"/api/v1/tickets/{tid_c}/review-resolve",
        json={"resolution": "TERMINATE_CANCEL", "reason": "市政拆除终止工单"},
        headers={**admin_hdr, "Idempotency-Key": new_idem_key("rev-e3")},
    )
    assert r_exit3.status_code == 200
    assert r_exit3.json()["status"] == "CANCELLED"

    # Verify rework_orders terminal rows & >=3 illegal transitions on rework_orders
    async with AsyncSession(async_engine, expire_on_commit=False) as s:
        rw_statuses = {
            row[0]: row[1]
            for row in (await s.execute(select(ReworkOrder.id, ReworkOrder.status))).all()
        }
        assert rw_statuses[rw1["id"]] == "CANCELLED"
        assert rw_statuses[rw2["id"]] == "CANCELLED"
        assert rw_statuses[rw3["id"]] == "CLOSED_BY_REVIEW"
        assert "PASSED" in rw_statuses.values()

        for rwid, bad_dst in (
            (rw1["id"], "IN_REPAIR"),
            (rw3["id"], "PENDING_DISPATCH"),
            (r_passed.json()["rework_order"]["id"], "DISPATCHED"),
        ):
            rw_row = await s.scalar(select(ReworkOrder).where(ReworkOrder.id == rwid))
            assert rw_row is not None
            with pytest.raises(AppError) as exc_rw:
                await transition_rework(s, rw_row, to_status=bad_dst)
            assert exc_rw.value.code == INVALID_STATE_TRANSITION


async def test_notification_terminal_sent_and_three_illegal_transitions_via_api(
    api_client: AsyncClient,
    async_engine: AsyncEngine,
) -> None:
    """Cover notification terminal SENT and >=3 illegal transitions via API."""
    admin_hdr = await auth_headers_for(api_client, "admin")
    insp_hdr = await auth_headers_for(api_client, "inspector1")

    t = await _create_manual_ticket(api_client, insp_hdr, fault_type="LAMP_OUT")
    assert t["status"] == "PENDING_DISPATCH"

    # Pending notification exists right after ticket creation
    async with AsyncSession(async_engine, expire_on_commit=False) as s:
        notif = await s.scalar(
            select(NotificationTask)
            .where(NotificationTask.related_ticket_id == t["id"])
            .order_by(NotificationTask.id.asc())
        )
        assert notif is not None
        assert notif.status == "PENDING"
        nid = notif.id

    # Illegal 1 & 2: PENDING cannot /retry or /acknowledge (only DEAD_LETTER allowed)
    r_bad1 = await api_client.post(f"/api/v1/notifications/{nid}/retry", headers=admin_hdr)
    assert r_bad1.status_code == 422
    assert r_bad1.json()["error"]["code"] == "INVALID_STATE_TRANSITION"

    r_bad2 = await api_client.post(f"/api/v1/notifications/{nid}/acknowledge", headers=admin_hdr)
    assert r_bad2.status_code == 422
    assert r_bad2.json()["error"]["code"] == "INVALID_STATE_TRANSITION"

    # Advance notification through domain transitions: PENDING -> PROCESSING -> DEAD_LETTER
    async with AsyncSession(async_engine, expire_on_commit=False) as s:
        notif_row = await s.scalar(select(NotificationTask).where(NotificationTask.id == nid))
        assert notif_row is not None
        await transition_notification(s, notif_row, to_status="PROCESSING")
        notif_row.attempt_count = notif_row.max_attempts
        await transition_notification(
            s,
            notif_row,
            to_status="DEAD_LETTER",
            last_error="simulated gateway delivery exhausted",
        )
        await s.commit()

    r_dl = await api_client.get("/api/v1/notifications/dead-letters", headers=admin_hdr)
    assert r_dl.status_code == 200
    assert any(item["id"] == nid for item in r_dl.json()["items"])

    r_ack = await api_client.post(f"/api/v1/notifications/{nid}/acknowledge", headers=admin_hdr)
    assert r_ack.status_code == 200
    assert r_ack.json()["status"] == "SENT"

    # Illegal 3 & 4: terminal SENT cannot /retry or /acknowledge
    for suffix in ("retry", "acknowledge"):
        r_bad_term = await api_client.post(
            f"/api/v1/notifications/{nid}/{suffix}",
            headers=admin_hdr,
        )
        assert r_bad_term.status_code == 422
        assert r_bad_term.json()["error"]["code"] == "INVALID_STATE_TRANSITION"
