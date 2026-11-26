"""Candidate-decision regression E2E: reject-all gate + point-switch discard / cancel restore.

Contract coordinates verified against `src/frontend/src` (frontend-auditor, task-23):

- Reject-all gate: `stores/inspection.ts:123-127` `allFaultCandidatesRejected` ->
  `InspectionReportView.vue:186-194` `canSubmit` returns false and
  `InspectionReportView.vue:203-210` `submitGateHint` surfaces
  「已剔除全部 AI 故障候选，草稿不可确认…」; `stores/inspection.ts:242-246`
  `submitConfirmedOrManual` throws `ALL_CANDIDATES_REJECTED` *before* `confirmTicketApi`
  (`api/tickets.ts:151-157` `POST /tickets/{id}/confirm`), so no confirm request may leave
  the browser. MVP boundary: candidate decisions live only in the pinia store — there is no
  decisions endpoint, so reject/confirm clicks must produce zero backend writes.
- Point switch: `InspectionReportView.vue:283-307` `handlePointChange` intercepts a point
  change once `hasAnalyzed` and shows ElMessageBox「清除本页结果并更换 / 取消」; confirm ->
  `stores/inspection.ts:293-299` `discardAnalysis()` clears evidences / draftTicket /
  decisions / hasAnalyzed / visionFallback while keeping the selected `files`; cancel ->
  restores `store.lightingPointId = previousPointId` leaving candidates and submit state
  untouched.
- `ElMessageBox` renders outside the Vue app and carries no testid (`conftest.MESSAGE_BOX_NOTE`),
  so dialog buttons are located by role+name inside `.el-message-box`.

Each test creates fresh lighting points via `POST /lighting-points` so the real
`409 DUPLICATE_ACTIVE_TICKET` guard never fires across tests; the upload is a textured
variant of `occluded_fault` (see `_textured_occluded_fault`) that goes through the real
baseline pipeline and returns `201` with an `OCCLUDED` fault candidate plus an
`AI_CANDIDATE_PENDING` draft ticket (same input contract as `test_api_tickets.py` step 11).
"""

from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw
from playwright.sync_api import Locator, Page, Request, expect

from tests.e2e.conftest import api_login, create_lighting_point, login, select_option

_CONFIRM_RE = re.compile(r"/api/v1/tickets/(\d+)/confirm$")
_ANALYZE_PATH = "/api/v1/tickets/ai-candidates"
_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _fault_indexes(analyze_body: dict[str, Any]) -> list[int]:
    """flatIndex of every `is_fault_candidate` (single evidence -> index == flatIndex)."""
    assert len(analyze_body["evidences"]) == 1, "本回归固定上传 1 张图，flatIndex 与候选索引一致"
    candidates = analyze_body["evidences"][0]["ai_result_json"]["candidates"]
    return [idx for idx, c in enumerate(candidates) if c["is_fault_candidate"] is True]


def _candidate_items(page: Page) -> Locator:
    return page.locator("[data-testid^='inspection-candidate-item-']")


def _textured_occluded_fault(tmp_path: Path) -> Path:
    """`occluded_fault` 变体：底色改为有噪声的暗街景，避免整图欠曝被判 quality_grade=fail。

    `tests/integration/conftest.py` 的 `make_occluded_fault_png` 是纯色背景 + 单矩形，
    其拉普拉斯方差贴着 `src/backend/tomori/vision/config.py` LAPLACIAN_BLUR_FAIL=20 边界，
    一旦被判 `quality_grade=fail`，`hasQualityFail` 会禁用确认/剔除按钮，阻断本回归必须的
    UI 决策交互。保持同样的中心绿植块（streetlight_occluded 语义不变），补暗网格与噪点
    纹理把拉普拉斯方差稳定抬过阈值，背景 V 通道仍 > UNDEREXPOSED_V 判定的欠曝线。
    """
    img = Image.new("RGB", (400, 400), (10, 10, 15))
    draw = ImageDraw.Draw(img)
    # 以灰色细线和噪点构成街道纹理。
    for x in range(0, 400, 10):
        draw.line((x, 0, x, 399), fill=(30, 30, 35), width=1)
    for y in range(0, 400, 10):
        draw.line((0, y, 399, y), fill=(25, 25, 30), width=1)
    for x in range(5, 400, 20):
        for y in range(5, 400, 20):
            draw.point((x, y), fill=(90, 90, 100))
    draw.rectangle((180, 180, 220, 220), fill=(0, 140, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    out = tmp_path / "occluded_fault_textured.png"
    out.write_bytes(buf.getvalue())
    return out


def _analyze_with(page: Page, file_path: Path) -> dict[str, Any]:
    """Upload a single file and run analyze; returns response body."""
    page.get_by_test_id("inspection-photo-input").set_input_files(str(file_path))
    body = _analyze_once(page)
    return body


def _analyze_once(page: Page) -> dict[str, Any]:
    """Wait for the async image decode gate, click analyze, return the real response body."""
    # `validateStaticImageFiles` 异步解码完成后才置 store.files，按钮随之解除禁用
    page.wait_for_function(
        "() => document.querySelector('[data-testid=\"inspection-ai-analyze-btn\"]')"
        ".disabled === false",
        timeout=20_000,
    )
    with page.expect_response(
        lambda r: r.url.endswith(_ANALYZE_PATH) and r.request.method == "POST"
    ) as analyze_info:
        page.get_by_test_id("inspection-ai-analyze-btn").click()
    response = analyze_info.value
    assert response.status in {200, 201}, response.text()
    body: dict[str, Any] = response.json()
    assert body["evidences"][0]["ai_result_json"]["image_quality"]["quality_grade"] != "fail"
    return body


def _message_box_click(page: Page, name: str) -> None:
    """Click an `ElMessageBox` button by role+name, then wait for the box to fully close.

    `confirm_message_box` 只点击不等待；本文件存在「连续弹窗 -> 再点页面按钮」的序列，
    必须等 `.el-message-box` 隐藏，否则遮罩会拦截下一次真实点击。
    """
    box = page.locator(".el-message-box")
    box.get_by_role("button", name=name, exact=True).click()
    box.wait_for(state="hidden", timeout=10_000)


def test_reject_all_fault_candidates_disables_submit_and_never_posts_confirm(
    page: Page,
    frontend_url: str,
    backend_server: str,
    tmp_path: Path,
) -> None:
    """剔除全部故障候选后禁止提交，显示提示且无后端写请求。"""
    page.set_viewport_size({"width": 1440, "height": 900})

    # 契约「不发 /tickets/{id}/confirm」与「decisions 仅存 store」：自分析响应落地后
    # 记录全部 API 写请求；剔除/确认决策只允许改 pinia store，不得产生任何后端写。
    tracking = {"on": False}
    confirm_requests: list[str] = []
    write_requests: list[str] = []

    def _watch(req: Request) -> None:
        if not tracking["on"] or "/api/v1/" not in req.url:
            return
        if _CONFIRM_RE.search(req.url) and req.method == "POST":
            confirm_requests.append(req.url)
        if req.method in _WRITE_METHODS:
            write_requests.append(f"{req.method} {req.url}")

    page.on("request", _watch)

    admin_token = api_login(backend_server, "admin")
    point_code = "SL-E2E-REJ-0001"
    create_lighting_point(backend_server, admin_token, point_code=point_code)

    login(page, frontend_url, "inspector1")
    page.wait_for_url("**/inspection/new", timeout=20_000)
    select_option(page, "inspection-pole-select", f"inspection-pole-option-{point_code}")

    body = _analyze_with(page, _textured_occluded_fault(tmp_path))
    tracking["on"] = True
    assert body["ticket"] is not None, "occluded_fault 必须产出 AI_CANDIDATE_PENDING 草稿"
    assert body["ticket"]["status"] == "AI_CANDIDATE_PENDING"
    fault_indexes = _fault_indexes(body)
    assert fault_indexes, "真实基线推理应至少检出 1 条故障候选"

    page.get_by_test_id("inspection-candidates-list").wait_for(state="visible", timeout=20_000)
    for idx in fault_indexes:
        page.get_by_test_id(f"inspection-candidate-reject-{idx}").click()
        _message_box_click(page, "确认剔除")
        expect(page.get_by_test_id(f"inspection-candidate-item-{idx}")).to_have_attribute(
            "data-decision", "rejected", timeout=10_000
        )

    # InspectionReportView.vue:186-194 canSubmit 硬禁 + :203-210 已剔除全部提示
    expect(page.get_by_test_id("inspection-submit-btn")).to_be_disabled()
    expect(
        page.get_by_text("已剔除全部故障候选，请重新分析、人工报障，或将参考候选改判为故障")
    ).to_be_visible()

    # 对禁用按钮派发合成 click（dispatchEvent 不受 disabled 用户交互拦截）：
    # handleSubmit 因 canSubmit=false 早退；即便进入 store 也会被 ALL_CANDIDATES_REJECTED 拦截。
    page.get_by_test_id("inspection-submit-btn").dispatch_event("click")
    page.wait_for_timeout(500)  # 有界观测窗口：给任何错误放行的 confirm 请求一次被抓到的机会
    assert page.locator(".el-message-box:visible").count() == 0
    assert confirm_requests == [], f"不应发出 POST /tickets/{{id}}/confirm：{confirm_requests}"
    assert write_requests == [], (
        f"决策仅存 store（MVP 边界）：剔除动作不应产生后端写请求：{write_requests}"
    )


def test_point_switch_confirmed_discards_draft_and_confirm_targets_new_draft(
    page: Page,
    frontend_url: str,
    backend_server: str,
    tmp_path: Path,
) -> None:
    """切换点位作废 A 草稿，保留文件并重新分析，提交确认 B 草稿。"""
    page.set_viewport_size({"width": 1440, "height": 900})
    admin_token = api_login(backend_server, "admin")
    point_a_code, point_b_code = "SL-E2E-SWA-001", "SL-E2E-SWB-001"
    point_a_id = create_lighting_point(backend_server, admin_token, point_code=point_a_code)
    point_b_id = create_lighting_point(backend_server, admin_token, point_code=point_b_code)

    login(page, frontend_url, "inspector1")
    page.wait_for_url("**/inspection/new", timeout=20_000)
    select_option(page, "inspection-pole-select", f"inspection-pole-option-{point_a_code}")
    body_a = _analyze_with(page, _textured_occluded_fault(tmp_path))
    assert body_a["ticket"] is not None
    assert body_a["ticket"]["lighting_point_id"] == point_a_id
    draft_a_id = int(body_a["ticket"]["id"])
    fault_indexes_a = _fault_indexes(body_a)
    assert fault_indexes_a
    for idx in fault_indexes_a:
        page.get_by_test_id(f"inspection-candidate-confirm-{idx}").click()
        expect(page.get_by_test_id(f"inspection-candidate-item-{idx}")).to_have_attribute(
            "data-decision", "confirmed", timeout=10_000
        )
    page.get_by_test_id("inspection-fault-type-select").click()
    page.get_by_role("option", name="遮挡", exact=True).click()
    expect(page.get_by_test_id("inspection-submit-btn")).to_be_enabled()

    # 切换点位 B -> ElMessageBox「清除本页结果并更换」-> inspection.ts:293-299 discardAnalysis
    select_option(page, "inspection-pole-select", f"inspection-pole-option-{point_b_code}")
    _message_box_click(page, "清除本页结果并更换")

    # 作废后：点位已是 B；候选/草稿/处置清空；已选文件保留；提交硬禁且回到未分析提示
    expect(page.get_by_test_id("inspection-pole-select")).to_contain_text(
        point_b_code, timeout=10_000
    )
    expect(_candidate_items(page)).to_have_count(0, timeout=10_000)
    expect(page.get_by_test_id("inspection-ai-status")).to_contain_text(
        "仅支持静态", timeout=10_000
    )
    expect(page.get_by_test_id("inspection-ai-analyze-btn")).to_contain_text(
        "已选 1/10 张", timeout=10_000
    )
    expect(page.locator(".file-chip")).to_have_count(1)
    expect(page.get_by_test_id("inspection-submit-btn")).to_be_disabled()
    expect(page.get_by_text("请先完成 AI 智能分析（或切换手动填报模式）")).to_be_visible()

    # 已选文件保留 -> 直接对新点位 B 重新触发真实分析
    body_b = _analyze_once(page)
    draft_b = body_b["ticket"]
    assert draft_b is not None, "B 点位重分析应产出新草稿"
    assert draft_b["lighting_point_id"] == point_b_id
    assert draft_b["id"] != draft_a_id
    fault_indexes_b = _fault_indexes(body_b)
    assert fault_indexes_b

    for idx in fault_indexes_b:
        page.get_by_test_id(f"inspection-candidate-confirm-{idx}").click()
        expect(page.get_by_test_id(f"inspection-candidate-item-{idx}")).to_have_attribute(
            "data-decision", "confirmed", timeout=10_000
        )

    # 提交 -> confirm 必须指向 B 的新草稿而非 A 的旧草稿
    page.get_by_test_id("inspection-fault-type-select").click()
    page.get_by_role("option", name="遮挡", exact=True).click()
    expect(page.get_by_test_id("inspection-submit-btn")).to_be_enabled()
    page.get_by_test_id("inspection-submit-btn").click()
    with page.expect_response(
        lambda r: _CONFIRM_RE.search(r.url) is not None and r.request.method == "POST"
    ) as confirm_info:
        _message_box_click(page, "确认提交")
    confirm_response = confirm_info.value
    assert confirm_response.status == 200, confirm_response.text()
    matched = _CONFIRM_RE.search(confirm_response.url)
    assert matched is not None and int(matched.group(1)) == draft_b["id"]

    page.wait_for_url(re.compile(rf".*/tickets/{draft_b['id']}$"), timeout=30_000)
    assert page.get_by_test_id("workorder-detail-no").inner_text().strip() == draft_b["ticket_no"]


def test_point_switch_cancel_restores_point_and_candidate_state(
    page: Page,
    frontend_url: str,
    backend_server: str,
    tmp_path: Path,
) -> None:
    """A 分析并处置完毕后切 B ->「取消」-> 点位还原为 A、候选决策与提交状态原样保留。"""
    page.set_viewport_size({"width": 1440, "height": 900})
    admin_token = api_login(backend_server, "admin")
    point_a_code, point_b_code = "SL-E2E-CXL-A01", "SL-E2E-CXL-B01"
    point_a_id = create_lighting_point(backend_server, admin_token, point_code=point_a_code)
    create_lighting_point(backend_server, admin_token, point_code=point_b_code)

    login(page, frontend_url, "inspector1")
    page.wait_for_url("**/inspection/new", timeout=20_000)
    select_option(page, "inspection-pole-select", f"inspection-pole-option-{point_a_code}")
    body_a = _analyze_with(page, _textured_occluded_fault(tmp_path))
    assert body_a["ticket"] is not None
    assert body_a["ticket"]["lighting_point_id"] == point_a_id
    draft_a = body_a["ticket"]
    fault_indexes = _fault_indexes(body_a)
    assert fault_indexes

    # 先把全部故障候选确认掉 -> 提交进入 enabled 状态，供「恢复提交状态」断言
    page.get_by_test_id("inspection-candidates-list").wait_for(state="visible", timeout=20_000)
    for idx in fault_indexes:
        page.get_by_test_id(f"inspection-candidate-confirm-{idx}").click()
        expect(page.get_by_test_id(f"inspection-candidate-item-{idx}")).to_have_attribute(
            "data-decision", "confirmed", timeout=10_000
        )
    page.get_by_test_id("inspection-fault-type-select").click()
    page.get_by_role("option", name="遮挡", exact=True).click()
    expect(page.get_by_test_id("inspection-submit-btn")).to_be_enabled()
    candidate_count = _candidate_items(page).count()

    # 切换点位 B -> 「取消」-> handlePointChange catch 分支还原 previousPointId
    select_option(page, "inspection-pole-select", f"inspection-pole-option-{point_b_code}")
    _message_box_click(page, "取消")

    # 恢复断言：点位回 A、候选数量与处置决策原样、提交仍旧 enabled、分析状态保留
    expect(page.get_by_test_id("inspection-pole-select")).to_contain_text(
        point_a_code, timeout=10_000
    )
    expect(page.get_by_test_id("inspection-ai-status")).to_contain_text("已完成", timeout=10_000)
    expect(_candidate_items(page)).to_have_count(candidate_count)
    for idx in fault_indexes:
        expect(page.get_by_test_id(f"inspection-candidate-item-{idx}")).to_have_attribute(
            "data-decision", "confirmed", timeout=10_000
        )
    expect(page.get_by_test_id("inspection-submit-btn")).to_be_enabled()

    # 端到端证明 A 草稿未被取消动作污染：confirm 仍指向 A 的草稿并落详情页
    page.get_by_test_id("inspection-submit-btn").click()
    with page.expect_response(
        lambda r: _CONFIRM_RE.search(r.url) is not None and r.request.method == "POST"
    ) as confirm_info:
        _message_box_click(page, "确认提交")
    confirm_response = confirm_info.value
    assert confirm_response.status == 200, confirm_response.text()
    matched = _CONFIRM_RE.search(confirm_response.url)
    assert matched is not None and int(matched.group(1)) == draft_a["id"]
    page.wait_for_url(re.compile(rf".*/tickets/{draft_a['id']}$"), timeout=30_000)


def test_asset_analysis_and_resume_with_supplementary_photo(
    page: Page, frontend_url: str, backend_server: str, tmp_path: Path
) -> None:
    """Select a real asset, add ordinary evidence, then resume and confirm the original draft."""
    from tests.e2e.conftest import _localhost_client, api_get
    from tests.integration.conftest import make_static_image

    admin = api_login(backend_server, "admin")
    inspector = api_login(backend_server, "inspector1")
    points = api_get(backend_server, admin, "/api/v1/lighting-points").json()["items"]
    point = next(p for p in points if p["point_code"] == "SL-ZZ-JS-0001")
    assets = api_get(
        backend_server, admin, f"/api/v1/facility-assets?lighting_point_id={point['id']}"
    ).json()["items"]
    asset = next(a for a in assets if a["importance"] == "HIGH")

    login(page, frontend_url, "inspector1")
    select_option(page, "inspection-pole-select", "inspection-pole-option-SL-ZZ-JS-0001")
    page.get_by_text("关联设施资产（可选）").locator("..").locator(".el-select").click()
    page.get_by_role("option", name=re.compile(re.escape(asset["asset_code"]))).click()
    body = _analyze_with(page, _textured_occluded_fault(tmp_path))
    draft = body["ticket"]
    assert draft["facility_asset_id"] == asset["id"]
    with _localhost_client() as client:
        uploaded = client.post(
            f"{backend_server}/api/v1/tickets/{draft['id']}/evidences",
            headers={"Authorization": f"Bearer {inspector}"},
            data={"evidence_type": "INSPECTION_PHOTO"},
            files={"files": ("extra.jpg", make_static_image(), "image/jpeg")},
        )
    assert uploaded.status_code in (200, 201), uploaded.text
    assert uploaded.json()[0]["analysis_id"] is None
    page.goto(f"{frontend_url}/tickets/{draft['id']}", wait_until="networkidle")
    page.get_by_role("button", name="逐条处置候选").click()
    if page.get_by_role("button", name="确认", exact=True).is_visible():
        page.get_by_role("button", name="确认", exact=True).click()
    page.wait_for_url("**/inspection/new")
    for index in _fault_indexes(body):
        page.get_by_test_id(f"inspection-candidate-confirm-{index}").click()
    page.get_by_test_id("inspection-fault-type-select").click()
    page.get_by_role("option", name="遮挡", exact=True).click()
    page.get_by_test_id("inspection-submit-btn").click()
    with page.expect_response(lambda r: _CONFIRM_RE.search(r.url) is not None) as result:
        _message_box_click(page, "确认提交")
    assert result.value.status == 200, result.value.text()
    assert result.value.json()["facility_asset_id"] == asset["id"]
    assert result.value.json()["priority"] == "P2"
    # Release the seeded point for independent E2E cases in the same session.
    from tests.integration.conftest import new_idem_key

    with _localhost_client() as client:
        cancelled = client.post(
            f"{backend_server}/api/v1/tickets/{draft['id']}/cancel",
            headers={
                "Authorization": f"Bearer {inspector}",
                "Idempotency-Key": new_idem_key("asset-cleanup"),
            },
            json={"cancel_reason": "E2E 验证结束"},
        )
    assert cancelled.status_code == 200, cancelled.text
