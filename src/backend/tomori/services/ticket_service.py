"""故障单全生命周期编排：12 步 AI 候选上传、人工报障、草稿编辑/确认/撤销/改级、详情组装。

严格遵循 `docs/API.md` §2.3 / §4 / §5 / §5.2 与 `docs/DESIGN.md` §3.3 / §5.1 / §5.2：
- **D1 视觉隔离**：模块顶层绝不导入 `tomori.vision`，只在 `_run_vision_on_file` 内延迟导入，
  保证 `POST /tickets` 人工报障在视觉包缺失、权重未就绪或开关关闭时仍返回 `201`。
- **D2 失败原子清理**：媒体一旦落盘，任何后续失败（视觉降级、建单冲突、关联校验）都必须
  `cleanup_files` 删除已写盘的原图与标注图，绝不留下孤儿文件。
- **D-30 零故障候选**：仅有 `NORMAL_LIT` / `DAYTIME_UNPOWERED_EXPECTED` 等非故障候选时
  不建票、不锁点位，只落 `fault_evidences(ticket_id=NULL)` 并返回 `{ticket: null, ...}`。
- **`uq_fault_tickets_active_point_type`**：应用层先查活跃单；并发竞争由 PG 部分唯一索引兜底，
  且必须精确比对 `constraint_name` 才翻译成 `409 DUPLICATE_ACTIVE_TICKET`，
  防止把撞号等真缺陷伪装成业务冲突。
- 状态字段一律经由 `state_machine` 修改，本模块永不直接赋值 `.status`。
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import null, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tomori.db.types import ensure_utc
from tomori.models.acceptance_record import AcceptanceRecord
from tomori.models.account import Account
from tomori.models.audit_log import AuditLog
from tomori.models.dispatch_record import DispatchRecord
from tomori.models.facility_asset import FacilityAsset
from tomori.models.fault_evidence import FaultEvidence
from tomori.models.fault_ticket import FaultTicket
from tomori.models.lighting_point import LightingPoint
from tomori.models.maintenance_region import MaintenanceRegion
from tomori.models.material_usage import MaterialUsage
from tomori.models.repair_process import RepairProcess
from tomori.models.rework_order import ReworkOrder
from tomori.schemas.dispatch import DispatchRecordOut, MaterialUsageOut, RepairProcessOut
from tomori.schemas.operations import AcceptanceRecordOut, ReworkOrderOut
from tomori.schemas.ticket import (
    AiCandidateEvidenceOut,
    CandidateDecisionIn,
    FaultCandidateResultOut,
    FaultEvidenceOut,
    FaultTicketDetailOut,
    FaultTicketOut,
    TicketCancelIn,
    TicketConfirmIn,
    TicketRegradeIn,
    TicketUpdateDraftIn,
    TimelineEntryOut,
)
from tomori.services.auth import get_authorized_region_ids, has_valid_region_scope
from tomori.services.exceptions import (
    DUPLICATE_ACTIVE_TICKET,
    FORBIDDEN_REGION_SCOPE,
    FORBIDDEN_ROLE,
    INVALID_STATE_TRANSITION,
    RESOURCE_NOT_FOUND,
    VALIDATION_ERROR,
    VISION_DISABLED,
    AppError,
)
from tomori.services.grading import evaluate_ticket_grading
from tomori.services.media import (
    ValidatedMediaFile,
    cleanup_files,
    resolve_evidence_paths,
    save_upload_file,
    validate_and_normalize_image,
    validate_upload_count,
)
from tomori.services.sla import get_ticket_deadlines
from tomori.services.state_machine import (
    build_audit_entry,
    create_ticket_record,
    list_region_admin_ids,
    transition_ticket,
)

__all__ = [
    "analyze_ai_candidates_flow",
    "build_ai_evidence_out",
    "build_ticket_detail",
    "cancel_ticket_flow",
    "confirm_ticket_flow",
    "create_manual_ticket_flow",
    "evidence_to_out",
    "next_ticket_no",
    "patch_ticket_draft_flow",
    "regrade_ticket_flow",
    "ticket_to_out",
    "upload_ticket_evidences_flow",
]

_SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")

# 视觉侧永不产出的故障类型（V3）：人工改判时必须带 manual_note，并脱离 AI_CANDIDATE 来源。
_MANUAL_NOTE_TYPES: frozenset[str] = frozenset({"FLICKERING", "ELECTRIC_HAZARD"})

# 活跃单部分唯一索引名（`fault_tickets`），并发冲突翻译的唯一判据。
_ACTIVE_TICKET_CONSTRAINT = "uq_fault_tickets_active_point_type"

_FAULT_TYPES_WITH_TICKET: frozenset[str] = frozenset(
    {"LAMP_OUT", "FIXTURE_DAMAGED", "OCCLUDED", "OTHER"}
)

# 终态工单不可复活（docs/DESIGN.md §2.1 Machine A 唯二终态）；按 docs/API.md §2.3 契约，
# 非终态各 `evidence_type`（INSPECTION_PHOTO / ANNOTATED_PHOTO / REPAIR_PHOTO / ACCEPTANCE_PHOTO）
# 的补传窗口保留不变，仅终态统一拒绝。
_TERMINAL_TICKET_STATUSES: frozenset[str] = frozenset({"CLOSED", "CANCELLED"})

_EVIDENCE_TYPES: frozenset[str] = frozenset(
    {"INSPECTION_PHOTO", "ANNOTATED_PHOTO", "REPAIR_PHOTO", "ACCEPTANCE_PHOTO"}
)


# ──────────────────────────────────────────────────────────────────────
# 单号与序列化
# ──────────────────────────────────────────────────────────────────────


async def next_ticket_no(session: AsyncSession, *, now: datetime | None = None) -> str:
    """生成 `FT-YYYYMMDD-NNNN`（上海时区日期 + 当日已用最大序号 + 1）。

    以「当日最大序号」而非 `count()` 递推：撤销或删除过的单号不会被重复占用。
    并发撞号最终由 `uq_fault_tickets_ticket_no` 兜底暴露，而非静默复用。
    """
    day_str = ensure_utc(now).astimezone(_SHANGHAI_TZ).strftime("%Y%m%d")
    prefix = f"FT-{day_str}-"
    used: set[int] = set()
    rows = await session.scalars(
        select(FaultTicket.ticket_no).where(FaultTicket.ticket_no.like(f"{prefix}%"))
    )
    for ticket_no in rows:
        suffix = ticket_no.rsplit("-", 1)[-1]
        if suffix.isdigit():
            used.add(int(suffix))
    return f"{prefix}{max(used, default=0) + 1:04d}"


def ticket_to_out(
    ticket: FaultTicket,
    *,
    region_name: str | None = None,
    point_code: str | None = None,
    point_name: str | None = None,
) -> FaultTicketOut:
    """把 ORM 工单转换为响应模型（可选带区域/点位展示名）。"""
    return FaultTicketOut(
        id=ticket.id,
        ticket_no=ticket.ticket_no,
        region_id=ticket.region_id,
        region_name=region_name,
        lighting_point_id=ticket.lighting_point_id,
        point_code=point_code,
        point_name=point_name,
        facility_asset_id=ticket.facility_asset_id,
        report_source=ticket.report_source,  # type: ignore[arg-type]
        fault_type=ticket.fault_type,  # type: ignore[arg-type]
        title=ticket.title,
        description=ticket.description,
        status=ticket.status,  # type: ignore[arg-type]
        priority=ticket.priority,  # type: ignore[arg-type]
        grading_score=ticket.grading_score,
        grading_snapshot_json=ticket.grading_snapshot_json,
        reporter_account_id=ticket.reporter_account_id,
        confirmed_by_inspector=ticket.confirmed_by_inspector,
        confirmed_by_inspector_id=ticket.confirmed_by_inspector_id,
        confirmed_at=ticket.confirmed_at,
        ai_suggestion_json=ticket.ai_suggestion_json,
        ai_max_confidence=ticket.ai_max_confidence,
        response_deadline_at=ticket.response_deadline_at,
        repair_deadline_at=ticket.repair_deadline_at,
        is_sla_overdue=ticket.is_sla_overdue,
        overdue_stage=ticket.overdue_stage,  # type: ignore[arg-type]
        overdue_marked_at=ticket.overdue_marked_at,
        rework_count=ticket.rework_count,
        dispatch_reject_count=ticket.dispatch_reject_count,
        manual_review_reason=ticket.manual_review_reason,
        cancel_reason=ticket.cancel_reason,
        cancelled_by_account_id=ticket.cancelled_by_account_id,
        cancelled_at=ticket.cancelled_at,
        closed_at=ticket.closed_at,
        created_at=ticket.created_at,
        updated_at=ticket.updated_at,
    )


# ──────────────────────────────────────────────────────────────────────
# 证据序列化（禁止泄露本地文件系统路径）
# ──────────────────────────────────────────────────────────────────────


def _annotated_path(ev: FaultEvidence, upload_dir: Path) -> Path | None:
    """解析标注图路径；无 analysis_id、越界或不存在时返回 `None`。"""
    if ev.analysis_id is None:
        return None
    try:
        _, annotated = resolve_evidence_paths(ev.storage_path, ev.analysis_id, upload_dir)
    except Exception:
        return None
    return annotated if annotated is not None and annotated.is_file() else None


def _sanitized_ai_result(
    ev: FaultEvidence,
    *,
    raw_url: str,
    annotated_url: str | None,
) -> dict[str, Any] | None:
    """把 `ai_result_json` 内的本地路径替换为 API 相对 URL，并补 `evidence_id`。"""
    if ev.ai_result_json is None:
        return None
    return {
        **ev.ai_result_json,
        "evidence_id": ev.id,
        "original_image_url": raw_url,
        "annotated_image_url": annotated_url,
    }


def evidence_to_out(ev: FaultEvidence, upload_dir: Path) -> FaultEvidenceOut:
    """序列化单条证据，并注入 `raw_url` / `annotated_url`（无标注图时为 `None`）。"""
    raw_url = f"/api/v1/evidence/{ev.id}/raw"
    annotated_url = (
        f"/api/v1/evidence/{ev.id}/annotated"
        if _annotated_path(ev, upload_dir) is not None
        else None
    )
    sanitized = _sanitized_ai_result(ev, raw_url=raw_url, annotated_url=annotated_url)
    return FaultEvidenceOut(
        id=ev.id,
        ticket_id=ev.ticket_id,
        analysis_id=ev.analysis_id,
        evidence_type=ev.evidence_type,  # type: ignore[arg-type]
        media_mime=ev.media_mime,
        storage_path=ev.storage_path,
        file_size_bytes=ev.file_size_bytes,
        sha256=ev.sha256,
        image_width=ev.image_width,
        image_height=ev.image_height,
        ai_result_json=(
            FaultCandidateResultOut.model_validate(sanitized) if sanitized is not None else None
        ),
        ai_is_baseline_mode=ev.ai_is_baseline_mode,
        uploaded_by_account_id=ev.uploaded_by_account_id,
        url=raw_url,
        raw_url=raw_url,
        annotated_url=annotated_url,
        captured_at=ev.captured_at,
        created_at=ev.created_at,
    )


def build_ai_evidence_out(ev: FaultEvidence, upload_dir: Path) -> AiCandidateEvidenceOut:
    """在 `evidence_to_out` 之上摊平 `ai_result_json` 摘要字段，供巡查台直接消费。"""
    base = evidence_to_out(ev, upload_dir)
    ai = base.ai_result_json
    if ai is None:
        return AiCandidateEvidenceOut(**base.model_dump())
    return AiCandidateEvidenceOut(
        **base.model_dump(),
        evidence_id=ev.id,
        inference_mode=ai.inference_mode,
        model_version=ai.model_version,
        original_image_url=ai.original_image_url,
        annotated_image_url=ai.annotated_image_url,
        image_quality=ai.image_quality,
        primary_fault_type_suggestion=ai.primary_fault_type_suggestion,
        max_fused_confidence=ai.max_fused_confidence,
        candidates=ai.candidates,
        requires_inspector_confirmation=ai.requires_inspector_confirmation,
        replaces_electrical_safety_check=ai.replaces_electrical_safety_check,
        disclaimer=ai.disclaimer,
    )


# ──────────────────────────────────────────────────────────────────────
# 点位活跃单防重
# ──────────────────────────────────────────────────────────────────────


async def _find_active_duplicate_ticket(
    session: AsyncSession,
    *,
    lighting_point_id: int,
    fault_type: str,
) -> FaultTicket | None:
    return (
        await session.scalars(
            select(FaultTicket).where(
                FaultTicket.lighting_point_id == lighting_point_id,
                FaultTicket.fault_type == fault_type,
                FaultTicket.status.notin_(("CLOSED", "CANCELLED")),
            )
        )
    ).first()


def _duplicate_error(existing: FaultTicket) -> AppError:
    """`409` 三字段 details 被前端用于渲染「查看既有工单」链接，属冻结契约。"""
    return AppError(
        DUPLICATE_ACTIVE_TICKET,
        "该照明点位已存在同类型的活跃故障单",
        {
            "existing_ticket_id": existing.id,
            "existing_ticket_no": existing.ticket_no,
            "existing_status": existing.status,
        },
    )


def _is_active_duplicate_violation(exc: IntegrityError) -> bool:
    """仅当 23505 来自活跃单部分唯一索引时才算业务冲突（撞号等真缺陷必须继续上抛）。"""
    return getattr(getattr(exc.orig, "diag", None), "constraint_name", None) == (
        _ACTIVE_TICKET_CONSTRAINT
    )


# ──────────────────────────────────────────────────────────────────────
# 媒体批处理（第 2–8 步）
# ──────────────────────────────────────────────────────────────────────


def _validate_media_batch(
    raw_files: list[tuple[bytes, str | None, str | None]],
    *,
    min_count: int,
    max_upload_bytes: int,
) -> list[ValidatedMediaFile]:
    """数量 + 逐图 4 层白名单/EXIF 归一化，全部通过才返回（落盘在调用方之后执行）。

    同批次内内容完全相同的图片会被显式拒绝：`uq_fault_evidences_sha256_per_ticket`
    不允许同一工单重复挂同一张图，静默丢弃会让「每张图一行证据」变成不可验证的承诺。
    """
    validate_upload_count(len(raw_files), min_count=min_count, max_count=10)
    validated: list[ValidatedMediaFile] = []
    seen: set[str] = set()
    for idx, (raw, filename, content_type) in enumerate(raw_files):
        media = validate_and_normalize_image(
            raw,
            filename=filename,
            content_type=content_type,
            file_index=idx,
            max_bytes=max_upload_bytes,
        )
        if media.sha256 in seen:
            raise AppError(
                VALIDATION_ERROR,
                f"第 {idx + 1} 个文件与同批次其他文件内容完全相同",
                {"file_index": idx, "sha256": media.sha256, "reason": "DUPLICATE_EVIDENCE"},
            )
        seen.add(media.sha256)
        validated.append(media)
    return validated


def _save_batch(
    validated: list[ValidatedMediaFile],
    *,
    upload_dir: Path,
    now: datetime,
    saved_paths: list[Path],
) -> list[str]:
    """把已校验图片写入磁盘，返回相对存储路径；绝对路径累积进 `saved_paths` 供失败回滚。"""
    rel_paths: list[str] = []
    for media in validated:
        rel_path, full_path = save_upload_file(media, upload_dir, now=now)
        saved_paths.append(full_path)
        rel_paths.append(rel_path)
    return rel_paths


def _persist_evidences(
    session: AsyncSession,
    validated: list[ValidatedMediaFile],
    rel_paths: list[str],
    *,
    ticket_id: int | None,
    evidence_type: str,
    actor_id: int,
    now: datetime,
    ai_results: list[dict[str, Any]] | None = None,
) -> list[FaultEvidence]:
    """以已落盘文件构造证据行（不再重复写盘）。

    `ai_result_json` 为空时必须写 SQL `NULL` 而非 JSON 字面量 `'null'`：
    `ck_fault_evidences_ai_safety_flags_required` 的 `ai_result_json IS NULL` 分支
    只认 SQL `NULL`，而 SQLAlchemy 对 `JSONB` 列绑定 Python `None` 默认渲染成 `'null'::jsonb`。
    """
    evidences: list[FaultEvidence] = []
    for idx, (media, rel_path) in enumerate(zip(validated, rel_paths, strict=True)):
        ai_payload = ai_results[idx] if ai_results is not None else None
        evidence = FaultEvidence(
            ticket_id=ticket_id,
            analysis_id=uuid.UUID(ai_payload["analysis_id"]) if ai_payload else None,
            evidence_type=evidence_type,
            media_mime=media.mime,
            storage_path=rel_path,
            file_size_bytes=media.size_bytes,
            sha256=media.sha256,
            image_width=media.width,
            image_height=media.height,
            ai_result_json=ai_payload["result"] if ai_payload else null(),
            ai_is_baseline_mode=bool(ai_payload["baseline"]) if ai_payload else False,
            uploaded_by_account_id=actor_id,
            captured_at=now,
        )
        session.add(evidence)
        evidences.append(evidence)
    return evidences


# ──────────────────────────────────────────────────────────────────────
# 第 9 步：视觉推理（唯一允许触达 tomori.vision 的边界）
# ──────────────────────────────────────────────────────────────────────


async def _run_vision_on_file(
    full_path: Path,
    *,
    upload_dir: Path,
    original_image_url: str,
    scene_lighting_hint: str,
) -> tuple[dict[str, Any], str, bool, Path | None]:
    """函数内延迟导入视觉包并在工作线程推理（`asyncio.to_thread` 不阻塞事件循环）。

    视觉包整体不可导入（未装 `--extra vision` 或依赖损坏）归 `503 VISION_DISABLED`：
    按 `docs/DESIGN.md` §5.2，特性不可用只有三条互斥 `503`，且必须带
    `analysis_stage=CONFIG_CHECK` 与 `details.fallback`（D3），绝不裸抛 `ImportError` 成 `500`。
    此时 `tomori.vision.errors` 亦不可导入，故用纯领域 `AppError` 承载同一码值。
    """
    try:
        from tomori.vision.client import analyze_image
        from tomori.vision.errors import VISION_INFERENCE_FAILED, VisionError, make_degraded_error
    except ImportError as exc:
        raise AppError(
            VISION_DISABLED,
            f"视觉推理组件不可用: {exc}",
            {
                "analysis_stage": "CONFIG_CHECK",
                "fallback": {"action": "MANUAL_REPORT", "resubmit_required": True},
            },
            message_key="vision.disabled.manual_fallback",
        ) from exc

    try:
        result = await asyncio.to_thread(
            analyze_image,
            full_path,
            original_image_url=original_image_url,
            scene_lighting_hint=scene_lighting_hint,
            save_annotated=True,
        )
    except VisionError:
        raise
    except Exception as exc:
        raise make_degraded_error(
            VISION_INFERENCE_FAILED,
            f"视觉分析执行失败: {exc}",
            analysis_stage="INFERENCE",
        ) from exc

    payload = result.to_json_dict()
    # 标注图在库内只留 API URL（输出期注入），绝不写入服务器本地绝对路径。
    payload["annotated_image_url"] = None
    candidate = upload_dir / "vision" / f"{result.analysis_id}_annotated.jpg"
    annotated = candidate if candidate.is_file() else None
    return payload, str(result.analysis_id), bool(result.ai_is_baseline_mode), annotated


# ──────────────────────────────────────────────────────────────────────
# 入口 1：AI 候选生成（POST /tickets/ai-candidates，12 步）
# ──────────────────────────────────────────────────────────────────────


async def _resolve_target_region(
    session: AsyncSession,
    *,
    lighting_point_id: int | None,
    region_id: int | None,
    actor: Account,
    has_inspect_scope_fn: Callable[[int], Awaitable[None]],
) -> tuple[int, LightingPoint | None]:
    """解析目标区域并完成区域 `INSPECT` 授权校验（第 1 步）。"""
    if lighting_point_id is not None:
        point = await session.scalar(
            select(LightingPoint).where(LightingPoint.id == lighting_point_id)
        )
        if point is None:
            raise AppError(
                RESOURCE_NOT_FOUND,
                "照明点位不存在",
                {"resource_type": "LIGHTING_POINT", "resource_id": lighting_point_id},
            )
        if region_id is not None and point.region_id != region_id:
            raise AppError(
                VALIDATION_ERROR,
                "传入 region_id 与点位所属区域不一致",
                {"point_region_id": point.region_id, "region_id": region_id},
            )
        await has_inspect_scope_fn(point.region_id)
        return point.region_id, point

    if region_id is not None:
        exists = await session.scalar(
            select(MaintenanceRegion.id).where(MaintenanceRegion.id == region_id)
        )
        if exists is None:
            raise AppError(
                RESOURCE_NOT_FOUND,
                "运维区域不存在",
                {"resource_type": "REGION", "resource_id": region_id},
            )
        await has_inspect_scope_fn(region_id)
        return region_id, None

    authorized = await get_authorized_region_ids(session, actor.id, scope_type="INSPECT")
    if not authorized:
        raise AppError(
            FORBIDDEN_REGION_SCOPE,
            "您尚未获得任何区域的巡查授权",
            {"required_scope": "INSPECT"},
        )
    target = sorted(authorized)[0]
    await has_inspect_scope_fn(target)
    return target, None


def _pick_primary_candidate(results: list[dict[str, Any]]) -> dict[str, Any] | None:
    """取全批 `is_fault_candidate=true` 中 `fused_confidence` 最高的候选。"""
    fault_candidates: list[dict[str, Any]] = [
        candidate
        for payload in results
        for candidate in payload.get("candidates") or []
        if candidate.get("is_fault_candidate") is True
        and candidate.get("candidate_fault_type") in _FAULT_TYPES_WITH_TICKET
    ]
    if not fault_candidates:
        return None
    return max(fault_candidates, key=lambda item: float(item.get("fused_confidence") or 0.0))


async def analyze_ai_candidates_flow(
    session: AsyncSession,
    *,
    raw_files: list[tuple[bytes, str | None, str | None]],
    lighting_point_id: int | None,
    facility_asset_id: int | None,
    region_id: int | None,
    scene_lighting_hint: str,
    actor: Account,
    actor_role_code: str,
    has_inspect_scope_fn: Callable[[int], Awaitable[None]],
    upload_dir: Path,
    max_upload_bytes: int,
) -> tuple[FaultTicket | None, list[FaultEvidence], list[AuditLog]]:
    """执行 12 步静态巡查识别：零候选留证不建票，否则建 `AI_CANDIDATE_PENDING` 草稿单。"""
    now = ensure_utc()

    # 1. 区域/点位解析 + INSPECT 授权
    target_region_id, point = await _resolve_target_region(
        session,
        lighting_point_id=lighting_point_id,
        region_id=region_id,
        actor=actor,
        has_inspect_scope_fn=has_inspect_scope_fn,
    )

    # 可选设施资产：与人工建单同构校验（存在 → 属目标点位；未给点位时以显式资产反解点位，
    # 仍须落在已授权目标区域内——不自动猜测点位资产）
    if facility_asset_id is not None:
        asset = await session.scalar(
            select(FacilityAsset).where(FacilityAsset.id == facility_asset_id)
        )
        if asset is None:
            raise AppError(
                RESOURCE_NOT_FOUND,
                "设施资产不存在",
                {"resource_type": "FACILITY_ASSET", "resource_id": facility_asset_id},
            )
        if point is not None:
            if asset.lighting_point_id != point.id:
                raise AppError(
                    VALIDATION_ERROR,
                    "设施资产不属于指定照明点位",
                    {"facility_asset_id": asset.id, "lighting_point_id": point.id},
                )
        else:
            asset_point = await session.scalar(
                select(LightingPoint).where(LightingPoint.id == asset.lighting_point_id)
            )
            if asset_point is None or asset_point.region_id != target_region_id:
                raise AppError(
                    VALIDATION_ERROR,
                    "设施资产不属于目标运维区域",
                    {"facility_asset_id": asset.id, "region_id": target_region_id},
                )
            point = asset_point

    # 2–7. 数量 + 4 层白名单 + EXIF 归一化（全部通过才进入写盘）
    validated = _validate_media_batch(raw_files, min_count=1, max_upload_bytes=max_upload_bytes)

    # 8–9. 落盘 + 逐图视觉推理（D2：任一步失败删除全部已写文件）
    saved_paths: list[Path] = []
    try:
        rel_paths = _save_batch(validated, upload_dir=upload_dir, now=now, saved_paths=saved_paths)

        results: list[dict[str, Any]] = []
        for rel_path, original in zip(rel_paths, raw_files, strict=True):
            payload, analysis_id, baseline, annotated = await _run_vision_on_file(
                upload_dir / rel_path,
                upload_dir=upload_dir,
                original_image_url=original[1] or Path(rel_path).name,
                scene_lighting_hint=scene_lighting_hint,
            )
            if annotated is not None:
                saved_paths.append(annotated)
            results.append(
                {
                    "result": payload,
                    "analysis_id": analysis_id,
                    "baseline": baseline,
                }
            )

        primary = _pick_primary_candidate([entry["result"] for entry in results])

        # 10. 零故障候选（含 D-30 NORMAL_LIT / DAYTIME_UNPOWERED_EXPECTED）：留证不建票
        if primary is None:
            evidences = _persist_evidences(
                session,
                validated,
                rel_paths,
                ticket_id=None,
                evidence_type="INSPECTION_PHOTO",
                actor_id=actor.id,
                now=now,
                ai_results=results,
            )
            await session.flush()
            audit = build_audit_entry(
                action="AI_CANDIDATE_GENERATED",
                resource_type="FAULT_EVIDENCE",
                resource_id=None,
                ticket_id=None,
                actor_account_id=actor.id,
                actor_username=actor.username,
                actor_role_code=actor_role_code,
                snapshot={"zero_candidate": True, "evidence_count": len(evidences)},
            )
            return None, evidences, [audit]

        # 11. 至少一个故障候选：定位点位 → 查重 → 建草稿单 → 逐图建证据
        if point is None:
            point = await session.scalar(
                select(LightingPoint)
                .where(
                    LightingPoint.region_id == target_region_id,
                    LightingPoint.status == "IN_SERVICE",
                )
                .order_by(LightingPoint.id.asc())
            )
            if point is None:
                raise AppError(
                    VALIDATION_ERROR,
                    "当前区域没有在役照明点位，请显式传入 lighting_point_id",
                    {"region_id": target_region_id},
                )

        fault_type = str(primary["candidate_fault_type"])
        max_confidence = Decimal(str(round(float(primary.get("fused_confidence") or 0.0), 3)))

        existing = await _find_active_duplicate_ticket(
            session, lighting_point_id=point.id, fault_type=fault_type
        )
        if existing is not None:
            raise _duplicate_error(existing)

        try:
            async with session.begin_nested():
                ticket, audits = await create_ticket_record(
                    session,
                    ticket_no=await next_ticket_no(session, now=now),
                    region_id=point.region_id,
                    lighting_point_id=point.id,
                    facility_asset_id=facility_asset_id,
                    report_source="AI_CANDIDATE",
                    fault_type=fault_type,
                    title=f"[AI候选] {point.name} {fault_type}"[:128],
                    description=primary.get("diagnostic_summary"),
                    reporter_account_id=actor.id,
                    initial_status="AI_CANDIDATE_PENDING",
                    priority=None,
                    grading_snapshot_json=None,
                    confirmed_by_inspector=False,
                    confirmed_by_inspector_id=None,
                    confirmed_at=None,
                    ai_suggestion_json={
                        "primary_fault_type": fault_type,
                        "max_fused_confidence": float(max_confidence),
                        "requires_inspector_confirmation": True,
                    },
                    ai_max_confidence=max_confidence,
                    response_deadline_at=None,
                    repair_deadline_at=None,
                    actor_username=actor.username,
                    actor_role_code=actor_role_code,
                    action="AI_CANDIDATE_GENERATED",
                    notify=[("AI_CANDIDATE_CREATED", [actor.id])],
                    persist_audit=False,
                )
        except IntegrityError as exc:
            if not _is_active_duplicate_violation(exc):
                raise
            conflict = await _find_active_duplicate_ticket(
                session, lighting_point_id=point.id, fault_type=fault_type
            )
            if conflict is None:
                raise
            raise _duplicate_error(conflict) from exc

        evidences = _persist_evidences(
            session,
            validated,
            rel_paths,
            ticket_id=ticket.id,
            evidence_type="INSPECTION_PHOTO",
            actor_id=actor.id,
            now=now,
            ai_results=results,
        )
        await session.flush()
        return ticket, evidences, audits
    except Exception:
        cleanup_files(saved_paths)
        raise


# ──────────────────────────────────────────────────────────────────────
# 入口 2：人工报障（POST /tickets，D1 永不依赖视觉）
# ──────────────────────────────────────────────────────────────────────


async def create_manual_ticket_flow(
    session: AsyncSession,
    *,
    lighting_point_id: int,
    facility_asset_id: int | None,
    fault_type: str,
    title: str,
    description: str | None,
    safety_flag: bool,
    manual_note: str | None,
    evidence_ids: list[int],
    candidate_decisions: list[CandidateDecisionIn] | None,
    raw_files: list[tuple[bytes, str | None, str | None]],
    actor: Account,
    actor_role_code: str,
    upload_dir: Path,
    max_upload_bytes: int,
    now: datetime | None = None,
) -> tuple[FaultTicket, list[AuditLog]]:
    """纯人工报障：直接 `PENDING_DISPATCH` + 分级 + 48h 暂不写入的响应/修复截止时间。

    本函数绝不导入 `tomori.vision`（D1），视觉不可用时仍返回 `201`。
    """
    ts = ensure_utc(now)
    point = await session.scalar(select(LightingPoint).where(LightingPoint.id == lighting_point_id))
    if point is None:
        raise AppError(
            RESOURCE_NOT_FOUND,
            "照明点位不存在",
            {"resource_type": "LIGHTING_POINT", "resource_id": lighting_point_id},
        )

    asset_importance: str | None = None
    if facility_asset_id is not None:
        asset = await session.scalar(
            select(FacilityAsset).where(FacilityAsset.id == facility_asset_id)
        )
        if asset is None:
            raise AppError(
                RESOURCE_NOT_FOUND,
                "设施资产不存在",
                {"resource_type": "FACILITY_ASSET", "resource_id": facility_asset_id},
            )
        if asset.lighting_point_id != point.id:
            raise AppError(
                VALIDATION_ERROR,
                "设施资产不属于指定照明点位",
                {"facility_asset_id": asset.id, "lighting_point_id": point.id},
            )
        asset_importance = asset.importance

    validated = _validate_media_batch(raw_files, min_count=0, max_upload_bytes=max_upload_bytes)

    existing = await _find_active_duplicate_ticket(
        session, lighting_point_id=point.id, fault_type=fault_type
    )
    if existing is not None:
        raise _duplicate_error(existing)

    grade = await evaluate_ticket_grading(
        session,
        fault_type=fault_type,
        road_level=point.road_level,
        asset_importance=asset_importance,
        safety_flag=safety_flag,
    )
    response_deadline_at, repair_deadline_at = await get_ticket_deadlines(session, confirmed_at=ts)
    admin_ids = await list_region_admin_ids(session, point.region_id)

    saved_paths: list[Path] = []
    try:
        try:
            async with session.begin_nested():
                ticket, audits = await create_ticket_record(
                    session,
                    ticket_no=await next_ticket_no(session, now=ts),
                    region_id=point.region_id,
                    lighting_point_id=point.id,
                    facility_asset_id=facility_asset_id,
                    report_source=(
                        "MANUAL_ADMIN" if actor_role_code == "ADMIN" else "MANUAL_INSPECT"
                    ),
                    fault_type=fault_type,
                    title=title,
                    description=description,
                    reporter_account_id=actor.id,
                    initial_status="PENDING_DISPATCH",
                    priority=grade.priority,
                    grading_snapshot_json=grade.snapshot_json,
                    confirmed_by_inspector=True,
                    confirmed_by_inspector_id=actor.id,
                    confirmed_at=ts,
                    response_deadline_at=response_deadline_at,
                    repair_deadline_at=repair_deadline_at,
                    actor_username=actor.username,
                    actor_role_code=actor_role_code,
                    action="TICKET_CREATED",
                    notify=[("TICKET_CONFIRMED", admin_ids)],
                    persist_audit=False,
                )
        except IntegrityError as exc:
            if not _is_active_duplicate_violation(exc):
                raise
            conflict = await _find_active_duplicate_ticket(
                session, lighting_point_id=point.id, fault_type=fault_type
            )
            if conflict is None:
                raise
            raise _duplicate_error(conflict) from exc

        audits.append(
            build_audit_entry(
                action="TICKET_GRADED",
                resource_type="FAULT_TICKET",
                resource_id=ticket.id,
                ticket_id=ticket.id,
                actor_account_id=actor.id,
                actor_username=actor.username,
                actor_role_code=actor_role_code,
                snapshot=grade.snapshot_json,
            )
        )

        _persist_evidences(
            session,
            validated,
            _save_batch(validated, upload_dir=upload_dir, now=ts, saved_paths=saved_paths),
            ticket_id=ticket.id,
            evidence_type="INSPECTION_PHOTO",
            actor_id=actor.id,
            now=ts,
        )

        orphans: list[FaultEvidence] = []
        if evidence_ids:
            orphan_stmt = select(FaultEvidence).where(
                FaultEvidence.id.in_(evidence_ids),
                FaultEvidence.ticket_id.is_(None),
            )
            if actor_role_code != "ADMIN":
                orphan_stmt = orphan_stmt.where(FaultEvidence.uploaded_by_account_id == actor.id)
            orphans = list((await session.scalars(orphan_stmt.with_for_update())).all())
            if len(orphans) != len(set(evidence_ids)):
                raise AppError(
                    VALIDATION_ERROR,
                    "证据不存在、已绑定或无权使用",
                    {"field": "evidence_ids"},
                )
            taken = {media.sha256 for media in validated} | {ev.sha256 for ev in orphans}
            if len(taken) != len(validated) + len(orphans):
                raise AppError(
                    VALIDATION_ERROR,
                    "evidence_ids 中存在与本次上传内容重复的证据",
                    {"reason": "DUPLICATE_EVIDENCE", "evidence_ids": evidence_ids},
                )
            for evidence in orphans:
                evidence.ticket_id = ticket.id

        # 有决定必须落在本次绑入证据的真实候选上；无证据携带决定一律 422（防静默丢弃）
        if candidate_decisions:
            bound_candidates = {
                (ev.id, candidate["candidate_id"])
                for ev in orphans
                if ev.ai_result_json is not None
                for candidate in ev.ai_result_json["candidates"]
            }
            submitted = {(d.evidence_id, d.candidate_id) for d in candidate_decisions}
            if len(submitted) != len(candidate_decisions) or not submitted <= bound_candidates:
                raise AppError(
                    VALIDATION_ERROR,
                    "候选处置必须唯一且属于本次绑定证据的 AI 候选",
                    {"field": "candidate_decisions"},
                )
            audits[0].business_snapshot_json = {
                **(audits[0].business_snapshot_json or {}),
                "candidate_decisions": [
                    entry.model_dump(mode="json") for entry in candidate_decisions
                ],
            }

        await session.flush()
        return ticket, audits
    except Exception:
        cleanup_files(saved_paths)
        raise


# ──────────────────────────────────────────────────────────────────────
# 入口 3–5：草稿编辑 / 确认 / 撤销 / 改级
# ──────────────────────────────────────────────────────────────────────


def _require_manual_note(fault_type: str, manual_note: str | None) -> None:
    """V3：视觉侧永不产出的类型必须由巡查员给出书面依据。"""
    if fault_type in _MANUAL_NOTE_TYPES and not (manual_note and manual_note.strip()):
        raise AppError(
            VALIDATION_ERROR,
            "改判为 FLICKERING 或 ELECTRIC_HAZARD 时必须填写 manual_note",
            {"field": "manual_note", "fault_type": fault_type},
        )


async def patch_ticket_draft_flow(
    session: AsyncSession,
    *,
    ticket: FaultTicket,
    payload: TicketUpdateDraftIn,
) -> FaultTicket:
    """编辑 AI 待确认草稿（仅 `AI_CANDIDATE_PENDING`）。"""
    if ticket.status != "AI_CANDIDATE_PENDING":
        raise AppError(
            INVALID_STATE_TRANSITION,
            f"仅 AI_CANDIDATE_PENDING 草稿允许编辑，当前为 {ticket.status}",
            {
                "entity": "fault_tickets",
                "from_status": ticket.status,
                "to_status": ticket.status,
            },
        )

    if payload.title is not None:
        ticket.title = payload.title
    if "description" in payload.model_fields_set:
        ticket.description = payload.description

    if "facility_asset_id" in payload.model_fields_set and payload.facility_asset_id is not None:
        asset = await session.scalar(
            select(FacilityAsset).where(FacilityAsset.id == payload.facility_asset_id)
        )
        if asset is None or asset.lighting_point_id != ticket.lighting_point_id:
            raise AppError(
                VALIDATION_ERROR,
                "设施资产不存在或不属于当前照明点位",
                {"facility_asset_id": payload.facility_asset_id},
            )
    if "facility_asset_id" in payload.model_fields_set:
        ticket.facility_asset_id = payload.facility_asset_id

    if payload.fault_type is not None and payload.fault_type != ticket.fault_type:
        _require_manual_note(payload.fault_type, payload.manual_note)
        ticket.fault_type = payload.fault_type
        if ticket.report_source == "AI_CANDIDATE" and payload.fault_type in _MANUAL_NOTE_TYPES:
            # ck_fault_tickets_ai_source_excludes_unsupported_types：改判后必须脱离 AI 来源。
            ticket.report_source = "MANUAL_INSPECT"

    await session.flush()
    return ticket


async def confirm_ticket_flow(
    session: AsyncSession,
    *,
    ticket: FaultTicket,
    payload: TicketConfirmIn,
    actor: Account,
    actor_role_code: str,
    now: datetime | None = None,
) -> tuple[FaultTicket, list[AuditLog]]:
    """巡查员确认 AI 候选：分级 + 48h 基准 deadline + 转 `PENDING_DISPATCH`（A3）。"""
    ts = ensure_utc(now)
    if ticket.status != "AI_CANDIDATE_PENDING":
        raise AppError(
            INVALID_STATE_TRANSITION,
            f"仅 AI_CANDIDATE_PENDING 可确认，当前为 {ticket.status}",
            {
                "entity": "fault_tickets",
                "from_status": ticket.status,
                "to_status": "PENDING_DISPATCH",
            },
        )

    evidences = (
        await session.scalars(select(FaultEvidence).where(FaultEvidence.ticket_id == ticket.id))
    ).all()
    # 决策键 = (evidence_id, candidate_id)：故障候选必须全覆盖唯一；
    # 参考候选（is_fault_candidate=False）可选项处置并留痕，未知/重复一律 422
    candidates = {
        (evidence.id, candidate["candidate_id"]): candidate
        for evidence in evidences
        if evidence.ai_result_json is not None
        for candidate in evidence.ai_result_json["candidates"]
    }
    fault_keys = {key for key, candidate in candidates.items() if candidate["is_fault_candidate"]}
    decisions = {
        (entry.evidence_id, entry.candidate_id): entry for entry in payload.candidate_decisions
    }
    if (
        not candidates
        or len(decisions) != len(payload.candidate_decisions)
        or not decisions.keys() <= candidates.keys()
        or not fault_keys <= decisions.keys()
    ):
        raise AppError(
            VALIDATION_ERROR,
            "必须对本工单每个故障候选提交唯一处置，且处置须属于本工单候选",
            {"field": "candidate_decisions"},
        )
    # 保留项有效类型：confirmed 取 AI 类型（参考候选无类型只留痕），modified 取改判类型
    retained_types = {
        entry.fault_type
        if entry.decision == "modified"
        else candidates[key]["candidate_fault_type"]
        for key, entry in decisions.items()
        if entry.decision != "rejected"
    }
    retained_types.discard(None)
    if not retained_types or payload.fault_type not in retained_types:
        raise AppError(
            VALIDATION_ERROR,
            "工单故障类型必须来自至少一个保留候选",
            {"field": "fault_type"},
        )

    final_fault_type = payload.fault_type
    # 缓存不可变标量：savepoint 回滚会 expire ORM 属性，except 路径不得再触发懒加载
    lighting_point_id = ticket.lighting_point_id
    ticket_id = ticket.id

    # 改判后的 (点位, 类型) 可能撞上既有活跃单（uq_fault_tickets_active_point_type）：
    # 应用层先查给出 409 冻结契约；savepoint+flush 兜底确认期间的并发竞态。
    conflict = await _find_active_duplicate_ticket(
        session,
        lighting_point_id=lighting_point_id,
        fault_type=final_fault_type,
    )
    if conflict is not None and conflict.id != ticket_id:
        raise _duplicate_error(conflict)

    try:
        async with session.begin_nested():
            if payload.title is not None:
                ticket.title = payload.title
            if "description" in payload.model_fields_set:
                ticket.description = payload.description
            ticket.fault_type = final_fault_type
            if ticket.report_source == "AI_CANDIDATE" and final_fault_type in _MANUAL_NOTE_TYPES:
                ticket.report_source = "MANUAL_INSPECT"
            await session.flush()
    except IntegrityError as exc:
        if not _is_active_duplicate_violation(exc):
            raise
        conflict = await _find_active_duplicate_ticket(
            session,
            lighting_point_id=lighting_point_id,
            fault_type=final_fault_type,
        )
        if conflict is None or conflict.id == ticket_id:
            raise
        raise _duplicate_error(conflict) from exc

    point = await session.scalar(
        select(LightingPoint).where(LightingPoint.id == ticket.lighting_point_id)
    )
    asset_importance: str | None = None
    if ticket.facility_asset_id is not None:
        asset_importance = await session.scalar(
            select(FacilityAsset.importance).where(FacilityAsset.id == ticket.facility_asset_id)
        )

    grade = await evaluate_ticket_grading(
        session,
        fault_type=final_fault_type,
        road_level=point.road_level if point is not None else "BRANCH",
        asset_importance=asset_importance,
        safety_flag=payload.safety_flag,
    )
    snapshot: dict[str, Any] = dict(grade.snapshot_json)
    priority = grade.priority
    if payload.priority_override is not None and payload.priority_override != priority:
        priority = payload.priority_override
        snapshot["manual_override"] = True
        snapshot["priority"] = priority
        snapshot["override_reason"] = (
            payload.manual_note.strip() if payload.manual_note else "priority_override"
        )

    response_deadline_at, repair_deadline_at = await get_ticket_deadlines(session, confirmed_at=ts)
    admin_ids = await list_region_admin_ids(session, ticket.region_id)

    # 先迁移状态再落确认三件套：`ck_fault_tickets_inspector_confirmation_serialized`
    # 要求 confirmed=true 与 status<>'AI_CANDIDATE_PENDING' 同时成立，而中途的
    # `list_region_admin_ids` 会触发 autoflush，倒序会写出非法中间态。
    confirm_audit = await transition_ticket(
        session,
        ticket,
        to_status="PENDING_DISPATCH",
        actor_account_id=actor.id,
        actor_username=actor.username,
        actor_role_code=actor_role_code,
        action="TICKET_CONFIRMED",
        now=ts,
        snapshot_extra={
            "priority": priority,
            "manual_override": bool(snapshot.get("manual_override")),
            "candidate_decisions": [
                entry.model_dump(mode="json") for entry in payload.candidate_decisions
            ],
        },
        notify=[("TICKET_CONFIRMED", admin_ids)],
        persist_audit=False,
    )

    ticket.priority = priority
    ticket.grading_score = None
    ticket.grading_snapshot_json = snapshot
    ticket.confirmed_by_inspector = True
    ticket.confirmed_by_inspector_id = actor.id
    ticket.confirmed_at = ts
    ticket.response_deadline_at = response_deadline_at
    ticket.repair_deadline_at = repair_deadline_at

    graded_audit = build_audit_entry(
        action="TICKET_GRADED",
        resource_type="FAULT_TICKET",
        resource_id=ticket.id,
        ticket_id=ticket.id,
        actor_account_id=actor.id,
        actor_username=actor.username,
        actor_role_code=actor_role_code,
        snapshot=snapshot,
    )
    return ticket, [confirm_audit, graded_audit]


async def cancel_ticket_flow(
    session: AsyncSession,
    *,
    ticket: FaultTicket,
    payload: TicketCancelIn,
    actor: Account,
    actor_role_code: str,
    now: datetime | None = None,
) -> tuple[FaultTicket, list[AuditLog]]:
    """撤销工单（A4 / A5）：仅 `AI_CANDIDATE_PENDING` 与 `PENDING_DISPATCH` 可撤。"""
    ts = ensure_utc(now)
    if ticket.status not in {"AI_CANDIDATE_PENDING", "PENDING_DISPATCH"}:
        raise AppError(
            INVALID_STATE_TRANSITION,
            f"当前状态 {ticket.status} 不允许撤销（返修单须走 /review-resolve）",
            {
                "entity": "fault_tickets",
                "from_status": ticket.status,
                "to_status": "CANCELLED",
                "allowed_from": ["AI_CANDIDATE_PENDING", "PENDING_DISPATCH"],
            },
        )

    if actor_role_code == "INSPECTOR" and ticket.reporter_account_id != actor.id:
        # 草稿单允许同区域巡查员撤销；已确认待派工单只允许报障人本人。
        scope = None
        if ticket.status == "AI_CANDIDATE_PENDING":
            scope = await has_valid_region_scope(
                session,
                account_id=actor.id,
                region_id=ticket.region_id,
                scope_type="INSPECT",
            )
        if scope is None:
            raise AppError(
                FORBIDDEN_ROLE,
                "仅报障人本人或持有该区域巡查授权的巡查员可撤销此工单",
                {"ticket_id": ticket.id, "required_scope": "INSPECT"},
            )

    admin_ids = await list_region_admin_ids(session, ticket.region_id)
    audit = await transition_ticket(
        session,
        ticket,
        to_status="CANCELLED",
        actor_account_id=actor.id,
        actor_username=actor.username,
        actor_role_code=actor_role_code,
        action="TICKET_CANCELLED",
        cancel_reason=payload.cancel_reason,
        now=ts,
        notify=[("TICKET_CANCELLED", admin_ids)],
        persist_audit=False,
    )
    return ticket, [audit]


async def regrade_ticket_flow(
    session: AsyncSession,
    *,
    ticket: FaultTicket,
    payload: TicketRegradeIn,
    actor: Account,
    actor_role_code: str,
) -> tuple[FaultTicket, list[AuditLog]]:
    """管理员手工改级：只写 `priority` 与快照，**不顺延**法定 48h 截止时间（D-11 / MUT-17）。"""
    if ticket.status in {"AI_CANDIDATE_PENDING", "CLOSED", "CANCELLED"}:
        raise AppError(
            INVALID_STATE_TRANSITION,
            f"当前状态 {ticket.status} 不允许手工改级",
            {
                "entity": "fault_tickets",
                "from_status": ticket.status,
                "to_status": ticket.status,
            },
        )

    reason = payload.reason.strip()
    previous_priority = ticket.priority
    previous_snapshot = dict(ticket.grading_snapshot_json or {})
    snapshot = {
        **previous_snapshot,
        "priority": payload.priority,
        "manual_override": True,
        "override_reason": reason,
        "previous_priority": previous_priority,
    }

    ticket.priority = payload.priority
    ticket.grading_score = None
    ticket.grading_snapshot_json = snapshot
    await session.flush()

    audit = build_audit_entry(
        action="TICKET_REGRADED",
        resource_type="FAULT_TICKET",
        resource_id=ticket.id,
        ticket_id=ticket.id,
        actor_account_id=actor.id,
        actor_username=actor.username,
        actor_role_code=actor_role_code,
        snapshot={
            "from_priority": previous_priority,
            "to_priority": payload.priority,
            "reason": reason,
            "previous_snapshot": previous_snapshot,
            "repair_deadline_at": ticket.repair_deadline_at,
        },
    )
    return ticket, [audit]


# ──────────────────────────────────────────────────────────────────────
# 入口 6–7：补传证据 / 详情组装
# ──────────────────────────────────────────────────────────────────────


async def upload_ticket_evidences_flow(
    session: AsyncSession,
    *,
    ticket: FaultTicket,
    raw_files: list[tuple[bytes, str | None, str | None]],
    evidence_type: str,
    actor: Account,
    upload_dir: Path,
    max_upload_bytes: int,
    now: datetime | None = None,
) -> list[FaultEvidence]:
    """为既有工单补传静态图片证据（1..10 张，同票 sha256 去重由 DB 兜底）。"""
    if evidence_type not in _EVIDENCE_TYPES:
        raise AppError(
            VALIDATION_ERROR,
            f"不支持的证据类型 {evidence_type}",
            {"field": "evidence_type", "expected": sorted(_EVIDENCE_TYPES)},
        )
    # 状态机 A 终态守卫（`docs/DESIGN.md` §2.1：`CLOSED`/`CANCELLED` 不可复活）。
    # 必须先于媒体校验与 sha256 去重，避免对已终态工单做无意义解码/写盘再落库。
    if ticket.status in _TERMINAL_TICKET_STATUSES:
        raise AppError(
            INVALID_STATE_TRANSITION,
            f"工单 {ticket.ticket_no} 已处于终态 {ticket.status}，禁止继续追加证据",
            {
                "ticket_id": ticket.id,
                "status": ticket.status,
                "evidence_type": evidence_type,
            },
        )
    ts = ensure_utc(now)
    validated = _validate_media_batch(raw_files, min_count=1, max_upload_bytes=max_upload_bytes)

    existing_hashes = set(
        (
            await session.scalars(
                select(FaultEvidence.sha256).where(FaultEvidence.ticket_id == ticket.id)
            )
        ).all()
    )
    duplicated = [media.sha256 for media in validated if media.sha256 in existing_hashes]
    if duplicated:
        raise AppError(
            VALIDATION_ERROR,
            "该工单已存在内容完全相同的证据图片",
            {"reason": "DUPLICATE_EVIDENCE", "sha256": duplicated[0]},
        )

    saved_paths: list[Path] = []
    try:
        created = _persist_evidences(
            session,
            validated,
            _save_batch(validated, upload_dir=upload_dir, now=ts, saved_paths=saved_paths),
            ticket_id=ticket.id,
            evidence_type=evidence_type,
            actor_id=actor.id,
            now=ts,
        )
        await session.flush()
        # `_persist_evidences` 对无 AI 载荷写入了 SQL `null()` 表达式，flush 后需显式
        # refresh 才能把 `ai_result_json=None` 与服务端时间落回 Python 实例，防 `MissingGreenlet`。
        for ev in created:
            await session.refresh(ev)
        return created
    except Exception:
        cleanup_files(saved_paths)
        raise


async def build_ticket_detail(
    session: AsyncSession,
    ticket_id: int,
    upload_dir: Path,
) -> FaultTicketDetailOut:
    """组装工单完整详情：票 + 证据 + 派工 + 维修/耗材 + 验收 + 返修 + 审计时间线。"""
    ticket = await session.scalar(select(FaultTicket).where(FaultTicket.id == ticket_id))
    if ticket is None:
        raise AppError(
            RESOURCE_NOT_FOUND,
            "故障工单不存在",
            {"resource_type": "FAULT_TICKET", "resource_id": ticket_id},
        )

    region_name = await session.scalar(
        select(MaintenanceRegion.name).where(MaintenanceRegion.id == ticket.region_id)
    )
    point = await session.scalar(
        select(LightingPoint).where(LightingPoint.id == ticket.lighting_point_id)
    )
    base = ticket_to_out(
        ticket,
        region_name=region_name,
        point_code=point.point_code if point is not None else None,
        point_name=point.name if point is not None else None,
    )

    evidence_rows = (
        await session.scalars(
            select(FaultEvidence)
            .where(FaultEvidence.ticket_id == ticket.id)
            .order_by(FaultEvidence.id.asc())
        )
    ).all()

    dispatch_rows = (
        await session.execute(
            select(DispatchRecord, Account.username, Account.full_name)
            .join(Account, Account.id == DispatchRecord.technician_account_id)
            .where(DispatchRecord.ticket_id == ticket.id)
            .order_by(DispatchRecord.id.asc())
        )
    ).all()
    dispatches = []
    for record, username, full_name in dispatch_rows:
        dispatch_out = DispatchRecordOut.model_validate(record)
        dispatch_out.ticket_no = ticket.ticket_no
        dispatch_out.ticket_status = ticket.status  # type: ignore[assignment]
        dispatch_out.ticket_priority = ticket.priority  # type: ignore[assignment]
        dispatch_out.ticket_fault_type = ticket.fault_type  # type: ignore[assignment]
        dispatch_out.ticket_title = ticket.title
        dispatch_out.point_code = point.point_code if point is not None else None
        dispatch_out.point_name = point.name if point is not None else None
        dispatch_out.region_name = region_name
        dispatch_out.technician_username = username
        dispatch_out.technician_full_name = full_name
        dispatch_out.repair_deadline_at = ticket.repair_deadline_at
        dispatches.append(dispatch_out)

    repair_rows = (
        await session.scalars(
            select(RepairProcess)
            .where(RepairProcess.ticket_id == ticket.id)
            .order_by(RepairProcess.id.asc())
        )
    ).all()
    material_rows = (
        await session.scalars(
            select(MaterialUsage)
            .where(MaterialUsage.ticket_id == ticket.id)
            .order_by(MaterialUsage.id.asc())
        )
    ).all()
    materials_by_repair: dict[int, list[MaterialUsageOut]] = {}
    for material in material_rows:
        materials_by_repair.setdefault(material.repair_process_id, []).append(
            MaterialUsageOut(
                id=material.id,
                repair_process_id=material.repair_process_id,
                ticket_id=material.ticket_id,
                material_code=material.material_code,
                material_name=material.material_name,
                quantity=float(material.quantity),
                unit=material.unit,  # type: ignore[arg-type]
                unit_price=(
                    float(material.unit_price) if material.unit_price is not None else None
                ),
                created_at=material.created_at,
            )
        )
    repair_processes = []
    for process in repair_rows:
        process_out = RepairProcessOut.model_validate(process)
        process_out.materials = materials_by_repair.get(process.id, [])
        repair_processes.append(process_out)

    acceptance_rows = (
        await session.scalars(
            select(AcceptanceRecord)
            .where(AcceptanceRecord.ticket_id == ticket.id)
            .order_by(AcceptanceRecord.id.asc())
        )
    ).all()

    rework_rows = (
        await session.scalars(
            select(ReworkOrder)
            .where(ReworkOrder.ticket_id == ticket.id)
            .order_by(ReworkOrder.round_no.asc(), ReworkOrder.id.asc())
        )
    ).all()

    audit_rows = (
        await session.scalars(
            select(AuditLog)
            .where(AuditLog.ticket_id == ticket.id)
            .order_by(AuditLog.created_at.asc(), AuditLog.id.asc())
        )
    ).all()

    return FaultTicketDetailOut(
        **base.model_dump(),
        evidences=[evidence_to_out(row, upload_dir) for row in evidence_rows],
        dispatches=dispatches,
        repair_processes=repair_processes,
        acceptances=[AcceptanceRecordOut.model_validate(row) for row in acceptance_rows],
        rework_orders=[ReworkOrderOut.model_validate(row) for row in rework_rows],
        timeline=[TimelineEntryOut.model_validate(row) for row in audit_rows],
    )
