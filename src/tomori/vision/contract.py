"""对外契约（Pydantic v2）：与 `backend-dev` / `frontend-dev` 已冻结的字段名逐字一致。

字段定义权威：`.team/notes/vision-dev/stage1-vision-spec-input.md` §4.1。
两条安全红线由 `model_validator` **强制**，任何调用方都无法构造出违反它们的实例。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tomori.vision import config
from tomori.vision.errors import INTERNAL_ERROR, VisionError

RawClass = Literal[
    "streetlight_on",
    "streetlight_off",
    "streetlight_damaged",
    # task-165：`occluded` 类明确弃用（`merged_v7` `nc=3`）；保留字面量仅为
    # `API`/`contract` 与 `config.RAW_CLASSES`（`nc=4` 共享契约）兼容——`nc=3` 模型
    # 不会产出 `occluded` 候选，但 `fault_type`/`fault_candidate` 枚举不动
    "streetlight_occluded",
]
FaultType = Literal["LAMP_OUT", "FIXTURE_DAMAGED", "OCCLUDED", "OTHER"]
VisualSignalState = Literal[
    "LAMP_OUT",
    "FIXTURE_DAMAGED",
    "OCCLUDED",
    "DAYTIME_ALWAYS_ON_SUSPECT",
    "NORMAL_LIT",
    "DAYTIME_UNPOWERED_EXPECTED",
]
InferenceMode = Literal["yolo26_onnx_e2e", "yolo26_pt", "baseline_pretrained_opencv"]
QualityGrade = Literal["pass", "warn", "fail"]
SceneLighting = Literal["day", "night", "twilight"]
FusionStatus = Literal[
    "consistent",
    "daytime_unpowered_suppressed",
    "low_confidence_conflict",
    "occlusion_merged",
    "baseline_heuristic",
]
SeverityHint = Literal["none", "P1", "P2", "P3", "P4"]

DISCLAIMER = (
    "AI 仅生成静态巡查照片故障候选，必须经巡查员人工确认后方可生成工单；"
    "严禁替代现场断电与电气安全检查，且不连接任何远程灯控设备。"
)

# 视觉侧永不产出的故障类型：FLICKERING 需多帧时序，ELECTRIC_HAZARD 需现场检测（`V3`）
FORBIDDEN_FAULT_TYPES: frozenset[str] = frozenset({"FLICKERING", "ELECTRIC_HAZARD"})


class ImageQualityOut(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    quality_grade: QualityGrade
    laplacian_var: float
    brightness_mean_v: float
    overexposed_ratio: float
    underexposed_ratio: float
    scene_lighting: SceneLighting
    warnings: list[str] = Field(default_factory=list)


class OpencvRoiMetricsOut(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    roi_v_mean: float
    roi_v_p95: float
    bg_ring_v_median: float
    luminance_contrast: float
    green_foliage_ratio: float
    edge_density: float
    fusion_status: FusionStatus


class FaultCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: str
    raw_class: RawClass
    candidate_fault_type: FaultType | None
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

    @model_validator(mode="after")
    def _bbox_consistent(self) -> FaultCandidate:
        x1, y1, x2, y2 = self.bbox_norm
        if not (0.0 <= x1 <= x2 <= 1.0 and 0.0 <= y1 <= y2 <= 1.0):
            raise ValueError(f"bbox_norm 非法或未归一化：{self.bbox_norm}")
        px1, py1, px2, py2 = self.bbox_px
        if not (0 <= px1 < px2 and 0 <= py1 < py2):
            raise ValueError(f"bbox_px 非法：{self.bbox_px}")
        return self

    @model_validator(mode="after")
    def _fault_type_consistent(self) -> FaultCandidate:
        # 正常/抑制态（streetlight_on 参照框等）必须无故障类型；故障态必须给出故障类型
        if self.is_fault_candidate and self.candidate_fault_type is None:
            raise ValueError(f"故障候选 {self.candidate_id} 的 candidate_fault_type 不得为空")
        if not self.is_fault_candidate and self.candidate_fault_type is not None:
            raise ValueError(
                f"非故障候选 {self.candidate_id} 的 candidate_fault_type 必须为空，"
                f"实际为 {self.candidate_fault_type}"
            )
        if self.candidate_fault_type in FORBIDDEN_FAULT_TYPES:
            raise ValueError("视觉侧严禁产出 FLICKERING / ELECTRIC_HAZARD")
        return self


class FaultCandidateResult(BaseModel):
    """单图完整分析载荷。`fault_evidences` 一图一行，此对象即 `ai_result_json`。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    analysis_id: str
    inference_mode: InferenceMode
    ai_is_baseline_mode: bool
    model_version: str
    image_width: int
    image_height: int
    original_image_url: str
    annotated_image_url: str | None
    image_quality: ImageQualityOut
    primary_fault_type_suggestion: FaultType | None
    max_fused_confidence: float
    candidates: list[FaultCandidate]
    requires_inspector_confirmation: Literal[True] = True
    replaces_electrical_safety_check: Literal[False] = False
    disclaimer: str = DISCLAIMER

    @model_validator(mode="after")
    def _soft_red_lines(self) -> FaultCandidateResult:
        # 1) 基线模式必须自证，且置信度不得突破 DB CHECK 的 0.65 封顶
        if self.inference_mode == "baseline_pretrained_opencv":
            if not self.ai_is_baseline_mode:
                raise ValueError("inference_mode 为基线模式时 ai_is_baseline_mode 必须为 True")
            if self.max_fused_confidence > config.BASELINE_CONFIDENCE_CAP:
                raise ValueError(
                    f"基线模式 max_fused_confidence {self.max_fused_confidence} "
                    f"超过封顶 {config.BASELINE_CONFIDENCE_CAP}"
                )
            for candidate in self.candidates:
                if candidate.fused_confidence > config.BASELINE_CONFIDENCE_CAP:
                    raise ValueError(
                        f"基线模式候选 {candidate.candidate_id} 的 fused_confidence "
                        f"{candidate.fused_confidence} 超过封顶 {config.BASELINE_CONFIDENCE_CAP}"
                    )
        # 2) max_fused_confidence 必须等于候选中最大 fused_confidence（无候选则 0）
        expected_max = max((c.fused_confidence for c in self.candidates), default=0.0)
        if abs(self.max_fused_confidence - expected_max) > 1e-9:
            raise ValueError(
                f"max_fused_confidence {self.max_fused_confidence} 与候选取值 {expected_max} 不一致"
            )
        # 3) primary 必须指向真候选的故障类型
        if self.primary_fault_type_suggestion is not None:
            fault_candidates = [c for c in self.candidates if c.is_fault_candidate]
            if not fault_candidates:
                raise ValueError(
                    "primary_fault_type_suggestion 非空但没有任何 is_fault_candidate=true 的候选"
                )
            if self.primary_fault_type_suggestion not in {
                c.candidate_fault_type for c in fault_candidates
            }:
                raise ValueError(
                    f"primary_fault_type_suggestion {self.primary_fault_type_suggestion} "
                    "不来自任何故障候选"
                )
        # 4) 视觉侧永不产出时序/电气类故障（DB CHECK 同款约束）
        for candidate in self.candidates:
            if candidate.candidate_fault_type in FORBIDDEN_FAULT_TYPES:
                raise ValueError("视觉侧严禁产出 FLICKERING / ELECTRIC_HAZARD")
        return self

    def to_json_dict(self) -> dict[str, object]:
        return self.model_dump(mode="json")


def ensure_fault_types_allowed(fault_type: str | None) -> None:
    """供编排层在构造前显式校验，避免靠异常兜底。"""
    if fault_type is not None and fault_type not in config.FAULT_TYPES:
        raise VisionError(
            INTERNAL_ERROR,
            f"未知故障类型 {fault_type}",
            {"fault_type": fault_type, "allowed": list(config.FAULT_TYPES)},
        )
