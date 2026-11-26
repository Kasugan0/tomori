"""Asset selection loads real later pages and surfaces later page errors."""

from typing import Any

from playwright.sync_api import Page

from tests.e2e.conftest import (
    _localhost_client,
    api_get,
    api_login,
    create_lighting_point,
    login,
    select_option,
)


def test_asset_second_page_option_and_failure(
    page: Page, frontend_url: str, backend_server: str
) -> None:
    admin = api_login(backend_server, "admin")
    code = "SL-E2E-ASSET-PAGE"
    point = create_lighting_point(backend_server, admin, point_code=code)
    with _localhost_client() as client:
        for i in range(101):
            response = client.post(
                f"{backend_server}/api/v1/facility-assets",
                headers={"Authorization": f"Bearer {admin}"},
                json={
                    "asset_code": f"PAGE-ASSET-{i:03d}",
                    "lighting_point_id": point,
                    "asset_type": "LUMINAIRE",
                    "name": f"分页资产 {i}",
                    "importance": "NORMAL",
                },
            )
            assert response.status_code == 201, response.text
    second = api_get(backend_server, admin, "/api/v1/facility-assets?page=2&page_size=100")
    assert second.status_code == 200 and second.json()["items"]
    target = next(a for a in second.json()["items"] if a["lighting_point_id"] == point)
    login(page, frontend_url, "inspector1")
    select_option(page, "inspection-pole-select", f"inspection-pole-option-{code}")
    select = page.get_by_text("关联设施资产（可选）").locator("..").locator(".el-select")
    select.click()
    page.get_by_role("option", name=f"{target['asset_code']} · {target['name']}").wait_for(
        state="visible"
    )
    hits: list[str] = []

    def fail(route: Any) -> None:
        hits.append(route.request.url)
        route.fulfill(
            status=500,
            content_type="application/json",
            body='{"error":{"code":"ASSET_PAGE_ERROR","message":"资产后页错误"}}',
        )

    def match(url: Any) -> bool:
        return "/api/v1/facility-assets?" in url and "page=2" in url

    page.route(match, fail)
    page.reload(wait_until="networkidle")
    assert hits
    assert "资产后页错误" in page.get_by_test_id("error-banner-message").inner_text()
