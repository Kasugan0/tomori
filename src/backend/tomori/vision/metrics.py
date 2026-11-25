"""OpenCV 物理度量：清晰度、亮度分布、昼夜判定、ROI 发光验算、植被占比。

所有数值均由真实像素计算得出，**不存在任何硬编码的指标常量**。
阈值集中在 `tomori.vision.config`，全部为工程初值，待真实巡查样本实测标定。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final, cast

import cv2
import numpy as np
from numpy.typing import NDArray

from tomori.vision import config

V_CHANNEL: Final = config.V_CHANNEL  # HSV 中 V 的下标

_U8 = NDArray[np.uint8]


@dataclass(frozen=True)
class ImageQuality:
    quality_grade: str
    laplacian_var: float
    brightness_mean_v: float
    overexposed_ratio: float
    underexposed_ratio: float
    scene_lighting: str
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RoiMetrics:
    roi_v_mean: float
    roi_v_p95: float
    bg_ring_v_median: float
    luminance_contrast: float
    green_foliage_ratio: float
    edge_density: float
    roi_saturated_ratio: float


def to_hsv(bgr: _U8) -> _U8:
    return cast(_U8, cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV))


def laplacian_variance(bgr: _U8) -> float:
    """清晰度：灰度图拉普拉斯响应的方差。越低越模糊。"""
    gray = cast(_U8, cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY))
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def scene_lighting(hsv: _U8) -> str:
    """昼夜判定：整图 V 中位数 + 顶部 1/3 天空区域 V 中位数。"""
    v = hsv[:, :, V_CHANNEL]
    v_median = float(np.median(v))
    sky_rows = max(1, v.shape[0] // 3)
    sky_v_median = float(np.median(v[:sky_rows, :]))
    if v_median >= config.DAY_V_MEDIAN or sky_v_median >= config.DAY_SKY_V_MEDIAN:
        return "day"
    if v_median <= config.NIGHT_V_MEDIAN and sky_v_median <= config.NIGHT_SKY_V_MEDIAN:
        return "night"
    return "twilight"


def _is_nightish(scene: str) -> bool:
    return scene in {"night", "twilight"}


def roi_metrics(bgr: _U8, hsv: _U8, box: tuple[int, int, int, int]) -> RoiMetrics:
    """灯头 ROI 发光物理验算：ROI 内 V 分位数 vs 外扩 1.5× 背景环 V 中位数。"""
    height, width = bgr.shape[:2]
    x1, y1, x2, y2 = box
    x1 = max(0, min(x1, width - 1))
    y1 = max(0, min(y1, height - 1))
    x2 = max(x1 + 1, min(x2, width))
    y2 = max(y1 + 1, min(y2, height))

    v = hsv[:, :, V_CHANNEL]
    roi_v = v[y1:y2, x1:x2]
    roi_v_p95 = float(np.percentile(roi_v, 95))
    roi_v_mean = float(np.mean(roi_v))
    roi_saturated_ratio = float(np.mean(roi_v >= config.ROI_SATURATED_V))

    # 外扩 1.5× 的框，扣掉内部 ROI 得到背景环
    cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
    half_w, half_h = (x2 - x1) * 0.75, (y2 - y1) * 0.75
    ox1 = max(0, int(cx - half_w))
    oy1 = max(0, int(cy - half_h))
    ox2 = min(width, int(cx + half_w))
    oy2 = min(height, int(cy + half_h))
    outer_v = v[oy1:oy2, ox1:ox2]
    outer_mask = np.ones(outer_v.shape, dtype=bool)
    inner_x1, inner_y1 = x1 - ox1, y1 - oy1
    inner_x2, inner_y2 = inner_x1 + (x2 - x1), inner_y1 + (y2 - y1)
    inner_x1 = max(0, inner_x1)
    inner_y1 = max(0, inner_y1)
    inner_x2 = min(outer_v.shape[1], inner_x2)
    inner_y2 = min(outer_v.shape[0], inner_y2)
    outer_mask[inner_y1:inner_y2, inner_x1:inner_x2] = False
    bg_values = outer_v[outer_mask]
    bg_ring_v_median = float(np.median(bg_values)) if bg_values.size else float(np.median(v))

    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    roi_gray = gray[y1:y2, x1:x2]
    edges = cv2.Canny(roi_gray, 50, 150)
    edge_density = float(np.count_nonzero(edges) / edges.size)

    return RoiMetrics(
        roi_v_mean=round(roi_v_mean, 2),
        roi_v_p95=round(roi_v_p95, 2),
        bg_ring_v_median=round(bg_ring_v_median, 2),
        luminance_contrast=round(roi_v_p95 - bg_ring_v_median, 2),
        green_foliage_ratio=round(green_foliage_ratio(hsv, (x1, y1, x2, y2)), 4),
        edge_density=round(edge_density, 4),
        roi_saturated_ratio=round(roi_saturated_ratio, 4),
    )


def green_foliage_ratio(hsv: _U8, box: tuple[int, int, int, int]) -> float:
    """ROI 内绿色植被像素占比 —— 树枝遮挡的物理证据。"""
    x1, y1, x2, y2 = box
    roi = hsv[y1:y2, x1:x2]
    if roi.size == 0:
        return 0.0
    lower = np.array([config.FOLIAGE_HUE_MIN, config.FOLIAGE_SAT_MIN, config.FOLIAGE_VAL_MIN])
    upper = np.array([config.FOLIAGE_HUE_MAX, 255, 255])
    mask = cv2.inRange(roi, lower, upper)
    return float(np.count_nonzero(mask) / mask.size)


def assess_quality(bgr: _U8, hsv: _U8) -> ImageQuality:
    """图像质量预检：失焦 / 全黑 / 过曝 → pass / warn / fail。"""
    v = hsv[:, :, V_CHANNEL]
    total = v.size
    overexposed_ratio = float(np.count_nonzero(v >= config.OVEREXPOSED_V) / total)
    underexposed_ratio = float(np.count_nonzero(v <= config.UNDEREXPOSED_V) / total)
    lap = laplacian_variance(bgr)
    scene = scene_lighting(hsv)

    warnings_out: list[str] = []
    grade = "pass"

    if lap < config.LAPLACIAN_BLUR_FAIL:
        grade = "fail"
        warnings_out.append(
            f"图像严重失焦或过低纹理（拉普拉斯方差 {lap:.2f} < "
            f"{config.LAPLACIAN_BLUR_FAIL}），无法可靠识别"
        )
    elif lap < config.LAPLACIAN_BLUR_WARN:
        grade = "warn"
        warnings_out.append(
            f"图像偏模糊（拉普拉斯方差 {lap:.2f} < {config.LAPLACIAN_BLUR_WARN}），建议重拍"
        )

    if overexposed_ratio > config.OVEREXPOSED_RATIO_FAIL:
        grade = "fail"
        warnings_out.append(
            f"过曝像素占比 {overexposed_ratio:.1%} 超过 "
            f"{config.OVEREXPOSED_RATIO_FAIL:.0%}，灯头细节不可判读"
        )
    elif overexposed_ratio > config.OVEREXPOSED_RATIO_WARN:
        if grade == "pass":
            grade = "warn"
        warnings_out.append(f"存在过曝区域（{overexposed_ratio:.1%}），灯头状态可能受影响")

    if underexposed_ratio > config.UNDEREXPOSED_RATIO_FAIL:
        grade = "fail"
        warnings_out.append(
            f"欠曝像素占比 {underexposed_ratio:.1%} 超过 "
            f"{config.UNDEREXPOSED_RATIO_FAIL:.0%}，画面接近全黑"
        )
    elif underexposed_ratio > config.UNDEREXPOSED_RATIO_WARN:
        if grade == "pass":
            grade = "warn"
        warnings_out.append(f"画面整体偏暗（欠曝 {underexposed_ratio:.1%}），夜间巡查属正常范围")

    return ImageQuality(
        quality_grade=grade,
        laplacian_var=round(lap, 2),
        brightness_mean_v=round(float(np.mean(v)), 2),
        overexposed_ratio=round(overexposed_ratio, 4),
        underexposed_ratio=round(underexposed_ratio, 4),
        scene_lighting=scene,
        warnings=warnings_out,
    )


def is_nightish(scene: str) -> bool:
    return _is_nightish(scene)
