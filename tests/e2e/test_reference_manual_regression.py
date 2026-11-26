"""Reference-only analysis can be reclassified into an auditable manual report."""

from pathlib import Path

from PIL import Image, ImageDraw
from playwright.sync_api import Page

from tests.e2e.conftest import api_get, api_login, create_lighting_point, login, select_option
from tests.e2e.test_candidate_decisions_regression import _analyze_with, _message_box_click


def test_reference_only_modified_becomes_manual_ticket(
    page: Page, frontend_url: str, backend_server: str, tmp_path: Path
) -> None:
    admin = api_login(backend_server, "admin")
    inspector = api_login(backend_server, "inspector1")
    code = "SL-E2E-REF-001"
    create_lighting_point(backend_server, admin, point_code=code)
    image = Image.new("RGB", (400, 400), (10, 10, 15))
    draw = ImageDraw.Draw(image)
    for x in range(0, 400, 10):
        draw.line((x, 0, x, 399), fill=(30, 30, 35))
    draw.rectangle((185, 185, 215, 215), fill=(255, 255, 255))
    path = tmp_path / "textured-normal.png"
    image.save(path)
    login(page, frontend_url, "inspector1")
    select_option(page, "inspection-pole-select", f"inspection-pole-option-{code}")
    body = _analyze_with(page, path)
    assert body["ticket"] is None
    evidence = body["evidences"][0]
    candidates = evidence["ai_result_json"]["candidates"]
    candidate = next(c for c in candidates if not c["is_fault_candidate"])
    index = candidates.index(candidate)
    assert page.get_by_test_id(f"inspection-candidate-confirm-{index}").is_disabled()
    page.get_by_test_id(f"inspection-candidate-edit-{index}").click()
    dialog = page.get_by_role("dialog", name="修改 AI 候选判定")
    dialog.locator(".el-select").click()
    dialog.get_by_role("option", name="灭灯 (LAMP_OUT)", exact=True).click()
    dialog.get_by_role("button", name="保存改判").click()
    page.get_by_test_id("inspection-submit-btn").click()
    with page.expect_response(
        lambda r: r.url.endswith("/api/v1/tickets") and r.request.method == "POST"
    ) as result:
        _message_box_click(page, "确认提交")
    response = result.value
    assert response.status == 201, response.text()
    request = response.request.post_data_json
    assert request is not None
    assert request["evidence_ids"] == [evidence["id"]]
    assert any(
        d["candidate_id"] == candidate["candidate_id"] and d["decision"] == "modified"
        for d in request["candidate_decisions"]
    )
    detail = api_get(backend_server, inspector, f"/api/v1/tickets/{response.json()['id']}")
    assert detail.status_code == 200, detail.text
    assert any(
        row["action"] == "TICKET_CREATED"
        and any(
            d["candidate_id"] == candidate["candidate_id"] and d["decision"] == "modified"
            for d in (row.get("business_snapshot_json") or {}).get("candidate_decisions", [])
        )
        for row in detail.json()["timeline"]
    )
