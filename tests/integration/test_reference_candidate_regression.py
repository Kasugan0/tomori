"""Reference candidate decisions are optional, traceable, and may be modified into faults."""

from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.integration.conftest import (
    auth_headers_for,
    make_night_normal_lit_png,
    make_occluded_fault_png,
    new_idem_key,
)
from tests.integration.test_candidate_persistence_regression import (
    _audit_rows,
    _confirm,
    _decision,
    _new_ai_draft,
    _point_id,
)


@pytest.mark.parametrize("reference_action", [None, "confirmed", "rejected"])
async def test_optional_reference_actions(
    api_client: AsyncClient, async_engine: AsyncEngine, reference_action: str | None
) -> None:
    admin = await auth_headers_for(api_client, "admin")
    inspector = await auth_headers_for(api_client, "inspector1")
    point = await _point_id(api_client, admin, "SL-ZZ-JS-0001")
    body = await _new_ai_draft(
        api_client,
        {**inspector, "Idempotency-Key": new_idem_key("ref-ai")},
        point_id=point,
        images=[make_occluded_fault_png(), make_night_normal_lit_png()],
    )
    candidates = [
        (ev["id"], c) for ev in body["evidences"] for c in ev["ai_result_json"]["candidates"]
    ]
    fault_id, fault = next((e, c) for e, c in candidates if c["is_fault_candidate"])
    ref_id, ref = next((e, c) for e, c in candidates if not c["is_fault_candidate"])
    decisions = [_decision(fault_id, fault["candidate_id"], "confirmed")]
    if reference_action is not None:
        decisions.append(_decision(ref_id, ref["candidate_id"], reference_action))
    result = await _confirm(
        api_client,
        {**inspector, "Idempotency-Key": new_idem_key("ref-conf")},
        body["ticket"]["id"],
        {"fault_type": fault["candidate_fault_type"], "candidate_decisions": decisions},
    )
    assert result.status_code == 200, result.text
    audits = await _audit_rows(async_engine, body["ticket"]["id"])
    snapshot = next(a.business_snapshot_json for a in audits if a.action == "TICKET_CONFIRMED")
    assert snapshot is not None
    saved = snapshot["candidate_decisions"]
    assert {(d["evidence_id"], d["candidate_id"], d["decision"]) for d in saved} == {
        (d["evidence_id"], d["candidate_id"], d["decision"]) for d in decisions
    }


async def test_reference_modification_can_replace_rejected_fault(
    api_client: AsyncClient, async_engine: AsyncEngine
) -> None:
    admin = await auth_headers_for(api_client, "admin")
    inspector = await auth_headers_for(api_client, "inspector1")
    point = await _point_id(api_client, admin, "SL-ZZ-JS-0001")
    body = await _new_ai_draft(
        api_client,
        {**inspector, "Idempotency-Key": new_idem_key("ref-ai")},
        point_id=point,
        images=[make_occluded_fault_png(), make_night_normal_lit_png()],
    )
    candidates = [
        (ev["id"], c) for ev in body["evidences"] for c in ev["ai_result_json"]["candidates"]
    ]
    fault_id, fault = next((e, c) for e, c in candidates if c["is_fault_candidate"])
    ref_id, ref = next((e, c) for e, c in candidates if not c["is_fault_candidate"])
    reject = _decision(fault_id, fault["candidate_id"], "rejected")
    reference = _decision(ref_id, ref["candidate_id"], "confirmed")

    async def attempt(decisions: list[dict[str, Any]]) -> Response:
        return await _confirm(
            api_client,
            {**inspector, "Idempotency-Key": new_idem_key("ref-conf")},
            body["ticket"]["id"],
            {"fault_type": "LAMP_OUT", "candidate_decisions": decisions},
        )

    # Reference confirmation is trace-only and cannot supply a final type.
    assert (await attempt([reject, reference])).status_code == 422
    assert (await attempt([reject, reference, reference])).status_code == 422
    assert (
        await attempt([reject, _decision(ref_id, "unknown", "modified", fault_type="LAMP_OUT")])
    ).status_code == 422
    modified = _decision(ref_id, ref["candidate_id"], "modified", fault_type="LAMP_OUT")
    result = await attempt([reject, modified])
    assert result.status_code == 200, result.text
    assert result.json()["fault_type"] == "LAMP_OUT"
    audits = await _audit_rows(async_engine, body["ticket"]["id"])
    snapshot = next(a.business_snapshot_json for a in audits if a.action == "TICKET_CONFIRMED")
    assert snapshot is not None
    assert any(
        d["evidence_id"] == ref_id
        and d["candidate_id"] == ref["candidate_id"]
        and d["decision"] == "modified"
        and d["fault_type"] == "LAMP_OUT"
        for d in snapshot["candidate_decisions"]
    )
