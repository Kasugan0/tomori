"""task-30/32 回归：`ai-candidates` 可选 `facility_asset_id` 透传与校验。

- 选资产分析 -> 草稿 `facility_asset_id` 一致 -> confirm 读 `importance` 分级
  （JS-0001 关联 HIGH 资产 -> R2 -> P2）；
- 不存在资产 -> 404 RESOURCE_NOT_FOUND；
- 资产点位与显式 `lighting_point_id` 不符 -> 422 VALIDATION_ERROR；
- 未给点位时以资产反解点位（须同区域）；
- 未选资产时不带该字段。
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient

from tests.integration.conftest import (
    auth_headers_for,
    build_confirm_payload,
    make_occluded_fault_png,
    new_idem_key,
)


def _err(resp: Any) -> dict[str, Any]:
    err: dict[str, Any] = resp.json()["error"]
    return err


async def _point_id(client: AsyncClient, headers: dict[str, str], code: str) -> int:
    items = (await client.get("/api/v1/lighting-points", headers=headers)).json()["items"]
    return int(next(p for p in items if p["point_code"] == code)["id"])


async def _asset_id(
    client: AsyncClient,
    headers: dict[str, str],
    *,
    point_id: int,
    importance: str | None = None,
) -> int:
    items = (
        await client.get(f"/api/v1/facility-assets?lighting_point_id={point_id}", headers=headers)
    ).json()["items"]
    if importance is None:
        return int(items[0]["id"])
    return int(next(a for a in items if a["importance"] == importance)["id"])


async def _ai_candidates(
    client: AsyncClient,
    headers: dict[str, str],
    *,
    point_id: int | None,
    asset_id: int | None = None,
) -> Any:
    data: dict[str, str] = {"scene_lighting_hint": "night"}
    if point_id is not None:
        data["lighting_point_id"] = str(point_id)
    if asset_id is not None:
        data["facility_asset_id"] = str(asset_id)
    return await client.post(
        "/api/v1/tickets/ai-candidates",
        data=data,
        files=[("files", ("occ.png", make_occluded_fault_png(), "image/png"))],
        headers=headers,
    )


async def test_facility_asset_pass_through_grades_confirmed_ticket_p2(
    api_client: AsyncClient,
) -> None:
    """选 HIGH 资产分析 -> 草稿挂载资产 -> confirm 后分级命中 R2 -> P2。"""
    insp_hdr = await auth_headers_for(api_client, "inspector1")
    admin_hdr = await auth_headers_for(api_client, "admin")
    pt_id = await _point_id(api_client, admin_hdr, "SL-ZZ-JS-0001")
    asset_id = await _asset_id(api_client, admin_hdr, point_id=pt_id, importance="HIGH")

    r = await _ai_candidates(
        api_client,
        {**insp_hdr, "Idempotency-Key": new_idem_key("far-draft")},
        point_id=pt_id,
        asset_id=asset_id,
    )
    assert r.status_code == 201, r.text
    draft = r.json()["ticket"]
    assert draft["facility_asset_id"] == asset_id

    r_conf = await api_client.post(
        f"/api/v1/tickets/{draft['id']}/confirm",
        json=build_confirm_payload(r.json()["evidences"]),
        headers={**insp_hdr, "Idempotency-Key": new_idem_key("far-conf")},
    )
    assert r_conf.status_code == 200, r_conf.text
    # JS-0001 MAIN 路段 + HIGH 资产 -> R2 -> P2（确认时读取 importance）
    assert r_conf.json()["priority"] == "P2"


async def test_facility_asset_validation_guards(
    api_client: AsyncClient,
) -> None:
    """资产 404 / 跨点位 422 / 资产反解点位（无 lighting_point_id）。"""
    insp_hdr = await auth_headers_for(api_client, "inspector1")
    admin_hdr = await auth_headers_for(api_client, "admin")
    pt1 = await _point_id(api_client, admin_hdr, "SL-ZZ-JS-0001")
    pt2 = await _point_id(api_client, admin_hdr, "SL-ZZ-JS-0002")
    asset_pt1 = await _asset_id(api_client, admin_hdr, point_id=pt1)

    # 1) 不存在资产 -> 404 RESOURCE_NOT_FOUND
    r_404 = await _ai_candidates(
        api_client,
        {**insp_hdr, "Idempotency-Key": new_idem_key("far-404")},
        point_id=pt1,
        asset_id=999_999_999,
    )
    assert r_404.status_code == 404, r_404.text
    assert _err(r_404)["code"] == "RESOURCE_NOT_FOUND"

    # 2) 资产点位 != 显式 lighting_point_id -> 422
    r_mismatch = await _ai_candidates(
        api_client,
        {**insp_hdr, "Idempotency-Key": new_idem_key("far-mm")},
        point_id=pt2,
        asset_id=asset_pt1,
    )
    assert r_mismatch.status_code == 422, r_mismatch.text
    assert _err(r_mismatch)["code"] == "VALIDATION_ERROR"
    assert _err(r_mismatch)["details"]["lighting_point_id"] == pt2

    # 3) 未给点位、仅资产 -> 反解资产所属点位，正常建草稿
    r_reverse = await _ai_candidates(
        api_client,
        {**insp_hdr, "Idempotency-Key": new_idem_key("far-rev")},
        point_id=None,
        asset_id=asset_pt1,
    )
    assert r_reverse.status_code in (200, 201), r_reverse.text
    body = r_reverse.json()
    if body["ticket"] is not None:
        assert body["ticket"]["lighting_point_id"] == pt1

    # 4) 未选资产 -> 不带该字段也能建草稿（对照组；用 pt2 避开 pt1 已建活跃单）
    r_none = await _ai_candidates(
        api_client,
        {**insp_hdr, "Idempotency-Key": new_idem_key("far-none")},
        point_id=pt2,
        asset_id=None,
    )
    assert r_none.status_code in (200, 201), r_none.text
