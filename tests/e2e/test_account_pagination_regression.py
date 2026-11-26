"""Account filter loads later pages and reports a later-page failure."""

from typing import Any

from playwright.sync_api import Page

from tests.e2e.conftest import _localhost_client, api_get, api_login, login


def test_account_second_page_option_and_failure(
    page: Page, frontend_url: str, backend_server: str
) -> None:
    admin = api_login(backend_server, "admin")
    with _localhost_client() as client:
        for i in range(101):
            response = client.post(
                f"{backend_server}/api/v1/accounts",
                headers={"Authorization": f"Bearer {admin}"},
                json={
                    "username": f"page_test_{i:03d}",
                    "password": "PageTestPassword123!",
                    "full_name": f"分页账号{i}",
                    "role_code": "INSPECTOR",
                },
            )
            assert response.status_code == 201, response.text
    second = api_get(backend_server, admin, "/api/v1/accounts?page=2&page_size=100")
    assert second.status_code == 200 and second.json()["items"]
    account = second.json()["items"][0]
    login(page, frontend_url, "admin")
    page.goto(f"{frontend_url}/audit", wait_until="networkidle")
    page.locator(".filter-row .el-select").first.click()
    page.get_by_role("option", name=f"{account['full_name']} ({account['username']})").wait_for(
        state="visible"
    )
    hits: list[str] = []

    def fail(route: Any) -> None:
        hits.append(route.request.url)
        route.fulfill(
            status=500,
            content_type="application/json",
            body='{"error":{"code":"ACCOUNT_PAGE_ERROR","message":"账号后页错误"}}',
        )

    def match(url: Any) -> bool:
        return "/api/v1/accounts?" in url and "page=2" in url

    page.route(match, fail)
    page.reload(wait_until="networkidle")
    assert hits
    assert "账号后页错误" in page.get_by_test_id("error-banner-message").inner_text()
