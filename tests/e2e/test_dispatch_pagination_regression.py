"""The dispatch board retrieves pending tickets beyond the first API page."""

from typing import Any

from playwright.sync_api import Page

from tests.e2e.conftest import api_get, api_login, create_lighting_point, login
from tests.e2e.test_deep_business_flows import _create_ticket


def test_dispatch_board_loads_old_ticket_beyond_first_page(
    page: Page, frontend_url: str, backend_server: str
) -> None:
    admin = api_login(backend_server, "admin")
    inspector = api_login(backend_server, "inspector1")
    first_id = None
    for index in range(101):
        point = create_lighting_point(backend_server, admin, point_code=f"SL-E2E-QUEUE-{index:03d}")
        ticket_id = _create_ticket(
            backend_server, inspector, fault_type="OTHER", title=f"分页队列 {index}", point_id=point
        )
        if first_id is None:
            first_id = ticket_id
    response = api_get(
        backend_server, admin, "/api/v1/tickets?status=PENDING_DISPATCH&page=2&page_size=100"
    )
    assert response.status_code == 200, response.text
    second = response.json()["items"]
    assert second, "must establish a real second page"
    old = next((item for item in second if item["id"] == first_id), second[0])
    login(page, frontend_url, "admin")
    page.goto(f"{frontend_url}/dispatch", wait_until="networkidle")
    page.get_by_test_id(f"dispatch-queue-row-{old['ticket_no']}").wait_for(state="visible")

    hits: list[str] = []

    def page_two(route: Any) -> None:
        hits.append(route.request.url)
        route.fulfill(
            status=500,
            content_type="application/json",
            body='{"error":{"code":"QUEUE_PAGE2_FAIL","message":"队列后页失败"}}',
        )

    def target(url: Any) -> bool:
        return "/api/v1/tickets?" in url and "page=2" in url and "status=PENDING_DISPATCH" in url

    page.route(target, page_two)
    page.reload(wait_until="networkidle")
    assert hits
    assert "队列后页失败" in page.get_by_test_id("error-banner-message").inner_text()
