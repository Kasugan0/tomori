"""Full lifecycle E2E: inspector reports -> admin dispatches -> tech repairs -> inspector accepts.

Every UI anchor is a frozen `data-testid` (`docs/API.md` §8, implemented in `src/frontend/src/**`).
`ElMessageBox.confirm` cannot carry a testid (`src/frontend/src` uses it directly, not wrapped), so
only confirmation buttons are located by `role=button`; see `conftest.MESSAGE_BOX_NOTE`.
The two native `<input type=file>` controls for repair/acceptance photos also carry no
testid, so they are addressed by `input[type=file]` inside the already-scoped page.

Real HTTP status codes come from `page.expect_response` against the real `uvicorn` on
`18142`; the database is the real `tomori_test` on PostgreSQL 17.12.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from playwright.sync_api import Locator, Page

from tests.e2e.conftest import confirm_message_box, login, select_option


def _file_input(page: Page) -> Locator:
    """First native file input on the page (repair / acceptance photo uploads have no testid)."""
    return page.locator('input[type="file"]').first


def test_full_workflow_report_dispatch_repair_accept_close(
    page: Page,
    frontend_url: str,
    e2e_upload_files: dict[str, Path],
) -> None:
    """inspector1 建单 -> admin 派工 tech1 -> tech1 完工 -> inspector1 验收 CLOSED."""
    page.set_viewport_size({"width": 1280, "height": 800})

    # ── 1. inspector1 人工报障（multipart + 静态图） ────────────────────────
    login(page, frontend_url, "inspector1")
    page.wait_for_url("**/inspection/new", timeout=20_000)

    page.get_by_test_id("inspection-manual-mode-toggle").click()
    select_option(
        page,
        "inspection-pole-select",
        "inspection-pole-option-SL-ZZ-JS-0001",
    )
    page.get_by_test_id("inspection-photo-input").set_input_files(
        str(e2e_upload_files["occluded_fault"])
    )
    page.get_by_test_id("inspection-fault-desc-input").fill(
        "E2E 全链路：夜间巡查发现灯具被树枝遮挡"
    )
    page.get_by_test_id("inspection-submit-btn").click()
    confirm_message_box(page, "确认提交")

    page.wait_for_url(re.compile(r".*/tickets/\d+$"), timeout=30_000)
    ticket_url = page.url
    ticket_id = int(re.search(r"/tickets/(\d+)$", ticket_url).group(1))  # type: ignore[union-attr]
    ticket_no = page.get_by_test_id("workorder-detail-no").inner_text().strip()
    assert ticket_no.startswith("FT-"), ticket_no

    # ── 2. admin 登录 -> 派工 tech1 ─────────────────────────────────────────
    page.get_by_test_id("auth-logout-btn").first.click()
    page.wait_for_url("**/login", timeout=20_000)
    login(page, frontend_url, "admin")
    page.wait_for_url("**/dashboard", timeout=20_000)
    assert page.get_by_test_id("shell-user-role").get_attribute("data-role") == "ADMIN"

    page.get_by_test_id("nav-desktop-dispatch").click()
    page.wait_for_url("**/dispatch", timeout=20_000)
    page.get_by_test_id(f"dispatch-queue-row-{ticket_no}").click()
    page.get_by_test_id("dispatch-open-btn").click()
    page.get_by_test_id("dispatch-assignee-select").click()
    page.get_by_test_id("dispatch-assignee-option-tech1").wait_for(state="visible", timeout=15_000)
    page.get_by_test_id("dispatch-assignee-option-tech1").click()

    with page.expect_response(
        lambda r: (
            re.search(r"/api/v1/tickets/\d+/dispatch$", r.url) is not None
            and r.request.method == "POST"
        )
    ) as dispatch_info:
        page.get_by_test_id("dispatch-confirm-btn").click()
        confirm_message_box(page, "确认")
    assert dispatch_info.value.status == 201, dispatch_info.value.text()
    dispatch_payload: dict[str, Any] = dispatch_info.value.json()
    dispatch_id = int(dispatch_payload["id"])
    assert dispatch_payload["status"] == "PENDING_ACCEPT"
    assert dispatch_payload["ticket_status"] == "DISPATCHED"

    # ── 3. tech1 接单 -> 六件套 -> REPAIR_PHOTO -> 完工 ─────────────────────
    page.get_by_test_id("auth-logout-btn").first.click()
    page.wait_for_url("**/login", timeout=20_000)
    login(page, frontend_url, "tech1")
    page.wait_for_url("**/repairs", timeout=20_000)
    page.get_by_test_id(f"repair-row-{dispatch_id}").click()
    page.wait_for_url(re.compile(r".*/repairs/\d+$"), timeout=20_000)

    with page.expect_response(
        lambda r: (
            r.url.endswith(f"/api/v1/dispatches/{dispatch_id}/accept")
            and r.request.method == "POST"
        )
    ) as accept_info:
        page.get_by_test_id("repair-accept-btn").click()
    accept_payload = accept_info.value
    assert accept_payload.status == 200, accept_payload.text()
    assert accept_payload.json()["status"] == "ACCEPTED"

    page.get_by_test_id("repair-arrival-checkin-btn").click()

    # 电气安全六件套：总开关 + 5 项逐条勾选（src/frontend/src 严禁默认勾选）
    page.get_by_test_id("repair-safety-checkbox").check()
    for item in (
        "repair-safety-item-power-isolated",
        "repair-safety-item-voltage-verified-zero",
        "repair-safety-item-insulation-intact",
        "repair-safety-item-grounding-connected",
        "repair-safety-item-ppe-worn",
    ):
        page.get_by_test_id(item).check()
    page.get_by_test_id("repair-safety-note-input").fill(
        "已断市电总闸并挂牌，验电笔复测零电压，绝缘手套与安全帽齐备"
    )
    page.get_by_test_id("repair-root-cause-input").fill("树枝长期遮挡导致灯具散热不良")
    page.get_by_test_id("repair-process-note-input").fill(
        "修剪遮挡树枝并清理灯罩内部积尘，复测照度达标"
    )

    page.get_by_test_id("repair-material-add-btn").click()
    page.get_by_test_id("repair-material-code-0").fill("MAT-TRIM-001")
    page.get_by_test_id("repair-material-name-0").fill("树枝修剪工时")
    page.get_by_test_id("repair-material-qty-0").fill("1")

    # 选中文件后必须显式点击「上传维修照片」按钮（change 事件只做本地校验，不自动上传）
    _file_input(page).set_input_files(str(e2e_upload_files["repair_photo"]))
    with page.expect_response(
        lambda r: (
            re.search(r"/api/v1/tickets/\d+/evidences$", r.url) is not None
            and r.request.method == "POST"
        )
    ) as repair_upload_info:
        page.get_by_role("button", name=re.compile(r"^上传维修照片")).click()
    repair_evidences = repair_upload_info.value
    assert repair_evidences.status in {200, 201}, repair_evidences.text()
    assert {ev["evidence_type"] for ev in repair_evidences.json()} == {"REPAIR_PHOTO"}

    with page.expect_response(
        lambda r: (
            r.url.endswith(f"/api/v1/dispatches/{dispatch_id}/complete")
            and r.request.method == "POST"
        )
    ) as complete_info:
        page.get_by_test_id("repair-complete-submit-btn").click()
    complete_response = complete_info.value
    assert complete_response.status == 200, complete_response.text()
    complete_body: dict[str, Any] = complete_response.json()
    assert complete_body["dispatch"]["status"] == "COMPLETED"
    assert complete_body["ticket"]["status"] == "PENDING_ACCEPTANCE"
    assert int(complete_body["ticket"]["id"]) == ticket_id
    assert complete_body["repair_process_id"] > 0
    assert len(complete_body["material_usage_ids"]) == 1

    # ── 4. inspector1 上传 ACCEPTANCE_PHOTO -> 验收通过 CLOSED ──────────────
    page.get_by_test_id("auth-logout-btn").first.click()
    page.wait_for_url("**/login", timeout=20_000)
    login(page, frontend_url, "inspector1")
    page.wait_for_url("**/inspection/new", timeout=20_000)
    page.goto(ticket_url, wait_until="networkidle")
    page.get_by_test_id("accept-evidence-compare").wait_for(state="visible", timeout=20_000)

    _file_input(page).set_input_files(str(e2e_upload_files["acceptance_photo"]))
    with page.expect_response(
        lambda r: (
            re.search(r"/api/v1/tickets/\d+/evidences$", r.url) is not None
            and r.request.method == "POST"
        )
    ) as accept_upload_info:
        page.get_by_role("button", name=re.compile(r"^立即上传")).click()
    accept_evidences = accept_upload_info.value
    assert accept_evidences.status in {200, 201}, accept_evidences.text()
    assert {ev["evidence_type"] for ev in accept_evidences.json()} == {"ACCEPTANCE_PHOTO"}

    page.get_by_test_id("accept-pass-btn").click()
    page.get_by_test_id("accept-comment-input").fill("现场复测照度达标，遮挡已清除，验收通过")

    with page.expect_response(
        lambda r: (
            r.url.endswith(f"/api/v1/tickets/{ticket_id}/acceptance") and r.request.method == "POST"
        )
    ) as acceptance_info:
        page.get_by_test_id("accept-submit-btn").click()
        confirm_message_box(page, "确认通过")
    acceptance_response = acceptance_info.value
    assert acceptance_response.status == 200, acceptance_response.text()
    acceptance_body: dict[str, Any] = acceptance_response.json()
    assert acceptance_body["decision"] == "APPROVED"
    assert acceptance_body["ticket"]["status"] == "CLOSED"
    assert acceptance_body["ticket"]["closed_at"] is not None

    page.wait_for_function("() => document.body.innerText.includes('已关闭')", timeout=30_000)
    assert page.get_by_test_id("workorder-detail-no").inner_text().strip() == ticket_no
