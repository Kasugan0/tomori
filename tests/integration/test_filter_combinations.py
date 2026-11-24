"""P2 deep filter-combination tests: multi-param AND semantics + param boundaries.

Honest parameter surface (verified against `api/v1/*` — task brief's `channel=`/`status=`/
`related_ticket_id=`/`date_from` params do NOT all exist; real params tested instead):

- `GET /audit-logs`: `action`+`actor_account_id`+`resource_type` AND-combination,
  `resource_id` numeric-only guard, `created_from`/`created_to` boundaries,
  `response_status`, `ticket_id`;
- `GET /notifications`: `is_read`+`event_type` AND-combination (no `channel`/`related_ticket_id`
  params exist — unknown params are ignored by FastAPI, asserted as boundary);
- `GET /facility-assets`: `asset_type`+`lighting_point_id`+`importance` AND-combination
  (no `status` param exists);
- `GET /repair-processes`: `ticket_id`+`technician_account_id` AND-combination
  (no `status` param exists — `RepairProcess` has no status column).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from tests.integration.conftest import auth_headers_for, new_idem_key
from tomori.models.account import Account
from tomori.services.state_machine import create_notification_task


def _paginated(body: dict[str, Any]) -> None:
    for key in ("items", "total", "page", "page_size", "total_pages"):
        assert key in body
    assert isinstance(body["items"], list)


async def test_audit_logs_multi_filter_combination_and_date_boundaries(
    api_client: AsyncClient,
) -> None:
    """`/audit-logs`: action+actor+resource_type AND-combination + date bounds + numeric guard."""
    admin_hdr = await auth_headers_for(api_client, "admin")
    insp_hdr = await auth_headers_for(api_client, "inspector1")
    accs = (await api_client.get("/api/v1/accounts", headers=admin_hdr)).json()["items"]
    inspector = next(a for a in accs if a["username"] == "inspector1")

    # Seed an audit row via a real failed login (LOGIN_FAILED audit, actor NULL)
    await api_client.post(
        "/api/v1/auth/login",
        json={"username": "inspector1", "password": "wrong-secret-x"},
    )

    # 1) action+actor_account_id+resource_type AND-combination
    r_combo = await api_client.get(
        "/api/v1/audit-logs?action=TICKET_CREATED&resource_type=FAULT_TICKET"
        f"&actor_account_id={inspector['id']}&page_size=100",
        headers=admin_hdr,
    )
    assert r_combo.status_code == 200
    combo = r_combo.json()
    _paginated(combo)
    for row in combo["items"]:
        assert row["action"] == "TICKET_CREATED"
        assert row["resource_type"] == "FAULT_TICKET"
        assert row["actor_account_id"] == inspector["id"]

    # 2) created_from/created_to boundary (URL-safe params dict — `+` in isoformat
    #    would be decoded as a space and 422, so params= is mandatory)
    now = datetime.now(UTC)
    r_win = await api_client.get(
        "/api/v1/audit-logs",
        params={
            "action": "LOGIN_FAILED",
            "created_from": (now - timedelta(minutes=5)).isoformat(),
            "created_to": (now + timedelta(minutes=5)).isoformat(),
        },
        headers=admin_hdr,
    )
    assert r_win.status_code == 200
    assert r_win.json()["total"] >= 1
    assert all(a["action"] == "LOGIN_FAILED" for a in r_win.json()["items"])

    r_future = await api_client.get(
        "/api/v1/audit-logs",
        params={"created_from": (now + timedelta(hours=1)).isoformat()},
        headers=admin_hdr,
    )
    assert r_future.status_code == 200
    assert r_future.json()["items"] == []
    assert r_future.json()["total"] == 0

    # 3) resource_id numeric-only guard: non-numeric value is IGNORED (no 500)
    r_nonstr = await api_client.get(
        "/api/v1/audit-logs?resource_id=not-a-number&page_size=5", headers=admin_hdr
    )
    assert r_nonstr.status_code == 200

    # 4) response_status filter isolates 2xx rows
    r_2xx = await api_client.get(
        "/api/v1/audit-logs?response_status=200&page_size=50", headers=admin_hdr
    )
    assert r_2xx.status_code == 200
    assert all(a["response_status"] == 200 for a in r_2xx.json()["items"])

    # 5) ticket_id filter on nonexistent ticket -> empty
    r_tid = await api_client.get("/api/v1/audit-logs?ticket_id=999999", headers=admin_hdr)
    assert r_tid.status_code == 200
    assert r_tid.json()["items"] == []

    # inspector forbidden on admin-only audit-logs
    r_403 = await api_client.get("/api/v1/audit-logs", headers=insp_hdr)
    assert r_403.status_code == 403


async def test_notifications_is_read_event_type_combination_and_unknown_param_boundary(
    api_client: AsyncClient,
    async_engine: AsyncEngine,
) -> None:
    """`/notifications`: is_read+event_type AND-combination; unknown `channel` param ignored."""
    insp_hdr = await auth_headers_for(api_client, "inspector1")

    async with AsyncSession(async_engine, expire_on_commit=False) as s, s.begin():
        inspector = await s.scalar(select(Account).where(Account.username == "inspector1"))
        assert inspector is not None
        n1 = create_notification_task(
            s,
            recipient_account_id=inspector.id,
            event_type="AI_CANDIDATE_CREATED",
            channel="IN_APP",
            title="P2 过滤组合-未读",
            body="AI 候选待确认",
        )
        n2 = create_notification_task(
            s,
            recipient_account_id=inspector.id,
            event_type="TICKET_DISPATCHED",
            channel="SMS",
            title="P2 过滤组合-已读",
            body="已派工通知",
        )
        await s.flush()
        n1_id, n2_id = n1.id, n2.id
        n2.is_read = True
        n2.read_at = datetime.now(UTC)

    # is_read=false + event_type=AI_CANDIDATE_CREATED -> only n1 matches
    r = await api_client.get(
        "/api/v1/notifications?is_read=false&event_type=AI_CANDIDATE_CREATED&page_size=100",
        headers=insp_hdr,
    )
    assert r.status_code == 200
    body = r.json()
    _paginated(body)
    ids = {n["id"] for n in body["items"]}
    assert n1_id in ids
    assert n2_id not in ids
    assert all(
        not n["is_read"] and n["event_type"] == "AI_CANDIDATE_CREATED" for n in body["items"]
    )

    # is_read=true -> n2 present, n1 absent
    r_read = await api_client.get(
        "/api/v1/notifications?is_read=true&page_size=100", headers=insp_hdr
    )
    assert n2_id in {n["id"] for n in r_read.json()["items"]}
    assert n1_id not in {n["id"] for n in r_read.json()["items"]}

    # Boundary: `channel=` is NOT a real query param — FastAPI ignores it, no 422
    r_chan = await api_client.get(
        "/api/v1/notifications?channel=EMAIL&page_size=5", headers=insp_hdr
    )
    assert r_chan.status_code == 200

    # dead-letters: only DEAD_LETTER rows + pagination (no related_ticket_id param)
    dead = await api_client.get(
        "/api/v1/notifications/dead-letters?page_size=100",
        headers=await auth_headers_for(api_client, "admin"),
    )
    assert dead.status_code == 200
    _paginated(dead.json())
    assert all(n["status"] == "DEAD_LETTER" for n in dead.json()["items"])


async def test_facility_assets_asset_type_point_importance_combination(
    api_client: AsyncClient,
) -> None:
    """`/facility-assets`: asset_type+lighting_point_id+importance AND-combination."""
    admin_hdr = await auth_headers_for(api_client, "admin")
    pts = (await api_client.get("/api/v1/lighting-points", headers=admin_hdr)).json()["items"]
    pt = pts[0]

    r_create = await api_client.post(
        "/api/v1/facility-assets",
        json={
            "asset_code": f"AST-P2-{pt['id']}",
            "lighting_point_id": pt["id"],
            "asset_type": "LUMINAIRE",
            "name": "P2 过滤组合灯具",
            "importance": "HIGH",
        },
        headers=admin_hdr,
    )
    assert r_create.status_code == 201, r_create.text
    new_asset = r_create.json()

    # AND-combination: all three must hold
    r_all = await api_client.get(
        "/api/v1/facility-assets?asset_type=LUMINAIRE"
        f"&lighting_point_id={pt['id']}&importance=HIGH&page_size=100",
        headers=admin_hdr,
    )
    assert r_all.status_code == 200
    body = r_all.json()
    _paginated(body)
    assert new_asset["id"] in {a["id"] for a in body["items"]}
    for a in body["items"]:
        assert a["asset_type"] == "LUMINAIRE"
        assert a["lighting_point_id"] == pt["id"]
        assert a["importance"] == "HIGH"

    # Tightening: importance=NORMAL on same point+type must EXCLUDE the HIGH asset
    r_norm = await api_client.get(
        "/api/v1/facility-assets?asset_type=LUMINAIRE"
        f"&lighting_point_id={pt['id']}&importance=NORMAL&page_size=100",
        headers=admin_hdr,
    )
    assert new_asset["id"] not in {a["id"] for a in r_norm.json()["items"]}

    # Boundary: `status=` is NOT a real query param — ignored (documented surface)
    r_status = await api_client.get(
        "/api/v1/facility-assets?status=IN_SERVICE&page_size=5", headers=admin_hdr
    )
    assert r_status.status_code == 200


async def test_repair_processes_ticket_and_technician_combination(
    api_client: AsyncClient,
) -> None:
    """`/repair-processes`: ticket_id+technician_account_id AND-combination + ownership scope."""
    admin_hdr = await auth_headers_for(api_client, "admin")
    insp_hdr = await auth_headers_for(api_client, "inspector1")
    tech1_hdr = await auth_headers_for(api_client, "tech1")
    accs = (await api_client.get("/api/v1/accounts", headers=admin_hdr)).json()["items"]
    tech1 = next(a for a in accs if a["username"] == "tech1")
    tech2 = next(a for a in accs if a["username"] == "tech2")

    # Build a repair process: ticket -> dispatch -> accept -> complete (creates RepairProcess)
    pts = (await api_client.get("/api/v1/lighting-points", headers=admin_hdr)).json()["items"]
    pt = next(p for p in pts if p["point_code"] == "SL-ZZ-JS-0001")
    r_t = await api_client.post(
        "/api/v1/tickets",
        json={
            "lighting_point_id": pt["id"],
            "fault_type": "FIXTURE_DAMAGED",
            "title": "P2 维修过程过滤组合",
            "safety_flag": False,
        },
        headers={**insp_hdr, "Idempotency-Key": new_idem_key("p2rp")},
    )
    tid = int(r_t.json()["id"])
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
        headers={**admin_hdr, "Idempotency-Key": new_idem_key("p2rpd")},
    )
    did = int(r_d.json()["id"])
    await api_client.post(
        f"/api/v1/dispatches/{did}/accept",
        headers={**tech1_hdr, "Idempotency-Key": new_idem_key("p2rpa")},
    )
    now = datetime.now(UTC)
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
            "safety_note": "六件套完成",
            "arrived_at": (now - timedelta(minutes=20)).isoformat(),
            "repair_summary": "P2 完工",
            "materials": [],
        },
        headers={**tech1_hdr, "Idempotency-Key": new_idem_key("p2rpc")},
    )

    # ticket_id+technician_account_id AND-combination -> the created repair process
    r_combo = await api_client.get(
        f"/api/v1/repair-processes?ticket_id={tid}"
        f"&technician_account_id={tech1['id']}&page_size=100",
        headers=admin_hdr,
    )
    assert r_combo.status_code == 200
    body = r_combo.json()
    _paginated(body)
    assert len(body["items"]) == 1
    rp = body["items"][0]
    assert rp["ticket_id"] == tid
    assert rp["technician_account_id"] == tech1["id"]

    # technician_account_id=tech2 on same ticket -> empty (AND semantics)
    r_t2 = await api_client.get(
        f"/api/v1/repair-processes?ticket_id={tid}&technician_account_id={tech2['id']}",
        headers=admin_hdr,
    )
    assert r_t2.json()["items"] == []

    # TECHNICIAN ownership scoping: tech2 sees only own processes
    r_t2list = await api_client.get(
        "/api/v1/repair-processes?page_size=100",
        headers=await auth_headers_for(api_client, "tech2"),
    )
    assert all(rp2["technician_account_id"] == tech2["id"] for rp2 in r_t2list.json()["items"])

    # Boundary: `status=` is NOT a real param on this endpoint — ignored
    r_status = await api_client.get(
        "/api/v1/repair-processes?status=COMPLETED&page_size=5", headers=admin_hdr
    )
    assert r_status.status_code == 200

    # inspector forbidden (ADMIN|TECHNICIAN only)
    r_403 = await api_client.get("/api/v1/repair-processes", headers=insp_hdr)
    assert r_403.status_code == 403
