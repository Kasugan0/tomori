"""故障单、AI 候选分析结果与故障证据相关 Pydantic v2 Schema。"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from tomori.schemas.common import (
    AiFaultType,
    EvidenceType,
    FaultType,
    FusionStatus,
    InferenceMode,
    ORMModel,
    OverdueStage,
    PriorityCode,
    QualityGrade,
    RawClass,
    ReportSource,
    RoleCode,
    SceneLighting,
    SeverityHint,
    TicketStatus,
    VisualSignalState,
)

if TYPE_CHECKING:
    from tomori.schemas.dispatch import DispatchRecordOut, RepairProcessOut
    from tomori.schemas.operations import AcceptanceRecordOut, ReworkOrderOut

_MANUAL_NOTE_REQUIRED_TYPES: frozenset[str] = frozenset({"FLICKERING", "ELECTRIC_HAZARD"})


class ImageQualityOut(ORMModel):
    """单图质量与光照检测输出。"""

    quality_grade: QualityGrade
    laplacian_var: float
    brightness_mean_v: float
    overexposed_ratio: float
    underexposed_ratio: float
    scene_lighting: SceneLighting
    warnings: list[str] = Field(default_factory=list)


class OpencvRoiMetricsOut(ORMModel):
    """灯头 ROI 物理启发式度量输出。"""

    roi_v_mean: float
    roi_v_p95: float
    bg_ring_v_median: float
    luminance_contrast: float
    green_foliage_ratio: float
    edge_density: float
    fusion_status: FusionStatus


class FaultCandidateOut(ORMModel):
    """单图单个检测目标的 AI 候选条目。"""

    candidate_id: str
    raw_class: RawClass
    candidate_fault_type: AiFaultType | None = None
    visual_signal_state: VisualSignalState
    is_fault_candidate: bool
    bbox_norm: tuple[float, float, float, float]
    bbox_px: tuple[int, int, int, int]
    yolo_confidence: float
    fused_confidence: float
    suggested_severity_hint: SeverityHint
    opencv_roi_metrics: OpencvRoiMetricsOut
    diagnostic_summary: str
    requires_inspector_confirmation: Literal[True] = True
    replaces_electrical_safety_check: Literal[False] = False


class FaultCandidateResultOut(ORMModel):
    """单图完整 AI 视觉分析载荷（与 fault_evidences.ai_result_json 对齐）。"""

    evidence_id: int | None = None
    analysis_id: str
    inference_mode: InferenceMode
    ai_is_baseline_mode: bool
    model_version: str
    image_width: int
    image_height: int
    original_image_url: str
    annotated_image_url: str | None = None
    image_quality: ImageQualityOut
    primary_fault_type_suggestion: AiFaultType | None = None
    max_fused_confidence: float
    candidates: list[FaultCandidateOut] = Field(default_factory=list)
    requires_inspector_confirmation: Literal[True] = True
    replaces_electrical_safety_check: Literal[False] = False
    disclaimer: str


class FaultEvidenceOut(ORMModel):
    """故障证据输出模型。"""

    id: int
    ticket_id: int | None = None
    analysis_id: str | UUID | None = None
    evidence_type: EvidenceType
    media_mime: str
    storage_path: str
    file_size_bytes: int
    sha256: str
    image_width: int
    image_height: int
    ai_result_json: FaultCandidateResultOut | None = None
    ai_is_baseline_mode: bool
    uploaded_by_account_id: int
    url: str | None = None
    raw_url: str | None = None
    annotated_url: str | None = None
    captured_at: datetime | None = None
    created_at: datetime

    @field_validator("analysis_id", mode="after")
    @classmethod
    def _coerce_analysis_id(cls, v: str | UUID | None) -> str | None:
        return str(v) if v is not None else None


class AiCandidateEvidenceOut(FaultEvidenceOut):
    """AI 候选识别响应中的单图证据（含可选顶层 FaultCandidateResultOut 字段）。"""

    evidence_id: int | None = None
    inference_mode: InferenceMode | None = None
    model_version: str | None = None
    original_image_url: str | None = None
    annotated_image_url: str | None = None
    image_quality: ImageQualityOut | None = None
    primary_fault_type_suggestion: AiFaultType | None = None
    max_fused_confidence: float | None = None
    candidates: list[FaultCandidateOut] | None = None
    requires_inspector_confirmation: Literal[True] | None = None
    replaces_electrical_safety_check: Literal[False] | None = None
    disclaimer: str | None = None


class FaultTicketOut(ORMModel):
    """故障工单主输出模型。"""

    id: int
    ticket_no: str
    region_id: int
    region_name: str | None = None
    lighting_point_id: int
    point_code: str | None = None
    point_name: str | None = None
    facility_asset_id: int | None = None
    report_source: ReportSource
    fault_type: FaultType
    title: str
    description: str | None = None
    status: TicketStatus
    priority: PriorityCode | None = None
    grading_score: Decimal | None = None
    grading_snapshot_json: dict[str, Any] | None = None
    reporter_account_id: int
    confirmed_by_inspector: bool
    confirmed_by_inspector_id: int | None = None
    confirmed_at: datetime | None = None
    ai_suggestion_json: dict[str, Any] | None = None
    ai_max_confidence: Decimal | None = None
    response_deadline_at: datetime | None = None
    repair_deadline_at: datetime | None = None
    is_sla_overdue: bool
    overdue_stage: OverdueStage | None = None
    overdue_marked_at: datetime | None = None
    rework_count: int
    dispatch_reject_count: int
    manual_review_reason: str | None = None
    cancel_reason: str | None = None
    cancelled_by_account_id: int | None = None
    cancelled_at: datetime | None = None
    closed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class AiCandidatesResponseOut(ORMModel):
    """POST /tickets/ai-candidates 响应模型（零故障候选时 ticket 为 None）。"""

    ticket: FaultTicketOut | None = None
    evidences: list[AiCandidateEvidenceOut] = Field(default_factory=list)


class TicketCreateJsonIn(BaseModel):
    """人工报障 JSON 请求体（POST /tickets）。"""

    lighting_point_id: int = Field(gt=0)
    facility_asset_id: int | None = Field(default=None, gt=0)
    fault_type: FaultType
    title: str = Field(min_length=1, max_length=128)
    description: str | None = None
    safety_flag: bool = False
    manual_note: str | None = None
    evidence_ids: list[Annotated[int, Field(strict=True, gt=0)]] = Field(default_factory=list)
    # 可选：对 evidence_ids 绑入证据内 AI 候选的逐条处置留痕（零故障分析→人工报障路径）
    candidate_decisions: list[CandidateDecisionIn] | None = None


class TicketUpdateDraftIn(BaseModel):
    """AI 待确认草稿编辑请求体（PATCH /tickets/{id}）。"""

    title: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = None
    fault_type: FaultType | None = None
    facility_asset_id: int | None = Field(default=None, gt=0)
    manual_note: str | None = None

    @model_validator(mode="after")
    def _validate_manual_note(self) -> TicketUpdateDraftIn:
        if self.fault_type in _MANUAL_NOTE_REQUIRED_TYPES and not (
            self.manual_note and self.manual_note.strip()
        ):
            raise ValueError("改判为 FLICKERING 或 ELECTRIC_HAZARD 时必须填写 manual_note")
        return self


class CandidateDecisionIn(BaseModel):
    evidence_id: int = Field(strict=True, gt=0)
    candidate_id: str = Field(min_length=1)
    decision: Literal["confirmed", "modified", "rejected"]
    fault_type: FaultType | None = None
    manual_note: str | None = Field(default=None, max_length=512)

    @model_validator(mode="after")
    def validate_decision(self) -> CandidateDecisionIn:
        if self.decision == "modified":
            if self.fault_type is None:
                raise ValueError("修改候选时必须填写 fault_type")
            if self.fault_type in _MANUAL_NOTE_REQUIRED_TYPES and not (
                self.manual_note and self.manual_note.strip()
            ):
                raise ValueError("改判为 FLICKERING 或 ELECTRIC_HAZARD 时必须填写 manual_note")
        elif self.fault_type is not None or self.manual_note is not None:
            raise ValueError("仅 modified 候选可携带 fault_type 或 manual_note")
        return self


class TicketConfirmIn(BaseModel):
    """巡查员确认 AI 候选草稿请求体（POST /tickets/{id}/confirm）。"""

    fault_type: FaultType
    candidate_decisions: list[CandidateDecisionIn] = Field(min_length=1)
    priority_override: PriorityCode | None = None
    safety_flag: bool = False
    manual_note: str | None = None
    title: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = None


class TicketCancelIn(BaseModel):
    """撤销工单请求体（POST /tickets/{id}/cancel）。"""

    cancel_reason: str = Field(min_length=1, max_length=256)

    @field_validator("cancel_reason")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("cancel_reason 不能为空白")
        return v


class TicketRegradeIn(BaseModel):
    """管理员手工改级请求体（POST /tickets/{id}/regrade）。"""

    priority: PriorityCode
    reason: str = Field(min_length=1, max_length=256)

    @field_validator("reason")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("reason 不能为空白")
        return v


class TimelineEntryOut(ORMModel):
    """工单详情审计时间线条目。"""

    id: int
    action: str
    actor_username: str | None = None
    actor_role_code: RoleCode | None = None
    business_snapshot_json: dict[str, Any] | None = None
    created_at: datetime


class FaultTicketDetailOut(FaultTicketOut):
    """工单完整详情响应模型。"""

    evidences: list[FaultEvidenceOut] = Field(default_factory=list)
    dispatches: list[DispatchRecordOut] = Field(default_factory=list)
    repair_processes: list[RepairProcessOut] = Field(default_factory=list)
    acceptances: list[AcceptanceRecordOut] = Field(default_factory=list)
    rework_orders: list[ReworkOrderOut] = Field(default_factory=list)
    timeline: list[TimelineEntryOut] = Field(default_factory=list)


__all__ = [
    "AiCandidateEvidenceOut",
    "AiCandidatesResponseOut",
    "CandidateDecisionIn",
    "FaultCandidateOut",
    "FaultCandidateResultOut",
    "FaultEvidenceOut",
    "FaultTicketDetailOut",
    "FaultTicketOut",
    "ImageQualityOut",
    "OpencvRoiMetricsOut",
    "TicketCancelIn",
    "TicketConfirmIn",
    "TicketCreateJsonIn",
    "TicketRegradeIn",
    "TicketUpdateDraftIn",
    "TimelineEntryOut",
]
