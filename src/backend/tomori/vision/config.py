"""集中配置：阈值、上限与枚举。

**全部数值均为工程初值，未经真实巡查样本实测标定。**
改动任何阈值必须同步 `.team/notes/vision-dev/stage1-vision-spec-input.md` §1.3 / §2.1，
并在 `docs/` 里保留「待实测标定」限定语。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Final

# --- 推理超时（后端代码常量，不入 `.env`，`docs/DESIGN.md` §1.1 / §5.2 唯一事实源）---
VISION_TIMEOUT_SECONDS: Final[float] = 60.0

# --- 模型契约（`D-06` / `D-28`：`yolo26s.pt` + `imgsz=1280` (2×640) + 无 NMS 300 检出上限）---
EXPECTED_YOLO_IMG_SIZE: Final[int] = 1280
ONNX_MAX_DET: Final[int] = 300
FALLBACK_MODEL_NAME: Final[str] = "yolo26s.pt"
DEFAULT_AUTOINSTALL: Final[bool] = False

# --- 输入白名单硬上限 ---
MAX_UPLOAD_BYTES: Final = 15 * 1024 * 1024  # 15 MiB，与 .env.example MAX_UPLOAD_SIZE_MB 一致
MAX_IMAGE_PIXELS: Final = 40_000_000  # 40 MP，防解压炸弹
MAX_FILES_PER_REQUEST: Final = 10
MAX_IMAGE_EDGE: Final = 12_000  # 单边像素上限，先于解码拦截

ALLOWED_EXTENSIONS: Final = frozenset({".jpg", ".jpeg", ".png", ".webp"})

# 视频容器魔数（用于把「视频伪装成图片」与「一般非图片」区分开）
VIDEO_MAGIC_PREFIXES: Final = (
    b"\x00\x00\x00\x18ftyp",  # MP4 / MOV
    b"\x00\x00\x00\x1cftyp",
    b"\x00\x00\x00\x20ftyp",
    b"ftyp",  # 兜底：as ftyp 出现在偏移 4
    b"\x1a\x45\xdf\xa3",  # Matroska / WebM
    b"RIFF",  # 需再看 WEBP/AVI 判别（见 whitelist）
    b"\x00\x00\x01\xba",  # MPEG-PS
    b"\x00\x00\x01\xb3",  # MPEG video
    b"FLV\x01",
    b"OggS",
    b"#!",
)
STREAM_URL_SCHEMES: Final = ("rtsp://", "rtsps://", "rtmp://", "rtmps://", "mms://", "srt://")
STREAM_URL_SUFFIXES: Final = (".m3u8", ".mpd", ".ts", ".m4s")

# --- OpenCV 物理度量阈值（工程初值，待实测标定）---
LAPLACIAN_BLUR_FAIL: Final = 20.0  # 低于此值判定为严重失焦 → quality_grade=fail
LAPLACIAN_BLUR_WARN: Final = 60.0  # 介于 fail 与 warn 之间 → quality_grade=warn
OVEREXPOSED_V: Final = 250  # V >= 250 视为过曝饱和
OVEREXPOSED_RATIO_FAIL: Final = 0.35  # 过曝像素占比超此值 → fail
UNDEREXPOSED_V: Final = 12  # V <= 12 视为欠曝（近全黑）
UNDEREXPOSED_RATIO_FAIL: Final = 0.85  # 欠曝占比超此值 → fail（全黑图）
OVEREXPOSED_RATIO_WARN: Final = 0.15
UNDEREXPOSED_RATIO_WARN: Final = 0.60

# 昼夜判定（HSV V 通道 0..255）
DAY_V_MEDIAN: Final = 115
DAY_SKY_V_MEDIAN: Final = 140
NIGHT_V_MEDIAN: Final = 65
NIGHT_SKY_V_MEDIAN: Final = 75

# 灯头 ROI 发光验算
ROI_HIGHLIGHT_V: Final = 195  # roi_v_p95 >= 此值 → 疑似点亮
ROI_DARK_V: Final = 145  # roi_v_p95 < 此值 → 疑似熄灭
ROI_STRONG_HIGHLIGHT_V: Final = 225
ROI_MIN_LUMINANCE_CONTRAST: Final = 65  # 点亮所需与局部背景的最小亮度差
ROI_MAX_DARK_CONTRAST: Final = 40
ROI_STRONG_CONTRAST: Final = 80
ROI_WEAK_CONTRAST: Final = 25
ROI_SATURATED_V: Final = 235

# HSV 中 V 的下标
V_CHANNEL: Final = 2

# 遮挡 / 植被
OCCLUSION_OVERLAP_MIN: Final = 0.30
FOLIAGE_HUE_MIN: Final = 35  # OpenCV H 0..179
FOLIAGE_HUE_MAX: Final = 85
FOLIAGE_SAT_MIN: Final = 40
FOLIAGE_VAL_MIN: Final = 30
FOLIAGE_RATIO_DAMAGED_HINT: Final = 0.18  # ROI 内绿植占比高于此值提示可能被树冠遮挡

# --- 置信度 ---
BASELINE_CONFIDENCE_CAP: Final = 0.65  # 基线模式硬上限，与 DB CHECK 一致
CONFLICT_CONFIDENCE_CAP: Final = 0.45
BASELINE_BASE_CONFIDENCE: Final = 0.55  # 基线启发式的起始置信度（永不超过 0.65）

# --- 枚举（与 backend-dev / frontend-dev 冻结契约一致）---
RAW_CLASSES: Final = (
    "streetlight_on",
    "streetlight_off",
    "streetlight_damaged",
    "streetlight_occluded",
)
FAULT_TYPES: Final = ("LAMP_OUT", "FIXTURE_DAMAGED", "OCCLUDED", "OTHER")
VISUAL_SIGNAL_STATES: Final = (
    "LAMP_OUT",
    "FIXTURE_DAMAGED",
    "OCCLUDED",
    "DAYTIME_ALWAYS_ON_SUSPECT",
    "NORMAL_LIT",
    "DAYTIME_UNPOWERED_EXPECTED",
)
INFERENCE_MODES: Final = ("yolo26_onnx_e2e", "yolo26_pt", "baseline_pretrained_opencv")
QUALITY_GRADES: Final = ("pass", "warn", "fail")
SCENE_LIGHTINGS: Final = ("day", "night", "twilight")
FUSION_STATUSES: Final = (
    "consistent",
    "daytime_unpowered_suppressed",
    "low_confidence_conflict",
    "occlusion_merged",
    "baseline_heuristic",
)
SEVERITY_HINTS: Final[tuple[str, ...]] = ("none", "P1", "P2", "P3", "P4")


@dataclass(frozen=True)
class Settings:
    """运行时设置，全部来自环境变量，缺省值只对本地开发有意义。"""

    upload_dir: Path
    max_upload_bytes: int
    yolo_weights_path: Path
    yolo_inference_enabled: bool
    yolo_inference_mode: str
    yolo_img_size: int
    yolo_conf_threshold: float
    yolo_device: str
    yolo_fallback_model: str = FALLBACK_MODEL_NAME
    yolo_iou_threshold: float = 0.45
    yolo_autoinstall: bool = DEFAULT_AUTOINSTALL
    vision_timeout_seconds: float = VISION_TIMEOUT_SECONDS

    @property
    def output_dir(self) -> Path:
        return self.upload_dir / "vision"


def _env_bool(key: str, default: bool) -> bool:
    raw = os.environ.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def load_settings() -> Settings:
    """从环境变量读取配置；不读取 `.env` 文件（那是后端/`dev-ops` 的职责）。"""
    max_mb = int(os.environ.get("MAX_UPLOAD_SIZE_MB", "15"))
    return Settings(
        upload_dir=Path(os.environ.get("UPLOAD_DIR", "./uploads")),
        max_upload_bytes=max_mb * 1024 * 1024,
        yolo_weights_path=Path(os.environ.get("YOLO_WEIGHTS_PATH", "./vision/weights/best.pt")),
        yolo_inference_enabled=_env_bool("YOLO_INFERENCE_ENABLED", True),
        yolo_inference_mode=os.environ.get("YOLO_INFERENCE_MODE", "auto").strip().lower(),
        yolo_img_size=int(os.environ.get("YOLO_IMG_SIZE", str(EXPECTED_YOLO_IMG_SIZE))),
        yolo_conf_threshold=float(os.environ.get("YOLO_CONF_THRESHOLD", "0.25")),
        yolo_device=os.environ.get("YOLO_DEVICE", "cpu"),
        yolo_fallback_model=os.environ.get("YOLO_FALLBACK_MODEL", FALLBACK_MODEL_NAME).strip(),
        yolo_iou_threshold=float(os.environ.get("YOLO_IOU_THRESHOLD", "0.45")),
        yolo_autoinstall=_env_bool("YOLO_AUTOINSTALL", DEFAULT_AUTOINSTALL),
    )
