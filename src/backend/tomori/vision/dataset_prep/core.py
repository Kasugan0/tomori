"""Tomori merged dataset ETL + 清洗管线（`dataset_prep` 核心模块）.

异构标注抽取、EXIF/坐标卫士、去重、`_clean_samples` 与模型定义。
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import re
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from tomori.vision import config

TARGET_NAMES: Final[dict[int, str]] = {idx: name for idx, name in enumerate(config.RAW_CLASSES)}
TARGET_NAME_TO_ID: Final[dict[str, int]] = {
    name: idx for idx, name in enumerate(config.RAW_CLASSES)
}

VIDEO_EXTENSIONS: Final[frozenset[str]] = frozenset(
    {
        ".mp4",
        ".mov",
        ".avi",
        ".mkv",
        ".flv",
        ".webm",
        ".m4v",
        ".ts",
        ".mpg",
        ".mpeg",
        ".wmv",
        ".m3u8",
    }
)

# Roboflow 导出文件名模式：`<orig_stem>_jpg.rf.<32hex>` 或 `<orig_stem>.rf.<32hex>`
_RF_STEM_RE: Final[re.Pattern[str]] = re.compile(
    r"^(.+?)(?:_(?:jpg|jpeg|png|webp|bmp))?\.rf\.[0-9a-fA-F]{32}$",
    re.IGNORECASE,
)


def _norm_label(raw: str) -> str:
    """将原始类名规范化为小写下划线形式，便于稳健语义匹配."""
    return re.sub(r"[\s\-]+", "_", raw.strip().lower())


# 仅收录语义明确且与 Tomori 4 类一致的别名；
# 严禁将 `pole` / `utility_pole` / `street_light` / `normal` 等健康杆体或泛化类名纳入破损映射！
SOURCE_CLASS_ALIASES: Final[dict[str, int]] = {
    "streetlight_on": 0,
    "lamp_on": 0,
    "working": 0,
    "on": 0,
    "on_streetlight": 0,
    "lit": 0,
    "working_light": 0,
    "working_streetlight": 0,
    "working_street_light": 0,
    "good_light": 0,
    "streetlight_off": 1,
    "lamp_off": 1,
    "not_working": 1,
    "not working": 1,
    "not-working": 1,
    "non_working": 1,
    "off": 1,
    "off_streetlight": 1,
    "unlit": 1,
    "streetlight_damaged": 2,
    "damaged": 2,
    "lamp_damaged": 2,
    "damaged_lamp": 2,
    "damaged_light": 2,
    "damaged_lights": 2,
    "broken": 2,
    "broken_pole": 2,
    "pole_damaged": 2,
    "damaged_pole": 2,
    "broken_light": 2,
    "broken_lamp": 2,
    "defective_lamp": 2,
    "tilted_pole": 2,
    "fallen_pole": 2,
    "rusted_pole": 2,
    "streetlight_occluded": 3,
    "lamp_occluded": 3,
    "occluded": 3,
    "occluded_lamp": 3,
    "obstructed": 3,
    "obstructed_lamp": 3,
}

# D1 (`ngocleltt/lamp-state-detection`) 在无 data.yaml/classes.txt 覆盖时的保底映射
DEFAULT_D1_ID_MAP: Final[dict[int, int]] = {0: 0, 1: 1, 2: 3}

_VALID_FORMATS: Final[frozenset[str]] = frozenset({"auto", "yolo", "coco", "voc"})

# `.rf.` 增广簇判据：
# - `stem`：以 Roboflow `.rf.<32hex>` 前缀（去掉 `.rf.<hash>` 后的原图 stem）作为同源增广簇 key。
#   仅当原图 stem 具备标识性时可信（如 `d4` 的 13 位 Unix 毫秒时间戳、`d3` 的 `806_20_<uuid>`）。
# - `phash`：完全忽略 `.rf.` 前缀，改用 64-bit pHash 聚簇判同源。
#   必须用于原图 stem 为**通用短名/数字名**的源（如 `d7` 的 `100`/`105`/`110`）：
#   该源实测存在「同名不同图」（`990` 对 dihedral-pHash `>14`、`MAD 31..63`）与
#   「异名同图」（`test/105_jpg` 与 `train/POSTEEEEEES-CAIDOS-50-_jpg`）双重陷阱，
#   盲目按 `.rf.` 前缀合并会把 `1,928` 个 pHash 独立簇误缩至 `1,450` 张。
_VALID_RF_MODES: Final[frozenset[str]] = frozenset({"stem", "phash"})

# 分层装箱枚举上限：`enum_groups`（多图组）最多枚举 `3^k` 种分配，截断到此规模防组合爆炸。
_SPLIT_ENUM_LIMIT: Final[int] = 12
# `occluded`（类 id=3）在 val/test 各自需达到的框数下限；仅当全库 `occluded` 达到其 2 倍时启用。
_OCCLUDED_MIN_PER_SPLIT: Final[int] = 30


@dataclass(frozen=True)
class DatasetSourceSpec:
    """单个待纳入训练的原始数据集配置。"""

    name: str
    root_dir: Path
    explicit_id_map: dict[int, int | None] | None = None
    class_name_map: dict[str, int | str | None] | None = None
    format_hint: str = "auto"  # "auto" | "yolo" | "coco" | "voc"
    include_unlabeled: bool = False
    drop_unmapped: bool = True
    has_preaugmentation: bool = False
    keep_emptied_by_drop: bool = False
    include_splits: tuple[str, ...] | None = None
    rf_cluster_mode: str = "stem"

    def __post_init__(self) -> None:
        if self.format_hint not in _VALID_FORMATS:
            raise ValueError(
                f"不支持的 format_hint={self.format_hint!r}，可选: {sorted(_VALID_FORMATS)}"
            )
        if self.include_splits is not None:
            norm_sp = tuple(
                ("val" if s.strip().lower() == "valid" else s.strip().lower())
                for s in self.include_splits
            )
            object.__setattr__(self, "include_splits", norm_sp)
        if self.rf_cluster_mode not in _VALID_RF_MODES:
            raise ValueError(
                f"不支持的 rf_cluster_mode={self.rf_cluster_mode!r}，"
                f"可选: {sorted(_VALID_RF_MODES)}"
            )


@dataclass(frozen=True)
class DatasetPrepConfig:
    """多源数据清洗、归一化、去重与防泄漏分层切分配置。"""

    sources: tuple[DatasetSourceSpec, ...]
    output_dir: Path
    train_ratio: float = 0.70
    val_ratio: float = 0.15
    test_ratio: float = 0.15
    phash_threshold: int = 6
    dedupe_phash: bool = True
    dedupe_rf_siblings: bool = False
    min_box_px: float = 2.0
    forbid_preaug_in_val_test: bool = True
    seed: int = 42
    link_mode: str = "hardlink"  # "hardlink" | "copy" | "symlink"
    allow_small_groups: bool = False
    # merged_v6 清洗开关（`task-161` 实测依据；默认全关保持 merged_v5 兼容）
    clean_day_mean_v_max: float = 0.0  # >0：剔除「`mean_v` 超阈值且全 `on`」白天帧
    clean_occluded_model: Path | None = (
        None  # `best.pt`：`occluded` 自证重标（`conf>=0.65` 判 `on`）
    )
    # `task-165`：`occluded` 类明确弃用（`nc=3`）——`_clean_samples` 兜底删 `cid==3`，
    # 输出层 `data.yaml` `nc`/`names`/`target_names`/`split_class_*` 均为 `3` 类
    clean_drop_occluded: bool = False
    # `task-204`：`d3` `damaged` 灯头区过滤——`damaged`（`cid==2`）GT 框须与任一灯具
    # 检测框 `IoU>=0.1` 或中心点落灯具框内才保留（`best.pt`，`conf>=0.25` 检出）
    clean_damaged_lamp_model: Path | None = None

    def __post_init__(self) -> None:
        if not self.sources:
            raise ValueError("至少需要提供一个数据集来源")
        total = self.train_ratio + self.val_ratio + self.test_ratio
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"train/val/test 比例之和必须为 1.0，当前 {total:.6f}")
        if min(self.train_ratio, self.val_ratio, self.test_ratio) <= 0.0:
            raise ValueError("train/val/test 比例均必须大于 0")
        if self.phash_threshold < 0 or self.phash_threshold > 64:
            raise ValueError(f"phash_threshold 必须在 [0, 64]，实际 {self.phash_threshold}")
        if self.min_box_px < 0.0:
            raise ValueError(f"min_box_px 不能为负，实际 {self.min_box_px}")
        if self.link_mode not in {"hardlink", "copy", "symlink"}:
            raise ValueError(f"不支持的 link_mode: {self.link_mode}")


@dataclass
class _ImageSample:
    sample_id: str
    source_name: str
    image_path: Path
    label_path: Path | None
    video_id: str | None
    rf_orig_stem: str | None
    source_split_hint: str
    is_preaugmented: bool
    val_test_eligible: bool
    group_key: str
    upright_width: int
    upright_height: int
    exif_orientation: int
    boxes: list[tuple[int, float, float, float, float]]
    phash64: int = 0
    phash_cluster: int = -1
    content_sha256: str = ""
    split: str = ""


@dataclass
class SourceStats:
    name: str
    root_dir: str
    detected_format: str = "yolo"
    raw_images_found: int = 0
    skipped_videos: int = 0
    skipped_video_samples: list[str] = field(default_factory=list)
    skipped_non_images: int = 0
    skipped_missing_labels: int = 0
    skipped_corrupt_or_empty: int = 0
    skipped_emptied_by_drop: int = 0
    exif_non_identity_images: int = 0
    yolo_exif_rotated_images: int = 0
    raw_annotations_total: int = 0
    clipped_boxes: int = 0
    dropped_unmapped_boxes: int = 0
    dropped_degenerate_boxes: int = 0
    dropped_invalid_boxes: int = 0
    kept_boxes_total: int = 0
    format_box_counts: dict[str, int] = field(default_factory=dict)
    mapped_classes_counts: dict[str, int] = field(default_factory=dict)
    dropped_classes_counts: dict[str, int] = field(default_factory=dict)
    deduped_sha256: int = 0
    deduped_phash: int = 0
    deduped_rf_siblings: int = 0
    preaugmented_images: int = 0
    kept_images: int = 0
    unique_video_ids: int = 0
    unique_rf_stems: int = 0
    rf_cluster_mode: str = "stem"


@dataclass(frozen=True)
class PrepResult:
    """`prepare_dataset` 输出摘要与校验产物路径。"""

    output_dir: Path
    data_yaml_path: Path
    manifest_path: Path
    total_kept_images: int
    total_skipped_videos: int
    total_deduped_phash: int
    split_image_counts: dict[str, int]
    leakage_checks: dict[str, int]


def compute_phash64(image_path: Path) -> int:
    """计算 EXIF 转正后图像 64-bit 2D-DCT 感知哈希（防 `!buf.empty()` 崩溃）."""
    cv2: Any = __import__("cv2")
    np: Any = __import__("numpy")

    raw = image_path.read_bytes()
    if len(raw) == 0:
        raise ValueError(f"空图片文件（0 字节）无法计算 pHash: {image_path}")

    buf = np.frombuffer(raw, dtype=np.uint8)
    # 使用 IMREAD_COLOR 确保 OpenCV 5 自动应用 EXIF 方向后再转灰度
    bgr = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    if bgr is None or bgr.size == 0:
        raise ValueError(f"图片损坏，cv2.imdecode 返回空矩阵: {image_path}")
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)

    resized = cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA)
    dct = cv2.dct(np.asarray(resized, dtype=np.float32))
    low_freq = dct[:8, :8].flatten()
    # 排除 (0,0) 直流分量计算 AC 中位数，保证亮度平移不变性
    ac = low_freq[1:]
    median = float(np.median(ac))
    bits = (low_freq > median).astype(np.uint8)
    phash = 0
    for idx, bit in enumerate(bits.tolist()):
        if bit:
            phash |= 1 << idx
    return phash


def hamming_distance64(a: int, b: int) -> int:
    """计算两个 64-bit pHash 的 Hamming 距离。"""
    return (a ^ b).bit_count()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_lfs_pointer(path: Path) -> bool:
    try:
        if path.stat().st_size >= 512:
            return False
        return path.read_bytes()[:32].startswith(b"version https://git-lfs")
    except OSError:
        return False


def _inspect_image(path: Path) -> tuple[int, int, int, int, int]:
    """`PIL.Image.verify()` 校验图像完整性并返回 `(raw_w, raw_h, ori, upright_w, upright_h)`."""
    Image: Any = __import__("PIL.Image", fromlist=["Image"])
    with Image.open(path) as probe:
        probe.verify()
    with Image.open(path) as probe2:
        raw_w, raw_h = int(probe2.size[0]), int(probe2.size[1])
        ori_raw = probe2.getexif().get(274, 1)
        ori = int(ori_raw) if ori_raw else 1
    if raw_w <= 0 or raw_h <= 0:
        raise ValueError(f"非法图像尺寸 {raw_w}x{raw_h}: {path}")
    if ori in {5, 6, 7, 8}:
        return raw_w, raw_h, ori, raw_h, raw_w
    return raw_w, raw_h, ori, raw_w, raw_h


def _transform_norm_point_for_exif(x: float, y: float, orientation: int) -> tuple[float, float]:
    """将归一化坐标 `(x, y)` 从存储像素系变换至 EXIF 转正后的显示像素系."""
    if orientation == 2:
        return (1.0 - x, y)
    if orientation == 3:
        return (1.0 - x, 1.0 - y)
    if orientation == 4:
        return (x, 1.0 - y)
    if orientation == 5:
        return (y, x)
    if orientation == 6:
        return (1.0 - y, x)
    if orientation == 7:
        return (1.0 - y, 1.0 - x)
    if orientation == 8:
        return (y, 1.0 - x)
    return (x, y)


def _transform_norm_xyxy_for_exif(
    xmin: float, ymin: float, xmax: float, ymax: float, orientation: int
) -> tuple[float, float, float, float]:
    if orientation not in {2, 3, 4, 5, 6, 7, 8}:
        return xmin, ymin, xmax, ymax
    x1, y1 = _transform_norm_point_for_exif(xmin, ymin, orientation)
    x2, y2 = _transform_norm_point_for_exif(xmax, ymax, orientation)
    return min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)


def _clip_and_validate_xyxy(
    xmin: float,
    ymin: float,
    xmax: float,
    ymax: float,
    *,
    img_w: int,
    img_h: int,
    min_box_px: float,
) -> tuple[tuple[float, float, float, float] | None, bool, bool]:
    """裁剪归一化 `[xmin, ymin, xmax, ymax]` 至 `[0, 1]` 并过滤退化框.

    返回 `(xywh_norm_or_none, was_clipped, was_degenerate)`.
    """
    if not all(math.isfinite(v) for v in (xmin, ymin, xmax, ymax)):
        return None, False, True
    if xmin > xmax:
        xmin, xmax = xmax, xmin
    if ymin > ymax:
        ymin, ymax = ymax, ymin

    clipped = xmin < 0.0 or ymin < 0.0 or xmax > 1.0 or ymax > 1.0
    c_xmin = max(0.0, min(1.0, xmin))
    c_ymin = max(0.0, min(1.0, ymin))
    c_xmax = max(0.0, min(1.0, xmax))
    c_ymax = max(0.0, min(1.0, ymax))

    bw = c_xmax - c_xmin
    bh = c_ymax - c_ymin
    if bw <= 1e-6 or bh <= 1e-6:
        return None, clipped, True
    if (bw * img_w) < min_box_px or (bh * img_h) < min_box_px:
        return None, clipped, True

    xc = 0.5 * (c_xmin + c_xmax)
    yc = 0.5 * (c_ymin + c_ymax)
    return (xc, yc, bw, bh), clipped, False


def _parse_yaml_or_text_names(root_dir: Path) -> dict[int, str]:
    """尝试从数据集根目录读取 `data.yaml` 或 `classes.txt` 的原始类别名映射。"""
    yaml_path = root_dir / "data.yaml"
    if yaml_path.is_file():
        try:
            yaml: Any = __import__("yaml")
            loaded = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and "names" in loaded:
                names_obj = loaded["names"]
                if isinstance(names_obj, list):
                    return {i: str(v).strip() for i, v in enumerate(names_obj)}
                if isinstance(names_obj, dict):
                    return {int(k): str(v).strip() for k, v in names_obj.items()}
        except Exception:
            pass

    classes_txt = root_dir / "classes.txt"
    if classes_txt.is_file():
        lines = [
            line.strip()
            for line in classes_txt.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if lines:
            return dict(enumerate(lines))
    return {}


def _resolve_target_class_value(val: int | str | None) -> int | None:
    """将用户配置的映射目标解析为 `0..3` 或 `None`（显式 `DROP`）."""
    if val is None:
        return None
    if isinstance(val, int):
        if val not in TARGET_NAMES:
            raise ValueError(f"非法目标类别 ID {val}，允许: {sorted(TARGET_NAMES)}")
        return val
    norm = val.strip()
    if norm.upper() == "DROP" or norm == "":
        return None
    if norm in TARGET_NAME_TO_ID:
        return TARGET_NAME_TO_ID[norm]
    norm_low = _norm_label(norm)
    if norm_low in SOURCE_CLASS_ALIASES:
        return SOURCE_CLASS_ALIASES[norm_low]
    raise ValueError(f"无法解析目标类别 {val!r}，允许: {list(TARGET_NAME_TO_ID)} 或 'DROP'")


def _resolve_name_to_target(
    raw_name: str,
    spec: DatasetSourceSpec,
) -> int | None:
    """根据显式 `class_name_map` → `SOURCE_CLASS_ALIASES` 决定目标 ID 或 `None` (`DROP`)."""
    norm = _norm_label(raw_name)
    if spec.class_name_map is not None:
        for k, v in spec.class_name_map.items():
            if _norm_label(k) == norm or k.strip() == raw_name.strip():
                return _resolve_target_class_value(v)
        if spec.drop_unmapped:
            return None
        raise ValueError(f"数据源 {spec.name} 未映射类别 {raw_name!r}")

    if norm in SOURCE_CLASS_ALIASES:
        return SOURCE_CLASS_ALIASES[norm]
    if raw_name.strip().lower() in SOURCE_CLASS_ALIASES:
        return SOURCE_CLASS_ALIASES[raw_name.strip().lower()]
    if spec.drop_unmapped:
        return None
    raise ValueError(f"数据源 {spec.name} 发现未知类别 {raw_name!r} 且 drop_unmapped=False")


def _build_id_map(
    spec: DatasetSourceSpec, source_names: dict[int, str]
) -> dict[int, tuple[int | None, str]]:
    """构建源类别 ID → `(目标类别 ID 或 None(DROP), 原始类名)` 映射表."""
    out: dict[int, tuple[int | None, str]] = {}
    if spec.explicit_id_map is not None:
        for src_id, dst_id in spec.explicit_id_map.items():
            raw_name = source_names.get(src_id, f"class_{src_id}")
            if dst_id is not None and dst_id not in TARGET_NAMES:
                raise ValueError(f"数据源 {spec.name} explicit_id_map 目标非法: {dst_id}")
            out[src_id] = (dst_id, raw_name)
        for src_id, raw_name in source_names.items():
            if src_id not in out:
                out[src_id] = (None, raw_name)
        return out

    if source_names:
        for src_id, raw_name in source_names.items():
            out[src_id] = (_resolve_name_to_target(raw_name, spec), raw_name)
        return out

    for src_id, dst_id in DEFAULT_D1_ID_MAP.items():
        out[src_id] = (dst_id, f"class_{src_id}")
    return out


def _load_metadata_video_map(root_dir: Path) -> dict[str, str]:
    """读取 D1 `metadata.csv`，按 `relative_path`、`file_name` 与 stem 建立到 `video_id` 的映射。"""
    csv_path = root_dir / "metadata.csv"
    if not csv_path.is_file():
        return {}

    video_map: dict[str, str] = {}
    # D1 metadata.csv 首列带 UTF-8 BOM（\ufeffrelative_path），必须用 utf-8-sig 剥除
    with csv_path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            vid = (row.get("video_id") or "").strip()
            if not vid:
                continue
            rel = (row.get("relative_path") or "").strip().replace("\\", "/")
            fname = (row.get("file_name") or "").strip()
            if rel:
                video_map[rel] = vid
                video_map[f"images/{rel}"] = vid
                video_map[Path(rel).name] = vid
            if fname:
                video_map[fname] = vid
    return video_map


def _parse_roboflow_stem_and_split(file_path: Path, root: Path) -> tuple[str | None, str]:
    """识别 Roboflow `.rf.<32hex>` 原图 stem 与原始目录划分（`train`/`valid`/`test`）。"""
    rel_parts = [p.lower() for p in file_path.relative_to(root).parts[:-1]]
    split_hint = ""
    for part in rel_parts:
        if part in {"train", "valid", "val", "test"}:
            split_hint = "val" if part == "valid" else part
            break

    m = _RF_STEM_RE.match(file_path.stem)
    rf_stem = m.group(1) if m else None
    return rf_stem, split_hint


def _find_yolo_label_file(root_dir: Path, image_path: Path) -> Path | None:
    """按常见 YOLO 目录结构定位标签 `.txt` 文件（覆盖 `images/` 与 `<split>/images/`）。"""
    rel = image_path.relative_to(root_dir)
    parts = list(rel.parts)
    candidates: list[Path] = []
    # 从右往左替换最近的 `images` 段（适配 `train/images/foo.jpg` -> `train/labels/foo.txt`）
    for idx in range(len(parts) - 1, -1, -1):
        if parts[idx] == "images":
            lbl_parts = [*parts[:idx], "labels", *parts[idx + 1 :]]
            candidates.append(root_dir.joinpath(*lbl_parts).with_suffix(".txt"))
    candidates.append((root_dir / "labels" / rel).with_suffix(".txt"))
    candidates.append((root_dir / "labels" / image_path.name).with_suffix(".txt"))
    candidates.append(image_path.with_suffix(".txt"))

    for cand in candidates:
        if cand.is_file():
            return cand
    return None


def _find_voc_xml_file(root_dir: Path, image_path: Path) -> Path | None:
    """定位对应 Pascal VOC `.xml` 文件."""
    rel = image_path.relative_to(root_dir)
    parts = list(rel.parts)
    candidates: list[Path] = []
    for folder_name in ("Annotations", "annotations", "labels"):
        for idx in range(len(parts) - 1, -1, -1):
            if parts[idx].lower() in {"images", "jpegimages"}:
                xml_parts = [*parts[:idx], folder_name, *parts[idx + 1 :]]
                candidates.append(root_dir.joinpath(*xml_parts).with_suffix(".xml"))
        candidates.append((root_dir / folder_name / rel).with_suffix(".xml"))
        candidates.append((root_dir / folder_name / image_path.name).with_suffix(".xml"))
    candidates.append(image_path.with_suffix(".xml"))

    for cand in candidates:
        if cand.is_file():
            return cand
    return None


@dataclass
class _CocoIndex:
    """已加载的 COCO JSON 索引（按 resolved path 与相对路径匹配图像）."""

    by_resolved: dict[
        Path, tuple[int, int, list[tuple[str, str, tuple[float, float, float, float]]]]
    ]
    by_rel_posix: dict[
        str, tuple[int, int, list[tuple[str, str, tuple[float, float, float, float]]]]
    ]
    by_basename: dict[
        str, tuple[int, int, list[tuple[str, str, tuple[float, float, float, float]]]] | None
    ]


def _extract_coco_xyxy_from_ann(
    ann: dict[str, Any],
) -> tuple[str, tuple[float, float, float, float]] | None:
    """从单条 COCO annotation 提取 `(fmt, (xmin_px, ymin_px, xmax_px, ymax_px))`."""
    bbox = ann.get("bbox")
    if isinstance(bbox, list) and len(bbox) == 4:
        try:
            x, y, w, h = float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])
            if math.isfinite(x) and math.isfinite(y) and math.isfinite(w) and math.isfinite(h):
                return "coco_bbox", (x, y, x + w, y + h)
        except (TypeError, ValueError):
            pass

    seg = ann.get("segmentation")
    if isinstance(seg, list) and seg:
        xs: list[float] = []
        ys: list[float] = []
        for poly in seg:
            if isinstance(poly, list) and len(poly) >= 6:
                try:
                    pts = [float(v) for v in poly]
                except (TypeError, ValueError):
                    continue
                xs.extend(pts[0::2])
                ys.extend(pts[1::2])
        if xs and ys:
            return "coco_polygon", (min(xs), min(ys), max(xs), max(ys))
    return None


def _discover_coco_index(root_dir: Path) -> _CocoIndex | None:
    """扫描 `root_dir` 下所有 COCO JSON（排除 D1 单图注释 JSON）并建立索引."""
    json_files = [
        p
        for p in sorted(root_dir.rglob("*.json"))
        if not any(part.startswith(".") for part in p.relative_to(root_dir).parts)
    ]
    by_resolved: dict[
        Path, tuple[int, int, list[tuple[str, str, tuple[float, float, float, float]]]]
    ] = {}
    by_rel_posix: dict[
        str, tuple[int, int, list[tuple[str, str, tuple[float, float, float, float]]]]
    ] = {}
    by_basename: dict[
        str, tuple[int, int, list[tuple[str, str, tuple[float, float, float, float]]]] | None
    ] = {}

    found_coco = False
    for jp in json_files:
        try:
            raw_bytes = jp.read_bytes()
        except OSError:
            continue
        if (
            b'"categories"' not in raw_bytes
            or b'"images"' not in raw_bytes
            or b'"annotations"' not in raw_bytes
        ):
            continue
        try:
            data = json.loads(raw_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        images_list = data.get("images")
        anns_list = data.get("annotations")
        cats_list = data.get("categories")
        if not (
            isinstance(images_list, list)
            and isinstance(anns_list, list)
            and isinstance(cats_list, list)
        ):
            continue

        found_coco = True
        cat_map: dict[int, str] = {}
        for c in cats_list:
            if isinstance(c, dict) and "id" in c and "name" in c:
                cat_map[int(c["id"])] = str(c["name"]).strip()

        anns_by_img: dict[int, list[tuple[str, str, tuple[float, float, float, float]]]] = (
            defaultdict(list)
        )
        for ann in anns_list:
            if not isinstance(ann, dict) or "image_id" not in ann or "category_id" not in ann:
                continue
            cname = cat_map.get(int(ann["category_id"]), f"cat_{ann['category_id']}")
            parsed = _extract_coco_xyxy_from_ann(ann)
            if parsed is None:
                continue
            fmt, xyxy = parsed
            anns_by_img[int(ann["image_id"])].append((fmt, cname, xyxy))

        j_parent = jp.parent
        for im_rec in images_list:
            if not isinstance(im_rec, dict) or "id" not in im_rec or "file_name" not in im_rec:
                continue
            img_id = int(im_rec["id"])
            fn = str(im_rec["file_name"]).replace("\\", "/").lstrip("./")
            cw = int(im_rec.get("width") or 0)
            ch = int(im_rec.get("height") or 0)
            entry = (cw, ch, anns_by_img.get(img_id, []))

            cand_paths = [
                (j_parent / fn).resolve(),
                (j_parent / "images" / fn).resolve(),
                (root_dir / fn).resolve(),
                (root_dir / "images" / fn).resolve(),
            ]
            for cp in cand_paths:
                by_resolved[cp] = entry
            try:
                rel_key = (j_parent / fn).resolve().relative_to(root_dir.resolve()).as_posix()
                by_rel_posix[rel_key] = entry
            except ValueError:
                pass
            by_rel_posix[fn] = entry
            base = Path(fn).name
            if base in by_basename:
                by_basename[base] = None  # 歧义 basename 禁用兜底
            else:
                by_basename[base] = entry

    if not found_coco:
        return None
    return _CocoIndex(
        by_resolved=by_resolved,
        by_rel_posix=by_rel_posix,
        by_basename=by_basename,
    )


def _lookup_coco_entry(
    coco: _CocoIndex, root_dir: Path, image_path: Path
) -> tuple[int, int, list[tuple[str, str, tuple[float, float, float, float]]]] | None:
    resolved = image_path.resolve()
    if resolved in coco.by_resolved:
        return coco.by_resolved[resolved]
    rel = image_path.relative_to(root_dir).as_posix()
    if rel in coco.by_rel_posix:
        return coco.by_rel_posix[rel]
    return coco.by_basename.get(image_path.name)


def _parse_yolo_labels(
    label_path: Path,
    id_map: dict[int, tuple[int | None, str]],
    *,
    upright_w: int,
    upright_h: int,
    min_box_px: float,
    stats: SourceStats,
) -> tuple[list[tuple[int, float, float, float, float]], bool]:
    """解析 YOLO TXT（支持 5 列检测框与多边形 `cls x1 y1 ...`），返回 `(boxes, had_raw_rows)`."""
    boxes: list[tuple[int, float, float, float, float]] = []
    had_raw_rows = False
    text = label_path.read_text(encoding="utf-8", errors="replace")

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        had_raw_rows = True
        stats.raw_annotations_total += 1
        parts = line.split()
        if len(parts) < 5:
            stats.dropped_invalid_boxes += 1
            continue

        try:
            src_cls = int(float(parts[0]))
        except ValueError:
            stats.dropped_invalid_boxes += 1
            continue

        if src_cls not in id_map:
            stats.dropped_unmapped_boxes += 1
            cname = f"class_{src_cls}"
            stats.dropped_classes_counts[cname] = stats.dropped_classes_counts.get(cname, 0) + 1
            continue

        dst_cls, src_name = id_map[src_cls]
        if dst_cls is None or dst_cls not in TARGET_NAMES:
            stats.dropped_unmapped_boxes += 1
            stats.dropped_classes_counts[src_name] = (
                stats.dropped_classes_counts.get(src_name, 0) + 1
            )
            continue

        if len(parts) == 5:
            fmt = "yolo_bbox"
            try:
                xc, yc, bw, bh = (
                    float(parts[1]),
                    float(parts[2]),
                    float(parts[3]),
                    float(parts[4]),
                )
            except ValueError:
                stats.dropped_invalid_boxes += 1
                continue
            xmin, ymin = xc - 0.5 * bw, yc - 0.5 * bh
            xmax, ymax = xc + 0.5 * bw, yc + 0.5 * bh
        elif len(parts) >= 7 and (len(parts) - 1) % 2 == 0:
            fmt = "yolo_polygon"
            try:
                coords = [float(v) for v in parts[1:]]
            except ValueError:
                stats.dropped_invalid_boxes += 1
                continue
            xs, ys = coords[0::2], coords[1::2]
            xmin, xmax = min(xs), max(xs)
            ymin, ymax = min(ys), max(ys)
        else:
            stats.dropped_invalid_boxes += 1
            continue

        xywh, clipped, deg = _clip_and_validate_xyxy(
            xmin,
            ymin,
            xmax,
            ymax,
            img_w=upright_w,
            img_h=upright_h,
            min_box_px=min_box_px,
        )
        if clipped:
            stats.clipped_boxes += 1
        if deg or xywh is None:
            stats.dropped_degenerate_boxes += 1
            continue

        stats.format_box_counts[fmt] = stats.format_box_counts.get(fmt, 0) + 1
        map_key = f"{src_name}->{TARGET_NAMES[dst_cls]}"
        stats.mapped_classes_counts[map_key] = stats.mapped_classes_counts.get(map_key, 0) + 1
        stats.kept_boxes_total += 1
        boxes.append((dst_cls, *xywh))

    return boxes, had_raw_rows


def _parse_coco_entry(
    entry: tuple[int, int, list[tuple[str, str, tuple[float, float, float, float]]]],
    spec: DatasetSourceSpec,
    *,
    raw_w: int,
    raw_h: int,
    ori: int,
    upright_w: int,
    upright_h: int,
    min_box_px: float,
    stats: SourceStats,
) -> tuple[list[tuple[int, float, float, float, float]], bool]:
    cw, ch, anns = entry
    ann_w = cw if cw > 0 else upright_w
    ann_h = ch if ch > 0 else upright_h
    need_exif_rot = ori in {2, 3, 4, 5, 6, 7, 8} and ann_w == raw_w and ann_h == raw_h

    boxes: list[tuple[int, float, float, float, float]] = []
    had_raw_rows = bool(anns)
    for fmt, raw_name, (px_xmin, px_ymin, px_xmax, px_ymax) in anns:
        stats.raw_annotations_total += 1
        dst_cls = _resolve_name_to_target(raw_name, spec)
        if dst_cls is None:
            stats.dropped_unmapped_boxes += 1
            stats.dropped_classes_counts[raw_name] = (
                stats.dropped_classes_counts.get(raw_name, 0) + 1
            )
            continue

        xmin = px_xmin / float(ann_w)
        ymin = px_ymin / float(ann_h)
        xmax = px_xmax / float(ann_w)
        ymax = px_ymax / float(ann_h)
        if need_exif_rot:
            xmin, ymin, xmax, ymax = _transform_norm_xyxy_for_exif(xmin, ymin, xmax, ymax, ori)

        xywh, clipped, deg = _clip_and_validate_xyxy(
            xmin,
            ymin,
            xmax,
            ymax,
            img_w=upright_w,
            img_h=upright_h,
            min_box_px=min_box_px,
        )
        if clipped:
            stats.clipped_boxes += 1
        if deg or xywh is None:
            stats.dropped_degenerate_boxes += 1
            continue

        stats.format_box_counts[fmt] = stats.format_box_counts.get(fmt, 0) + 1
        map_key = f"{raw_name}->{TARGET_NAMES[dst_cls]}"
        stats.mapped_classes_counts[map_key] = stats.mapped_classes_counts.get(map_key, 0) + 1
        stats.kept_boxes_total += 1
        boxes.append((dst_cls, *xywh))

    return boxes, had_raw_rows


def _parse_voc_xml(
    xml_path: Path,
    spec: DatasetSourceSpec,
    *,
    raw_w: int,
    raw_h: int,
    ori: int,
    upright_w: int,
    upright_h: int,
    min_box_px: float,
    stats: SourceStats,
) -> tuple[list[tuple[int, float, float, float, float]], bool]:
    tree = ET.parse(xml_path)
    root_el = tree.getroot()
    size_el = root_el.find("size")
    xw = int(float(size_el.findtext("width") or 0)) if size_el is not None else 0
    xh = int(float(size_el.findtext("height") or 0)) if size_el is not None else 0
    ann_w = xw if xw > 0 else upright_w
    ann_h = xh if xh > 0 else upright_h
    need_exif_rot = ori in {2, 3, 4, 5, 6, 7, 8} and ann_w == raw_w and ann_h == raw_h

    boxes: list[tuple[int, float, float, float, float]] = []
    objs = root_el.findall("object")
    had_raw_rows = bool(objs)
    for obj in objs:
        stats.raw_annotations_total += 1
        raw_name = (obj.findtext("name") or "").strip()
        if not raw_name:
            stats.dropped_invalid_boxes += 1
            continue
        dst_cls = _resolve_name_to_target(raw_name, spec)
        if dst_cls is None:
            stats.dropped_unmapped_boxes += 1
            stats.dropped_classes_counts[raw_name] = (
                stats.dropped_classes_counts.get(raw_name, 0) + 1
            )
            continue

        bnd = obj.find("bndbox")
        if bnd is None:
            stats.dropped_invalid_boxes += 1
            continue
        try:
            px_xmin = float(bnd.findtext("xmin") or "")
            px_ymin = float(bnd.findtext("ymin") or "")
            px_xmax = float(bnd.findtext("xmax") or "")
            px_ymax = float(bnd.findtext("ymax") or "")
        except ValueError:
            stats.dropped_invalid_boxes += 1
            continue

        xmin = px_xmin / float(ann_w)
        ymin = px_ymin / float(ann_h)
        xmax = px_xmax / float(ann_w)
        ymax = px_ymax / float(ann_h)
        if need_exif_rot:
            xmin, ymin, xmax, ymax = _transform_norm_xyxy_for_exif(xmin, ymin, xmax, ymax, ori)

        xywh, clipped, deg = _clip_and_validate_xyxy(
            xmin,
            ymin,
            xmax,
            ymax,
            img_w=upright_w,
            img_h=upright_h,
            min_box_px=min_box_px,
        )
        if clipped:
            stats.clipped_boxes += 1
        if deg or xywh is None:
            stats.dropped_degenerate_boxes += 1
            continue

        stats.format_box_counts["voc_xml"] = stats.format_box_counts.get("voc_xml", 0) + 1
        map_key = f"{raw_name}->{TARGET_NAMES[dst_cls]}"
        stats.mapped_classes_counts[map_key] = stats.mapped_classes_counts.get(map_key, 0) + 1
        stats.kept_boxes_total += 1
        boxes.append((dst_cls, *xywh))

    return boxes, had_raw_rows


class _UnionFind:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            if ra < rb:
                self.parent[rb] = ra
            else:
                self.parent[ra] = rb


def _sample_rank_key(s: _ImageSample) -> tuple[int, int, str, str]:
    """选择聚簇代表样本时的优先级：优先非预增广原图、标注框更多、路径更靠前."""
    return (
        1 if s.is_preaugmented else 0,
        -len(s.boxes),
        s.source_name,
        str(s.image_path),
    )


def _dedupe_exact_and_rf(
    samples: list[_ImageSample],
    *,
    dedupe_rf_siblings: bool,
    stats_by_source: dict[str, SourceStats],
) -> list[_ImageSample]:
    """精确 SHA256 去重与 Roboflow `.rf.` 同源多副本标记/可选去重."""
    if not samples:
        return []

    # 先按文件 SHA256 精确去重（保留优先级最高的代表样本）
    sha_groups: dict[str, list[int]] = defaultdict(list)
    for idx, s in enumerate(samples):
        sha_groups[s.content_sha256].append(idx)

    after_sha: list[_ImageSample] = []
    for sha in sorted(sha_groups):
        idxs = sorted(sha_groups[sha], key=lambda i: _sample_rank_key(samples[i]))
        after_sha.append(samples[idxs[0]])
        for dup_i in idxs[1:]:
            stats_by_source[samples[dup_i].source_name].deduped_sha256 += 1

    # 统计同一 (source_name, rf_orig_stem) 出现次数：>1 说明存在 Roboflow 多副本增广簇
    rf_counts: Counter[tuple[str, str]] = Counter()
    for s in after_sha:
        if s.rf_orig_stem is not None:
            rf_counts[(s.source_name, s.rf_orig_stem)] += 1
    for s in after_sha:
        if s.rf_orig_stem is not None and rf_counts[(s.source_name, s.rf_orig_stem)] > 1:
            s.is_preaugmented = True
            s.val_test_eligible = False

    if not dedupe_rf_siblings:
        return after_sha

    rf_buckets: dict[tuple[str, str], list[int]] = defaultdict(list)
    kept: list[_ImageSample] = []
    for idx, s in enumerate(after_sha):
        if s.rf_orig_stem is None:
            kept.append(s)
        else:
            rf_buckets[(s.source_name, s.rf_orig_stem)].append(idx)
    for key in sorted(rf_buckets):
        idxs = sorted(rf_buckets[key], key=lambda i: _sample_rank_key(after_sha[i]))
        kept.append(after_sha[idxs[0]])
        for dup_i in idxs[1:]:
            stats_by_source[after_sha[dup_i].source_name].deduped_rf_siblings += 1
    return kept


def _cluster_and_dedupe_phash(
    samples: list[_ImageSample],
    *,
    phash_threshold: int,
    dedupe: bool,
    stats_by_source: dict[str, SourceStats],
) -> list[_ImageSample]:
    """用向量化 64-bit pHash Hamming 距离做近重复聚簇（可选删除冗余副本）。"""
    if not samples:
        return []

    np: Any = __import__("numpy")
    n = len(samples)
    hashes = np.array([s.phash64 for s in samples], dtype=np.uint64)
    uf = _UnionFind(n)

    exact_buckets: dict[int, int] = {}
    unique_indices: list[int] = []
    for idx, s in enumerate(samples):
        first = exact_buckets.get(s.phash64)
        if first is not None:
            uf.union(first, idx)
        else:
            exact_buckets[s.phash64] = idx
            unique_indices.append(idx)

    if phash_threshold > 0 and len(unique_indices) > 1:
        u_idx_arr = np.array(unique_indices, dtype=np.int64)
        u_hashes = hashes[u_idx_arr]
        m = len(unique_indices)
        chunk_size = 512
        for start in range(0, m, chunk_size):
            end = min(m, start + chunk_size)
            block = u_hashes[start:end, None] ^ u_hashes[None, :]
            dists = np.bitwise_count(block)
            rows, cols = np.where(dists <= phash_threshold)
            for r_off, c_idx in zip(rows.tolist(), cols.tolist(), strict=True):
                u_i = start + r_off
                if c_idx > u_i:
                    uf.union(int(u_idx_arr[u_i]), int(u_idx_arr[c_idx]))

    clusters: dict[int, list[int]] = defaultdict(list)
    for idx in range(n):
        root = uf.find(idx)
        samples[idx].phash_cluster = root
        clusters[root].append(idx)

    if not dedupe:
        return samples

    kept: list[_ImageSample] = []
    for root in sorted(clusters):
        member_indices = sorted(clusters[root], key=lambda i: _sample_rank_key(samples[i]))
        kept.append(samples[member_indices[0]])
        for dup_idx in member_indices[1:]:
            stats_by_source[samples[dup_idx].source_name].deduped_phash += 1
    return kept


def _deterministic_hash(seed: int, key: str) -> str:
    return hashlib.sha256(f"{seed}:{key}".encode()).hexdigest()


def _out_names(cfg: DatasetPrepConfig) -> dict[int, str]:
    """`merged_v7` 输出类表：`occluded` 弃用（`cid==3`）时从 `TARGET_NAMES` 剔除."""
    if cfg.clean_drop_occluded:
        return {k: v for k, v in TARGET_NAMES.items() if k != 3}
    return TARGET_NAMES


def _clean_samples(
    samples: list[_ImageSample],
    *,
    cfg: DatasetPrepConfig,
) -> tuple[list[_ImageSample], dict[str, Any]]:
    """merged_v6 清洗管线：夜间过滤 + `occluded` 自证重标 + `damaged` 子集过滤.

    依据 `task-161` 实测：`occluded` 类 `~95%` 错标（楼窗/高架灯/树叶/杆上小物），
    自证重标仅 `~22%` 可归向；`damaged` 混「杆体磨损」与「灯头损坏」；
    `off`/`on` 白天帧混入为 `off` `P≈44%` 与 `on` 漏标的根因。零人工标注。

    Args:
        samples: `_cluster_and_dedupe_phash` 产出的全量样本。
        cfg: `DatasetPrepConfig`（`clean_*` 开关）。

    Returns:
        `(cleaned_samples, clean_stats)`：`cleaned_samples` 为夜间过滤 + 类清洗后的样本
        （`s.boxes` 已原地更新）；`clean_stats` 为清洗漏斗计数（供 `prep_manifest.json`）。
    """
    stats: dict[str, Any] = {
        "day_dropped_images": 0,
        "day_dropped_by_source": Counter(),
        "day_dropped_boxes_by_cls": Counter(),
        "occluded_relabeled_on": 0,
        "occluded_deleted": 0,
        "damaged_dropped_nonlamp_images": 0,
        "damaged_dropped_nonlamp_boxes": 0,
        "damaged_kept_lamp_overlap": 0,
        "empty_frames_dropped": 0,
    }
    cv2: Any = __import__("cv2")

    # `occluded` 自证重标模型（`best.pt`，`conf>=0.65` 判 `on` 则 `3`→`0`）与
    # `damaged` 灯头区过滤模型（`best.pt`，`conf>=0.25` 检出灯具框）共用懒加载
    occl_model = None
    lamp_model = None
    if cfg.clean_occluded_model is not None or cfg.clean_damaged_lamp_model is not None:
        os.environ["YOLO_AUTOINSTALL"] = "False"
        ultralytics_mod: Any = __import__("ultralytics")
        if cfg.clean_occluded_model is not None:
            occl_model = ultralytics_mod.YOLO(str(cfg.clean_occluded_model))
        if cfg.clean_damaged_lamp_model is not None:
            lamp_model = ultralytics_mod.YOLO(str(cfg.clean_damaged_lamp_model))

    def _n2x(c: Sequence[float]) -> tuple[float, float, float, float]:
        xc, yc, w, h = c
        return (xc - w / 2, yc - h / 2, xc + w / 2, yc + h / 2)

    def _iou(a: tuple[float, ...], b: tuple[float, ...]) -> float:
        ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
        ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
        iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
        inter = iw * ih
        if inter <= 0:
            return 0.0
        return inter / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter)

    kept: list[_ImageSample] = []
    for s in samples:
        # 夜间过滤：`mean_v` 超过阈值的帧整帧剔除（`off`/`on` 白天混入为污染根因）
        img = cv2.imread(str(s.image_path))
        if img is None:
            continue
        if cfg.clean_day_mean_v_max > 0.0:
            # 夜滤：仅删「`mean_v` 超阈值 **且** 全为 `on` 框」的帧——白天亮招牌误标的 `on` 该删，
            # 但白天含 `off`/`damaged`/`occluded` 的帧是合法样本（灭灯外观/损坏/遮挡不因白天失效）
            v = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)[:, :, 2].astype(float)
            if float(v.mean()) > cfg.clean_day_mean_v_max and all(cid == 0 for cid, *_ in s.boxes):
                stats["day_dropped_images"] += 1
                stats["day_dropped_by_source"][s.source_name] += 1
                for cid, *_ in s.boxes:
                    stats["day_dropped_boxes_by_cls"][cid] += 1
                continue

        occl_results = None
        lamp_results = None
        dropped_lamp_boxes = 0
        new_boxes: list[tuple[int, float, float, float, float]] = []
        for cid, xc, yc, bw, bh in s.boxes:
            if cid == 3 and occl_model is not None:
                # `occluded` 自证重标：框区判 `on`(cid=0) 且 `conf>=0.65` → `3`→`0`，否则删框
                if occl_results is None:
                    occl_results = occl_model.predict(
                        str(s.image_path),
                        imgsz=1280,
                        conf=0.65,
                        iou=0.7,
                        verbose=False,
                    )[0]
                relabeled = False
                if occl_results.boxes is not None:
                    gb = _n2x((xc, yc, bw, bh))
                    for b in occl_results.boxes:
                        # `conf=0.65` 已做推理阈值过滤，`pcid==0` 即满足「`conf>=0.65` 判 `on`」
                        if (
                            int(b.cls[0]) == 0
                            and _iou(gb, _n2x(tuple(map(float, b.xywhn[0].tolist())))) >= 0.1
                        ):
                            relabeled = True
                            break
                if relabeled:
                    new_boxes.append((0, xc, yc, bw, bh))
                    stats["occluded_relabeled_on"] += 1
                else:
                    stats["occluded_deleted"] += 1
                continue
            if cid == 3 and cfg.clean_drop_occluded:
                # `task-165`：`occluded` 弃用——无自证模型时兜底删 `cid==3`（`nc=3` 输出）
                stats["occluded_deleted"] += 1
                continue
            if cid == 2 and lamp_model is not None:
                # `task-204`：`d3` `damaged` 灯头区过滤——`damaged` GT 框须与任一灯具
                # 检测框 `IoU>=0.1` 或 `GT` 中心点落在灯具框内，否则剔除（`d3` 杆体/
                # 基座/地坪误标 `damaged` `~82%` 实测，`task-199` 抽检依据）
                if lamp_results is None:
                    lamp_results = lamp_model.predict(
                        str(s.image_path),
                        imgsz=1280,
                        conf=0.25,
                        iou=0.7,
                        verbose=False,
                    )[0]
                gb = _n2x((xc, yc, bw, bh))
                lamp_ok = False
                if lamp_results.boxes is not None:
                    for b in lamp_results.boxes:
                        lb = _n2x(tuple(map(float, b.xywhn[0].tolist())))
                        if _iou(gb, lb) >= 0.1 or (lb[0] <= xc <= lb[2] and lb[1] <= yc <= lb[3]):
                            lamp_ok = True
                            break
                if lamp_ok:
                    stats["damaged_kept_lamp_overlap"] += 1
                    new_boxes.append((cid, xc, yc, bw, bh))
                else:
                    stats["damaged_dropped_nonlamp_boxes"] += 1
                    dropped_lamp_boxes += 1
                continue
            # `damaged` 全保留（`task-161` 已证「杆体 vs 灯头」语义混杂但不删，
            # 由 `D-30` 映射层按「设施损坏」语义处理；`lamp_model` 缺省时不过滤）
            new_boxes.append((cid, xc, yc, bw, bh))

        if dropped_lamp_boxes > 0:
            stats["damaged_dropped_nonlamp_images"] += 1

        if new_boxes:
            s.boxes = new_boxes
            kept.append(s)
        else:
            stats["empty_frames_dropped"] += 1

    return kept, stats
