"""Auth session resilience: /auth/me 5xx keeps session + retry; 401 clears it."""

from typing import Any, cast

from playwright.sync_api import Page

from tests.e2e.conftest import login


def _me_url(url: str) -> bool:
    return "/api/v1/auth/me" in url


def _token(page: Page) -> str | None:
    return cast(str | None, page.evaluate("() => sessionStorage.getItem('tomori_access_token')"))


def test_auth_me_503_keeps_session_and_retries(
    page: Page, frontend_url: str, backend_server: str
) -> None:
    """503 on /auth/me → token/session kept, banner shown, next nav retries OK."""
    _ = backend_server
    login(page, frontend_url, "inspector1")
    page.wait_for_url("**/inspection/new", timeout=20_000)
    token = _token(page)
    assert token

    def _fail(route: Any) -> None:
        route.fulfill(
            status=503,
            content_type="application/json",
            body='{"error":{"code":"UPSTREAM_UNAVAILABLE","message":"injected"}}',
        )

    page.route(_me_url, _fail)
    page.reload()
    page.wait_for_load_state("networkidle")
    # 保持会话：仍在业务页、token 未清
    assert "/login" not in page.url
    assert _token(page) == token
    assert page.locator(".tomori-error-banner, [role='alert']").first.is_visible()

    # 恢复后下一次导航成功重试 loadMe
    page.unroute(_me_url, handler=_fail)
    with page.expect_response(lambda r: _me_url(r.url) and r.status == 200) as me_result:
        page.goto(f"{frontend_url}/tickets", wait_until="networkidle")
    me = me_result.value.json()
    assert me["account"]["username"] == "inspector1"
    assert "/login" not in page.url


def test_auth_me_401_clears_session_and_redirects_login(
    page: Page, frontend_url: str, backend_server: str
) -> None:
    """401 on /auth/me → session cleared, redirected to /login."""
    _ = backend_server
    login(page, frontend_url, "inspector1")
    page.wait_for_url("**/inspection/new", timeout=20_000)

    def _unauth(route: Any) -> None:
        route.fulfill(
            status=401,
            content_type="application/json",
            body='{"error":{"code":"AUTH_TOKEN_EXPIRED","message":"expired"}}',
        )

    page.route(_me_url, _unauth)
    page.goto(f"{frontend_url}/tickets", wait_until="networkidle")
    page.wait_for_url("**/login**", timeout=15_000)
    assert _token(page) is None
