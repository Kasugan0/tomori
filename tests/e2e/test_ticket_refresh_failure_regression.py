"""Observe ticket cancellation when its follow-up detail refresh fails."""

from typing import Any

from playwright.sync_api import Page

from tests.e2e.conftest import api_get, api_login, create_lighting_point, login
from tests.e2e.test_deep_business_flows import (
    _api_accept_dispatch,
    _api_complete_dispatch,
    _create_ticket,
    _dispatch,
)


def test_cancel_success_followed_by_detail_get_failure(
    page: Page, frontend_url: str, backend_server: str
) -> None:
    admin = api_login(backend_server, "admin")
    inspector = api_login(backend_server, "inspector1")
    point = create_lighting_point(backend_server, admin, point_code="SL-E2E-REFRESH-001")
    tid = _create_ticket(
        backend_server, inspector, fault_type="OTHER", title="刷新失败", point_id=point
    )
    login(page, frontend_url, "inspector1")
    page.goto(f"{frontend_url}/tickets/{tid}", wait_until="networkidle")
    page.get_by_test_id("workorder-cancel-btn").click()
    page.get_by_test_id("workorder-cancel-reason-input").fill("误报需要撤销")
    hits: list[str] = []
    posts: list[str] = []

    def observe_request(request: Any) -> None:
        if request.method == "POST" and request.url.endswith(f"/tickets/{tid}/cancel"):
            posts.append(request.url)

    page.on("request", observe_request)

    def fail_get(route: Any) -> None:
        if route.request.method == "GET":
            hits.append(route.request.url)
            route.fulfill(
                status=500,
                content_type="application/json",
                body='{"error":{"code":"REFRESH_FAILED","message":"刷新失败注入"}}',
            )
        else:
            route.continue_()

    pattern = f"**/api/v1/tickets/{tid}"
    page.route(pattern, fail_get)
    with page.expect_response(
        lambda r: r.url.endswith(f"/tickets/{tid}/cancel") and r.request.method == "POST"
    ) as result:
        page.get_by_test_id("workorder-cancel-confirm-btn").click()
        page.get_by_role("dialog", name="确认撤销误报").get_by_role(
            "button", name="确认撤销", exact=True
        ).click()
    assert result.value.status == 200, result.value.text()
    page.get_by_test_id("error-banner-message").wait_for(state="visible")
    assert hits
    assert "刷新失败注入" in page.get_by_test_id("error-banner-message").inner_text()
    response = api_get(backend_server, inspector, f"/api/v1/tickets/{tid}")
    assert response.status_code == 200 and response.json()["status"] == "CANCELLED"
    assert len(posts) == 1
    assert page.get_by_test_id("workorder-cancel-btn").is_disabled(), "写成功后界面仍显示可撤销"
    assert page.get_by_text("工单已撤销", exact=False).is_visible()
    assert page.get_by_text("操作已成功，但工单详情刷新失败", exact=False).is_visible()
    page.unroute(pattern, handler=fail_get)
    with page.expect_response(lambda r: r.url.endswith(f"/tickets/{tid}") and r.status == 200):
        page.get_by_role("button", name="重新加载详情").click()
    assert page.get_by_text("操作已成功，但工单详情刷新失败", exact=False).count() == 0
    assert len(posts) == 1


def test_acceptance_success_followed_by_detail_get_failure(
    page: Page, frontend_url: str, backend_server: str
) -> None:
    admin = api_login(backend_server, "admin")
    inspector = api_login(backend_server, "inspector1")
    technician = api_login(backend_server, "tech1")
    point = create_lighting_point(backend_server, admin, point_code="SL-E2E-ACCEPT-REFRESH")
    tid = _create_ticket(
        backend_server, inspector, fault_type="OTHER", title="验收刷新失败", point_id=point
    )
    did = _dispatch(backend_server, admin, tid)
    _api_accept_dispatch(backend_server, technician, did)
    _api_complete_dispatch(backend_server, technician, did)
    login(page, frontend_url, "inspector1")
    page.goto(f"{frontend_url}/tickets/{tid}", wait_until="networkidle")
    page.get_by_test_id("accept-reject-btn").click()
    page.get_by_test_id("accept-comment-input").fill("现场复核未通过")
    posts: list[str] = []
    hits: list[str] = []
    page.on(
        "request",
        lambda r: (
            posts.append(r.url)
            if r.method == "POST" and r.url.endswith(f"/tickets/{tid}/acceptance")
            else None
        ),
    )

    def fail_get(route: Any) -> None:
        if route.request.method == "GET":
            hits.append(route.request.url)
            route.fulfill(
                status=500,
                content_type="application/json",
                body='{"error":{"code":"REFRESH_FAILED","message":"验收刷新失败"}}',
            )
        else:
            route.continue_()

    page.route(f"**/api/v1/tickets/{tid}", fail_get)
    with page.expect_response(
        lambda r: r.url.endswith(f"/tickets/{tid}/acceptance") and r.request.method == "POST"
    ) as result:
        page.get_by_test_id("accept-submit-btn").click()
    assert result.value.status == 200, result.value.text()
    assert result.value.json()["ticket"]["status"] == "REWORK_PENDING_DISPATCH"
    page.get_by_test_id("error-banner-message").wait_for(state="visible")
    assert hits and len(posts) == 1
    response = api_get(backend_server, inspector, f"/api/v1/tickets/{tid}")
    assert response.status_code == 200 and response.json()["status"] == "REWORK_PENDING_DISPATCH"
    assert page.get_by_test_id("accept-submit-btn").count() == 0
    assert page.get_by_text("已驳回返修", exact=False).is_visible()
    assert page.get_by_text("操作已成功，但工单详情刷新失败", exact=False).is_visible()
    page.unroute(f"**/api/v1/tickets/{tid}", handler=fail_get)
    with page.expect_response(lambda r: r.url.endswith(f"/tickets/{tid}") and r.status == 200):
        page.get_by_role("button", name="重新加载详情").click()
    assert page.get_by_text("操作已成功，但工单详情刷新失败", exact=False).count() == 0
    assert len(posts) == 1
