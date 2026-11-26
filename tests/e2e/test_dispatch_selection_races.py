"""Dispatch selection refresh failures and late write responses."""

import asyncio

import pytest
from playwright.async_api import Route, async_playwright, expect

from tests.e2e.conftest import api_get, api_login, create_lighting_point
from tests.e2e.test_deep_business_flows import _create_ticket


@pytest.fixture(autouse=True)
def _artifact_capture() -> None:
    pass


async def test_failed_selected_detail_blocks_dispatch_until_refresh(
    backend_server: str, frontend_url: str
) -> None:
    admin = api_login(backend_server, "admin")
    inspector = api_login(backend_server, "inspector1")
    point = create_lighting_point(backend_server, admin, point_code="SL-BOARD-DETAIL-FAIL")
    tid = _create_ticket(
        backend_server, inspector, fault_type="OTHER", title="详情失败", point_id=point
    )
    ticket_no = api_get(backend_server, admin, f"/api/v1/tickets/{tid}").json()["ticket_no"]
    hits: list[str] = []
    fail = True
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            page = await browser.new_page()
            await page.goto(frontend_url)
            await page.evaluate(
                """t => { sessionStorage.setItem('tomori_access_token',t);
              sessionStorage.setItem('tomori_role_code','ADMIN'); }""",
                admin,
            )
            await page.goto(f"{frontend_url}/dispatch", wait_until="networkidle")
            await page.get_by_test_id(f"dispatch-queue-row-{ticket_no}").click()
            await page.get_by_test_id("dispatch-open-btn").wait_for(state="visible")
            await expect(page.get_by_test_id("dispatch-open-btn")).to_be_enabled()

            async def intercept(route: Route) -> None:
                if fail:
                    hits.append(route.request.url)
                    await route.fulfill(
                        status=500,
                        content_type="application/json",
                        body='{"error":{"code":"DETAIL_FAIL","message":"详情请求失败"}}',
                    )
                else:
                    await route.continue_()

            await page.route(f"**/api/v1/tickets/{tid}", intercept)
            async with (
                page.expect_response(lambda r: "/api/v1/tickets?" in r.url and r.status == 200),
                page.expect_response(
                    lambda r: r.url.endswith(f"/tickets/{tid}") and r.status == 500
                ),
            ):
                await page.get_by_role("button", name="刷新队列").click()
            assert hits
            await page.get_by_text("工单详情加载失败，已暂停派工和改派", exact=False).wait_for(
                state="visible"
            )
            await page.get_by_test_id("error-banner-message").wait_for(state="visible")
            assert await page.get_by_test_id("dispatch-open-btn").is_disabled()
            fail = False
            async with page.expect_response(
                lambda r: r.url.endswith(f"/tickets/{tid}") and r.status == 200
            ):
                await page.get_by_role("button", name="刷新队列").click()
            await page.get_by_test_id("dispatch-open-btn").wait_for(state="visible")
            await expect(page.get_by_test_id("dispatch-open-btn")).to_be_enabled()
        finally:
            await browser.close()


async def test_late_dispatch_response_does_not_reset_another_selection(
    backend_server: str, frontend_url: str
) -> None:
    admin = api_login(backend_server, "admin")
    inspector = api_login(backend_server, "inspector1")
    point_a = create_lighting_point(backend_server, admin, point_code="SL-BOARD-SWITCH-A")
    point_b = create_lighting_point(backend_server, admin, point_code="SL-BOARD-SWITCH-B")
    a = _create_ticket(
        backend_server, inspector, fault_type="OTHER", title="派工A", point_id=point_a
    )
    b = _create_ticket(
        backend_server, inspector, fault_type="OTHER", title="派工B", point_id=point_b
    )
    no_a = api_get(backend_server, admin, f"/api/v1/tickets/{a}").json()["ticket_no"]
    no_b = api_get(backend_server, admin, f"/api/v1/tickets/{b}").json()["ticket_no"]
    held, release, delivered = asyncio.Event(), asyncio.Event(), asyncio.Event()
    statuses: list[int] = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            page = await browser.new_page()
            await page.goto(frontend_url)
            await page.evaluate(
                """t => { sessionStorage.setItem('tomori_access_token',t);
              sessionStorage.setItem('tomori_role_code','ADMIN'); }""",
                admin,
            )
            await page.goto(f"{frontend_url}/dispatch", wait_until="networkidle")
            await page.get_by_test_id(f"dispatch-queue-row-{no_a}").click()
            await page.get_by_test_id("dispatch-open-btn").click()
            await page.get_by_test_id("dispatch-confirm-btn").wait_for(state="visible")

            async def intercept(route: Route) -> None:
                response = await route.fetch()
                statuses.append(response.status)
                held.set()
                await release.wait()
                await route.fulfill(response=response)
                delivered.set()

            await page.route(f"**/api/v1/tickets/{a}/dispatch", intercept)
            await page.get_by_test_id("dispatch-confirm-btn").click()
            await (
                page.get_by_role("dialog", name="确认派工")
                .get_by_role("button", name="确认", exact=True)
                .click()
            )
            await asyncio.wait_for(held.wait(), 10)
            assert statuses == [201] and not delivered.is_set()
            await page.get_by_test_id(f"dispatch-queue-row-{no_b}").click()
            await page.get_by_test_id("dispatch-open-btn").click()
            remark = page.get_by_placeholder("派工要求或现场提示")
            await remark.fill("B 表单应保留")
            release.set()
            await asyncio.wait_for(delivered.wait(), 10)
            await page.get_by_text(f"{no_a} 派工成功").wait_for(state="visible")
            await page.get_by_text("操作已成功，但列表或详情刷新失败", exact=False).wait_for(
                state="visible"
            )
            assert await remark.input_value() == "B 表单应保留"
            assert await page.get_by_test_id("dispatch-confirm-btn").is_visible()
            assert (
                no_b
                in await page.locator(".tomori-card")
                .filter(has=page.get_by_test_id("dispatch-open-btn"))
                .inner_text()
            )
        finally:
            await browser.close()
    assert api_get(backend_server, admin, f"/api/v1/tickets/{a}").json()["status"] == "DISPATCHED"
