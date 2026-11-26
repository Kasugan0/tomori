"""Old authenticated responses must not disturb a newer browser session."""

import asyncio

import pytest
from playwright.async_api import Route, async_playwright

from tests.integration.conftest import DEMO_CREDENTIALS


@pytest.fixture(autouse=True)
def _artifact_capture() -> None:
    pass


@pytest.mark.parametrize("status", [401, 500, 200])
async def test_old_session_response_does_not_affect_new_login(
    frontend_url: str, status: int
) -> None:
    held = asyncio.Event()
    release = asyncio.Event()
    delivered = asyncio.Event()
    hits: list[str] = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            page = await browser.new_page()
            await page.goto(frontend_url, wait_until="networkidle")
            a_user, a_secret = DEMO_CREDENTIALS["inspector1"]
            b_user, b_secret = DEMO_CREDENTIALS["admin"]
            await page.evaluate(
                """async ([user, secret]) => {
              const {useAuthStore} = await import('/src/stores/auth.ts');
              const {pinia} = await import('/src/stores/pinia.ts');
              await useAuthStore(pinia).login(user, secret);
            }""",
                [a_user, a_secret],
            )

            async def intercept(route: Route) -> None:
                hits.append(route.request.headers.get("authorization", ""))
                held.set()
                await release.wait()
                await route.fulfill(
                    status=status,
                    content_type="application/json",
                    body='{"error":{"code":"OLD_SESSION","message":"旧会话注入错误"}}'
                    if status != 200
                    else '{"items":[],"total":0,"page":1,"page_size":100}',
                )
                delivered.set()

            await page.route("**/api/v1/tickets?race_session=1", intercept)
            await page.evaluate("""async () => {
              const {apiClient} = await import('/src/shared/api.ts');
              window.oldSessionResult = apiClient.get('/tickets?race_session=1')
                .then(() => 'resolved', () => 'rejected');
            }""")
            await asyncio.wait_for(held.wait(), 10)
            assert len(hits) == 1 and hits[0].startswith("Bearer ") and not delivered.is_set()
            await page.evaluate(
                """async ([user, secret]) => {
              const {useAuthStore} = await import('/src/stores/auth.ts');
              const {pinia} = await import('/src/stores/pinia.ts');
              const auth = useAuthStore(pinia);
              auth.clearSession();
              await auth.login(user, secret);
              await (await import('/src/router/index.ts')).router.push('/dashboard');
            }""",
                [b_user, b_secret],
            )
            token_b = await page.evaluate("sessionStorage.getItem('tomori_access_token')")
            assert token_b and hits[0] != f"Bearer {token_b}"
            release.set()
            await asyncio.wait_for(delivered.wait(), 10)
            assert await page.evaluate("window.oldSessionResult") == "rejected"
            state = await page.evaluate("""async () => {
              const {useAuthStore} = await import('/src/stores/auth.ts');
              const {pinia} = await import('/src/stores/pinia.ts');
              const auth = useAuthStore(pinia);
              return {token: auth.token, username: auth.account?.username, path: location.pathname};
            }""")
            assert state == {"token": token_b, "username": b_user, "path": "/dashboard"}
            assert await page.get_by_text("旧会话注入错误").count() == 0
        finally:
            await browser.close()


async def test_concurrent_logins_preserve_newer_account(frontend_url: str) -> None:
    held, release, delivered = asyncio.Event(), asyncio.Event(), asyncio.Event()
    hits: list[int] = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            page = await browser.new_page()
            await page.goto(frontend_url, wait_until="networkidle")

            async def intercept(route: Route) -> None:
                if hits:
                    await route.continue_()
                    return
                response = await route.fetch()
                hits.append(response.status)
                held.set()
                await release.wait()
                await route.fulfill(response=response)
                delivered.set()

            await page.route("**/api/v1/auth/login", intercept)
            await page.evaluate(
                """async ([user, secret]) => {
              const {useAuthStore} = await import('/src/stores/auth.ts');
              const {pinia} = await import('/src/stores/pinia.ts');
              window.oldLogin = useAuthStore(pinia).login(user, secret)
                .then(() => 'resolved', () => 'rejected');
            }""",
                list(DEMO_CREDENTIALS["inspector1"]),
            )
            await asyncio.wait_for(held.wait(), 10)
            assert hits == [200] and not delivered.is_set()
            await page.evaluate(
                """async ([user, secret]) => {
              const {useAuthStore} = await import('/src/stores/auth.ts');
              const {pinia} = await import('/src/stores/pinia.ts');
              await useAuthStore(pinia).login(user, secret);
              await (await import('/src/router/index.ts')).router.push('/dashboard');
            }""",
                list(DEMO_CREDENTIALS["admin"]),
            )
            token_b = await page.evaluate("sessionStorage.getItem('tomori_access_token')")
            assert token_b
            release.set()
            await asyncio.wait_for(delivered.wait(), 10)
            assert await page.evaluate("window.oldLogin") == "rejected"
            state = await page.evaluate("""async () => {
              const {useAuthStore} = await import('/src/stores/auth.ts');
              const {pinia} = await import('/src/stores/pinia.ts');
              const auth = useAuthStore(pinia);
              return {token: auth.token, username: auth.account?.username, path: location.pathname};
            }""")
            assert state == {
                "token": token_b,
                "username": DEMO_CREDENTIALS["admin"][0],
                "path": "/dashboard",
            }
        finally:
            await browser.close()


async def test_old_logout_response_cannot_clear_new_login(frontend_url: str) -> None:
    held = asyncio.Event()
    release = asyncio.Event()
    delivered = asyncio.Event()
    hits: list[int] = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
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

            async def intercept(route: Route) -> None:
                response = await route.fetch()
                hits.append(response.status)
                held.set()
                await release.wait()
                await route.fulfill(response=response)
                delivered.set()

            await page.route("**/api/v1/auth/logout", intercept)
            await page.evaluate("""async () => {
              const {useAuthStore} = await import('/src/stores/auth.ts');
              const {pinia} = await import('/src/stores/pinia.ts');
              window.oldLogout = useAuthStore(pinia).logout()
                .then(() => 'resolved', () => 'rejected');
            }""")
            await asyncio.wait_for(held.wait(), 10)
            assert hits == [200] and not delivered.is_set()
            await page.evaluate(
                """async ([user, secret]) => {
              const {useAuthStore} = await import('/src/stores/auth.ts');
              const {pinia} = await import('/src/stores/pinia.ts');
              const auth = useAuthStore(pinia);
              await auth.login(user, secret);
              await (await import('/src/router/index.ts')).router.push('/dashboard');
            }""",
                list(DEMO_CREDENTIALS["admin"]),
            )
            token_b = await page.evaluate("sessionStorage.getItem('tomori_access_token')")
            assert token_b
            release.set()
            await asyncio.wait_for(delivered.wait(), 10)
            await page.evaluate("window.oldLogout")
            state = await page.evaluate("""async () => {
              const {useAuthStore} = await import('/src/stores/auth.ts');
              const {pinia} = await import('/src/stores/pinia.ts');
              const auth = useAuthStore(pinia);
              return {token: auth.token, username: auth.account?.username, path: location.pathname};
            }""")
            assert state == {
                "token": token_b,
                "username": DEMO_CREDENTIALS["admin"][0],
                "path": "/dashboard",
            }
        finally:
            await browser.close()
