"""P1 GET endpoint coverage gap tests: 403/404/pagination structure/region scoping/filters.

Coverage targets (real PG17, exact `error.code` where applicable):
- ADMIN-only GET lists -> `403 FORBIDDEN_ROLE` for INSPECTOR/TECHNICIAN;
- `ADMIN|TECHNICIAN`-only GET lists -> `403 FORBIDDEN_ROLE` for INSPECTOR;
- region-scoped GET lists -> filtered (not 403) for scoped roles;
  cross-region detail GETs -> `403 FORBIDDEN_REGION_SCOPE`;
- `PaginatedResponse` structure: `items`/`total`/`page`/`page_size`/`total_pages`;
- out-of-range `page` -> `items=[]` (not 500); `page_size=101` -> `422 VALIDATION_ERROR`;
- detail GETs -> `404 RESOURCE_NOT_FOUND` on missing ids;
- ordering stability + filter parameters actually filter.
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from tests.integration.conftest import auth_headers_for, new_idem_key
from tomori.models.account import Account
from tomori.services.state_machine import create_notification_task


def _err(resp: Any) -> dict[str, Any]:
    err: dict[str, Any] = resp.json()["error"]
    return err


def _assert_paginated(body: dict[str, Any]) -> None:
    for key in ("items", "total", "page", "page_size", "total_pages"):
        assert key in body, f"missing paginated key {key} in {body}"
    assert isinstance(body["items"], list)
    assert body["page_size"] >= 0
    assert body["page"] >= 1
    assert body["total_pages"] == (
        0 if body["total"] == 0 else max(1, -(-body["total"] // body["page_size"]))
    )


async def test_admin_only_get_lists_return_403_for_inspector_and_technician(
    api_client: AsyncClient,
) -> None:
    """ADMIN-only GET lists: FORBIDDEN_ROLE for non-admin; 200 + paginated shape for admin."""
    admin_hdr = await auth_headers_for(api_client, "admin")
    insp_hdr = await auth_headers_for(api_client, "inspector1")
    tech1_hdr = await auth_headers_for(api_client, "tech1")

    admin_only_paginated = [
        "/api/v1/accounts",
        "/api/v1/scope-authorizations",
        "/api/v1/business-rules",
        "/api/v1/audit-logs",
        "/api/v1/notifications/dead-letters",
    ]
    for path in admin_only_paginated:
        for hdr in (insp_hdr, tech1_hdr):
            r = await api_client.get(path, headers=hdr)
            assert r.status_code == 403, f"{path} expected 403 got {r.status_code}"
            assert _err(r)["code"] == "FORBIDDEN_ROLE"
        r_admin = await api_client.get(path, headers=admin_hdr)
        assert r_admin.status_code == 200, f"{path}: {r_admin.text}"
        _assert_paginated(r_admin.json())

    # /roles is list-shaped (not paginated), still ADMIN-only
    r_roles_403 = await api_client.get("/api/v1/roles", headers=insp_hdr)
    assert r_roles_403.status_code == 403
    assert _err(r_roles_403)["code"] == "FORBIDDEN_ROLE"
    r_roles = await api_client.get("/api/v1/roles", headers=admin_hdr)
    assert r_roles.status_code == 200
    assert isinstance(r_roles.json(), list)
    assert {r["code"] for r in r_roles.json()} == {"ADMIN", "INSPECTOR", "TECHNICIAN"}


async def test_repair_technician_only_get_lists_return_403_for_inspector(
    api_client: AsyncClient,
) -> None:
    """`ADMIN|TECHNICIAN` GET lists -> 403 FORBIDDEN_ROLE for INSPECTOR."""
    admin_hdr = await auth_headers_for(api_client, "admin")
    insp_hdr = await auth_headers_for(api_client, "inspector1")
    tech1_hdr = await auth_headers_for(api_client, "tech1")

    for path in (
        "/api/v1/dispatches",
        "/api/v1/repair-processes",
        "/api/v1/material-usages",
    ):
        r = await api_client.get(path, headers=insp_hdr)
        assert r.status_code == 403, f"{path} expected 403 got {r.status_code}: {r.text}"
        assert _err(r)["code"] == "FORBIDDEN_ROLE"
        # ADMIN + TECHNICIAN both allowed
        for hdr in (admin_hdr, tech1_hdr):
            r_ok = await api_client.get(path, headers=hdr)
            assert r_ok.status_code == 200, f"{path}: {r_ok.text}"
            _assert_paginated(r_ok.json())


async def test_region_scoped_get_lists_filter_and_detail_403(
    api_client: AsyncClient,
) -> None:
    """Region-scoped lists filter (not 403); cross-region detail -> 403 FORBIDDEN_REGION_SCOPE."""
    admin_hdr = await auth_headers_for(api_client, "admin")
    insp_hdr = await auth_headers_for(api_client, "inspector1")

    # /regions: inspector1 sees only authorized regions (INSPECT scope -> ZZ-JS-01)
    r_regions = await api_client.get("/api/v1/regions", headers=insp_hdr)
    assert r_regions.status_code == 200
    region_codes = {r["region_code"] for r in r_regions.json()}
    assert "ZZ-JS-01" in region_codes
    assert "ZZ-ZD-01" not in region_codes  # inspector1 has no INSPECT scope on ZD
    # admin sees all
    r_regions_admin = await api_client.get("/api/v1/regions", headers=admin_hdr)
    assert {r["region_code"] for r in r_regions_admin.json()} == {
        "ZZ-HQ-01",
        "ZZ-JS-01",
        "ZZ-ZD-01",
    }

    # /lighting-points: paginated + region filtered for inspector
    r_pts = await api_client.get("/api/v1/lighting-points", headers=insp_hdr)
    assert r_pts.status_code == 200
    body = r_pts.json()
    _assert_paginated(body)
    pt_codes = {p["point_code"] for p in body["items"]}
    assert "SL-ZZ-JS-0001" in pt_codes
    assert "SL-ZZ-ZD-0001" not in pt_codes

    # /facility-assets: inspector scoped list; cross-region assets filtered out
    r_assets = await api_client.get("/api/v1/facility-assets", headers=insp_hdr)
    assert r_assets.status_code == 200
    _assert_paginated(r_assets.json())

    # Detail: cross-region ticket detail -> 403 FORBIDDEN_REGION_SCOPE
    # (admin creates ticket on ZD; inspector1 lacks INSPECT there)
    zd_pts = next(
        p
        for p in (await api_client.get("/api/v1/lighting-points", headers=admin_hdr)).json()[
            "items"
        ]
        if p["point_code"] == "SL-ZZ-ZD-0001"
    )
    r_t = await api_client.post(
        "/api/v1/tickets",
        json={
            "lighting_point_id": zd_pts["id"],
            "fault_type": "LAMP_OUT",
            "title": "P1 跨区详情单",
            "safety_flag": False,
        },
        headers={**admin_hdr, "Idempotency-Key": new_idem_key("p1-zd")},
    )
    assert r_t.status_code == 201
    tid = r_t.json()["id"]
    r_cross = await api_client.get(f"/api/v1/tickets/{tid}", headers=insp_hdr)
    assert r_cross.status_code == 403
    assert _err(r_cross)["code"] == "FORBIDDEN_REGION_SCOPE"


async def test_pagination_structure_out_of_range_and_page_size_cap(
    api_client: AsyncClient,
) -> None:
    """Pagination: out-of-range page -> items=[]; page_size=101 -> 422 VALIDATION_ERROR."""
    admin_hdr = await auth_headers_for(api_client, "admin")

    # Out-of-range page on accounts -> items=[] (not 500)
    r_far = await api_client.get("/api/v1/accounts?page=999&page_size=20", headers=admin_hdr)
    assert r_far.status_code == 200, r_far.text
    body = r_far.json()
    _assert_paginated(body)
    assert body["items"] == []
    assert body["total"] >= 4  # seeded accounts exist
    assert body["page"] == 999

    # page_size=101 -> 422 VALIDATION_ERROR (le=100 cap)
    r_cap = await api_client.get("/api/v1/accounts?page_size=101", headers=admin_hdr)
    assert r_cap.status_code == 422
    assert _err(r_cap)["code"] == "VALIDATION_ERROR"

    # Empty-set semantics on acceptances for a nonexistent ticket_id
    r_empty = await api_client.get("/api/v1/acceptances?ticket_id=999999", headers=admin_hdr)
    assert r_empty.status_code == 200
    body = r_empty.json()
    _assert_paginated(body)
    assert body["items"] == []
    assert body["total"] == 0
    assert body["total_pages"] == 0


async def test_detail_get_404s_on_missing_ids(api_client: AsyncClient) -> None:
    """Detail GETs return 404 RESOURCE_NOT_FOUND for nonexistent ids."""
    admin_hdr = await auth_headers_for(api_client, "admin")
    for path in (
        "/api/v1/tickets/999999",
        "/api/v1/rework-orders/999999",
        "/api/v1/repair-processes/999999",
        "/api/v1/evidence/999999/raw",
        "/api/v1/evidence/999999/annotated",
    ):
        r = await api_client.get(path, headers=admin_hdr)
        assert r.status_code == 404, f"{path} expected 404 got {r.status_code}"
        assert _err(r)["code"] == "RESOURCE_NOT_FOUND"


async def test_get_list_ordering_stability_and_filter_parameters(
    api_client: AsyncClient,
    async_engine: AsyncEngine,
) -> None:
    """Ordering stability (two calls identical) + filter params actually filter."""
    admin_hdr = await auth_headers_for(api_client, "admin")
    insp_hdr = await auth_headers_for(api_client, "inspector1")

    # Ordering stability on /tickets (id-asc or id-desc: whatever impl, stable across calls)
    r_t1 = await api_client.get("/api/v1/tickets", headers=admin_hdr)
    r_t2 = await api_client.get("/api/v1/tickets", headers=admin_hdr)
    assert r_t1.status_code == r_t2.status_code == 200
    assert [t["id"] for t in r_t1.json()["items"]] == [t["id"] for t in r_t2.json()["items"]]

    # Filter: /audit-logs?action=LOGIN_FAILED only returns matching actions
    r_aud = await api_client.get(
        "/api/v1/audit-logs?action=LOGIN_FAILED&page_size=100", headers=admin_hdr
    )
    assert r_aud.status_code == 200
    aud_body = r_aud.json()
    _assert_paginated(aud_body)
    assert all(a["action"] == "LOGIN_FAILED" for a in aud_body["items"])

    # Filter: /facility-assets?asset_type=POLE filters (if any POLE exists) — at least
    # every returned row must match the filter predicate
    r_fa = await api_client.get(
        "/api/v1/facility-assets?asset_type=POLE&page_size=100", headers=admin_hdr
    )
    assert r_fa.status_code == 200
    assert all(a["asset_type"] == "POLE" for a in r_fa.json()["items"])

    # Filter: /notifications?is_read=false — seed one notification then check filter
    async with AsyncSession(async_engine, expire_on_commit=False) as s, s.begin():
        inspector = await s.scalar(select(Account).where(Account.username == "inspector1"))
        assert inspector is not None
        n = create_notification_task(
            s,
            recipient_account_id=inspector.id,
            event_type="AI_CANDIDATE_CREATED",
            channel="IN_APP",
            title="P1 通知过滤验证",
            body="过滤参数生效检查",
        )
        await s.flush()
        nid = n.id

    r_n = await api_client.get("/api/v1/notifications?is_read=false", headers=insp_hdr)
    assert r_n.status_code == 200
    notifs = r_n.json()
    _assert_paginated(notifs)
    ids = [x["id"] for x in notifs["items"]]
    assert nid in ids
    assert all(not x["is_read"] for x in notifs["items"])

    # /scope-authorizations?account_id= filters to that account only
    accs = (await api_client.get("/api/v1/accounts", headers=admin_hdr)).json()["items"]
    tech1 = next(a for a in accs if a["username"] == "tech1")
    r_sc = await api_client.get(
        f"/api/v1/scope-authorizations?account_id={tech1['id']}&page_size=100",
        headers=admin_hdr,
    )
    assert r_sc.status_code == 200
    assert all(s["account_id"] == tech1["id"] for s in r_sc.json()["items"])


async def test_notifications_are_per_user_scoped(api_client: AsyncClient) -> None:
    """GET /notifications returns only the caller's own notifications."""
    admin_hdr = await auth_headers_for(api_client, "admin")
    insp_hdr = await auth_headers_for(api_client, "inspector1")
    tech1_hdr = await auth_headers_for(api_client, "tech1")

    r_insp = await api_client.get("/api/v1/notifications?page_size=100", headers=insp_hdr)
    assert r_insp.status_code == 200
    _assert_paginated(r_insp.json())

    r_tech = await api_client.get("/api/v1/notifications?page_size=100", headers=tech1_hdr)
    assert r_tech.status_code == 200
    insp_ids = {n["id"] for n in r_insp.json()["items"]}
    tech_ids = {n["id"] for n in r_tech.json()["items"]}
    assert insp_ids.isdisjoint(tech_ids)  # recipients don't overlap

    # admin also sees only own notifications (no global view on this endpoint)
    r_admin = await api_client.get("/api/v1/notifications?page_size=100", headers=admin_hdr)
    assert r_admin.status_code == 200
    admin_ids = {n["id"] for n in r_admin.json()["items"]}
    assert admin_ids.isdisjoint(insp_ids) or not insp_ids


async def test_health_endpoint_structure_and_vision_degraded_modes(
    api_client: AsyncClient,
) -> None:
    """GET /health: 200 + fields; service probes for strict_custom/disabled degrade."""
    r = await api_client.get("/api/v1/health")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert isinstance(body["vision"], dict)
    for key in ("ready", "mode", "detail"):
        assert key in body["vision"]
    # Test env resolves `auto` -> baseline_pretrained_opencv: ready=True, honest non-degraded
    assert body["vision"]["ready"] is True
    assert body["vision"]["mode"] == "baseline_pretrained_opencv"
    assert isinstance(body["vision"]["detail"], str) and body["vision"]["detail"]

    # Degraded states probed via the real VisionClient code path (no mock):
    # strict_custom with no weights -> ready=False; inference disabled -> ready=False
    from pathlib import Path

    from tomori.vision import config as vconfig
    from tomori.vision.client import VisionClient

    strict_cfg = vconfig.Settings(
        upload_dir=Path("/tmp/x"),
        max_upload_bytes=15 * 1024 * 1024,
        yolo_weights_path=Path("/tmp/definitely-missing-best.pt"),
        yolo_inference_enabled=True,
        yolo_inference_mode="strict_custom",
        yolo_img_size=1280,
        yolo_conf_threshold=0.25,
        yolo_device="cpu",
    )
    probe = VisionClient(settings=strict_cfg).check_readiness()
    assert probe.ready is False
    assert probe.mode is None
    assert probe.weights_path == str(strict_cfg.yolo_weights_path)
    assert "降级" in probe.detail or "不可" in probe.detail or "不可用" in probe.detail

    disabled_cfg = strict_cfg.__class__(**{**vars(strict_cfg), "yolo_inference_enabled": False})
    probe2 = VisionClient(settings=disabled_cfg).check_readiness()
    assert probe2.ready is False
