"""Distinct purposes on one ticket retain separate staged upload IDs."""

from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from tests.e2e.conftest import api_get, api_login
from tests.e2e.test_async_ticket_races import _make_manual_review_on_point


@pytest.fixture(autouse=True)
def _artifact_capture() -> None:
    pass


async def test_two_purposes_coexist_on_same_ticket(
    backend_server: str, frontend_url: str, e2e_upload_files: dict[str, Path]
) -> None:
    admin = api_login(backend_server, "admin")
    tid = _make_manual_review_on_point(backend_server, "SL-E2E-PURPOSE-BOTH")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            page = await browser.new_page()
            await page.goto(frontend_url)
            await page.evaluate(
                """token => {
              sessionStorage.setItem('tomori_access_token', token);
              sessionStorage.setItem('tomori_role_code', 'ADMIN');
            }""",
                admin,
            )
            await page.goto(f"{frontend_url}/tickets/{tid}", wait_until="networkidle")
            await page.evaluate("""() => {
              const input = document.createElement('input');
              input.type = 'file'; input.id = 'purpose-test-file'; document.body.append(input);
            }""")
            await page.locator("#purpose-test-file").set_input_files(
                str(e2e_upload_files["acceptance_photo"])
            )
            await page.evaluate("""() => {
              const input = document.createElement('input');
              input.type = 'file'; input.id = 'purpose-test-file-two'; document.body.append(input);
            }""")
            await page.locator("#purpose-test-file-two").set_input_files(
                str(e2e_upload_files["repair_photo"])
            )
            result = await page.evaluate(
                """async id => {
              const {useTicketsStore} = await import('/src/stores/tickets.ts');
              const {pinia} = await import('/src/stores/pinia.ts');
              const store = useTicketsStore(pinia);
              const file = document.querySelector('#purpose-test-file').files[0];
              const a = await store.uploadEvidence(id, [file], 'ACCEPTANCE_PHOTO', 'acceptance');
              const other = document.querySelector('#purpose-test-file-two').files[0];
              const u = await store.uploadEvidence(id, [other], 'ACCEPTANCE_PHOTO', 'urge');
              return {a: a.map(x => x.id), u: u.map(x => x.id),
                acceptance: store.getUploadedEvidence(id, 'acceptance'),
                urge: store.getUploadedEvidence(id, 'urge')};
            }""",
                tid,
            )
            assert len(result["a"]) == len(result["u"]) == 1
            assert result["a"] != result["u"]
            assert result["acceptance"] == result["a"] and result["urge"] == result["u"]
            await page.reload(wait_until="networkidle")
            state = await page.evaluate(
                """async id => {
              const {useTicketsStore} = await import('/src/stores/tickets.ts');
              const {pinia} = await import('/src/stores/pinia.ts');
              const store = useTicketsStore(pinia);
              return [store.getUploadedEvidence(id, 'acceptance'),
                      store.getUploadedEvidence(id, 'urge')];
            }""",
                tid,
            )
            assert state == [[], []]
        finally:
            await browser.close()
    detail = api_get(backend_server, admin, f"/api/v1/tickets/{tid}").json()
    assert set(result["a"] + result["u"]).issubset({item["id"] for item in detail["evidences"]})
