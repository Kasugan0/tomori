"""A-B-A candidate requests in ticket detail preserve the latest result."""

import asyncio

import pytest
from playwright.async_api import Response, Route, async_playwright

from tests.e2e.conftest import api_login, create_lighting_point
from tests.e2e.test_deep_business_flows import _create_ticket


@pytest.fixture(autouse=True)
def _artifact_capture() -> None:
    pass


async def test_ticket_candidate_aba_old_error_does_not_clear_new_request(
    backend_server: str, frontend_url: str
) -> None:
    admin = api_login(backend_server, "admin")
    inspector = api_login(backend_server, "inspector1")
    pa = create_lighting_point(backend_server, admin, point_code="SL-TC-ABA-A")
    pb = create_lighting_point(backend_server, admin, point_code="SL-TC-ABA-B")
    a = _create_ticket(backend_server, inspector, fault_type="OTHER", title="详情A", point_id=pa)
    b = _create_ticket(backend_server, inspector, fault_type="OTHER", title="详情B", point_id=pb)
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
                """t => {sessionStorage.setItem('tomori_access_token', t);
              sessionStorage.setItem('tomori_role_code', 'ADMIN');}""",
                admin,
            )
            await page.goto(f"{frontend_url}/tickets/{a}", wait_until="networkidle")

            async def intercept(route: Route) -> None:
                idx = len(statuses)
                response = await route.fetch()
                statuses.append(response.status)
                data = await response.json()
                assert data["candidates"]
                data["candidates"][0]["full_name"] = "最新详情候选"
                hit[idx].set()
                await release[idx].wait()
                if idx == 0:
                    await route.fulfill(
                        status=500,
                        content_type="application/json",
                        body='{"error":{"code":"OLD_FAIL","message":"旧详情候选错误"}}',
                    )
                else:
                    await route.fulfill(response=response, json=data)
                delivered[idx].set()

            await page.route(f"**/api/v1/tickets/{a}/dispatch-candidates", intercept)
            await page.get_by_role("button", name="立即派工").click()
            await asyncio.wait_for(hit[0].wait(), 10)
            assert statuses == [200] and not delivered[0].is_set()

            def matches_detail(response: Response) -> bool:
                return response.url.endswith(f"/tickets/{tid}") and response.status == 200

            for tid in (b, a):
                async with page.expect_response(matches_detail):
                    await page.evaluate(
                        """async id => (await import('/src/router/index.ts'))
                      .router.push(`/tickets/${id}`)""",
                        tid,
                    )
            await page.get_by_role("button", name="立即派工").click()
            await asyncio.wait_for(hit[1].wait(), 10)
            assert statuses == [200, 200] and not delivered[1].is_set()
            release[0].set()
            await asyncio.wait_for(delivered[0].wait(), 10)
            await page.get_by_text("正在查询本区域合规维修员").wait_for(state="visible")
            assert await page.get_by_text("旧详情候选错误", exact=False).count() == 0
            release[1].set()
            await asyncio.wait_for(delivered[1].wait(), 10)
            await page.get_by_text("正在查询本区域合规维修员").wait_for(state="hidden")
            await (
                page.get_by_role("dialog", name="指派区域授权维修员").locator(".el-select").click()
            )
            await page.get_by_text("最新详情候选", exact=False).first.wait_for(state="visible")
            assert await page.get_by_text("旧详情候选错误", exact=False).count() == 0
        finally:
            await browser.close()
