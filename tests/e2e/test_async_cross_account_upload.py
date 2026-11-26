"""A completed upload response must not enter a later account's session."""

import asyncio
from pathlib import Path

import pytest
from playwright.async_api import Route, async_playwright

from tests.e2e.conftest import api_get, api_login, create_lighting_point
from tests.e2e.test_deep_business_flows import (
    _api_accept_dispatch,
    _api_complete_dispatch,
    _create_ticket,
    _dispatch,
)
from tests.integration.conftest import DEMO_CREDENTIALS


@pytest.fixture(autouse=True)
def _artifact_capture() -> None:
    pass


async def test_upload_response_after_account_switch(
    backend_server: str, frontend_url: str, e2e_upload_files: dict[str, Path]
) -> None:
    admin = api_login(backend_server, "admin")
    inspector = api_login(backend_server, "inspector1")
    tech = api_login(backend_server, "tech1")
    point = create_lighting_point(backend_server, admin, point_code="SL-E2E-SESSION-UPLOAD")
    tid = _create_ticket(
        backend_server, inspector, fault_type="OTHER", title="跨账户上传", point_id=point
    )
    did = _dispatch(backend_server, admin, tid)
    _api_accept_dispatch(backend_server, tech, did)
    _api_complete_dispatch(backend_server, tech, did)
    held, release, delivered = asyncio.Event(), asyncio.Event(), asyncio.Event()
    ids: list[int] = []
    other_posts: list[str] = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            page = await browser.new_page()
            await page.goto(frontend_url)
            await page.evaluate(
                """async ([user, secret]) => {
              const {useAuthStore} = await import('/src/stores/auth.ts');
              const {pinia} = await import('/src/stores/pinia.ts');
              await useAuthStore(pinia).login(user, secret);
            }""",
                list(DEMO_CREDENTIALS["inspector1"]),
            )
            await page.goto(f"{frontend_url}/tickets/{tid}", wait_until="networkidle")

            async def intercept(route: Route) -> None:
                response = await page.request.post(
                    route.request.url,
                    headers={"Authorization": route.request.headers["authorization"]},
                    multipart={
                        "evidence_type": "ACCEPTANCE_PHOTO",
                        "files": {
                            "name": "photo.jpg",
                            "mimeType": "image/jpeg",
                            "buffer": e2e_upload_files["acceptance_photo"].read_bytes(),
                        },
                    },
                )
                assert response.status in {200, 201}, await response.text()
                ids.extend(int(item["id"]) for item in await response.json())
                held.set()
                await release.wait()
                await route.fulfill(response=response)
                delivered.set()

            await page.route(f"**/api/v1/tickets/{tid}/evidences", intercept)
            page.on(
                "request",
                lambda r: (
                    other_posts.append(r.url)
                    if r.method == "POST"
                    and (r.url.endswith("/acceptance") or r.url.endswith("/review-resolve"))
                    else None
                ),
            )
            await page.locator("input[type=file][multiple]").set_input_files(
                str(e2e_upload_files["acceptance_photo"])
            )
            await page.get_by_role("button", name="立即上传", exact=False).click()
            await asyncio.wait_for(held.wait(), 10)
            assert len(ids) == 1 and not delivered.is_set()
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
            release.set()
            await asyncio.wait_for(delivered.wait(), 10)
            await page.wait_for_timeout(300)
            state = await page.evaluate(
                """async id => {
              const {useAuthStore} = await import('/src/stores/auth.ts');
              const {useTicketsStore} = await import('/src/stores/tickets.ts');
              const {pinia} = await import('/src/stores/pinia.ts');
              return {token: useAuthStore(pinia).token,
                      username: useAuthStore(pinia).account?.username,
                      evidence: useTicketsStore(pinia).getUploadedEvidence(id, 'acceptance')};
            }""",
                tid,
            )
            assert state == {
                "token": token_b,
                "username": DEMO_CREDENTIALS["admin"][0],
                "evidence": [],
            }
            assert not other_posts
        finally:
            await browser.close()
    detail = api_get(backend_server, admin, f"/api/v1/tickets/{tid}").json()
    assert ids[0] in [item["id"] for item in detail["evidences"]]
