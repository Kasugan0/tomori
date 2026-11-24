"""L2 API ticket creation, duplicate 409 contract, and full 12-step ai-candidates tests."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from tests.integration.conftest import (
    auth_headers_for,
    clear_settings_cache,
    make_animated_bytes,
    make_minimal_png_with_dimensions,
    make_night_normal_lit_png,
    make_occluded_fault_png,
    make_static_image,
    new_idem_key,
)
from tomori.models.audit_log import AuditLog
from tomori.models.fault_evidence import FaultEvidence
from tomori.models.fault_ticket import FaultTicket
from tomori.models.notification_task import NotificationTask


async def _point_by_code(client: AsyncClient, headers: dict[str, str], code: str) -> dict[str, int]:
    rows = (await client.get("/api/v1/lighting-points", headers=headers)).json()["items"]
    pt = next(r for r in rows if r["point_code"] == code)
    return {"id": int(pt["id"]), "region_id": int(pt["region_id"])}


async def test_manual_ticket_create_and_duplicate_409_exact_3_details_keys(
    api_client: AsyncClient,
    async_engine: AsyncEngine,
) -> None:
    """Manual report -> 201 PENDING_DISPATCH + 48h SLA; duplicate -> 409 exact 3-key details."""
    admin_hdr = await auth_headers_for(api_client, "admin")
    insp_hdr = await auth_headers_for(api_client, "inspector1")
    js_pt = await _point_by_code(api_client, admin_hdr, "SL-ZZ-JS-0001")

    r_ok = await api_client.post(
        "/api/v1/tickets",
        json={
            "lighting_point_id": js_pt["id"],
            "fault_type": "LAMP_OUT",
            "title": "花园路 0001 灭灯",
            "description": "巡查人工确认灭灯",
            "safety_flag": False,
        },
        headers={**insp_hdr, "Idempotency-Key": new_idem_key("manual-ok")},
    )
    assert r_ok.status_code == 201
    t_body = r_ok.json()
    assert t_body["report_source"] == "MANUAL_INSPECT"
    assert t_body["status"] == "PENDING_DISPATCH"
    assert t_body["confirmed_by_inspector"] is True
    assert t_body["response_deadline_at"] is None
    assert t_body["repair_deadline_at"] is not None
    assert t_body["grading_score"] is None
    assert "emergency_action_required" in t_body["grading_snapshot_json"]

    # Admin manual ticket on different point -> MANUAL_ADMIN
    js_pt2 = await _point_by_code(api_client, admin_hdr, "SL-ZZ-JS-0002")
    r_adm = await api_client.post(
        "/api/v1/tickets",
        json={
            "lighting_point_id": js_pt2["id"],
            "fault_type": "FIXTURE_DAMAGED",
            "title": "中州大道灯具破损",
            "safety_flag": True,
        },
        headers={**admin_hdr, "Idempotency-Key": new_idem_key("manual-adm")},
    )
    assert r_adm.status_code == 201
    assert r_adm.json()["report_source"] == "MANUAL_ADMIN"
    assert r_adm.json()["priority"] == "P1"

    # Duplicate active ticket on same (lighting_point_id, fault_type) -> 409 + exact 3 details keys
    r_dup = await api_client.post(
        "/api/v1/tickets",
        json={
            "lighting_point_id": js_pt["id"],
            "fault_type": "LAMP_OUT",
            "title": "重复人工报障",
            "safety_flag": False,
        },
        headers={**insp_hdr, "Idempotency-Key": new_idem_key("manual-dup")},
    )
    assert r_dup.status_code == 409
    err = r_dup.json()["error"]
    assert err["code"] == "DUPLICATE_ACTIVE_TICKET"
    assert set(err["details"].keys()) == {
        "existing_ticket_id",
        "existing_ticket_no",
        "existing_status",
    }
    assert err["details"]["existing_ticket_id"] == t_body["id"]
    assert err["details"]["existing_ticket_no"] == t_body["ticket_no"]
    assert err["details"]["existing_status"] == "PENDING_DISPATCH"

    # Verify DB rows & audit/notification side effects
    async with AsyncSession(async_engine, expire_on_commit=False) as s:
        actions = set(
            (
                await s.scalars(select(AuditLog.action).where(AuditLog.ticket_id == t_body["id"]))
            ).all()
        )
        assert {"TICKET_CREATED", "TICKET_GRADED"}.issubset(actions)
        notif_cnt = len(
            (
                await s.scalars(
                    select(NotificationTask).where(
                        NotificationTask.related_ticket_id == t_body["id"]
                    )
                )
            ).all()
        )
        assert notif_cnt >= 1


async def test_manual_ticket_cross_region_inspector_must_return_403_region_scope(
    api_client: AsyncClient,
) -> None:
    """Layer-4 region check: inspector1 lacks INSPECT on ZZ-ZD-01 -> must return 403."""
    admin_hdr = await auth_headers_for(api_client, "admin")
    insp_hdr = await auth_headers_for(api_client, "inspector1")
    zd_pt = await _point_by_code(api_client, admin_hdr, "SL-ZZ-ZD-0001")

    r_cross = await api_client.post(
        "/api/v1/tickets",
        json={
            "lighting_point_id": zd_pt["id"],
            "fault_type": "LAMP_OUT",
            "title": "跨区无授权人工报障",
            "safety_flag": False,
        },
        headers={**insp_hdr, "Idempotency-Key": new_idem_key("manual-cross")},
    )
    assert r_cross.status_code == 403
    assert r_cross.json()["error"]["code"] == "FORBIDDEN_REGION_SCOPE"


async def test_ai_candidates_steps_1_to_6_media_order_and_errors(
    api_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify docs/API.md §5 steps 1-6 media validation, priority order, and 503 VISION_DISABLED."""
    admin_hdr = await auth_headers_for(api_client, "admin")
    insp_hdr = await auth_headers_for(api_client, "inspector1")
    js_pt = await _point_by_code(api_client, admin_hdr, "SL-ZZ-JS-0001")
    zd_pt = await _point_by_code(api_client, admin_hdr, "SL-ZZ-ZD-0001")

    # Step 1: out-of-region INSPECTOR with valid static image -> 403 FORBIDDEN_REGION_SCOPE
    ok_jpg = make_static_image("JPEG")
    r_scope = await api_client.post(
        "/api/v1/tickets/ai-candidates",
        data={"lighting_point_id": str(zd_pt["id"]), "scene_lighting_hint": "night"},
        files=[("files", ("lamp.jpg", ok_jpg, "image/jpeg"))],
        headers={**insp_hdr, "Idempotency-Key": new_idem_key("ai-scope")},
    )
    assert r_scope.status_code == 403
    assert r_scope.json()["error"]["code"] == "FORBIDDEN_REGION_SCOPE"

    # Step 2a: 0 files -> 422 NO_FILES
    r_zero = await api_client.post(
        "/api/v1/tickets/ai-candidates",
        data={"lighting_point_id": str(js_pt["id"])},
        headers={**insp_hdr, "Idempotency-Key": new_idem_key("ai-nofiles")},
    )
    assert r_zero.status_code == 422
    assert r_zero.json()["error"]["code"] == "NO_FILES"

    # Step 2b: 11 files -> 422 TOO_MANY_FILES
    eleven_files = [
        (
            "files",
            (f"img_{i}.png", make_static_image("PNG", bg_color=(i * 15, 20, 30)), "image/png"),
        )
        for i in range(11)
    ]
    r_many = await api_client.post(
        "/api/v1/tickets/ai-candidates",
        data={"lighting_point_id": str(js_pt["id"])},
        files=eleven_files,
        headers={**insp_hdr, "Idempotency-Key": new_idem_key("ai-many")},
    )
    assert r_many.status_code == 422
    assert r_many.json()["error"]["code"] == "TOO_MANY_FILES"

    # Step 3 (priority ①): >15 MiB even with MP4 header -> 413 IMAGE_SIZE_EXCEEDED
    oversized_mp4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * (15 * 1024 * 1024 + 1 - 12)
    r_big = await api_client.post(
        "/api/v1/tickets/ai-candidates",
        data={"lighting_point_id": str(js_pt["id"])},
        files=[("files", ("huge.jpg", oversized_mp4, "image/jpeg"))],
        headers={**insp_hdr, "Idempotency-Key": new_idem_key("ai-413")},
    )
    assert r_big.status_code == 413
    assert r_big.json()["error"]["code"] == "IMAGE_SIZE_EXCEEDED"

    # Step 4 / 5a: GIF & APNG & animated WebP -> 415 UNSUPPORTED_IMAGE_FORMAT
    for fname, payload_bytes, mime in (
        ("anim.gif", make_animated_bytes("GIF"), "image/gif"),
        ("anim.png", make_animated_bytes("PNG"), "image/png"),
        ("anim.webp", make_animated_bytes("WEBP"), "image/webp"),
    ):
        r_anim = await api_client.post(
            "/api/v1/tickets/ai-candidates",
            data={"lighting_point_id": str(js_pt["id"])},
            files=[("files", (fname, payload_bytes, mime))],
            headers={**insp_hdr, "Idempotency-Key": new_idem_key("ai-415a")},
        )
        assert r_anim.status_code == 415, f"{fname}: {r_anim.text}"
        assert r_anim.json()["error"]["code"] == "UNSUPPORTED_IMAGE_FORMAT"

    # Step 5b / 5c: video container disguised as .jpg + stream -> 415 MEDIA_VIDEO_STREAM_FORBIDDEN
    mp4_bytes = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"
    r_vid = await api_client.post(
        "/api/v1/tickets/ai-candidates",
        data={"lighting_point_id": str(js_pt["id"])},
        files=[("files", ("fake.jpg", mp4_bytes, "image/jpeg"))],
        headers={**insp_hdr, "Idempotency-Key": new_idem_key("ai-415v")},
    )
    assert r_vid.status_code == 415
    assert r_vid.json()["error"]["code"] == "MEDIA_VIDEO_STREAM_FORBIDDEN"

    r_stream = await api_client.post(
        "/api/v1/tickets/ai-candidates",
        data={
            "lighting_point_id": str(js_pt["id"]),
            "stream_url": "rtsp://10.0.0.1/live",
        },
        files=[("files", ("lamp.jpg", ok_jpg, "image/jpeg"))],
        headers={**insp_hdr, "Idempotency-Key": new_idem_key("ai-415s")},
    )
    assert r_stream.status_code == 415
    assert r_stream.json()["error"]["code"] == "MEDIA_VIDEO_STREAM_FORBIDDEN"

    # Step 6: > 40,000,000 pixels -> 422 IMAGE_PIXEL_LIMIT_EXCEEDED
    bomb_png = make_minimal_png_with_dimensions(8001, 5000)
    r_px = await api_client.post(
        "/api/v1/tickets/ai-candidates",
        data={"lighting_point_id": str(js_pt["id"])},
        files=[("files", ("bomb.png", bomb_png, "image/png"))],
        headers={**insp_hdr, "Idempotency-Key": new_idem_key("ai-422px")},
    )
    assert r_px.status_code == 422
    assert r_px.json()["error"]["code"] == "IMAGE_PIXEL_LIMIT_EXCEEDED"

    # Step 9 fallback: YOLO_INFERENCE_ENABLED=false -> 503 VISION_DISABLED (no evidences/tickets)
    monkeypatch.setenv("YOLO_INFERENCE_ENABLED", "false")
    clear_settings_cache()
    r_503 = await api_client.post(
        "/api/v1/tickets/ai-candidates",
        data={"lighting_point_id": str(js_pt["id"])},
        files=[("files", ("lamp.jpg", ok_jpg, "image/jpeg"))],
        headers={**insp_hdr, "Idempotency-Key": new_idem_key("ai-503")},
    )
    assert r_503.status_code == 503
    err_503 = r_503.json()["error"]
    assert err_503["code"] == "VISION_DISABLED"
    assert err_503["message_key"]
    assert err_503["details"]["fallback"]["action"] == "MANUAL_REPORT"
    assert err_503["details"]["fallback"]["resubmit_required"] is True
    assert "evidence_ids" not in err_503["details"]

    # Manual fallback POST /tickets MUST succeed 201 even while YOLO_INFERENCE_ENABLED=false (D1)
    r_fallback_manual = await api_client.post(
        "/api/v1/tickets",
        data={
            "lighting_point_id": str(js_pt["id"]),
            "fault_type": "LAMP_OUT",
            "title": "视觉降级后人工兜底提交",
            "safety_flag": "false",
        },
        files=[("files", ("lamp.jpg", ok_jpg, "image/jpeg"))],
        headers={**insp_hdr, "Idempotency-Key": new_idem_key("manual-fb")},
    )
    assert r_fallback_manual.status_code == 201
    assert r_fallback_manual.json()["status"] == "PENDING_DISPATCH"


async def test_ai_candidates_step10_zero_fault_d30_orphan_and_step11_draft_ticket(
    api_client: AsyncClient,
    async_engine: AsyncEngine,
) -> None:
    """Step 10 (D-30 NORMAL_LIT -> 200 ticket=null, ticket_id=NULL) & Step 11 (201 draft)."""
    admin_hdr = await auth_headers_for(api_client, "admin")
    insp_hdr = await auth_headers_for(api_client, "inspector1")
    js_pt = await _point_by_code(api_client, admin_hdr, "SL-ZZ-JS-0001")

    # Step 10: night normal lit -> D-30 returns NORMAL_LIT (is_fault_candidate=False), no ticket
    normal_lit_png = make_night_normal_lit_png()
    r_step10 = await api_client.post(
        "/api/v1/tickets/ai-candidates",
        data={"lighting_point_id": str(js_pt["id"]), "scene_lighting_hint": "night"},
        files=[("files", ("normal_night.png", normal_lit_png, "image/png"))],
        headers={**insp_hdr, "Idempotency-Key": new_idem_key("ai-step10")},
    )
    assert r_step10.status_code == 200, r_step10.text
    b10 = r_step10.json()
    assert b10["ticket"] is None
    assert len(b10["evidences"]) == 1
    ev10 = b10["evidences"][0]
    assert ev10["ticket_id"] is None
    cands10 = ev10["candidates"]
    assert len(cands10) >= 1
    assert all(c["is_fault_candidate"] is False for c in cands10)
    assert any(
        c["raw_class"] == "streetlight_on" and c["visual_signal_state"] == "NORMAL_LIT"
        for c in cands10
    )

    async with AsyncSession(async_engine, expire_on_commit=False) as s:
        assert len((await s.scalars(select(FaultTicket))).all()) == 0
        db_ev = await s.scalar(select(FaultEvidence).where(FaultEvidence.id == ev10["id"]))
        assert db_ev is not None
        assert db_ev.ticket_id is None
        assert db_ev.evidence_type == "INSPECTION_PHOTO"
        assert db_ev.analysis_id is not None

    # Step 11: occluded foliage fault -> 201 AI_CANDIDATE_PENDING + evidence + binary stream
    occluded_png = make_occluded_fault_png()
    r_step11 = await api_client.post(
        "/api/v1/tickets/ai-candidates",
        data={"lighting_point_id": str(js_pt["id"]), "scene_lighting_hint": "night"},
        files=[("files", ("occluded.png", occluded_png, "image/png"))],
        headers={**insp_hdr, "Idempotency-Key": new_idem_key("ai-step11")},
    )
    assert r_step11.status_code == 201, r_step11.text
    b11 = r_step11.json()
    draft = b11["ticket"]
    assert draft is not None
    assert draft["status"] == "AI_CANDIDATE_PENDING"
    assert draft["confirmed_by_inspector"] is False
    assert draft["priority"] is None
    assert draft["repair_deadline_at"] is None
    assert draft["fault_type"] == "OCCLUDED"
    assert len(b11["evidences"]) == 1
    ev11 = b11["evidences"][0]
    assert ev11["ticket_id"] == draft["id"]
    assert uuid.UUID(str(ev11["analysis_id"]))

    # Binary streams for raw and annotated images must work with Bearer auth
    r_raw = await api_client.get(f"/api/v1/evidence/{ev11['id']}/raw", headers=insp_hdr)
    assert r_raw.status_code == 200
    assert r_raw.headers["content-type"].startswith("image/")

    r_ann = await api_client.get(f"/api/v1/evidence/{ev11['id']}/annotated", headers=insp_hdr)
    assert r_ann.status_code == 200
    assert r_ann.headers["content-type"].startswith("image/")

    # Confirm draft -> PENDING_DISPATCH + 48h SLA
    r_conf = await api_client.post(
        f"/api/v1/tickets/{draft['id']}/confirm",
        json={"safety_flag": False},
        headers={**insp_hdr, "Idempotency-Key": new_idem_key("ai-conf")},
    )
    assert r_conf.status_code == 200
    confirmed = r_conf.json()
    assert confirmed["status"] == "PENDING_DISPATCH"
    assert confirmed["confirmed_by_inspector"] is True
    assert confirmed["response_deadline_at"] is None
    assert confirmed["repair_deadline_at"] is not None

    # Upload supplementary evidence via POST /tickets/{id}/evidences (task-92 regression)
    supp_jpg = make_static_image("JPEG", bg_color=(12, 88, 144))
    r_up = await api_client.post(
        f"/api/v1/tickets/{draft['id']}/evidences",
        data={"evidence_type": "INSPECTION_PHOTO"},
        files=[("files", ("supp.jpg", supp_jpg, "image/jpeg"))],
        headers=insp_hdr,
    )
    assert r_up.status_code in {200, 201}, r_up.text
    up_items = r_up.json()
    assert len(up_items) == 1
    assert up_items[0]["ticket_id"] == draft["id"]
    assert up_items[0]["evidence_type"] == "INSPECTION_PHOTO"
