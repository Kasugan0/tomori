"""12 端点并发幂等执行引擎（P1-4 定案：pg_try_advisory_xact_lock + audit_logs append-only）。

严格对齐 `docs/DESIGN.md` §3.5 与 `docs/API.md` §6.2–§6.5：
1. 校验 `Idempotency-Key` 缺失 (`400 IDEMPOTENCY_KEY_REQUIRED`) /
   格式 (`400 IDEMPOTENCY_KEY_INVALID`, `^[A-Za-z0-9_-]{8,128}$`)。
2. 计算规范化请求载荷 SHA-256（JSON 按排序键序列化；multipart 按字段+文件摘要计算，
   不因随机 multipart boundary 导致合法重试被误判 payload mismatch）。
3. 事务内快速只读查 `audit_logs` 已提交 2xx 记录 → 未命中则执行非阻塞事务级咨询锁：
   `pg_try_advisory_xact_lock(hashtextextended(actor_account_id::text || ':' || method
   || ':' || path || ':' || idempotency_key, 0))`。
4. 获锁失败立即返回 `409 IDEMPOTENCY_REQUEST_IN_PROGRESS`（不写占位行、不阻塞等待）。
5. 获锁成功后在锁内二次读取 `audit_logs`（消解 TOCTOU 竞态）；仍未命中才执行业务写操作，
   且仅在返回 2xx 时同事务向 `audit_logs` 写入唯一一行（严格 append-only：无 UPDATE）。
6. 重放命中时原样返回原状态码与响应体，并附带 `X-Idempotent-Replay: true`
   （注意：该响应头为**本项目自定义扩展，非 IETF 草案内容**，不可写成标准）。
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from tomori.models.account import Account
from tomori.models.audit_log import AuditLog
from tomori.services.exceptions import (
    IDEMPOTENCY_KEY_INVALID,
    IDEMPOTENCY_KEY_REQUIRED,
    IDEMPOTENCY_PAYLOAD_MISMATCH,
    IDEMPOTENCY_REQUEST_IN_PROGRESS,
    AppError,
)

_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,128}$")


@dataclass
class IdempotentOutcome:
    """业务回调执行结果，用于同一事务内一次性组装并 append-only 插入 AuditLog。"""

    status_code: int
    body: dict[str, Any]
    action: str
    resource_type: str
    resource_id: int | None = None
    ticket_id: int | None = None
    business_snapshot_json: dict[str, Any] | None = None
    sanitized_request_payload: dict[str, Any] | None = None
    location: str | None = None
    extra_audits: list[AuditLog] = field(default_factory=list)


def validate_idempotency_key(raw_key: str | None) -> str:
    """校验 `Idempotency-Key` 头存在且符合 `^[A-Za-z0-9_-]{8,128}$`。"""
    if raw_key is None or raw_key.strip() == "":
        raise AppError(IDEMPOTENCY_KEY_REQUIRED, "该写操作必须携带 Idempotency-Key 请求头")
    key = raw_key.strip()
    if not _KEY_PATTERN.fullmatch(key):
        raise AppError(
            IDEMPOTENCY_KEY_INVALID,
            "Idempotency-Key 格式不合法（仅允许 8..128 位字母、数字、下划线或连字符）",
            {"pattern": "^[A-Za-z0-9_-]{8,128}$"},
        )
    return key


def hash_json_payload(payload: Any) -> str:
    """计算结构化请求体规范化 SHA-256（64 位小写十六进制）。"""
    canonical = json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=False,
        default=str,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def hash_multipart_payload(
    *,
    fields: dict[str, Any],
    file_digests: list[tuple[str, str, int]],
) -> str:
    """计算 multipart/form-data 的稳定规范化 SHA-256（脱离随机 HTTP boundary）。

    `file_digests` 元素为 `(filename, sha256_hex, size_bytes)`。
    """
    return hash_json_payload(
        {
            "fields": fields,
            "files": [
                {"filename": fn, "sha256": digest, "size_bytes": sz}
                for fn, digest, sz in file_digests
            ],
        }
    )


async def _find_completed_2xx_record(
    session: AsyncSession,
    *,
    actor_account_id: int,
    method: str,
    path: str,
    idempotency_key: str,
) -> AuditLog | None:
    stmt = select(AuditLog).where(
        AuditLog.actor_account_id == actor_account_id,
        AuditLog.method == method,
        AuditLog.path == path,
        AuditLog.idempotency_key == idempotency_key,
        AuditLog.response_status >= 200,
        AuditLog.response_status < 300,
    )
    return (await session.scalars(stmt)).first()


def _check_or_replay(
    existing: AuditLog,
    *,
    current_hash: str,
    request_id: str,
) -> JSONResponse:
    if existing.request_payload_hash != current_hash:
        raise AppError(
            IDEMPOTENCY_PAYLOAD_MISMATCH,
            "相同 Idempotency-Key 不得复用于不同请求载荷",
            {
                "original_payload_hash": existing.request_payload_hash,
                "current_payload_hash": current_hash,
            },
        )
    # 注意：X-Idempotent-Replay 是本项目自定义扩展响应头，非 IETF 草案标准
    return JSONResponse(
        status_code=existing.response_status,
        content=existing.response_body_json or {},
        headers={
            "X-Idempotent-Replay": "true",
            "X-Request-Id": request_id,
        },
    )


async def run_idempotent(
    session: AsyncSession,
    *,
    actor: Account,
    actor_role_code: str,
    method: str,
    path: str,
    idempotency_key: str | None,
    payload_hash: str,
    request_id: str,
    client_ip: str | None,
    user_agent: str | None,
    handler: Callable[[AsyncSession], Awaitable[IdempotentOutcome]],
) -> JSONResponse:
    """在单事务内完成：键校验 → 快速查重 → 事务咨询锁 → 锁内二次查重 → 业务执行 → append-only 提交。

    无论 FastAPI 依赖注入期间是否已因前置 SELECT 触发 SQLAlchemy autobegin，
    都在同一事务末尾显式 commit()（成功）或 rollback()（异常/4xx/5xx），
    绝不依赖 AsyncSession 上下文退出时的隐式回滚。
    """
    key = validate_idempotency_key(idempotency_key)
    norm_method = method.upper()
    norm_path = path[:256]

    try:
        # 1) 第一次无锁快速只读查询（热重放命中免争锁）
        existing = await _find_completed_2xx_record(
            session,
            actor_account_id=actor.id,
            method=norm_method,
            path=norm_path,
            idempotency_key=key,
        )
        if existing is not None:
            resp = _check_or_replay(existing, current_hash=payload_hash, request_id=request_id)
            await session.commit()
            return resp

        # 2) 非阻塞事务级咨询锁抢占（Lead P1-4 裁决口径）
        lock_sql = text(
            "SELECT pg_try_advisory_xact_lock("
            "hashtextextended("
            "CAST(:actor_id AS text) || ':' || :method || ':' || :path || ':' || :ikey, 0"
            ")"
            ")"
        )
        acquired = await session.scalar(
            lock_sql,
            {
                "actor_id": actor.id,
                "method": norm_method,
                "path": norm_path,
                "ikey": key,
            },
        )
        if not acquired:
            raise AppError(
                IDEMPOTENCY_REQUEST_IN_PROGRESS,
                "相同幂等键的请求正在处理中，请稍后重试",
            )

        # 3) 锁内二次读取 audit_logs（消除 TOCTOU 竞态）
        existing_after_lock = await _find_completed_2xx_record(
            session,
            actor_account_id=actor.id,
            method=norm_method,
            path=norm_path,
            idempotency_key=key,
        )
        if existing_after_lock is not None:
            resp = _check_or_replay(
                existing_after_lock, current_hash=payload_hash, request_id=request_id
            )
            await session.commit()
            return resp

        # 4) 执行业务写操作（若抛异常，catch 分支回滚事务，自动释放咨询锁且不占用幂等键）
        outcome = await handler(session)

        # 5) 严格 append-only：仅 2xx 写入 1 行带幂等键的审计行（不写占位行，无 UPDATE）
        if 200 <= outcome.status_code < 300:
            try:
                req_uuid = uuid.UUID(request_id)
            except ValueError:
                req_uuid = uuid.uuid4()

            audit_row = AuditLog(
                request_id=req_uuid,
                actor_account_id=actor.id,
                actor_username=actor.username,
                actor_role_code=actor_role_code,
                action=outcome.action,
                resource_type=outcome.resource_type,
                resource_id=outcome.resource_id,
                ticket_id=outcome.ticket_id,
                method=norm_method,
                path=norm_path,
                request_payload=outcome.sanitized_request_payload,
                request_payload_hash=payload_hash,
                idempotency_key=key,
                response_status=outcome.status_code,
                error_code=None,
                business_snapshot_json=outcome.business_snapshot_json,
                response_body_json=outcome.body,
                client_ip=client_ip,
                user_agent=user_agent[:256] if user_agent else None,
            )
            session.add(audit_row)
            for extra in outcome.extra_audits:
                extra.request_id = req_uuid
                extra.actor_account_id = actor.id
                extra.actor_username = actor.username
                extra.actor_role_code = actor_role_code
                extra.method = norm_method
                extra.path = norm_path
                extra.idempotency_key = None  # 附带语义审计行不占幂等唯一索引
                extra.response_status = outcome.status_code
                extra.client_ip = client_ip
                extra.user_agent = user_agent[:256] if user_agent else None
                session.add(extra)
            await session.flush()

        await session.commit()

        headers: dict[str, str] = {"X-Request-Id": request_id}
        if outcome.location:
            headers["Location"] = outcome.location
        return JSONResponse(
            status_code=outcome.status_code,
            content=outcome.body,
            headers=headers,
        )
    except Exception:
        await session.rollback()
        raise
