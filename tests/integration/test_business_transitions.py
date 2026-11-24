"""P1 business transition negative path gap tests: wrong-source-state 422 + detail keys.

All transitions exercised through the real API against real PG17:
- ticket transitions: `confirm`/`cancel`/`regrade`/`review-resolve`/`acceptance`/`reassign`
- dispatch transitions: `accept`/`reject`/`complete`/`escalate`
- error `details` keys asserted precisely (`allowed_from`/`from_status`/`ticket_status`)
- `deps` `FORBIDDEN_REGION_SCOPE` combos (scope gate precedes state gate)
- `services/grading.py` `VALIDATION_ERROR` on malformed inputs (service-level)
- `api/idempotency.py`: 4xx doesn't consume keys; replay only for 2xx
"""

from __future__ import annotations

import io
from typing import Any

from httpx import AsyncClient
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from tests.integration.conftest import auth_headers_for, new_idem_key
from tomori.services.exceptions import VALIDATION_ERROR, AppError
from tomori.services.grading import evaluate_ticket_grading


def _err(resp: Any) -> dict[str, Any]:
    err: dict[str, Any] = resp.json()["error"]
    return err


def _night_png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (400, 400), (10, 10, 15)).save(buf, format="PNG")
    return buf.getvalue()


async def _mk_ticket(
    api_client: AsyncClient,
    *,
    point_code: str = "SL-ZZ-JS-0001",
    fault_type: str = "LAMP_OUT",
    title: str,
    idem_prefix: str,
) -> tuple[int, dict[str, Any], dict[str, Any]]:
    """Create a manual PENDING_DISPATCH ticket. Returns (id, admin_hdr, insp_hdr)."""
    admin_hdr = await auth_headers_for(api_client, "admin")
    insp_hdr = await auth_headers_for(api_client, "inspector1")
    pt = next(
        p
        for p in (await api_client.get("/api/v1/lighting-points", headers=admin_hdr)).json()[
            "items"
        ]
        if p["point_code"] == point_code
    )
    r = await api_client.post(
        "/api/v1/tickets",
        json={
            "lighting_point_id": pt["id"],
            "fault_type": fault_type,
            "title": title,
            "safety_flag": False,
        },
        headers={**insp_hdr, "Idempotency-Key": new_idem_key(idem_prefix)},
    )
    assert r.status_code == 201, r.text
    return int(r.json()["id"]), admin_hdr, insp_hdr


async def _mk_ai_draft(
    api_client: AsyncClient, *, idem_prefix: str
) -> tuple[int, dict[str, Any], dict[str, Any]]:
    """Create an AI_CANDIDATE_PENDING draft (baseline occluded image). Returns (id, hdrs)."""
    admin_hdr = await auth_headers_for(api_client, "admin")
    insp_hdr = await auth_headers_for(api_client, "inspector1")
    pt = next(
        p
        for p in (await api_client.get("/api/v1/lighting-points", headers=admin_hdr)).json()[
            "items"
        ]
        if p["point_code"] == "SL-ZZ-JS-0002"
    )
    buf = io.BytesIO()
    img = Image.new("RGB", (400, 400), (10, 10, 15))
    from PIL import ImageDraw

    ImageDraw.Draw(img).rectangle((180, 180, 220, 220), fill=(0, 140, 0))
    img.save(buf, format="PNG")
    r = await api_client.post(
        "/api/v1/tickets/ai-candidates",
        data={"lighting_point_id": str(pt["id"]), "scene_lighting_hint": "night"},
        files=[("files", ("occ.png", buf.getvalue(), "image/png"))],
        headers={**insp_hdr, "Idempotency-Key": new_idem_key(idem_prefix)},
    )
    assert r.status_code == 201, r.text
    return int(r.json()["ticket"]["id"]), admin_hdr, insp_hdr


async def _dispatch(api_client: AsyncClient, tid: int, admin_hdr: dict[str, Any]) -> int:
    cands = (
        await api_client.get(f"/api/v1/tickets/{tid}/dispatch-candidates", headers=admin_hdr)
    ).json()["candidates"]
    cand = next(c for c in cands if c["username"] == "tech1")
    r = await api_client.post(
        f"/api/v1/tickets/{tid}/dispatch",
        json={
            "technician_account_id": cand["account_id"],
            "scope_authorization_id": cand["scope_authorization_id"],
        },
        headers={**admin_hdr, "Idempotency-Key": new_idem_key("p1t-d")},
    )
    assert r.status_code == 201, r.text
    return int(r.json()["id"])


async def test_ticket_transitions_wrong_source_state_return_422_with_detail_keys(
    api_client: AsyncClient,
) -> None:
    """confirm/cancel/regrade/review-resolve wrong-state -> 422 INVALID_STATE_TRANSITION."""
    admin_hdr = await auth_headers_for(api_client, "admin")
    insp_hdr = await auth_headers_for(api_client, "inspector1")

    # manual ticket is PENDING_DISPATCH (not AI_CANDIDATE_PENDING)
    tid, _, _ = await _mk_ticket(api_client, title="P1 流转负路径", idem_prefix="p1t")

    # confirm requires AI_CANDIDATE_PENDING -> 422
    r_confirm = await api_client.post(
        f"/api/v1/tickets/{tid}/confirm",
        json={"safety_flag": False},
        headers={**insp_hdr, "Idempotency-Key": new_idem_key("p1t-cf")},
    )
    assert r_confirm.status_code == 422
    assert _err(r_confirm)["code"] == "INVALID_STATE_TRANSITION"

    # cancel allowed_from = AI_CANDIDATE_PENDING | PENDING_DISPATCH -> cancel is legal here;
    # drive to DISPATCHED first, then cancel must be 422
    await _dispatch(api_client, tid, admin_hdr)
    r_cancel = await api_client.post(
        f"/api/v1/tickets/{tid}/cancel",
        json={"cancel_reason": "尝试在派工中撤销工单测试负路径"},
        headers={**insp_hdr, "Idempotency-Key": new_idem_key("p1t-cc")},
    )
    assert r_cancel.status_code == 422
    err_cancel = _err(r_cancel)
    assert err_cancel["code"] == "INVALID_STATE_TRANSITION"
    assert err_cancel["details"]["allowed_from"] == [
        "AI_CANDIDATE_PENDING",
        "PENDING_DISPATCH",
    ]
    assert err_cancel["details"]["from_status"] == "DISPATCHED"

    # reassign requires DISPATCHED ticket status; this ticket is DISPATCHED so legal —
    # but a PENDING_DISPATCH ticket must 422. Create another ticket for that probe.
    tid2, _, _ = await _mk_ticket(
        api_client, title="P1 改派负路径", idem_prefix="p1u", fault_type="OCCLUDED"
    )
    cands = (
        await api_client.get(f"/api/v1/tickets/{tid2}/dispatch-candidates", headers=admin_hdr)
    ).json()["candidates"]
    cand2 = cands[0]
    r_reassign = await api_client.post(
        f"/api/v1/tickets/{tid2}/reassign",
        json={
            "technician_account_id": cand2["account_id"],
            "scope_authorization_id": cand2["scope_authorization_id"],
            "reason": "PENDING_DISPATCH 状态下不许改派测试",
            "escalate": False,
        },
        headers={**admin_hdr, "Idempotency-Key": new_idem_key("p1u-rs")},
    )
    assert r_reassign.status_code == 422
    assert _err(r_reassign)["code"] == "INVALID_STATE_TRANSITION"

    # review-resolve requires MANUAL_REVIEW -> 422 on PENDING_DISPATCH
    r_review = await api_client.post(
        f"/api/v1/tickets/{tid2}/review-resolve",
        json={
            "resolution": "SPECIAL_CLOSE",
            "reason": "试图在非复核态直接裁决",
        },
        headers={**admin_hdr, "Idempotency-Key": new_idem_key("p1u-rv")},
    )
    assert r_review.status_code == 422
    assert _err(r_review)["code"] == "INVALID_STATE_TRANSITION"

    # acceptance requires PENDING_ACCEPTANCE -> 422 on PENDING_DISPATCH
    r_acc = await api_client.post(
        f"/api/v1/tickets/{tid2}/acceptance",
        json={"decision": "APPROVED", "comment": "未验收先裁决"},
        headers={**insp_hdr, "Idempotency-Key": new_idem_key("p1u-ac")},
    )
    assert r_acc.status_code == 422
    assert _err(r_acc)["code"] == "INVALID_STATE_TRANSITION"

    # regrade forbidden on AI_CANDIDATE_PENDING (needs an AI draft) + CLOSED/CANCELLED
    aid, _, insp_hdr2 = await _mk_ai_draft(api_client, idem_prefix="p1v")
    r_rg = await api_client.post(
        f"/api/v1/tickets/{aid}/regrade",
        json={"priority": "P2", "reason": "AI 草稿态禁止手工改级"},
        headers={**admin_hdr, "Idempotency-Key": new_idem_key("p1v-rg")},
    )
    assert r_rg.status_code == 422
    err_rg = _err(r_rg)
    assert err_rg["code"] == "INVALID_STATE_TRANSITION"
    assert err_rg["details"]["from_status"] == "AI_CANDIDATE_PENDING"

    # regrade on PENDING_DISPATCH is legal -> happy control
    r_rg_ok = await api_client.post(
        f"/api/v1/tickets/{tid2}/regrade",
        json={"priority": "P2", "reason": "调度侧临时降级为 P2"},
        headers={**admin_hdr, "Idempotency-Key": new_idem_key("p1u-rg")},
    )
    assert r_rg_ok.status_code == 200, r_rg_ok.text
    assert r_rg_ok.json()["priority"] == "P2"


async def test_dispatch_transitions_wrong_source_state_return_422(
    api_client: AsyncClient,
) -> None:
    """accept/reject/complete/escalate wrong-source-state -> 422 + 404/403 edges."""
    tech1_hdr = await auth_headers_for(api_client, "tech1")
    tech2_hdr = await auth_headers_for(api_client, "tech2")
    admin_hdr = await auth_headers_for(api_client, "admin")
    tid, _, _ = await _mk_ticket(api_client, title="P1 派工流转负路径", idem_prefix="p1w")
    did = await _dispatch(api_client, tid, admin_hdr)

    # 404: accept on nonexistent dispatch
    r_404 = await api_client.post(
        "/api/v1/dispatches/999999/accept",
        headers={**tech1_hdr, "Idempotency-Key": new_idem_key("p1w-404")},
    )
    assert r_404.status_code == 404
    assert _err(r_404)["code"] == "RESOURCE_NOT_FOUND"

    # 403: tech2 (not assigned) cannot accept tech1's dispatch
    r_403 = await api_client.post(
        f"/api/v1/dispatches/{did}/accept",
        headers={**tech2_hdr, "Idempotency-Key": new_idem_key("p1w-403")},
    )
    assert r_403.status_code == 403
    assert _err(r_403)["code"] == "FORBIDDEN_ROLE"

    # complete on PENDING_ACCEPT -> 422 (not ACCEPTED yet)
    r_c = await api_client.post(
        f"/api/v1/dispatches/{did}/complete",
        json={
            "electrical_safety_checked": True,
            "safety_checklist": {
                "power_isolated": True,
                "voltage_verified_zero": True,
                "insulation_intact": True,
                "grounding_connected": True,
                "ppe_worn": True,
            },
            "safety_note": "六件套",
            "arrived_at": "2026-11-25T10:00:00Z",
            "repair_summary": "未接单先完工",
            "materials": [],
        },
        headers={**tech1_hdr, "Idempotency-Key": new_idem_key("p1w-c")},
    )
    assert r_c.status_code == 422
    assert _err(r_c)["code"] == "INVALID_STATE_TRANSITION"

    # escalate on PENDING_ACCEPT -> 422 (requires ACCEPTED + IN_REPAIR)
    r_e = await api_client.post(
        f"/api/v1/dispatches/{did}/escalate",
        json={"escalate_reason": "未接单就升级"},
        headers={**tech1_hdr, "Idempotency-Key": new_idem_key("p1w-e")},
    )
    assert r_e.status_code == 422
    err_e = _err(r_e)
    assert err_e["code"] == "INVALID_STATE_TRANSITION"
    assert err_e["details"]["from_status"] == "PENDING_ACCEPT"
    assert "ticket_status" in err_e["details"]

    # accept (happy) -> 200
    r_a = await api_client.post(
        f"/api/v1/dispatches/{did}/accept",
        headers={**tech1_hdr, "Idempotency-Key": new_idem_key("p1w-a")},
    )
    assert r_a.status_code == 200, r_a.text

    # accept again on ACCEPTED -> 422
    r_a2 = await api_client.post(
        f"/api/v1/dispatches/{did}/accept",
        headers={**tech1_hdr, "Idempotency-Key": new_idem_key("p1w-a2")},
    )
    assert r_a2.status_code == 422
    assert _err(r_a2)["code"] == "INVALID_STATE_TRANSITION"

    # reject on ACCEPTED -> 422 (allowed_from PENDING_ACCEPT only)
    r_rj = await api_client.post(
        f"/api/v1/dispatches/{did}/reject",
        json={"reject_reason": "已接单禁止拒单"},
        headers={**tech1_hdr, "Idempotency-Key": new_idem_key("p1w-rj")},
    )
    assert r_rj.status_code == 422
    err_rj = _err(r_rj)
    assert err_rj["code"] == "INVALID_STATE_TRANSITION"
    assert err_rj["details"]["allowed_from"] == ["PENDING_ACCEPT"]
    assert err_rj["details"]["from_status"] == "ACCEPTED"


async def test_region_scope_gate_precedes_state_gate_on_transitions(
    api_client: AsyncClient,
) -> None:
    """deps: FORBIDDEN_REGION_SCOPE fires BEFORE INVALID_STATE_TRANSITION on cross-region."""
    admin_hdr = await auth_headers_for(api_client, "admin")
    insp_hdr = await auth_headers_for(api_client, "inspector1")
    zd_pt = next(
        p
        for p in (await api_client.get("/api/v1/lighting-points", headers=admin_hdr)).json()[
            "items"
        ]
        if p["point_code"] == "SL-ZZ-ZD-0001"
    )
    r_t = await api_client.post(
        "/api/v1/tickets",
        json={
            "lighting_point_id": zd_pt["id"],
            "fault_type": "OCCLUDED",
            "title": "P1 跨区流转探测",
            "safety_flag": False,
        },
        headers={**admin_hdr, "Idempotency-Key": new_idem_key("p1x")},
    )
    assert r_t.status_code == 201
    zd_tid = int(r_t.json()["id"])

    # inspector1 (INSPECT on ZZ-JS only) tries cancel/patch/confirm on ZD ticket -> 403
    # Gate order varies by endpoint role requirement:
    # cancel/confirm/patch require INSPECTOR|ADMIN -> inspector1 passes role gate -> region gate
    #   fires -> 403 FORBIDDEN_REGION_SCOPE.
    # regrade is ADMIN-only -> role gate fires first -> 403 FORBIDDEN_ROLE.
    cases = [
        (
            "post",
            f"/api/v1/tickets/{zd_tid}/cancel",
            {"cancel_reason": "跨区撤销应被拒"},
            "FORBIDDEN_REGION_SCOPE",
        ),
        (
            "patch",
            f"/api/v1/tickets/{zd_tid}",
            {"title": "跨区改草稿"},
            "FORBIDDEN_REGION_SCOPE",
        ),
        (
            "post",
            f"/api/v1/tickets/{zd_tid}/confirm",
            {"safety_flag": False},
            "FORBIDDEN_REGION_SCOPE",
        ),
        (
            "post",
            f"/api/v1/tickets/{zd_tid}/regrade",
            {"priority": "P2", "reason": "跨区改级"},
            "FORBIDDEN_ROLE",
        ),
    ]
    for method, path, payload, expected_code in cases:
        hdr = dict(insp_hdr)
        if method == "post":
            hdr["Idempotency-Key"] = new_idem_key("p1x")
        r = await api_client.request(method, path, json=payload, headers=hdr)
        assert r.status_code == 403, f"{method} {path}: {r.status_code} {r.text}"
        assert _err(r)["code"] == expected_code


async def test_grading_service_rejects_malformed_inputs_with_validation_error(
    api_client: AsyncClient,
    async_engine: AsyncEngine,
) -> None:
    """services/grading.py: each illegal factor -> VALIDATION_ERROR (exact code)."""
    _ = await auth_headers_for(api_client, "admin")  # ensure seed present via client fixture
    async with AsyncSession(async_engine, expire_on_commit=False) as s:
        for kwargs in (
            {"fault_type": "GHOST_FAULT"},
            {"road_level": "LEVEL_9"},
            {"asset_importance": "CRITICAL_X"},
            {"safety_flag": "yes"},
        ):
            base: dict[str, Any] = {
                "fault_type": "LAMP_OUT",
                "road_level": "MAIN_ROAD",
                "asset_importance": "NORMAL",
                "safety_flag": True,
            }
            base.update(kwargs)
            try:
                await evaluate_ticket_grading(s, **base)
            except AppError as exc:
                assert exc.code == VALIDATION_ERROR, f"{kwargs}: got {exc.code}"
            else:
                raise AssertionError(f"expected VALIDATION_ERROR for {kwargs}")


async def test_idempotency_4xx_does_not_consume_key_and_2xx_only_replays(
    api_client: AsyncClient,
    async_engine: AsyncEngine,
) -> None:
    """Same key: 422 then 201 (not replay); replay of success returns X-Idempotent-Replay."""
    insp_hdr = await auth_headers_for(api_client, "inspector1")
    admin_hdr = await auth_headers_for(api_client, "admin")
    pt = next(
        p
        for p in (await api_client.get("/api/v1/lighting-points", headers=admin_hdr)).json()[
            "items"
        ]
        if p["point_code"] == "SL-ZZ-JS-0001"
    )
    key = new_idem_key("p1y")

    # 1) Same key + bad body (missing lighting_point_id) -> 422 does NOT consume key
    r_bad = await api_client.post(
        "/api/v1/tickets",
        json={"fault_type": "LAMP_OUT", "title": "缺字段"},
        headers={**insp_hdr, "Idempotency-Key": key},
    )
    assert r_bad.status_code == 422

    # 2) Same key + valid body -> fresh 201 (NOT payload-mismatch/replay error)
    r_ok = await api_client.post(
        "/api/v1/tickets",
        json={
            "lighting_point_id": pt["id"],
            "fault_type": "OTHER",
            "title": "P1 幂等 4xx 不消费键",
            "safety_flag": False,
        },
        headers={**insp_hdr, "Idempotency-Key": key},
    )
    assert r_ok.status_code == 201, r_ok.text
    assert "x-idempotent-replay" not in {k.lower() for k in r_ok.headers}
    tid = int(r_ok.json()["id"])

    # 3) Exact replay -> X-Idempotent-Replay: true + identical body
    r_replay = await api_client.post(
        "/api/v1/tickets",
        json={
            "lighting_point_id": pt["id"],
            "fault_type": "OTHER",
            "title": "P1 幂等 4xx 不消费键",
            "safety_flag": False,
        },
        headers={**insp_hdr, "Idempotency-Key": key},
    )
    assert r_replay.status_code == 201
    assert r_replay.headers.get("x-idempotent-replay") == "true"
    assert r_replay.json()["id"] == tid

    # 4) Same key + DIFFERENT body -> 422 IDEMPOTENCY_PAYLOAD_MISMATCH
    r_diff = await api_client.post(
        "/api/v1/tickets",
        json={
            "lighting_point_id": pt["id"],
            "fault_type": "OCCLUDED",
            "title": "载荷不一致",
            "safety_flag": True,
        },
        headers={**insp_hdr, "Idempotency-Key": key},
    )
    assert r_diff.status_code == 422
    assert _err(r_diff)["code"] == "IDEMPOTENCY_PAYLOAD_MISMATCH"

    # 5) Only ONE idempotency-keyed 2xx audit row for this key
    async with AsyncSession(async_engine, expire_on_commit=False) as s:
        from tomori.models.audit_log import AuditLog

        rows = (
            await s.scalars(
                select(AuditLog).where(
                    AuditLog.idempotency_key == key,
                    AuditLog.response_status >= 200,
                    AuditLog.response_status < 300,
                )
            )
        ).all()
        assert len(rows) == 1
        assert rows[0].resource_id == tid
