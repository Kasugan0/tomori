"""L2 API dispatch ranking, 5 excluded reasons, safety 6-piece, and 48h SLA scan tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from tests.integration.conftest import auth_headers_for, new_idem_key
from tomori.models.account import Account
from tomori.models.account_role import AccountRole
from tomori.models.audit_log import AuditLog
from tomori.models.dispatch_record import DispatchRecord
from tomori.models.fault_ticket import FaultTicket
from tomori.models.role import Role
from tomori.models.scope_authorization import ScopeAuthorization
from tomori.services.auth import hash_password


async def test_dispatch_candidates_ranking_and_all_5_excluded_reasons(
    api_client: AsyncClient,
    async_engine: AsyncEngine,
) -> None:
    """Verify ranking + all 5 excluded[].reason values + 422 eligibility errors on dispatch."""
    admin_hdr = await auth_headers_for(api_client, "admin")
    insp_hdr = await auth_headers_for(api_client, "inspector1")

    pts = (await api_client.get("/api/v1/lighting-points", headers=admin_hdr)).json()["items"]
    js_pt = next(p for p in pts if p["point_code"] == "SL-ZZ-JS-0001")
    js_pt2 = next(p for p in pts if p["point_code"] == "SL-ZZ-JS-0002")
    region_id = int(js_pt["region_id"])

    # Create target ticket for candidate computation (fault_type="LAMP_OUT": tech1 matches skill!)
    r_t = await api_client.post(
        "/api/v1/tickets",
        json={
            "lighting_point_id": js_pt["id"],
            "fault_type": "LAMP_OUT",
            "title": "Candidate ranking probe",
            "safety_flag": False,
        },
        headers={**insp_hdr, "Idempotency-Key": new_idem_key("disp-t")},
    )
    assert r_t.status_code == 201
    tid = int(r_t.json()["id"])

    # Seed accounts covering all 5 ineligible reasons for Jinshui region
    async with AsyncSession(async_engine, expire_on_commit=False) as s:
        role_tech = await s.scalar(select(Role).where(Role.code == "TECHNICIAN"))
        role_insp = await s.scalar(select(Role).where(Role.code == "INSPECTOR"))
        assert role_tech is not None and role_insp is not None

        def _mk_acc(uname: str, *, status: str = "ACTIVE", max_disp: int = 3) -> Account:
            acc = Account(
                username=uname,
                full_name=uname,
                status=status,
                skills=[],
                max_active_dispatch=max_disp,
            )
            acc.password_hash = hash_password("{{SECRET_g1f0l4j5}}")
            s.add(acc)
            return acc

        acc_no_scope = _mk_acc("tech_no_scope")
        acc_expired = _mk_acc("tech_expired")
        acc_inactive = _mk_acc("tech_inactive", status="DISABLED")
        acc_wrong_role = _mk_acc("insp_with_repair_scope")
        acc_overloaded = _mk_acc("tech_overloaded", max_disp=1)
        await s.flush()

        for acc in (acc_no_scope, acc_expired, acc_inactive, acc_overloaded):
            s.add(AccountRole(account_id=acc.id, role_id=role_tech.id))
        s.add(AccountRole(account_id=acc_wrong_role.id, role_id=role_insp.id))

        # Expired REPAIR scope
        expired_scope = ScopeAuthorization(
            account_id=acc_expired.id,
            region_id=region_id,
            scope_type="REPAIR",
            is_active=True,
            valid_from=datetime.now(UTC) - timedelta(days=10),
            valid_to=datetime.now(UTC) - timedelta(days=1),
        )
        # Valid REPAIR scopes for wrong_role and overloaded
        wrong_role_scope = ScopeAuthorization(
            account_id=acc_wrong_role.id,
            region_id=region_id,
            scope_type="REPAIR",
            is_active=True,
            valid_from=datetime.now(UTC) - timedelta(days=1),
        )
        overloaded_scope = ScopeAuthorization(
            account_id=acc_overloaded.id,
            region_id=region_id,
            scope_type="REPAIR",
            is_active=True,
            valid_from=datetime.now(UTC) - timedelta(days=1),
        )
        s.add_all([expired_scope, wrong_role_scope, overloaded_scope])
        await s.flush()
        exp_sid = expired_scope.id
        wr_sid = wrong_role_scope.id
        ov_sid = overloaded_scope.id
        no_scope_id = acc_no_scope.id
        expired_id = acc_expired.id
        inactive_id = acc_inactive.id
        wrong_role_id = acc_wrong_role.id
        overloaded_id = acc_overloaded.id
        await s.commit()

    # Give `tech_overloaded` 1 active dispatch on js_pt2 so active_count == max_active_dispatch (1)
    r_t2 = await api_client.post(
        "/api/v1/tickets",
        json={
            "lighting_point_id": js_pt2["id"],
            "fault_type": "OTHER",
            "title": "Load saturator",
            "safety_flag": False,
        },
        headers={**insp_hdr, "Idempotency-Key": new_idem_key("disp-load")},
    )
    assert r_t2.status_code == 201
    r_d_load = await api_client.post(
        f"/api/v1/tickets/{r_t2.json()['id']}/dispatch",
        json={"technician_account_id": overloaded_id, "scope_authorization_id": ov_sid},
        headers={**admin_hdr, "Idempotency-Key": new_idem_key("disp-ov")},
    )
    assert r_d_load.status_code == 201

    # Query candidates for tid
    r_cands = await api_client.get(
        f"/api/v1/tickets/{tid}/dispatch-candidates",
        headers=admin_hdr,
    )
    assert r_cands.status_code == 200
    body = r_cands.json()
    cands = body["candidates"]
    assert len(cands) >= 2
    assert cands[0]["recommend_score"] >= cands[1]["recommend_score"]
    # Both tech1 and tech2 have LAMP_OUT in seed skills and 0 active -> deterministic tie-break
    assert {c["username"] for c in cands}.issuperset({"tech1", "tech2"})

    excl_by_user = {e["username"]: e["reason"] for e in body["excluded"]}
    assert excl_by_user["tech_no_scope"] == "NO_REGION_SCOPE"
    assert excl_by_user["tech_expired"] == "SCOPE_EXPIRED"
    assert excl_by_user["tech_inactive"] == "ACCOUNT_INACTIVE"
    assert excl_by_user["insp_with_repair_scope"] == "WRONG_ROLE"
    assert excl_by_user["tech_overloaded"] == "OVERLOADED"
    assert set(excl_by_user.values()) >= {
        "NO_REGION_SCOPE",
        "SCOPE_EXPIRED",
        "ACCOUNT_INACTIVE",
        "WRONG_ROLE",
        "OVERLOADED",
    }

    # Direct dispatch checks: 422 DISPATCH_TECHNICIAN_NOT_ELIGIBLE vs TECHNICIAN_OVERLOADED
    for bad_uid, bad_sid, exp_code, exp_reason in (
        (no_scope_id, ov_sid, "DISPATCH_TECHNICIAN_NOT_ELIGIBLE", "NO_REGION_SCOPE"),
        (expired_id, exp_sid, "DISPATCH_TECHNICIAN_NOT_ELIGIBLE", "SCOPE_EXPIRED"),
        (inactive_id, ov_sid, "DISPATCH_TECHNICIAN_NOT_ELIGIBLE", "ACCOUNT_INACTIVE"),
        (wrong_role_id, wr_sid, "DISPATCH_TECHNICIAN_NOT_ELIGIBLE", "WRONG_ROLE"),
        (overloaded_id, ov_sid, "TECHNICIAN_OVERLOADED", "OVERLOADED"),
    ):
        r_rej = await api_client.post(
            f"/api/v1/tickets/{tid}/dispatch",
            json={"technician_account_id": bad_uid, "scope_authorization_id": bad_sid},
            headers={**admin_hdr, "Idempotency-Key": new_idem_key("disp-bad")},
        )
        assert r_rej.status_code == 422
        err = r_rej.json()["error"]
        assert err["code"] == exp_code
        assert err["details"]["reason"] == exp_reason


async def test_electrical_safety_six_piece_collects_all_missing_items(
    api_client: AsyncClient,
) -> None:
    """Incomplete safety checklist returns 422 ELECTRICAL_SAFETY_CHECK_REQUIRED with ALL missing."""
    admin_hdr = await auth_headers_for(api_client, "admin")
    insp_hdr = await auth_headers_for(api_client, "inspector1")
    tech_hdr = await auth_headers_for(api_client, "tech1")

    pts = (await api_client.get("/api/v1/lighting-points", headers=admin_hdr)).json()["items"]
    js_pt = next(p for p in pts if p["point_code"] == "SL-ZZ-JS-0001")

    r_t = await api_client.post(
        "/api/v1/tickets",
        json={
            "lighting_point_id": js_pt["id"],
            "fault_type": "LAMP_OUT",
            "title": "Safety 6-piece probe",
            "safety_flag": False,
        },
        headers={**insp_hdr, "Idempotency-Key": new_idem_key("safe-t")},
    )
    tid = r_t.json()["id"]
    cands = (
        await api_client.get(f"/api/v1/tickets/{tid}/dispatch-candidates", headers=admin_hdr)
    ).json()["candidates"]
    t1 = next(c for c in cands if c["username"] == "tech1")
    r_d = await api_client.post(
        f"/api/v1/tickets/{tid}/dispatch",
        json={
            "technician_account_id": t1["account_id"],
            "scope_authorization_id": t1["scope_authorization_id"],
        },
        headers={**admin_hdr, "Idempotency-Key": new_idem_key("safe-d")},
    )
    did = r_d.json()["id"]
    await api_client.post(
        f"/api/v1/dispatches/{did}/accept",
        headers={**tech_hdr, "Idempotency-Key": new_idem_key("safe-a")},
    )

    # Partial failure: master switch False, 2 checklist items False, blank safety_note
    r_fail = await api_client.post(
        f"/api/v1/dispatches/{did}/complete",
        json={
            "electrical_safety_checked": False,
            "safety_checklist": {
                "power_isolated": True,
                "voltage_verified_zero": False,
                "insulation_intact": True,
                "grounding_connected": True,
                "ppe_worn": False,
            },
            "safety_note": "   ",
            "arrived_at": datetime.now(UTC).isoformat(),
            "repair_summary": "attempt without full safety",
        },
        headers={**tech_hdr, "Idempotency-Key": new_idem_key("safe-fail")},
    )
    assert r_fail.status_code == 422
    err = r_fail.json()["error"]
    assert err["code"] == "ELECTRICAL_SAFETY_CHECK_REQUIRED"
    assert set(err["details"]["missing_items"]) == {
        "electrical_safety_checked",
        "voltage_verified_zero",
        "ppe_worn",
        "safety_note",
    }


async def test_48h_sla_p4_coverage_regrade_no_extension_and_scan_idempotency(
    api_client: AsyncClient,
    async_engine: AsyncEngine,
) -> None:
    """P4 gets 48h deadline; regrade never extends it; 2nd /sla/scan returns marked_overdue=0."""
    admin_hdr = await auth_headers_for(api_client, "admin")

    pts = (await api_client.get("/api/v1/lighting-points", headers=admin_hdr)).json()["items"]
    zd_pt = next(p for p in pts if p["point_code"] == "SL-ZZ-ZD-0001")

    # SL-ZZ-ZD-0001 (BRANCH) + OTHER + safety_flag=False -> P4 fallback, STILL gets 48h deadline!
    r_p4 = await api_client.post(
        "/api/v1/tickets",
        json={
            "lighting_point_id": zd_pt["id"],
            "fault_type": "OTHER",
            "title": "P4 48h SLA baseline check",
            "safety_flag": False,
        },
        headers={**admin_hdr, "Idempotency-Key": new_idem_key("sla-p4")},
    )
    assert r_p4.status_code == 201
    t = r_p4.json()
    assert t["priority"] == "P4"
    assert t["response_deadline_at"] is None
    assert t["repair_deadline_at"] is not None
    orig_deadline = datetime.fromisoformat(t["repair_deadline_at"])
    confirmed_at = datetime.fromisoformat(t["confirmed_at"])
    assert orig_deadline - confirmed_at == timedelta(hours=48)

    # Regrade P4 -> P1 must NOT change repair_deadline_at (MUT-17)
    r_regrade = await api_client.post(
        f"/api/v1/tickets/{t['id']}/regrade",
        json={"priority": "P1", "reason": "人工加急但法定时限不顺延"},
        headers={**admin_hdr, "Idempotency-Key": new_idem_key("sla-reg")},
    )
    assert r_regrade.status_code == 200
    assert r_regrade.json()["priority"] == "P1"
    assert datetime.fromisoformat(r_regrade.json()["repair_deadline_at"]) == orig_deadline

    # Dispatch to tech2 (has REPAIR on ZZ-ZD-01) and backdate both repair_deadline & accept_deadline
    cands = (
        await api_client.get(f"/api/v1/tickets/{t['id']}/dispatch-candidates", headers=admin_hdr)
    ).json()["candidates"]
    t2 = next(c for c in cands if c["username"] == "tech2")
    r_d = await api_client.post(
        f"/api/v1/tickets/{t['id']}/dispatch",
        json={
            "technician_account_id": t2["account_id"],
            "scope_authorization_id": t2["scope_authorization_id"],
        },
        headers={**admin_hdr, "Idempotency-Key": new_idem_key("sla-d")},
    )
    assert r_d.status_code == 201
    did = r_d.json()["id"]

    past = datetime.now(UTC) - timedelta(hours=2)
    async with AsyncSession(async_engine, expire_on_commit=False) as s:
        await s.execute(
            text("UPDATE fault_tickets SET repair_deadline_at = :past WHERE id = :tid"),
            {"past": past, "tid": t["id"]},
        )
        await s.execute(
            text("UPDATE dispatch_records SET accept_deadline_at = :past WHERE id = :did"),
            {"past": past, "did": did},
        )
        await s.commit()

    # 1st scan: marks repair overdue + timeout reassigns dispatch
    r_scan1 = await api_client.post("/api/v1/sla/scan", headers=admin_hdr)
    assert r_scan1.status_code == 200
    s1 = r_scan1.json()
    assert s1["marked_overdue"] == 1
    assert s1["timeout_reassigned"] == 1
    assert s1["notifications_created"] >= 2

    # 2nd scan immediately after: completely idempotent -> 0 / 0 / 0!
    r_scan2 = await api_client.post("/api/v1/sla/scan", headers=admin_hdr)
    assert r_scan2.status_code == 200
    assert r_scan2.json() == {
        "marked_overdue": 0,
        "timeout_reassigned": 0,
        "notifications_created": 0,
    }

    async with AsyncSession(async_engine, expire_on_commit=False) as s:
        db_t = await s.scalar(select(FaultTicket).where(FaultTicket.id == t["id"]))
        db_d = await s.scalar(select(DispatchRecord).where(DispatchRecord.id == did))
        assert db_t is not None and db_d is not None
        assert db_t.is_sla_overdue is True
        assert db_t.overdue_stage == "REPAIR"
        assert db_t.status == "PENDING_DISPATCH"
        assert db_d.status == "TIMEOUT_REASSIGNED"

        sla_audits = (
            await s.scalars(select(AuditLog.action).where(AuditLog.ticket_id == t["id"]))
        ).all()
        assert "SLA_TIMEOUT_MARKED" in sla_audits
        assert "DISPATCH_TIMEOUT_REASSIGNED" in sla_audits
