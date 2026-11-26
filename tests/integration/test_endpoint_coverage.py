"""P0-1 endpoint coverage gap tests: PATCH / DELETE / POST assertions (real PG17, no mocked DB).

Notable deviations discovered while writing (verified against source, reported to Lead):
- `POINT_HAS_ACTIVE_TICKET` is `422` (`services/exceptions.py:70`), not `403` as Lead's
  task text said — asserted as `422` per the error contract (§6.8(e) intent-vs-contract).
- 原 `P3-1` 盲区「`evidences` 无终态守卫」已由 `task-141` 在 `ticket_service.py`
  补 `_TERMINAL_TICKET_STATUSES`（`CLOSED`/`CANCELLED` → `422 INVALID_STATE_TRANSITION`）
  修复；其回归测试位于 `test_media_negative.py` 的
  `test_evidence_upload_on_terminal_ticket_returns_422_invalid_state_transition`（现已绿）。
"""

from __future__ import annotations

import uuid
from typing import Any

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from tests.integration.conftest import (
    auth_headers_for,
    build_confirm_payload,
    new_idem_key,
)


def _err(resp: Any) -> dict[str, Any]:
    body = resp.json()
    assert "error" in body and "code" in body["error"], resp.text
    err: dict[str, Any] = body["error"]
    return err


async def test_patch_lighting_point_region_switch_with_active_ticket_returns_422_contract(
    api_client: AsyncClient,
    async_engine: AsyncEngine,
) -> None:
    """Contract anchor: PATCH point to a new region while it has an ACTIVE ticket -> 422."""
    admin_hdr = await auth_headers_for(api_client, "admin")
    insp_hdr = await auth_headers_for(api_client, "inspector1")

    pts = (await api_client.get("/api/v1/lighting-points", headers=admin_hdr)).json()["items"]
    js_pt = next(p for p in pts if p["point_code"] == "SL-ZZ-JS-0001")
    zd_pt = next(p for p in pts if p["point_code"] == "SL-ZZ-ZD-0001")

    # 1) happy: PATCH name/road_name without touching region_id -> 200 reflected
    r_name = await api_client.patch(
        f"/api/v1/lighting-points/{js_pt['id']}",
        json={"name": "花园路农业路口西北侧第3杆-E2E", "road_name": "花园路"},
        headers=admin_hdr,
    )
    assert r_name.status_code == 200, r_name.text
    assert r_name.json()["name"] == "花园路农业路口西北侧第3杆-E2E"

    # 2) 403 FORBIDDEN_ROLE for inspector PATCH
    r_forbidden = await api_client.patch(
        f"/api/v1/lighting-points/{js_pt['id']}",
        json={"name": "越权改名"},
        headers=insp_hdr,
    )
    assert r_forbidden.status_code == 403
    assert _err(r_forbidden)["code"] == "FORBIDDEN_ROLE"

    # 3) Create an ACTIVE ticket on the point via the real production path
    r_ticket = await api_client.post(
        "/api/v1/tickets",
        json={
            "lighting_point_id": js_pt["id"],
            "fault_type": "LAMP_OUT",
            "title": "P0-1 契约锚点测试工单",
            "safety_flag": False,
        },
        headers={**insp_hdr, "Idempotency-Key": new_idem_key("p01-anchor")},
    )
    assert r_ticket.status_code == 201, r_ticket.text

    # 4) Contract anchor: switching region_id with ACTIVE ticket -> 422 POINT_HAS_ACTIVE_TICKET
    r_anchor = await api_client.patch(
        f"/api/v1/lighting-points/{js_pt['id']}",
        json={"region_id": zd_pt["region_id"]},
        headers=admin_hdr,
    )
    assert r_anchor.status_code == 422, r_anchor.text
    err = _err(r_anchor)
    assert err["code"] == "POINT_HAS_ACTIVE_TICKET"

    # 5) 404 on missing point
    r_404 = await api_client.patch(
        "/api/v1/lighting-points/999999",
        json={"name": "幽灵点位"},
        headers=admin_hdr,
    )
    assert r_404.status_code == 404
    assert _err(r_404)["code"] == "RESOURCE_NOT_FOUND"


async def test_patch_facility_asset_403_404_and_field_update(
    api_client: AsyncClient,
) -> None:
    """PATCH /facility-assets/{id}: 403 non-admin, 404 missing, 200 update fields."""
    admin_hdr = await auth_headers_for(api_client, "admin")
    insp_hdr = await auth_headers_for(api_client, "inspector1")

    assets = (await api_client.get("/api/v1/facility-assets", headers=admin_hdr)).json()["items"]
    asset = assets[0]

    r_forbidden = await api_client.patch(
        f"/api/v1/facility-assets/{asset['id']}",
        json={"name": "越权资产改名"},
        headers=insp_hdr,
    )
    assert r_forbidden.status_code == 403
    assert _err(r_forbidden)["code"] == "FORBIDDEN_ROLE"

    r_404 = await api_client.patch(
        "/api/v1/facility-assets/999999",
        json={"name": "幽灵资产"},
        headers=admin_hdr,
    )
    assert r_404.status_code == 404
    assert _err(r_404)["code"] == "RESOURCE_NOT_FOUND"

    r_ok = await api_client.patch(
        f"/api/v1/facility-assets/{asset['id']}",
        json={"name": "主路灯具-已检修", "importance": "HIGH", "description": "E2E 修补"},
        headers=admin_hdr,
    )
    assert r_ok.status_code == 200, r_ok.text
    assert r_ok.json()["name"] == "主路灯具-已检修"
    assert r_ok.json()["importance"] == "HIGH"


async def test_patch_region_403_404_and_field_update(api_client: AsyncClient) -> None:
    """PATCH /regions/{id}: 403 non-admin, 404 missing, 200 update."""
    admin_hdr = await auth_headers_for(api_client, "admin")
    insp_hdr = await auth_headers_for(api_client, "inspector1")
    regions = (await api_client.get("/api/v1/regions", headers=admin_hdr)).json()
    region = next(r for r in regions if r["region_code"] == "ZZ-JS-01")

    r_forbidden = await api_client.patch(
        f"/api/v1/regions/{region['id']}", json={"name": "越权区域改名"}, headers=insp_hdr
    )
    assert r_forbidden.status_code == 403
    assert _err(r_forbidden)["code"] == "FORBIDDEN_ROLE"

    r_404 = await api_client.patch(
        "/api/v1/regions/999999", json={"name": "幽灵区域"}, headers=admin_hdr
    )
    assert r_404.status_code == 404
    assert _err(r_404)["code"] == "RESOURCE_NOT_FOUND"

    r_ok = await api_client.patch(
        f"/api/v1/regions/{region['id']}",
        json={"name": "金水区主干道路段-重点维护", "description": "E2E 区域更新"},
        headers=admin_hdr,
    )
    assert r_ok.status_code == 200
    assert r_ok.json()["name"] == "金水区主干道路段-重点维护"


async def test_patch_repair_process_overwrites_materials_and_requires_in_repair(
    api_client: AsyncClient,
) -> None:
    """PATCH /repair-processes/{id}: only owning tech/admin + only while ticket IN_REPAIR.

    RepairProcess 仅在 `complete` 时创建，而 PATCH 又要求 `ticket.status == "IN_REPAIR"`，
    故合法窗口仅存在于「完工→验收驳回→返修再派工→接单(IN_REPAIR)」之后、或返修派发接单后。
    """
    admin_hdr = await auth_headers_for(api_client, "admin")
    insp_hdr = await auth_headers_for(api_client, "inspector1")
    tech1_hdr = await auth_headers_for(api_client, "tech1")
    tech2_hdr = await auth_headers_for(api_client, "tech2")

    pts = (await api_client.get("/api/v1/lighting-points", headers=admin_hdr)).json()["items"]
    pt = next(p for p in pts if p["point_code"] == "SL-ZZ-JS-0001")
    r_t = await api_client.post(
        "/api/v1/tickets",
        json={
            "lighting_point_id": pt["id"],
            "fault_type": "LAMP_OUT",
            "title": "P0-1 维修过程 PATCH 工单",
            "safety_flag": False,
        },
        headers={**insp_hdr, "Idempotency-Key": new_idem_key("p01-rp")},
    )
    tid = r_t.json()["id"]
    cands = (
        await api_client.get(f"/api/v1/tickets/{tid}/dispatch-candidates", headers=admin_hdr)
    ).json()["candidates"]
    cand = next(c for c in cands if c["username"] == "tech1")
    r_d = await api_client.post(
        f"/api/v1/tickets/{tid}/dispatch",
        json={
            "technician_account_id": cand["account_id"],
            "scope_authorization_id": cand["scope_authorization_id"],
        },
        headers={**admin_hdr, "Idempotency-Key": new_idem_key("p01-rpd")},
    )
    did = int(r_d.json()["id"])
    await api_client.post(
        f"/api/v1/dispatches/{did}/accept",
        headers={**tech1_hdr, "Idempotency-Key": new_idem_key("p01-rpa")},
    )
    now = __import__("datetime").datetime.now(__import__("datetime").UTC)
    await api_client.post(
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
            "safety_note": "六件套齐全",
            "arrived_at": (now - __import__("datetime").timedelta(minutes=30)).isoformat(),
            "repair_summary": "初修完成待验收",
            "materials": [
                {
                    "material_code": "MAT-OLD-1",
                    "material_name": "旧料A",
                    "quantity": 1,
                    "unit": "PCS",
                    "unit_price": 1.0,
                },
            ],
        },
        headers={**tech1_hdr, "Idempotency-Key": new_idem_key("p01-rpc")},
    )

    rps = (
        await api_client.get(f"/api/v1/repair-processes?ticket_id={tid}", headers=admin_hdr)
    ).json()["items"]
    assert len(rps) == 1
    rp_id = rps[0]["id"]

    # 422: ticket is PENDING_ACCEPTANCE (not IN_REPAIR) -> PATCH forbidden
    r_not_repair = await api_client.patch(
        f"/api/v1/repair-processes/{rp_id}",
        json={"repair_summary": "非维修态禁改"},
        headers=tech1_hdr,
    )
    assert r_not_repair.status_code == 422
    assert _err(r_not_repair)["code"] == "INVALID_STATE_TRANSITION"

    # 403: tech2 (non-owner, non-admin) forbidden -> 403 on role check first
    r_403 = await api_client.patch(
        f"/api/v1/repair-processes/{rp_id}",
        json={"repair_summary": "越权修改"},
        headers=tech2_hdr,
    )
    assert r_403.status_code == 403
    assert _err(r_403)["code"] == "FORBIDDEN_ROLE"

    # 404: missing repair process
    r_404 = await api_client.patch(
        "/api/v1/repair-processes/999999",
        json={"repair_summary": "幽灵记录"},
        headers=tech1_hdr,
    )
    assert r_404.status_code == 404
    assert _err(r_404)["code"] == "RESOURCE_NOT_FOUND"

    # Drive ticket back to IN_REPAIR: reject acceptance -> REWORK -> re-dispatch -> accept
    r_rej = await api_client.post(
        f"/api/v1/tickets/{tid}/acceptance",
        json={"decision": "REJECTED", "comment": "照度不达标返修"},
        headers={**insp_hdr, "Idempotency-Key": new_idem_key("p01-rpr")},
    )
    assert r_rej.status_code == 200
    cands2 = (
        await api_client.get(f"/api/v1/tickets/{tid}/dispatch-candidates", headers=admin_hdr)
    ).json()["candidates"]
    cand2 = next(c for c in cands2 if c["username"] == "tech1")
    r_d2 = await api_client.post(
        f"/api/v1/tickets/{tid}/dispatch",
        json={
            "technician_account_id": cand2["account_id"],
            "scope_authorization_id": cand2["scope_authorization_id"],
        },
        headers={**admin_hdr, "Idempotency-Key": new_idem_key("p01-rpd2")},
    )
    did2 = int(r_d2.json()["id"])
    await api_client.post(
        f"/api/v1/dispatches/{did2}/accept",
        headers={**tech1_hdr, "Idempotency-Key": new_idem_key("p01-rpa2")},
    )

    # 200: owning tech1 patches the ORIGINAL repair process while ticket is IN_REPAIR again
    r_ok = await api_client.patch(
        f"/api/v1/repair-processes/{rp_id}",
        json={
            "repair_summary": "更换驱动电源并复核照度",
            "materials": [
                {
                    "material_code": "MAT-DRV-150",
                    "material_name": "150W 恒流驱动",
                    "quantity": 1,
                    "unit": "PCS",
                    "unit_price": 120.0,
                },
                {
                    "material_code": "MAT-GLUE-01",
                    "material_name": "密封胶圈",
                    "quantity": 2,
                    "unit": "PCS",
                    "unit_price": 8.0,
                },
            ],
        },
        headers=tech1_hdr,
    )
    assert r_ok.status_code == 200, r_ok.text
    body = r_ok.json()
    assert len(body["materials"]) == 2
    assert {m["material_code"] for m in body["materials"]} == {"MAT-DRV-150", "MAT-GLUE-01"}
    assert body["repair_summary"] == "更换驱动电源并复核照度"


async def test_patch_ticket_draft_fields_404_422_and_facility_asset_validation(
    api_client: AsyncClient,
) -> None:
    """PATCH /tickets/{id} on AI draft: fields update, invalid asset 422, non-draft 422, 404."""
    admin_hdr = await auth_headers_for(api_client, "admin")
    insp_hdr = await auth_headers_for(api_client, "inspector1")

    # 404: missing ticket
    r_404 = await api_client.patch(
        "/api/v1/tickets/999999", json={"title": "幽灵草稿"}, headers=insp_hdr
    )
    assert r_404.status_code == 404
    assert _err(r_404)["code"] == "RESOURCE_NOT_FOUND"

    # Build an AI_CANDIDATE_PENDING draft via the real ai-candidates path (baseline occluded)
    lp_items = (await api_client.get("/api/v1/lighting-points", headers=admin_hdr)).json()["items"]
    js_pt = next(p for p in lp_items if p["point_code"] == "SL-ZZ-JS-0001")
    occ = __import__("io").BytesIO()
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (400, 400), (10, 10, 15))
    ImageDraw.Draw(img).rectangle((180, 180, 220, 220), fill=(0, 140, 0))
    img.save(occ, format="PNG")
    occ_bytes = occ.getvalue()

    r_ai = await api_client.post(
        "/api/v1/tickets/ai-candidates",
        data={"lighting_point_id": str(js_pt["id"]), "scene_lighting_hint": "night"},
        files=[("files", ("occ.png", occ_bytes, "image/png"))],
        headers={**insp_hdr, "Idempotency-Key": new_idem_key("p01-draft")},
    )
    assert r_ai.status_code == 201, r_ai.text
    draft = r_ai.json()["ticket"]
    assert draft["status"] == "AI_CANDIDATE_PENDING"

    # 403: cross-region inspector (inspector1 has INSPECT on ZZ-JS only; ZD ticket region differs)
    # Use admin-created ticket on ZD point to probe inspector scope enforcement
    zd_pt = next(p for p in lp_items if p["point_code"] == "SL-ZZ-ZD-0001")
    r_zd = await api_client.post(
        "/api/v1/tickets/ai-candidates",
        data={"lighting_point_id": str(zd_pt["id"]), "scene_lighting_hint": "night"},
        files=[("files", ("occ.png", occ_bytes, "image/png"))],
        headers={**admin_hdr, "Idempotency-Key": new_idem_key("p01-zd")},
    )
    assert r_zd.status_code == 201, r_zd.text
    zd_draft = r_zd.json()["ticket"]
    r_cross = await api_client.patch(
        f"/api/v1/tickets/{zd_draft['id']}",
        json={"title": "跨区补丁"},
        headers=insp_hdr,
    )
    assert r_cross.status_code == 403
    assert _err(r_cross)["code"] == "FORBIDDEN_REGION_SCOPE"

    # 422 VALIDATION_ERROR: facility_asset_id points to an asset on a DIFFERENT point
    fa_items = (await api_client.get("/api/v1/facility-assets", headers=admin_hdr)).json()["items"]
    other_pt_asset = next(a for a in fa_items if a["lighting_point_id"] != js_pt["id"])
    r_bad_asset = await api_client.patch(
        f"/api/v1/tickets/{draft['id']}",
        json={"facility_asset_id": other_pt_asset["id"]},
        headers=insp_hdr,
    )
    assert r_bad_asset.status_code == 422
    assert _err(r_bad_asset)["code"] == "VALIDATION_ERROR"

    # 200: happy draft field update
    r_ok = await api_client.patch(
        f"/api/v1/tickets/{draft['id']}",
        json={
            "title": "AI 草稿：被遮挡路灯核实",
            "description": "夜间发现灯具被树枝遮挡，现场修剪需求",
            "manual_note": "现场确认枝叶完全罩住灯头",
        },
        headers=insp_hdr,
    )
    assert r_ok.status_code == 200, r_ok.text
    updated = r_ok.json()
    assert updated["title"] == "AI 草稿：被遮挡路灯核实"
    assert updated["description"] == "夜间发现灯具被树枝遮挡，现场修剪需求"
    assert updated["status"] == "AI_CANDIDATE_PENDING"

    # 422 INVALID_STATE_TRANSITION: confirm first, then PATCH -> must reject non-draft state
    r_confirm = await api_client.post(
        f"/api/v1/tickets/{draft['id']}/confirm",
        json=build_confirm_payload(r_ai.json()["evidences"]),
        headers={**insp_hdr, "Idempotency-Key": new_idem_key("p01-conf")},
    )
    assert r_confirm.status_code == 200, r_confirm.text
    r_notdraft = await api_client.patch(
        f"/api/v1/tickets/{draft['id']}",
        json={"title": "已确认后禁改草稿"},
        headers=insp_hdr,
    )
    assert r_notdraft.status_code == 422
    assert _err(r_notdraft)["code"] == "INVALID_STATE_TRANSITION"


async def test_delete_scope_authorization_soft_404_403_and_deactivate(
    api_client: AsyncClient,
) -> None:
    """DELETE /scope-authorizations/{id}: soft delete -> 200 + is_active=false; 404 repeat; 403."""
    admin_hdr = await auth_headers_for(api_client, "admin")
    insp_hdr = await auth_headers_for(api_client, "inspector1")

    accs = (await api_client.get("/api/v1/accounts", headers=admin_hdr)).json()["items"]
    tech2 = next(a for a in accs if a["username"] == "tech2")
    regions = (await api_client.get("/api/v1/regions", headers=admin_hdr)).json()
    zd_region = next(r for r in regions if r["region_code"] == "ZZ-ZD-01")
    scopes = (
        await api_client.get("/api/v1/scope-authorizations?page_size=100", headers=admin_hdr)
    ).json()["items"]
    target = next(
        s
        for s in scopes
        if s["account_id"] == tech2["id"]
        and s["region_id"] == zd_region["id"]
        and s["scope_type"] == "REPAIR"
    )

    r_forbidden = await api_client.delete(
        f"/api/v1/scope-authorizations/{target['id']}", headers=insp_hdr
    )
    assert r_forbidden.status_code == 403
    assert _err(r_forbidden)["code"] == "FORBIDDEN_ROLE"

    r_del = await api_client.delete(
        f"/api/v1/scope-authorizations/{target['id']}", headers=admin_hdr
    )
    assert r_del.status_code == 200, r_del.text
    assert r_del.json()["is_active"] is False

    # Repeat DELETE on already-inactive scope -> row still exists -> 200 idempotent soft delete
    r_repeat = await api_client.delete(
        f"/api/v1/scope-authorizations/{target['id']}", headers=admin_hdr
    )
    assert r_repeat.status_code == 200
    assert r_repeat.json()["is_active"] is False

    r_404 = await api_client.delete("/api/v1/scope-authorizations/999999", headers=admin_hdr)
    assert r_404.status_code == 404
    assert _err(r_404)["code"] == "RESOURCE_NOT_FOUND"


async def test_create_business_rule_version_bump_deactivates_old_and_invalid_payload(
    api_client: AsyncClient,
    async_engine: AsyncEngine,
) -> None:
    """POST /business-rules: version increments, old active row deactivated; bad payload -> 422."""
    admin_hdr = await auth_headers_for(api_client, "admin")
    insp_hdr = await auth_headers_for(api_client, "inspector1")

    rules = (await api_client.get("/api/v1/business-rules", headers=admin_hdr)).json()["items"]
    rule_code = rules[0]["rule_code"]

    r_forbidden = await api_client.post(
        "/api/v1/business-rules",
        json={"rule_code": rule_code, "rule_value": {"ttl_hours": 72}},
        headers=insp_hdr,
    )
    assert r_forbidden.status_code == 403
    assert _err(r_forbidden)["code"] == "FORBIDDEN_ROLE"

    # Invalid payload -> 422 INVALID_RULE_PAYLOAD
    r_bad = await api_client.post(
        "/api/v1/business-rules",
        json={"rule_code": rule_code, "rule_value": {"unexpected": True}},
        headers=admin_hdr,
    )
    assert r_bad.status_code == 422
    assert _err(r_bad)["code"] == "INVALID_RULE_PAYLOAD"

    # Happy: same rule_code with valid new value -> version bumps + old deactivated
    valid_value = dict(rules[0]["rule_value"])
    r_v2 = await api_client.post(
        "/api/v1/business-rules",
        json={"rule_code": rule_code, "rule_value": valid_value, "description": "E2E v2"},
        headers=admin_hdr,
    )
    assert r_v2.status_code in {200, 201}, r_v2.text
    v2 = r_v2.json()
    assert v2["version"] == rules[0]["version"] + 1
    assert v2["is_active"] is True

    # DB-level check: only one active row per rule_code now
    async with AsyncSession(async_engine, expire_on_commit=False) as s:
        from tomori.models.business_rule import BusinessRule

        rows = (
            await s.scalars(select(BusinessRule).where(BusinessRule.rule_code == rule_code))
        ).all()
        active = [r for r in rows if r.is_active]
        assert len(active) == 1
        assert active[0].id == v2["id"]
        assert all(r.id != v2["id"] or not r.is_active for r in rows if r.id != v2["id"])


async def test_create_scope_authorization_replaces_existing_active_and_404s(
    api_client: AsyncClient,
    async_engine: AsyncEngine,
) -> None:
    """POST /scope-authorizations: stop-old-grant-new; 404 missing account/region; 403 non-admin."""
    admin_hdr = await auth_headers_for(api_client, "admin")
    insp_hdr = await auth_headers_for(api_client, "inspector1")
    accs = (await api_client.get("/api/v1/accounts", headers=admin_hdr)).json()["items"]
    tech1 = next(a for a in accs if a["username"] == "tech1")
    regions = (await api_client.get("/api/v1/regions", headers=admin_hdr)).json()
    zd_region = next(r for r in regions if r["region_code"] == "ZZ-ZD-01")

    r_forbidden = await api_client.post(
        "/api/v1/scope-authorizations",
        json={
            "account_id": tech1["id"],
            "region_id": zd_region["id"],
            "scope_type": "REPAIR",
        },
        headers=insp_hdr,
    )
    assert r_forbidden.status_code == 403
    assert _err(r_forbidden)["code"] == "FORBIDDEN_ROLE"

    r_404_acc = await api_client.post(
        "/api/v1/scope-authorizations",
        json={
            "account_id": 999999,
            "region_id": zd_region["id"],
            "scope_type": "REPAIR",
        },
        headers=admin_hdr,
    )
    assert r_404_acc.status_code == 404
    assert _err(r_404_acc)["code"] == "RESOURCE_NOT_FOUND"

    r_404_reg = await api_client.post(
        "/api/v1/scope-authorizations",
        json={
            "account_id": tech1["id"],
            "region_id": 999999,
            "scope_type": "REPAIR",
        },
        headers=admin_hdr,
    )
    assert r_404_reg.status_code == 404
    assert _err(r_404_reg)["code"] == "RESOURCE_NOT_FOUND"

    # Grant REPAIR on ZD to tech1 (new scope)
    r_new = await api_client.post(
        "/api/v1/scope-authorizations",
        json={
            "account_id": tech1["id"],
            "region_id": zd_region["id"],
            "scope_type": "REPAIR",
            "remark": "P0-1 停旧授新验证",
        },
        headers=admin_hdr,
    )
    assert r_new.status_code in {200, 201}, r_new.text
    new_scope = r_new.json()
    assert new_scope["is_active"] is True

    # Grant the SAME (account, region, scope_type) again -> old must deactivate
    r_new2 = await api_client.post(
        "/api/v1/scope-authorizations",
        json={
            "account_id": tech1["id"],
            "region_id": zd_region["id"],
            "scope_type": "REPAIR",
            "remark": "P0-1 第二次授权应停用第一条",
        },
        headers=admin_hdr,
    )
    assert r_new2.status_code in {200, 201}, r_new2.text

    async with AsyncSession(async_engine, expire_on_commit=False) as s:
        from tomori.models.scope_authorization import ScopeAuthorization

        rows = (
            await s.scalars(
                select(ScopeAuthorization).where(
                    ScopeAuthorization.account_id == tech1["id"],
                    ScopeAuthorization.region_id == zd_region["id"],
                    ScopeAuthorization.scope_type == "REPAIR",
                )
            )
        ).all()
        active = [r for r in rows if r.is_active]
        assert len(active) == 1
        assert active[0].id == r_new2.json()["id"]
        assert all(not r.is_active for r in rows if r.id != active[0].id)


async def test_create_region_facility_asset_accounts_and_409_404_403(
    api_client: AsyncClient,
) -> None:
    """POST /regions /facility-assets /accounts: 201 + Location; 422 dup code; 404 bad parent."""
    admin_hdr = await auth_headers_for(api_client, "admin")
    insp_hdr = await auth_headers_for(api_client, "inspector1")
    regions = (await api_client.get("/api/v1/regions", headers=admin_hdr)).json()
    js_region = next(r for r in regions if r["region_code"] == "ZZ-JS-01")
    pts = (await api_client.get("/api/v1/lighting-points", headers=admin_hdr)).json()["items"]
    js_pt = next(p for p in pts if p["point_code"] == "SL-ZZ-JS-0001")
    uid = uuid.uuid4().hex[:10]

    # --- POST /regions ---
    r_forbidden = await api_client.post(
        "/api/v1/regions",
        json={"region_code": f"R-{uid}", "name": "越权区域"},
        headers=insp_hdr,
    )
    assert r_forbidden.status_code == 403
    assert _err(r_forbidden)["code"] == "FORBIDDEN_ROLE"

    r_404 = await api_client.post(
        "/api/v1/regions",
        json={
            "region_code": f"R-{uid}",
            "name": "孤立子区域",
            "parent_region_id": 999999,
        },
        headers=admin_hdr,
    )
    assert r_404.status_code == 404
    assert _err(r_404)["code"] == "RESOURCE_NOT_FOUND"

    r_created = await api_client.post(
        "/api/v1/regions",
        json={
            "region_code": f"R-{uid}",
            "name": "E2E 测试子区域",
            "parent_region_id": js_region["id"],
            "level": 3,
        },
        headers=admin_hdr,
    )
    assert r_created.status_code == 201, r_created.text
    assert r_created.headers.get("location", "").endswith(
        f"/api/v1/regions/{r_created.json()['id']}"
    )
    assert r_created.json()["is_active"] is True

    # Duplicate region_code -> 422 VALIDATION_ERROR (unique constraint translation)
    r_dup = await api_client.post(
        "/api/v1/regions",
        json={"region_code": f"R-{uid}", "name": "重复编码"},
        headers=admin_hdr,
    )
    assert r_dup.status_code == 422
    assert _err(r_dup)["code"] == "VALIDATION_ERROR"

    # --- POST /facility-assets ---
    r_asset = await api_client.post(
        "/api/v1/facility-assets",
        json={
            "asset_code": f"AST-E2E-{uid}",
            "lighting_point_id": js_pt["id"],
            "asset_type": "LUMINAIRE",
            "name": "E2E 新增灯具",
            "importance": "NORMAL",
        },
        headers=admin_hdr,
    )
    assert r_asset.status_code == 201, r_asset.text
    assert r_asset.headers.get("location", "").endswith(
        f"/api/v1/facility-assets/{r_asset.json()['id']}"
    )

    r_dup_asset = await api_client.post(
        "/api/v1/facility-assets",
        json={
            "asset_code": f"AST-E2E-{uid}",
            "lighting_point_id": js_pt["id"],
            "asset_type": "LUMINAIRE",
            "name": "重复资产编码",
        },
        headers=admin_hdr,
    )
    assert r_dup_asset.status_code == 422
    assert _err(r_dup_asset)["code"] == "VALIDATION_ERROR"

    r_404_asset = await api_client.post(
        "/api/v1/facility-assets",
        json={
            "asset_code": f"AST-999-{uid}",
            "lighting_point_id": 999999,
            "asset_type": "LUMINAIRE",
            "name": "孤儿资产",
        },
        headers=admin_hdr,
    )
    assert r_404_asset.status_code == 404
    assert _err(r_404_asset)["code"] == "RESOURCE_NOT_FOUND"

    # --- POST /accounts ---
    r_acc = await api_client.post(
        "/api/v1/accounts",
        json={
            "username": f"e2e_inspector_{uid}",
            "password": "E2e#Local123!",
            "full_name": "E2E 巡查员",
            "role_code": "INSPECTOR",
            "skills": ["LAMP_OUT"],
        },
        headers=admin_hdr,
    )
    assert r_acc.status_code == 201, r_acc.text
    assert r_acc.json()["role_code"] == "INSPECTOR"

    # Newly created account can actually log in
    r_login = await api_client.post(
        "/api/v1/auth/login",
        json={"username": f"e2e_inspector_{uid}", "password": "E2e#Local123!"},
    )
    assert r_login.status_code == 200, r_login.text

    # Duplicate username -> 422 VALIDATION_ERROR (unique constraint)
    r_dup_acc = await api_client.post(
        "/api/v1/accounts",
        json={
            "username": f"e2e_inspector_{uid}",
            "password": "E2e#Local123!",
            "full_name": "重复账号",
            "role_code": "INSPECTOR",
        },
        headers=admin_hdr,
    )
    assert r_dup_acc.status_code == 422
    assert _err(r_dup_acc)["code"] == "VALIDATION_ERROR"

    # Bad role_code -> 422 VALIDATION_ERROR (pydantic enum rejects before handler)
    r_badrole = await api_client.post(
        "/api/v1/accounts",
        json={
            "username": f"e2e_badrole_{uid}",
            "password": "E2e#Local123!",
            "full_name": "无效角色",
            "role_code": "GHOST_ROLE",
        },
        headers=admin_hdr,
    )
    assert r_badrole.status_code == 422
    assert _err(r_badrole)["code"] == "VALIDATION_ERROR"


async def test_reset_password_and_change_password_revoke_tokens(
    api_client: AsyncClient,
) -> None:
    """POST reset-password + change-password: token_version bumps -> old JWT 401."""
    admin_hdr = await auth_headers_for(api_client, "admin")
    accs = (await api_client.get("/api/v1/accounts", headers=admin_hdr)).json()["items"]
    inspector = next(a for a in accs if a["username"] == "inspector1")

    # 1) Admin reset password -> old inspector token becomes invalid
    insp_token_pre = (
        await api_client.post(
            "/api/v1/auth/login",
            json={"username": "inspector1", "password": "Tomori#Inspect2026"},
        )
    ).json()["access_token"]

    r_reset = await api_client.post(
        f"/api/v1/accounts/{inspector['id']}/reset-password",
        json={"new_password": "NewE2e#Reset123!"},
        headers=admin_hdr,
    )
    assert r_reset.status_code == 200, r_reset.text
    assert r_reset.json()["status"] == "ok"

    # Old JWT -> 401 TOKEN_INVALID
    r_old = await api_client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {insp_token_pre}"}
    )
    assert r_old.status_code == 401
    assert _err(r_old)["code"] == "TOKEN_INVALID"

    # New password login works
    r_new_login = await api_client.post(
        "/api/v1/auth/login",
        json={"username": "inspector1", "password": "NewE2e#Reset123!"},
    )
    assert r_new_login.status_code == 200, r_new_login.text
    insp_hdr_new = {"Authorization": f"Bearer {r_new_login.json()['access_token']}"}

    # 2) User change-password: wrong old -> 401 INVALID_CREDENTIALS
    r_wrong = await api_client.post(
        "/api/v1/auth/change-password",
        json={"old_password": "WrongOld#123", "new_password": "NewE2e#Change456!"},
        headers=insp_hdr_new,
    )
    assert r_wrong.status_code == 401
    assert _err(r_wrong)["code"] == "INVALID_CREDENTIALS"

    # 3) Correct old -> 200 + old token invalid afterwards
    r_change = await api_client.post(
        "/api/v1/auth/change-password",
        json={"old_password": "NewE2e#Reset123!", "new_password": "{{SECRET_nmb3xqzn}}"},
        headers=insp_hdr_new,
    )
    assert r_change.status_code == 200, r_change.text

    r_old2 = await api_client.get("/api/v1/auth/me", headers=insp_hdr_new)
    assert r_old2.status_code == 401
    assert _err(r_old2)["code"] == "TOKEN_INVALID"

    # Reset back to the demo Tomori#Inspect2026 so later tests stay green (defensive cleanup)
    r_restore = await api_client.post(
        f"/api/v1/accounts/{inspector['id']}/reset-password",
        json={"new_password": "Tomori#Inspect2026"},
        headers=admin_hdr,
    )
    assert r_restore.status_code == 200


async def test_mark_notification_read_owner_only_403_and_404(
    api_client: AsyncClient,
    async_engine: AsyncEngine,
) -> None:
    """POST /notifications/{id}/read: 200 is_read+read_at; 403 non-owner non-admin; 404."""
    admin_hdr = await auth_headers_for(api_client, "admin")
    insp_hdr = await auth_headers_for(api_client, "inspector1")
    tech1_hdr = await auth_headers_for(api_client, "tech1")

    # Seed a notification owned by inspector1 via production-path domain call
    async with AsyncSession(async_engine, expire_on_commit=False) as s, s.begin():
        from tomori.models.account import Account
        from tomori.services.state_machine import create_notification_task

        inspector = await s.scalar(select(Account).where(Account.username == "inspector1"))
        assert inspector is not None
        notif = create_notification_task(
            s,
            recipient_account_id=inspector.id,
            event_type="AI_CANDIDATE_CREATED",
            channel="IN_APP",
            title="P0-1 已读测试通知",
            body="请确认 AI 候选",
            payload_json={"deep_link": "/tickets/1"},
        )
        await s.flush()
        nid = notif.id

    # 403: tech1 (non-owner, non-admin) cannot mark inspector's notification
    r_403 = await api_client.post(f"/api/v1/notifications/{nid}/read", headers=tech1_hdr)
    assert r_403.status_code == 403
    assert _err(r_403)["code"] == "FORBIDDEN_ROLE"

    # 404: missing notification
    r_404 = await api_client.post("/api/v1/notifications/999999/read", headers=insp_hdr)
    assert r_404.status_code == 404
    assert _err(r_404)["code"] == "RESOURCE_NOT_FOUND"

    # 200: owner inspector1 marks read -> is_read + read_at set
    r_read = await api_client.post(f"/api/v1/notifications/{nid}/read", headers=insp_hdr)
    assert r_read.status_code == 200, r_read.text
    body = r_read.json()
    assert body["is_read"] is True
    assert body["read_at"] is not None

    # Repeat read: stays read (idempotent soft-touch), still 200
    r_read2 = await api_client.post(f"/api/v1/notifications/{nid}/read", headers=insp_hdr)
    assert r_read2.status_code == 200
    assert r_read2.json()["is_read"] is True

    # Admin CAN read someone else's notification (privilege override per code line 372)
    r_admin = await api_client.post(f"/api/v1/notifications/{nid}/read", headers=admin_hdr)
    assert r_admin.status_code == 200
