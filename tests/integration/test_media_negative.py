"""P0-4 media negative path gap tests: real malformed files, exact boundaries, evidence guards.

Exact-code assertions (never just `status_code`):
- `0` bytes -> `422 VALIDATION_ERROR`
- corrupt JPEG bytes -> `415 UNSUPPORTED_IMAGE_FORMAT`
- SVG text as `.png` -> `415 UNSUPPORTED_IMAGE_FORMAT`
- real MP4 magic disguised as `.jpg` + `image/jpeg` MIME -> `415 MEDIA_VIDEO_STREAM_FORBIDDEN`
- exactly `15 MiB` random bytes -> passes size -> fails format -> `415` (NOT `413`)
- `>15 MiB` -> `413 IMAGE_SIZE_EXCEEDED`
- `>40MP` IHDR-crafted PNG -> `422 IMAGE_PIXEL_LIMIT_EXCEEDED`
- duplicate evidence sha256 -> `422 VALIDATION_ERROR` (`reason: DUPLICATE_EVIDENCE`)
- bad `evidence_type` -> `422 VALIDATION_ERROR`
- cross-region inspector on `evidences` -> `403 FORBIDDEN_REGION_SCOPE`
- upload to `CLOSED`/`CANCELLED` ticket -> `422 INVALID_STATE_TRANSITION`
  （终态守卫已由 `task-141` 在 `ticket_service.py` 补 `_TERMINAL_TICKET_STATUSES`；
  本文件的 `test_evidence_upload_on_terminal_ticket_returns_422_invalid_state_transition`
  为该守卫的回归测试，已绿）
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient

from tests.integration.conftest import auth_headers_for, make_static_image, new_idem_key


def _err(resp: Any) -> dict[str, Any]:
    err: dict[str, Any] = resp.json()["error"]
    return err


def _mp4_magic_bytes() -> bytes:
    """Real MP4 container header bytes (ftyp at offset 4) per `_detect_container`."""
    # ftm: 4-byte size + 'ftypisom' brand + version
    return b"\x00\x00\x00\x18ftypisom\x00\x00\x00\x00isomiso2mp41"


async def test_zero_byte_and_corrupt_jpeg_and_svg_and_mp4_disguise_return_exact_codes(
    api_client: AsyncClient,
) -> None:
    """All media negatives carry exact `error.code`, not just an HTTP status."""
    admin_hdr = await auth_headers_for(api_client, "admin")
    insp_hdr = await auth_headers_for(api_client, "inspector1")
    pts = (await api_client.get("/api/v1/lighting-points", headers=admin_hdr)).json()["items"]
    pt = pts[0]
    data = {"lighting_point_id": str(pt["id"]), "scene_lighting_hint": "night"}

    # 1) 0 bytes -> 422 VALIDATION_ERROR
    r_zero = await api_client.post(
        "/api/v1/tickets/ai-candidates",
        data=data,
        files=[("files", ("zero.png", b"", "image/png"))],
        headers={**insp_hdr, "Idempotency-Key": new_idem_key("p04-zero")},
    )
    assert r_zero.status_code == 422
    assert _err(r_zero)["code"] == "VALIDATION_ERROR"

    # 2) corrupt JPEG bytes (valid ext + MIME but garbage payload) -> 415 UNSUPPORTED_IMAGE_FORMAT
    r_corrupt = await api_client.post(
        "/api/v1/tickets/ai-candidates",
        data=data,
        files=[("files", ("bad.jpg", b"\x00\x01\x02\x03" * 200, "image/jpeg"))],
        headers={**insp_hdr, "Idempotency-Key": new_idem_key("p04-corrupt")},
    )
    assert r_corrupt.status_code == 415
    assert _err(r_corrupt)["code"] == "UNSUPPORTED_IMAGE_FORMAT"

    # 3) SVG content disguised as .png -> 415 UNSUPPORTED_IMAGE_FORMAT
    r_svg = await api_client.post(
        "/api/v1/tickets/ai-candidates",
        data=data,
        files=[
            (
                "files",
                ("fake.png", b'<svg xmlns="http://www.w3.org/2000/svg"></svg>', "image/png"),
            )
        ],
        headers={**insp_hdr, "Idempotency-Key": new_idem_key("p04-svg")},
    )
    assert r_svg.status_code == 415
    assert _err(r_svg)["code"] == "UNSUPPORTED_IMAGE_FORMAT"

    # 4) real MP4 magic with .jpg filename + image/jpeg MIME -> 415 MEDIA_VIDEO_STREAM_FORBIDDEN
    r_mp4 = await api_client.post(
        "/api/v1/tickets/ai-candidates",
        data=data,
        files=[("files", ("clip.jpg", _mp4_magic_bytes(), "image/jpeg"))],
        headers={**insp_hdr, "Idempotency-Key": new_idem_key("p04-mp4")},
    )
    assert r_mp4.status_code == 415
    assert _err(r_mp4)["code"] == "MEDIA_VIDEO_STREAM_FORBIDDEN"


async def test_exact_15mib_boundary_and_over_15mib_and_over_40mp_boundaries(
    api_client: AsyncClient,
) -> None:
    """Boundary precision: exactly 15MiB -> NOT 413 (falls through to format check)."""
    admin_hdr = await auth_headers_for(api_client, "admin")
    insp_hdr = await auth_headers_for(api_client, "inspector1")
    pts = (await api_client.get("/api/v1/lighting-points", headers=admin_hdr)).json()["items"]
    pt = pts[0]
    data = {"lighting_point_id": str(pt["id"]), "scene_lighting_hint": "night"}

    # exactly 15 MiB of random bytes -> must NOT be 413 (size gate is `>` 15MiB);
    # instead it falls through to format validation -> 415 UNSUPPORTED_IMAGE_FORMAT
    exact_15mib = b"\xab" * (15 * 1024 * 1024)
    r_edge = await api_client.post(
        "/api/v1/tickets/ai-candidates",
        data=data,
        files=[("files", ("edge.bin", exact_15mib, "application/octet-stream"))],
        headers={**insp_hdr, "Idempotency-Key": new_idem_key("p04-e15")},
    )
    assert r_edge.status_code == 415, r_edge.text
    assert _err(r_edge)["code"] == "UNSUPPORTED_IMAGE_FORMAT"

    # >15 MiB -> 413 IMAGE_SIZE_EXCEEDED (before any format check)
    over_15mib = b"\xab" * (15 * 1024 * 1024 + 1)
    r_big = await api_client.post(
        "/api/v1/tickets/ai-candidates",
        data=data,
        files=[("files", ("big.bin", over_15mib, "application/octet-stream"))],
        headers={**insp_hdr, "Idempotency-Key": new_idem_key("p04-o15")},
    )
    assert r_big.status_code == 413
    assert _err(r_big)["code"] == "IMAGE_SIZE_EXCEEDED"

    # >40MP crafted PNG -> 422 IMAGE_PIXEL_LIMIT_EXCEEDED
    def _minimal_png_with_dims(w: int, h: int) -> bytes:
        import struct
        import zlib

        def _chunk(tag: bytes, payload: bytes) -> bytes:
            crc = zlib.crc32(tag + payload) & 0xFFFFFFFF
            return struct.pack(">I", len(payload)) + tag + payload + struct.pack(">I", crc)

        sig = b"\x89PNG\r\n\x1a\n"
        ihdr = _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        idat = _chunk(b"IDAT", zlib.compress(b"\x00" * 64))
        iend = _chunk(b"IEND", b"")
        return sig + ihdr + idat + iend

    over_40mp = _minimal_png_with_dims(8000, 5001)  # 40,008,000 px > 40,000,000
    r_40mp = await api_client.post(
        "/api/v1/tickets/ai-candidates",
        data=data,
        files=[("files", ("bomb.png", over_40mp, "image/png"))],
        headers={**insp_hdr, "Idempotency-Key": new_idem_key("p04-40mp")},
    )
    assert r_40mp.status_code == 422
    assert _err(r_40mp)["code"] == "IMAGE_PIXEL_LIMIT_EXCEEDED"


async def test_evidence_upload_duplicate_and_invalid_type_and_cross_region_guard(
    api_client: AsyncClient,
) -> None:
    """evidences upload: duplicate sha256 422, bad evidence_type 422, cross-region 403."""
    admin_hdr = await auth_headers_for(api_client, "admin")
    insp_hdr = await auth_headers_for(api_client, "inspector1")

    pts = (await api_client.get("/api/v1/lighting-points", headers=admin_hdr)).json()["items"]
    js_pt = next(p for p in pts if p["point_code"] == "SL-ZZ-JS-0001")
    zd_pt = next(p for p in pts if p["point_code"] == "SL-ZZ-ZD-0001")

    # Admin creates a ticket on ZD point, inspector1 lacks INSPECT there -> upload must 403
    r_zd = await api_client.post(
        "/api/v1/tickets",
        json={
            "lighting_point_id": zd_pt["id"],
            "fault_type": "LAMP_OUT",
            "title": "P0-4 跨区补证目标单",
            "safety_flag": False,
        },
        headers={**admin_hdr, "Idempotency-Key": new_idem_key("p04-zd")},
    )
    assert r_zd.status_code == 201
    zd_tid = r_zd.json()["id"]
    jpg = make_static_image("JPEG", bg_color=(12, 88, 144))
    r_cross = await api_client.post(
        f"/api/v1/tickets/{zd_tid}/evidences",
        data={"evidence_type": "REPAIR_PHOTO"},
        files=[("files", ("r.jpg", jpg, "image/jpeg"))],
        headers=insp_hdr,
    )
    assert r_cross.status_code == 403
    assert _err(r_cross)["code"] == "FORBIDDEN_REGION_SCOPE"

    # Create a ticket on JS point (inspector1 has INSPECT scope there)
    r_t = await api_client.post(
        "/api/v1/tickets",
        json={
            "lighting_point_id": js_pt["id"],
            "fault_type": "OCCLUDED",
            "title": "P0-4 补证工单",
            "safety_flag": False,
        },
        headers={**insp_hdr, "Idempotency-Key": new_idem_key("p04-js")},
    )
    assert r_t.status_code == 201
    tid = r_t.json()["id"]

    # 422 VALIDATION_ERROR: invalid evidence_type
    r_type = await api_client.post(
        f"/api/v1/tickets/{tid}/evidences",
        data={"evidence_type": "REPORT_PHOTO"},
        files=[("files", ("a.jpg", jpg, "image/jpeg"))],
        headers=insp_hdr,
    )
    assert r_type.status_code == 422
    assert _err(r_type)["code"] == "VALIDATION_ERROR"

    # Happy upload INSPECTION_PHOTO
    r_up1 = await api_client.post(
        f"/api/v1/tickets/{tid}/evidences",
        data={"evidence_type": "INSPECTION_PHOTO"},
        files=[("files", ("a.jpg", jpg, "image/jpeg"))],
        headers=insp_hdr,
    )
    assert r_up1.status_code in {200, 201}, r_up1.text
    assert r_up1.json()[0]["evidence_type"] == "INSPECTION_PHOTO"

    # Same sha256 again -> 422 VALIDATION_ERROR with reason DUPLICATE_EVIDENCE
    r_dup = await api_client.post(
        f"/api/v1/tickets/{tid}/evidences",
        data={"evidence_type": "INSPECTION_PHOTO"},
        files=[("files", ("b.jpg", jpg, "image/jpeg"))],
        headers=insp_hdr,
    )
    assert r_dup.status_code == 422
    assert _err(r_dup)["code"] == "VALIDATION_ERROR"
    assert _err(r_dup)["details"]["reason"] == "DUPLICATE_EVIDENCE"


async def test_evidence_upload_on_terminal_ticket_returns_422_invalid_state_transition(
    api_client: AsyncClient,
) -> None:
    """Expect `422 INVALID_STATE_TRANSITION` when uploading to a CLOSED ticket."""
    admin_hdr = await auth_headers_for(api_client, "admin")
    insp_hdr = await auth_headers_for(api_client, "inspector1")
    tech1_hdr = await auth_headers_for(api_client, "tech1")

    pts = (await api_client.get("/api/v1/lighting-points", headers=admin_hdr)).json()["items"]
    js_pt = next(p for p in pts if p["point_code"] == "SL-ZZ-JS-0001")

    # Build the full workflow to CLOSED via real API calls
    r_t = await api_client.post(
        "/api/v1/tickets",
        json={
            "lighting_point_id": js_pt["id"],
            "fault_type": "OCCLUDED",
            "title": "P0-4 终态上传探测",
            "safety_flag": False,
        },
        headers={**insp_hdr, "Idempotency-Key": new_idem_key("p04-term")},
    )
    tid = r_t.json()["id"]
    cands = (
        await api_client.get(f"/api/v1/tickets/{tid}/dispatch-candidates", headers=admin_hdr)
    ).json()["candidates"]
    cand = next(c for c in cands if c["username"] == "tech1")
    r_d = await api_client.post(
        f"/api/v1/tickets/{tid}/dispatch",
        json={
            "technician_account_id": cand["account_id"],
            "scope_authorization_id": cand["scope_authorization_id"],
        },
        headers={**admin_hdr, "Idempotency-Key": new_idem_key("p04-td")},
    )
    did = int(r_d.json()["id"])
    await api_client.post(
        f"/api/v1/dispatches/{did}/accept",
        headers={**tech1_hdr, "Idempotency-Key": new_idem_key("p04-ta")},
    )
    now = __import__("datetime").datetime.now(__import__("datetime").UTC)
    await api_client.post(
        f"/api/v1/dispatches/{did}/complete",
        json={
            "electrical_safety_checked": True,
            "safety_checklist": {
                "power_isolated": True,
                "voltage_verified_zero": True,
                "insulation_intact": True,
                "grounding_connected": True,
                "ppe_worn": True,
            },
            "safety_note": "六件套齐全",
            "arrived_at": (now - __import__("datetime").timedelta(minutes=10)).isoformat(),
            "repair_summary": "修复完成",
            "materials": [],
        },
        headers={**tech1_hdr, "Idempotency-Key": new_idem_key("p04-tc")},
    )
    r_close = await api_client.post(
        f"/api/v1/tickets/{tid}/acceptance",
        json={"decision": "APPROVED", "comment": "验收通过关闭"},
        headers={**insp_hdr, "Idempotency-Key": new_idem_key("p04-tx")},
    )
    assert r_close.status_code == 200
    assert r_close.json()["ticket"]["status"] == "CLOSED"

    # Upload evidence to the CLOSED ticket -> expected contract: 422 INVALID_STATE_TRANSITION
    jpg = make_static_image("JPEG", bg_color=(12, 88, 144))
    r_upload = await api_client.post(
        f"/api/v1/tickets/{tid}/evidences",
        data={"evidence_type": "REPAIR_PHOTO"},
        files=[("files", ("post-close.jpg", jpg, "image/jpeg"))],
        headers=insp_hdr,
    )
    assert r_upload.status_code == 422, r_upload.text
    assert _err(r_upload)["code"] == "INVALID_STATE_TRANSITION"
