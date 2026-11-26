"""Candidate-decision persistence regression for `POST /tickets/{id}/confirm` (task-25).

FR-13: 巡查员逐条确认/修改/剔除 AI 候选（改判 FLICKERING/ELECTRIC_HAZARD 须附 manual_note）。

覆盖范围（全部走真实 ASGI + `tomori_test` + OpenCV 基线推理，无 mock）：
- 混合候选：剔除 A（OCCLUDED）确认 B（FIXTURE_DAMAGED），最终 fault_type 必须来自
  被保留决策、且不得等于被剔除候选的类型；
- timeline 审计：`TICKET_CONFIRMED` 的 `business_snapshot_json.candidate_decisions`
  逐条落库（evidence_id/candidate_id/decision/fault_type/manual_note），详情时间线可读；
- 完整性守卫：重复 / 遗漏 / 跨票 / 未知候选 / 全剔除 -> 422 且草稿原子不变；
- 旧草稿无候选（`ai_result_json` 为 SQL NULL 或仅含非故障候选）不得被确认；
- 同名跨图：不同证据各自产出 `cand-001`，决策键必须是 `(evidence_id, candidate_id)`；
- `modified` 决策的 fault_type 依据（`FLICKERING`/`ELECTRIC_HAZARD` 强制 manual_note，
  最终类型落入视觉禁产类型时 report_source 翻转为 MANUAL_INSPECT）；
- 幂等：`request_payload_hash` 规范化 SHA-256 落库、原样重放、载荷不一致拒绝；
- 事务回滚：确认中途触发部分唯一索引 -> 500 且草稿/审计/幂等键全部回滚可重试。

合成图（经本地 `baseline.detect + mapping.build_result` 验证确定性输出）：
绿块 -> `streetlight_occluded`(OCCLUDED)；高频棋盘 -> `streetlight_damaged`(FIXTURE_DAMAGED)。
"""

from __future__ import annotations

import asyncio
import io
import uuid
from datetime import UTC, datetime
from typing import Any

from httpx import AsyncClient, Response
from PIL import Image, ImageDraw
from sqlalchemy import func, null, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from tests.integration.conftest import (
    auth_headers_for,
    build_confirm_payload,
    make_occluded_fault_png,
    new_idem_key,
)
from tomori.api.idempotency import hash_json_payload
from tomori.models.account import Account
from tomori.models.audit_log import AuditLog
from tomori.models.fault_evidence import FaultEvidence
from tomori.models.fault_ticket import FaultTicket
from tomori.models.lighting_point import LightingPoint
from tomori.models.notification_task import NotificationTask
from tomori.schemas.ticket import TicketConfirmIn

# ──────────────────────────────────────────────────────────────────────
# 合成图（与 tests/integration/conftest.py 的 make_*_png 同族，仅位置/纹理不同）
# ──────────────────────────────────────────────────────────────────────


def _occluded_png_at(x0: int, y0: int, size: int = 42) -> bytes:
    """不同位置的绿植块 -> 同类型 OCCLUDED 但不同 sha256 的独立证据。"""
    img = Image.new("RGB", (400, 400), (10, 10, 15))
    draw = ImageDraw.Draw(img)
    draw.rectangle((x0, y0, x0 + size, y0 + size), fill=(0, 140, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _mixed_fault_png() -> bytes:
    """单图双候选：cand-001 OCCLUDED（先检出）+ cand-002 FIXTURE_DAMAGED。"""
    img = Image.new("RGB", (400, 400), (10, 10, 15))
    draw = ImageDraw.Draw(img)
    draw.rectangle((60, 60, 110, 110), fill=(0, 140, 0))
    for y in range(260, 340, 4):
        for x in range(260, 340, 4):
            shade = (200, 200, 200) if (x // 4 + y // 4) % 2 == 0 else (30, 30, 30)
            draw.rectangle((x, y, x + 3, y + 3), fill=shade)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ──────────────────────────────────────────────────────────────────────
# 业务助手
# ──────────────────────────────────────────────────────────────────────


async def _point_id(client: AsyncClient, headers: dict[str, str], code: str) -> int:
    items = (await client.get("/api/v1/lighting-points", headers=headers)).json()["items"]
    return int(next(p for p in items if p["point_code"] == code)["id"])


async def _new_ai_draft(
    client: AsyncClient,
    headers: dict[str, str],
    *,
    point_id: int,
    images: list[bytes],
) -> dict[str, Any]:
    """走真实 `POST /tickets/ai-candidates` 建 `AI_CANDIDATE_PENDING` 草稿。"""
    files = [("files", (f"scan_{i}.png", blob, "image/png")) for i, blob in enumerate(images)]
    r = await client.post(
        "/api/v1/tickets/ai-candidates",
        data={"lighting_point_id": str(point_id), "scene_lighting_hint": "night"},
        files=files,
        headers=headers,
    )
    assert r.status_code == 201, r.text
    body: dict[str, Any] = r.json()
    assert body["ticket"]["status"] == "AI_CANDIDATE_PENDING"
    return body


def _fault_candidates(evidences: list[dict[str, Any]]) -> dict[tuple[int, str], dict[str, Any]]:
    """`(evidence_id, candidate_id)` -> 候选载荷，与服务端决策键同构。"""
    return {
        (int(ev["id"]), str(c["candidate_id"])): c
        for ev in evidences
        for c in ev["ai_result_json"]["candidates"]
        if c["is_fault_candidate"]
    }


def _decision(
    evidence_id: int,
    candidate_id: str,
    decision: str,
    **extra: Any,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "evidence_id": evidence_id,
        "candidate_id": candidate_id,
        "decision": decision,
    }
    entry.update(extra)
    return entry


async def _confirm(
    client: AsyncClient,
    headers: dict[str, str],
    ticket_id: int,
    payload: dict[str, Any],
) -> Response:
    return await client.post(
        f"/api/v1/tickets/{ticket_id}/confirm",
        json=payload,
        headers=headers,
    )


async def _ticket_row(engine: AsyncEngine, ticket_id: int) -> FaultTicket:
    async with AsyncSession(engine, expire_on_commit=False) as s:
        ticket = await s.scalar(select(FaultTicket).where(FaultTicket.id == ticket_id))
    assert ticket is not None
    return ticket


async def _audit_rows(engine: AsyncEngine, ticket_id: int) -> list[AuditLog]:
    async with AsyncSession(engine, expire_on_commit=False) as s:
        rows = (
            await s.scalars(
                select(AuditLog).where(AuditLog.ticket_id == ticket_id).order_by(AuditLog.id)
            )
        ).all()
    return list(rows)


async def _assert_draft_untouched(engine: AsyncEngine, ticket_id: int) -> None:
    """失败确认后草稿必须原子不变：状态/确认字段原样且无任何确认/分级审计行。"""
    ticket = await _ticket_row(engine, ticket_id)
    assert ticket.status == "AI_CANDIDATE_PENDING"
    assert ticket.confirmed_by_inspector is False
    assert ticket.confirmed_by_inspector_id is None
    assert ticket.confirmed_at is None
    assert ticket.repair_deadline_at is None
    actions = {row.action for row in await _audit_rows(engine, ticket_id)}
    assert "TICKET_CONFIRMED" not in actions
    assert "TICKET_GRADED" not in actions


# ──────────────────────────────────────────────────────────────────────
# 1. 混合候选：剔除 A 确认 B，最终类型非 A
# ──────────────────────────────────────────────────────────────────────


async def test_mixed_candidates_reject_a_confirm_b_final_type_not_a(
    api_client: AsyncClient,
    async_engine: AsyncEngine,
) -> None:
    """剔除 OCCLUDED、确认 FIXTURE_DAMAGED -> 终类型为 B 且被剔除类型不可作终类型。"""
    admin_hdr = await auth_headers_for(api_client, "admin")
    insp_hdr = await auth_headers_for(api_client, "inspector1")
    pt_id = await _point_id(api_client, admin_hdr, "SL-ZZ-JS-0001")

    body = await _new_ai_draft(
        api_client,
        {**insp_hdr, "Idempotency-Key": new_idem_key("ai-mix")},
        point_id=pt_id,
        images=[_mixed_fault_png()],
    )
    draft = body["ticket"]
    cands = _fault_candidates(body["evidences"])
    assert {c["candidate_fault_type"] for c in cands.values()} == {
        "OCCLUDED",
        "FIXTURE_DAMAGED",
    }
    occ_key = next(k for k, c in cands.items() if c["candidate_fault_type"] == "OCCLUDED")
    dmg_key = next(k for k, c in cands.items() if c["candidate_fault_type"] == "FIXTURE_DAMAGED")
    # 并列最高置信度时 cand-001（OCCLUDED）胜出成为草稿初判类型
    assert draft["fault_type"] == "OCCLUDED"

    key = new_idem_key("conf-mix")
    decisions = [
        _decision(occ_key[0], occ_key[1], "rejected"),
        _decision(dmg_key[0], dmg_key[1], "confirmed"),
    ]

    # 被剔除候选的 OCCLUDED 不得成为最终类型（retained_types 只剩 FIXTURE_DAMAGED）
    r_bad_type = await _confirm(
        api_client,
        {**insp_hdr, "Idempotency-Key": key},
        draft["id"],
        {"fault_type": "OCCLUDED", "safety_flag": False, "candidate_decisions": decisions},
    )
    assert r_bad_type.status_code == 422
    err = r_bad_type.json()["error"]
    assert err["code"] == "VALIDATION_ERROR"
    assert err["details"]["field"] == "fault_type"
    await _assert_draft_untouched(async_engine, draft["id"])

    # 422 不占幂等键：同键重试保留类型 FIXTURE_DAMAGED -> 200
    r_ok = await _confirm(
        api_client,
        {**insp_hdr, "Idempotency-Key": key},
        draft["id"],
        {
            "fault_type": "FIXTURE_DAMAGED",
            "safety_flag": False,
            "candidate_decisions": decisions,
        },
    )
    assert r_ok.status_code == 200, r_ok.text
    confirmed = r_ok.json()
    assert confirmed["fault_type"] == "FIXTURE_DAMAGED"
    assert confirmed["fault_type"] != "OCCLUDED"
    assert confirmed["status"] == "PENDING_DISPATCH"
    assert confirmed["confirmed_by_inspector"] is True
    # JS-0001 MAIN+资产 HIGH -> R2（FIXTURE_DAMAGED 的 MAIN 型分支与资产分支均命中）
    assert confirmed["priority"] == "P2"

    ticket = await _ticket_row(async_engine, draft["id"])
    assert ticket.fault_type == "FIXTURE_DAMAGED"
    assert ticket.status == "PENDING_DISPATCH"


# ──────────────────────────────────────────────────────────────────────
# 2. timeline 审计逐条决定落库
# ──────────────────────────────────────────────────────────────────────


async def test_timeline_audit_persists_each_candidate_decision(
    api_client: AsyncClient,
    async_engine: AsyncEngine,
) -> None:
    """`TICKET_CONFIRMED` 快照逐条保存全部决策并经详情时间线可读。"""
    admin_hdr = await auth_headers_for(api_client, "admin")
    insp_hdr = await auth_headers_for(api_client, "inspector1")
    pt_id = await _point_id(api_client, admin_hdr, "SL-ZZ-JS-0001")

    body = await _new_ai_draft(
        api_client,
        {**insp_hdr, "Idempotency-Key": new_idem_key("ai-tl")},
        point_id=pt_id,
        images=[_mixed_fault_png()],
    )
    draft = body["ticket"]
    cands = _fault_candidates(body["evidences"])
    occ_key = next(k for k, c in cands.items() if c["candidate_fault_type"] == "OCCLUDED")
    dmg_key = next(k for k, c in cands.items() if c["candidate_fault_type"] == "FIXTURE_DAMAGED")

    key = new_idem_key("conf-tl")
    decisions = [
        _decision(occ_key[0], occ_key[1], "rejected"),
        _decision(dmg_key[0], dmg_key[1], "confirmed"),
    ]
    r_ok = await _confirm(
        api_client,
        {**insp_hdr, "Idempotency-Key": key},
        draft["id"],
        {
            "fault_type": "FIXTURE_DAMAGED",
            "safety_flag": False,
            "candidate_decisions": decisions,
        },
    )
    assert r_ok.status_code == 200, r_ok.text

    rows = await _audit_rows(async_engine, draft["id"])
    conf = [a for a in rows if a.action == "TICKET_CONFIRMED" and a.idempotency_key == key]
    assert len(conf) == 1
    snapshot = conf[0].business_snapshot_json or {}
    assert snapshot["from_status"] == "AI_CANDIDATE_PENDING"
    assert snapshot["to_status"] == "PENDING_DISPATCH"

    # 逐条回读：提交的每个决策原样（顺序+全字段）落库
    persisted = snapshot["candidate_decisions"]
    assert persisted == [
        {
            "evidence_id": occ_key[0],
            "candidate_id": occ_key[1],
            "decision": "rejected",
            "fault_type": None,
            "manual_note": None,
        },
        {
            "evidence_id": dmg_key[0],
            "candidate_id": dmg_key[1],
            "decision": "confirmed",
            "fault_type": None,
            "manual_note": None,
        },
    ]

    graded = [a for a in rows if a.action == "TICKET_GRADED"]
    assert len(graded) == 1
    assert graded[0].idempotency_key is None  # 附带审计行不占幂等唯一索引
    assert (graded[0].business_snapshot_json or {})["inputs"]["fault_type"] == ("FIXTURE_DAMAGED")

    # GET /tickets/{id} 详情时间线暴露同一逐条快照
    detail = (await api_client.get(f"/api/v1/tickets/{draft['id']}", headers=insp_hdr)).json()
    timeline_conf = [e for e in detail["timeline"] if e["action"] == "TICKET_CONFIRMED"]
    assert len(timeline_conf) == 1
    assert (timeline_conf[0]["business_snapshot_json"] or {})["candidate_decisions"] == (persisted)
    assert any(e["action"] == "TICKET_GRADED" for e in detail["timeline"])

    # 同事务通知任务落库（I8）
    async with AsyncSession(async_engine, expire_on_commit=False) as s:
        notif_cnt = int(
            (
                await s.scalar(
                    select(func.count(NotificationTask.id)).where(
                        NotificationTask.related_ticket_id == draft["id"],
                        NotificationTask.event_type == "TICKET_CONFIRMED",
                    )
                )
            )
            or 0
        )
    assert notif_cnt >= 1


# ──────────────────────────────────────────────────────────────────────
# 3–6. 完整性守卫：重复 / 遗漏 / 跨票 / 未知候选 / 全剔除
# ──────────────────────────────────────────────────────────────────────


async def test_duplicate_decision_entry_rejected_atomically(
    api_client: AsyncClient,
    async_engine: AsyncEngine,
) -> None:
    """同一 `(evidence_id, candidate_id)` 决策出现两次 -> 422 且草稿不变。"""
    admin_hdr = await auth_headers_for(api_client, "admin")
    insp_hdr = await auth_headers_for(api_client, "inspector1")
    pt_id = await _point_id(api_client, admin_hdr, "SL-ZZ-JS-0001")

    body = await _new_ai_draft(
        api_client,
        {**insp_hdr, "Idempotency-Key": new_idem_key("ai-dup")},
        point_id=pt_id,
        images=[make_occluded_fault_png()],
    )
    draft = body["ticket"]
    (only_key,) = _fault_candidates(body["evidences"]).keys()

    dup = _decision(only_key[0], only_key[1], "confirmed")
    r = await _confirm(
        api_client,
        {**insp_hdr, "Idempotency-Key": new_idem_key("conf-dup")},
        draft["id"],
        {
            "fault_type": "OCCLUDED",
            "safety_flag": False,
            "candidate_decisions": [dup, dict(dup)],
        },
    )
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "VALIDATION_ERROR"
    assert err["details"]["field"] == "candidate_decisions"
    await _assert_draft_untouched(async_engine, draft["id"])


async def test_missing_or_phantom_decision_rejected_atomically(
    api_client: AsyncClient,
    async_engine: AsyncEngine,
) -> None:
    """少提交一个候选 / 多提交不存在的候选键 -> 422 且草稿不变。"""
    admin_hdr = await auth_headers_for(api_client, "admin")
    insp_hdr = await auth_headers_for(api_client, "inspector1")
    pt_id = await _point_id(api_client, admin_hdr, "SL-ZZ-JS-0001")

    body = await _new_ai_draft(
        api_client,
        {**insp_hdr, "Idempotency-Key": new_idem_key("ai-miss")},
        point_id=pt_id,
        images=[_mixed_fault_png()],
    )
    draft = body["ticket"]
    cands = _fault_candidates(body["evidences"])
    key1, key2 = sorted(cands.keys())
    ev_id = body["evidences"][0]["id"]

    # 遗漏：只为 key1 提交处置 -> key2 仍欠处置
    r_missing = await _confirm(
        api_client,
        {**insp_hdr, "Idempotency-Key": new_idem_key("conf-miss")},
        draft["id"],
        {
            "fault_type": cands[key1]["candidate_fault_type"],
            "safety_flag": False,
            "candidate_decisions": [_decision(key1[0], key1[1], "confirmed")],
        },
    )
    assert r_missing.status_code == 422
    err = r_missing.json()["error"]
    assert err["code"] == "VALIDATION_ERROR"
    assert err["details"]["field"] == "candidate_decisions"

    # 幻影键：全部真候选 + 一个不存在于任何证据的 candidate_id -> 键集不匹配
    r_phantom = await _confirm(
        api_client,
        {**insp_hdr, "Idempotency-Key": new_idem_key("conf-phantom")},
        draft["id"],
        {
            "fault_type": cands[key1]["candidate_fault_type"],
            "safety_flag": False,
            "candidate_decisions": [
                _decision(key1[0], key1[1], "confirmed"),
                _decision(key2[0], key2[1], "confirmed"),
                _decision(ev_id, "cand-999", "confirmed"),
            ],
        },
    )
    assert r_phantom.status_code == 422
    assert r_phantom.json()["error"]["details"]["field"] == "candidate_decisions"
    await _assert_draft_untouched(async_engine, draft["id"])


async def test_cross_ticket_evidence_decision_rejected(
    api_client: AsyncClient,
    async_engine: AsyncEngine,
) -> None:
    """用票 B 的 `(evidence_id, candidate_id)` 向票 A 提交 -> 422，两票均不变。"""
    admin_hdr = await auth_headers_for(api_client, "admin")
    insp_hdr = await auth_headers_for(api_client, "inspector1")
    pt1 = await _point_id(api_client, admin_hdr, "SL-ZZ-JS-0001")
    pt2 = await _point_id(api_client, admin_hdr, "SL-ZZ-JS-0002")

    body_a = await _new_ai_draft(
        api_client,
        {**insp_hdr, "Idempotency-Key": new_idem_key("ai-xa")},
        point_id=pt1,
        images=[make_occluded_fault_png()],
    )
    body_b = await _new_ai_draft(
        api_client,
        {**insp_hdr, "Idempotency-Key": new_idem_key("ai-xb")},
        point_id=pt2,
        images=[_occluded_png_at(60, 60)],
    )
    draft_a, draft_b = body_a["ticket"], body_b["ticket"]
    (key_a,) = _fault_candidates(body_a["evidences"]).keys()
    (key_b,) = _fault_candidates(body_b["evidences"]).keys()
    assert key_a != key_b

    r = await _confirm(
        api_client,
        {**insp_hdr, "Idempotency-Key": new_idem_key("conf-x")},
        draft_a["id"],
        {
            "fault_type": "OCCLUDED",
            "safety_flag": False,
            "candidate_decisions": [
                _decision(key_a[0], key_a[1], "confirmed"),
                _decision(key_b[0], key_b[1], "confirmed"),
            ],
        },
    )
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "VALIDATION_ERROR"
    assert err["details"]["field"] == "candidate_decisions"
    await _assert_draft_untouched(async_engine, draft_a["id"])
    await _assert_draft_untouched(async_engine, draft_b["id"])

    # 票 A 用自身完整决策集仍可确认 -> 跨票噪声未被吞进本票
    r_ok = await _confirm(
        api_client,
        {**insp_hdr, "Idempotency-Key": new_idem_key("conf-xa")},
        draft_a["id"],
        {
            "fault_type": "OCCLUDED",
            "safety_flag": False,
            "candidate_decisions": [_decision(key_a[0], key_a[1], "confirmed")],
        },
    )
    assert r_ok.status_code == 200, r_ok.text


async def test_all_candidates_rejected_blocks_confirm(
    api_client: AsyncClient,
    async_engine: AsyncEngine,
) -> None:
    """全部剔除 -> retained_types 为空 -> 422，草稿原子不变。"""
    admin_hdr = await auth_headers_for(api_client, "admin")
    insp_hdr = await auth_headers_for(api_client, "inspector1")
    pt_id = await _point_id(api_client, admin_hdr, "SL-ZZ-JS-0001")

    body = await _new_ai_draft(
        api_client,
        {**insp_hdr, "Idempotency-Key": new_idem_key("ai-rej")},
        point_id=pt_id,
        images=[make_occluded_fault_png()],
    )
    draft = body["ticket"]
    (only_key,) = _fault_candidates(body["evidences"]).keys()

    r = await _confirm(
        api_client,
        {**insp_hdr, "Idempotency-Key": new_idem_key("conf-rej")},
        draft["id"],
        {
            "fault_type": "OCCLUDED",
            "safety_flag": False,
            "candidate_decisions": [_decision(only_key[0], only_key[1], "rejected")],
        },
    )
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "VALIDATION_ERROR"
    # 「工单故障类型必须来自至少一个保留候选」落点字段为 fault_type
    assert err["details"]["field"] == "fault_type"
    await _assert_draft_untouched(async_engine, draft["id"])


# ──────────────────────────────────────────────────────────────────────
# 7. 同名跨图：候选键必须含 evidence_id
# ──────────────────────────────────────────────────────────────────────


async def test_same_candidate_id_across_images_keyed_by_evidence(
    api_client: AsyncClient,
    async_engine: AsyncEngine,
) -> None:
    """两张图各自产出 `cand-001`：决策键 `(evidence_id, candidate_id)` 缺一不可。"""
    admin_hdr = await auth_headers_for(api_client, "admin")
    insp_hdr = await auth_headers_for(api_client, "inspector1")
    pt_id = await _point_id(api_client, admin_hdr, "SL-ZZ-JS-0001")

    body = await _new_ai_draft(
        api_client,
        {**insp_hdr, "Idempotency-Key": new_idem_key("ai-ximg")},
        point_id=pt_id,
        images=[_occluded_png_at(60, 60), _occluded_png_at(280, 280)],
    )
    draft = body["ticket"]
    evidences = body["evidences"]
    assert len(evidences) == 2
    cands = _fault_candidates(evidences)
    assert len(cands) == 2
    # 两条证据的故障候选同名（不同 evidence_id），证实冲突场景真实存在
    assert len({k[1] for k in cands}) == 1
    (key1, key2) = sorted(cands.keys())
    assert key1[0] != key2[0] and key1[1] == key2[1]

    # 只为 ev1/cand-001 决策 -> ev2 的同名候选仍欠处置
    r_partial = await _confirm(
        api_client,
        {**insp_hdr, "Idempotency-Key": new_idem_key("conf-partial")},
        draft["id"],
        {
            "fault_type": "OCCLUDED",
            "safety_flag": False,
            "candidate_decisions": [_decision(key1[0], key1[1], "confirmed")],
        },
    )
    assert r_partial.status_code == 422
    assert r_partial.json()["error"]["details"]["field"] == "candidate_decisions"
    await _assert_draft_untouched(async_engine, draft["id"])

    # 同名但不同证据：剔除 ev1/cand-001、确认 ev2/cand-001 -> 200
    r_ok = await _confirm(
        api_client,
        {**insp_hdr, "Idempotency-Key": new_idem_key("conf-ximg")},
        draft["id"],
        {
            "fault_type": "OCCLUDED",
            "safety_flag": False,
            "candidate_decisions": [
                _decision(key1[0], key1[1], "rejected"),
                _decision(key2[0], key2[1], "confirmed"),
            ],
        },
    )
    assert r_ok.status_code == 200, r_ok.text
    assert r_ok.json()["fault_type"] == "OCCLUDED"
    assert r_ok.json()["status"] == "PENDING_DISPATCH"

    rows = await _audit_rows(async_engine, draft["id"])
    conf = next(a for a in rows if a.action == "TICKET_CONFIRMED")
    persisted = (conf.business_snapshot_json or {})["candidate_decisions"]
    assert {(d["evidence_id"], d["candidate_id"]) for d in persisted} == {key1, key2}


# ──────────────────────────────────────────────────────────────────────
# 8. modified 决策的 fault_type 依据与 manual_note 守卫
# ──────────────────────────────────────────────────────────────────────


async def test_modified_decision_fault_type_basis_and_manual_note_guards(
    api_client: AsyncClient,
    async_engine: AsyncEngine,
) -> None:
    """`modified` 以 entry.fault_type 为依据；禁产类型须 manual_note 且翻来源。"""
    admin_hdr = await auth_headers_for(api_client, "admin")
    insp_hdr = await auth_headers_for(api_client, "inspector1")
    pt1 = await _point_id(api_client, admin_hdr, "SL-ZZ-JS-0001")
    pt2 = await _point_id(api_client, admin_hdr, "SL-ZZ-JS-0002")

    body = await _new_ai_draft(
        api_client,
        {**insp_hdr, "Idempotency-Key": new_idem_key("ai-mod")},
        point_id=pt1,
        images=[make_occluded_fault_png()],
    )
    draft = body["ticket"]
    (only_key,) = _fault_candidates(body["evidences"]).keys()
    ev_id, cand_id = only_key

    # schema 守卫（进不了服务层）：modified 缺 fault_type
    r_no_type = await _confirm(
        api_client,
        {**insp_hdr, "Idempotency-Key": new_idem_key("conf-mod1")},
        draft["id"],
        {
            "fault_type": "OTHER",
            "safety_flag": False,
            "candidate_decisions": [_decision(ev_id, cand_id, "modified")],
        },
    )
    assert r_no_type.status_code == 422
    assert r_no_type.json()["error"]["code"] == "VALIDATION_ERROR"

    # schema 守卫：modified->FLICKERING 缺 manual_note
    r_no_note = await _confirm(
        api_client,
        {**insp_hdr, "Idempotency-Key": new_idem_key("conf-mod2")},
        draft["id"],
        {
            "fault_type": "FLICKERING",
            "safety_flag": False,
            "candidate_decisions": [_decision(ev_id, cand_id, "modified", fault_type="FLICKERING")],
        },
    )
    assert r_no_note.status_code == 422
    assert r_no_note.json()["error"]["code"] == "VALIDATION_ERROR"

    # schema 守卫：非 modified 决策不得携带 fault_type / manual_note
    for bad_decision in (
        _decision(ev_id, cand_id, "confirmed", fault_type="OCCLUDED"),
        _decision(ev_id, cand_id, "rejected", manual_note="x"),
    ):
        r_bad = await _confirm(
            api_client,
            {**insp_hdr, "Idempotency-Key": new_idem_key("conf-modx")},
            draft["id"],
            {
                "fault_type": "OCCLUDED",
                "safety_flag": False,
                "candidate_decisions": [bad_decision],
            },
        )
        assert r_bad.status_code == 422
        assert r_bad.json()["error"]["code"] == "VALIDATION_ERROR"

    # modified 依据取代 AI 原类型：retained={OTHER}，提交 OCCLUDED -> 422
    r_wrong_type = await _confirm(
        api_client,
        {**insp_hdr, "Idempotency-Key": new_idem_key("conf-mod3")},
        draft["id"],
        {
            "fault_type": "OCCLUDED",
            "safety_flag": False,
            "candidate_decisions": [_decision(ev_id, cand_id, "modified", fault_type="OTHER")],
        },
    )
    assert r_wrong_type.status_code == 422
    assert r_wrong_type.json()["error"]["details"]["field"] == "fault_type"
    await _assert_draft_untouched(async_engine, draft["id"])

    # AI 草稿不继承点位资产：先经 PATCH /tickets/{id} 合法关联 JS-0001 的 HIGH 资产
    assets = (
        await api_client.get(f"/api/v1/facility-assets?lighting_point_id={pt1}", headers=admin_hdr)
    ).json()
    high_asset = next(
        a
        for a in (assets["items"] if isinstance(assets, dict) else assets)
        if a["importance"] == "HIGH"
    )
    r_patch = await api_client.patch(
        f"/api/v1/tickets/{draft['id']}",
        json={"facility_asset_id": high_asset["id"]},
        headers=insp_hdr,
    )
    assert r_patch.status_code == 200, r_patch.text

    # modified->OTHER：终类型以 decision.fault_type 为依据而非 AI 的 OCCLUDED
    r_mod = await _confirm(
        api_client,
        {**insp_hdr, "Idempotency-Key": new_idem_key("conf-mod4")},
        draft["id"],
        {
            "fault_type": "OTHER",
            "safety_flag": False,
            "candidate_decisions": [_decision(ev_id, cand_id, "modified", fault_type="OTHER")],
        },
    )
    assert r_mod.status_code == 200, r_mod.text
    updated = r_mod.json()
    assert updated["fault_type"] == "OTHER"
    assert updated["report_source"] == "AI_CANDIDATE"  # OTHER 非禁产类型，来源不变
    assert updated["priority"] == "P2"  # JS-0001 关联 HIGH 资产 -> R2（与 fault_type 无关）

    rows = await _audit_rows(async_engine, draft["id"])
    conf = next(a for a in rows if a.action == "TICKET_CONFIRMED")
    persisted = (conf.business_snapshot_json or {})["candidate_decisions"]
    assert persisted[0]["decision"] == "modified"
    assert persisted[0]["fault_type"] == "OTHER"

    # modified->FLICKERING + manual_note：终类型为禁产类型 -> report_source 翻转
    body2 = await _new_ai_draft(
        api_client,
        {**insp_hdr, "Idempotency-Key": new_idem_key("ai-modf")},
        point_id=pt2,
        images=[_occluded_png_at(60, 60)],
    )
    draft2 = body2["ticket"]
    (key2,) = _fault_candidates(body2["evidences"]).keys()
    assets2 = (
        await api_client.get(f"/api/v1/facility-assets?lighting_point_id={pt2}", headers=admin_hdr)
    ).json()
    crit_asset = next(
        a
        for a in (assets2["items"] if isinstance(assets2, dict) else assets2)
        if a["importance"] == "CRITICAL"
    )
    r_patch2 = await api_client.patch(
        f"/api/v1/tickets/{draft2['id']}",
        json={"facility_asset_id": crit_asset["id"]},
        headers=insp_hdr,
    )
    assert r_patch2.status_code == 200, r_patch2.text
    r_modf = await _confirm(
        api_client,
        {**insp_hdr, "Idempotency-Key": new_idem_key("conf-modf")},
        draft2["id"],
        {
            "fault_type": "FLICKERING",
            "safety_flag": False,
            "candidate_decisions": [
                _decision(
                    key2[0],
                    key2[1],
                    "modified",
                    fault_type="FLICKERING",
                    manual_note="现场疑似闪烁，需带仪器复核",
                )
            ],
        },
    )
    assert r_modf.status_code == 200, r_modf.text
    updated2 = r_modf.json()
    assert updated2["fault_type"] == "FLICKERING"
    assert updated2["report_source"] == "MANUAL_INSPECT"
    assert updated2["priority"] == "P1"  # JS-0002 EXPRESS+CRITICAL 资产 -> R1

    rows2 = await _audit_rows(async_engine, draft2["id"])
    conf2 = next(a for a in rows2 if a.action == "TICKET_CONFIRMED")
    persisted2 = (conf2.business_snapshot_json or {})["candidate_decisions"]
    assert persisted2[0]["decision"] == "modified"
    assert persisted2[0]["fault_type"] == "FLICKERING"
    assert persisted2[0]["manual_note"] == "现场疑似闪烁，需带仪器复核"

    ticket2 = await _ticket_row(async_engine, draft2["id"])
    assert ticket2.report_source == "MANUAL_INSPECT"


# ──────────────────────────────────────────────────────────────────────
# 9. 幂等 hash 与重放
# ──────────────────────────────────────────────────────────────────────


async def test_confirm_idempotency_replay_and_payload_hash_persisted(
    api_client: AsyncClient,
    async_engine: AsyncEngine,
) -> None:
    """确认幂等键复用：规范化 SHA-256 落库、原样重放、改载荷 422、新键重新执行。"""
    admin_hdr = await auth_headers_for(api_client, "admin")
    insp_hdr = await auth_headers_for(api_client, "inspector1")
    pt_id = await _point_id(api_client, admin_hdr, "SL-ZZ-JS-0001")

    body = await _new_ai_draft(
        api_client,
        {**insp_hdr, "Idempotency-Key": new_idem_key("ai-idem")},
        point_id=pt_id,
        images=[make_occluded_fault_png()],
    )
    draft = body["ticket"]
    payload = build_confirm_payload(body["evidences"])
    path = f"/api/v1/tickets/{draft['id']}/confirm"

    # 缺幂等键 -> 400
    r_nokey = await api_client.post(path, json=payload, headers=insp_hdr)
    assert r_nokey.status_code == 400
    assert r_nokey.json()["error"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"

    key = new_idem_key("conf-idem")
    r_ok = await api_client.post(path, json=payload, headers={**insp_hdr, "Idempotency-Key": key})
    assert r_ok.status_code == 200, r_ok.text
    first_body: dict[str, Any] = r_ok.json()
    assert r_ok.headers.get("x-idempotent-replay") is None

    # 同键同载荷 -> 原样重放（不重执行、不写第二行）
    r_replay = await api_client.post(
        path, json=payload, headers={**insp_hdr, "Idempotency-Key": key}
    )
    assert r_replay.status_code == 200
    assert r_replay.headers.get("x-idempotent-replay") == "true"
    assert r_replay.json() == first_body

    # 规范化载荷 hash 与幂等键原样落库；响应体一并持久化供重放
    expected_hash = hash_json_payload(
        TicketConfirmIn.model_validate(payload).model_dump(mode="json")
    )
    async with AsyncSession(async_engine, expire_on_commit=False) as s:
        rows = (await s.scalars(select(AuditLog).where(AuditLog.idempotency_key == key))).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.action == "TICKET_CONFIRMED"
    assert row.ticket_id == draft["id"]
    assert row.request_payload_hash == expected_hash
    assert row.response_status == 200
    assert row.response_body_json == first_body
    assert (row.request_payload or {})["candidate_decisions"]

    # 同键不同载荷 -> 422 IDEMPOTENCY_PAYLOAD_MISMATCH（details 带双 hash）
    r_mis = await api_client.post(
        path,
        json=build_confirm_payload(body["evidences"], safety_flag=True),
        headers={**insp_hdr, "Idempotency-Key": key},
    )
    assert r_mis.status_code == 422
    err = r_mis.json()["error"]
    assert err["code"] == "IDEMPOTENCY_PAYLOAD_MISMATCH"
    assert err["details"]["original_payload_hash"] == expected_hash
    assert err["details"]["current_payload_hash"] != expected_hash

    # 新键重新执行（非重放）：已确认工单再过状态机守卫 -> 422 INVALID_STATE_TRANSITION
    r_again = await api_client.post(
        path,
        json=payload,
        headers={**insp_hdr, "Idempotency-Key": new_idem_key("conf-again")},
    )
    assert r_again.status_code == 422
    assert r_again.json()["error"]["code"] == "INVALID_STATE_TRANSITION"


# ──────────────────────────────────────────────────────────────────────
# 10. 事务回滚：终类型撞活跃单部分唯一索引 -> 500 全回滚、键可复用
# ──────────────────────────────────────────────────────────────────────


async def test_confirm_rollback_on_active_type_collision(
    api_client: AsyncClient,
    async_engine: AsyncEngine,
) -> None:
    """改判撞同点位活跃工单返回 409，回滚草稿并允许同键重试。"""
    admin_hdr = await auth_headers_for(api_client, "admin")
    insp_hdr = await auth_headers_for(api_client, "inspector1")
    pt_id = await _point_id(api_client, admin_hdr, "SL-ZZ-JS-0002")

    # 既有活跃 OTHER 人工单，占住 (point, OTHER) 活跃唯一位
    r_manual = await api_client.post(
        "/api/v1/tickets",
        json={
            "lighting_point_id": pt_id,
            "fault_type": "OTHER",
            "title": "既有 OTHER 活跃单",
            "safety_flag": False,
        },
        headers={**insp_hdr, "Idempotency-Key": new_idem_key("manual-rb")},
    )
    assert r_manual.status_code == 201

    body = await _new_ai_draft(
        api_client,
        {**insp_hdr, "Idempotency-Key": new_idem_key("ai-rb")},
        point_id=pt_id,
        images=[_occluded_png_at(60, 60)],
    )
    draft = body["ticket"]
    (only_key,) = _fault_candidates(body["evidences"]).keys()
    assert draft["fault_type"] == "OCCLUDED"

    key = new_idem_key("conf-rb")
    r_conflict = await _confirm(
        api_client,
        {**insp_hdr, "Idempotency-Key": key},
        draft["id"],
        {
            "fault_type": "OTHER",
            "safety_flag": False,
            "candidate_decisions": [
                _decision(only_key[0], only_key[1], "modified", fault_type="OTHER")
            ],
        },
    )
    assert r_conflict.status_code == 409, r_conflict.text
    assert r_conflict.json()["error"]["code"] == "DUPLICATE_ACTIVE_TICKET"

    # 全回滚：草稿仍为 OCCLUDED 待确认草稿，无确认/分级审计，幂等键未落库
    ticket = await _ticket_row(async_engine, draft["id"])
    assert ticket.fault_type == "OCCLUDED"
    await _assert_draft_untouched(async_engine, draft["id"])
    async with AsyncSession(async_engine, expire_on_commit=False) as s:
        leaked = int(
            (await s.scalar(select(func.count(AuditLog.id)).where(AuditLog.idempotency_key == key)))
            or 0
        )
    assert leaked == 0

    # 同事务级咨询锁随回滚释放：同键改用合法决策可重新执行成功
    r_retry = await _confirm(
        api_client,
        {**insp_hdr, "Idempotency-Key": key},
        draft["id"],
        build_confirm_payload(body["evidences"]),
    )
    assert r_retry.status_code == 200, r_retry.text
    assert r_retry.json()["fault_type"] == "OCCLUDED"
    assert r_retry.json()["status"] == "PENDING_DISPATCH"


# ──────────────────────────────────────────────────────────────────────
# 11. 并发兜底：未提交插入占唯一位 -> savepoint flush 撞 23505 -> 409 + 回滚
# ──────────────────────────────────────────────────────────────────────


async def test_confirm_savepoint_fallback_on_concurrent_unique_violation(
    api_client: AsyncClient,
    async_engine: AsyncEngine,
) -> None:
    """强制两事务经预查后竞态唯一约束（区别于顺序预查分支）：

    T1 持未提交的活跃 OTHER 工单（读已提交快照外），confirm 的应用层预查
    看不到它 -> 走到 savepoint flush 才撞 23505 -> 必须映射 409 并整体回滚；
    兜底分支不得访问过期 ORM 属性（MissingGreenlet）。"""
    insp_hdr = await auth_headers_for(api_client, "inspector1")
    admin_hdr = await auth_headers_for(api_client, "admin")
    pt_id = await _point_id(api_client, admin_hdr, "SL-ZZ-JS-0002")
    t0 = datetime.now(UTC)

    body = await _new_ai_draft(
        api_client,
        {**insp_hdr, "Idempotency-Key": new_idem_key("ai-race")},
        point_id=pt_id,
        images=[_occluded_png_at(60, 60)],
    )
    draft = body["ticket"]
    (only_key,) = _fault_candidates(body["evidences"]).keys()

    # T1：未提交插入占住 (point, OTHER) 活跃唯一位，独占期间不提交
    holder = AsyncSession(async_engine, expire_on_commit=False)
    try:
        insp = await holder.scalar(select(Account).where(Account.username == "inspector1"))
        point = await holder.scalar(select(LightingPoint).where(LightingPoint.id == pt_id))
        assert insp is not None and point is not None
        holder.add(
            FaultTicket(
                ticket_no=f"FT-RACE-{uuid.uuid4().hex[:8].upper()}",
                region_id=point.region_id,
                lighting_point_id=point.id,
                report_source="MANUAL_INSPECT",
                fault_type="OTHER",
                title="竞态占位 OTHER 单",
                status="PENDING_DISPATCH",
                reporter_account_id=insp.id,
                confirmed_by_inspector=True,
                confirmed_by_inspector_id=insp.id,
                confirmed_at=t0,
            )
        )
        await holder.flush()  # 唯一位已占，仍不可见

        # T2：confirm modified->OTHER —— 预查看不见 T1，flush 时被索引阻塞
        key = new_idem_key("conf-race")
        confirm_task = asyncio.create_task(
            _confirm(
                api_client,
                {**insp_hdr, "Idempotency-Key": key},
                draft["id"],
                {
                    "fault_type": "OTHER",
                    "safety_flag": False,
                    "candidate_decisions": [
                        _decision(only_key[0], only_key[1], "modified", fault_type="OTHER")
                    ],
                },
            )
        )
        # 等 T2 真正阻塞在唯一索引上再释放 T1，确保走 savepoint 兜底
        for _ in range(50):
            await asyncio.sleep(0.1)
            blocked = await holder.scalar(
                text(
                    "SELECT count(*) FROM pg_locks l JOIN pg_stat_activity a"
                    " ON a.pid = l.pid WHERE NOT l.granted"
                    " AND a.query LIKE '%UPDATE fault_tickets%'"
                )
            )
            if blocked:
                break
        assert blocked, "confirm 未观测到唯一索引等待，不能证明 savepoint 兜底被执行"
        await holder.commit()
        r = await asyncio.wait_for(confirm_task, timeout=30)
    finally:
        await holder.close()

    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "DUPLICATE_ACTIVE_TICKET"

    ticket = await _ticket_row(async_engine, draft["id"])
    assert ticket.fault_type == "OCCLUDED"
    await _assert_draft_untouched(async_engine, draft["id"])
    async with AsyncSession(async_engine, expire_on_commit=False) as s:
        leaked = int(
            (await s.scalar(select(func.count(AuditLog.id)).where(AuditLog.idempotency_key == key)))
            or 0
        )
    assert leaked == 0


# ──────────────────────────────────────────────────────────────────────
# 12. 旧草稿无候选：历史遗留 AI_CANDIDATE_PENDING 不得被确认
# ──────────────────────────────────────────────────────────────────────


async def test_legacy_draft_without_fault_candidates_cannot_confirm(
    api_client: AsyncClient,
    async_engine: AsyncEngine,
) -> None:
    """直插一张无故障候选的旧草稿（NULL 与全非故障候选两种遗留形态）-> 422。"""
    insp_hdr = await auth_headers_for(api_client, "inspector1")

    async with AsyncSession(async_engine, expire_on_commit=False) as s:
        insp = await s.scalar(select(Account).where(Account.username == "inspector1"))
        point = await s.scalar(
            select(LightingPoint).where(LightingPoint.point_code == "SL-ZZ-JS-0002")
        )
        assert insp is not None and point is not None
        legacy = FaultTicket(
            ticket_no="FT-LEGACY-0001",
            region_id=point.region_id,
            lighting_point_id=point.id,
            facility_asset_id=None,
            report_source="AI_CANDIDATE",
            fault_type="OCCLUDED",
            title="[遗留] 无候选 AI 草稿",
            status="AI_CANDIDATE_PENDING",
            reporter_account_id=insp.id,
            ai_suggestion_json={"primary_fault_type": "OCCLUDED"},
        )
        s.add(legacy)
        await s.flush()
        # 遗留形态 1：ai_result_json 为 SQL NULL（必须 null()，None 会落成 'null'::jsonb）
        s.add(
            FaultEvidence(
                ticket_id=legacy.id,
                analysis_id=uuid.uuid4(),
                evidence_type="INSPECTION_PHOTO",
                media_mime="image/png",
                storage_path="legacy/ev_null.png",
                file_size_bytes=1024,
                sha256="a" * 64,
                image_width=400,
                image_height=400,
                ai_result_json=null(),
                ai_is_baseline_mode=False,
                uploaded_by_account_id=insp.id,
            )
        )
        # 遗留形态 2：有 AI 结果但全部为非故障参照框（NORMAL_LIT）
        s.add(
            FaultEvidence(
                ticket_id=legacy.id,
                analysis_id=uuid.uuid4(),
                evidence_type="INSPECTION_PHOTO",
                media_mime="image/png",
                storage_path="legacy/ev_nonfault.png",
                file_size_bytes=2048,
                sha256="b" * 64,
                image_width=400,
                image_height=400,
                ai_result_json={
                    "analysis_id": str(uuid.uuid4()),
                    "inference_mode": "baseline_pretrained_opencv",
                    "ai_is_baseline_mode": True,
                    "model_version": "baseline-opencv-heuristic-v1",
                    "image_width": 400,
                    "image_height": 400,
                    "original_image_url": "legacy.png",
                    "annotated_image_url": None,
                    "image_quality": {
                        "quality_grade": "pass",
                        "laplacian_var": 100.0,
                        "brightness_mean_v": 30.0,
                        "overexposed_ratio": 0.0,
                        "underexposed_ratio": 0.5,
                        "scene_lighting": "night",
                        "warnings": [],
                    },
                    "primary_fault_type_suggestion": None,
                    "max_fused_confidence": 0.5,
                    "candidates": [
                        {
                            "candidate_id": "cand-001",
                            "raw_class": "streetlight_on",
                            "candidate_fault_type": None,
                            "visual_signal_state": "NORMAL_LIT",
                            "is_fault_candidate": False,
                            "bbox_norm": [0.5, 0.5, 0.6, 0.6],
                            "bbox_px": [200, 200, 240, 240],
                            "yolo_confidence": 0.5,
                            "fused_confidence": 0.5,
                            "suggested_severity_hint": "none",
                            "opencv_roi_metrics": {
                                "roi_v_mean": 200.0,
                                "roi_v_p95": 240.0,
                                "bg_ring_v_median": 30.0,
                                "luminance_contrast": 180.0,
                                "green_foliage_ratio": 0.0,
                                "edge_density": 0.02,
                                "fusion_status": "baseline_heuristic",
                            },
                            "diagnostic_summary": "正常亮灯参照",
                            "requires_inspector_confirmation": True,
                            "replaces_electrical_safety_check": False,
                        }
                    ],
                    "requires_inspector_confirmation": True,
                    "replaces_electrical_safety_check": False,
                    "disclaimer": "AI 候选必须经人工确认",
                },
                ai_is_baseline_mode=True,
                uploaded_by_account_id=insp.id,
            )
        )
        await s.commit()
        legacy_id = legacy.id

    # 决策键集为空：无论提交什么都命中 `not candidates` -> 422
    r = await _confirm(
        api_client,
        {**insp_hdr, "Idempotency-Key": new_idem_key("conf-legacy")},
        legacy_id,
        {
            "fault_type": "OCCLUDED",
            "safety_flag": False,
            "candidate_decisions": [
                _decision(1, "cand-001", "confirmed"),
            ],
        },
    )
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "VALIDATION_ERROR"
    assert err["details"]["field"] == "candidate_decisions"
    await _assert_draft_untouched(async_engine, legacy_id)
