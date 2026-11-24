"""本地可运行基线检测器：**真实 OpenCV 启发式**，不是硬编码 JSON。

设计意图（`stage1-vision-spec-input.md` §4.4）：
远端 YOLO26 权重就绪前，用纯 OpenCV 形态学 + 连通域分析产出**真实来自像素**的候选框，
让输入白名单 / 物理度量 / 契约 / 标注图这条主干今天就能端到端跑通。

**它不是模型，检出率远低于训练后的 YOLO26**；其定位是「可验证的占位主干」，
一旦 `YOLO_WEIGHTS_PATH` 就绪即被 `tomori/vision/inference.py` 的 YOLO 通道替换。
基线模式对外恒以 `inference_mode="baseline_pretrained_opencv"` +
`ai_is_baseline_mode=true` + `fused_confidence <= 0.65` 暴露，前端会打「仅供参考」标。

算法（每步都是真实计算）：
1. HSV V 通道按场景光照自适应阈值二值化，提取高亮区域 → 候选灯头。
2. 形态学闭运算粘合灯珠碎块，连通域分析取外接框。
3. 按面积占比 / 长短边比 / 位置先验过滤明显非灯头区域。
4. 逐候选算 ROI 物理度量（`tomori.vision.metrics.roi_metrics`），据此判定亮 / 灭。
5. 高植被占比区域 + 暗 ROI → 遮挡候选；高边缘密度 + 暗 ROI → 破损候选。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

import cv2
import numpy as np
from numpy.typing import NDArray

from tomori.vision import config
from tomori.vision.metrics import ImageQuality, RoiMetrics, roi_metrics

_U8 = NDArray[np.uint8]


@dataclass(frozen=True)
class RawDetection:
    """检测器输出的中间结果，尚未经过物理融合决策。"""

    box: tuple[int, int, int, int]  # x1, y1, x2, y2（原图像素）
    raw_class: str
    detector_confidence: float
    metrics: RoiMetrics


def _highlight_mask(hsv: _U8, quality: ImageQuality) -> _U8:
    """按场景光照自适应地提取「明显比环境亮」的区域。

    夜间灯头是画面最亮的部分，阈值取高分位；白天正常亮灯则几乎与背景同亮，
    故用相对阈值（V 分布的高分位）而非固定绝对阈值。
    """
    v = hsv[:, :, config.V_CHANNEL]
    if quality.scene_lighting == "night":
        threshold = max(float(np.percentile(v, 99.0)), 200.0)
    elif quality.scene_lighting == "twilight":
        threshold = max(float(np.percentile(v, 98.5)), 180.0)
    else:  # day
        threshold = max(float(np.percentile(v, 99.5)), 235.0)

    _, mask = cv2.threshold(v, threshold, 255, cv2.THRESH_BINARY)
    mask = cast(_U8, mask)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    return cast(_U8, cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel))


def _plausible_lamp_box(box: tuple[int, int, int, int], image_w: int, image_h: int) -> bool:
    """形态学先验过滤：排除过大的天空/地面块与极端细长条。"""
    x1, y1, x2, y2 = box
    w, h = x2 - x1, y2 - y1
    if w < 6 or h < 6:
        return False
    area_ratio = (w * h) / float(image_w * image_h)
    if area_ratio > 0.12:  # 灯头不会占满画面 12%
        return False
    aspect = max(w, h) / float(min(w, h))
    return aspect <= 6.0  # 排除电线、栏杆等细长目标


def detect(bgr: _U8, quality: ImageQuality) -> list[RawDetection]:
    """真实检测：返回按物理证据分类的候选列表。空列表是合法结果。"""
    hsv = cast(_U8, cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV))
    image_h, image_w = bgr.shape[:2]
    mask = _highlight_mask(hsv, quality)

    count, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    detections: list[RawDetection] = []
    for label in range(1, count):
        x, y, w, h, area = (
            int(stats[label, cv2.CC_STAT_LEFT]),
            int(stats[label, cv2.CC_STAT_TOP]),
            int(stats[label, cv2.CC_STAT_WIDTH]),
            int(stats[label, cv2.CC_STAT_HEIGHT]),
            int(stats[label, cv2.CC_STAT_AREA]),
        )
        if area < 40:  # 噪点
            continue
        box = (x, y, x + w, y + h)
        if not _plausible_lamp_box(box, image_w, image_h):
            continue
        metrics = roi_metrics(bgr, hsv, box)
        detections.append(
            RawDetection(
                box=box,
                raw_class=_classify(metrics, quality),
                detector_confidence=_confidence_from_metrics(metrics, quality),
                metrics=metrics,
            )
        )

    # 亮区只能给出 on/off；破损与遮挡由独立通道补充
    detections.extend(_detect_occluded(bgr, hsv, quality, detections))
    detections.extend(_detect_damaged(bgr, hsv, quality, detections))
    return detections[: config.MAX_FILES_PER_REQUEST * 3]


def _classify(metrics: RoiMetrics, quality: ImageQuality) -> str:
    """依据 ROI 物理度量判定亮 / 灭。白天未满足高亮条件时归 off。

    白天「不亮」是电网断电的正常表现（`DAYTIME_UNPOWERED_EXPECTED`），必须归
    `streetlight_off` 交给 `mapping.py` 显式抑制（`is_fault_candidate=false`），
    不能归 `streetlight_on` —— 否则暗 ROI 被判成「白天常亮嫌疑」，触发
    `DAYTIME_ALWAYS_ON_SUSPECT` 误报。
    """
    lit = (
        metrics.roi_v_p95 >= config.ROI_HIGHLIGHT_V
        and metrics.luminance_contrast >= config.ROI_MIN_LUMINANCE_CONTRAST
    )
    return "streetlight_on" if lit else "streetlight_off"


def _confidence_from_metrics(metrics: RoiMetrics, quality: ImageQuality) -> float:
    """置信度由物理证据强度连续映射，**上限为基线硬顶**。"""
    contrast_score = min(1.0, metrics.luminance_contrast / 120.0)
    highlight_score = min(1.0, metrics.roi_v_p95 / 255.0)
    quality_penalty = {"pass": 0.0, "warn": 0.12, "fail": 0.35}[quality.quality_grade]
    raw = 0.30 + 0.35 * contrast_score + 0.25 * highlight_score - quality_penalty
    return round(max(0.10, min(config.BASELINE_CONFIDENCE_CAP, raw)), 2)


def _detect_occluded(
    bgr: _U8,
    hsv: _U8,
    quality: ImageQuality,
    existing: list[RawDetection],
) -> list[RawDetection]:
    """遮挡候选：高绿植占比的区域。真实来自 HSV 色相分布。"""
    lower = np.array([config.FOLIAGE_HUE_MIN, config.FOLIAGE_SAT_MIN, config.FOLIAGE_VAL_MIN])
    upper = np.array([config.FOLIAGE_HUE_MAX, 255, 255])
    foliage = cv2.inRange(hsv, lower, upper)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
    foliage = cv2.morphologyEx(foliage, cv2.MORPH_CLOSE, kernel)

    image_h, image_w = bgr.shape[:2]
    count, _, stats, _ = cv2.connectedComponentsWithStats(foliage, connectivity=8)
    results: list[RawDetection] = []
    for label in range(1, count):
        x, y, w, h, area = (
            int(stats[label, cv2.CC_STAT_LEFT]),
            int(stats[label, cv2.CC_STAT_TOP]),
            int(stats[label, cv2.CC_STAT_WIDTH]),
            int(stats[label, cv2.CC_STAT_HEIGHT]),
            int(stats[label, cv2.CC_STAT_AREA]),
        )
        if area < 800:
            continue
        box = (x, y, x + w, y + h)
        if not _plausible_lamp_box(box, image_w, image_h):
            continue
        if _overlaps_any(box, existing):
            continue
        metrics = roi_metrics(bgr, hsv, box)
        if metrics.green_foliage_ratio < config.OCCLUSION_OVERLAP_MIN:
            continue
        results.append(
            RawDetection(
                box=box,
                raw_class="streetlight_occluded",
                detector_confidence=round(
                    min(config.BASELINE_CONFIDENCE_CAP, 0.35 + metrics.green_foliage_ratio), 2
                ),
                metrics=metrics,
            )
        )
    return results[:2]


def _detect_damaged(
    bgr: _U8,
    hsv: _U8,
    quality: ImageQuality,
    existing: list[RawDetection],
) -> list[RawDetection]:
    """破损候选：高边缘密度 + 低亮度的局部区域（碎裂/脱落灯罩的物理表征）。"""
    gray = cast(_U8, cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY))
    edges = cast(_U8, cv2.Canny(gray, 60, 160))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
    edges_dense = cast(_U8, cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel))

    image_h, image_w = bgr.shape[:2]
    count, _, stats, _ = cv2.connectedComponentsWithStats(edges_dense, connectivity=8)
    results: list[RawDetection] = []
    for label in range(1, count):
        x, y, w, h, area = (
            int(stats[label, cv2.CC_STAT_LEFT]),
            int(stats[label, cv2.CC_STAT_TOP]),
            int(stats[label, cv2.CC_STAT_WIDTH]),
            int(stats[label, cv2.CC_STAT_HEIGHT]),
            int(stats[label, cv2.CC_STAT_AREA]),
        )
        if area < 1200:
            continue
        box = (x, y, x + w, y + h)
        if not _plausible_lamp_box(box, image_w, image_h):
            continue
        if _overlaps_any(box, existing):
            continue
        metrics = roi_metrics(bgr, hsv, box)
        if metrics.edge_density < 0.10:
            continue
        results.append(
            RawDetection(
                box=box,
                raw_class="streetlight_damaged",
                detector_confidence=round(
                    min(config.BASELINE_CONFIDENCE_CAP, 0.30 + metrics.edge_density * 2.0), 2
                ),
                metrics=metrics,
            )
        )
    return results[:2]


def _overlaps_any(box: tuple[int, int, int, int], existing: list[RawDetection]) -> bool:
    return any(_overlap_ratio(box, det.box) >= config.OCCLUSION_OVERLAP_MIN for det in existing)


def _overlap_ratio(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    """交并面积 / min(面积) —— 与 §1.3 遮挡 IoU 定义一致。"""
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    if ix2 <= ix1 or iy2 <= iy1:
        return 0.0
    inter = (ix2 - ix1) * (iy2 - iy1)
    area_a = max(1, (ax2 - ax1) * (ay2 - ay1))
    area_b = max(1, (bx2 - bx1) * (by2 - by1))
    return inter / float(min(area_a, area_b))
