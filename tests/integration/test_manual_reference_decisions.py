"""Manual reports preserve optional decisions on orphan reference evidence."""

import json

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from tests.integration.conftest import auth_headers_for, make_night_normal_lit_png, new_idem_key
from tests.integration.test_candidate_persistence_regression import _audit_rows, _point_id
from tomori.models.fault_evidence import FaultEvidence
from tomori.models.fault_ticket import FaultTicket


async def _orphan(client: AsyncClient, headers: dict[str, str], point: int) -> tuple[int, str]:
    response = await client.post(
        "/api/v1/tickets/ai-candidates",
        data={"lighting_point_id": str(point), "scene_lighting_hint": "night"},
        files={"files": ("normal.png", make_night_normal_lit_png(), "image/png")},
        headers={**headers, "Idempotency-Key": new_idem_key("ref-orphan")},
    )
    assert response.status_code == 200, response.text
    assert response.json()["ticket"] is None
    evidence = response.json()["evidences"][0]
    candidate = next(
        c for c in evidence["ai_result_json"]["candidates"] if not c["is_fault_candidate"]
    )
    return evidence["id"], candidate["candidate_id"]


async def test_manual_decision_validation_audit_idempotency_and_rollback(
    api_client: AsyncClient, async_engine: AsyncEngine
) -> None:
    admin = await auth_headers_for(api_client, "admin")
    inspector = await auth_headers_for(api_client, "inspector1")
    point = await _point_id(api_client, admin, "SL-ZZ-JS-0001")
    evidence, candidate = await _orphan(api_client, inspector, point)
    other_evidence, other_candidate = await _orphan(api_client, admin, point)
    decision = {"evidence_id": evidence, "candidate_id": candidate, "decision": "rejected"}
    payload = {
        "lighting_point_id": point,
        "fault_type": "OTHER",
        "title": "reference manual",
        "evidence_ids": [evidence],
        "candidate_decisions": [decision],
    }
    key = new_idem_key("ref-manual")
    for invalid in (
        [decision, decision],
        [{**decision, "candidate_id": "unknown"}],
        [{"evidence_id": other_evidence, "candidate_id": other_candidate, "decision": "rejected"}],
    ):
        response = await api_client.post(
            "/api/v1/tickets",
            json={**payload, "candidate_decisions": invalid},
            headers={**inspector, "Idempotency-Key": key},
        )
        assert response.status_code == 422, response.text
        async with AsyncSession(async_engine) as session:
            row = await session.scalar(select(FaultEvidence).where(FaultEvidence.id == evidence))
            assert row is not None and row.ticket_id is None
    foreign = await api_client.post(
        "/api/v1/tickets",
        json={
            **payload,
            "evidence_ids": [other_evidence],
            "candidate_decisions": [
                {
                    "evidence_id": other_evidence,
                    "candidate_id": other_candidate,
                    "decision": "rejected",
                }
            ],
        },
        headers={**inspector, "Idempotency-Key": key},
    )
    assert foreign.status_code == 422, foreign.text
    created = await api_client.post(
        "/api/v1/tickets", json=payload, headers={**inspector, "Idempotency-Key": key}
    )
    assert created.status_code == 201, created.text
    assert created.json()["fault_type"] == "OTHER"
    replay = await api_client.post(
        "/api/v1/tickets", json=payload, headers={**inspector, "Idempotency-Key": key}
    )
    assert replay.status_code == 201 and replay.json() == created.json()
    assert replay.headers.get("x-idempotent-replay") == "true"
    mismatch = await api_client.post(
        "/api/v1/tickets",
        json={**payload, "candidate_decisions": [{**decision, "decision": "confirmed"}]},
        headers={**inspector, "Idempotency-Key": key},
    )
    assert (
        mismatch.status_code == 422
        and mismatch.json()["error"]["code"] == "IDEMPOTENCY_PAYLOAD_MISMATCH"
    )
    audits = await _audit_rows(async_engine, created.json()["id"])
    snapshot = next(a.business_snapshot_json for a in audits if a.action == "TICKET_CREATED")
    assert snapshot is not None
    assert snapshot["candidate_decisions"][0]["decision"] == "rejected"
    assert snapshot["candidate_decisions"][0]["evidence_id"] == evidence
    assert snapshot["candidate_decisions"][0]["candidate_id"] == candidate


@pytest.mark.parametrize("id_format", ["array", "comma"])
async def test_manual_multipart_decisions_and_omitted_compatibility(
    api_client: AsyncClient, async_engine: AsyncEngine, id_format: str
) -> None:
    admin = await auth_headers_for(api_client, "admin")
    inspector = await auth_headers_for(api_client, "inspector1")
    point = await _point_id(api_client, admin, "SL-ZZ-JS-0002")
    evidence, candidate = await _orphan(api_client, inspector, point)
    decision = {
        "evidence_id": evidence,
        "candidate_id": candidate,
        "decision": "modified",
        "fault_type": "LAMP_OUT",
    }
    response = await api_client.post(
        "/api/v1/tickets",
        files={
            "lighting_point_id": (None, str(point)),
            "fault_type": (None, "OTHER"),
            "title": (None, "multipart reference"),
            "evidence_ids": (
                None,
                json.dumps([evidence]) if id_format == "array" else str(evidence),
            ),
            "candidate_decisions": (None, json.dumps([decision])),
        },
        headers={**inspector, "Idempotency-Key": new_idem_key("ref-multi")},
    )
    assert response.status_code == 201, response.text
    assert response.json()["fault_type"] == "OTHER"
    audit = next(
        a
        for a in await _audit_rows(async_engine, response.json()["id"])
        if a.action == "TICKET_CREATED"
    )
    assert audit.business_snapshot_json is not None
    assert audit.business_snapshot_json["candidate_decisions"][0]["fault_type"] == "LAMP_OUT"
    another_point = await _point_id(api_client, admin, "SL-ZZ-JS-0001")
    plain = await api_client.post(
        "/api/v1/tickets",
        json={
            "lighting_point_id": another_point,
            "fault_type": "LAMP_OUT",
            "title": "without decisions",
        },
        headers={**inspector, "Idempotency-Key": new_idem_key("ref-plain")},
    )
    assert plain.status_code == 201, plain.text


async def test_invalid_decisions_without_evidence_and_malformed_multipart(
    api_client: AsyncClient, async_engine: AsyncEngine
) -> None:
    admin = await auth_headers_for(api_client, "admin")
    inspector = await auth_headers_for(api_client, "inspector1")
    point = await _point_id(api_client, admin, "SL-ZZ-JS-0001")
    key = new_idem_key("manual-invalid")
    decision = {"evidence_id": 999999, "candidate_id": "unknown", "decision": "rejected"}
    payload = {"lighting_point_id": point, "fault_type": "OTHER", "title": "invalid decisions"}
    invalid = await api_client.post(
        "/api/v1/tickets",
        json={**payload, "candidate_decisions": [decision]},
        headers={**inspector, "Idempotency-Key": key},
    )
    assert invalid.status_code == 422, invalid.text
    malformed = await api_client.post(
        "/api/v1/tickets",
        files={
            "lighting_point_id": (None, str(point)),
            "fault_type": (None, "OTHER"),
            "title": (None, "invalid multipart"),
            "candidate_decisions": (None, "[bad"),
        },
        headers={**inspector, "Idempotency-Key": key},
    )
    assert malformed.status_code == 422, malformed.text
    for invalid_ids in (
        "[bad",
        "abc",
        '[1,"x"]',
        "[1e309]",
        "[1.5]",
        "[true]",
        "[0]",
        "[-1]",
        "1,abc",
    ):
        malformed_ids = await api_client.post(
            "/api/v1/tickets",
            files={
                "lighting_point_id": (None, str(point)),
                "fault_type": (None, "OTHER"),
                "title": (None, "invalid evidence IDs"),
                "evidence_ids": (None, invalid_ids),
            },
            headers={**inspector, "Idempotency-Key": key},
        )
        assert malformed_ids.status_code == 422, malformed_ids.text
    for invalid_value in (True, 0, -1, 1.0, "1", None):
        invalid_json = await api_client.post(
            "/api/v1/tickets",
            json={**payload, "evidence_ids": [invalid_value]},
            headers={**inspector, "Idempotency-Key": key},
        )
        assert invalid_json.status_code == 422, invalid_json.text
        invalid_decision = await api_client.post(
            "/api/v1/tickets",
            json={**payload, "candidate_decisions": [{**decision, "evidence_id": invalid_value}]},
            headers={**inspector, "Idempotency-Key": key},
        )
        assert invalid_decision.status_code == 422, invalid_decision.text
    async with AsyncSession(async_engine) as session:
        assert await session.scalar(select(func.count(FaultTicket.id))) == 0
    valid = await api_client.post(
        "/api/v1/tickets", json=payload, headers={**inspector, "Idempotency-Key": key}
    )
    assert valid.status_code == 201, valid.text
