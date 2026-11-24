"""L2 API auth, RBAC, token revocation, account status, and scope authorization tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
from httpx import AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from tests.integration.conftest import (
    DEMO_CREDENTIALS,
    auth_headers_for,
    login_user,
    new_idem_key,
)
from tomori.models.account import Account
from tomori.models.audit_log import AuditLog
from tomori.models.business_rule import BusinessRule
from tomori.models.fault_ticket import FaultTicket
from tomori.models.scope_authorization import ScopeAuthorization
from tomori.settings import get_settings


async def _table_counts(engine: AsyncEngine) -> dict[str, int]:
    async with AsyncSession(engine, expire_on_commit=False) as s:
        return {
            "accounts": int((await s.scalar(select(func.count(Account.id)))) or 0),
            "fault_tickets": int((await s.scalar(select(func.count(FaultTicket.id)))) or 0),
            "business_rules": int((await s.scalar(select(func.count(BusinessRule.id)))) or 0),
            "scope_authorizations": int(
                (await s.scalar(select(func.count(ScopeAuthorization.id)))) or 0
            ),
        }


async def test_health_endpoint_ok_without_auth(api_client: AsyncClient) -> None:
    """GET /api/v1/health succeeds without Bearer token and reports database ok."""
    resp = await api_client.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert "vision" in body
    assert resp.headers.get("x-request-id")


async def test_login_all_three_roles_and_get_auth_me(api_client: AsyncClient) -> None:
    """Valid login for ADMIN, INSPECTOR, TECHNICIAN returns JWT and `/auth/me` profile."""
    expected_roles = {
        "admin": "ADMIN",
        "inspector1": "INSPECTOR",
        "tech1": "TECHNICIAN",
    }
    for key, role_code in expected_roles.items():
        login_data = await login_user(api_client, key)
        assert login_data["token_type"] == "bearer"
        assert login_data["expires_in"] > 0
        assert login_data["account"]["role_code"] == role_code

        me_resp = await api_client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {login_data['access_token']}"},
        )
        assert me_resp.status_code == 200
        me = me_resp.json()
        assert me["account"]["username"] == DEMO_CREDENTIALS[key][0]
        assert me["role_code"] == role_code
        assert isinstance(me["scope_authorizations"], list)
        assert len(me["scope_authorizations"]) >= 1


async def test_login_failure_nonexistent_and_wrong_secret_indistinguishable(
    api_client: AsyncClient,
    async_engine: AsyncEngine,
) -> None:
    """Wrong username and wrong password both return 401 INVALID_CREDENTIALS and audit."""
    r_missing = await api_client.post(
        "/api/v1/auth/login",
        json={"username": "no_such_user", "password": "WrongPass123"},
    )
    r_wrong = await api_client.post(
        "/api/v1/auth/login",
        json={"username": "inspector1", "password": "WrongPass123"},
    )
    assert r_missing.status_code == 401
    assert r_wrong.status_code == 401
    assert r_missing.json()["error"]["code"] == "INVALID_CREDENTIALS"
    assert r_wrong.json()["error"]["code"] == "INVALID_CREDENTIALS"
    assert r_missing.json()["error"]["message"] == r_wrong.json()["error"]["message"]

    async with AsyncSession(async_engine, expire_on_commit=False) as s:
        failed_audits = (
            await s.scalars(
                select(AuditLog).where(
                    AuditLog.action == "LOGIN_FAILED",
                    AuditLog.error_code == "INVALID_CREDENTIALS",
                )
            )
        ).all()
        assert len(failed_audits) >= 2


async def test_missing_invalid_expired_and_logged_out_tokens_return_401(
    api_client: AsyncClient,
) -> None:
    """Missing/malformed/bad-signature/expired/revoked tokens map to exact 401 codes."""
    r_missing = await api_client.get("/api/v1/auth/me")
    assert r_missing.status_code == 401
    assert r_missing.json()["error"]["code"] == "TOKEN_MISSING"

    for bad_header in ("Token abc", "Bearer ", "Bearer not.a.jwt"):
        r_bad = await api_client.get(
            "/api/v1/auth/me",
            headers={"Authorization": bad_header},
        )
        assert r_bad.status_code == 401
        assert r_bad.json()["error"]["code"] == "TOKEN_INVALID"

    settings = get_settings()
    expired_payload = {
        "sub": "1",
        "tv": 1,
        "role": "ADMIN",
        "iat": int((datetime.now(UTC) - timedelta(hours=2)).timestamp()),
        "exp": int((datetime.now(UTC) - timedelta(minutes=5)).timestamp()),
    }
    expired_token = jwt.encode(
        expired_payload,
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )
    r_expired = await api_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {expired_token}"},
    )
    assert r_expired.status_code == 401
    assert r_expired.json()["error"]["code"] == "TOKEN_EXPIRED"

    # Logout bumps token_version -> previous JWT becomes 401 TOKEN_INVALID
    headers = await auth_headers_for(api_client, "inspector1")
    r_logout = await api_client.post("/api/v1/auth/logout", headers=headers)
    assert r_logout.status_code == 200
    r_after = await api_client.get("/api/v1/auth/me", headers=headers)
    assert r_after.status_code == 401
    assert r_after.json()["error"]["code"] == "TOKEN_INVALID"


async def test_disabled_account_returns_403_account_disabled_and_revokes_token(
    api_client: AsyncClient,
    async_engine: AsyncEngine,
) -> None:
    """Disabled account is blocked at login and on API access with 403 ACCOUNT_DISABLED."""
    admin_hdr = await auth_headers_for(api_client, "admin")
    insp_login = await login_user(api_client, "inspector1")
    insp_id = insp_login["account"]["id"]
    insp_hdr = {"Authorization": f"Bearer {insp_login['access_token']}"}

    # Admin disables inspector1 via PATCH -> bumps token_version AND sets status=DISABLED
    r_disable = await api_client.patch(
        f"/api/v1/accounts/{insp_id}",
        json={"status": "DISABLED"},
        headers=admin_hdr,
    )
    assert r_disable.status_code == 200
    assert r_disable.json()["status"] == "DISABLED"

    # Old JWT fails tv check -> 401 TOKEN_INVALID
    r_old_jwt = await api_client.get("/api/v1/auth/me", headers=insp_hdr)
    assert r_old_jwt.status_code == 401
    assert r_old_jwt.json()["error"]["code"] == "TOKEN_INVALID"

    # Attempting to log in while DISABLED -> 403 ACCOUNT_DISABLED
    r_login_disabled = await api_client.post(
        "/api/v1/auth/login",
        json={"username": "inspector1", "password": "Tomori#Inspect2026"},
    )
    assert r_login_disabled.status_code == 403
    assert r_login_disabled.json()["error"]["code"] == "ACCOUNT_DISABLED"

    # Also verify Layer 2 check inside get_current_principal for matching tv + non-ACTIVE status
    tech_login = await login_user(api_client, "tech1")
    tech_hdr = {"Authorization": f"Bearer {tech_login['access_token']}"}
    async with AsyncSession(async_engine, expire_on_commit=False) as s:
        await s.execute(
            text("UPDATE accounts SET status = 'DISABLED' WHERE id = :id"),
            {"id": tech_login["account"]["id"]},
        )
        await s.commit()

    r_layer2 = await api_client.get("/api/v1/auth/me", headers=tech_hdr)
    assert r_layer2.status_code == 403
    assert r_layer2.json()["error"]["code"] == "ACCOUNT_DISABLED"


async def test_three_role_rbac_403_forbidden_role_zero_dirty_write_and_audit(
    api_client: AsyncClient,
    async_engine: AsyncEngine,
) -> None:
    """Each role crossing RBAC boundaries returns 403 FORBIDDEN_ROLE with 0 DB dirty writes."""
    admin_hdr = await auth_headers_for(api_client, "admin")
    insp_hdr = await auth_headers_for(api_client, "inspector1")
    tech_hdr = await auth_headers_for(api_client, "tech1")

    # Prepare one real ticket + dispatch so dispatch/acceptance RBAC probes hit real resources
    pts = (await api_client.get("/api/v1/lighting-points", headers=admin_hdr)).json()["items"]
    js_point = next(p for p in pts if p["point_code"] == "SL-ZZ-JS-0001")
    r_ticket = await api_client.post(
        "/api/v1/tickets",
        json={
            "lighting_point_id": js_point["id"],
            "fault_type": "LAMP_OUT",
            "title": "RBAC probe ticket",
            "safety_flag": False,
        },
        headers={**insp_hdr, "Idempotency-Key": new_idem_key("rbac-t")},
    )
    assert r_ticket.status_code == 201
    ticket_id = r_ticket.json()["id"]

    cands = (
        await api_client.get(f"/api/v1/tickets/{ticket_id}/dispatch-candidates", headers=admin_hdr)
    ).json()["candidates"]
    tech1_cand = next(c for c in cands if c["username"] == "tech1")
    r_disp = await api_client.post(
        f"/api/v1/tickets/{ticket_id}/dispatch",
        json={
            "technician_account_id": tech1_cand["account_id"],
            "scope_authorization_id": tech1_cand["scope_authorization_id"],
        },
        headers={**admin_hdr, "Idempotency-Key": new_idem_key("rbac-d")},
    )
    assert r_disp.status_code == 201
    dispatch_id = r_disp.json()["id"]

    before_counts = await _table_counts(async_engine)
    async with AsyncSession(async_engine, expire_on_commit=False) as s:
        before_denied = int(
            (
                await s.scalar(
                    select(func.count(AuditLog.id)).where(
                        AuditLog.action == "FORBIDDEN_ACCESS_DENIED"
                    )
                )
            )
            or 0
        )

    # 1) INSPECTOR calling ADMIN endpoints -> 403 FORBIDDEN_ROLE
    r1 = await api_client.get("/api/v1/accounts", headers=insp_hdr)
    r2 = await api_client.post(
        "/api/v1/business-rules",
        json={
            "rule_code": "ai_candidate_ttl_hours",
            "rule_value": {"ttl_hours": 48},
            "description": "forbidden",
        },
        headers=insp_hdr,
    )
    r3 = await api_client.post(
        f"/api/v1/tickets/{ticket_id}/dispatch",
        json={
            "technician_account_id": tech1_cand["account_id"],
            "scope_authorization_id": tech1_cand["scope_authorization_id"],
        },
        headers={**insp_hdr, "Idempotency-Key": new_idem_key("rbac-x1")},
    )

    # 2) TECHNICIAN calling INSPECTOR/ADMIN endpoints -> 403 FORBIDDEN_ROLE
    r4 = await api_client.post(
        "/api/v1/tickets",
        json={
            "lighting_point_id": js_point["id"],
            "fault_type": "OCCLUDED",
            "title": "forbidden tech report",
            "safety_flag": False,
        },
        headers={**tech_hdr, "Idempotency-Key": new_idem_key("rbac-x2")},
    )
    r5 = await api_client.post(
        f"/api/v1/tickets/{ticket_id}/acceptance",
        json={"decision": "APPROVED"},
        headers={**tech_hdr, "Idempotency-Key": new_idem_key("rbac-x3")},
    )

    # 3) ADMIN calling TECHNICIAN-only dispatch accept -> 403 FORBIDDEN_ROLE
    r6 = await api_client.post(
        f"/api/v1/dispatches/{dispatch_id}/accept",
        headers={**admin_hdr, "Idempotency-Key": new_idem_key("rbac-x4")},
    )

    for resp in (r1, r2, r3, r4, r5, r6):
        assert resp.status_code == 403, f"expected 403, got {resp.status_code}: {resp.text}"
        assert resp.json()["error"]["code"] == "FORBIDDEN_ROLE"

    after_counts = await _table_counts(async_engine)
    assert after_counts == before_counts

    async with AsyncSession(async_engine, expire_on_commit=False) as s:
        after_denied = int(
            (
                await s.scalar(
                    select(func.count(AuditLog.id)).where(
                        AuditLog.action == "FORBIDDEN_ACCESS_DENIED"
                    )
                )
            )
            or 0
        )
    assert after_denied == before_denied + 6


async def test_admin_cannot_self_disable_and_scope_soft_delete_retained(
    api_client: AsyncClient,
    async_engine: AsyncEngine,
) -> None:
    """Admin cannot self-disable; DELETE /scope-authorizations/{id} soft-deactivates only."""
    admin_login = await login_user(api_client, "admin")
    admin_id = admin_login["account"]["id"]
    admin_hdr = {"Authorization": f"Bearer {admin_login['access_token']}"}

    r_self = await api_client.patch(
        f"/api/v1/accounts/{admin_id}",
        json={"status": "DISABLED"},
        headers=admin_hdr,
    )
    assert r_self.status_code == 422
    assert r_self.json()["error"]["code"] == "VALIDATION_ERROR"

    scopes = (
        await api_client.get(
            "/api/v1/scope-authorizations?is_active=true",
            headers=admin_hdr,
        )
    ).json()["items"]
    target_scope = next(s for s in scopes if s["scope_type"] == "INSPECT")

    r_del = await api_client.delete(
        f"/api/v1/scope-authorizations/{target_scope['id']}",
        headers=admin_hdr,
    )
    assert r_del.status_code == 200
    assert r_del.json()["is_active"] is False

    async with AsyncSession(async_engine, expire_on_commit=False) as s:
        db_row = await s.scalar(
            select(ScopeAuthorization).where(ScopeAuthorization.id == target_scope["id"])
        )
        assert db_row is not None
        assert db_row.is_active is False
