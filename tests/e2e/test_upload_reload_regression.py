"""Uploaded evidence persists on the server but not in staged browser state."""

from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from tests.e2e.conftest import api_get, api_login, create_lighting_point
from tests.e2e.test_deep_business_flows import (
    _api_accept_dispatch,
    _api_complete_dispatch,
    _create_ticket,
    _dispatch,
)


@pytest.fixture(autouse=True)
def _artifact_capture() -> None:
    pass


async def test_full_reload_discards_staged_acceptance_ids(
    backend_server: str, frontend_url: str, e2e_upload_files: dict[str, Path]
) -> None:
    admin = api_login(backend_server, "admin")
    inspector = api_login(backend_server, "inspector1")
    tech = api_login(backend_server, "tech1")
    point = create_lighting_point(backend_server, admin, point_code="SL-E2E-RELOAD-UPLOAD")
    tid = _create_ticket(
        backend_server, inspector, fault_type="OTHER", title="刷新暂存", point_id=point
    )
    did = _dispatch(backend_server, admin, tid)
    _api_accept_dispatch(backend_server, tech, did)
    _api_complete_dispatch(backend_server, tech, did)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            page = await browser.new_page()
            await page.goto(frontend_url)
            await page.evaluate(
                """token => {
              sessionStorage.setItem('tomori_access_token', token);
              sessionStorage.setItem('tomori_role_code', 'INSPECTOR');
            }""",
                inspector,
            )
            await page.goto(f"{frontend_url}/tickets/{tid}", wait_until="networkidle")
            await page.locator("input[type=file][multiple]").set_input_files(
                str(e2e_upload_files["acceptance_photo"])
            )
            async with page.expect_response(
                lambda r: r.url.endswith(f"/tickets/{tid}/evidences") and r.request.method == "POST"
            ) as info:
                await page.get_by_role("button", name="立即上传", exact=False).click()
            response = await info.value
            assert response.status in {200, 201}
            uploaded = await response.json()
            assert len(uploaded) == 1
            notice = page.get_by_text("本会话已上传待绑定验收照片 1 张", exact=False)
            await notice.wait_for(state="visible")
            assert "页面刷新后暂存不保留" in await notice.inner_text()
            await page.reload(wait_until="networkidle")
            assert await notice.count() == 0
            staged = await page.evaluate(
                """async id => {
              const {useTicketsStore} = await import('/src/stores/tickets.ts');
              const {pinia} = await import('/src/stores/pinia.ts');
              return useTicketsStore(pinia).getUploadedEvidence(id, 'acceptance');
            }""",
                tid,
            )
            assert staged == []
        finally:
            await browser.close()
    detail = api_get(backend_server, inspector, f"/api/v1/tickets/{tid}").json()
    assert uploaded[0]["id"] in [item["id"] for item in detail["evidences"]]
