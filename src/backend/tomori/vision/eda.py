"""数据集探索性分析（EDA）：分辨率/长宽比/框尺度/类别/亮度/分组规模的只读统计。

用途：回答「为什么 `imgsz` 选 `1024` 而不是 `1280`」这类必须用真实数据支撑的问题。
在远端只读扫描已落盘数据集，产出 Markdown 报告 + JSON 摘要。

**只读保证**：本模块只做 `Path.iterdir()` / `read_text()` / `read_bytes()` 与
`cv2.imdecode` 内存解码，不创建、不修改、不删除任何数据集文件。

**D1 延迟导入纪律**：模块顶层仅导入标准库；`cv2` / `numpy` / `PIL` / `tomori.vision.config`
全部在函数体内延迟导入，因此在仅装了 `opencv-python-headless` + `pillow` 的远端 venv
（无需本仓库包）也能以 `python -` 直接执行。
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

IMAGE_SUFFIXES: Final[frozenset[str]] = frozenset({".jpg", ".jpeg", ".png", ".webp"})
VIDEO_SUFFIXES: Final[frozenset[str]] = frozenset(
    {".mp4", ".mov", ".avi", ".mkv", ".flv", ".webm", ".m4v", ".ts", ".mpg", ".mpeg", ".wmv"}
)

# COCO 目标尺度分档（像素面积）
COCO_SMALL_MAX_AREA: Final[int] = 32 * 32
COCO_MEDIUM_MAX_AREA: Final[int] = 96 * 96


@dataclass(frozen=True)
class DatasetFacts:
    """单个数据集的探测结果（含相对路径清单，用于后续统计）。"""

    name: str
    root: Path
    images: tuple[Path, ...]
    videos: tuple[Path, ...]
    label_dirs: tuple[Path, ...]
    metadata_csv: Path | None
    data_yaml: Path | None
    prep_manifest_json: Path | None = None


def _percentiles(values: Sequence[float], points: Sequence[float]) -> dict[str, float]:
    """线性插值分位数（纯标准库实现，避免引入 numpy 依赖到模块顶层）。"""
    if not values:
        return {f"p{int(p * 100)}": 0.0 for p in points}
    ordered = sorted(values)
    n = len(ordered)
    out: dict[str, float] = {}
    for p in points:
        if n == 1:
            out[f"p{int(p * 100)}"] = float(ordered[0])
            continue
        pos = p * (n - 1)
        lo = int(math.floor(pos))
        hi = min(lo + 1, n - 1)
        frac = pos - lo
        out[f"p{int(p * 100)}"] = round(
            float(ordered[lo]) * (1.0 - frac) + float(ordered[hi]) * frac, 2
        )
    return out


def _dist_summary(values: Sequence[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0}
    return {
        "count": len(values),
        "min": round(float(min(values)), 2),
        "median": _percentiles(values, (0.5,))["p50"],
        "mean": round(sum(values) / len(values), 2),
        "p95": _percentiles(values, (0.95,))["p95"],
        "max": round(float(max(values)), 2),
    }


def _is_lfs_pointer(path: Path) -> bool:
    try:
        if path.stat().st_size >= 512:
            return False
        return path.read_bytes()[:32].startswith(b"version https://git-lfs")
    except OSError:
        return False


def discover_dataset(name: str, root: Path) -> DatasetFacts:
    """只读扫描一个数据集根目录，收集图片/视频/标注目录清单（跳过 .git 与 LFS 指针）。"""
    images: list[Path] = []
    videos: list[Path] = []
    label_dirs: list[Path] = []
    for path in sorted(root.rglob("*")):
        rel_parts = path.relative_to(root).parts
        if any(part.startswith(".") for part in rel_parts):
            continue
        if path.is_dir():
            if path.name.lower() in {"labels", "annotations"}:
                label_dirs.append(path)
            continue
        suffix = path.suffix.lower()
        if suffix in VIDEO_SUFFIXES:
            videos.append(path)
        elif suffix in IMAGE_SUFFIXES and not _is_lfs_pointer(path):
            images.append(path)
    metadata_csv = root / "metadata.csv"
    data_yaml = root / "data.yaml"
    prep_manifest = root / "prep_manifest.json"
    return DatasetFacts(
        name=name,
        root=root,
        images=tuple(images),
        videos=tuple(videos),
        label_dirs=tuple(label_dirs),
        metadata_csv=metadata_csv if metadata_csv.is_file() else None,
        data_yaml=data_yaml if data_yaml.is_file() else None,
        prep_manifest_json=prep_manifest if prep_manifest.is_file() else None,
    )


def _read_image_header(path: Path) -> tuple[int, int, int] | None:
    """只读图像头获取 EXIF 转正后宽高与方向 `(upright_w, upright_h, ori)`，失败返回 None。"""
    Image: Any = __import__("PIL.Image", fromlist=["Image"])
    try:
        with Image.open(path) as probe:
            w, h = int(probe.size[0]), int(probe.size[1])
            ori = int(probe.getexif().get(274, 1) or 1)
            if ori in {5, 6, 7, 8}:
                return h, w, ori
            return w, h, ori
    except Exception:
        return None


def _brightness_stats(path: Path) -> tuple[float, float, float] | None:
    """解码图像并返回 `(whole_v_mean, whole_v_median, top_third_sky_v_median)`。"""
    cv2: Any = __import__("cv2")
    np: Any = __import__("numpy")
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if not raw:
        return None
    img = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None or img.size == 0:
        return None
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    v = hsv[:, :, 2]
    sky_rows = max(1, v.shape[0] // 3)
    return (
        round(float(v.mean()), 2),
        round(float(np.median(v)), 2),
        round(float(np.median(v[:sky_rows, :])), 2),
    )


def _scene_lighting(v_median: float, sky_v_median: float) -> str:
    """对齐 `tomori.vision.metrics.scene_lighting` 昼夜判定工程初值。"""
    if v_median >= 115.0 or sky_v_median >= 140.0:
        return "day"
    if v_median <= 65.0 and sky_v_median <= 75.0:
        return "night"
    return "twilight"


def _parse_yolo_label_file(path: Path) -> list[tuple[int, float, float, float, float]]:
    """支持 YOLO 5 列框与分割多边形（转外接矩形 `[cls, xc, yc, bw, bh]`）."""
    boxes: list[tuple[int, float, float, float, float]] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return boxes
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        try:
            cls_id = int(float(parts[0]))
            if len(parts) == 5:
                boxes.append(
                    (
                        cls_id,
                        float(parts[1]),
                        float(parts[2]),
                        float(parts[3]),
                        float(parts[4]),
                    )
                )
            elif len(parts) >= 7 and (len(parts) - 1) % 2 == 0:
                coords = [float(v) for v in parts[1:]]
                xs, ys = coords[0::2], coords[1::2]
                xmin, xmax = min(xs), max(xs)
                ymin, ymax = min(ys), max(ys)
                boxes.append(
                    (
                        cls_id,
                        0.5 * (xmin + xmax),
                        0.5 * (ymin + ymax),
                        xmax - xmin,
                        ymax - ymin,
                    )
                )
        except ValueError:
            continue
    return boxes


def _read_metadata_csv(path: Path) -> list[dict[str, str]]:
    """读取 D1 `metadata.csv`；注意该文件带 UTF-8 BOM，必须用 `utf-8-sig`。"""
    import csv

    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return [dict(row) for row in csv.DictReader(fh)]


def analyze_dataset(
    facts: DatasetFacts,
    *,
    brightness_sample_size: int = 600,
    seed: int = 42,
    class_names: Sequence[str] = (),
) -> dict[str, Any]:
    """对单个数据集做分辨率/体积/框尺度/类别/亮度/分组规模全量统计（只读）。"""
    rng = random.Random(seed)
    report: dict[str, Any] = {
        "name": facts.name,
        "root": str(facts.root),
        "image_count": len(facts.images),
        "video_count": len(facts.videos),
        "video_samples": [str(p.relative_to(facts.root)) for p in facts.videos[:20]],
    }

    # --- 1. 分辨率 / 长宽比 / 文件体积 / EXIF ---
    widths: list[float] = []
    heights: list[float] = []
    aspects: list[float] = []
    byte_sizes: list[float] = []
    resolution_counter: Counter[str] = Counter()
    exif_ori_counter: Counter[int] = Counter()
    per_image_size: dict[str, tuple[int, int]] = {}
    header_failures = 0
    for path in facts.images:
        header = _read_image_header(path)
        if header is None:
            header_failures += 1
            continue
        width, height, ori = header
        per_image_size[path.stem] = (width, height)
        exif_ori_counter[ori] += 1
        widths.append(width)
        heights.append(height)
        aspects.append(round(width / height, 4) if height else 0.0)
        byte_sizes.append(float(path.stat().st_size))
        resolution_counter[f"{width}x{height}"] += 1

    total_measured = len(widths)
    report["resolution"] = {
        "measured_images": total_measured,
        "header_failures": header_failures,
        "exif_orientation_counts": {str(k): v for k, v in sorted(exif_ori_counter.items())},
        "width": _dist_summary(widths),
        "height": _dist_summary(heights),
        "aspect_ratio": _dist_summary(aspects),
        "file_bytes": _dist_summary(byte_sizes),
        "top_resolutions": resolution_counter.most_common(10),
        "exactly_1280x720": resolution_counter.get("1280x720", 0),
        "exactly_1280x720_ratio": round(resolution_counter.get("1280x720", 0) / total_measured, 4)
        if total_measured
        else 0.0,
        "width_ge_1280_ratio": round(sum(1 for w in widths if w >= 1280) / total_measured, 4)
        if total_measured
        else 0.0,
        "width_gt_1024_ratio": round(sum(1 for w in widths if w > 1024) / total_measured, 4)
        if total_measured
        else 0.0,
        "width_le_1024_ratio": round(sum(1 for w in widths if w <= 1024) / total_measured, 4)
        if total_measured
        else 0.0,
    }

    # --- 2. 标注框尺度（遍历所有子目录 *.txt，按实际图像宽高还原为像素） ---
    box_px_w: list[float] = []
    box_px_h: list[float] = []
    box_img_dims: list[tuple[int, int]] = []
    box_area_px: list[float] = []
    box_rel_area: list[float] = []
    boxes_per_image: list[float] = []
    class_box_counter: Counter[int] = Counter()
    class_image_counter: Counter[int] = Counter()
    coco_buckets: Counter[str] = Counter()
    label_files = 0
    for label_dir in facts.label_dirs:
        for lbl in sorted(label_dir.rglob("*.txt")):
            label_files += 1
            boxes = _parse_yolo_label_file(lbl)
            boxes_per_image.append(float(len(boxes)))
            if not boxes:
                continue
            size = per_image_size.get(lbl.stem)
            if size is None:
                continue
            img_w, img_h = size
            present: set[int] = set()
            for cls_id, _xc, _yc, bw, bh in boxes:
                px_w, px_h = bw * img_w, bh * img_h
                area = px_w * px_h
                box_px_w.append(px_w)
                box_px_h.append(px_h)
                box_img_dims.append((img_w, img_h))
                box_area_px.append(area)
                box_rel_area.append(area / float(img_w * img_h) if img_w and img_h else 0.0)
                class_box_counter[cls_id] += 1
                present.add(cls_id)
                if area < COCO_SMALL_MAX_AREA:
                    coco_buckets["tiny(<32^2)"] += 1
                elif area < COCO_MEDIUM_MAX_AREA:
                    coco_buckets["small(32^2..96^2)"] += 1
                elif area < 96 * 96 * 4:
                    coco_buckets["medium(<192^2)"] += 1
                else:
                    coco_buckets["large(>=192^2)"] += 1
            for cls_id in present:
                class_image_counter[cls_id] += 1

    total_boxes = len(box_area_px)
    report["boxes"] = {
        "label_files": label_files,
        "total_boxes": total_boxes,
        "boxes_per_image": _dist_summary(boxes_per_image),
        "pixel_width": _dist_summary(box_px_w),
        "pixel_height": _dist_summary(box_px_h),
        "pixel_area": _dist_summary(box_area_px),
        "relative_area": _dist_summary(box_rel_area),
        "relative_area_lt_0.1pct": round(sum(1 for r in box_rel_area if r < 0.001) / total_boxes, 4)
        if total_boxes
        else 0.0,
        "relative_area_lt_0.5pct": round(sum(1 for r in box_rel_area if r < 0.005) / total_boxes, 4)
        if total_boxes
        else 0.0,
        "relative_area_lt_2pct": round(sum(1 for r in box_rel_area if r < 0.02) / total_boxes, 4)
        if total_boxes
        else 0.0,
        "coco_scale_buckets": dict(coco_buckets),
        "class_box_counts": {
            (class_names[c] if 0 <= c < len(class_names) else f"class_{c}"): n
            for c, n in sorted(class_box_counter.items())
        },
        "class_image_counts": {
            (class_names[c] if 0 <= c < len(class_names) else f"class_{c}"): n
            for c, n in sorted(class_image_counter.items())
        },
    }

    # --- 3. 昼夜亮度分布（全量或抽样解码 V 通道，<=0 表示全量） ---
    sample = list(facts.images)
    if brightness_sample_size > 0 and len(sample) > brightness_sample_size:
        sample = rng.sample(sample, brightness_sample_size)
    v_means: list[float] = []
    v_medians: list[float] = []
    sky_v_medians: list[float] = []
    lighting_counter: Counter[str] = Counter()
    for path in sample:
        stats = _brightness_stats(path)
        if stats is None:
            continue
        v_mean, v_med, sky_med = stats
        v_means.append(v_mean)
        v_medians.append(v_med)
        sky_v_medians.append(sky_med)
        lighting_counter[_scene_lighting(v_med, sky_med)] += 1
    report["brightness_v"] = {
        "sampled_images": len(v_means),
        "v_mean_distribution": _dist_summary(v_means),
        "v_median_distribution": _dist_summary(v_medians),
        "sky_v_median_distribution": _dist_summary(sky_v_medians),
        "scene_lighting_counts": dict(lighting_counter),
    }

    # --- 4. D1 式 `metadata.csv` 分组规模分布 ---
    if facts.metadata_csv is not None:
        rows = _read_metadata_csv(facts.metadata_csv)
        video_counter: Counter[str] = Counter()
        labels_present_counter: Counter[str] = Counter()
        num_objects: list[float] = []
        for row in rows:
            vid = (row.get("video_id") or "").strip()
            if vid:
                video_counter[vid] += 1
            lp = (row.get("labels_present") or "").strip()
            if lp:
                labels_present_counter[lp] += 1
            try:
                num_objects.append(float(row.get("num_objects") or 0))
            except ValueError:
                continue
        group_sizes = [float(n) for n in video_counter.values()]
        report["groups"] = {
            "metadata_rows": len(rows),
            "video_id_count": len(video_counter),
            "video_id_frame_counts": dict(sorted(video_counter.items())),
            "video_group_size": _dist_summary(group_sizes),
            "labels_present_distribution": dict(labels_present_counter),
            "num_objects_per_image": _dist_summary(num_objects),
        }

    # --- 5. 多源合并后 `prep_manifest.json` 摘要（来源占比 / 分片分布 / 防泄漏校验） ---
    if facts.prep_manifest_json is not None:
        try:
            manifest = json.loads(facts.prep_manifest_json.read_text(encoding="utf-8"))
            if isinstance(manifest, dict):
                report["merged_manifest"] = {
                    "etl_funnel_totals": manifest.get("etl_funnel_totals", {}),
                    "splits": manifest.get("splits", {}),
                    "leakage_checks": manifest.get("leakage_checks", {}),
                    "sources": [
                        {
                            "name": s.get("name"),
                            "detected_format": s.get("detected_format"),
                            "raw_images_found": s.get("raw_images_found"),
                            "kept_images": s.get("kept_images"),
                            "kept_boxes_total": s.get("kept_boxes_total"),
                            "dropped_classes_counts": s.get("dropped_classes_counts", {}),
                        }
                        for s in manifest.get("sources", [])
                        if isinstance(s, dict)
                    ],
                }
        except (OSError, json.JSONDecodeError):
            pass
    return report


def _imgsz_small_object_retention(
    box_px_w: Sequence[float],
    box_px_h: Sequence[float],
    imgsz: int,
    native_dims: Sequence[tuple[int, int]] | None = None,
    default_w: int = 1280,
    default_h: int = 720,
    p3_stride: int = 8,
) -> dict[str, Any]:
    """按每张图实际 `(img_w, img_h)` 计算 `imgsz` letterbox 下特征图格数与退化比例."""
    below_1cell = 0
    below_2cells = 0
    below_3cells = 0
    resized_coco_tiny = 0
    resized_min_sides: list[float] = []
    scales: list[float] = []
    dims = native_dims if native_dims is not None else [(default_w, default_h)] * len(box_px_w)
    for w, h, (nw, nh) in zip(box_px_w, box_px_h, dims, strict=True):
        scale = min(imgsz / float(max(1, nw)), imgsz / float(max(1, nh)))
        scales.append(scale)
        rw, rh = w * scale, h * scale
        min_side = min(rw, rh)
        resized_min_sides.append(min_side)
        if min_side < p3_stride:
            below_1cell += 1
        if min_side < p3_stride * 2:
            below_2cells += 1
        if min_side < p3_stride * 3:
            below_3cells += 1
        if rw * rh < COCO_SMALL_MAX_AREA:
            resized_coco_tiny += 1
    n = len(box_px_w)
    mean_scale = (sum(scales) / n) if n else min(imgsz / float(default_w), imgsz / float(default_h))
    # 原生像素信息保留率上限 1.0（上采样不新增原生像素信息，只反映下采样损失）
    mean_kept = (
        (sum(min(1.0, s * s) for s in scales) / n) if n else min(1.0, mean_scale * mean_scale)
    )
    mode_scale = min(imgsz / float(max(1, default_w)), imgsz / float(max(1, default_h)))
    mode_kept = min(1.0, mode_scale * mode_scale)
    downsampled_boxes = sum(1 for s in scales if s < 1.0 - 1e-6)
    upscaled_boxes = sum(1 for s in scales if s > 1.0 + 1e-6)
    return {
        "imgsz": imgsz,
        "mode_resolution": f"{default_w}x{default_h}",
        "mode_letterbox_scale": round(mode_scale, 4),
        "mode_native_pixels_kept": round(mode_kept, 4),
        "mode_resized": f"{round(default_w * mode_scale)}x{round(default_h * mode_scale)}",
        "letterbox_scale": round(mean_scale, 4),
        "native_pixels_kept": round(mean_kept, 4),
        "downsampled_boxes": downsampled_boxes,
        "downsampled_boxes_ratio": round(downsampled_boxes / n, 4) if n else 0.0,
        "upscaled_boxes": upscaled_boxes,
        "upscaled_boxes_ratio": round(upscaled_boxes / n, 4) if n else 0.0,
        "p3_feature_map": f"{imgsz // p3_stride}x{imgsz // p3_stride}",
        "one_to_many_anchors": (imgsz // 8) ** 2 + (imgsz // 16) ** 2 + (imgsz // 32) ** 2,
        "resized_min_side_px": _dist_summary(resized_min_sides),
        "boxes_below_one_p3_cell_lt_8px": below_1cell,
        "boxes_below_one_p3_cell_ratio": round(below_1cell / n, 4) if n else 0.0,
        "boxes_below_two_p3_cells_lt_16px": below_2cells,
        "boxes_below_two_p3_cells_ratio": round(below_2cells / n, 4) if n else 0.0,
        "boxes_below_three_p3_cells_lt_24px": below_3cells,
        "boxes_below_three_p3_cells_ratio": round(below_3cells / n, 4) if n else 0.0,
        "resized_coco_tiny_lt_32x32": resized_coco_tiny,
        "resized_coco_tiny_ratio": round(resized_coco_tiny / n, 4) if n else 0.0,
    }


def _render_markdown(report: dict[str, Any]) -> str:
    """把 EDA JSON 渲染为可直接引用的 Markdown 报告。"""
    lines: list[str] = []
    gen = report["generated_at"]
    lines.append("# 数据集探索性分析（EDA）报告")
    lines.append("")
    lines.append(f"> 生成时间：`{gen}`｜只读扫描，未修改任何数据集文件。")
    lines.append(">")
    lines.append("> 采集口径：分辨率/长宽比/体积来自全量图像**头信息只读**（不解码像素）；")
    lines.append("> 框尺度来自全量 YOLO `.txt` 标注 × 对应图像真实宽高；")
    lines.append("> `V` 通道亮度为**抽样解码**，样本量见各表。")
    lines.append("")

    for ds in report["datasets"]:
        lines.append(f"## {ds['name']}")
        lines.append("")
        lines.append(f"- 根目录：`{ds['root']}`")
        lines.append(f"- 静态图片：**{ds['image_count']}** 张｜视频：**{ds['video_count']}** 个")
        res = ds["resolution"]
        lines.append(
            f"- 分辨率实测：{res['measured_images']} 张成功读头，失败 {res['header_failures']} 张"
            f"｜EXIF 方向：`{res.get('exif_orientation_counts', {})}`"
        )
        lines.append("")
        lines.append("### 分辨率与体积")
        lines.append("")
        lines.append("| 维度 | min | median | mean | p95 | max |")
        lines.append("|---|---:|---:|---:|---:|---:|")
        for label, key in (("宽 (px)", "width"), ("高 (px)", "height"), ("长宽比", "aspect_ratio")):
            d = res[key]
            lines.append(
                f"| {label} | {d['min']} | {d['median']} | {d['mean']} | {d['p95']} | {d['max']} |"
            )
        fb = res["file_bytes"]
        lines.append(
            f"| 体积 (B) | {fb['min']} | {fb['median']} | "
            f"{fb['mean']} | {fb['p95']} | {fb['max']} |"
        )
        lines.append("")
        lines.append("| 关键占比 | 数值 |")
        lines.append("|---|---:|")
        lines.append(
            f"| 恰好 `1280x720` | {res['exactly_1280x720']} "
            f"（{res['exactly_1280x720_ratio']:.2%}） |"
        )
        lines.append(f"| 宽度 `>= 1280` | {res['width_ge_1280_ratio']:.2%} |")
        lines.append(f"| 宽度 `> 1024` | {res['width_gt_1024_ratio']:.2%} |")
        lines.append(f"| 宽度 `<= 1024` | {res['width_le_1024_ratio']:.2%} |")
        lines.append("")
        if res["top_resolutions"]:
            lines.append("Top 分辨率：")
            lines.append("")
            lines.append("| 分辨率 | 张数 |")
            lines.append("|---|---:|")
            for reso, cnt in res["top_resolutions"]:
                lines.append(f"| `{reso}` | {cnt} |")
            lines.append("")

        bx = ds["boxes"]
        if bx["total_boxes"]:
            lines.append("### 标注框尺度")
            lines.append("")
            lines.append(f"标注文件 **{bx['label_files']}** 个｜框总数 **{bx['total_boxes']}** 个")
            lines.append("")
            lines.append("| 维度 | min | median | mean | p95 | max |")
            lines.append("|---|---:|---:|---:|---:|---:|")
            for label, key in (("框宽 (px)", "pixel_width"), ("框高 (px)", "pixel_height")):
                d = bx[key]
                lines.append(
                    f"| {label} | {d['min']} | {d['median']} | "
                    f"{d['mean']} | {d['p95']} | {d['max']} |"
                )
            for label, key in (("框面积 (px²)", "pixel_area"), ("框相对面积", "relative_area")):
                d = bx[key]
                lines.append(
                    f"| {label} | {d['min']} | {d['median']} | "
                    f"{d['mean']} | {d['p95']} | {d['max']} |"
                )
            lines.append("")
            lines.append("| 相对面积阈值 | 框占比 |")
            lines.append("|---|---:|")
            lines.append(f"| `< 0.1%` | {bx['relative_area_lt_0.1pct']:.2%} |")
            lines.append(f"| `< 0.5%` | {bx['relative_area_lt_0.5pct']:.2%} |")
            lines.append(f"| `< 2%` | {bx['relative_area_lt_2pct']:.2%} |")
            lines.append("")
            lines.append("| COCO 尺度桶 | 框数 |")
            lines.append("|---|---:|")
            for bucket, cnt in bx["coco_scale_buckets"].items():
                lines.append(f"| `{bucket}` | {cnt} |")
            lines.append("")
            lines.append("| 类别 | 框数 | 出现图数 |")
            lines.append("|---|---:|---:|")
            for cname, cnt in bx["class_box_counts"].items():
                img_cnt = bx["class_image_counts"].get(cname, 0)
                lines.append(f"| `{cname}` | {cnt} | {img_cnt} |")
            lines.append("")
            bpi = bx["boxes_per_image"]
            lines.append(
                f"每图框数：min `{bpi['min']}` / median `{bpi['median']}` / "
                f"mean `{bpi['mean']}` / p95 `{bpi['p95']}` / max `{bpi['max']}`"
            )
            lines.append("")

        br = ds["brightness_v"]
        if br["sampled_images"]:
            lines.append("### 昼夜亮度（HSV `V` 通道抽样）")
            lines.append("")
            lines.append(f"抽样解码 **{br['sampled_images']}** 张。")
            lines.append("")
            vd = br["v_mean_distribution"]
            lines.append(
                f"`V` 均值：min `{vd['min']}` / median `{vd['median']}` / "
                f"mean `{vd['mean']}` / p95 `{vd['p95']}` / max `{vd['max']}`"
            )
            lines.append("")
            lines.append("| 场景判定 | 张数 |")
            lines.append("|---|---:|")
            for scene, cnt in br["scene_lighting_counts"].items():
                lines.append(f"| `{scene}` | {cnt} |")
            lines.append("")

        gr = ds.get("groups")
        if gr:
            lines.append("### `video_id` 分组规模（防泄漏切分依据）")
            lines.append("")
            lines.append(
                f"`metadata.csv` 行数 **{gr['metadata_rows']}**｜"
                f"独立 `video_id` **{gr['video_id_count']}** 个"
            )
            lines.append("")
            gs = gr["video_group_size"]
            lines.append(
                f"每组帧数：min `{gs['min']}` / median `{gs['median']}` / "
                f"mean `{gs['mean']}` / p95 `{gs['p95']}` / max `{gs['max']}`"
            )
            lines.append("")
            lines.append("| `video_id` | 帧数 |")
            lines.append("|---|---:|")
            for vid, cnt in gr["video_id_frame_counts"].items():
                lines.append(f"| `{vid}` | {cnt} |")
            lines.append("")
            if gr["labels_present_distribution"]:
                lines.append("| `labels_present` | 图数 |")
                lines.append("|---|---:|")
                for lp, cnt in gr["labels_present_distribution"].items():
                    lines.append(f"| `{lp}` | {cnt} |")
                lines.append("")

        mm = ds.get("merged_manifest")
        if isinstance(mm, dict) and mm.get("splits"):
            lines.append("### 多源合并与分层切分分布（`prep_manifest.json`）")
            lines.append("")
            funnel = mm.get("etl_funnel_totals", {})
            if funnel:
                bad_cnt = funnel.get("skipped_corrupt_or_empty", 0) + funnel.get(
                    "skipped_missing_labels", 0
                )
                lines.append(
                    f"- ETL 总漏斗：输入 `{funnel.get('raw_images_found')}` 张 → "
                    f"视频 `{funnel.get('skipped_videos')}` → 坏图/无标 `{bad_cnt}` → "
                    f"裁剪 `{funnel.get('clipped_boxes')}` / "
                    f"退化丢弃 `{funnel.get('dropped_degenerate_boxes')}` / "
                    f"DROP `{funnel.get('dropped_unmapped_boxes')}` → "
                    f"去重(SHA `{funnel.get('deduped_sha256', 0)}` / "
                    f"RF `{funnel.get('deduped_rf_siblings', 0)}` / "
                    f"pHash `{funnel.get('deduped_phash', 0)}`) → "
                    f"保留 **`{funnel.get('kept_images')}`** 张 / "
                    f"**`{funnel.get('kept_boxes_total')}`** 框"
                )
                lines.append("")
            lines.append("| 来源 | 格式 | 保留图数 | 保留框数 | DROP 类别 |")
            lines.append("|---|---|---:|---:|---|")
            for src in mm.get("sources", []):
                lines.append(
                    f"| `{src.get('name')}` | `{src.get('detected_format')}` | "
                    f"{src.get('kept_images')} | {src.get('kept_boxes_total')} | "
                    f"`{src.get('dropped_classes_counts')}` |"
                )
            lines.append("")
            lines.append("| Split | 图数 | 框数 | 各类别框分布 | 各来源图分布 |")
            lines.append("|---|---:|---:|---|---|")
            for sp_name in ("train", "val", "test"):
                sp = mm["splits"].get(sp_name, {})
                lines.append(
                    f"| `{sp_name}` | {sp.get('images', 0)} | {sp.get('boxes', 0)} | "
                    f"`{sp.get('class_box_counts', {})}` | `{sp.get('source_image_counts', {})}` |"
                )
            lines.append("")
            lines.append(f"- 跨 split 防泄漏校验：`{mm.get('leakage_checks', {})}`")
            lines.append("")

    # --- 分辨率决策对比 ---
    cmp = report.get("imgsz_comparison")
    if cmp:
        lines.append("## `imgsz` 决策对比（`1280` vs `1024`）")
        lines.append("")
        lines.append(
            "> 依据：`ultralytics==8.4.216` `cfg/models/26/yolo26.yaml` 中 `Detect(P3, P4, P5)` "
            "三层输出 + `engine/trainer.py` letterbox 行为。"
        )
        lines.append("")
        lines.append("| 指标 | `imgsz=1024` | `imgsz=1280` |")
        lines.append("|---|---:|---:|")
        lines.append("| 相对 `640` 倍数 | `1.6×` | **`2.0×`** |")
        lines.append(f"| P3/8 特征图 | `{cmp['p3_1024']}` | `{cmp['p3_1280']}` |")
        lines.append(f"| P4/16 特征图 | `{cmp['p4_1024']}` | `{cmp['p4_1280']}` |")
        lines.append(f"| P5/32 特征图 | `{cmp['p5_1024']}` | `{cmp['p5_1280']}` |")
        lines.append(f"| One-to-Many anchors | `{cmp['anchors_1024']}` | `{cmp['anchors_1280']}` |")
        d1024 = cmp["detail_1024"]
        d1280 = cmp["detail_1280"]
        mode_res = d1024.get("mode_resolution", "1280x720")
        m_s1024, m_s1280 = d1024["mode_letterbox_scale"], d1280["mode_letterbox_scale"]
        m_k1024, m_k1280 = d1024["mode_native_pixels_kept"], d1280["mode_native_pixels_kept"]
        kept_1024, kept_1280 = cmp["kept_1024"], cmp["kept_1280"]
        lines.append(f"| 众数分辨率 `{mode_res}` 缩放率 | `{m_s1024}` | **`{m_s1280}`** |")
        lines.append(f"| 众数 `{mode_res}` 原生像素保留 | `{m_k1024:.1%}` | **`{m_k1280:.1%}`** |")
        lines.append(
            f"| 全框均摊原生像素保留率（上限100%） | `{kept_1024:.1%}` | **`{kept_1280:.1%}`** |"
        )
        lines.append(
            f"| 全框发生下采样占比（scale < 1） | "
            f"`{d1024['downsampled_boxes_ratio']:.2%}` | "
            f"**`{d1280['downsampled_boxes_ratio']:.2%}`** |"
        )
        lines.append(
            f"| `{mode_res}` 落图尺寸 | `{cmp['resized_1024']}` | `{cmp['resized_1280']}` |"
        )
        for row_label, cnt_k, ratio_k in (
            (
                "缩后短边 < 1 P3 格（<8px）",
                "boxes_below_one_p3_cell_lt_8px",
                "boxes_below_one_p3_cell_ratio",
            ),
            (
                "缩后短边 < 2 P3 格（<16px）",
                "boxes_below_two_p3_cells_lt_16px",
                "boxes_below_two_p3_cells_ratio",
            ),
            (
                "缩后短边 < 3 P3 格（<24px）",
                "boxes_below_three_p3_cells_lt_24px",
                "boxes_below_three_p3_cells_ratio",
            ),
            (
                "缩后 COCO Tiny（<32×32 px²）",
                "resized_coco_tiny_lt_32x32",
                "resized_coco_tiny_ratio",
            ),
        ):
            c1024, r1024 = d1024[cnt_k], d1024[ratio_k]
            c1280, r1280 = d1280[cnt_k], d1280[ratio_k]
            lines.append(f"| {row_label} | `{c1024}` ({r1024:.2%}) | **`{c1280}` ({r1280:.2%})** |")
        lines.append("")
    return "\n".join(lines) + "\n"


def run_eda(
    sources: Sequence[tuple[str, Path]],
    *,
    output_dir: Path,
    brightness_sample_size: int = 600,
    seed: int = 42,
) -> dict[str, Any]:
    """对多个数据集执行 EDA 并落盘 Markdown 报告与 JSON 摘要。"""
    datasets: list[dict[str, Any]] = []
    primary_box_w: list[float] = []
    primary_box_h: list[float] = []
    primary_box_dims: list[tuple[int, int]] = []
    primary_name = ""
    for name, root in sources:
        facts = discover_dataset(name, root)
        class_names: tuple[str, ...] = ()
        if facts.data_yaml is not None:
            yaml: Any = __import__("yaml")
            try:
                loaded = yaml.safe_load(facts.data_yaml.read_text(encoding="utf-8"))
                names_obj = loaded.get("names") if isinstance(loaded, dict) else None
                if isinstance(names_obj, dict):
                    class_names = tuple(
                        str(v) for _, v in sorted(names_obj.items(), key=lambda kv: int(kv[0]))
                    )
                elif isinstance(names_obj, list):
                    class_names = tuple(str(v) for v in names_obj)
            except Exception:
                class_names = ()
        report = analyze_dataset(
            facts,
            brightness_sample_size=brightness_sample_size,
            seed=seed,
            class_names=class_names,
        )
        datasets.append(report)
        if not primary_box_w:
            primary_name = name
            per_image_size: dict[str, tuple[int, int]] = {}
            for path in facts.images:
                header = _read_image_header(path)
                if header is not None:
                    per_image_size[path.stem] = (header[0], header[1])
            for label_dir in facts.label_dirs:
                for lbl in sorted(label_dir.rglob("*.txt")):
                    size = per_image_size.get(lbl.stem)
                    if size is None:
                        continue
                    img_w, img_h = size
                    for _cls, _xc, _yc, bw, bh in _parse_yolo_label_file(lbl):
                        primary_box_w.append(bw * img_w)
                        primary_box_h.append(bh * img_h)
                        primary_box_dims.append((img_w, img_h))

    imgsz_comparison: dict[str, Any] = {}
    if primary_box_w:
        mode_dim = Counter(primary_box_dims).most_common(1)[0][0]
        cmp_1024 = _imgsz_small_object_retention(
            primary_box_w,
            primary_box_h,
            1024,
            native_dims=primary_box_dims,
            default_w=mode_dim[0],
            default_h=mode_dim[1],
        )
        cmp_1280 = _imgsz_small_object_retention(
            primary_box_w,
            primary_box_h,
            1280,
            native_dims=primary_box_dims,
            default_w=mode_dim[0],
            default_h=mode_dim[1],
        )
        imgsz_comparison = {
            "primary_dataset": primary_name,
            "boxes_compared": len(primary_box_w),
            "mode_resolution": cmp_1024["mode_resolution"],
            "p3_1024": cmp_1024["p3_feature_map"],
            "p3_1280": cmp_1280["p3_feature_map"],
            "p4_1024": "64x64",
            "p4_1280": "80x80",
            "p5_1024": "32x32",
            "p5_1280": "40x40",
            "anchors_1024": cmp_1024["one_to_many_anchors"],
            "anchors_1280": cmp_1280["one_to_many_anchors"],
            "scale_1024": cmp_1024["mode_letterbox_scale"],
            "scale_1280": cmp_1280["mode_letterbox_scale"],
            "mean_scale_1024": cmp_1024["letterbox_scale"],
            "mean_scale_1280": cmp_1280["letterbox_scale"],
            "kept_1024": cmp_1024["native_pixels_kept"],
            "kept_1280": cmp_1280["native_pixels_kept"],
            "resized_1024": cmp_1024["mode_resized"],
            "resized_1280": cmp_1280["mode_resized"],
            "below_one_cell_1024": cmp_1024["boxes_below_one_p3_cell_ratio"],
            "below_one_cell_1280": cmp_1280["boxes_below_one_p3_cell_ratio"],
            "detail_1024": cmp_1024,
            "detail_1280": cmp_1280,
        }

    payload: dict[str, Any] = {
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "brightness_sample_size": brightness_sample_size,
        "seed": seed,
        "datasets": datasets,
        "imgsz_comparison": imgsz_comparison,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "dataset_eda.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "dataset-eda-report.md").write_text(_render_markdown(payload), encoding="utf-8")
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Tomori 数据集探索性分析（只读）：分辨率/框尺度/类别/亮度/分组规模"
    )
    parser.add_argument(
        "--source",
        action="append",
        required=True,
        help="数据集来源，格式 name=/path，可多次传入",
    )
    parser.add_argument("--output-dir", type=Path, required=True, help="报告输出目录")
    parser.add_argument("--brightness-sample-size", type=int, default=600)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(list(argv) if argv is not None else None)

    sources: list[tuple[str, Path]] = []
    for item in args.source:
        if "=" not in item:
            raise ValueError(f"--source 格式应为 name=/path，收到: {item}")
        name, path_str = item.split("=", 1)
        sources.append((name.strip(), Path(path_str)))

    payload = run_eda(
        sources,
        output_dir=args.output_dir,
        brightness_sample_size=args.brightness_sample_size,
        seed=args.seed,
    )
    summary = {
        "generated_at": payload["generated_at"],
        "datasets": [
            {
                "name": d["name"],
                "image_count": d["image_count"],
                "video_count": d["video_count"],
                "top_resolution": d["resolution"]["top_resolutions"][:3],
                "total_boxes": d["boxes"]["total_boxes"],
                "video_ids": d.get("groups", {}).get("video_id_count"),
            }
            for d in payload["datasets"]
        ],
        "imgsz_comparison": {
            k: v for k, v in payload["imgsz_comparison"].items() if not k.startswith("detail_")
        },
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
