"""Three-role isolation E2E: route guards, nav visibility and API-level 403 enforcement.

All anchors are frozen `data-testid`s. The `router.beforeEach` guard in
`src/frontend/src/router/index.ts` redirects authenticated users without the required role to
`/403` (`forbidden-page`), while the backend independently returns
`403 FORBIDDEN_ROLE` — both layers are asserted so a UI-only regression cannot pass.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from playwright.sync_api import Page

from tests.e2e.conftest import (
    _localhost_client,
    api_get,
    api_login,
    api_post,
    create_lighting_point,
    login,
    logout,
)


def _post_idem(backend_url: str, token: str, path: str, json_body: dict[str, Any]) -> Any:
    """Authenticated POST with a fresh Idempotency-Key (production contract)."""
    import uuid

    with _localhost_client() as client:
        return client.post(
            f"{backend_url}{path}",
            headers={
                "Authorization": f"Bearer {token}",
                "Idempotency-Key": f"e2e-{uuid.uuid4().hex[:32]}",
            },
            json=json_body,
        )


def test_inspector_cannot_reach_admin_routes_and_sees_no_admin_nav(
    page: Page,
    frontend_url: str,
    backend_server: str,
) -> None:
    """inspector1 访问 /admin/*、/dashboard、/dispatch 均被重定向 /403，且侧栏无 ADMIN 入口."""
    _ = backend_server
    page.set_viewport_size({"width": 1280, "height": 800})
    login(page, frontend_url, "inspector1")
    page.wait_for_url("**/inspection/new", timeout=20_000)
    assert page.get_by_test_id("shell-user-role").get_attribute("data-role") == "INSPECTOR"

    # INSPECTOR 可见的桌面导航：巡查报障 / 故障工单 / 站内通知
    for testid in ("nav-desktop-inspection", "nav-desktop-workorders", "nav-desktop-notifications"):
        assert page.get_by_test_id(testid).count() == 1, testid
    # ADMIN 专属入口必须完全不存在（v-for + roles 过滤后不渲染）
    for testid in (
        "nav-desktop-dashboard",
        "nav-desktop-dispatch",
        "nav-desktop-rules",
        "nav-desktop-regions",
        "nav-desktop-points",
        "nav-desktop-assets",
        "nav-desktop-scopes",
        "nav-desktop-accounts",
        "nav-desktop-audit",
    ):
        assert page.get_by_test_id(testid).count() == 0, f"inspector 不应看到 {testid}"

    for admin_path in ("/dashboard", "/admin/accounts", "/admin/rules", "/dispatch", "/audit"):
        page.goto(f"{frontend_url}{admin_path}", wait_until="networkidle")
        page.wait_for_url(re.compile(r".*/403\?from="), timeout=20_000)
        assert page.get_by_test_id("forbidden-page").is_visible()
        page.get_by_test_id("forbidden-back-btn").click()
        page.wait_for_url("**/inspection/new", timeout=20_000)

    # 后端独立守门：越权 API 仍返回 403 FORBIDDEN_ROLE（不因前端隐藏而降级）
    token = api_login(backend_server, "inspector1")
    response = api_get(backend_server, token, "/api/v1/accounts")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN_ROLE"


def test_technician_sees_only_repair_workbench_no_dispatch_board(
    page: Page,
    frontend_url: str,
    backend_server: str,
) -> None:
    """tech1 只有 /repairs，看不到派工看板/工单列表/管理台，且后端 403 一致."""
    _ = backend_server
    page.set_viewport_size({"width": 1280, "height": 800})
    login(page, frontend_url, "tech1")
    page.wait_for_url("**/repairs", timeout=20_000)
    assert page.get_by_test_id("shell-user-role").get_attribute("data-role") == "TECHNICIAN"

    assert page.get_by_test_id("nav-desktop-repairs").count() == 1
    assert page.get_by_test_id("nav-desktop-notifications").count() == 1
    for testid in (
        "nav-desktop-dashboard",
        "nav-desktop-dispatch",
        "nav-desktop-workorders",
        "nav-desktop-inspection",
        "nav-desktop-audit",
    ):
        assert page.get_by_test_id(testid).count() == 0, f"tech1 不应看到 {testid}"

    for forbidden_path in ("/dispatch", "/tickets", "/dashboard", "/inspection/new"):
        page.goto(f"{frontend_url}{forbidden_path}", wait_until="networkidle")
        page.wait_for_url(re.compile(r".*/403\?from="), timeout=20_000)
        assert page.get_by_test_id("forbidden-page").is_visible()

    token = api_login(backend_server, "tech1")
    # `GET /tickets` 对 TECHNICIAN 是刻意放开的（按 REPAIR 授权区域 + 已派工范围收窄，
    # 见 tickets.py:482-505），因此这里断言的是真正仅限 ADMIN/INSPECTOR 的入口。
    sla_response = api_post(backend_server, token, "/api/v1/sla/scan")
    assert sla_response.status_code == 403
    assert sla_response.json()["error"]["code"] == "FORBIDDEN_ROLE"

    candidates_response = api_get(backend_server, token, "/api/v1/tickets/1/dispatch-candidates")
    assert candidates_response.status_code == 403
    assert candidates_response.json()["error"]["code"] == "FORBIDDEN_ROLE"

    accounts_response = api_get(backend_server, token, "/api/v1/accounts")
    assert accounts_response.status_code == 403
    assert accounts_response.json()["error"]["code"] == "FORBIDDEN_ROLE"

    own_dispatch_response = api_get(backend_server, token, "/api/v1/dispatches?page_size=5")
    assert own_dispatch_response.status_code == 200
    me = api_get(backend_server, token, "/api/v1/auth/me")
    assert me.status_code == 200
    my_account_id = int(me.json()["account"]["id"])
    own_items = own_dispatch_response.json()["items"]
    if not own_items:
        # Baseline has no dispatch for tech1 yet — create one via production path
        # to make the ownership assertion meaningful (real business side effect).
        admin_token = api_login(backend_server, "admin")
        insp_token = api_login(backend_server, "inspector1")
        pt_id = create_lighting_point(backend_server, admin_token, point_code="SL-E2E-ISO-001")
        r_ticket = _post_idem(
            backend_server,
            insp_token,
            "/api/v1/tickets",
            {
                "lighting_point_id": pt_id,
                "fault_type": "LAMP_OUT",
                "title": "E2E 角色隔离工单",
                "safety_flag": False,
            },
        )
        assert r_ticket.status_code == 201, r_ticket.text
        tid = int(r_ticket.json()["id"])
        cands = api_get(backend_server, admin_token, f"/api/v1/tickets/{tid}/dispatch-candidates")
        cand = next(c for c in cands.json()["candidates"] if c["username"] == "tech1")
        r_disp = _post_idem(
            backend_server,
            admin_token,
            f"/api/v1/tickets/{tid}/dispatch",
            {
                "technician_account_id": cand["account_id"],
                "scope_authorization_id": cand["scope_authorization_id"],
            },
        )
        assert r_disp.status_code == 201, r_disp.text
        # Re-query own dispatches after creating the real one
        own_dispatch_response = api_get(backend_server, token, "/api/v1/dispatches?page_size=5")
        own_items = own_dispatch_response.json()["items"]
        assert own_items, "生产路径建单派工后 tech1 必须有可见派工"
    assert all(item["technician_account_id"] == my_account_id for item in own_items), (
        "维修员只能看到自己的派工单"
    )


def test_admin_sees_every_nav_entry_and_other_role_pages(
    page: Page,
    frontend_url: str,
    backend_server: str,
) -> None:
    """admin 可见全部 ADMIN 入口 + 通知中心，可访工单列表；技术员专属页被 403 拦截."""
    _ = backend_server
    page.set_viewport_size({"width": 1920, "height": 1080})
    login(page, frontend_url, "admin")
    page.wait_for_url("**/dashboard", timeout=20_000)
    assert page.get_by_test_id("shell-user-role").get_attribute("data-role") == "ADMIN"

    for testid in (
        "nav-desktop-dispatch",
        "nav-desktop-workorders",
        "nav-desktop-rules",
        "nav-desktop-regions",
        "nav-desktop-points",
        "nav-desktop-assets",
        "nav-desktop-scopes",
        "nav-desktop-accounts",
        "nav-desktop-audit",
        "nav-desktop-notifications",
    ):
        assert page.get_by_test_id(testid).count() == 1, f"admin 应看到 {testid}"
    # ADMIN 不渲染 dashboard 专用 desktopTestId（DashboardView 无该 nav 项）
    assert page.get_by_test_id("nav-desktop-inspection").count() == 0
    assert page.get_by_test_id("nav-desktop-repairs").count() == 0

    # admin 可打开工单列表（ADMIN+INSPECTOR 共有）
    page.get_by_test_id("nav-desktop-workorders").click()
    page.wait_for_url("**/tickets", timeout=20_000)

    # 技术员专属 /repairs 对 admin 是 403（actions/RULES 与 router meta 双重限制）
    page.goto(f"{frontend_url}/repairs", wait_until="networkidle")
    page.wait_for_url(re.compile(r".*/403\?from="), timeout=20_000)
    assert page.get_by_test_id("forbidden-page").is_visible()

    token = api_login(backend_server, "admin")
    admin_response = api_get(backend_server, token, "/api/v1/accounts?page_size=5")
    assert admin_response.status_code == 200

    logout(page)
    page.wait_for_url("**/login", timeout=20_000)
    assert page.get_by_test_id("auth-username-input").is_visible()


@pytest.mark.parametrize(
    ("account_key", "home_path", "role_code"),
    [
        ("admin", "/dashboard", "ADMIN"),
        ("inspector1", "/inspection/new", "INSPECTOR"),
        ("tech1", "/repairs", "TECHNICIAN"),
    ],
)
def test_role_home_redirect_after_login(
    page: Page,
    frontend_url: str,
    account_key: str,
    home_path: str,
    role_code: str,
) -> None:
    """登录后 `defaultHomeRoute` 必须把三种角色分别送到各自首页."""
    page.set_viewport_size({"width": 1280, "height": 800})
    login(page, frontend_url, account_key)
    page.wait_for_url(f"**{home_path}", timeout=20_000)
    assert page.get_by_test_id("shell-user-role").get_attribute("data-role") == role_code
    assert page.url.endswith(home_path)
