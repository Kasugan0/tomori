"""L2 error envelope (6 fields), exact 34 error codes, banned codes, and leak prevention."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import pytest
from fastapi import APIRouter
from httpx import ASGITransport, AsyncClient

from tests.integration.conftest import (
    auth_headers_for,
    clear_settings_cache,
    make_animated_bytes,
    make_minimal_png_with_dimensions,
    make_static_image,
    new_idem_key,
)
from tomori.api import errors as api_errors
from tomori.api.errors import build_error_response
from tomori.services import exceptions as svc_exc
from tomori.vision import errors as vision_errors

AUTHORITATIVE_34_ERROR_CODES: dict[str, int] = {
    # §3.1 Auth & Permission (9)
    "INVALID_CREDENTIALS": 401,
    "TOKEN_EXPIRED": 401,
    "TOKEN_INVALID": 401,
    "TOKEN_MISSING": 401,
    "FORBIDDEN_ROLE": 403,
    "FORBIDDEN_REGION_SCOPE": 403,
    "ACCOUNT_DISABLED": 403,
    "RESOURCE_NOT_FOUND": 404,
    "EVIDENCE_NOT_ANNOTATED": 404,
    # §3.2 State & Business Rules (12)
    "DUPLICATE_ACTIVE_TICKET": 409,
    "IDEMPOTENCY_REQUEST_IN_PROGRESS": 409,
    "INVALID_STATE_TRANSITION": 422,
    "AI_CANDIDATE_NOT_CONFIRMED": 422,
    "ELECTRICAL_SAFETY_CHECK_REQUIRED": 422,
    "DISPATCH_TECHNICIAN_NOT_ELIGIBLE": 422,
    "TECHNICIAN_OVERLOADED": 422,
    "POINT_HAS_ACTIVE_TICKET": 422,
    "RULE_VALUE_IMMUTABLE": 422,
    "INVALID_RULE_PAYLOAD": 422,
    "IDEMPOTENCY_PAYLOAD_MISMATCH": 422,
    "VALIDATION_ERROR": 422,
    # §3.3 Media & Capacity (6)
    "IDEMPOTENCY_KEY_REQUIRED": 400,
    "IDEMPOTENCY_KEY_INVALID": 400,
    "IMAGE_SIZE_EXCEEDED": 413,
    "UNSUPPORTED_IMAGE_FORMAT": 415,
    "MEDIA_VIDEO_STREAM_FORBIDDEN": 415,
    "IMAGE_PIXEL_LIMIT_EXCEEDED": 422,
    # §3.4 Server & Vision Degradation (7)
    "TOO_MANY_FILES": 422,
    "NO_FILES": 422,
    "INTERNAL_ERROR": 500,
    "SERVICE_UNAVAILABLE": 503,
    "VISION_DISABLED": 503,
    "VISION_TIMEOUT": 503,
    "VISION_INFERENCE_FAILED": 503,
}

FORBIDDEN_LEGACY_ERROR_CODES: frozenset[str] = frozenset(
    {
        "IMAGE_TOO_LARGE",
        "VISION_WEIGHTS_NOT_READY",
        "DISPATCH_TECHNICIAN_OUT_OF_SCOPE",
        "DISPATCH_OUT_OF_SCOPE",
        "INVALID_DISPATCH_TRANSITION",
    }
)

ENVELOPE_6_FIELDS: frozenset[str] = frozenset(
    {"code", "message", "message_key", "details", "request_id", "timestamp"}
)


def _assert_six_field_envelope(
    body: dict[str, Any],
    *,
    expected_code: str,
    header_request_id: str | None = None,
) -> dict[str, Any]:
    assert set(body.keys()) == {"error"}
    err = body["error"]
    assert set(err.keys()) == ENVELOPE_6_FIELDS
    assert err["code"] == expected_code
    assert isinstance(err["message"], str) and err["message"]
    assert isinstance(err["message_key"], str) and err["message_key"]
    assert isinstance(err["request_id"], str) and err["request_id"]
    if header_request_id is not None:
        assert err["request_id"] == header_request_id
    parsed_ts = datetime.fromisoformat(err["timestamp"])
    assert parsed_ts.tzinfo is not None
    return dict(err)


def test_exact_34_error_codes_and_zero_banned_legacy_codes() -> None:
    """Verify all 34 authoritative codes, status mappings, message keys, and zero banned codes."""
    assert len(AUTHORITATIVE_34_ERROR_CODES) == 34
    assert svc_exc.HTTP_STATUS_BY_CODE == AUTHORITATIVE_34_ERROR_CODES
    assert api_errors.HTTP_STATUS_BY_CODE == AUTHORITATIVE_34_ERROR_CODES
    assert set(svc_exc.MESSAGE_KEY_BY_CODE) == set(AUTHORITATIVE_34_ERROR_CODES)
    assert all(bool(v.strip()) for v in svc_exc.MESSAGE_KEY_BY_CODE.values())

    for code, expected_status in vision_errors.HTTP_STATUS_BY_CODE.items():
        assert AUTHORITATIVE_34_ERROR_CODES[code] == expected_status

    all_codes = (
        set(svc_exc.HTTP_STATUS_BY_CODE)
        | set(api_errors.HTTP_STATUS_BY_CODE)
        | set(vision_errors.HTTP_STATUS_BY_CODE)
    )
    assert all_codes.isdisjoint(FORBIDDEN_LEGACY_ERROR_CODES)

    # Every one of the 34 codes builds a valid 6-field envelope
    for code, expected_status in AUTHORITATIVE_34_ERROR_CODES.items():
        resp = build_error_response(
            code=code,
            message=f"msg for {code}",
            request_id="req-test-34",
            details={"probe": True},
        )
        assert resp.status_code == expected_status
        parsed = json.loads(bytes(resp.body).decode("utf-8"))
        _assert_six_field_envelope(parsed, expected_code=code, header_request_id="req-test-34")


def test_app_mounts_exact_60_business_endpoints_plus_health_and_no_put_rules() -> None:
    """Verify 60 business (method, path) routes + GET /health and zero PUT /business-rules."""
    from tomori.main import create_app

    app = create_app()
    paths = app.openapi()["paths"]
    endpoints: set[tuple[str, str]] = {
        (method.upper(), path)
        for path, ops in paths.items()
        for method in ops
        if path.startswith("/api/v1")
        and method.lower() in {"get", "post", "put", "patch", "delete"}
    }

    assert ("GET", "/api/v1/health") in endpoints
    business_endpoints = endpoints - {("GET", "/api/v1/health")}
    assert len(business_endpoints) == 60
    assert not any(m == "PUT" and "business-rules" in p for m, p in endpoints)


def test_sanitize_details_strips_sensitive_keys() -> None:
    """build_error_response strips traceback/stack/sql/file_path/secret/password keys."""
    resp = build_error_response(
        code="INTERNAL_ERROR",
        message="err",
        request_id="req-sanitize",
        details={
            "traceback": "Traceback (most recent call last)...",
            "stack": "frame",
            "sql": "SELECT * FROM accounts",
            "query": "SELECT 1",
            "file_path": "/home/yiming/secret.txt",
            "storage_path": "/var/lib/uploads/a.jpg",
            "secret": "top-secret",
            "jwt_secret_key": "jwt-key",
            "password": "plain-pw",
            "password_hash": "$argon2id$...",
            "safe_key": "ok",
        },
    )
    parsed = json.loads(bytes(resp.body).decode("utf-8"))
    assert parsed["error"]["details"] == {"safe_key": "ok"}


async def test_error_response_must_not_duplicate_x_request_id_header(
    api_client: AsyncClient,
) -> None:
    """Error responses must emit exactly one X-Request-Id header matching error.request_id."""
    r401 = await api_client.get("/api/v1/auth/me")
    assert r401.status_code == 401
    rid = r401.json()["error"]["request_id"]
    header_vals = r401.headers.get_list("x-request-id")
    assert header_vals == [rid]


async def test_live_http_error_envelopes_across_status_classes_and_no_500_leakage(
    api_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Validate 6-field error envelopes across 400..503 HTTP calls and zero 500 leakage."""
    admin_hdr = await auth_headers_for(api_client, "admin")
    insp_hdr = await auth_headers_for(api_client, "inspector1")
    pts = (await api_client.get("/api/v1/lighting-points", headers=admin_hdr)).json()["items"]
    pt = next(p for p in pts if p["point_code"] == "SL-ZZ-JS-0001")

    rules = (await api_client.get("/api/v1/business-rules", headers=admin_hdr)).json()["items"]
    rule_id = rules[0]["id"]

    # 401 TOKEN_MISSING
    r401 = await api_client.get("/api/v1/auth/me")
    assert r401.status_code == 401
    _assert_six_field_envelope(r401.json(), expected_code="TOKEN_MISSING")

    # 403 FORBIDDEN_ROLE
    r403 = await api_client.get("/api/v1/accounts", headers=insp_hdr)
    assert r403.status_code == 403
    _assert_six_field_envelope(r403.json(), expected_code="FORBIDDEN_ROLE")

    # 404 RESOURCE_NOT_FOUND
    r404 = await api_client.get("/api/v1/tickets/999999", headers=admin_hdr)
    assert r404.status_code == 404
    _assert_six_field_envelope(r404.json(), expected_code="RESOURCE_NOT_FOUND")

    # 422 RULE_VALUE_IMMUTABLE
    r422_rule = await api_client.patch(
        f"/api/v1/business-rules/{rule_id}",
        json={"rule_value": {"ttl_hours": 12}},
        headers=admin_hdr,
    )
    assert r422_rule.status_code == 422
    _assert_six_field_envelope(r422_rule.json(), expected_code="RULE_VALUE_IMMUTABLE")

    # 400 IDEMPOTENCY_KEY_REQUIRED & 422 NO_FILES / 413 / 415 / 422 pixel
    r400 = await api_client.post(
        "/api/v1/tickets",
        json={
            "lighting_point_id": pt["id"],
            "fault_type": "LAMP_OUT",
            "title": "missing key",
            "safety_flag": False,
        },
        headers=insp_hdr,
    )
    assert r400.status_code == 400
    _assert_six_field_envelope(r400.json(), expected_code="IDEMPOTENCY_KEY_REQUIRED")

    r415 = await api_client.post(
        "/api/v1/tickets/ai-candidates",
        data={"lighting_point_id": str(pt["id"])},
        files=[("files", ("anim.png", make_animated_bytes("PNG"), "image/png"))],
        headers={**insp_hdr, "Idempotency-Key": new_idem_key("err-415")},
    )
    assert r415.status_code == 415
    _assert_six_field_envelope(r415.json(), expected_code="UNSUPPORTED_IMAGE_FORMAT")

    r422_px = await api_client.post(
        "/api/v1/tickets/ai-candidates",
        data={"lighting_point_id": str(pt["id"])},
        files=[
            (
                "files",
                ("bomb.png", make_minimal_png_with_dimensions(8001, 5000), "image/png"),
            )
        ],
        headers={**insp_hdr, "Idempotency-Key": new_idem_key("err-px")},
    )
    assert r422_px.status_code == 422
    _assert_six_field_envelope(r422_px.json(), expected_code="IMAGE_PIXEL_LIMIT_EXCEEDED")

    # 503 VISION_DISABLED
    monkeypatch.setenv("YOLO_INFERENCE_ENABLED", "false")
    clear_settings_cache()
    r503 = await api_client.post(
        "/api/v1/tickets/ai-candidates",
        data={"lighting_point_id": str(pt["id"])},
        files=[("files", ("ok.jpg", make_static_image("JPEG"), "image/jpeg"))],
        headers={**insp_hdr, "Idempotency-Key": new_idem_key("err-503")},
    )
    assert r503.status_code == 503
    _assert_six_field_envelope(r503.json(), expected_code="VISION_DISABLED")

    # 500 INTERNAL_ERROR must NEVER leak traceback / SQL / paths / secrets
    from tomori.main import create_app

    probe_app = create_app()
    boom_router = APIRouter()

    @boom_router.get("/__test_boom")
    async def _boom() -> None:
        raise RuntimeError(
            "Traceback SELECT * FROM accounts "
            "/home/yiming/Code/tomori "
            "secret=tomori-stage2-isolated-test-jwt-secret-key-not-for-prod"
        )

    probe_app.include_router(boom_router)
    async with AsyncClient(
        transport=ASGITransport(app=probe_app, raise_app_exceptions=False),
        base_url="http://test",
    ) as err_client:
        r500 = await err_client.get("/__test_boom")
    assert r500.status_code == 500
    err500 = _assert_six_field_envelope(r500.json(), expected_code="INTERNAL_ERROR")
    raw_text = r500.text
    for leak in (
        "Traceback",
        "SELECT ",
        "FROM ",
        "/home/yiming",
        "tomori-stage2-isolated-test-jwt-secret-key-not-for-prod",
    ):
        assert leak not in raw_text
    assert err500["details"] is None
