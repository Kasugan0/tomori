"""Responsive E2E: `390x844` (mobile) vs `1280x800` (desktop) layout switching.

`src/frontend/src/shared/breakpoints.ts` defines `BP_DESKTOP_MIN = 992`, and `AppShell.vue` mounts
the desktop sidebar and the mobile tab bar with mutually exclusive `v-if`/`v-else`, so the
two nav families must never coexist. Horizontal overflow is measured from real
`document.documentElement.scrollWidth` against `clientWidth`.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, ViewportSize

from tests.e2e.conftest import (
    _localhost_client,
    api_login,
    create_lighting_point,
    login,
)

MOBILE: ViewportSize = {"width": 390, "height": 844}
DESKTOP: ViewportSize = {"width": 1280, "height": 800}


_OVERFLOW_PROBE = """() => {
    const de = document.documentElement;
    const vw = de.clientWidth;
    const offenders = [];
    document.querySelectorAll('*').forEach(el => {
        const r = el.getBoundingClientRect();
        if (r.width > 0 && r.right > vw + 0.5) {
            offenders.push({
                tag: el.tagName,
                testid: el.getAttribute('data-testid'),
                cls: (el.className || '').toString().slice(0, 70),
                right: Math.round(r.right),
                width: Math.round(r.width),
            });
        }
    });
    return {overflow: de.scrollWidth - de.clientWidth, vw, offenders};
}"""


def _assert_no_overflow(page: Page, label: str) -> None:
    """Assert no horizontal overflow; on failure report the exact offending elements."""
    probe = page.evaluate(_OVERFLOW_PROBE)
    assert probe["overflow"] <= 1, (
        f"{label} 横向溢出 {probe['overflow']}px (viewport={probe['vw']}px)；"
        f"越界元素={probe['offenders'][:8]}"
    )


def test_nav_switches_between_mobile_and_desktop_for_inspector(
    page: Page,
    frontend_url: str,
) -> None:
    """390x844 只渲染移动端导航；1280x800 只渲染桌面端导航；两族互斥且无横向溢出."""
    page.set_viewport_size(MOBILE)
    login(page, frontend_url, "inspector1")
    page.wait_for_url("**/inspection/new", timeout=20_000)

    # 移动端：底部 tab bar + 更多抽屉入口；桌面侧栏必须完全不存在
    assert page.get_by_test_id("nav-mobile-inspection").is_visible()
    assert page.get_by_test_id("nav-mobile-workorders").is_visible()
    assert page.get_by_test_id("nav-mobile-notifications").is_visible()
    assert page.get_by_test_id("nav-mobile-more").is_visible()
    for desktop_testid in (
        "nav-desktop-inspection",
        "nav-desktop-workorders",
        "nav-desktop-dispatch",
    ):
        assert page.get_by_test_id(desktop_testid).count() == 0, desktop_testid
    _assert_no_overflow(page, "390x844")

    # 移动端「更多」抽屉可打开并列出角色可见菜单
    page.get_by_test_id("nav-mobile-more").click()
    page.wait_for_function("() => document.body.innerText.includes('工作台菜单')", timeout=10_000)

    # 切到桌面视口：移动端导航消失、侧栏导航出现
    page.set_viewport_size(DESKTOP)
    page.get_by_test_id("nav-desktop-inspection").wait_for(state="visible", timeout=10_000)
    assert page.get_by_test_id("nav-mobile-inspection").count() == 0
    assert page.get_by_test_id("nav-mobile-more").count() == 0
    assert page.get_by_test_id("nav-desktop-workorders").is_visible()
    _assert_no_overflow(page, "1280x800")


def test_nav_switches_between_mobile_and_desktop_for_admin(
    page: Page,
    frontend_url: str,
) -> None:
    """admin 在移动端只有 4 个基础 tab（无 dashboard/dispatch 专属 tab），桌面端完整侧栏."""
    page.set_viewport_size(MOBILE)
    login(page, frontend_url, "admin")
    page.wait_for_url("**/dashboard", timeout=20_000)

    assert page.get_by_test_id("nav-mobile-workorders").is_visible()
    assert page.get_by_test_id("nav-mobile-notifications").is_visible()
    assert page.get_by_test_id("nav-mobile-more").is_visible()
    # ADMIN 既非 INSPECTOR 也非 TECHNICIAN -> 对应移动 tab 不渲染
    assert page.get_by_test_id("nav-mobile-inspection").count() == 0
    assert page.get_by_test_id("nav-mobile-repairs").count() == 0
    _assert_no_overflow(page, page.url)

    page.set_viewport_size(DESKTOP)
    page.get_by_test_id("nav-desktop-dispatch").wait_for(state="visible", timeout=10_000)
    for testid in (
        "nav-desktop-rules",
        "nav-desktop-regions",
        "nav-desktop-points",
        "nav-desktop-assets",
        "nav-desktop-scopes",
        "nav-desktop-accounts",
        "nav-desktop-audit",
        "nav-desktop-notifications",
    ):
        assert page.get_by_test_id(testid).is_visible(), testid
    _assert_no_overflow(page, page.url)


def _seed_ticket(backend_url: str, *, point_code: str) -> str:
    """Create one real ticket through `POST /tickets` so the list has a row to render."""
    admin_token = api_login(backend_url, "admin")
    point_id = create_lighting_point(backend_url, admin_token, point_code=point_code)
    inspector_token = api_login(backend_url, "inspector1")
    with _localhost_client() as client:
        created = client.post(
            f"{backend_url}/api/v1/tickets",
            headers={
                "Authorization": f"Bearer {inspector_token}",
                "Idempotency-Key": f"e2e-responsive-{point_code.lower()}",
            },
            json={
                "lighting_point_id": point_id,
                "fault_type": "LAMP_OUT",
                "title": f"E2E 响应式布局工单 {point_code}",
                "safety_flag": False,
            },
        )
    if created.status_code != 201:
        raise RuntimeError(f"创建响应式测试工单失败（{created.status_code}）：{created.text}")
    body: dict[str, object] = created.json()
    return str(body["ticket_no"])


def test_ticket_list_renders_table_on_desktop_and_cards_on_mobile(
    page: Page,
    frontend_url: str,
    backend_server: str,
) -> None:
    """工单列表在桌面端用表格、移动端用卡片（`v-if`/`v-else` 互斥挂载）."""
    ticket_no = _seed_ticket(backend_server, point_code="SL-E2E-RESP-0001")
    page.set_viewport_size(DESKTOP)
    login(page, frontend_url, "inspector1")
    page.wait_for_url("**/inspection/new", timeout=20_000)
    page.get_by_test_id("nav-desktop-workorders").click()
    page.wait_for_url("**/tickets", timeout=20_000)
    page.get_by_test_id(f"workorder-row-{ticket_no}").wait_for(state="visible", timeout=20_000)
    assert page.locator("table.workorder-table").count() == 1
    assert page.locator(".mobile-card-list").count() == 0
    _assert_no_overflow(page, page.url)

    page.set_viewport_size(MOBILE)
    page.wait_for_function(
        "() => document.querySelector('.mobile-card-list') !== null", timeout=15_000
    )
    page.get_by_test_id(f"workorder-row-{ticket_no}").wait_for(state="visible", timeout=20_000)
    assert page.locator("table.workorder-table").count() == 0
    _assert_no_overflow(page, page.url)


@pytest.mark.parametrize(
    "viewport",
    [
        ViewportSize(width=390, height=844),
        ViewportSize(width=768, height=1024),
        ViewportSize(width=992, height=800),
        ViewportSize(width=1280, height=800),
        ViewportSize(width=1920, height=1080),
    ],
)
def test_inspection_page_has_no_horizontal_overflow_across_breakpoints(
    page: Page,
    frontend_url: str,
    viewport: ViewportSize,
) -> None:
    """巡查报障页在 5 个断点下均无横向溢出，且 bbox 容器宽度不超过视口."""
    page.set_viewport_size(viewport)
    login(page, frontend_url, "inspector1")
    page.wait_for_url("**/inspection/new", timeout=20_000)
    page.wait_for_load_state("networkidle")

    _assert_no_overflow(page, f"{viewport['width']}x{viewport['height']}")

    # 桌面断点(>= 992) 必须走桌面导航；以下走移动导航
    if viewport["width"] >= 992:
        assert page.get_by_test_id("nav-desktop-inspection").is_visible()
        assert page.get_by_test_id("nav-mobile-inspection").count() == 0
    else:
        assert page.get_by_test_id("nav-mobile-inspection").is_visible()
        assert page.get_by_test_id("nav-desktop-inspection").count() == 0

    # 关键表单控件必须可交互且宽度受限
    pole_select_box = page.get_by_test_id("inspection-pole-select").bounding_box()
    assert pole_select_box is not None
    assert pole_select_box["width"] <= viewport["width"]
    assert page.get_by_test_id("inspection-submit-btn").is_visible()


def test_mobile_tabbar_navigation_works_with_touch_viewport(
    page: Page,
    frontend_url: str,
) -> None:
    """移动端底部 tab 真实可点击跳转，且跳转后页面无横向溢出."""
    page.set_viewport_size(MOBILE)
    login(page, frontend_url, "inspector1")
    page.wait_for_url("**/inspection/new", timeout=20_000)

    page.get_by_test_id("nav-mobile-workorders").click()
    page.wait_for_url("**/tickets", timeout=20_000)
    page.wait_for_load_state("networkidle")
    _assert_no_overflow(page, page.url)

    page.get_by_test_id("nav-mobile-notifications").click()
    page.wait_for_url("**/notifications", timeout=20_000)
    page.wait_for_load_state("networkidle")
    _assert_no_overflow(page, page.url)

    page.get_by_test_id("nav-mobile-inspection").click()
    page.wait_for_url("**/inspection/new", timeout=20_000)
    assert page.get_by_test_id("inspection-pole-select").is_visible()

    # 1) 未展开下拉时先测一次基线（<= 390）
    _assert_no_overflow(page, f"{page.url} [before-open]")

    # 2) 展开 `el-select` 弹层态：直接断言 `scrollWidth <= 390`（移除 Escape 隔离）
    page.get_by_test_id("inspection-pole-select").click()
    option = page.get_by_test_id("inspection-pole-option-SL-ZZ-JS-0001")
    option.wait_for(state="visible", timeout=10_000)
    open_scroll_w = int(page.evaluate("() => document.documentElement.scrollWidth"))
    assert open_scroll_w <= 390, f"el-select 展开态 scrollWidth={open_scroll_w} > 390"
    _assert_no_overflow(page, f"{page.url} [popper-open]")

    # 3) 选中选项后（不再按 Escape）仍不得溢出
    option.click()
    _assert_no_overflow(page, f"{page.url} [after-select]")
