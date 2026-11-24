"""模型原始检测结果到 `FaultCandidateResult` 的契约映射与安全边界强制层。

落位 `docs/DESIGN.md` §5.1–§5.2：
- `D-30` 硬契约：夜间 / 黄昏 `streetlight_on` 必须作为 `NORMAL_LIT`（`is_fault_candidate=False`）
  参照框回传，严禁过滤丢弃；
- `V1–V3` 三层安全边界：显式维护 `ALLOWED_FAULT_TYPES` / `FORBIDDEN_FAULT_TYPES` 白黑名单，
  任何企图映射出 `ELECTRIC_HAZARD` 或 `FLICKERING` 的输入直接拒绝，并对每个候选与根结果
  恒注入 `requires_inspector_confirmation=True`、`replaces_electrical_safety_check=False`、
  `disclaimer=DISCLAIMER`。
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Sequence
from typing import Final, Literal

from tomori.vision import config
from tomori.vision.baseline import RawDetection
from tomori.vision.contract import (
    DISCLAIMER,
    FaultCandidate,
    FaultCandidateResult,
    FaultType,
    FusionStatus,
    ImageQualityOut,
    InferenceMode,
    OpencvRoiMetricsOut,
    RawClass,
    SceneLighting,
    SeverityHint,
    VisualSignalState,
)
from tomori.vision.errors import INTERNAL_ERROR, VisionError
from tomori.vision.metrics import ImageQuality

ALLOWED_FAULT_TYPES: Final[frozenset[str]] = frozenset(
    {"LAMP_OUT", "FIXTURE_DAMAGED", "OCCLUDED", "OTHER"}
)
FORBIDDEN_FAULT_TYPES: Final[frozenset[str]] = frozenset({"ELECTRIC_HAZARD", "FLICKERING"})

_SEVERITY_RANK: Final[dict[SeverityHint, int]] = {
    "P1": 1,
    "P2": 2,
    "P3": 3,
    "P4": 4,
    "none": 99,
}

_FORBIDDEN_RAW_CLASS_ALIASES: Final[frozenset[str]] = frozenset(
    {
        "ELECTRIC_HAZARD",
        "FLICKERING",
        "STREETLIGHT_ELECTRIC_HAZARD",
        "STREETLIGHT_FLICKERING",
    }
)

__all__ = [
    "ALLOWED_FAULT_TYPES",
    "FORBIDDEN_FAULT_TYPES",
    "build_result",
    "map_detection",
    "validate_fault_type",
]


def validate_fault_type(fault_type: str | None) -> FaultType | None:
    """强制校验 `V3` 故障类型白名单，严禁产出 `ELECTRIC_HAZARD` / `FLICKERING`。"""
    if fault_type is None:
        return None
    if fault_type in FORBIDDEN_FAULT_TYPES:
        raise VisionError(
            INTERNAL_ERROR,
            f"V3 安全边界禁止视觉产出故障类型: {fault_type}",
            {
                "fault_type": fault_type,
                "forbidden": sorted(FORBIDDEN_FAULT_TYPES),
                "allowed": sorted(ALLOWED_FAULT_TYPES),
            },
        )
    if fault_type == "LAMP_OUT":
        return "LAMP_OUT"
    if fault_type == "FIXTURE_DAMAGED":
        return "FIXTURE_DAMAGED"
    if fault_type == "OCCLUDED":
        return "OCCLUDED"
    if fault_type == "OTHER":
        return "OTHER"
    raise VisionError(
        INTERNAL_ERROR,
        f"未知故障类型: {fault_type}",
        {"fault_type": fault_type, "allowed": sorted(ALLOWED_FAULT_TYPES)},
    )


def _validate_image_dimensions(image_width: int, image_height: int) -> None:
    if image_width <= 0 or image_height <= 0:
        raise VisionError(
            INTERNAL_ERROR,
            f"图像尺寸必须为正整数，实际收到 {image_width}x{image_height}",
            {"image_width": image_width, "image_height": image_height},
        )


def _validate_inference_mode(inference_mode: str) -> InferenceMode:
    if inference_mode == "yolo26_onnx_e2e":
        return "yolo26_onnx_e2e"
    if inference_mode == "yolo26_pt":
        return "yolo26_pt"
    if inference_mode == "baseline_pretrained_opencv":
        return "baseline_pretrained_opencv"
    raise VisionError(
        INTERNAL_ERROR,
        f"未知推理模式: {inference_mode}",
        {"inference_mode": inference_mode, "allowed": list(config.INFERENCE_MODES)},
    )


def _normalize_scene_lighting(value: str) -> SceneLighting:
    if value == "day":
        return "day"
    if value == "night":
        return "night"
    if value == "twilight":
        return "twilight"
    raise VisionError(
        INTERNAL_ERROR,
        f"未知场景光照类型: {value}",
        {"scene_lighting": value, "allowed": list(config.SCENE_LIGHTINGS)},
    )


def _normalize_quality_grade(value: str) -> Literal["pass", "warn", "fail"]:
    if value == "pass":
        return "pass"
    if value == "warn":
        return "warn"
    if value == "fail":
        return "fail"
    raise VisionError(
        INTERNAL_ERROR,
        f"未知图像质量等级: {value}",
        {"quality_grade": value, "allowed": list(config.QUALITY_GRADES)},
    )


def _normalize_raw_class(raw_class: str) -> RawClass:
    normalized = raw_class.strip()
    upper = normalized.upper()
    if upper in _FORBIDDEN_RAW_CLASS_ALIASES or "ELECTRIC_HAZARD" in upper or "FLICKERING" in upper:
        raise VisionError(
            INTERNAL_ERROR,
            f"V3 安全边界禁止映射时序或电气安全类检测标签: {raw_class}",
            {"raw_class": raw_class, "forbidden": sorted(FORBIDDEN_FAULT_TYPES)},
        )
    if normalized == "streetlight_on":
        return "streetlight_on"
    if normalized == "streetlight_off":
        return "streetlight_off"
    if normalized == "streetlight_damaged":
        return "streetlight_damaged"
    if normalized == "streetlight_occluded":
        return "streetlight_occluded"
    raise VisionError(
        INTERNAL_ERROR,
        f"未知原始检测类别: {raw_class}",
        {"raw_class": raw_class, "allowed": list(config.RAW_CLASSES)},
    )


def _normalize_detector_confidence(raw_confidence: float) -> float:
    conf = float(raw_confidence)
    if not math.isfinite(conf):
        raise VisionError(
            INTERNAL_ERROR,
            f"检测器置信度必须为有限浮点数，实际收到: {raw_confidence}",
            {"detector_confidence": str(raw_confidence)},
        )
    return round(max(0.0, min(1.0, conf)), 4)


def _clip_and_normalize_box(
    box: tuple[int, int, int, int],
    image_width: int,
    image_height: int,
) -> tuple[tuple[int, int, int, int], tuple[float, float, float, float]]:
    raw_x1, raw_y1, raw_x2, raw_y2 = box
    x1 = max(0, min(int(raw_x1), image_width - 1))
    y1 = max(0, min(int(raw_y1), image_height - 1))
    x2 = max(x1 + 1, min(int(raw_x2), image_width))
    y2 = max(y1 + 1, min(int(raw_y2), image_height))

    nx1 = max(0.0, min(1.0, round(x1 / image_width, 4)))
    ny1 = max(0.0, min(1.0, round(y1 / image_height, 4)))
    nx2 = max(nx1, min(1.0, round(x2 / image_width, 4)))
    ny2 = max(ny1, min(1.0, round(y2 / image_height, 4)))
    return (x1, y1, x2, y2), (nx1, ny1, nx2, ny2)


def map_detection(
    det: RawDetection,
    quality: ImageQuality,
    *,
    image_width: int,
    image_height: int,
    inference_mode: InferenceMode,
    candidate_id: str,
) -> FaultCandidate:
    """将单条 `RawDetection` 结合昼夜与 ROI 物理度量映射为契约 `FaultCandidate`。"""
    _validate_image_dimensions(image_width, image_height)
    mode = _validate_inference_mode(inference_mode)
    is_baseline = mode == "baseline_pretrained_opencv"
    scene = _normalize_scene_lighting(quality.scene_lighting)
    raw_class = _normalize_raw_class(det.raw_class)
    yolo_conf = _normalize_detector_confidence(det.detector_confidence)
    bbox_px, bbox_norm = _clip_and_normalize_box(det.box, image_width, image_height)
    metrics = det.metrics

    candidate_fault_type: FaultType | None
    visual_signal_state: VisualSignalState
    is_fault_candidate: bool
    severity_hint: SeverityHint
    fusion_status: FusionStatus
    confidence_cap = config.BASELINE_CONFIDENCE_CAP if is_baseline else 1.0
    raw_fused_conf: float
    diagnostic_summary: str

    if raw_class == "streetlight_on":
        if scene in {"night", "twilight"}:
            # 分支 1：D-30 硬契约 — 夜间/黄昏正常亮灯必须回传，不可过滤丢弃
            candidate_fault_type = None
            visual_signal_state = "NORMAL_LIT"
            is_fault_candidate = False
            severity_hint = "none"
            fusion_status = "baseline_heuristic" if is_baseline else "consistent"
            if is_baseline:
                raw_fused_conf = min(confidence_cap, yolo_conf)
            elif (
                metrics.roi_v_p95 >= config.ROI_HIGHLIGHT_V
                and metrics.luminance_contrast >= config.ROI_MIN_LUMINANCE_CONTRAST
            ):
                raw_fused_conf = min(1.0, yolo_conf * 1.05)
            else:
                raw_fused_conf = yolo_conf * 0.92
            diagnostic_summary = (
                f"夜间/低照度正常亮灯参照框（ROI V95={metrics.roi_v_p95:.1f}，"
                f"对比度={metrics.luminance_contrast:.1f}），按 D-30 回传但不计入故障候选"
            )
        else:
            # 分支 2：streetlight_on + day（强高亮或高置信非基线 → 疑似常亮；否则抑制）
            strong_lit = (
                metrics.roi_v_p95 >= config.ROI_HIGHLIGHT_V
                and metrics.luminance_contrast >= config.ROI_MIN_LUMINANCE_CONTRAST
            )
            non_baseline_confident = (not is_baseline) and (yolo_conf >= 0.55)
            if strong_lit or non_baseline_confident:
                candidate_fault_type = "OTHER"
                visual_signal_state = "DAYTIME_ALWAYS_ON_SUSPECT"
                is_fault_candidate = True
                severity_hint = "P4"
                fusion_status = "baseline_heuristic" if is_baseline else "consistent"
                if is_baseline:
                    raw_fused_conf = min(confidence_cap, yolo_conf)
                elif strong_lit:
                    raw_fused_conf = min(1.0, yolo_conf * 1.05)
                else:
                    raw_fused_conf = yolo_conf * 0.95
                diagnostic_summary = (
                    f"白天疑似常亮灯具（ROI V95={metrics.roi_v_p95:.1f}，"
                    f"对比度={metrics.luminance_contrast:.1f}），归入 OTHER（P4），"
                    "请巡查员现场核实"
                )
            else:
                candidate_fault_type = None
                visual_signal_state = "DAYTIME_UNPOWERED_EXPECTED"
                is_fault_candidate = False
                severity_hint = "none"
                fusion_status = "daytime_unpowered_suppressed"
                confidence_cap = min(confidence_cap, config.CONFLICT_CONFIDENCE_CAP)
                raw_fused_conf = min(confidence_cap, yolo_conf)
                diagnostic_summary = (
                    f"白天疑似亮灯但物理高亮特征较弱（ROI V95={metrics.roi_v_p95:.1f}，"
                    f"对比度={metrics.luminance_contrast:.1f}），按环境反光冲突抑制"
                )
    elif raw_class == "streetlight_off":
        if scene in {"night", "twilight"}:
            # 分支 3：streetlight_off + night/twilight（灭灯候选 + 强高亮冲突降权）
            candidate_fault_type = "LAMP_OUT"
            visual_signal_state = "LAMP_OUT"
            is_fault_candidate = True
            severity_hint = "P2"
            strong_highlight_conflict = (
                metrics.roi_v_p95 >= config.ROI_STRONG_HIGHLIGHT_V
                and metrics.luminance_contrast >= config.ROI_STRONG_CONTRAST
            )
            if strong_highlight_conflict:
                fusion_status = "low_confidence_conflict"
                confidence_cap = min(confidence_cap, config.CONFLICT_CONFIDENCE_CAP)
                raw_fused_conf = min(confidence_cap, yolo_conf)
                diagnostic_summary = (
                    f"模型判为灭灯但局部高亮反差显著（ROI V95={metrics.roi_v_p95:.1f}，"
                    f"对比度={metrics.luminance_contrast:.1f}），疑似过曝或邻近反光，请巡查员重点复核"
                )
            else:
                fusion_status = "baseline_heuristic" if is_baseline else "consistent"
                if is_baseline:
                    raw_fused_conf = min(confidence_cap, yolo_conf)
                elif (
                    metrics.roi_v_p95 < config.ROI_HIGHLIGHT_V
                    and metrics.luminance_contrast < config.ROI_MIN_LUMINANCE_CONTRAST
                ):
                    raw_fused_conf = min(1.0, yolo_conf * 1.03)
                else:
                    raw_fused_conf = yolo_conf * 0.95
                diagnostic_summary = (
                    f"夜间/低照度灯头未点亮（ROI V95={metrics.roi_v_p95:.1f}，"
                    f"对比度={metrics.luminance_contrast:.1f}），建议巡查员确认是否上报灭灯故障"
                )
        else:
            # 分支 4：streetlight_off + day（昼夜物理边界抑制）
            candidate_fault_type = None
            visual_signal_state = "DAYTIME_UNPOWERED_EXPECTED"
            is_fault_candidate = False
            severity_hint = "none"
            fusion_status = "daytime_unpowered_suppressed"
            raw_fused_conf = min(confidence_cap, yolo_conf) if is_baseline else yolo_conf
            diagnostic_summary = (
                "白天正常灭灯场景（昼夜物理边界抑制），不计入故障候选；如确需报修请巡查员手动确认"
            )
    elif raw_class == "streetlight_damaged":
        # 分支 5：streetlight_damaged（全天候破损候选）
        candidate_fault_type = "FIXTURE_DAMAGED"
        visual_signal_state = "FIXTURE_DAMAGED"
        is_fault_candidate = True
        severity_hint = "P1" if metrics.edge_density >= 0.25 else "P2"
        fusion_status = "baseline_heuristic" if is_baseline else "consistent"
        if is_baseline:
            raw_fused_conf = min(confidence_cap, yolo_conf)
        elif metrics.edge_density >= 0.15:
            raw_fused_conf = min(1.0, yolo_conf * 1.03)
        else:
            raw_fused_conf = yolo_conf
        diagnostic_summary = (
            f"检测到灯具结构明显破损（边缘密度={metrics.edge_density:.3f}，"
            f"建议等级={severity_hint}），请巡查员确认并优先完成现场电气安全检查"
        )
    else:
        # 分支 6：streetlight_occluded（全天候遮挡候选）
        candidate_fault_type = "OCCLUDED"
        visual_signal_state = "OCCLUDED"
        is_fault_candidate = True
        severity_hint = "P3"
        fusion_status = "baseline_heuristic" if is_baseline else "consistent"
        if is_baseline:
            raw_fused_conf = min(confidence_cap, yolo_conf)
        elif metrics.green_foliage_ratio >= config.OCCLUSION_OVERLAP_MIN:
            raw_fused_conf = min(1.0, yolo_conf * 1.03)
        else:
            raw_fused_conf = yolo_conf
        diagnostic_summary = (
            f"检测到灯具存在遮挡（绿植占比={metrics.green_foliage_ratio:.1%}），"
            "建议巡查员核实并安排修剪或清障"
        )

    validated_fault_type = validate_fault_type(candidate_fault_type)
    fused_conf = min(
        confidence_cap,
        round(max(0.0, min(confidence_cap, raw_fused_conf)), 4),
    )

    roi_out = OpencvRoiMetricsOut(
        roi_v_mean=float(metrics.roi_v_mean),
        roi_v_p95=float(metrics.roi_v_p95),
        bg_ring_v_median=float(metrics.bg_ring_v_median),
        luminance_contrast=float(metrics.luminance_contrast),
        green_foliage_ratio=float(metrics.green_foliage_ratio),
        edge_density=float(metrics.edge_density),
        fusion_status=fusion_status,
    )

    return FaultCandidate(
        candidate_id=candidate_id,
        raw_class=raw_class,
        candidate_fault_type=validated_fault_type,
        visual_signal_state=visual_signal_state,
        is_fault_candidate=is_fault_candidate,
        bbox_norm=bbox_norm,
        bbox_px=bbox_px,
        yolo_confidence=yolo_conf,
        fused_confidence=fused_conf,
        suggested_severity_hint=severity_hint,
        opencv_roi_metrics=roi_out,
        diagnostic_summary=diagnostic_summary,
        requires_inspector_confirmation=True,
        replaces_electrical_safety_check=False,
    )


def build_result(
    detections: Sequence[RawDetection],
    quality: ImageQuality,
    image_width: int,
    image_height: int,
    inference_mode: InferenceMode,
    model_version: str,
    original_image_url: str,
    annotated_image_url: str | None = None,
    analysis_id: str | None = None,
) -> FaultCandidateResult:
    """将整图检测列表汇总构造为 `FaultCandidateResult`（`fault_evidences.ai_result_json`）。"""
    _validate_image_dimensions(image_width, image_height)
    mode = _validate_inference_mode(inference_mode)
    is_baseline = mode == "baseline_pretrained_opencv"

    image_quality_out = ImageQualityOut(
        quality_grade=_normalize_quality_grade(quality.quality_grade),
        laplacian_var=float(quality.laplacian_var),
        brightness_mean_v=float(quality.brightness_mean_v),
        overexposed_ratio=float(quality.overexposed_ratio),
        underexposed_ratio=float(quality.underexposed_ratio),
        scene_lighting=_normalize_scene_lighting(quality.scene_lighting),
        warnings=list(quality.warnings),
    )

    candidates = [
        map_detection(
            det,
            quality,
            image_width=image_width,
            image_height=image_height,
            inference_mode=mode,
            candidate_id=f"cand-{idx:03d}",
        )
        for idx, det in enumerate(detections, start=1)
    ]

    for candidate in candidates:
        validate_fault_type(candidate.candidate_fault_type)

    fault_candidates = [c for c in candidates if c.is_fault_candidate]
    primary_fault_type_suggestion: FaultType | None = None
    if fault_candidates:
        primary = min(
            fault_candidates,
            key=lambda c: (-c.fused_confidence, _SEVERITY_RANK[c.suggested_severity_hint]),
        )
        primary_fault_type_suggestion = validate_fault_type(primary.candidate_fault_type)
        if primary_fault_type_suggestion is None:
            raise VisionError(
                INTERNAL_ERROR,
                f"真故障候选 {primary.candidate_id} 缺少 candidate_fault_type",
                {"candidate_id": primary.candidate_id},
            )

    max_fused_confidence = max((c.fused_confidence for c in candidates), default=0.0)
    resolved_analysis_id = analysis_id if analysis_id is not None else str(uuid.uuid4())

    return FaultCandidateResult(
        analysis_id=resolved_analysis_id,
        inference_mode=mode,
        ai_is_baseline_mode=is_baseline,
        model_version=model_version,
        image_width=image_width,
        image_height=image_height,
        original_image_url=original_image_url,
        annotated_image_url=annotated_image_url,
        image_quality=image_quality_out,
        primary_fault_type_suggestion=primary_fault_type_suggestion,
        max_fused_confidence=max_fused_confidence,
        candidates=candidates,
        requires_inspector_confirmation=True,
        replaces_electrical_safety_check=False,
        disclaimer=DISCLAIMER,
    )
