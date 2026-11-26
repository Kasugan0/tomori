"""Real async Playwright concurrency, lock, and ordering races."""

from __future__ import annotations

import asyncio
import contextlib
import re
import uuid
from pathlib import Path
from typing import Any

import pytest
from playwright.async_api import Browser, Page, Route, async_playwright

from tests.e2e.conftest import (
    _localhost_client,
    api_get,
    api_login,
    create_lighting_point,
)
from tests.e2e.test_deep_business_flows import (
    _api_accept_dispatch,
    _api_complete_dispatch,
    _api_reject_dispatch,
    _create_ticket,
    _dispatch,
)
from tests.integration.conftest import make_occluded_fault_png


@pytest.fixture(autouse=True)
def _artifact_capture() -> None:
    """Async tests write screenshots directly via `e2e_artifacts` instead of sync `page`."""
    return None


def _save_name(nodeid: str) -> str:
    return f"{re.sub(r'[^A-Za-z0-9_.-]+', '_', nodeid)[-120:]}.png"


async def _authed_page(
    playwright: Any, frontend_url: str, *, token: str, role: str
) -> tuple[Browser, Page]:
    browser: Browser = await playwright.chromium.launch()
    context = await browser.new_context()
    page = await context.new_page()
    await page.goto(frontend_url)
    await page.evaluate(
        "([tok, r]) => {"
        " sessionStorage.setItem('tomori_access_token', tok);"
        " sessionStorage.setItem('tomori_role_code', r); }",
        [token, role],
    )
    return browser, page


async def _snap(page: Page, e2e_artifacts: Path, nodeid: str) -> None:
    with contextlib.suppress(Exception):
        await page.screenshot(path=str(e2e_artifacts / _save_name(nodeid)), full_page=True)


def _create_draft_ticket(backend_url: str, inspector_token: str, point_id: int) -> int:
    with _localhost_client() as client:
        resp = client.post(
            f"{backend_url}/api/v1/tickets/ai-candidates",
            headers={
                "Authorization": f"Bearer {inspector_token}",
                "Idempotency-Key": str(uuid.uuid4()),
            },
            data={"lighting_point_id": str(point_id), "scene_lighting_hint": "night"},
            files={"files": ("occluded.png", make_occluded_fault_png(), "image/png")},
        )
    assert resp.status_code in {200, 201}, resp.text
    return int(resp.json()["ticket"]["id"])


def _make_manual_review_on_point(backend_url: str, point_code: str) -> int:
    admin = api_login(backend_url, "admin")
    inspector = api_login(backend_url, "inspector1")
    tech = api_login(backend_url, "tech1")
    point = create_lighting_point(backend_url, admin, point_code=point_code)
    tid = _create_ticket(
        backend_url, inspector, fault_type="OCCLUDED", title="复核并发", point_id=point
    )
    for _ in range(3):
        did = _dispatch(backend_url, admin, tid)
        _api_reject_dispatch(backend_url, tech, did, "现场条件暂不具备")
    detail = api_get(backend_url, admin, f"/api/v1/tickets/{tid}").json()
    assert detail["status"] == "MANUAL_REVIEW"
    return tid


async def test_old_detail_cannot_unlock_after_write(
    backend_server: str,
    frontend_url: str,
    e2e_artifacts: Path,
    request: pytest.FixtureRequest,
) -> None:
    admin = api_login(backend_server, "admin")
    inspector = api_login(backend_server, "inspector1")
    point = create_lighting_point(backend_server, admin, point_code="SL-E2E-RACE-01")
    tid = _create_ticket(
        backend_server, inspector, fault_type="OTHER", title="竞态回归", point_id=point
    )
    old = api_get(backend_server, inspector, f"/api/v1/tickets/{tid}")
    assert old.status_code == 200

    held = asyncio.Event()
    release = asyncio.Event()
    delivered = asyncio.Event()
    hits: list[str] = []
    failures: list[str] = []
    async with async_playwright() as pw:
        browser, page = await _authed_page(pw, frontend_url, token=inspector, role="INSPECTOR")
        try:
            await page.goto(f"{frontend_url}/tickets/{tid}", wait_until="networkidle")
            await page.get_by_test_id("workorder-cancel-btn").wait_for(state="visible")

            async def control(route: Route) -> None:
                if route.request.method != "GET":
                    await route.continue_()
                elif not hits:
                    hits.append(route.request.url)
                    held.set()
                    await release.wait()
                    await route.fulfill(status=200, content_type="application/json", body=old.text)
                    delivered.set()
                else:
                    failures.append(route.request.url)
                    await route.fulfill(
                        status=500,
                        content_type="application/json",
                        body='{"error":{"code":"REFRESH_FAILED","message":"刷新失败注入"}}',
                    )

            await page.route(f"**/api/v1/tickets/{tid}", control)
            await page.get_by_role("button", name="刷新详情").click()
            await asyncio.wait_for(held.wait(), timeout=10)
            assert len(hits) == 1 and not delivered.is_set()
            await page.get_by_test_id("workorder-cancel-btn").click()
            await page.get_by_test_id("workorder-cancel-reason-input").fill("竞态验证")
            async with page.expect_response(
                lambda r: r.url.endswith(f"/tickets/{tid}/cancel") and r.request.method == "POST"
            ) as info:
                await page.get_by_test_id("workorder-cancel-confirm-btn").click()
                await (
                    page.get_by_role("dialog", name="确认撤销误报")
                    .get_by_role("button", name="确认撤销", exact=True)
                    .click()
                )
            assert (await info.value).status == 200
            await page.get_by_text("操作已成功，但工单详情刷新失败", exact=False).wait_for(
                state="visible"
            )
            assert failures and not delivered.is_set()
            release.set()
            await asyncio.wait_for(delivered.wait(), timeout=10)
            await page.wait_for_timeout(200)
            assert await page.get_by_text(
                "操作已成功，但工单详情刷新失败", exact=False
            ).is_visible()
            assert await page.get_by_test_id("workorder-cancel-btn").is_disabled()
            await page.unroute(f"**/api/v1/tickets/{tid}", control)
            async with page.expect_response(
                lambda r: r.url.endswith(f"/tickets/{tid}") and r.status == 200
            ):
                await page.get_by_role("button", name="重新加载详情").click()
            await page.get_by_text("操作已成功，但工单详情刷新失败", exact=False).wait_for(
                state="hidden"
            )
            await _snap(page, e2e_artifacts, request.node.nodeid)
        finally:
            await browser.close()

    final = api_get(backend_server, inspector, f"/api/v1/tickets/{tid}")
    assert final.status_code == 200 and final.json()["status"] == "CANCELLED"


async def test_regrade_post_fail_does_not_fake_and_refresh_failure_unlocks(
    backend_server: str,
    frontend_url: str,
    e2e_artifacts: Path,
    request: pytest.FixtureRequest,
) -> None:
    """POST 500 不假成功且换 payload 重置幂等键；再成功+GET 500 禁写，刷新解锁状态不变动作。"""
    admin = api_login(backend_server, "admin")
    inspector = api_login(backend_server, "inspector1")
    point = create_lighting_point(backend_server, admin, point_code="SL-E2E-REGRADE-LOCK")
    tid = _create_ticket(
        backend_server, inspector, fault_type="OTHER", title="调级锁解锁", point_id=point
    )
    initial_priority = api_get(backend_server, admin, f"/api/v1/tickets/{tid}").json()["priority"]
    async with async_playwright() as pw:
        browser, page = await _authed_page(pw, frontend_url, token=admin, role="ADMIN")
        try:
            await page.goto(f"{frontend_url}/tickets/{tid}", wait_until="networkidle")
            await page.get_by_role("button", name="手工调级").click()
            dialog = page.get_by_role("dialog", name="手工调整工单优先级")
            await dialog.locator(".el-select").click()
            await page.get_by_role("option", name=re.compile(r"^P1")).click()
            await dialog.get_by_placeholder("请填写优先级调整理由").fill("首次失败")

            keys: list[str] = []
            fail_post = True
            fail_get = False
            get_failures: list[str] = []

            async def control(route: Route) -> None:
                req = route.request
                if req.method == "POST" and req.url.endswith(f"/tickets/{tid}/regrade"):
                    keys.append(req.headers.get("idempotency-key", ""))
                    if fail_post:
                        await route.fulfill(
                            status=500,
                            content_type="application/json",
                            body='{"error":{"code":"REGRADE_BOOM","message":"调级接口故障"}}',
                        )
                        return
                elif (
                    req.method == "GET" and req.url.endswith(f"/api/v1/tickets/{tid}") and fail_get
                ):
                    get_failures.append(req.url)
                    await route.fulfill(
                        status=500,
                        content_type="application/json",
                        body='{"error":{"code":"GET_FAIL","message":"刷新详情报错"}}',
                    )
                    return
                await route.continue_()

            await page.route(f"**/api/v1/tickets/{tid}**", control)
            async with page.expect_response(
                lambda r: r.url.endswith(f"/tickets/{tid}/regrade") and r.request.method == "POST"
            ) as first_resp:
                await dialog.get_by_role("button", name="确认调级").click()
            assert (await first_resp.value).status == 500
            await page.get_by_test_id("error-banner-message").wait_for(state="visible")
            assert "调级接口故障" in await page.get_by_test_id("error-banner-message").inner_text()
            assert (
                await page.get_by_text("操作已成功，但工单详情刷新失败", exact=False).count() == 0
            )
            assert (
                api_get(backend_server, admin, f"/api/v1/tickets/{tid}").json()["priority"]
                == initial_priority
            )

            fail_post = False
            fail_get = True
            await dialog.get_by_placeholder("请填写优先级调整理由").fill("变更理由后重提")
            async with page.expect_response(
                lambda r: r.url.endswith(f"/tickets/{tid}/regrade") and r.request.method == "POST"
            ) as second_resp:
                await dialog.get_by_role("button", name="确认调级").click()
            assert (await second_resp.value).status == 200
            assert len(keys) == 2 and keys[0] and keys[1] and keys[0] != keys[1]

            await page.get_by_text("操作已成功，但工单详情刷新失败", exact=False).wait_for(
                state="visible"
            )
            assert get_failures
            assert "P1" in await page.get_by_test_id("workorder-detail-priority").inner_text()
            assert await page.get_by_role("button", name="手工调级").count() == 0
            assert await page.get_by_role("button", name="立即派工").count() == 0
            assert await page.get_by_test_id("workorder-cancel-btn").is_disabled()

            fail_get = False
            async with page.expect_response(
                lambda r: r.url.endswith(f"/tickets/{tid}") and r.status == 200
            ):
                await page.get_by_role("button", name="重新加载详情").click()
            await page.get_by_text("操作已成功，但工单详情刷新失败", exact=False).wait_for(
                state="hidden"
            )
            assert await page.get_by_role("button", name="手工调级").is_enabled()
            assert await page.get_by_role("button", name="立即派工").is_enabled()
            assert await page.get_by_test_id("workorder-cancel-btn").is_enabled()
            await _snap(page, e2e_artifacts, request.node.nodeid)
        finally:
            await browser.close()

    final = api_get(backend_server, admin, f"/api/v1/tickets/{tid}").json()
    assert final["priority"] == "P1" and final["status"] == "PENDING_DISPATCH"


async def test_draft_save_locks_on_refresh_failure_and_unlocks_to_resave(
    backend_server: str,
    frontend_url: str,
    e2e_artifacts: Path,
    request: pytest.FixtureRequest,
) -> None:
    admin = api_login(backend_server, "admin")
    inspector = api_login(backend_server, "inspector1")
    point = create_lighting_point(backend_server, admin, point_code="SL-E2E-DRAFT-LOCK")
    tid = _create_draft_ticket(backend_server, inspector, point)

    async with async_playwright() as pw:
        browser, page = await _authed_page(pw, frontend_url, token=inspector, role="INSPECTOR")
        try:
            await page.goto(f"{frontend_url}/tickets/{tid}", wait_until="networkidle")
            fail_get = True
            get_failures: list[str] = []

            async def control(route: Route) -> None:
                if (
                    route.request.method == "GET"
                    and route.request.url.endswith(f"/api/v1/tickets/{tid}")
                    and fail_get
                ):
                    get_failures.append(route.request.url)
                    await route.fulfill(
                        status=500,
                        content_type="application/json",
                        body='{"error":{"code":"DRAFT_GET_FAIL","message":"草稿刷新报错"}}',
                    )
                else:
                    await route.continue_()

            await page.route(f"**/api/v1/tickets/{tid}", control)
            await page.get_by_placeholder("请输入工单标题").fill("草稿标题一")
            async with page.expect_response(
                lambda r: r.url.endswith(f"/tickets/{tid}") and r.request.method == "PATCH"
            ) as patch1:
                await page.get_by_role("button", name="保存草稿修改").click()
            assert (await patch1.value).status == 200
            await page.get_by_text("操作已成功，但工单详情刷新失败", exact=False).wait_for(
                state="visible"
            )
            assert get_failures
            assert await page.get_by_role("button", name="保存草稿修改").count() == 0
            assert await page.get_by_test_id("workorder-cancel-btn").is_disabled()

            fail_get = False
            async with page.expect_response(
                lambda r: r.url.endswith(f"/tickets/{tid}") and r.status == 200
            ):
                await page.get_by_role("button", name="重新加载详情").click()
            await page.get_by_text("操作已成功，但工单详情刷新失败", exact=False).wait_for(
                state="hidden"
            )
            assert await page.get_by_role("button", name="保存草稿修改").is_enabled()

            await page.get_by_placeholder("请输入工单标题").fill("草稿标题二")
            async with page.expect_response(
                lambda r: r.url.endswith(f"/tickets/{tid}") and r.request.method == "PATCH"
            ) as patch2:
                await page.get_by_role("button", name="保存草稿修改").click()
            assert (await patch2.value).status == 200
            await _snap(page, e2e_artifacts, request.node.nodeid)
        finally:
            await browser.close()

    assert (
        api_get(backend_server, inspector, f"/api/v1/tickets/{tid}").json()["title"] == "草稿标题二"
    )


async def test_ticket_switch_late_old_detail_get_cannot_overwrite_new_ticket(
    backend_server: str,
    frontend_url: str,
    e2e_artifacts: Path,
    request: pytest.FixtureRequest,
) -> None:
    admin = api_login(backend_server, "admin")
    inspector = api_login(backend_server, "inspector1")
    point_a = create_lighting_point(backend_server, admin, point_code="SL-E2E-SWITCH-A")
    point_b = create_lighting_point(backend_server, admin, point_code="SL-E2E-SWITCH-B")
    tid_a = _create_ticket(
        backend_server, inspector, fault_type="OTHER", title="票A", point_id=point_a
    )
    tid_b = _create_ticket(
        backend_server, inspector, fault_type="OTHER", title="票B", point_id=point_b
    )
    detail_a = api_get(backend_server, inspector, f"/api/v1/tickets/{tid_a}")
    detail_b = api_get(backend_server, inspector, f"/api/v1/tickets/{tid_b}")
    assert detail_a.status_code == 200 and detail_b.status_code == 200
    no_b = str(detail_b.json()["ticket_no"])

    held = asyncio.Event()
    release = asyncio.Event()
    delivered = asyncio.Event()
    hits: list[str] = []
    async with async_playwright() as pw:
        browser, page = await _authed_page(pw, frontend_url, token=inspector, role="INSPECTOR")
        try:
            await page.goto(f"{frontend_url}/tickets/{tid_a}", wait_until="networkidle")

            async def control(route: Route) -> None:
                if route.request.method == "GET" and route.request.url.endswith(
                    f"/api/v1/tickets/{tid_a}"
                ):
                    hits.append(route.request.url)
                    held.set()
                    await release.wait()
                    await route.fulfill(
                        status=200, content_type="application/json", body=detail_a.text
                    )
                    delivered.set()
                else:
                    await route.continue_()

            await page.route(f"**/api/v1/tickets/{tid_a}", control)
            await page.get_by_role("button", name="刷新详情").click()
            await asyncio.wait_for(held.wait(), timeout=10)
            assert len(hits) == 1 and not delivered.is_set()

            async with page.expect_response(
                lambda r: r.url.endswith(f"/tickets/{tid_b}") and r.status == 200
            ):
                await page.evaluate(
                    "async id => (await import('/src/router/index.ts'))"
                    ".router.push(`/tickets/${id}`)",
                    tid_b,
                )
            assert (await page.get_by_test_id("workorder-detail-no").inner_text()).strip() == no_b
            release.set()
            await asyncio.wait_for(delivered.wait(), timeout=10)
            await page.wait_for_timeout(200)
            assert (await page.get_by_test_id("workorder-detail-no").inner_text()).strip() == no_b
            await _snap(page, e2e_artifacts, request.node.nodeid)
        finally:
            await browser.close()


async def test_acceptance_upload_mutex_refresh_failure_keeps_id_and_cross_ticket_isolated(
    backend_server: str,
    frontend_url: str,
    e2e_upload_files: dict[str, Path],
    e2e_artifacts: Path,
    request: pytest.FixtureRequest,
) -> None:
    admin = api_login(backend_server, "admin")
    inspector = api_login(backend_server, "inspector1")
    tech = api_login(backend_server, "tech1")
    point_a = create_lighting_point(backend_server, admin, point_code="SL-E2E-UACC-A")
    point_b = create_lighting_point(backend_server, admin, point_code="SL-E2E-UACC-B")
    tid_a = _create_ticket(
        backend_server, inspector, fault_type="OTHER", title="验收互斥A", point_id=point_a
    )
    tid_b = _create_ticket(
        backend_server, inspector, fault_type="OTHER", title="验收互斥B", point_id=point_b
    )
    for tid in (tid_a, tid_b):
        did = _dispatch(backend_server, admin, tid)
        _api_accept_dispatch(backend_server, tech, did)
        _api_complete_dispatch(backend_server, tech, did)

    upload_held = asyncio.Event()
    upload_release = asyncio.Event()
    upload_delivered = asyncio.Event()
    upload_hits_a: list[str] = []
    uploaded_ids: list[int] = []
    posts: list[str] = []
    fail_get_a = False
    get_failures_a: list[str] = []

    async with async_playwright() as pw:
        browser, page = await _authed_page(pw, frontend_url, token=inspector, role="INSPECTOR")
        try:
            page.on(
                "request",
                lambda r: posts.append(f"{r.method}:{r.url}") if r.method == "POST" else None,
            )

            async def capture_upload(resp: Any) -> None:
                if (
                    resp.request.method == "POST"
                    and resp.url.endswith(f"/tickets/{tid_a}/evidences")
                    and resp.status in {200, 201}
                ):
                    uploaded_ids.extend(int(item["id"]) for item in await resp.json())

            page.on("response", capture_upload)

            async def route_a(route: Route) -> None:
                req = route.request
                if req.method == "POST" and req.url.endswith(f"/tickets/{tid_a}/evidences"):
                    upload_hits_a.append(req.url)
                    if len(upload_hits_a) == 1:
                        upload_held.set()
                        await upload_release.wait()
                    await route.continue_()
                    if len(upload_hits_a) == 1:
                        upload_delivered.set()
                elif (
                    req.method == "GET"
                    and req.url.endswith(f"/api/v1/tickets/{tid_a}")
                    and fail_get_a
                ):
                    get_failures_a.append(req.url)
                    await route.fulfill(
                        status=500,
                        content_type="application/json",
                        body='{"error":{"code":"ACCEPT_GET_FAIL","message":"验收刷新报错"}}',
                    )
                else:
                    await route.continue_()

            await page.route(f"**/api/v1/tickets/{tid_a}**", route_a)
            await page.goto(f"{frontend_url}/tickets/{tid_a}", wait_until="networkidle")
            await page.locator("input[type=file][multiple]").set_input_files(
                str(e2e_upload_files["acceptance_photo"])
            )
            await page.get_by_role("button", name=re.compile(r"^立即上传")).click()
            await asyncio.wait_for(upload_held.wait(), timeout=10)
            assert len(upload_hits_a) == 1 and not upload_delivered.is_set()
            assert await page.get_by_test_id("accept-submit-btn").count() == 0

            async with page.expect_response(
                lambda r: r.url.endswith(f"/tickets/{tid_b}") and r.status == 200
            ):
                await page.evaluate(
                    "async id => (await import('/src/router/index.ts'))"
                    ".router.push(`/tickets/${id}`)",
                    tid_b,
                )
            upload_release.set()
            await asyncio.wait_for(upload_delivered.wait(), timeout=10)
            await page.wait_for_timeout(300)
            assert await page.get_by_text("本会话已上传待绑定验收照片", exact=False).count() == 0
            assert not any(f"/tickets/{tid_b}/" in item for item in posts)

            async with page.expect_response(
                lambda r: r.url.endswith(f"/tickets/{tid_a}") and r.status == 200
            ):
                await page.evaluate(
                    "async id => (await import('/src/router/index.ts'))"
                    ".router.push(`/tickets/${id}`)",
                    tid_a,
                )
            await page.get_by_text("本会话已上传待绑定验收照片 1 张", exact=False).wait_for(
                state="visible"
            )

            fail_get_a = True
            await page.locator("input[type=file][multiple]").set_input_files(
                str(e2e_upload_files["repair_photo"])
            )
            async with page.expect_response(
                lambda r: (
                    r.url.endswith(f"/tickets/{tid_a}/evidences") and r.request.method == "POST"
                )
            ) as second_upload:
                await page.get_by_role("button", name=re.compile(r"^立即上传")).click()
            assert (await second_upload.value).status in {200, 201}
            await page.get_by_text("操作已成功，但工单详情刷新失败", exact=False).wait_for(
                state="visible"
            )
            assert get_failures_a
            assert await page.get_by_test_id("accept-submit-btn").count() == 0

            fail_get_a = False
            async with page.expect_response(
                lambda r: r.url.endswith(f"/tickets/{tid_a}") and r.status == 200
            ):
                await page.get_by_role("button", name="重新加载详情").click()
            await page.get_by_text("本会话已上传待绑定验收照片 2 张", exact=False).wait_for(
                state="visible"
            )
            purposes = await page.evaluate(
                """async id => {
                const {useTicketsStore} = await import('/src/stores/tickets.ts');
                const {pinia} = await import('/src/stores/pinia.ts');
                const store = useTicketsStore(pinia);
                return {urge: store.getUploadedEvidence(id, 'urge'),
                        acceptance: store.getUploadedEvidence(id, 'acceptance')};
            }""",
                tid_a,
            )
            assert purposes == {"urge": [], "acceptance": uploaded_ids}
            await page.get_by_test_id("accept-reject-btn").click()
            await page.get_by_test_id("accept-comment-input").fill("复核带双照片不重传")
            async with page.expect_response(
                lambda r: (
                    r.url.endswith(f"/tickets/{tid_a}/acceptance") and r.request.method == "POST"
                )
            ) as acc_resp:
                await page.get_by_test_id("accept-submit-btn").click()
            resp = await acc_resp.value
            assert resp.status == 200, await resp.text()
            body = resp.request.post_data_json or {}
            assert len(uploaded_ids) == 2
            assert set(body.get("evidence_ids") or []) == set(uploaded_ids)
            assert len(upload_hits_a) == 2
            await _snap(page, e2e_artifacts, request.node.nodeid)
        finally:
            await browser.close()

    final = api_get(backend_server, inspector, f"/api/v1/tickets/{tid_a}").json()
    assert final["status"] == "REWORK_PENDING_DISPATCH"
    assert len([ev for ev in final["evidences"] if ev["evidence_type"] == "ACCEPTANCE_PHOTO"]) == 2


async def test_special_close_upload_success_refresh_failure_resumes_without_reupload(
    backend_server: str,
    frontend_url: str,
    e2e_upload_files: dict[str, Path],
    e2e_artifacts: Path,
    request: pytest.FixtureRequest,
) -> None:
    admin = api_login(backend_server, "admin")
    tid = _make_manual_review_on_point(backend_server, "SL-E2E-SC-LOCK")
    fail_get = True
    uploads: list[int] = []
    resolves: list[str] = []
    get_failures: list[str] = []

    async with async_playwright() as pw:
        browser, page = await _authed_page(pw, frontend_url, token=admin, role="ADMIN")
        try:
            await page.goto(f"{frontend_url}/tickets/{tid}", wait_until="networkidle")

            async def control(route: Route) -> None:
                req = route.request
                if req.method == "GET" and req.url.endswith(f"/api/v1/tickets/{tid}") and fail_get:
                    get_failures.append(req.url)
                    await route.fulfill(
                        status=500,
                        content_type="application/json",
                        body='{"error":{"code":"SC_GET_FAIL","message":"特批详情刷新失败"}}',
                    )
                else:
                    await route.continue_()

            def watch_req(req: Any) -> None:
                if req.method == "POST" and req.url.endswith(f"/tickets/{tid}/review-resolve"):
                    resolves.append(req.url)

            async def watch_resp(resp: Any) -> None:
                if (
                    resp.request.method == "POST"
                    and resp.url.endswith(f"/tickets/{tid}/evidences")
                    and resp.status in {200, 201}
                ):
                    for item in await resp.json():
                        uploads.append(int(item["id"]))

            page.on("request", watch_req)
            page.on("response", watch_resp)
            await page.route(f"**/api/v1/tickets/{tid}**", control)

            await page.get_by_test_id("escalation-manual-review-btn").click()
            await page.locator('input[name="review-resolution"][value="SPECIAL_CLOSE"]').check()
            await page.get_by_test_id("escalation-resolve-reason-input").fill(
                "上传凭证后详情刷新失败并进行恢复重提"
            )
            await page.get_by_test_id("escalation-urged-unit-input").fill("市政运维中心")
            await page.get_by_test_id("escalation-urged-at-input").fill("2026-11-26T19:30")
            await page.get_by_test_id("escalation-urge-evidence-input").set_input_files(
                str(e2e_upload_files["acceptance_photo"])
            )

            async with page.expect_response(
                lambda r: r.url.endswith(f"/tickets/{tid}/evidences") and r.request.method == "POST"
            ):
                await page.get_by_test_id("escalation-resolve-btn").click()
                await (
                    page.get_by_role("dialog", name="确认复核裁决")
                    .get_by_role("button", name="确认裁决", exact=True)
                    .click()
                )
            await page.get_by_text("操作已成功，但工单详情刷新失败", exact=False).wait_for(
                state="visible"
            )
            assert len(uploads) == 1 and get_failures and not resolves
            assert await page.get_by_test_id("escalation-resolve-btn").is_disabled()

            fail_get = False
            async with page.expect_response(
                lambda r: r.url.endswith(f"/tickets/{tid}") and r.status == 200
            ):
                await page.get_by_role("button", name="重新加载详情").click()
            await page.get_by_text("本会话已上传督促凭证 1 份", exact=False).wait_for(
                state="visible"
            )
            purposes = await page.evaluate(
                """async id => {
                const {useTicketsStore} = await import('/src/stores/tickets.ts');
                const {pinia} = await import('/src/stores/pinia.ts');
                const store = useTicketsStore(pinia);
                return {urge: store.getUploadedEvidence(id, 'urge'),
                        acceptance: store.getUploadedEvidence(id, 'acceptance')};
            }""",
                tid,
            )
            assert purposes == {"urge": uploads, "acceptance": []}
            async with page.expect_response(
                lambda r: (
                    r.url.endswith(f"/tickets/{tid}/review-resolve") and r.request.method == "POST"
                )
            ) as resolve_info:
                await page.get_by_test_id("escalation-resolve-btn").click()
                await (
                    page.get_by_role("dialog", name="确认复核裁决")
                    .get_by_role("button", name="确认裁决", exact=True)
                    .click()
                )
            resp = await resolve_info.value
            assert resp.status == 200, await resp.text()
            body = resp.request.post_data_json or {}
            assert body.get("urge_evidence_ids") == uploads
            assert len(uploads) == 1
            await _snap(page, e2e_artifacts, request.node.nodeid)
        finally:
            await browser.close()

    assert api_get(backend_server, admin, f"/api/v1/tickets/{tid}").json()["status"] == "CLOSED"


async def test_dispatch_board_late_old_queue_get_cannot_overwrite_or_clear_loading(
    backend_server: str,
    frontend_url: str,
    e2e_artifacts: Path,
    request: pytest.FixtureRequest,
) -> None:
    admin = api_login(backend_server, "admin")
    inspector = api_login(backend_server, "inspector1")
    point = create_lighting_point(backend_server, admin, point_code="SL-E2E-BOARD-RACE")
    tid = _create_ticket(
        backend_server, inspector, fault_type="OTHER", title="看板队列竞态", point_id=point
    )
    ticket_no = str(api_get(backend_server, admin, f"/api/v1/tickets/{tid}").json()["ticket_no"])
    old_queue = api_get(
        backend_server, admin, "/api/v1/tickets?status=PENDING_DISPATCH&page=1&page_size=100"
    )
    assert old_queue.status_code == 200

    capture_old = False
    fail_new = False
    held = asyncio.Event()
    release = asyncio.Event()
    delivered = asyncio.Event()
    old_hits: list[str] = []
    new_failures: list[str] = []
    failure_hit = asyncio.Event()

    async with async_playwright() as pw:
        browser, page = await _authed_page(pw, frontend_url, token=admin, role="ADMIN")
        try:

            async def control(route: Route) -> None:
                req = route.request
                if (
                    req.method == "GET"
                    and "/api/v1/tickets?" in req.url
                    and "status=PENDING_DISPATCH" in req.url
                ):
                    if capture_old and not old_hits:
                        old_hits.append(req.url)
                        held.set()
                        await release.wait()
                        await route.fulfill(
                            status=200, content_type="application/json", body=old_queue.text
                        )
                        delivered.set()
                        return
                    if fail_new:
                        new_failures.append(req.url)
                        failure_hit.set()
                        await route.fulfill(
                            status=500,
                            content_type="application/json",
                            body='{"error":{"code":"QUEUE_FAIL","message":"队列刷新失败"}}',
                        )
                        return
                await route.continue_()

            await page.route("**/api/v1/tickets?*", control)
            await page.goto(f"{frontend_url}/dispatch", wait_until="networkidle")
            row = page.get_by_test_id(f"dispatch-queue-row-{ticket_no}")
            await row.click()
            await page.get_by_test_id("dispatch-open-btn").click()
            await page.get_by_test_id("dispatch-confirm-btn").wait_for(state="visible")

            capture_old = True
            await page.get_by_role("button", name="刷新队列").click()
            await asyncio.wait_for(held.wait(), timeout=10)
            capture_old = False
            assert len(old_hits) == 1 and not delivered.is_set()

            fail_new = True
            async with page.expect_response(
                lambda r: r.url.endswith(f"/tickets/{tid}/dispatch") and r.request.method == "POST"
            ) as disp_resp:
                await page.get_by_test_id("dispatch-confirm-btn").click()
                await (
                    page.get_by_role("dialog", name="确认派工")
                    .get_by_role("button", name="确认", exact=True)
                    .click()
                )
            assert (await disp_resp.value).status == 201
            await page.get_by_text("操作已成功，但列表或详情刷新失败", exact=False).wait_for(
                state="visible"
            )
            await asyncio.wait_for(failure_hit.wait(), timeout=10)
            assert new_failures and not delivered.is_set()
            assert await page.get_by_test_id("dispatch-open-btn").is_disabled()
            assert await page.get_by_role("button", name="刷新队列").is_enabled()

            release.set()
            await asyncio.wait_for(delivered.wait(), timeout=10)
            await page.wait_for_timeout(200)
            assert await page.get_by_text(
                "操作已成功，但列表或详情刷新失败", exact=False
            ).is_visible()
            assert await page.get_by_test_id("dispatch-open-btn").is_disabled()

            fail_new = False
            async with page.expect_response(
                lambda r: (
                    "/api/v1/tickets?" in r.url
                    and "status=PENDING_DISPATCH" in r.url
                    and r.status == 200
                )
            ):
                await page.get_by_role("button", name="重新加载").click()
            await page.get_by_text("操作已成功，但列表或详情刷新失败", exact=False).wait_for(
                state="hidden"
            )
            assert (
                "已派工待接单"
                in await page.get_by_test_id(f"dispatch-queue-row-{ticket_no}").inner_text()
            )
            await _snap(page, e2e_artifacts, request.node.nodeid)
        finally:
            await browser.close()

    assert api_get(backend_server, admin, f"/api/v1/tickets/{tid}").json()["status"] == "DISPATCHED"
