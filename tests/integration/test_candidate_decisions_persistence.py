"""task-25 回归：POST /tickets/{id}/confirm 逐条候选决定的持久化与约束守卫。

覆盖混合处置（剔除 A / modified B → 最终类型取自保留候选且非 A）、审计快照
`TICKET_CONFIRMED.business_snapshot_json.candidate_decisions` 落库，以及重复 /
遗漏 / 跨票证据 / 全剔除 / 草稿无故障候选等负向 422 路径与失败不脏写。
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from tests.integration.conftest import (
    auth_headers_for,
    make_occluded_fault_png,
    new_idem_key,
)
from tomori.models.audit_log import AuditLog
from tomori.models.fault_ticket import FaultTicket


def _err(resp: Any) -> dict[str, Any]:
    err: dict[str, Any] = resp.json()["error"]
    return err


def _occluded_variant_png() -> bytes:
    """`occluded_fault` 变体：位置不同的绿植块 + 噪点，保持 OCCLUDED 语义且字节不同。"""
    import io

    from PIL import Image, ImageDraw

    img = Image.new("RGB", (400, 400), (12, 12, 18))
    draw = ImageDraw.Draw(img)
    for x in range(0, 400, 8):
        draw.line((x, 0, x, 399), fill=(40, 40, 48), width=1)
    draw.rectangle((60, 60, 110, 110), fill=(0, 150, 10))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


async def _point_id(client: AsyncClient, headers: dict[str, str], code: str) -> int:
    pts = (await client.get("/api/v1/lighting-points", headers=headers)).json()["items"]
    return int(next(p for p in pts if p["point_code"] == code)["id"])


async def _two_evidence_draft(
    client: AsyncClient,
    headers: dict[str, str],
    point_id: int,
) -> dict[str, Any]:
    """一次分析两张故障图 -> 1 个草稿 + 2 张证据（同名 candidate_id 跨图）。"""
    r = await client.post(
        "/api/v1/tickets/ai-candidates",
        data={"lighting_point_id": str(point_id), "scene_lighting_hint": "night"},
        files=[
            ("files", ("occ1.png", make_occluded_fault_png(), "image/png")),
            ("files", ("occ2.png", _occluded_variant_png(), "image/png")),
        ],
        headers={**headers, "Idempotency-Key": new_idem_key("cdp-draft")},
    )
    assert r.status_code == 201, r.text
    body: dict[str, Any] = r.json()
    assert body["ticket"]["status"] == "AI_CANDIDATE_PENDING"
    assert len(body["evidences"]) == 2
    return body


def _fault_cands(ev: dict[str, Any]) -> list[dict[str, Any]]:
    return [c for c in ev["ai_result_json"]["candidates"] if c["is_fault_candidate"]]


async def test_mixed_decisions_final_type_from_retained_and_audit_persisted(
    api_client: AsyncClient,
    async_engine: AsyncEngine,
) -> None:
    """剔除主候选 A + modified B -> 最终类型非 A，逐条决定写入审计快照。"""
    insp_hdr = await auth_headers_for(api_client, "inspector1")
    admin_hdr = await auth_headers_for(api_client, "admin")
    pid = await _point_id(api_client, admin_hdr, "SL-ZZ-JS-0001")
    body = await _two_evidence_draft(api_client, insp_hdr, pid)
    draft = body["ticket"]
    ev_a, ev_b = body["evidences"]

    cand_a = _fault_cands(ev_a)[0]
    cand_b = _fault_cands(ev_b)[0]
    type_a = cand_a["candidate_fault_type"]
    assert type_a == "OCCLUDED"

    r_conf = await api_client.post(
        f"/api/v1/tickets/{draft['id']}/confirm",
        json={
            "fault_type": "LAMP_OUT",
            "safety_flag": False,
            "candidate_decisions": [
                {
                    "evidence_id": ev_a["id"],
                    "candidate_id": cand_a["candidate_id"],
                    "decision": "rejected",
                },
                {
                    "evidence_id": ev_b["id"],
                    "candidate_id": cand_b["candidate_id"],
                    "decision": "modified",
                    "fault_type": "LAMP_OUT",
                },
            ],
        },
        headers={**insp_hdr, "Idempotency-Key": new_idem_key("cdp-mix")},
    )
    assert r_conf.status_code == 200, r_conf.text
    confirmed = r_conf.json()
    assert confirmed["status"] == "PENDING_DISPATCH"
    assert confirmed["fault_type"] == "LAMP_OUT"
    assert confirmed["fault_type"] != type_a

    async with AsyncSession(async_engine, expire_on_commit=False) as s:
        audit = await s.scalar(
            select(AuditLog).where(
                AuditLog.ticket_id == draft["id"],
                AuditLog.action == "TICKET_CONFIRMED",
            )
        )
        assert audit is not None
        snapshot = audit.business_snapshot_json or {}
        decisions = snapshot.get("candidate_decisions")
        assert isinstance(decisions, list) and len(decisions) == 2
        keyed = {(d["evidence_id"], d["candidate_id"]): d for d in decisions}
        assert keyed[(ev_a["id"], cand_a["candidate_id"])]["decision"] == "rejected"
        modified = keyed[(ev_b["id"], cand_b["candidate_id"])]
        assert modified["decision"] == "modified" and modified["fault_type"] == "LAMP_OUT"


async def test_decision_validation_guards_and_no_dirty_write(
    api_client: AsyncClient,
    async_engine: AsyncEngine,
) -> None:
    """重复 / 遗漏 / 跨票 / 全剔除 / 无候选草稿 / modified 缺类型 -> 422 且草稿不变。"""
    insp_hdr = await auth_headers_for(api_client, "inspector1")
    admin_hdr = await auth_headers_for(api_client, "admin")
    pid = await _point_id(api_client, admin_hdr, "SL-ZZ-JS-0001")
    pid2 = await _point_id(api_client, admin_hdr, "SL-ZZ-JS-0002")
    body = await _two_evidence_draft(api_client, insp_hdr, pid)
    draft = body["ticket"]
    ev_a, ev_b = body["evidences"]
    cand_a = _fault_cands(ev_a)[0]
    cand_b = _fault_cands(ev_b)[0]
    keep_dec = {
        "evidence_id": ev_b["id"],
        "candidate_id": cand_b["candidate_id"],
        "decision": "confirmed",
    }
    top_type = cand_b["candidate_fault_type"]

    async def _attempt(decisions: list[dict[str, Any]], fault_type: str = top_type) -> Any:
        return await api_client.post(
            f"/api/v1/tickets/{draft['id']}/confirm",
            json={
                "fault_type": fault_type,
                "safety_flag": False,
                "candidate_decisions": decisions,
            },
            headers={**insp_hdr, "Idempotency-Key": new_idem_key("cdp-neg")},
        )

    base_dec = {
        "evidence_id": ev_a["id"],
        "candidate_id": cand_a["candidate_id"],
        "decision": "confirmed",
    }
    # 重复处置同一候选 -> 422
    r_dup = await _attempt([base_dec, base_dec.copy(), keep_dec])
    assert r_dup.status_code == 422 and _err(r_dup)["code"] == "VALIDATION_ERROR"

    # 遗漏一个故障候选 -> 422
    r_missing = await _attempt([keep_dec])
    assert r_missing.status_code == 422 and _err(r_missing)["code"] == "VALIDATION_ERROR"

    # 跨票证据：另一草稿的候选 -> 422
    body2 = await _two_evidence_draft(api_client, insp_hdr, pid2)
    ev_foreign = body2["evidences"][0]
    cand_foreign = _fault_cands(ev_foreign)[0]
    r_cross = await _attempt(
        [
            base_dec,
            {
                "evidence_id": ev_foreign["id"],
                "candidate_id": cand_foreign["candidate_id"],
                "decision": "confirmed",
            },
        ]
    )
    assert r_cross.status_code == 422 and _err(r_cross)["code"] == "VALIDATION_ERROR"

    # 全剔除 -> 422（retained_types 为空）
    r_allrej = await _attempt(
        [
            {
                "evidence_id": ev_a["id"],
                "candidate_id": cand_a["candidate_id"],
                "decision": "rejected",
            },
            {
                "evidence_id": ev_b["id"],
                "candidate_id": cand_b["candidate_id"],
                "decision": "rejected",
            },
        ]
    )
    assert r_allrej.status_code == 422 and _err(r_allrej)["code"] == "VALIDATION_ERROR"

    # top-level fault_type 不在保留候选类型中 -> 422
    r_type = await _attempt([base_dec, keep_dec], fault_type="OTHER")
    assert r_type.status_code == 422 and _err(r_type)["code"] == "VALIDATION_ERROR"

    # modified 缺 fault_type -> schema 422
    r_mod = await _attempt(
        [
            base_dec,
            {
                "evidence_id": ev_b["id"],
                "candidate_id": cand_b["candidate_id"],
                "decision": "modified",
            },
        ]
    )
    assert r_mod.status_code == 422 and _err(r_mod)["code"] == "VALIDATION_ERROR"

    # confirmed 携带 fault_type/manual_note -> schema 422
    r_extra = await _attempt(
        [
            base_dec,
            {
                "evidence_id": ev_b["id"],
                "candidate_id": cand_b["candidate_id"],
                "decision": "confirmed",
                "fault_type": "OTHER",
            },
        ]
    )
    assert r_extra.status_code == 422 and _err(r_extra)["code"] == "VALIDATION_ERROR"

    # 全部失败后草稿必须仍为 AI_CANDIDATE_PENDING（无脏写）
    async with AsyncSession(async_engine, expire_on_commit=False) as s:
        row = await s.scalar(select(FaultTicket).where(FaultTicket.id == draft["id"]))
        assert row is not None and row.status == "AI_CANDIDATE_PENDING"
        assert row.confirmed_by_inspector is False

    # Simulate a historical draft whose analysis no longer has fault candidates.
    async with AsyncSession(async_engine, expire_on_commit=False) as s:
        await s.execute(
            text(
                "UPDATE fault_evidences SET ai_result_json = jsonb_set("
                "ai_result_json, '{candidates}', '[]'::jsonb) WHERE ticket_id = :tid"
            ),
            {"tid": body2["ticket"]["id"]},
        )
        await s.commit()
    r_nocand = await api_client.post(
        f"/api/v1/tickets/{body2['ticket']['id']}/confirm",
        json={
            "fault_type": "LAMP_OUT",
            "candidate_decisions": [
                {"evidence_id": 1, "candidate_id": "x", "decision": "confirmed"}
            ],
        },
        headers={**insp_hdr, "Idempotency-Key": new_idem_key("cdp-nocand")},
    )
    assert r_nocand.status_code == 422 and _err(r_nocand)["code"] == "VALIDATION_ERROR"


async def test_confirm_idempotency_replay_and_mismatch(
    api_client: AsyncClient,
    async_engine: AsyncEngine,
) -> None:
    """同 key 同载荷重放返回同 body 且审计仅 1 条；异载荷 -> 422 MISMATCH。"""
    insp_hdr = await auth_headers_for(api_client, "inspector1")
    admin_hdr = await auth_headers_for(api_client, "admin")
    pid = await _point_id(api_client, admin_hdr, "SL-ZZ-JS-0001")
    body = await _two_evidence_draft(api_client, insp_hdr, pid)
    draft = body["ticket"]
    evidences = body["evidences"]

    decisions = [
        {
            "evidence_id": ev["id"],
            "candidate_id": _fault_cands(ev)[0]["candidate_id"],
            "decision": "confirmed",
        }
        for ev in evidences
    ]
    payload = {
        "fault_type": "OCCLUDED",
        "safety_flag": False,
        "candidate_decisions": decisions,
    }
    key = new_idem_key("cdp-idem")
    r1 = await api_client.post(
        f"/api/v1/tickets/{draft['id']}/confirm",
        json=payload,
        headers={**insp_hdr, "Idempotency-Key": key},
    )
    assert r1.status_code == 200, r1.text

    r_rep = await api_client.post(
        f"/api/v1/tickets/{draft['id']}/confirm",
        json=payload,
        headers={**insp_hdr, "Idempotency-Key": key},
    )
    assert r_rep.status_code == 200
    assert r_rep.headers.get("x-idempotent-replay") == "true"
    assert r_rep.json()["id"] == r1.json()["id"]

    r_mis = await api_client.post(
        f"/api/v1/tickets/{draft['id']}/confirm",
        json={**payload, "safety_flag": True},
        headers={**insp_hdr, "Idempotency-Key": key},
    )
    assert r_mis.status_code == 422
    assert _err(r_mis)["code"] == "IDEMPOTENCY_PAYLOAD_MISMATCH"

    # 决定本身变化同样触发 mismatch（非仅顶层字段）
    r_mis_dec = await api_client.post(
        f"/api/v1/tickets/{draft['id']}/confirm",
        json={
            **payload,
            "candidate_decisions": [{**decisions[0], "decision": "rejected"}, *decisions[1:]],
        },
        headers={**insp_hdr, "Idempotency-Key": key},
    )
    assert r_mis_dec.status_code == 422
    assert _err(r_mis_dec)["code"] == "IDEMPOTENCY_PAYLOAD_MISMATCH"

    async with AsyncSession(async_engine, expire_on_commit=False) as s:
        audit_cnt = len(
            (
                await s.scalars(
                    select(AuditLog).where(
                        AuditLog.ticket_id == draft["id"],
                        AuditLog.action == "TICKET_CONFIRMED",
                    )
                )
            ).all()
        )
        assert audit_cnt == 1


async def test_sensitive_modified_type_requires_manual_note(
    api_client: AsyncClient,
) -> None:
    """modified 为 FLICKERING/ELECTRIC_HAZARD 时该条缺 manual_note -> 422。"""
    insp_hdr = await auth_headers_for(api_client, "inspector1")
    admin_hdr = await auth_headers_for(api_client, "admin")
    pid = await _point_id(api_client, admin_hdr, "SL-ZZ-JS-0001")
    body = await _two_evidence_draft(api_client, insp_hdr, pid)
    draft = body["ticket"]
    ev_a, ev_b = body["evidences"]
    cand_a = _fault_cands(ev_a)[0]
    cand_b = _fault_cands(ev_b)[0]

    r_sensitive = await api_client.post(
        f"/api/v1/tickets/{draft['id']}/confirm",
        json={
            "fault_type": "FLICKERING",
            "safety_flag": False,
            "candidate_decisions": [
                {
                    "evidence_id": ev_a["id"],
                    "candidate_id": cand_a["candidate_id"],
                    "decision": "rejected",
                },
                {
                    "evidence_id": ev_b["id"],
                    "candidate_id": cand_b["candidate_id"],
                    "decision": "modified",
                    "fault_type": "FLICKERING",
                },
            ],
        },
        headers={**insp_hdr, "Idempotency-Key": new_idem_key("cdp-sens")},
    )
    assert r_sensitive.status_code == 422
    assert _err(r_sensitive)["code"] == "VALIDATION_ERROR"

    # 补齐该条 manual_note 后可确认（report_source 转 MANUAL_INSPECT 由产品语义允许）
    r_ok = await api_client.post(
        f"/api/v1/tickets/{draft['id']}/confirm",
        json={
            "fault_type": "FLICKERING",
            "safety_flag": False,
            "manual_note": "现场频闪，疑似驱动故障",
            "candidate_decisions": [
                {
                    "evidence_id": ev_a["id"],
                    "candidate_id": cand_a["candidate_id"],
                    "decision": "rejected",
                },
                {
                    "evidence_id": ev_b["id"],
                    "candidate_id": cand_b["candidate_id"],
                    "decision": "modified",
                    "fault_type": "FLICKERING",
                    "manual_note": "现场确认持续频闪",
                },
            ],
        },
        headers={**insp_hdr, "Idempotency-Key": new_idem_key("cdp-sens-ok")},
    )
    assert r_ok.status_code == 200, r_ok.text
    assert r_ok.json()["fault_type"] == "FLICKERING"
