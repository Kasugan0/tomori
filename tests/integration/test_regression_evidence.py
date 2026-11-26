"""Regression evidence tests (task-23): audit-confirmed fixes must stay green.

Exact-code assertions (never just `status_code`), real `tomori_test` + ASGI only:

- Orphan AI evidence cross-uploader binding (audits/v0.1.0-independent-audit.md P1):
  `inspector1` binding an orphan evidence uploaded by `admin` -> `422 VALIDATION_ERROR`,
  the evidence stays `ticket_id IS NULL`; binding one's own orphan still works `201`.
- `SEED_DEMO=true` + `APP_ENV=production`: lifespan refuses `seed_demo_data` ->
  zero rows in `accounts`/`account_roles`; non-production env seeds the 4 demo accounts.
- Upload `Content-Length` precheck (`tickets.py:_reject_oversized_body`): declared body
  > `15 MiB * 10 + 1 MiB` overhead -> `413 IMAGE_SIZE_EXCEEDED` before form parsing;
  lying `Content-Length` (under-declared) does not bypass the per-file cap -> `413`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from asgi_lifespan import LifespanManager
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from tests.integration.conftest import (
    auth_headers_for,
    make_night_normal_lit_png,
    new_idem_key,
    reset_to_post_migration_baseline,
)
from tomori.db import session as db_session_mod
from tomori.models.account import Account
from tomori.models.account_role import AccountRole
from tomori.models.fault_evidence import FaultEvidence
from tomori.settings import MAX_FILES_PER_REQUEST, get_settings


def _err(resp: Any) -> dict[str, Any]:
    err: dict[str, Any] = resp.json()["error"]
    return err


@pytest.fixture
def demo_seed_lifespan(monkeypatch: pytest.MonkeyPatch) -> None:
    """Enable `SEED_DEMO` before `asgi_app` builds the app and enters its lifespan.

    Must be listed before `api_client` in test signatures: pytest resolves
    same-scope fixtures left-to-right, so the env var is set before the app
    lifespan reads `settings.seed_demo`. The repo pins `APP_ENV=test` only;
    `SEED_DEMO` defaults to `False` in code and must be opted in here.
    """
    monkeypatch.setenv("SEED_DEMO", "true")
    get_settings.cache_clear()


async def test_orphan_evidence_cross_uploader_bind_rejected_and_own_bind_ok(
    demo_seed_lifespan: None,
    api_client: AsyncClient,
    async_engine: AsyncEngine,
) -> None:
    """audits/v0.1.0 P1 回归：他人上传的孤儿证据不得被绑定（非 ADMIN 上传者过滤）。"""
    _ = demo_seed_lifespan
    admin_hdr = await auth_headers_for(api_client, "admin")
    insp_hdr = await auth_headers_for(api_client, "inspector1")

    pts = (await api_client.get("/api/v1/lighting-points", headers=admin_hdr)).json()["items"]
    point = next(p for p in pts if p["point_code"] == "SL-ZZ-JS-0001")
    form = {"lighting_point_id": str(point["id"]), "scene_lighting_hint": "night"}

    async def _zero_candidate_evidence(hdr: dict[str, str], prefix: str) -> int:
        resp = await api_client.post(
            "/api/v1/tickets/ai-candidates",
            data=form,
            files={"files": ("normal.png", make_night_normal_lit_png(), "image/png")},
            headers={**hdr, "Idempotency-Key": new_idem_key(prefix)},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ticket"] is None
        evidences = body["evidences"]
        assert len(evidences) == 1
        assert evidences[0]["ticket_id"] is None
        return int(evidences[0]["id"])

    # 1) admin 上传的孤儿证据，inspector1（有本区 INSPECT 授权但非上传者）绑定 -> 422
    admin_evidence_id = await _zero_candidate_evidence(admin_hdr, "reg-or-admin")
    create = await api_client.post(
        "/api/v1/tickets",
        json={
            "lighting_point_id": point["id"],
            "fault_type": "LAMP_OUT",
            "title": "regression orphan bind",
            "description": "cross-uploader orphan evidence bind must fail",
            "evidence_ids": [admin_evidence_id],
        },
        headers={**insp_hdr, "Idempotency-Key": new_idem_key("reg-ti")},
    )
    assert create.status_code == 422, create.text
    assert _err(create)["code"] == "VALIDATION_ERROR"
    assert _err(create)["details"]["field"] == "evidence_ids"

    # 2) 同一上传者的孤儿证据仍可正常绑定 -> 201，且 DB 中 ticket_id 已回填
    own_evidence_id = await _zero_candidate_evidence(insp_hdr, "reg-or-own")
    create_own = await api_client.post(
        "/api/v1/tickets",
        json={
            "lighting_point_id": point["id"],
            "fault_type": "OCCLUDED",
            "title": "regression own orphan bind",
            "description": "same-uploader orphan evidence bind must succeed",
            "evidence_ids": [own_evidence_id],
        },
        headers={**insp_hdr, "Idempotency-Key": new_idem_key("reg-ti-own")},
    )
    assert create_own.status_code == 201, create_own.text
    own_ticket_id = int(create_own.json()["id"])

    # 3) 边界断言：ADMIN 不受上传者过滤（修复代码显式保留 admin bypass），
    #    admin 可绑定 inspector1 刚上传的另一条孤儿证据 -> 201
    admin_bound_evidence_id = await _zero_candidate_evidence(insp_hdr, "reg-or-adm-bind")
    create_admin = await api_client.post(
        "/api/v1/tickets",
        json={
            "lighting_point_id": point["id"],
            "fault_type": "FIXTURE_DAMAGED",
            "title": "regression admin orphan bind",
            "description": "admin bypass must still bind cross-uploader orphans",
            "evidence_ids": [admin_bound_evidence_id],
        },
        headers={**admin_hdr, "Idempotency-Key": new_idem_key("reg-ti-adm")},
    )
    assert create_admin.status_code == 201, create_admin.text
    admin_ticket_id = int(create_admin.json()["id"])

    async with AsyncSession(async_engine) as session:
        stolen = await session.scalar(
            select(FaultEvidence).where(FaultEvidence.id == admin_evidence_id)
        )
        own = await session.scalar(select(FaultEvidence).where(FaultEvidence.id == own_evidence_id))
        admin_bound = await session.scalar(
            select(FaultEvidence).where(FaultEvidence.id == admin_bound_evidence_id)
        )
    assert stolen is not None and stolen.ticket_id is None
    assert own is not None and own.ticket_id == own_ticket_id
    assert admin_bound is not None and admin_bound.ticket_id == admin_ticket_id


async def test_production_seed_demo_does_not_create_accounts(
    async_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """生产 + SEED_DEMO=true：lifespan 拒绝 seed_demo_data，accounts 保持 0 行。

    `main.py:lifespan` 对 `seed_demo and app_env == "production"` 只记 error 不调用
    `seed_demo_data`；本测试以真 lifespan + 真 `tomori_test` 验证 accounts/account_roles
    均为空，再在非生产环境验证同一 lifespan 路径确实会创建 4 个演示账号（对照组）。
    """
    from tomori.main import create_app

    async def _run_lifespan(*, app_env: str, seed_demo: bool) -> tuple[int, int]:
        monkeypatch.setenv("APP_ENV", app_env)
        monkeypatch.setenv("SEED_DEMO", "true" if seed_demo else "false")
        monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / f"uploads-{app_env}"))
        get_settings.cache_clear()
        db_session_mod.get_engine.cache_clear()
        db_session_mod.get_async_sessionmaker.cache_clear()
        await reset_to_post_migration_baseline(async_engine)
        try:
            app = create_app()
            async with LifespanManager(app):
                pass
        finally:
            await db_session_mod.get_engine().dispose()
            db_session_mod.get_engine.cache_clear()
            db_session_mod.get_async_sessionmaker.cache_clear()
            get_settings.cache_clear()
        async with AsyncSession(async_engine) as session:
            accounts = int(await session.scalar(select(func.count()).select_from(Account)) or 0)
            account_roles = int(
                await session.scalar(select(func.count()).select_from(AccountRole)) or 0
            )
        return accounts, account_roles

    try:
        # 1) 生产 + SEED_DEMO=true -> 不创建任何演示账号/角色绑定
        accounts, account_roles = await _run_lifespan(app_env="production", seed_demo=True)
        assert accounts == 0
        assert account_roles == 0

        # 2) 对照：SEED_DEMO=false 同样不创建（生产与开发都不应隐式建号）
        accounts_off, account_roles_off = await _run_lifespan(app_env="production", seed_demo=False)
        assert accounts_off == 0
        assert account_roles_off == 0

        # 3) 对照组：非生产 + SEED_DEMO=true -> lifespan 确实创建 4 个演示账号
        #    （证明上两条断言不是「seeding 从来没跑」的假绿）
        accounts_dev, account_roles_dev = await _run_lifespan(app_env="test", seed_demo=True)
        assert accounts_dev == 4
        assert account_roles_dev == 4
    finally:
        await reset_to_post_migration_baseline(async_engine)


async def test_content_length_precheck_rejects_oversized_declared_body(
    demo_seed_lifespan: None,
    api_client: AsyncClient,
) -> None:
    """`Content-Length` 预检：声明总量超限 -> 413，发生在 `request.form()` 解析之前。

    上限 = `max_upload_size_bytes * MAX_FILES_PER_REQUEST + _FORM_OVERHEAD_BYTES`
    = 15 MiB * 10 + 1 MiB。这里直接构造裸 multipart body 并显式设置头部，
    验证声明值（而非真实字节数）即足以触发 413；再验证谎报小 Content-Length
    不能绕过逐文件 `max+1` 截断读取的第二道闸门。
    """
    _ = demo_seed_lifespan
    insp_hdr = await auth_headers_for(api_client, "inspector1")
    admin_hdr = await auth_headers_for(api_client, "admin")
    pts = (await api_client.get("/api/v1/lighting-points", headers=admin_hdr)).json()["items"]
    point = next(p for p in pts if p["point_code"] == "SL-ZZ-JS-0001")

    settings = get_settings()
    declared_limit = settings.max_upload_size_bytes * MAX_FILES_PER_REQUEST + 1024 * 1024
    declared = declared_limit + 1
    body = b'--b\r\nContent-Disposition: form-data; name="x"\r\n\r\n1\r\n--b--\r\n'

    r_big = await api_client.post(
        "/api/v1/tickets/ai-candidates",
        content=body,
        headers={
            **insp_hdr,
            "Content-Type": "multipart/form-data; boundary=b",
            "Content-Length": str(declared),
            "Idempotency-Key": new_idem_key("reg-cl"),
        },
    )
    assert r_big.status_code == 413, r_big.text
    err = _err(r_big)
    assert err["code"] == "IMAGE_SIZE_EXCEEDED"
    assert err["details"]["declared_bytes"] == declared
    assert err["details"]["max_bytes"] == declared_limit

    # 对照：声明值恰在上限 -> 不因预检 413（后续校验照常执行）
    r_edge = await api_client.post(
        "/api/v1/tickets/ai-candidates",
        content=body,
        headers={
            **insp_hdr,
            "Content-Type": "multipart/form-data; boundary=b",
            "Content-Length": str(declared_limit),
            "Idempotency-Key": new_idem_key("reg-cl-edge"),
        },
    )
    assert r_edge.status_code == 422, r_edge.text
    assert _err(r_edge)["code"] == "NO_FILES"

    # 对照：谎报小 Content-Length + 真实单文件超限 -> 逐文件截断闸门仍 413
    over_file = b"\xab" * (settings.max_upload_size_bytes + 1)
    r_lie = await api_client.post(
        "/api/v1/tickets/ai-candidates",
        data={
            "lighting_point_id": str(point["id"]),
            "scene_lighting_hint": "night",
        },
        files=[("files", ("big.bin", over_file, "application/octet-stream"))],
        headers={**insp_hdr, "Content-Length": "1", "Idempotency-Key": new_idem_key("reg-cl-lie")},
    )
    assert r_lie.status_code == 413, r_lie.text
    assert _err(r_lie)["code"] == "IMAGE_SIZE_EXCEEDED"
