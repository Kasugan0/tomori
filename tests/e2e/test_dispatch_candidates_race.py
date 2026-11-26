"""A-B-A candidate fetches must not overwrite one another."""

import asyncio

import pytest
from playwright.async_api import Route, async_playwright, expect

from tests.e2e.conftest import api_get, api_login, create_lighting_point
from tests.e2e.test_deep_business_flows import _create_ticket


@pytest.fixture(autouse=True)
def _artifact_capture() -> None:
    pass


async def test_candidate_aba_old_response_does_not_clear_new_loading(
    backend_server: str, frontend_url: str
) -> None:
    admin = api_login(backend_server, "admin")
    inspector = api_login(backend_server, "inspector1")
    pa = create_lighting_point(backend_server, admin, point_code="SL-CAND-ABA-A")
    pb = create_lighting_point(backend_server, admin, point_code="SL-CAND-ABA-B")
    a = _create_ticket(backend_server, inspector, fault_type="OTHER", title="候选A", point_id=pa)
    b = _create_ticket(backend_server, inspector, fault_type="OTHER", title="候选B", point_id=pb)
    no_a = api_get(backend_server, admin, f"/api/v1/tickets/{a}").json()["ticket_no"]
    no_b = api_get(backend_server, admin, f"/api/v1/tickets/{b}").json()["ticket_no"]
    hit = [asyncio.Event(), asyncio.Event()]
    release = [asyncio.Event(), asyncio.Event()]
    delivered = [asyncio.Event(), asyncio.Event()]
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

            async def intercept(route: Route) -> None:
                idx = len(statuses)
                response = await route.fetch()
                statuses.append(response.status)
                data = await response.json()
                assert data["candidates"]
                data["candidates"][0]["full_name"] = ["旧候选标记", "新候选标记"][idx]
                hit[idx].set()
                await release[idx].wait()
                await route.fulfill(response=response, json=data)
                delivered[idx].set()

            await page.route(f"**/api/v1/tickets/{a}/dispatch-candidates", intercept)
            await page.get_by_test_id("dispatch-open-btn").click()
            await asyncio.wait_for(hit[0].wait(), 10)
            assert statuses == [200] and not delivered[0].is_set()
            await page.get_by_test_id(f"dispatch-queue-row-{no_b}").click()
            await page.get_by_test_id(f"dispatch-queue-row-{no_a}").click()
            await expect(page.get_by_test_id("dispatch-open-btn")).to_be_enabled()
            await page.get_by_test_id("dispatch-open-btn").click()
            await asyncio.wait_for(hit[1].wait(), 10)
            assert statuses == [200, 200] and not delivered[1].is_set()
            release[0].set()
            await asyncio.wait_for(delivered[0].wait(), 10)
            await expect(page.get_by_test_id("dispatch-open-btn")).to_be_disabled()
            assert "查询区域候选人中" in await page.get_by_test_id("dispatch-open-btn").inner_text()
            assert await page.get_by_text("旧候选标记", exact=False).count() == 0
            release[1].set()
            await asyncio.wait_for(delivered[1].wait(), 10)
            await page.get_by_text("新候选标记", exact=False).first.wait_for(state="visible")
            assert await page.get_by_text("旧候选标记", exact=False).count() == 0
            await expect(page.get_by_test_id("dispatch-open-btn")).to_be_enabled()
        finally:
            await browser.close()
