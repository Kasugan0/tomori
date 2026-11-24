"""P0-2 notification lifecycle gap tests: DEAD_LETTER -> retry -> re-deliver SENT + acknowledge.

All state transitions go through the real worker (`process_due_notifications_once`) and the
real API (`POST /notifications/{id}/retry|acknowledge`). No raw SQL status forging; the
`dispatcher` parameter of the worker is used to choose a succeeding delivery channel.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from tests.integration.conftest import auth_headers_for
from tomori.models.account import Account
from tomori.models.audit_log import AuditLog
from tomori.models.notification_task import NotificationTask
from tomori.services.state_machine import create_notification_task
from tomori.workers.notifier import process_due_notifications_once


def _err(resp: Any) -> dict[str, Any]:
    err: dict[str, Any] = resp.json()["error"]
    return err


async def _create_email_dead_letter(async_engine: AsyncEngine, *, t0: datetime) -> tuple[int, int]:
    """Drive an EMAIL task through 3 real worker failures into DEAD_LETTER."""
    async with AsyncSession(async_engine, expire_on_commit=False) as s, s.begin():
        inspector = await s.scalar(select(Account).where(Account.username == "inspector1"))
        assert inspector is not None
        task = create_notification_task(
            s,
            recipient_account_id=inspector.id,
            event_type="REPAIR_SLA_TIMEOUT",
            channel="EMAIL",
            title="P0-2 死信重试源",
            body="验证 DEAD_LETTER→retry→SENT 全程",
            payload_json={"deep_link": "/notifications"},
            max_attempts=3,
        )
        await s.flush()
        nid = task.id
        recipient_id = inspector.id

    for offset in (0, 60, 360):
        async with AsyncSession(async_engine, expire_on_commit=False) as s:
            await process_due_notifications_once(s, now=t0 + timedelta(seconds=offset))

    async with AsyncSession(async_engine, expire_on_commit=False) as s:
        row = await s.scalar(select(NotificationTask).where(NotificationTask.id == nid))
        assert row is not None and row.status == "DEAD_LETTER", row and row.status
        assert row.attempt_count == 3
    return nid, recipient_id


async def test_dead_letter_retry_resets_attempt_count_then_requeues_and_delivers_sent(
    api_client: AsyncClient,
    async_engine: AsyncEngine,
) -> None:
    """P0-2 core: retry resets attempt_count=0/next_retry_at=now/sent_at NULL, then re-delivers."""
    admin_hdr = await auth_headers_for(api_client, "admin")
    insp_hdr = await auth_headers_for(api_client, "inspector1")
    t0 = datetime.now(UTC)

    nid, _ = await _create_email_dead_letter(async_engine, t0=t0)

    # 403: inspector cannot retry someone else's dead-letter (ADMIN only)
    r_403 = await api_client.post(f"/api/v1/notifications/{nid}/retry", headers=insp_hdr)
    assert r_403.status_code == 403
    assert _err(r_403)["code"] == "FORBIDDEN_ROLE"

    # 404: missing notification
    r_404 = await api_client.post("/api/v1/notifications/999999/retry", headers=admin_hdr)
    assert r_404.status_code == 404
    assert _err(r_404)["code"] == "RESOURCE_NOT_FOUND"

    # POST /retry -> PENDING with attempt_count=0, next_retry_at≈now, sent_at NULL, last_error NULL
    t_retry = datetime.now(UTC)
    r_retry = await api_client.post(f"/api/v1/notifications/{nid}/retry", headers=admin_hdr)
    assert r_retry.status_code == 200, r_retry.text
    body = r_retry.json()
    assert body["status"] == "PENDING"
    assert body["attempt_count"] == 0
    assert body["sent_at"] is None
    assert body["last_error"] is None
    assert body["next_retry_at"] is not None

    # DB verification (committed row, not just response)
    async with AsyncSession(async_engine, expire_on_commit=False) as s:
        row = await s.scalar(select(NotificationTask).where(NotificationTask.id == nid))
        assert row is not None
        assert row.status == "PENDING"
        assert row.attempt_count == 0
        assert row.sent_at is None
        assert row.last_error is None
        assert row.next_retry_at is not None
        assert abs((row.next_retry_at - t_retry).total_seconds()) < 5.0
        audits = (
            await s.scalars(
                select(AuditLog).where(
                    AuditLog.resource_id == nid,
                    AuditLog.action == "NOTIFICATION_RETRIED",
                )
            )
        ).all()
        assert len(audits) == 1

    # Re-deliver via real worker with a succeeding dispatcher -> SENT
    t_sent = t_retry + timedelta(seconds=1)
    async with AsyncSession(async_engine, expire_on_commit=False) as s:
        summary = await process_due_notifications_once(s, dispatcher=lambda _task: True, now=t_sent)
    # >=1: retried task + previously created IN_APP admin dead-letter alerts all get sent
    assert summary["sent"] >= 1

    async with AsyncSession(async_engine, expire_on_commit=False) as s:
        row2 = await s.scalar(select(NotificationTask).where(NotificationTask.id == nid))
        assert row2 is not None
        assert row2.status == "SENT"
        assert row2.sent_at is not None
        assert row2.next_retry_at is None


async def test_acknowledge_dead_letter_records_acknowledged_by_and_sent_at_and_illegal_retry(
    api_client: AsyncClient,
    async_engine: AsyncEngine,
) -> None:
    """P0-2 acknowledge: DEAD_LETTER -> SENT with acknowledged_by/at + sent_at; repeat -> 422."""
    admin_hdr = await auth_headers_for(api_client, "admin")
    insp_hdr = await auth_headers_for(api_client, "inspector1")
    t0 = datetime.now(UTC)

    nid, _ = await _create_email_dead_letter(async_engine, t0=t0)

    # 403 non-admin
    r_403 = await api_client.post(f"/api/v1/notifications/{nid}/acknowledge", headers=insp_hdr)
    assert r_403.status_code == 403
    assert _err(r_403)["code"] == "FORBIDDEN_ROLE"

    # 404 missing
    r_404 = await api_client.post("/api/v1/notifications/999999/acknowledge", headers=admin_hdr)
    assert r_404.status_code == 404
    assert _err(r_404)["code"] == "RESOURCE_NOT_FOUND"

    # POST /acknowledge -> SENT + acknowledged_by=admin + acknowledged_at + sent_at
    r_ack = await api_client.post(f"/api/v1/notifications/{nid}/acknowledge", headers=admin_hdr)
    assert r_ack.status_code == 200, r_ack.text
    body = r_ack.json()
    assert body["status"] == "SENT"
    assert body["sent_at"] is not None
    assert body["acknowledged_at"] is not None

    async with AsyncSession(async_engine, expire_on_commit=False) as s:
        row = await s.scalar(select(NotificationTask).where(NotificationTask.id == nid))
        assert row is not None
        assert row.status == "SENT"
        assert row.sent_at is not None
        assert row.acknowledged_at is not None
        admin = await s.scalar(select(Account).where(Account.username == "admin"))
        assert admin is not None
        assert row.acknowledged_by_account_id == admin.id
        audits = (
            await s.scalars(
                select(AuditLog).where(
                    AuditLog.resource_id == nid,
                    AuditLog.action == "NOTIFICATION_ACKNOWLEDGED",
                )
            )
        ).all()
        assert len(audits) == 1

    # Repeat acknowledge on terminal SENT -> 422 INVALID_STATE_TRANSITION
    r_repeat = await api_client.post(f"/api/v1/notifications/{nid}/acknowledge", headers=admin_hdr)
    assert r_repeat.status_code == 422
    assert _err(r_repeat)["code"] == "INVALID_STATE_TRANSITION"


async def test_retry_on_non_dead_letter_returns_422_and_illegal_transitions(
    api_client: AsyncClient,
    async_engine: AsyncEngine,
) -> None:
    """retry on PENDING/PROCESSING tasks is INVALID_STATE_TRANSITION; on SENT likewise."""
    admin_hdr = await auth_headers_for(api_client, "admin")
    t0 = datetime.now(UTC)

    async with AsyncSession(async_engine, expire_on_commit=False) as s, s.begin():
        inspector = await s.scalar(select(Account).where(Account.username == "inspector1"))
        assert inspector is not None
        pending = create_notification_task(
            s,
            recipient_account_id=inspector.id,
            event_type="AI_CANDIDATE_CREATED",
            channel="IN_APP",
            title="P0-2 非法重试源",
            body="未死信不可重试",
        )
        await s.flush()
        pending_id = pending.id

    # PENDING -> retry must be 422 INVALID_STATE_TRANSITION (D7 only allows DEAD_LETTER)
    r_bad = await api_client.post(f"/api/v1/notifications/{pending_id}/retry", headers=admin_hdr)
    assert r_bad.status_code == 422
    assert _err(r_bad)["code"] == "INVALID_STATE_TRANSITION"

    # Deliver pending via worker to reach SENT, then retry must also be 422
    async with AsyncSession(async_engine, expire_on_commit=False) as s:
        await process_due_notifications_once(s, dispatcher=lambda _t: True, now=t0)
    r_sent = await api_client.post(f"/api/v1/notifications/{pending_id}/retry", headers=admin_hdr)
    assert r_sent.status_code == 422
    assert _err(r_sent)["code"] == "INVALID_STATE_TRANSITION"
