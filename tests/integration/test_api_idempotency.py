"""L2 API 12 idempotent POST endpoints, replay/mismatch, and real-PG concurrency tests."""

from __future__ import annotations

import asyncio
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from tests.integration.conftest import (
    auth_headers_for,
    build_confirm_payload,
    make_occluded_fault_png,
    make_static_image,
    new_idem_key,
)
from tomori.models.audit_log import AuditLog
from tomori.models.fault_ticket import FaultTicket


async def _assert_idempotent_json_cycle(
    client: AsyncClient,
    engine: AsyncEngine,
    *,
    path: str,
    headers: dict[str, str],
    payload: dict[str, Any] | None,
    mismatch_payload: dict[str, Any] | None,
    expected_status: int,
) -> dict[str, Any]:
    """Verify missing/invalid key -> first 2xx -> replay -> payload mismatch for one POST."""
    # 1. Missing Idempotency-Key -> 400 IDEMPOTENCY_KEY_REQUIRED
    r_req = await client.post(path, json=payload, headers=headers)
    assert r_req.status_code == 400, f"{path} missing key -> {r_req.status_code}: {r_req.text}"
    assert r_req.json()["error"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"

    # 2. Invalid Idempotency-Key -> 400 IDEMPOTENCY_KEY_INVALID
    for bad_key in ("short", "bad key with spaces!"):
        r_inv = await client.post(
            path,
            json=payload,
            headers={**headers, "Idempotency-Key": bad_key},
        )
        assert r_inv.status_code == 400, f"{path} invalid key -> {r_inv.status_code}: {r_inv.text}"
        assert r_inv.json()["error"]["code"] == "IDEMPOTENCY_KEY_INVALID"

    # 3. Valid request -> expected 2xx (no X-Idempotent-Replay)
    key = new_idem_key("idem-ok")
    r_ok = await client.post(
        path,
        json=payload,
        headers={**headers, "Idempotency-Key": key},
    )
    assert r_ok.status_code == expected_status, f"{path} ok -> {r_ok.status_code}: {r_ok.text}"
    assert r_ok.headers.get("x-idempotent-replay") is None
    first_body: dict[str, Any] = r_ok.json()

    # 4. Same key + same payload -> exact replay + X-Idempotent-Replay: true
    r_rep = await client.post(
        path,
        json=payload,
        headers={**headers, "Idempotency-Key": key},
    )
    assert r_rep.status_code == expected_status
    assert r_rep.headers.get("x-idempotent-replay") == "true"
    assert r_rep.json() == first_body

    # 5. Same key + different payload -> 422 IDEMPOTENCY_PAYLOAD_MISMATCH
    if mismatch_payload is not None:
        r_mis = await client.post(
            path,
            json=mismatch_payload,
            headers={**headers, "Idempotency-Key": key},
        )
        assert r_mis.status_code == 422, f"{path} mismatch -> {r_mis.status_code}: {r_mis.text}"
        assert r_mis.json()["error"]["code"] == "IDEMPOTENCY_PAYLOAD_MISMATCH"

    # 6. Exactly 1 audit_logs row for this idempotency key
    async with AsyncSession(engine, expire_on_commit=False) as s:
        cnt = int(
            (await s.scalar(select(func.count(AuditLog.id)).where(AuditLog.idempotency_key == key)))
            or 0
        )
        assert cnt == 1
    return first_body


async def test_all_12_idempotent_posts_cycle(
    api_client: AsyncClient,
    async_engine: AsyncEngine,
) -> None:
    """Exercise every one of the 12 mandatory idempotent POST endpoints end-to-end."""
    admin_hdr = await auth_headers_for(api_client, "admin")
    insp_hdr = await auth_headers_for(api_client, "inspector1")
    tech1_hdr = await auth_headers_for(api_client, "tech1")

    pts = (await api_client.get("/api/v1/lighting-points", headers=admin_hdr)).json()["items"]
    pt1 = next(p for p in pts if p["point_code"] == "SL-ZZ-JS-0001")
    pt2 = next(p for p in pts if p["point_code"] == "SL-ZZ-JS-0002")

    # ── #1: POST /tickets/ai-candidates (multipart) ──
    occluded_png = make_occluded_fault_png()
    r_ai_req = await api_client.post(
        "/api/v1/tickets/ai-candidates",
        data={"lighting_point_id": str(pt1["id"]), "scene_lighting_hint": "night"},
        files=[("files", ("occ.png", occluded_png, "image/png"))],
        headers=insp_hdr,
    )
    assert r_ai_req.status_code == 400
    assert r_ai_req.json()["error"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"

    r_ai_inv = await api_client.post(
        "/api/v1/tickets/ai-candidates",
        data={"lighting_point_id": str(pt1["id"]), "scene_lighting_hint": "night"},
        files=[("files", ("occ.png", occluded_png, "image/png"))],
        headers={**insp_hdr, "Idempotency-Key": "bad!"},
    )
    assert r_ai_inv.status_code == 400
    assert r_ai_inv.json()["error"]["code"] == "IDEMPOTENCY_KEY_INVALID"

    ai_key = new_idem_key("idem-ai")
    r_ai_ok = await api_client.post(
        "/api/v1/tickets/ai-candidates",
        data={"lighting_point_id": str(pt1["id"]), "scene_lighting_hint": "night"},
        files=[("files", ("occ.png", occluded_png, "image/png"))],
        headers={**insp_hdr, "Idempotency-Key": ai_key},
    )
    assert r_ai_ok.status_code == 201
    r_ai_rep = await api_client.post(
        "/api/v1/tickets/ai-candidates",
        data={"lighting_point_id": str(pt1["id"]), "scene_lighting_hint": "night"},
        files=[("files", ("occ.png", occluded_png, "image/png"))],
        headers={**insp_hdr, "Idempotency-Key": ai_key},
    )
    assert r_ai_rep.status_code == 201
    assert r_ai_rep.headers.get("x-idempotent-replay") == "true"
    assert r_ai_rep.json()["ticket"]["id"] == r_ai_ok.json()["ticket"]["id"]

    other_jpg = make_static_image("JPEG")
    r_ai_mis = await api_client.post(
        "/api/v1/tickets/ai-candidates",
        data={"lighting_point_id": str(pt1["id"]), "scene_lighting_hint": "night"},
        files=[("files", ("other.jpg", other_jpg, "image/jpeg"))],
        headers={**insp_hdr, "Idempotency-Key": ai_key},
    )
    assert r_ai_mis.status_code == 422
    assert r_ai_mis.json()["error"]["code"] == "IDEMPOTENCY_PAYLOAD_MISMATCH"
    ai_ticket_id = int(r_ai_ok.json()["ticket"]["id"])

    # ── #2: POST /tickets (manual report) ──
    manual_t = await _assert_idempotent_json_cycle(
        api_client,
        async_engine,
        path="/api/v1/tickets",
        headers=insp_hdr,
        payload={
            "lighting_point_id": pt2["id"],
            "fault_type": "LAMP_OUT",
            "title": "Idem manual ticket",
            "safety_flag": False,
        },
        mismatch_payload={
            "lighting_point_id": pt2["id"],
            "fault_type": "LAMP_OUT",
            "title": "Different title",
            "safety_flag": False,
        },
        expected_status=201,
    )
    manual_tid = int(manual_t["id"])

    # ── #3: POST /tickets/{id}/confirm (task-25: per-candidate decisions) ──
    confirm_evidences = r_ai_ok.json()["evidences"]
    await _assert_idempotent_json_cycle(
        api_client,
        async_engine,
        path=f"/api/v1/tickets/{ai_ticket_id}/confirm",
        headers=insp_hdr,
        payload=build_confirm_payload(confirm_evidences),
        mismatch_payload=build_confirm_payload(confirm_evidences, safety_flag=True),
        expected_status=200,
    )

    # ── #4: POST /tickets/{id}/cancel (on AI-confirmed ticket) ──
    await _assert_idempotent_json_cycle(
        api_client,
        async_engine,
        path=f"/api/v1/tickets/{ai_ticket_id}/cancel",
        headers=insp_hdr,
        payload={"cancel_reason": "误报撤销"},
        mismatch_payload={"cancel_reason": "另一个理由"},
        expected_status=200,
    )

    # ── #5: POST /tickets/{id}/regrade ──
    await _assert_idempotent_json_cycle(
        api_client,
        async_engine,
        path=f"/api/v1/tickets/{manual_tid}/regrade",
        headers=admin_hdr,
        payload={"priority": "P1", "reason": "重点路段升级"},
        mismatch_payload={"priority": "P3", "reason": "降级"},
        expected_status=200,
    )

    # ── #6: POST /tickets/{id}/dispatch ──
    cands = (
        await api_client.get(f"/api/v1/tickets/{manual_tid}/dispatch-candidates", headers=admin_hdr)
    ).json()["candidates"]
    t1_c = next(c for c in cands if c["username"] == "tech1")
    t2_c = next(c for c in cands if c["username"] == "tech2")
    await _assert_idempotent_json_cycle(
        api_client,
        async_engine,
        path=f"/api/v1/tickets/{manual_tid}/dispatch",
        headers=admin_hdr,
        payload={
            "technician_account_id": t1_c["account_id"],
            "scope_authorization_id": t1_c["scope_authorization_id"],
        },
        mismatch_payload={
            "technician_account_id": t2_c["account_id"],
            "scope_authorization_id": t2_c["scope_authorization_id"],
        },
        expected_status=201,
    )

    # ── #7: POST /tickets/{id}/reassign ──
    d2 = await _assert_idempotent_json_cycle(
        api_client,
        async_engine,
        path=f"/api/v1/tickets/{manual_tid}/reassign",
        headers=admin_hdr,
        payload={
            "technician_account_id": t1_c["account_id"],
            "scope_authorization_id": t1_c["scope_authorization_id"],
            "reason": "重新指定张伟",
            "escalate": False,
        },
        mismatch_payload={
            "technician_account_id": t2_c["account_id"],
            "scope_authorization_id": t2_c["scope_authorization_id"],
            "reason": "改另一个人",
            "escalate": True,
        },
        expected_status=200,
    )
    d2_id = int(d2["id"])

    # ── #8: POST /dispatches/{id}/reject ──
    await _assert_idempotent_json_cycle(
        api_client,
        async_engine,
        path=f"/api/v1/dispatches/{d2_id}/reject",
        headers=tech1_hdr,
        payload={"reject_reason": "设备不足"},
        mismatch_payload={"reject_reason": "距离太远"},
        expected_status=200,
    )

    # Re-dispatch so we can test #9 accept, #10 complete, #11 acceptance, #12 review-resolve
    r_d3 = await api_client.post(
        f"/api/v1/tickets/{manual_tid}/dispatch",
        json={
            "technician_account_id": t1_c["account_id"],
            "scope_authorization_id": t1_c["scope_authorization_id"],
        },
        headers={**admin_hdr, "Idempotency-Key": new_idem_key("idem-d3")},
    )
    assert r_d3.status_code == 201
    d3_id = int(r_d3.json()["id"])

    # ── #9: POST /dispatches/{id}/accept (body-less endpoint: payload_hash derived from route) ──
    await _assert_idempotent_json_cycle(
        api_client,
        async_engine,
        path=f"/api/v1/dispatches/{d3_id}/accept",
        headers=tech1_hdr,
        payload=None,
        mismatch_payload=None,
        expected_status=200,
    )

    # ── #10: POST /dispatches/{id}/complete ──
    now_iso = datetime.now(UTC).isoformat()
    comp_base: dict[str, Any] = {
        "electrical_safety_checked": True,
        "safety_checklist": {
            "power_isolated": True,
            "voltage_verified_zero": True,
            "insulation_intact": True,
            "grounding_connected": True,
            "ppe_worn": True,
        },
        "safety_note": "安全检查已全部确认",
        "arrived_at": now_iso,
        "repair_summary": "已更换光源",
        "materials": [],
    }
    await _assert_idempotent_json_cycle(
        api_client,
        async_engine,
        path=f"/api/v1/dispatches/{d3_id}/complete",
        headers=tech1_hdr,
        payload=comp_base,
        mismatch_payload={**comp_base, "repair_summary": "不同摘要"},
        expected_status=200,
    )

    # ── #11: POST /tickets/{id}/acceptance (with 4xx-no-consume check first) ──
    reuse_key = new_idem_key("idem-4xx-free")
    r_4xx = await api_client.post(
        f"/api/v1/tickets/{manual_tid}/acceptance",
        json={"decision": "REJECTED", "comment": ""},
        headers={**insp_hdr, "Idempotency-Key": reuse_key},
    )
    assert r_4xx.status_code == 422

    # Failed 4xx MUST NOT consume Idempotency-Key: same key now succeeds with APPROVED
    r_after_4xx = await api_client.post(
        f"/api/v1/tickets/{manual_tid}/acceptance",
        json={"decision": "APPROVED", "comment": "合格关闭"},
        headers={**insp_hdr, "Idempotency-Key": reuse_key},
    )
    assert r_after_4xx.status_code == 200
    r_acc_mis = await api_client.post(
        f"/api/v1/tickets/{manual_tid}/acceptance",
        json={"decision": "APPROVED", "comment": "换个备注"},
        headers={**insp_hdr, "Idempotency-Key": reuse_key},
    )
    assert r_acc_mis.status_code == 422
    assert r_acc_mis.json()["error"]["code"] == "IDEMPOTENCY_PAYLOAD_MISMATCH"

    # ── #12: POST /tickets/{id}/review-resolve ──
    t_rev = await api_client.post(
        "/api/v1/tickets",
        json={
            "lighting_point_id": pt2["id"],
            "fault_type": "OTHER",
            "title": "Review resolve idem ticket",
            "safety_flag": False,
        },
        headers={**insp_hdr, "Idempotency-Key": new_idem_key("idem-rev-t")},
    )
    assert t_rev.status_code == 201
    rev_tid = int(t_rev.json()["id"])
    r_dr = await api_client.post(
        f"/api/v1/tickets/{rev_tid}/dispatch",
        json={
            "technician_account_id": t1_c["account_id"],
            "scope_authorization_id": t1_c["scope_authorization_id"],
        },
        headers={**admin_hdr, "Idempotency-Key": new_idem_key("idem-rev-d")},
    )
    dr_id = int(r_dr.json()["id"])
    await api_client.post(
        f"/api/v1/dispatches/{dr_id}/accept",
        headers={**tech1_hdr, "Idempotency-Key": new_idem_key("idem-rev-a")},
    )
    await api_client.post(
        f"/api/v1/dispatches/{dr_id}/escalate",
        json={"escalate_reason": "需要管理员裁决"},
        headers=tech1_hdr,
    )
    await _assert_idempotent_json_cycle(
        api_client,
        async_engine,
        path=f"/api/v1/tickets/{rev_tid}/review-resolve",
        headers=admin_hdr,
        payload={"resolution": "APPROVE_REASSIGN", "reason": "同意改派"},
        mismatch_payload={"resolution": "TERMINATE_CANCEL", "reason": "直接终止"},
        expected_status=200,
    )


async def test_concurrent_gather_invariants_without_artificial_delay(
    api_client: AsyncClient,
    async_engine: AsyncEngine,
) -> None:
    """Natural asyncio.gather on POST /tickets preserves §6.5 invariants: +1 ticket, 1 audit key."""
    admin_hdr = await auth_headers_for(api_client, "admin")
    insp_hdr = await auth_headers_for(api_client, "inspector1")
    pts = (await api_client.get("/api/v1/lighting-points", headers=admin_hdr)).json()["items"]
    pt = next(p for p in pts if p["point_code"] == "SL-ZZ-JS-0001")

    shared_key = new_idem_key("gather-nat")
    payload = {
        "lighting_point_id": pt["id"],
        "fault_type": "LAMP_OUT",
        "title": "Natural concurrent idempotency",
        "safety_flag": False,
    }

    async def _post_one() -> tuple[int, str | None]:
        r = await api_client.post(
            "/api/v1/tickets",
            json=payload,
            headers={**insp_hdr, "Idempotency-Key": shared_key},
        )
        return r.status_code, r.headers.get("x-idempotent-replay")

    results = await asyncio.gather(*(_post_one() for _ in range(4)))
    statuses = [st for st, _ in results]
    assert set(statuses).issubset({201, 409})
    assert 201 in statuses

    async with AsyncSession(async_engine, expire_on_commit=False) as s:
        t_cnt = int((await s.scalar(select(func.count(FaultTicket.id)))) or 0)
        a_cnt = int(
            (
                await s.scalar(
                    select(func.count(AuditLog.id)).where(AuditLog.idempotency_key == shared_key)
                )
            )
            or 0
        )
        assert t_cnt == 1
        assert a_cnt == 1


async def test_concurrent_advisory_lock_triggers_409_in_progress_real_pg(
    api_client: AsyncClient,
    async_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two real PG17 connections competing on pg_try_advisory_xact_lock -> 201 + 409."""
    from tomori.vision import client as vision_client_mod

    admin_hdr = await auth_headers_for(api_client, "admin")
    insp_hdr = await auth_headers_for(api_client, "inspector1")
    pts = (await api_client.get("/api/v1/lighting-points", headers=admin_hdr)).json()["items"]
    pt = next(p for p in pts if p["point_code"] == "SL-ZZ-JS-0001")

    real_analyze_image = vision_client_mod.analyze_image
    loop = asyncio.get_running_loop()
    async_lock_held = asyncio.Event()
    release_first = threading.Event()
    call_counter = 0
    counter_lock = threading.Lock()

    def _pausing_analyze_image(path: Path | str, **kwargs: Any) -> Any:
        nonlocal call_counter
        with counter_lock:
            call_counter += 1
            is_first = call_counter == 1
        if is_first:
            loop.call_soon_threadsafe(async_lock_held.set)
            assert release_first.wait(timeout=10.0), "timed out waiting for contender"
        return real_analyze_image(path, **kwargs)

    monkeypatch.setattr(vision_client_mod, "analyze_image", _pausing_analyze_image)

    shared_key = new_idem_key("advisory-409")
    occluded_png = make_occluded_fault_png()

    async def _req1() -> Any:
        return await api_client.post(
            "/api/v1/tickets/ai-candidates",
            data={"lighting_point_id": str(pt["id"]), "scene_lighting_hint": "night"},
            files=[("files", ("occ.png", occluded_png, "image/png"))],
            headers={**insp_hdr, "Idempotency-Key": shared_key},
        )

    async def _req2() -> Any:
        await async_lock_held.wait()
        try:
            return await api_client.post(
                "/api/v1/tickets/ai-candidates",
                data={"lighting_point_id": str(pt["id"]), "scene_lighting_hint": "night"},
                files=[("files", ("occ.png", occluded_png, "image/png"))],
                headers={**insp_hdr, "Idempotency-Key": shared_key},
            )
        finally:
            release_first.set()

    r1, r2 = await asyncio.gather(_req1(), _req2())
    assert r1.status_code == 201, r1.text
    assert r2.status_code == 409, r2.text
    assert r2.json()["error"]["code"] == "IDEMPOTENCY_REQUEST_IN_PROGRESS"

    # Once Req 1 commits & frees the advisory lock, Req 3 with same key & payload replays 201
    r3 = await api_client.post(
        "/api/v1/tickets/ai-candidates",
        data={"lighting_point_id": str(pt["id"]), "scene_lighting_hint": "night"},
        files=[("files", ("occ.png", occluded_png, "image/png"))],
        headers={**insp_hdr, "Idempotency-Key": shared_key},
    )
    assert r3.status_code == 201
    assert r3.headers.get("x-idempotent-replay") == "true"
    assert r3.json()["ticket"]["id"] == r1.json()["ticket"]["id"]

    async with AsyncSession(async_engine, expire_on_commit=False) as s:
        assert int((await s.scalar(select(func.count(FaultTicket.id)))) or 0) == 1
        assert (
            int(
                (
                    await s.scalar(
                        select(func.count(AuditLog.id)).where(
                            AuditLog.idempotency_key == shared_key
                        )
                    )
                )
                or 0
            )
            == 1
        )
