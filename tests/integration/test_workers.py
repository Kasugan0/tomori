"""Real PostgreSQL 17 integration tests for `tomori.workers` (`notifier` & `sla_scanner`).

Strict rules:
- Connects to isolated `tomori_test` via `async_engine` (no mocked DB, no SQLite).
- State transitions go exclusively through domain/worker functions (no raw SQL state forging).
- Fresh `AsyncSession` verification reads back committed PG rows after each worker step.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from tests.integration.conftest import reset_to_post_migration_baseline
from tomori.models.account import Account
from tomori.models.account_role import AccountRole
from tomori.models.audit_log import AuditLog
from tomori.models.fault_evidence import FaultEvidence
from tomori.models.fault_ticket import FaultTicket
from tomori.models.lighting_point import LightingPoint
from tomori.models.notification_task import NotificationTask
from tomori.models.role import Role
from tomori.services.auth import hash_password, seed_demo_data
from tomori.services.state_machine import create_notification_task, transition_notification
from tomori.workers import notifier as notifier_mod
from tomori.workers.notifier import (
    compute_backoff_seconds,
    default_channel_dispatcher,
    process_due_notifications_once,
    recover_stuck_processing_tasks,
)
from tomori.workers.sla_scanner import (
    cleanup_orphan_uploads_once,
    expire_stale_ai_candidates_once,
)


@pytest_asyncio.fixture(scope="function")
async def seeded_worker_db(async_engine: AsyncEngine) -> AsyncIterator[AsyncEngine]:
    """Reset `tomori_test` to baseline and run `seed_demo_data` via real transaction."""
    await reset_to_post_migration_baseline(async_engine)
    async with AsyncSession(async_engine, expire_on_commit=False) as session, session.begin():
        await seed_demo_data(session)
    try:
        yield async_engine
    finally:
        await reset_to_post_migration_baseline(async_engine)


async def _get_account(session: AsyncSession, username: str) -> Account:
    acc = await session.scalar(select(Account).where(Account.username == username))
    assert acc is not None, f"missing demo account {username}"
    return acc


async def test_process_due_notifications_happy_backoff_d5_d2_and_dead_letter(
    seeded_worker_db: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify IN_APP SENT, D5->D2 retry after 1m/5m backoff, and DEAD_LETTER admin fanout."""
    engine = seeded_worker_db
    t0 = datetime(2026, 11, 24, 10, 0, 0, tzinfo=UTC)

    # Seed an extra active ADMIN (`admin2`) and a disabled ADMIN (`admin_off`)
    async with AsyncSession(engine, expire_on_commit=False) as s, s.begin():
        inspector = await _get_account(s, "inspector1")
        admin1 = await _get_account(s, "admin")
        admin_role = await s.scalar(select(Role).where(Role.code == "ADMIN"))
        assert admin_role is not None

        admin2 = Account(
            username="admin2_worker",
            full_name="值班管理员二",
            status="ACTIVE",
            skills=[],
            max_active_dispatch=3,
            token_version=1,
            password_hash=hash_password("Admin2Test#123"),
        )
        admin_off = Account(
            username="admin_disabled_worker",
            full_name="已停用管理员",
            status="DISABLED",
            skills=[],
            max_active_dispatch=3,
            token_version=1,
            password_hash=hash_password("AdminOff#123"),
        )
        s.add_all([admin2, admin_off])
        await s.flush()
        s.add_all(
            [
                AccountRole(
                    account_id=admin2.id,
                    role_id=admin_role.id,
                    granted_by_account_id=admin1.id,
                ),
                AccountRole(
                    account_id=admin_off.id,
                    role_id=admin_role.id,
                    granted_by_account_id=admin1.id,
                ),
            ]
        )
        inapp_task = create_notification_task(
            s,
            recipient_account_id=inspector.id,
            event_type="AI_CANDIDATE_CREATED",
            channel="IN_APP",
            title="站内通知正常投递",
            body="测试 IN_APP",
        )
        future_task = create_notification_task(
            s,
            recipient_account_id=inspector.id,
            event_type="AI_CANDIDATE_CREATED",
            channel="IN_APP",
            title="未到期任务",
            body="不应提前抢占",
        )
        future_task.next_retry_at = t0 + timedelta(minutes=2)
        await s.flush()
        inapp_id = inapp_task.id
        future_id = future_task.id
        admin1_id = admin1.id
        admin2_id = admin2.id
        admin_off_id = admin_off.id
        inspector_id = inspector.id

    # 1) IN_APP -> PENDING -> PROCESSING -> SENT (future task skipped)
    async with AsyncSession(engine, expire_on_commit=False) as s:
        summary1 = await process_due_notifications_once(
            s,
            dispatcher=default_channel_dispatcher,
            now=t0,
        )
    assert summary1 == {
        "claimed": 1,
        "sent": 1,
        "failed": 0,
        "dead_lettered": 0,
        "dead_letter_alerts_created": 0,
    }
    async with AsyncSession(engine, expire_on_commit=False) as s:
        row_inapp = await s.scalar(select(NotificationTask).where(NotificationTask.id == inapp_id))
        row_future = await s.scalar(
            select(NotificationTask).where(NotificationTask.id == future_id)
        )
        assert row_inapp is not None and row_inapp.status == "SENT"
        assert row_inapp.sent_at == t0
        assert row_inapp.next_retry_at is None
        assert row_future is not None and row_future.status == "PENDING"

    # Deliver `future_task` cleanly at t0 + 2m so queue is empty for SMS retry test
    async with AsyncSession(engine, expire_on_commit=False) as s:
        summary_future = await process_due_notifications_once(s, now=t0 + timedelta(minutes=2))
    assert summary_future["sent"] == 1

    # 2) SMS backoff (1m -> 5m) + spy verifying D5 (FAILED->PENDING) -> D2 (PENDING->PROCESSING)
    async with AsyncSession(engine, expire_on_commit=False) as s, s.begin():
        sms_task = create_notification_task(
            s,
            recipient_account_id=inspector_id,
            event_type="TICKET_DISPATCHED",
            channel="SMS",
            title="短信退避测试",
            body="验证 D5->D2 路径",
            max_attempts=3,
        )
        await s.flush()
        sms_id = sms_task.id

    # Fail #1 at t0 -> FAILED, attempt=1, backoff=60s (1m)
    async with AsyncSession(engine, expire_on_commit=False) as s:
        sum_f1 = await process_due_notifications_once(s, now=t0)
    assert sum_f1 == {
        "claimed": 1,
        "sent": 0,
        "failed": 1,
        "dead_lettered": 0,
        "dead_letter_alerts_created": 0,
    }
    assert compute_backoff_seconds(1) == 60
    async with AsyncSession(engine, expire_on_commit=False) as s:
        row_sms = await s.scalar(select(NotificationTask).where(NotificationTask.id == sms_id))
        assert row_sms is not None
        assert row_sms.status == "FAILED"
        assert row_sms.attempt_count == 1
        assert row_sms.next_retry_at == t0 + timedelta(seconds=60)
        assert row_sms.last_error == "channel_sms_not_configured_in_v1"

    # Not due at t0 + 59s -> claimed=0
    async with AsyncSession(engine, expire_on_commit=False) as s:
        sum_early1 = await process_due_notifications_once(s, now=t0 + timedelta(seconds=59))
    assert sum_early1["claimed"] == 0

    # Fail #2 at t0 + 60s -> FAILED, attempt=2, backoff=300s (5m)
    t1 = t0 + timedelta(seconds=60)
    async with AsyncSession(engine, expire_on_commit=False) as s:
        sum_f2 = await process_due_notifications_once(s, now=t1)
    assert sum_f2["failed"] == 1
    assert compute_backoff_seconds(2) == 300
    async with AsyncSession(engine, expire_on_commit=False) as s:
        row_sms2 = await s.scalar(select(NotificationTask).where(NotificationTask.id == sms_id))
        assert row_sms2 is not None
        assert row_sms2.status == "FAILED"
        assert row_sms2.attempt_count == 2
        assert row_sms2.next_retry_at == t1 + timedelta(seconds=300)

    # Not due at t1 + 299s -> claimed=0
    async with AsyncSession(engine, expire_on_commit=False) as s:
        sum_early2 = await process_due_notifications_once(s, now=t1 + timedelta(seconds=299))
    assert sum_early2["claimed"] == 0

    # Spy pass-through on transition_notification (real DB transitions, zero mocking)
    recorded_edges: list[tuple[int, str, str]] = []
    real_transition = transition_notification

    async def _spy_transition(
        session: AsyncSession,
        task: NotificationTask,
        to_status: str,
        **kwargs: Any,
    ) -> AuditLog:
        recorded_edges.append((task.id, task.status, to_status))
        return await real_transition(session, task, to_status, **kwargs)

    monkeypatch.setattr(notifier_mod, "transition_notification", _spy_transition)

    # Succeed at t1 + 300s -> MUST traverse D5 (FAILED->PENDING) -> D2 (PENDING->PROCESSING) -> SENT
    t2 = t1 + timedelta(seconds=300)
    async with AsyncSession(engine, expire_on_commit=False) as s:
        sum_ok = await process_due_notifications_once(
            s,
            dispatcher=lambda _task: True,
            now=t2,
        )
    assert sum_ok == {
        "claimed": 1,
        "sent": 1,
        "failed": 0,
        "dead_lettered": 0,
        "dead_letter_alerts_created": 0,
    }
    assert [(f, t) for tid, f, t in recorded_edges if tid == sms_id] == [
        ("FAILED", "PENDING"),
        ("PENDING", "PROCESSING"),
        ("PROCESSING", "SENT"),
    ]
    async with AsyncSession(engine, expire_on_commit=False) as s:
        row_sms_sent = await s.scalar(select(NotificationTask).where(NotificationTask.id == sms_id))
        assert row_sms_sent is not None
        assert row_sms_sent.status == "SENT"
        assert row_sms_sent.sent_at == t2
        assert row_sms_sent.next_retry_at is None
        dead_audits = (
            await s.scalars(
                select(AuditLog).where(
                    AuditLog.resource_id == sms_id,
                    AuditLog.action == "NOTIFICATION_DEAD_LETTER",
                )
            )
        ).all()
        assert len(dead_audits) == 0

    monkeypatch.undo()

    # 3) Exhaust max_attempts=3 on EMAIL -> DEAD_LETTER + alerts to BOTH active ADMINs + 1 audit
    async with AsyncSession(engine, expire_on_commit=False) as s, s.begin():
        email_task = create_notification_task(
            s,
            recipient_account_id=inspector_id,
            event_type="REPAIR_SLA_TIMEOUT",
            channel="EMAIL",
            title="邮件死信测试",
            body="3 次失败应进死信",
            max_attempts=3,
        )
        await s.flush()
        email_id = email_task.id

    async with AsyncSession(engine, expire_on_commit=False) as s:
        await process_due_notifications_once(s, now=t2)
    async with AsyncSession(engine, expire_on_commit=False) as s:
        await process_due_notifications_once(s, now=t2 + timedelta(seconds=60))
    t3 = t2 + timedelta(seconds=360)
    async with AsyncSession(engine, expire_on_commit=False) as s:
        sum_dlq = await process_due_notifications_once(s, now=t3)

    assert sum_dlq == {
        "claimed": 1,
        "sent": 0,
        "failed": 0,
        "dead_lettered": 1,
        "dead_letter_alerts_created": 2,
    }
    async with AsyncSession(engine, expire_on_commit=False) as s:
        row_dead = await s.scalar(select(NotificationTask).where(NotificationTask.id == email_id))
        assert row_dead is not None
        assert row_dead.status == "DEAD_LETTER"
        assert row_dead.attempt_count == 3
        assert row_dead.last_error == "channel_email_not_configured_in_v1"
        assert row_dead.next_retry_at is None

        dlq_audits = (
            await s.scalars(
                select(AuditLog).where(
                    AuditLog.resource_type == "NOTIFICATION",
                    AuditLog.resource_id == email_id,
                    AuditLog.action == "NOTIFICATION_DEAD_LETTER",
                )
            )
        ).all()
        assert len(dlq_audits) == 1

        alerts = (
            await s.scalars(
                select(NotificationTask).where(
                    NotificationTask.event_type == "NOTIFICATION_DEAD_LETTER"
                )
            )
        ).all()
        recipients = {a.recipient_account_id for a in alerts}
        assert recipients == {admin1_id, admin2_id}
        assert admin_off_id not in recipients
        assert all(
            a.payload_json is not None and a.payload_json["dead_letter_task_id"] == email_id
            for a in alerts
        )


async def test_recover_stuck_processing_tasks_requeue_and_dead_letter(
    seeded_worker_db: AsyncEngine,
) -> None:
    """Tasks stuck in PROCESSING >10m: <max_attempts -> PENDING; >=max_attempts -> DEAD_LETTER."""
    engine = seeded_worker_db
    t_now = datetime(2026, 11, 24, 12, 0, 0, tzinfo=UTC)

    async with AsyncSession(engine, expire_on_commit=False) as s, s.begin():
        inspector = await _get_account(s, "inspector1")
        admin1 = await _get_account(s, "admin")

        fresh = create_notification_task(
            s,
            recipient_account_id=inspector.id,
            event_type="AI_CANDIDATE_CREATED",
            channel="IN_APP",
            title="9分钟未超时",
            body="保持 PROCESSING",
            max_attempts=3,
        )
        requeue = create_notification_task(
            s,
            recipient_account_id=inspector.id,
            event_type="TICKET_DISPATCHED",
            channel="SMS",
            title="11分钟未满次数回退",
            body="应回 PENDING",
            max_attempts=3,
        )
        exhausted = create_notification_task(
            s,
            recipient_account_id=inspector.id,
            event_type="REPAIR_SLA_TIMEOUT",
            channel="EMAIL",
            title="15分钟满次数转死信",
            body="应转 DEAD_LETTER",
            max_attempts=3,
        )
        await s.flush()

        await transition_notification(s, fresh, "PROCESSING", write_audit=False)
        fresh.attempt_count = 0
        fresh.updated_at = t_now - timedelta(minutes=9)

        await transition_notification(s, requeue, "PROCESSING", write_audit=False)
        requeue.attempt_count = 1
        requeue.updated_at = t_now - timedelta(minutes=11)

        await transition_notification(s, exhausted, "PROCESSING", write_audit=False)
        exhausted.attempt_count = 2
        exhausted.updated_at = t_now - timedelta(minutes=15)

        await s.flush()
        fresh_id, requeue_id, exhausted_id = fresh.id, requeue.id, exhausted.id
        admin1_id = admin1.id

    async with AsyncSession(engine, expire_on_commit=False) as s:
        recovered = await recover_stuck_processing_tasks(s, now=t_now)
    assert recovered == 2

    async with AsyncSession(engine, expire_on_commit=False) as s:
        r_fresh = await s.scalar(select(NotificationTask).where(NotificationTask.id == fresh_id))
        r_requeue = await s.scalar(
            select(NotificationTask).where(NotificationTask.id == requeue_id)
        )
        r_ex = await s.scalar(select(NotificationTask).where(NotificationTask.id == exhausted_id))

        assert r_fresh is not None and r_fresh.status == "PROCESSING"
        assert r_requeue is not None
        assert r_requeue.status == "PENDING"
        assert r_requeue.attempt_count == 2
        assert r_requeue.last_error == "worker_processing_timeout_10m"
        assert r_requeue.next_retry_at == t_now

        assert r_ex is not None
        assert r_ex.status == "DEAD_LETTER"
        assert r_ex.attempt_count == 3
        assert r_ex.last_error == "worker_processing_timeout_10m"

        audits = (
            await s.scalars(
                select(AuditLog).where(
                    AuditLog.resource_id == exhausted_id,
                    AuditLog.action == "NOTIFICATION_DEAD_LETTER",
                )
            )
        ).all()
        assert len(audits) == 1

        alerts = (
            await s.scalars(
                select(NotificationTask).where(
                    NotificationTask.event_type == "NOTIFICATION_DEAD_LETTER",
                    NotificationTask.recipient_account_id == admin1_id,
                )
            )
        ).all()
        assert len(alerts) == 1
        assert alerts[0].payload_json is not None
        assert alerts[0].payload_json["dead_letter_task_id"] == exhausted_id


async def test_expire_stale_ai_candidates_once_72h_ttl_and_notification(
    seeded_worker_db: AsyncEngine,
) -> None:
    """AI_CANDIDATE_PENDING > 72h TTL -> CANCELLED + AI_CANDIDATE_EXPIRED notification."""
    engine = seeded_worker_db
    t_now = datetime(2026, 11, 24, 14, 0, 0, tzinfo=UTC)

    async with AsyncSession(engine, expire_on_commit=False) as s, s.begin():
        inspector = await _get_account(s, "inspector1")
        pts = list((await s.scalars(select(LightingPoint).order_by(LightingPoint.id))).all())
        assert len(pts) >= 3

        stale_ticket = FaultTicket(
            ticket_no="FT-20261124-9001",
            region_id=pts[0].region_id,
            lighting_point_id=pts[0].id,
            report_source="AI_CANDIDATE",
            fault_type="LAMP_OUT",
            title="超72h未确认AI候选",
            status="AI_CANDIDATE_PENDING",
            reporter_account_id=inspector.id,
            confirmed_by_inspector=False,
            created_at=t_now - timedelta(hours=72, minutes=1),
            updated_at=t_now - timedelta(hours=72, minutes=1),
        )
        fresh_ticket = FaultTicket(
            ticket_no="FT-20261124-9002",
            region_id=pts[1].region_id,
            lighting_point_id=pts[1].id,
            report_source="AI_CANDIDATE",
            fault_type="OCCLUDED",
            title="71h59m未超期AI候选",
            status="AI_CANDIDATE_PENDING",
            reporter_account_id=inspector.id,
            confirmed_by_inspector=False,
            created_at=t_now - timedelta(hours=71, minutes=59),
            updated_at=t_now - timedelta(hours=71, minutes=59),
        )
        non_ai_ticket = FaultTicket(
            ticket_no="FT-20261124-9003",
            region_id=pts[2].region_id,
            lighting_point_id=pts[2].id,
            report_source="MANUAL_INSPECT",
            fault_type="FIXTURE_DAMAGED",
            title="已确认待派工不收72h TTL影响",
            status="PENDING_DISPATCH",
            priority="P3",
            reporter_account_id=inspector.id,
            confirmed_by_inspector=True,
            confirmed_by_inspector_id=inspector.id,
            confirmed_at=t_now - timedelta(hours=100),
            created_at=t_now - timedelta(hours=100),
            updated_at=t_now - timedelta(hours=100),
        )
        s.add_all([stale_ticket, fresh_ticket, non_ai_ticket])
        await s.flush()
        stale_id, fresh_id, non_ai_id = stale_ticket.id, fresh_ticket.id, non_ai_ticket.id
        inspector_id = inspector.id

    async with AsyncSession(engine, expire_on_commit=False) as s:
        expired = await expire_stale_ai_candidates_once(s, now=t_now)
    assert expired == 1

    # Second call at same timestamp must be idempotent
    async with AsyncSession(engine, expire_on_commit=False) as s:
        expired2 = await expire_stale_ai_candidates_once(s, now=t_now)
    assert expired2 == 0

    async with AsyncSession(engine, expire_on_commit=False) as s:
        r_stale = await s.scalar(select(FaultTicket).where(FaultTicket.id == stale_id))
        r_fresh = await s.scalar(select(FaultTicket).where(FaultTicket.id == fresh_id))
        r_non_ai = await s.scalar(select(FaultTicket).where(FaultTicket.id == non_ai_id))
        assert r_stale is not None and r_stale.status == "CANCELLED"
        assert r_stale.cancel_reason is not None and "72h TTL" in r_stale.cancel_reason
        assert r_stale.cancelled_at is not None
        assert r_fresh is not None and r_fresh.status == "AI_CANDIDATE_PENDING"
        assert r_non_ai is not None and r_non_ai.status == "PENDING_DISPATCH"

        audits = (
            await s.scalars(
                select(AuditLog).where(
                    AuditLog.ticket_id == stale_id,
                    AuditLog.action == "TICKET_CANCELLED",
                )
            )
        ).all()
        assert len(audits) == 1

        notifs = (
            await s.scalars(
                select(NotificationTask).where(
                    NotificationTask.related_ticket_id == stale_id,
                    NotificationTask.event_type == "AI_CANDIDATE_EXPIRED",
                )
            )
        ).all()
        assert len(notifs) == 1
        assert notifs[0].recipient_account_id == inspector_id


async def test_cleanup_orphan_uploads_once_preserves_db_and_recent_files(
    seeded_worker_db: AsyncEngine,
    tmp_path: Path,
) -> None:
    """Delete >24h orphan files without DB record; keep tracked and <24h files."""
    engine = seeded_worker_db
    t_now = datetime(2026, 11, 24, 16, 0, 0, tzinfo=UTC)
    upload_root = tmp_path / "uploads"
    (upload_root / "2026" / "11").mkdir(parents=True, exist_ok=True)
    (upload_root / "vision").mkdir(parents=True, exist_ok=True)

    orphan_old = upload_root / "2026" / "11" / "orphan_old.jpg"
    orphan_fresh = upload_root / "2026" / "11" / "orphan_fresh.jpg"
    tracked_old = upload_root / "2026" / "11" / "tracked_old.jpg"
    analysis_id = uuid.UUID("11111111-2222-3333-4444-555555555555")
    annotated_old = upload_root / "vision" / f"{analysis_id}_annotated.jpg"

    for fp in (orphan_old, orphan_fresh, tracked_old, annotated_old):
        fp.write_bytes(b"synthetic-image-bytes")

    old_ts = (t_now - timedelta(hours=25)).timestamp()
    fresh_ts = (t_now - timedelta(hours=23)).timestamp()
    os.utime(orphan_old, (old_ts, old_ts))
    os.utime(tracked_old, (old_ts, old_ts))
    os.utime(annotated_old, (old_ts, old_ts))
    os.utime(orphan_fresh, (fresh_ts, fresh_ts))

    async with AsyncSession(engine, expire_on_commit=False) as s, s.begin():
        inspector = await _get_account(s, "inspector1")
        ev = FaultEvidence(
            ticket_id=None,
            analysis_id=analysis_id,
            evidence_type="INSPECTION_PHOTO",
            media_mime="image/jpeg",
            storage_path="2026/11/tracked_old.jpg",
            file_size_bytes=21,
            sha256="a" * 64,
            image_width=100,
            image_height=100,
            ai_result_json={
                "annotated_image_url": "/api/v1/evidence/1/annotated",
                "requires_inspector_confirmation": True,
                "replaces_electrical_safety_check": False,
            },
            uploaded_by_account_id=inspector.id,
        )
        s.add(ev)

    async with AsyncSession(engine, expire_on_commit=False) as s:
        deleted = await cleanup_orphan_uploads_once(s, upload_dir=upload_root, now=t_now)

    assert deleted == 1
    assert not orphan_old.exists()
    assert orphan_fresh.exists()
    assert tracked_old.exists()
    assert annotated_old.exists()


# ── P0-5 side-effect assertions: notification payload + audit snapshot fields ────────────


async def test_notification_payload_and_audit_business_snapshot_side_effects(
    seeded_worker_db: AsyncEngine,
) -> None:
    """AI_CANDIDATE_EXPIRED + NOTIFICATION_DEAD_LETTER notifications carry full payload_json
    (deep_link/title/body) and audit_logs carry `from_status`/`to_status`/`actor_account_id`."""
    engine = seeded_worker_db
    t_now = datetime(2026, 11, 24, 18, 0, 0, tzinfo=UTC)

    # ── 1) expire stale AI candidate -> AI_CANDIDATE_EXPIRED notification payload ──
    async with AsyncSession(engine, expire_on_commit=False) as s, s.begin():
        inspector = await _get_account(s, "inspector1")
        pts = list((await s.scalars(select(LightingPoint).order_by(LightingPoint.id))).all())
        stale = FaultTicket(
            ticket_no="FT-20261124-9501",
            region_id=pts[0].region_id,
            lighting_point_id=pts[0].id,
            report_source="AI_CANDIDATE",
            fault_type="LAMP_OUT",
            title="P0-5 过期通知副作用",
            status="AI_CANDIDATE_PENDING",
            reporter_account_id=inspector.id,
            confirmed_by_inspector=False,
            created_at=t_now - timedelta(hours=80),
            updated_at=t_now - timedelta(hours=80),
        )
        s.add(stale)
        await s.flush()
        stale_id, inspector_id = stale.id, inspector.id

    async with AsyncSession(engine, expire_on_commit=False) as s:
        expired = await expire_stale_ai_candidates_once(s, now=t_now)
    assert expired == 1

    async with AsyncSession(engine, expire_on_commit=False) as s:
        notifs = (
            await s.scalars(
                select(NotificationTask).where(
                    NotificationTask.related_ticket_id == stale_id,
                    NotificationTask.event_type == "AI_CANDIDATE_EXPIRED",
                )
            )
        ).all()
        assert len(notifs) == 1
        n = notifs[0]
        # Side-effect assertions: payload carries deep_link + semantic title/body
        assert n.recipient_account_id == inspector_id
        assert n.channel == "IN_APP"
        assert n.title is not None and len(n.title) > 0
        assert n.body is not None and len(n.body) > 0
        assert n.payload_json is not None
        assert n.payload_json.get("deep_link") is not None
        assert "/tickets/" in n.payload_json["deep_link"]
        assert n.payload_json["deep_link"].endswith(str(stale_id))
        assert n.status == "PENDING"

        audits = (
            await s.scalars(
                select(AuditLog).where(
                    AuditLog.ticket_id == stale_id,
                    AuditLog.action == "TICKET_CANCELLED",
                )
            )
        ).all()
        assert len(audits) == 1
        snap = audits[0].business_snapshot_json or {}
        assert snap.get("from_status") == "AI_CANDIDATE_PENDING"
        assert snap.get("to_status") == "CANCELLED"
        assert audits[0].action == "TICKET_CANCELLED"

    # ── 2) dead-letter alert -> NOTIFICATION_DEAD_LETTER payload + audit snapshot ──
    async with AsyncSession(engine, expire_on_commit=False) as s, s.begin():
        inspector = await _get_account(s, "inspector1")
        email_task = create_notification_task(
            s,
            recipient_account_id=inspector.id,
            event_type="REPAIR_SLA_TIMEOUT",
            channel="EMAIL",
            title="P0-5 死信副作用",
            body="死信载荷断言",
            payload_json={"deep_link": "/tickets/42"},
            max_attempts=3,
        )
        await s.flush()
        email_id = email_task.id

    t0 = t_now
    for off in (0, 60, 360):
        async with AsyncSession(engine, expire_on_commit=False) as s:
            await process_due_notifications_once(s, now=t0 + timedelta(seconds=off))

    async with AsyncSession(engine, expire_on_commit=False) as s:
        alerts = (
            await s.scalars(
                select(NotificationTask).where(
                    NotificationTask.event_type == "NOTIFICATION_DEAD_LETTER"
                )
            )
        ).all()
        assert len(alerts) >= 1
        for a in alerts:
            # Side-effect: admin alert carries structured payload pointing back to dead task
            assert a.channel == "IN_APP"
            assert a.title is not None and len(a.title) > 0
            assert a.body is not None and len(a.body) > 0
            assert a.payload_json is not None
            assert a.payload_json.get("dead_letter_task_id") == email_id
            assert a.payload_json.get("deep_link") is not None

        dlq_audits = (
            await s.scalars(
                select(AuditLog).where(
                    AuditLog.resource_id == email_id,
                    AuditLog.action == "NOTIFICATION_DEAD_LETTER",
                )
            )
        ).all()
        assert len(dlq_audits) == 1
        dsnap = dlq_audits[0].business_snapshot_json or {}
        assert dsnap.get("from_status") == "FAILED"
        assert dsnap.get("to_status") == "DEAD_LETTER"
        assert dlq_audits[0].action == "NOTIFICATION_DEAD_LETTER"
