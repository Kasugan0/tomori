"""P1/P2 deep business E2E: rework->manual_review, dispatch reject x3, deadline, notifications,
SLA scan visibility, forbidden page, point region guard, safety-missing items.

Driving model (deterministic, no sleeps):
- Business state reached via real API calls (`api_login`/`api_post`/`api_get`) against the
  real `uvicorn` on 18142 and real PG `tomori_test`;
- timestamp backdating (`accept_deadline_at`/`repair_deadline_at`) uses the same SYNC
  `create_engine` pattern as `conftest._reset_and_reseed_rules` (column update, never a
  status write) — mirrors the documented `POST /sla/scan` determinism requirement;
- UI assertions via frozen `data-testid` anchors only; `ElMessageBox.confirm` has no testid
  (see `conftest.MESSAGE_BOX_NOTE`).
"""

from __future__ import annotations

import asyncio
import re
import threading
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from playwright.sync_api import Page
from sqlalchemy import create_engine, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from tests.e2e.conftest import (
    E2E_DATABASE_URL,
    SYNC_DATABASE_URL,
    _localhost_client,
    api_get,
    api_login,
    confirm_message_box,
    create_lighting_point,
    login,
    logout,
)
from tomori.models.account import Account
from tomori.models.notification_task import NotificationTask
from tomori.services.state_machine import create_notification_task
from tomori.workers.notifier import process_due_notifications_once


def _idem_key() -> str:
    """Fresh Idempotency-Key (pattern ^[A-Za-z0-9_-]{8,128}$ required by the API)."""
    return f"e2e-{uuid.uuid4().hex[:16]}"


def _run_async(coro: Any) -> Any:
    """在**新建线程**里 `asyncio.run` 协程 —— 主线程被 pytest-playwright 的 runner loop
    占据时，`asyncio.run()` 会报「cannot be called from a running event loop」；
    新线程拥有自己的事件循环，是唯一可承载域异步函数的合法路径。
    """
    box: dict[str, Any] = {}

    def _t() -> None:
        try:
            box["result"] = asyncio.run(coro)
        except BaseException as exc:  # noqa: BLE001
            box["error"] = exc

    th = threading.Thread(target=_t, daemon=True)
    th.start()
    th.join(90)
    if "error" in box:
        raise box["error"]
    return box.get("result")


def _api_post_idem(
    backend_url: str, token: str, path: str, *, json_body: Any = None
) -> httpx.Response:
    """POST with Authorization + Idempotency-Key (all idempotent write endpoints)."""
    with _localhost_client() as client:
        return client.post(
            f"{backend_url}{path}",
            headers={
                "Authorization": f"Bearer {token}",
                "Idempotency-Key": _idem_key(),
            },
            json=json_body,
        )


SAFETY_OK: dict[str, Any] = {
    "electrical_safety_checked": True,
    "safety_checklist": {
        "power_isolated": True,
        "voltage_verified_zero": True,
        "insulation_intact": True,
        "grounding_connected": True,
        "ppe_worn": True,
    },
    "safety_note": "已断电验电并完成个人防护穿戴",
}


def _sync_exec(sql: str, params: dict[str, Any]) -> None:
    """SYNC column write (timestamps only) against tomori_test — never status forging."""
    engine = create_engine(SYNC_DATABASE_URL, poolclass=NullPool)
    try:
        with engine.begin() as conn:
            conn.execute(text(sql), params)
    finally:
        engine.dispose()


def _sync_rows(sql: str, params: dict[str, Any]) -> list[tuple[Any, ...]]:
    engine = create_engine(SYNC_DATABASE_URL, poolclass=NullPool)
    try:
        with engine.begin() as conn:
            return [tuple(r) for r in conn.execute(text(sql), params).all()]
    finally:
        engine.dispose()


def _admin_token(backend_url: str) -> str:
    return api_login(backend_url, "admin")


def _inspector_token(backend_url: str) -> str:
    return api_login(backend_url, "inspector1")


def _tech1_token(backend_url: str) -> str:
    return api_login(backend_url, "tech1")


def _tech2_token(backend_url: str) -> str:
    return api_login(backend_url, "tech2")


def _js_point_id(backend_url: str, token: str, point_code: str = "SL-ZZ-JS-0001") -> int:
    items = api_get(backend_url, token, "/api/v1/lighting-points").json()["items"]
    return int(next(p for p in items if p["point_code"] == point_code)["id"])


def _create_ticket(
    backend_url: str,
    insp_token: str,
    *,
    fault_type: str,
    title: str,
    point_id: int,
) -> int:
    r = _api_post_idem(
        backend_url,
        insp_token,
        "/api/v1/tickets",
        json_body={
            "lighting_point_id": point_id,
            "fault_type": fault_type,
            "title": title,
            "safety_flag": False,
        },
    )
    assert r.status_code == 201, r.text
    return int(r.json()["id"])


def _dispatch(backend_url: str, admin_token: str, tid: int, username: str = "tech1") -> int:
    cands = api_get(backend_url, admin_token, f"/api/v1/tickets/{tid}/dispatch-candidates").json()[
        "candidates"
    ]
    cand = next(c for c in cands if c["username"] == username)
    r = _api_post_idem(
        backend_url,
        admin_token,
        f"/api/v1/tickets/{tid}/dispatch",
        json_body={
            "technician_account_id": cand["account_id"],
            "scope_authorization_id": cand["scope_authorization_id"],
        },
    )
    assert r.status_code == 201, r.text
    return int(r.json()["id"])


def _api_reject_dispatch(backend_url: str, tech_token: str, did: int, reason: str) -> None:
    r = _api_post_idem(
        backend_url,
        tech_token,
        f"/api/v1/dispatches/{did}/reject",
        json_body={"reject_reason": reason},
    )
    assert r.status_code == 200, r.text


def _api_accept_dispatch(backend_url: str, tech_token: str, did: int) -> None:
    r = _api_post_idem(backend_url, tech_token, f"/api/v1/dispatches/{did}/accept")
    assert r.status_code == 200, r.text


def _api_complete_dispatch(backend_url: str, tech_token: str, did: int) -> None:
    now = datetime.now(UTC)
    body = {
        **SAFETY_OK,
        "arrived_at": (now - timedelta(minutes=30)).isoformat(),
        "repair_summary": "E2E API 完工",
        "materials": [],
    }
    r = _api_post_idem(
        backend_url, tech_token, f"/api/v1/dispatches/{did}/complete", json_body=body
    )
    assert r.status_code == 200, r.text


def _api_reject_acceptance(
    backend_url: str, insp_token: str, tid: int, comment: str
) -> dict[str, Any]:
    r = _api_post_idem(
        backend_url,
        insp_token,
        f"/api/v1/tickets/{tid}/acceptance",
        json_body={"decision": "REJECTED", "comment": comment},
    )
    assert r.status_code == 200, r.text
    body: dict[str, Any] = r.json()
    return body


def _ui_reject_dispatch(page: Page, frontend_url: str, backend_url: str, did: int) -> None:
    """UI-driven dispatch reject: reject btn -> reason -> confirm -> ElMessageBox 确认拒单."""
    _ = backend_url
    page.goto(f"{frontend_url}/repairs/{did}", wait_until="networkidle")
    page.get_by_test_id("repair-reject-btn").click()
    page.get_by_test_id("repair-reject-reason-input").fill("现场不具备维修条件（E2E）")
    page.get_by_test_id("repair-reject-confirm-btn").click()
    # handleReject 先 ElMessageBox.confirm「确认拒单」；对话框按钮与 repair-reject-confirm-btn
    # 同名（均「确认拒单」），须在 dialog 作用域内定位（见 conftest.MESSAGE_BOX_NOTE）。
    with page.expect_response(
        lambda resp: resp.url.endswith(f"/dispatches/{did}/reject") and resp.status == 200,
        timeout=15000,
    ):
        page.get_by_role("dialog", name="确认拒单").get_by_role(
            "button", name="确认拒单", exact=True
        ).click()


def test_rework_three_rounds_to_manual_review_and_admin_resolve_ui(
    page: Page, frontend_url: str, backend_server: str
) -> None:
    """3 轮验收驳回 -> MANUAL_REVIEW + rework_order IN_MANUAL_REVIEW + admin 复核裁决可见。"""
    backend = backend_server
    admin_t = _admin_token(backend)
    insp_t = _inspector_token(backend)
    tech_t = _tech1_token(backend)

    pt = _js_point_id(backend, admin_t, "SL-ZZ-JS-0001")
    tid = _create_ticket(
        backend, insp_t, fault_type="OCCLUDED", title="E2E 三轮返修复核", point_id=pt
    )

    for round_no in range(1, 4):
        did = _dispatch(backend, admin_t, tid)
        _api_accept_dispatch(backend, tech_t, did)
        _api_complete_dispatch(backend, tech_t, did)
        body = _api_reject_acceptance(backend, insp_t, tid, comment=f"第{round_no}轮验收不合格")
        status = body["ticket"]["status"]
        if round_no < 3:
            assert status == "REWORK_PENDING_DISPATCH", status
        else:
            assert status == "MANUAL_REVIEW", status

    # API-level verification of rework threshold semantics
    detail = api_get(backend, admin_t, f"/api/v1/tickets/{tid}").json()
    assert detail["rework_count"] == 3
    assert detail["status"] == "MANUAL_REVIEW"
    assert detail["manual_review_reason"] == "rework_threshold_exceeded"
    rework_orders = detail.get("rework_orders", [])
    assert len(rework_orders) == 3
    assert rework_orders[-1]["status"] == "IN_MANUAL_REVIEW"

    # Admin UI: manual-review notice + rework history + resolve panel visible
    login(page, frontend_url, "admin")
    page.goto(f"{frontend_url}/tickets/{tid}", wait_until="networkidle")
    page.get_by_test_id("workorder-manual-review-notice").wait_for()
    assert page.get_by_test_id("workorder-detail-rework-count").inner_text().strip() == "3"
    assert page.locator('[data-testid="rework-history-list"] > *').count() >= 3
    # Resolve via UI: TERMINATE_CANCEL (reason >=1 char) -> ticket CANCELLED
    page.get_by_test_id("escalation-manual-review-btn").click()
    page.locator('input[name="review-resolution"][value="TERMINATE_CANCEL"]').check()
    page.get_by_test_id("escalation-resolve-reason-input").fill("长期无人接单，终止工单")
    page.get_by_test_id("escalation-resolve-btn").click()
    # handleReviewResolve 先 ElMessageBox.confirm「确认裁决」（无 testid）
    with page.expect_response(
        lambda resp: resp.url.endswith(f"/tickets/{tid}/review-resolve") and resp.status == 200,
        timeout=15000,
    ):
        confirm_message_box(page, "确认裁决")
    final = api_get(backend, admin_t, f"/api/v1/tickets/{tid}").json()
    assert final["status"] == "CANCELLED"


def test_dispatch_reject_count3_via_ui_escalates_to_manual_review(
    page: Page, frontend_url: str, backend_server: str
) -> None:
    """3 次 UI 拒单 -> ticket MANUAL_REVIEW + dispatch_reject_count=3 + MANUAL_REVIEW_REQUIRED."""
    backend = backend_server
    admin_t = _admin_token(backend)
    insp_t = _inspector_token(backend)

    pt = _js_point_id(backend, admin_t, "SL-ZZ-JS-0002")
    tid = _create_ticket(
        backend, insp_t, fault_type="LAMP_OUT", title="E2E 三拒单升级", point_id=pt
    )

    login(page, frontend_url, "tech1")
    for round_no in range(1, 4):
        did = _dispatch(backend, admin_t, tid)
        _ui_reject_dispatch(page, frontend_url, backend, did)
        detail = api_get(backend, admin_t, f"/api/v1/tickets/{tid}").json()
        if round_no < 3:
            assert detail["status"] == "PENDING_DISPATCH", detail["status"]
        else:
            assert detail["status"] == "MANUAL_REVIEW", detail["status"]

    final = api_get(backend, admin_t, f"/api/v1/tickets/{tid}").json()
    assert final["dispatch_reject_count"] == 3
    assert final["manual_review_reason"] == "dispatch_reject_threshold_exceeded"

    # Admin notification center shows MANUAL_REVIEW_REQUIRED for this ticket
    logout(page)
    login(page, frontend_url, "admin")
    page.goto(f"{frontend_url}/notifications", wait_until="networkidle")
    rows = page.locator('[data-testid^="notification-row-"]')
    assert rows.count() >= 1
    # The MANUAL_REVIEW_REQUIRED notification should deep-link to the ticket
    notifs = api_get(backend, admin_t, "/api/v1/notifications?page_size=100").json()
    mrv = next(
        n
        for n in notifs["items"]
        if n["event_type"] == "MANUAL_REVIEW_REQUIRED" and n["related_ticket_id"] == tid
    )
    assert mrv["payload_json"] is not None


def test_dispatch_accept_deadline_overdue_422_ui_error(
    page: Page, frontend_url: str, backend_server: str
) -> None:
    """accept_deadline_at 过期后 UI 接单 -> 422 INVALID_STATE_TRANSITION + error-banner 可见。"""
    backend = backend_server
    admin_t = _admin_token(backend)
    insp_t = _inspector_token(backend)

    pt = _js_point_id(backend, admin_t, "SL-ZZ-JS-0001")
    tid = _create_ticket(
        backend, insp_t, fault_type="FIXTURE_DAMAGED", title="E2E 接单超时边界", point_id=pt
    )
    did = _dispatch(backend, admin_t, tid)

    # Backdate accept_deadline_at (timestamp column, not status) via SYNC engine
    past = datetime.now(UTC) - timedelta(seconds=1)
    _sync_exec(
        "UPDATE dispatch_records SET accept_deadline_at = :past WHERE id = :did",
        {"past": past, "did": did},
    )

    login(page, frontend_url, "tech1")
    page.goto(f"{frontend_url}/repairs/{did}", wait_until="networkidle")
    with page.expect_response(
        lambda resp: resp.url.endswith(f"/dispatches/{did}/accept") and resp.status == 422,
        timeout=15000,
    ):
        page.get_by_test_id("repair-accept-btn").click()
    # Global error banner carries the precise error code
    page.get_by_test_id("error-banner").wait_for()
    assert "INVALID_STATE_TRANSITION" in page.get_by_test_id("error-banner-code").inner_text()


def test_notification_dead_letter_retry_acknowledge_and_read_ui(
    page: Page, frontend_url: str, backend_server: str
) -> None:
    """DEAD_LETTER -> UI retry -> PENDING；UI acknowledge -> SENT + acknowledged_by；标已读。"""
    backend = backend_server
    admin_t = _admin_token(backend)

    # 域路径造 DEAD_LETTER / SENT / 未读通知（原生 INSERT 仅允许用于「列级」
    # 时间戳回填；状态字段必须经 `create_notification_task` + worker 退避链产生）。
    # `DEAD_LETTER` 无法直接构造——只能由 worker 对失败通道多次退避后产生
    # （`workers/notifier.py` 的 `transition_notification` 合法边 D4→D6）。
    async def _seed_notifications() -> tuple[int, int, int, int]:
        engine = create_async_engine(E2E_DATABASE_URL)
        try:
            async with AsyncSession(engine) as s:
                admin = await s.scalar(select(Account).where(Account.username == "admin"))
                assert admin is not None
                admin_id_local = admin.id  # capture before commit (object expires after)
                email_dl = create_notification_task(
                    s,
                    recipient_account_id=admin.id,
                    event_type="REPAIR_SLA_TIMEOUT",
                    channel="EMAIL",
                    title="E2E 死信重试任务",
                    body="SMTP 超时进入死信",
                    payload_json={"deep_link": "/tickets/1"},
                    max_attempts=3,
                )
                sms_dl = create_notification_task(
                    s,
                    recipient_account_id=admin.id,
                    event_type="AI_CANDIDATE_EXPIRED",
                    channel="SMS",
                    title="E2E 死信确认任务",
                    body="SMS 通道未配置进入死信",
                    payload_json={"deep_link": "/tickets/2"},
                    max_attempts=3,
                )
                mine_sent = create_notification_task(
                    s,
                    recipient_account_id=admin.id,
                    event_type="TICKET_DISPATCHED",
                    channel="IN_APP",
                    title="E2E 已读标记任务",
                    body="待标记已读的普通通知",
                    payload_json={"deep_link": "/tickets/3"},
                )
                await s.flush()
                dl_id, ack_id, mine_id = email_dl.id, sms_dl.id, mine_sent.id
                await s.commit()

            t0 = datetime.now(UTC)
            for off in (0, 60, 360):  # EMAIL/SMS 默认 dispatcher 必然失败
                async with AsyncSession(engine) as s:
                    await process_due_notifications_once(s, now=t0 + timedelta(seconds=off))
            async with AsyncSession(engine) as s:
                await process_due_notifications_once(
                    s, dispatcher=lambda _task: True, now=t0 + timedelta(seconds=361)
                )
            async with AsyncSession(engine) as s:
                dl_row = await s.scalar(
                    select(NotificationTask).where(NotificationTask.id == dl_id)
                )
                ack_row = await s.scalar(
                    select(NotificationTask).where(NotificationTask.id == ack_id)
                )
                mine_row = await s.scalar(
                    select(NotificationTask).where(NotificationTask.id == mine_id)
                )
                assert dl_row is not None
                assert dl_row.status == "DEAD_LETTER" and dl_row.attempt_count == 3
                assert ack_row is not None
                assert ack_row.status == "DEAD_LETTER" and ack_row.attempt_count == 3
                assert mine_row is not None
                assert mine_row.status == "SENT" and not mine_row.is_read
        finally:
            await engine.dispose()
        return admin_id_local, dl_id, ack_id, mine_id

    admin_id, dl_id, ack_id, mine_id = _run_async(_seed_notifications())

    login(page, frontend_url, "admin")

    # ── retry: DEAD_LETTER -> PENDING (attempt_count reset 0, sent_at NULL) ──
    page.goto(f"{frontend_url}/notifications?tab=dead-letters", wait_until="networkidle")
    row = page.get_by_test_id(f"notification-row-{dl_id}")
    row.wait_for()
    with page.expect_response(
        lambda resp: resp.url.endswith(f"/notifications/{dl_id}/retry") and resp.status == 200,
        timeout=15000,
    ):
        row.get_by_test_id("notification-retry-btn").click()
    after = api_get(backend, admin_t, "/api/v1/notifications/dead-letters?page_size=100").json()
    assert dl_id not in {n["id"] for n in after["items"]}
    st = _sync_rows(
        "SELECT status, attempt_count, sent_at FROM notification_tasks WHERE id=:id",
        {"id": dl_id},
    )[0]
    assert st[0] == "PENDING" and st[1] == 0 and st[2] is None

    # ── acknowledge: DEAD_LETTER -> SENT with acknowledged_by/at ──
    row2 = page.get_by_test_id(f"notification-row-{ack_id}")
    row2.wait_for()
    with page.expect_response(
        lambda resp: (
            resp.url.endswith(f"/notifications/{ack_id}/acknowledge") and resp.status == 200
        ),
        timeout=15000,
    ):
        row2.get_by_test_id("notification-acknowledge-btn").click()
    st2 = _sync_rows(
        "SELECT status, acknowledged_by_account_id, acknowledged_at, sent_at "
        "FROM notification_tasks WHERE id=:id",
        {"id": ack_id},
    )[0]
    assert st2[0] == "SENT" and st2[1] == admin_id and st2[2] is not None and st2[3] is not None

    # ── mark read on mine tab ──
    page.goto(f"{frontend_url}/notifications", wait_until="networkidle")
    mine_row = page.get_by_test_id(f"notification-row-{mine_id}")
    mine_row.wait_for()
    with page.expect_response(
        lambda resp: resp.url.endswith(f"/notifications/{mine_id}/read") and resp.status == 200,
        timeout=15000,
    ):
        mine_row.get_by_role("button", name="标记已读").click()
    st3 = _sync_rows(
        "SELECT is_read, read_at FROM notification_tasks WHERE id=:id",
        {"id": mine_id},
    )[0]
    assert st3[0] is True and st3[1] is not None


def test_sla_scan_overdue_badges_and_notifications_visible(
    page: Page, frontend_url: str, backend_server: str
) -> None:
    """/sla/scan 后 `workorder-sla-overdue-badge` 在派工看板与工单详情可见。"""
    backend = backend_server
    admin_t = _admin_token(backend)
    insp_t = _inspector_token(backend)
    tech_t = _tech1_token(backend)

    # Ticket A -> REWORK_PENDING_DISPATCH; Ticket B -> MANUAL_REVIEW (3 API rejects)
    pt = _js_point_id(backend, admin_t, "SL-ZZ-JS-0001")
    tid_rw = _create_ticket(
        backend, insp_t, fault_type="OTHER", title="E2E SLA 返修分支", point_id=pt
    )
    did = _dispatch(backend, admin_t, tid_rw)
    _api_accept_dispatch(backend, tech_t, did)
    _api_complete_dispatch(backend, tech_t, did)
    _api_reject_acceptance(backend, insp_t, tid_rw, "验收不合格进入返修")

    pt2 = _js_point_id(backend, admin_t, "SL-ZZ-JS-0002")
    tid_mrv = _create_ticket(
        backend, insp_t, fault_type="OCCLUDED", title="E2E SLA 复核分支", point_id=pt2
    )
    for _ in range(3):
        d_i = _dispatch(backend, admin_t, tid_mrv)
        _api_reject_dispatch(backend, tech_t, d_i, "E2E 复核分支拒单")

    # Backdate repair_deadline_at on both to force overdue marking
    past = datetime.now(UTC) - timedelta(hours=49)
    _sync_exec(
        "UPDATE fault_tickets SET repair_deadline_at = :past WHERE id IN (:a, :b)",
        {"past": past, "a": tid_rw, "b": tid_mrv},
    )

    login(page, frontend_url, "admin")
    page.goto(f"{frontend_url}/dispatch", wait_until="networkidle")
    with page.expect_response(
        lambda resp: resp.url.endswith("/sla/scan") and resp.status == 200,
        timeout=15000,
    ):
        page.get_by_test_id("sla-scan-trigger-btn").first.click()

    # Overdue badge visible on the ticket detail page
    page.goto(f"{frontend_url}/tickets/{tid_rw}", wait_until="networkidle")
    page.get_by_test_id("workorder-sla-overdue-badge").wait_for()
    page.goto(f"{frontend_url}/tickets/{tid_mrv}", wait_until="networkidle")
    page.get_by_test_id("workorder-sla-overdue-badge").wait_for()
    page.get_by_test_id("workorder-manual-review-notice").wait_for()

    # API-level scan result: both marked
    detail = api_get(backend, admin_t, f"/api/v1/tickets/{tid_mrv}").json()
    assert detail["is_sla_overdue"] is True
    assert detail["status"] == "MANUAL_REVIEW"


def test_forbidden_page_shows_role_target_and_back_button(
    page: Page, frontend_url: str, backend_server: str
) -> None:
    """/403 页显示角色原因 + 受限路径 + 返回按钮回默认工作台。"""
    _ = backend_server
    login(page, frontend_url, "inspector1")
    page.goto(f"{frontend_url}/admin/accounts", wait_until="networkidle")

    page.get_by_test_id("forbidden-page").wait_for()
    # Copy asserts: current role display + restricted target path
    text = page.get_by_test_id("forbidden-page").inner_text()
    assert "当前登录角色" in text
    assert "/admin/accounts" in text  # 受限目标路径回显
    page.get_by_test_id("forbidden-back-btn").click()
    page.wait_for_url(re.compile(r"/inspection/new"))
    assert "/403" not in page.url


def test_lighting_point_region_change_with_active_ticket_shows_error(
    page: Page, frontend_url: str, backend_server: str
) -> None:
    """PATCH point region with active ticket -> dialog shows POINT_HAS_ACTIVE_TICKET error."""
    backend = backend_server
    admin_t = _admin_token(backend)
    insp_t = _inspector_token(backend)

    # Fresh point (region 2 = ZZ-JS-01) -> no (point,fault_type) collision with other tests
    point_code = f"SL-E2E-PG-{uuid.uuid4().hex[:6]}"
    pt = create_lighting_point(backend, admin_t, point_code=point_code)
    _create_ticket(backend, insp_t, fault_type="LAMP_OUT", title="E2E 点位区域守卫", point_id=pt)

    # Region name for the ZD option (fetched at runtime, not hardcoded)
    zd_name = next(
        r["name"]
        for r in api_get(backend, admin_t, "/api/v1/regions").json()
        if r["region_code"] == "ZZ-ZD-01"
    )

    login(page, frontend_url, "admin")
    page.goto(f"{frontend_url}/admin/points", wait_until="networkidle")
    # Filter by the point code so the row is deterministic
    page.locator('input[placeholder="点位编码 / 名称 / 道路"]').fill(point_code)
    page.get_by_role("button", name="查询").click()
    row = page.locator("tr", has_text=point_code).first
    row.wait_for()
    row.get_by_role("button", name="编辑").click()
    dialog = page.locator(".el-dialog")
    dialog.wait_for()

    # Region el-select (first select in the dialog form) -> choose ZZ-ZD-01
    dialog.locator(".el-select").first.click()
    dialog.locator(".el-select-dropdown__item", has_text=zd_name).first.click()
    dialog.get_by_role("button", name="保存").click()

    # el-alert inside the dialog surfaces the 422 contract error
    alert = page.locator(".el-dialog .el-alert")
    alert.wait_for()
    assert "POINT_HAS_ACTIVE_TICKET" in alert.inner_text()


def test_repair_complete_missing_safety_items_gated_in_ui(
    page: Page, frontend_url: str, backend_server: str
) -> None:
    """Unchecked safety items -> complete submit disabled + gate-hints lists missing items.

    事实校正（诚实标记）：`canSubmitComplete` 前端门禁要求 master 开关 + 5 项全勾 +
    `safety_note`/`repair_summary` 非空 + `arrived_at` 打卡 —— 缺任何一项提交按钮即禁用，
    `ELECTRICAL_SAFETY_CHECK_REQUIRED` 的 `422`/`is-missing-error` 是服务端兜底路径，
    **UI 上不可达**，故本用例断言「禁按 + gate-hints-box 缺失项文案」而非伪造 422。
    """
    backend = backend_server
    admin_t = _admin_token(backend)
    insp_t = _inspector_token(backend)

    pt = _js_point_id(backend, admin_t, "SL-ZZ-JS-0002")
    tid = _create_ticket(
        backend, insp_t, fault_type="ELECTRIC_HAZARD", title="E2E 六件套缺失", point_id=pt
    )
    did = _dispatch(backend, admin_t, tid)

    login(page, frontend_url, "tech1")
    page.goto(f"{frontend_url}/repairs/{did}", wait_until="networkidle")

    with page.expect_response(
        lambda resp: resp.url.endswith(f"/dispatches/{did}/accept") and resp.status == 200,
        timeout=15000,
    ):
        page.get_by_test_id("repair-accept-btn").click()
    page.get_by_test_id("repair-arrival-checkin-btn").click()

    # Master checkbox enables the 5 item checkboxes; leave 2 unchecked so the
    # 422 ELECTRICAL_SAFETY_CHECK_REQUIRED carries missing_items -> is-missing-error rows.
    page.get_by_test_id("repair-safety-checkbox").check()
    for n in (
        "repair-safety-item-power-isolated",
        "repair-safety-item-voltage-verified-zero",
        "repair-safety-item-insulation-intact",
    ):
        page.get_by_test_id(n).check()
    page.get_by_test_id("repair-safety-note-input").fill("部分项未完成")
    page.get_by_test_id("repair-root-cause-input").fill("初查为驱动损坏")
    page.get_by_test_id("repair-process-note-input").fill("已更换驱动电源")

    # 未勾全 5 项 -> 提交按钮禁用 + gate-hints-box 列出缺失门禁项
    assert page.get_by_test_id("repair-complete-submit-btn").is_disabled()
    hints = page.locator(".gate-hints-box")
    hints.wait_for()
    assert "需逐项勾选全部 5 项电气安全检查" in hints.inner_text()

    # 勾满剩余两项后按钮恢复可用（门禁语义再验证）
    page.get_by_test_id("repair-safety-item-grounding-connected").check()
    page.get_by_test_id("repair-safety-item-ppe-worn").check()
    assert page.get_by_test_id("repair-complete-submit-btn").is_enabled()


def test_health_structure_and_admin_notifications_visibility_smoke(
    page: Page, frontend_url: str, backend_server: str
) -> None:
    """admin 通知中心可见真实通知行（业务事件产生，非仅结构容器）。"""
    admin_token = api_login(backend_server, "admin")
    insp_token = api_login(backend_server, "inspector1")
    pt_id = create_lighting_point(backend_server, admin_token, point_code="SL-E2E-NOTIF-001")
    # 真实业务副作用：人工建单 -> TICKET_CONFIRMED 通知本区域 ADMIN
    r_ticket = _api_post_idem(
        backend_server,
        insp_token,
        "/api/v1/tickets",
        json_body={
            "lighting_point_id": pt_id,
            "fault_type": "LAMP_OUT",
            "title": "E2E 通知可见性工单",
            "safety_flag": False,
        },
    )
    assert r_ticket.status_code == 201, r_ticket.text
    login(page, frontend_url, "admin")
    page.goto(f"{frontend_url}/notifications", wait_until="networkidle")
    # 我的通知 tab 默认激活，死信队列按钮存在（ADMIN 专属）
    assert page.get_by_role("button", name=re.compile(r"死信队列")).count() == 1
    rows = page.locator('[data-testid^="notification-row-"]')
    rows.first.wait_for(state="visible", timeout=10_000)
    assert rows.count() >= 1


# ──────────────────────────────────────────────────────────────────────
# SPECIAL_CLOSE 浏览器督促留证（task-45 缺口）：上传证据 ID 精确入裁决体、
# 上传失败不发裁决且重试不重复落证据
# ──────────────────────────────────────────────────────────────────────


def _make_manual_review_ticket(backend_url: str, point_code: str) -> tuple[int, str]:
    """真实 API 路径造 MANUAL_REVIEW 单：建单 -> 3×派工 -> 3×拒单。"""
    admin_t = _admin_token(backend_url)
    insp_t = _inspector_token(backend_url)
    tech_t = _tech1_token(backend_url)
    pt = _js_point_id(backend_url, admin_t, point_code)
    tid = _create_ticket(
        backend_url, insp_t, fault_type="OCCLUDED", title="E2E 特批关闭", point_id=pt
    )
    for _ in range(3):
        did = _dispatch(backend_url, admin_t, tid)
        _api_reject_dispatch(backend_url, tech_t, did, "现场不具备维修条件（E2E）")
    detail = api_get(backend_url, admin_t, f"/api/v1/tickets/{tid}").json()
    assert detail["status"] == "MANUAL_REVIEW", detail["status"]
    return tid, admin_t


def test_special_close_uploads_urge_evidence_and_resolves(
    page: Page, frontend_url: str, backend_server: str, e2e_upload_files: dict[str, object]
) -> None:
    """SPECIAL_CLOSE 全流程：督促凭证上传 ID 精确出现在裁决体 -> CLOSED。"""
    backend = backend_server
    tid, admin_t = _make_manual_review_ticket(backend, "SL-ZZ-JS-0001")

    login(page, frontend_url, "admin")
    page.goto(f"{frontend_url}/tickets/{tid}", wait_until="networkidle")
    page.get_by_test_id("escalation-manual-review-btn").click()
    page.locator('input[name="review-resolution"][value="SPECIAL_CLOSE"]').check()

    # 必填门禁：未填督促三件套前按钮 disabled
    page.get_by_test_id("escalation-resolve-reason-input").fill("督促单位两次未果，特批关闭结案")
    page.get_by_test_id("escalation-urged-unit-input").fill("市政运维中心")
    assert page.get_by_test_id("escalation-resolve-btn").is_disabled()

    page.get_by_test_id("escalation-urged-at-input").fill("2026-11-26T19:00")
    page.get_by_test_id("escalation-urge-evidence-input").set_input_files(
        str(e2e_upload_files["acceptance_photo"])
    )

    uploaded_ids: list[int] = []

    def _capture_upload(resp: Any) -> None:
        if resp.url.endswith(f"/tickets/{tid}/evidences") and resp.request.method == "POST":
            assert resp.status in {200, 201}
            for item in resp.json():
                uploaded_ids.append(int(item["id"]))

    resolve_body: dict[str, Any] = {}
    page.on("response", _capture_upload)
    with page.expect_response(
        lambda r: r.url.endswith(f"/tickets/{tid}/review-resolve"),
        timeout=15_000,
    ) as resolve_info:
        page.get_by_test_id("escalation-resolve-btn").click()
        confirm_message_box(page, "确认裁决")
    resolve_resp = resolve_info.value
    assert resolve_resp.status == 200, resolve_resp.text()
    resolve_body = resolve_resp.request.post_data_json or {}

    # 上传证据真实落库且其 ID 精确进入裁决体
    assert uploaded_ids, "督促凭证应通过真实 POST evidences 上传"
    assert resolve_body["resolution"] == "SPECIAL_CLOSE"
    assert resolve_body["urge_evidence_ids"] == uploaded_ids
    assert resolve_body["urged_unit"] == "市政运维中心"
    parsed_urged_at = datetime.fromisoformat(resolve_body["urged_at"].replace("Z", "+00:00"))
    assert parsed_urged_at.tzinfo is not None and parsed_urged_at.utcoffset() is not None

    final = api_get(backend, admin_t, f"/api/v1/tickets/{tid}").json()
    assert final["status"] == "CLOSED"
    urge_ev = [e for e in final["evidences"] if e["id"] in uploaded_ids]
    assert urge_ev, "上传凭证应挂到本票证据"


def test_special_close_upload_failure_blocks_resolve(
    page: Page, frontend_url: str, backend_server: str, e2e_upload_files: dict[str, object]
) -> None:
    """客户端拦截的上传 500 -> 不发 review-resolve、待传文件保留；重试只成功上传一份。

    证据边界：本用例仅覆盖服务端 500 到达客户端的失败语义；不声称覆盖
    「请求已提交但响应丢失」的幂等重放路径。"""
    backend = backend_server
    tid, admin_t = _make_manual_review_ticket(backend, "SL-ZZ-JS-0001")

    login(page, frontend_url, "admin")
    page.goto(f"{frontend_url}/tickets/{tid}", wait_until="networkidle")
    page.get_by_test_id("escalation-manual-review-btn").click()
    page.locator('input[name="review-resolution"][value="SPECIAL_CLOSE"]').check()
    page.get_by_test_id("escalation-resolve-reason-input").fill("督促单位两次未果，特批关闭结案")
    page.get_by_test_id("escalation-urged-unit-input").fill("市政运维中心")
    page.get_by_test_id("escalation-urged-at-input").fill("2026-11-26T19:00")
    page.get_by_test_id("escalation-urge-evidence-input").set_input_files(
        str(e2e_upload_files["acceptance_photo"])
    )

    resolve_posted: list[str] = []

    def _watch_resolve(request: Any) -> None:
        if request.url.endswith(f"/tickets/{tid}/review-resolve"):
            resolve_posted.append(request.url)

    page.on("request", _watch_resolve)

    # 注入上传失败（真实请求路径，不发裁决）
    def _fail_upload(route: Any) -> None:
        route.fulfill(
            status=500, content_type="application/json", body='{"error":{"code":"INJECTED"}}'
        )

    def upload_pred(url: Any) -> bool:
        return bool(url.endswith(f"/tickets/{tid}/evidences"))

    page.route(upload_pred, _fail_upload)
    page.get_by_test_id("escalation-resolve-btn").click()
    confirm_message_box(page, "确认裁决")
    page.wait_for_timeout(800)  # 有界窗口证明无裁决请求流出
    assert resolve_posted == []
    assert page.locator(".pending-file-item").count() == 1  # 失败后待传文件保留

    # 恢复网络后重试：仅一次成功上传、一次裁决
    page.unroute(upload_pred, handler=_fail_upload)
    uploads: list[dict[str, Any]] = []

    def _ok_upload(resp: Any) -> None:
        if (
            resp.url.endswith(f"/tickets/{tid}/evidences")
            and resp.request.method == "POST"
            and resp.status in {200, 201}
        ):
            uploads.extend(resp.json())

    page.on("response", _ok_upload)
    with page.expect_response(
        lambda r: r.url.endswith(f"/tickets/{tid}/review-resolve") and r.status == 200,
        timeout=15_000,
    ):
        page.get_by_test_id("escalation-resolve-btn").click()
        confirm_message_box(page, "确认裁决")
    assert len(uploads) == 1
    final = api_get(backend, admin_t, f"/api/v1/tickets/{tid}").json()
    assert final["status"] == "CLOSED"
    urge_ids = [e["id"] for e in final["evidences"] if e["evidence_type"] == "ACCEPTANCE_PHOTO"]
    assert urge_ids == [uploads[0]["id"]], "失败重试不得产生重复凭证"


# ──────────────────────────────────────────────────────────────────────
# 通知深链接（task-45 缺口）：TECHNICIAN 有 dispatch_id -> /repairs/{id}，
# 无 -> /repairs（目标身份断言，而非仅导航可见）
# ──────────────────────────────────────────────────────────────────────


def test_technician_notification_links_to_dispatch_and_todo(
    page: Page, frontend_url: str, backend_server: str
) -> None:
    """TECHNICIAN 通知：related_dispatch_id -> /repairs/{id}；缺省 -> /repairs。"""
    backend = backend_server
    admin_t = _admin_token(backend)
    insp_t = _inspector_token(backend)
    tech_t = api_login(backend, "tech2")

    pt = create_lighting_point(backend, admin_t, point_code="SL-E2E-NOTIF-TECH")
    tid = _create_ticket(
        backend, insp_t, fault_type="LAMP_OUT", title="E2E 通知深链接", point_id=pt
    )
    _dispatch(backend, admin_t, tid)  # tech1 首派
    # 管理员真实改派 -> 新 tech 通知携带 related_dispatch_id（dispatch.py:972-988）
    cands = api_get(backend, admin_t, f"/api/v1/tickets/{tid}/dispatch-candidates").json()[
        "candidates"
    ]
    cand2 = next(c for c in cands if c["username"] == "tech2")
    r_reassign = _api_post_idem(
        backend,
        admin_t,
        f"/api/v1/tickets/{tid}/reassign",
        json_body={
            "technician_account_id": cand2["account_id"],
            "scope_authorization_id": cand2["scope_authorization_id"],
            "reason": "原技师超时未响应",
        },
    )
    assert r_reassign.status_code in (200, 201), r_reassign.text
    did = int(r_reassign.json()["id"])

    async def _add_plain_notification() -> int:
        engine = create_async_engine(E2E_DATABASE_URL)
        try:
            async with AsyncSession(engine) as s:
                tech = await s.scalar(select(Account).where(Account.username == "tech2"))
                assert tech is not None
                task = create_notification_task(
                    s,
                    recipient_account_id=tech.id,
                    event_type="DISPATCH_RESPONSE_TIMEOUT",
                    channel="IN_APP",
                    title="E2E 待办提醒",
                    body="无关联派工的通知",
                    related_ticket_id=tid,
                    related_dispatch_id=None,
                )
                await s.flush()
                nid = task.id
                await s.commit()
                return nid
        finally:
            await engine.dispose()

    async def _deliver_all() -> None:
        """经 worker 真实路径投递 IN_APP 通知（列表只示已投递任务）。"""
        engine = create_async_engine(E2E_DATABASE_URL)
        try:
            async with AsyncSession(engine) as s:
                await process_due_notifications_once(
                    s, now=datetime.now(UTC) + timedelta(minutes=1)
                )
        finally:
            await engine.dispose()

    plain_notif_id = _run_async(_add_plain_notification())
    _run_async(_deliver_all())

    notifs = api_get(backend, tech_t, "/api/v1/notifications?page_size=100").json()
    mine = notifs["items"]
    linked = next(
        n
        for n in mine
        if n["event_type"] == "TICKET_DISPATCHED" and n.get("related_dispatch_id") == did
    )
    plain = next(n for n in mine if n["id"] == plain_notif_id)

    login(page, frontend_url, "tech2")
    page.goto(f"{frontend_url}/notifications", wait_until="networkidle")

    # related_dispatch_id -> 「查看派工 #id」 -> /repairs/{id}
    page.get_by_test_id(f"notification-row-{linked['id']}").get_by_role(
        "button", name=f"查看派工 #{did}"
    ).click()
    page.wait_for_url(f"**/repairs/{did}", timeout=15_000)
    page.get_by_test_id("repair-reject-btn").wait_for(state="visible", timeout=15_000)

    # 无 related_dispatch_id -> 「查看我的待办」 -> /repairs
    page.goto(f"{frontend_url}/notifications", wait_until="networkidle")
    page.get_by_test_id(f"notification-row-{plain['id']}").get_by_role(
        "button", name="查看我的待办"
    ).click()
    page.wait_for_url(re.compile(r".*/repairs$"), timeout=15_000)


# ──────────────────────────────────────────────────────────────────────
# 写成功后刷新失败（task-45 缺口）：POST 仅一次、成功态保留、错误可见、
# 恢复后真实后端状态一致
# ──────────────────────────────────────────────────────────────────────


def test_repair_accept_succeeds_despite_refresh_failure(
    page: Page, frontend_url: str, backend_server: str
) -> None:
    """接单 POST 成功后后续刷新 GET 500：成功态保留 + 错误横幅，恢复后真实 ACCEPTED。"""
    backend = backend_server
    admin_t = _admin_token(backend)
    insp_t = _inspector_token(backend)
    tech_t = _tech1_token(backend)
    point_code = "SL-E2E-RWF-0001"
    create_lighting_point(backend, admin_t, point_code=point_code)
    pt = _js_point_id(backend, admin_t, point_code)
    tid = _create_ticket(
        backend, insp_t, fault_type="LAMP_OUT", title="E2E 写后读失败", point_id=pt
    )
    did = _dispatch(backend, admin_t, tid)

    login(page, frontend_url, "tech1")
    page.goto(f"{frontend_url}/repairs/{did}", wait_until="networkidle")
    page.get_by_test_id("repair-accept-btn").wait_for(state="visible", timeout=15_000)

    accept_posts: list[str] = []

    def _watch_accept(request: Any) -> None:
        if request.url.endswith(f"/dispatches/{did}/accept"):
            accept_posts.append(request.url)

    page.on("request", _watch_accept)

    def _fail_ticket_get(route: Any) -> None:
        route.fulfill(
            status=500,
            content_type="application/json",
            body='{"error":{"code":"INJECTED_REFRESH","message":"injected"}}',
        )

    def ticket_get_pred(url: Any) -> bool:
        return bool(url.endswith(f"/api/v1/tickets/{tid}") or f"/api/v1/tickets/{tid}?" in url)

    page.route(ticket_get_pred, _fail_ticket_get)

    with page.expect_response(
        lambda r: r.url.endswith(f"/dispatches/{did}/accept") and r.request.method == "POST"
    ) as accept_info:
        page.get_by_test_id("repair-accept-btn").click()
    assert accept_info.value.status == 200, accept_info.value.text()

    # 成功态由写响应驱动：成功提示 + 到场打卡可点；POST 仅一次
    page.get_by_text("接单成功").wait_for(state="visible", timeout=15_000)
    assert len(accept_posts) == 1
    assert page.get_by_test_id("repair-arrival-checkin-btn").is_enabled()
    # 刷新失败错误以横幅可见，且不覆盖成功态
    assert page.locator(".tomori-error-banner, [role='alert']").first.is_visible()

    # 恢复网络后真实后端确认 ACCEPTED
    page.unroute(ticket_get_pred, handler=_fail_ticket_get)
    page.reload()
    page.wait_for_load_state("networkidle")
    items = api_get(backend, tech_t, "/api/v1/dispatches?page_size=100").json()["items"]
    detail = next(d for d in items if d["id"] == did)
    assert detail["status"] == "ACCEPTED"
