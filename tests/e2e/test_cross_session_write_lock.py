"""A late write must not release another session's active ticket lock."""

import asyncio

import pytest
from playwright.async_api import Route, async_playwright

from tests.e2e.conftest import api_get, api_login, create_lighting_point
from tests.e2e.test_deep_business_flows import _create_ticket
from tests.integration.conftest import DEMO_CREDENTIALS


@pytest.fixture(autouse=True)
def _artifact_capture() -> None:
    pass


async def test_old_write_cannot_release_new_write_lock(
    backend_server: str, frontend_url: str
) -> None:
    admin = api_login(backend_server, "admin")
    inspector = api_login(backend_server, "inspector1")
    point = create_lighting_point(backend_server, admin, point_code="SL-E2E-WRITE-SESS")
    tid = _create_ticket(
        backend_server, inspector, fault_type="OTHER", title="跨会话写锁", point_id=point
    )
    a_hit, a_release, a_done = asyncio.Event(), asyncio.Event(), asyncio.Event()
    b_hit, b_release, b_done = asyncio.Event(), asyncio.Event(), asyncio.Event()
    results: list[tuple[str, int]] = []
    auth_headers: dict[str, str] = {}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            page = await browser.new_page()
            await page.goto(frontend_url, wait_until="networkidle")
            await page.evaluate(
                """async ([user, secret]) => {
              const {useAuthStore} = await import('/src/stores/auth.ts');
              const {pinia} = await import('/src/stores/pinia.ts');
              await useAuthStore(pinia).login(user, secret);
            }""",
                list(DEMO_CREDENTIALS["inspector1"]),
            )

            token_a = await page.evaluate("sessionStorage.getItem('tomori_access_token')")

            async def intercept(route: Route) -> None:
                if route.request.url.endswith("/cancel"):
                    auth_headers["A"] = route.request.headers.get("authorization", "")
                    a_hit.set()
                    await a_release.wait()
                    response = await route.fetch()
                    results.append(("A", response.status))
                    await route.fulfill(response=response)
                    a_done.set()
                else:
                    auth_headers["B"] = route.request.headers.get("authorization", "")
                    response = await route.fetch()
                    results.append(("B", response.status))
                    b_hit.set()
                    await b_release.wait()
                    await route.fulfill(response=response)
                    b_done.set()

            await page.route(f"**/api/v1/tickets/{tid}/cancel", intercept)
            await page.route(f"**/api/v1/tickets/{tid}/regrade", intercept)
            await page.evaluate(
                """async id => {
              const {useTicketsStore} = await import('/src/stores/tickets.ts');
              const {pinia} = await import('/src/stores/pinia.ts');
              window.writeA = useTicketsStore(pinia).cancel(id, '旧会话撤销', crypto.randomUUID())
                .then(() => 'resolved', () => 'rejected');
            }""",
                tid,
            )
            await asyncio.wait_for(a_hit.wait(), 10)
            await page.evaluate(
                """async ([user, secret]) => {
              const {useAuthStore} = await import('/src/stores/auth.ts');
              const {pinia} = await import('/src/stores/pinia.ts');
              const auth = useAuthStore(pinia);
              auth.clearSession();
              await auth.login(user, secret);
            }""",
                list(DEMO_CREDENTIALS["admin"]),
            )
            token_b = await page.evaluate("sessionStorage.getItem('tomori_access_token')")
            await page.evaluate(
                """async id => {
              const {useTicketsStore} = await import('/src/stores/tickets.ts');
              const {pinia} = await import('/src/stores/pinia.ts');
              window.writeB = useTicketsStore(pinia).regrade(id,
                {priority: 'P1', reason: '新会话调级'}, crypto.randomUUID())
                .then(() => 'resolved', () => 'rejected');
            }""",
                tid,
            )
            await asyncio.wait_for(b_hit.wait(), 10)
            assert token_a and token_b and token_a != token_b
            assert auth_headers == {"A": f"Bearer {token_a}", "B": f"Bearer {token_b}"}
            assert results == [("B", 200)] and not a_done.is_set() and not b_done.is_set()
            a_release.set()
            await asyncio.wait_for(a_done.wait(), 10)
            assert await page.evaluate("window.writeA") == "rejected"
            locked = await page.evaluate(
                """async id => {
              const {useTicketsStore} = await import('/src/stores/tickets.ts');
              const {pinia} = await import('/src/stores/pinia.ts');
              return useTicketsStore(pinia).writingTicketIds.includes(id);
            }""",
                tid,
            )
            assert locked and not b_done.is_set()
            b_release.set()
            await asyncio.wait_for(b_done.wait(), 10)
            assert await page.evaluate("window.writeB") == "resolved"
            assert results == [("B", 200), ("A", 200)]
            assert not await page.evaluate(
                """async id => {
                  const {useTicketsStore} = await import('/src/stores/tickets.ts');
                  const {pinia} = await import('/src/stores/pinia.ts');
                  return useTicketsStore(pinia).writingTicketIds.includes(id);
                }""",
                tid,
            )
        finally:
            await browser.close()
    detail = api_get(backend_server, admin, f"/api/v1/tickets/{tid}").json()
    assert detail["status"] == "CANCELLED" and detail["priority"] == "P1"
