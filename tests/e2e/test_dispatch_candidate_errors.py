"""Candidate errors are scoped to their active selection and session."""

import asyncio

import pytest
from playwright.async_api import Route, async_playwright

from tests.e2e.conftest import api_get, api_login, create_lighting_point
from tests.e2e.test_deep_business_flows import _create_ticket


@pytest.fixture(autouse=True)
def _artifact_capture() -> None:
    pass


@pytest.mark.parametrize("case", ["stale500", "current500", "current401"])
async def test_candidate_error_context(backend_server: str, frontend_url: str, case: str) -> None:
    admin = api_login(backend_server, "admin")
    inspector = api_login(backend_server, "inspector1")
    pa = create_lighting_point(backend_server, admin, point_code=f"SL-CERR-A-{case}")
    pb = create_lighting_point(backend_server, admin, point_code=f"SL-CERR-B-{case}")
    a = _create_ticket(
        backend_server, inspector, fault_type="OTHER", title="候选错误A", point_id=pa
    )
    b = _create_ticket(
        backend_server, inspector, fault_type="OTHER", title="候选错误B", point_id=pb
    )
    no_a = api_get(backend_server, admin, f"/api/v1/tickets/{a}").json()["ticket_no"]
    no_b = api_get(backend_server, admin, f"/api/v1/tickets/{b}").json()["ticket_no"]
    held, release, delivered = asyncio.Event(), asyncio.Event(), asyncio.Event()
    hits: list[str] = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            page = await browser.new_page()
            await page.goto(frontend_url)
            await page.evaluate(
                """t => {sessionStorage.setItem('tomori_access_token', t);
              sessionStorage.setItem('tomori_role_code', 'ADMIN');}""",
                admin,
            )
            await page.goto(f"{frontend_url}/dispatch", wait_until="networkidle")
            await page.get_by_test_id(f"dispatch-queue-row-{no_a}").click()

            async def intercept(route: Route) -> None:
                hits.append(route.request.url)
                held.set()
                await release.wait()
                await route.fulfill(
                    status=401 if case == "current401" else 500,
                    content_type="application/json",
                    body='{"error":{"code":"CANDIDATE_FAILURE","message":"候选请求注入失败"}}',
                )
                delivered.set()

            await page.route(f"**/api/v1/tickets/{a}/dispatch-candidates", intercept)
            await page.get_by_test_id("dispatch-open-btn").click()
            await asyncio.wait_for(held.wait(), 10)
            assert len(hits) == 1 and not delivered.is_set()
            if case == "stale500":
                await page.get_by_test_id(f"dispatch-queue-row-{no_b}").click()
            release.set()
            await asyncio.wait_for(delivered.wait(), 10)
            if case == "current401":
                await page.wait_for_url("**/login?**")
                assert await page.evaluate("sessionStorage.getItem('tomori_access_token')") is None
            elif case == "current500":
                await page.get_by_text("候选请求注入失败", exact=False).first.wait_for(
                    state="visible"
                )
                assert await page.evaluate("sessionStorage.getItem('tomori_access_token')") == admin
            else:
                await page.wait_for_timeout(300)
                assert await page.get_by_text("候选请求注入失败", exact=False).count() == 0
                assert await page.evaluate("sessionStorage.getItem('tomori_access_token')") == admin
                assert (
                    no_b
                    in await page.locator(".tomori-card")
                    .filter(has=page.get_by_test_id("dispatch-open-btn"))
                    .inner_text()
                )
        finally:
            await browser.close()
