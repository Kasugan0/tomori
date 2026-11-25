"""AI candidate flow E2E: real upload -> real `POST /tickets/ai-candidates` -> frozen UI anchors.

Two real backends are exercised:

* `18142` (`YOLO_INFERENCE_MODE=auto`, no custom weights) -> the OpenCV baseline runs and
  produces real candidates, so `D-30` reference boxes render as grey dashed rectangles that
  are *not* selectable as faults and the submit button stays hard-gated.
* `18143` + frontend `15175` (`YOLO_INFERENCE_MODE=strict_custom`) -> the backend refuses to
  silently degrade and returns `503 VISION_DISABLED` with
  `details.fallback.action == "MANUAL_REPORT"`; the frontend must reveal the fallback alert
  and auto-enter manual-report mode, after which a real manual ticket can still be created.

Each AI test creates its own fresh lighting point via `POST /lighting-points` so the real
`409 DUPLICATE_ACTIVE_TICKET` guard (per point + fault type) never fires across tests.

NOTE (deviation already reported to Lead): `docs/DESIGN.md:1189` names the fallback anchor
`vision-fallback-banner`, but `src/frontend/src/views/inspection/InspectionReportView.vue:660`
implements `inspection-vision-disabled` with `data-code` + `data-fallback-action`.
`src/frontend/src` is the frozen implementation, so the assertions target the real anchor.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page

from tests.e2e.conftest import (
    _localhost_client,
    api_login,
    create_lighting_point,
    login,
    select_option,
)

_FALLBACK_ANCHOR = "inspection-vision-disabled"


def test_ai_candidates_baseline_d30_reference_boxes_default_unselected(
    page: Page,
    frontend_url: str,
    backend_server: str,
    e2e_upload_files: dict[str, object],
) -> None:
    """夜间亮灯图 -> 真实基线推理 -> 灰虚线参照框默认未选中且「确认故障」disabled."""
    page.set_viewport_size({"width": 1440, "height": 900})
    admin_token = api_login(backend_server, "admin")
    point_code = "SL-E2E-D30-0001"
    create_lighting_point(backend_server, admin_token, point_code=point_code)

    login(page, frontend_url, "inspector1")
    page.wait_for_url("**/inspection/new", timeout=20_000)
    select_option(page, "inspection-pole-select", f"inspection-pole-option-{point_code}")
    page.get_by_test_id("inspection-photo-input").set_input_files(
        str(e2e_upload_files["night_normal_lit"])
    )
    # 前端 `validateStaticImageFiles` 异步解码（createImageBitmap）后才置 store.files
    page.wait_for_function(
        "() => document.querySelector('[data-testid=\"inspection-ai-analyze-btn\"]')"
        ".disabled === false",
        timeout=20_000,
    )

    with page.expect_response(
        lambda r: r.url.endswith("/api/v1/tickets/ai-candidates") and r.request.method == "POST"
    ) as analyze_info:
        page.get_by_test_id("inspection-ai-analyze-btn").click()
    analyze_response = analyze_info.value
    assert analyze_response.status in {200, 201}, analyze_response.text()
    body = analyze_response.json()

    # 真实响应契约：D-30 夜间亮灯必须是 NORMAL_LIT 且 is_fault_candidate=false
    result = body["evidences"][0]["ai_result_json"]
    assert result["inference_mode"] == "baseline_pretrained_opencv"
    assert result["image_quality"]["scene_lighting"] == "night"
    candidates = result["candidates"]
    assert candidates, "基线推理应至少返回 1 个候选框"
    ref_index = next(idx for idx, c in enumerate(candidates) if c["is_fault_candidate"] is False)
    assert candidates[ref_index]["visual_signal_state"] == "NORMAL_LIT"
    assert body["ticket"] is None, "全部为参照候选时不得建单（docs/API.md §5 step 10）"

    # 前端渲染：候选列表 + 「本次未建单」提示 + 参照框未选中
    page.get_by_test_id("inspection-candidates-list").wait_for(state="visible", timeout=20_000)
    page.get_by_test_id("inspection-no-ticket-notice").wait_for(state="visible", timeout=20_000)

    ref_card = page.get_by_test_id(f"inspection-candidate-item-{ref_index}")
    assert ref_card.get_attribute("data-is-fault-candidate") == "false"
    assert "is-ref-card" in (ref_card.get_attribute("class") or "")
    assert ref_card.get_attribute("data-decision") == "pending", "参照候选默认不得被选中"
    assert page.get_by_test_id(f"inspection-candidate-confirm-{ref_index}").is_disabled()

    # 灰虚线参照框：真实 computed style 必须是 dashed（.bbox-overlay-rect.is-reference）
    bbox = page.get_by_test_id(f"inspection-candidate-bbox-{ref_index}")
    assert bbox.count() == 1
    assert bbox.get_attribute("data-is-fault-candidate") == "false"
    assert bbox.evaluate("el => getComputedStyle(el).borderTopStyle") == "dashed"
    assert "is-reference" in (bbox.get_attribute("class") or "")

    # 未建单 + 未处置候选 -> 提交按钮必须被硬门禁禁用
    assert page.get_by_test_id("inspection-submit-btn").is_disabled()
    # 参照候选只能「修改 / 剔除」，不能直接确认
    assert page.get_by_test_id(f"inspection-candidate-edit-{ref_index}").is_enabled()
    assert page.get_by_test_id(f"inspection-candidate-reject-{ref_index}").is_enabled()


def test_vision_disabled_503_reveals_fallback_banner_and_manual_mode(
    page: Page,
    strict_vision_frontend: str,
    strict_vision_backend: str,
    e2e_upload_files: dict[str, object],
) -> None:
    """`strict_custom` 无自定义权重 -> 真实 503 VISION_DISABLED -> 兜底横幅 + 人工模式建单."""
    admin_token = api_login(strict_vision_backend, "admin")
    point_code = "SL-E2E-VIS-0001"
    create_lighting_point(strict_vision_backend, admin_token, point_code=point_code)

    page.set_viewport_size({"width": 1440, "height": 900})
    login(page, strict_vision_frontend, "inspector1")
    page.wait_for_url("**/inspection/new", timeout=20_000)
    select_option(page, "inspection-pole-select", f"inspection-pole-option-{point_code}")
    page.get_by_test_id("inspection-photo-input").set_input_files(
        str(e2e_upload_files["occluded_fault"])
    )

    with page.expect_response(
        lambda r: r.url.endswith("/api/v1/tickets/ai-candidates") and r.request.method == "POST"
    ) as analyze_info:
        page.get_by_test_id("inspection-ai-analyze-btn").click()
    error_response = analyze_info.value
    assert error_response.status == 503, error_response.text()

    # D3 兜底契约：fallback.action / resubmit_required / message_key，且不含 evidence_ids
    error_body = error_response.json()["error"]
    assert error_body["code"] == "VISION_DISABLED"
    fallback = error_body["details"]["fallback"]
    assert fallback["action"] == "MANUAL_REPORT"
    assert fallback["resubmit_required"] is True
    assert error_body["message_key"].startswith("vision.")
    assert "evidence_ids" not in error_body["details"]
    assert "evidence_ids" not in fallback

    banner = page.get_by_test_id(_FALLBACK_ANCHOR)
    banner.wait_for(state="visible", timeout=20_000)
    assert banner.get_attribute("data-code") == "VISION_DISABLED"
    assert banner.get_attribute("data-fallback-action") == "MANUAL_REPORT"
    assert "VISION_DISABLED" in banner.inner_text()

    # 兜底必须自动切到人工填报模式（提交按钮文案随之改变）
    page.wait_for_function(
        "() => document.querySelector('[data-testid=\"inspection-submit-btn\"]')"
        ".textContent.includes('提交人工报障单')",
        timeout=20_000,
    )
    page.get_by_test_id("inspection-fault-desc-input").fill("视觉识别不可用，按人工判定报障")

    submit_button = page.get_by_test_id("inspection-submit-btn")
    assert submit_button.is_enabled(), "已选点位 + 人工模式 + 已选图 -> 应允许人工提交"
    submit_button.click()
    page.get_by_role("button", name="确认提交", exact=True).click()
    page.wait_for_url(re.compile(r".*/tickets/\d+$"), timeout=30_000)
    ticket_no = page.get_by_test_id("workorder-detail-no").inner_text().strip()
    assert ticket_no.startswith("FT-"), ticket_no


def test_ai_candidates_endpoint_contract_via_direct_api(
    backend_server: str,
    e2e_upload_files: dict[str, object],
) -> None:
    """浏览器之外的独立 HTTP 证据：200 契约 + 401 TOKEN_MISSING."""
    admin_token = api_login(backend_server, "admin")
    point_code = "SL-E2E-HTTP-0001"
    point_id = create_lighting_point(backend_server, admin_token, point_code=point_code)
    token = api_login(backend_server, "inspector1")

    with (
        _localhost_client() as client,
        open(str(e2e_upload_files["night_normal_lit"]), "rb") as handle,
    ):
        resp = client.post(
            f"{backend_server}/api/v1/tickets/ai-candidates",
            headers={
                "Authorization": f"Bearer {token}",
                "Idempotency-Key": "e2e-ai-contract-0001",
            },
            data={"lighting_point_id": str(point_id), "scene_lighting_hint": "night"},
            files=[("files", ("night.png", handle.read(), "image/png"))],
        )
    assert resp.status_code in {200, 201}, resp.text
    payload = resp.json()
    assert payload["ticket"] is None
    assert payload["evidences"][0]["evidence_type"] == "INSPECTION_PHOTO"
    assert payload["evidences"][0]["ticket_id"] is None, "step 10 孤儿证据 ticket_id 必须为 NULL"

    with _localhost_client() as client:
        unauth = client.get(f"{backend_server}/api/v1/lighting-points")
    assert unauth.status_code == 401
    assert unauth.json()["error"]["code"] == "TOKEN_MISSING"

    # 幂等键格式非法 -> 真实 400（媒体必须先通过，否则会先报 415/413）
    with (
        _localhost_client() as client,
        open(str(e2e_upload_files["night_normal_lit"]), "rb") as handle,
    ):
        bad_key = client.post(
            f"{backend_server}/api/v1/tickets/ai-candidates",
            headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "short"},
            data={"lighting_point_id": str(point_id), "scene_lighting_hint": "night"},
            files=[("files", ("night.png", handle.read(), "image/png"))],
        )
    assert bad_key.status_code == 400, bad_key.text
    assert bad_key.json()["error"]["code"] == "IDEMPOTENCY_KEY_INVALID"


@pytest.mark.parametrize("scene_hint", ["night", "auto"])
def test_multi_image_upload_creates_evidence_tabs(
    page: Page,
    frontend_url: str,
    backend_server: str,
    e2e_upload_files: dict[str, object],
    scene_hint: str,
) -> None:
    """两张静态图真实上传后，Step 4 的图片切换标签与候选列表都能渲染."""
    admin_token = api_login(backend_server, "admin")
    point_code = f"SL-E2E-MULTI-{scene_hint.upper()}"
    create_lighting_point(backend_server, admin_token, point_code=point_code)

    page.set_viewport_size({"width": 1440, "height": 900})
    login(page, frontend_url, "inspector1")
    page.wait_for_url("**/inspection/new", timeout=20_000)
    select_option(page, "inspection-pole-select", f"inspection-pole-option-{point_code}")
    page.get_by_test_id("inspection-photo-input").set_input_files(
        [
            str(e2e_upload_files["night_normal_lit"]),
            str(e2e_upload_files["occluded_fault"]),
        ]
    )
    with page.expect_response(
        lambda r: r.url.endswith("/api/v1/tickets/ai-candidates") and r.request.method == "POST"
    ) as analyze_info:
        page.get_by_test_id("inspection-ai-analyze-btn").click()
    response = analyze_info.value
    assert response.status in {200, 201}, response.text()
    body = response.json()
    assert len(body["evidences"]) == 2
    assert all(ev["ai_result_json"]["inference_mode"] for ev in body["evidences"])

    page.get_by_test_id("inspection-ai-status").wait_for(state="visible", timeout=20_000)
    assert page.get_by_test_id("inspection-candidates-list").is_visible()
    tabs = page.get_by_role("button", name=re.compile(r"^第 \d+ 张$"))
    assert tabs.count() == 2, tabs.count()


def test_ai_candidates_503_anchor_exists_once_on_page(
    page: Page,
    frontend_url: str,
) -> None:
    """兜底横幅锚点在未触发时不存在，且关键锚点在页面上唯一."""
    page.set_viewport_size({"width": 1440, "height": 900})
    login(page, frontend_url, "inspector1")
    page.wait_for_url("**/inspection/new", timeout=20_000)
    assert page.get_by_test_id(_FALLBACK_ANCHOR).count() == 0
    for testid in (
        "inspection-ai-disclaimer",
        "inspection-ai-requires-confirmation",
        "inspection-photo-input",
        "inspection-pole-select",
        "inspection-manual-mode-toggle",
    ):
        assert page.get_by_test_id(testid).count() == 1, testid
