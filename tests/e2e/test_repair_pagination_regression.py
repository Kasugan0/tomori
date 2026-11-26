"""Technician workbench loads historical dispatches beyond the first page."""

from typing import Any

from playwright.sync_api import Page

from tests.e2e.conftest import api_get, api_login, create_lighting_point, login
from tests.e2e.test_deep_business_flows import _api_reject_dispatch, _create_ticket, _dispatch


def test_repair_workbench_loads_second_page_and_reports_failure(
    page: Page, frontend_url: str, backend_server: str
) -> None:
    admin = api_login(backend_server, "admin")
    inspector = api_login(backend_server, "inspector1")
    tech = api_login(backend_server, "tech1")
    first = None
    for i in range(101):
        point = create_lighting_point(backend_server, admin, point_code=f"SL-E2E-TODO-{i:03d}")
        ticket = _create_ticket(
            backend_server, inspector, fault_type="OTHER", title=f"待办分页{i}", point_id=point
        )
        dispatch = _dispatch(backend_server, admin, ticket)
        if first is None:
            first = dispatch
        _api_reject_dispatch(backend_server, tech, dispatch, "当前无法处理")

    second = api_get(backend_server, tech, "/api/v1/dispatches?page=2&page_size=100")
    assert second.status_code == 200, second.text
    assert any(item["id"] == first for item in second.json()["items"])
    login(page, frontend_url, "tech1")
    page.goto(f"{frontend_url}/repairs", wait_until="networkidle")
    page.get_by_test_id(f"repair-row-{first}").wait_for(state="visible")
    hits: list[str] = []

    def fail(route: Any) -> None:
        hits.append(route.request.url)
        route.fulfill(
            status=500,
            content_type="application/json",
            body='{"error":{"code":"TODO_PAGE_ERROR","message":"待办后页错误"}}',
        )

    def match(url: Any) -> bool:
        return "/api/v1/dispatches?" in url and "page=2" in url

    page.route(match, fail)
    page.reload(wait_until="networkidle")
    assert hits
    assert "待办后页错误" in page.get_by_test_id("error-banner-message").inner_text()
