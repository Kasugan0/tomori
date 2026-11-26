"""`fetchAllPages` regression: >100 items must remain reachable as options.

- 105 real lighting points → the 101st item visible in inspector point select;
- injected 500 on page=2 → global error banner, no silent truncation;
- recovery → options complete again.
"""

from typing import Any

from playwright.sync_api import Page

from tests.e2e.conftest import api_login, create_lighting_point, login

_TOTAL = 105
_LAST_CODE = f"SL-E2E-PG-{_TOTAL:03d}"


def test_pagination_over_100_points_in_options_and_page2_error(
    page: Page, frontend_url: str, backend_server: str
) -> None:
    backend = backend_server
    admin_t = api_login(backend, "admin")
    for i in range(1, _TOTAL + 1):
        create_lighting_point(backend, admin_t, point_code=f"SL-E2E-PG-{i:03d}")

    login(page, frontend_url, "inspector1")
    page.wait_for_url("**/inspection/new", timeout=20_000)
    page.get_by_test_id("inspection-pole-select").click()
    page.get_by_test_id(f"inspection-pole-option-{_LAST_CODE}").wait_for(
        state="visible", timeout=20_000
    )
    page.keyboard.press("Escape")

    # 注入第 2 页失败 -> 错误可见且不得静默截断为首页
    page2_hits: list[str] = []

    def _fail_page2(route: Any) -> None:
        page2_hits.append(route.request.url)
        route.fulfill(
            status=500,
            content_type="application/json",
            body='{"error":{"code":"INJECTED_PAGE2","message":"injected"}}',
        )

    def _page2_pred(url: Any) -> bool:
        return "/api/v1/lighting-points" in url and "page=2" in url

    page.route(_page2_pred, _fail_page2)
    page.goto(f"{frontend_url}/tickets", wait_until="networkidle")
    page.goto(f"{frontend_url}/inspection/new", wait_until="networkidle")
    assert page2_hits, "page=2 请求必须命中故障注入"
    assert "injected" in page.get_by_test_id("error-banner-message").inner_text()

    # 恢复后完整选项再次可见
    page.unroute(_page2_pred, handler=_fail_page2)
    page.goto(f"{frontend_url}/tickets", wait_until="networkidle")
    page.goto(f"{frontend_url}/inspection/new", wait_until="networkidle")
    page.get_by_test_id("inspection-pole-select").click()
    page.get_by_test_id(f"inspection-pole-option-{_LAST_CODE}").wait_for(
        state="visible", timeout=20_000
    )
