"""图像解码：OpenCV `cv2.imdecode` 双路径异常隔离 + EXIF 归一化。

EXIF 归一化责任在视觉侧（`docs/DESIGN.md` §5.1 / `stage1-vision-spec-input.md` §4.1 不变量 4）：
先 `ImageOps.exif_transpose()` 转正再推理，并把**归一化后**的宽高写入契约。

`cv2.imdecode` 已实测双分支行为（OpenCV 5.1.0）：
1. 传入 0 字节 `b""` → 直接抛 `cv2.error: (-215:Assertion failed) !buf.empty()`；
   因此必须在进入 `cv2.imdecode` 前先判 `len(raw) == 0` → 抛 `422 VALIDATION_ERROR`。
2. 传入非空损坏字节流 → 静默返回 `None`；
   因此必须在 `cv2.imdecode` 后判 `img is None or img.size == 0` →
   抛 `415 UNSUPPORTED_IMAGE_FORMAT`。
"""

from __future__ import annotations

import io
import warnings
from pathlib import Path

import cv2
import numpy as np
from numpy.typing import NDArray
from PIL import Image, ImageOps, UnidentifiedImageError

from tomori.vision import config
from tomori.vision.errors import (
    IMAGE_PIXEL_LIMIT_EXCEEDED,
    UNSUPPORTED_IMAGE_FORMAT,
    VALIDATION_ERROR,
    VisionError,
)

_U8 = NDArray[np.uint8]


def decode_imdecode_bgr(raw: bytes) -> _U8:
    """使用 `cv2.imdecode` 解码内存字节流，显式隔离空 buffer 与损坏字节双异常路径。"""
    if len(raw) == 0:
        raise VisionError(
            VALIDATION_ERROR,
            "输入图片字节流为空（0 字节），无法解码",
            {"reason": "EMPTY_BUFFER", "size_bytes": 0},
        )

    buf = np.frombuffer(raw, dtype=np.uint8)
    try:
        img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    except cv2.error as exc:
        if "MAX_IMAGE_PIXELS" in str(exc):
            raise VisionError(
                IMAGE_PIXEL_LIMIT_EXCEEDED,
                "图片像素总量超过 OpenCV 安全上限",
                {"width": 0, "height": 0, "pixels": 0, "limit": config.MAX_IMAGE_PIXELS},
            ) from exc
        raise VisionError(
            UNSUPPORTED_IMAGE_FORMAT,
            f"OpenCV 解码图片失败：{exc}",
            {
                "reason": "CORRUPT_IMAGE_BYTES",
                "size_bytes": len(raw),
                "expected": sorted(config.ALLOWED_EXTENSIONS),
            },
        ) from exc

    if img is None or img.size == 0:
        raise VisionError(
            UNSUPPORTED_IMAGE_FORMAT,
            "图片字节流已损坏或无法解码，cv2.imdecode 返回空矩阵",
            {
                "reason": "CORRUPT_IMAGE_BYTES",
                "size_bytes": len(raw),
                "expected": sorted(config.ALLOWED_EXTENSIONS),
            },
        )

    if img.ndim != 3 or img.shape[2] != 3:
        raise VisionError(
            UNSUPPORTED_IMAGE_FORMAT,
            "解码结果不是三通道 BGR 图像",
            {"reason": "INVALID_CHANNELS", "shape": list(img.shape)},
        )

    return np.asarray(img, dtype=np.uint8)


def decode_bytes_to_bgr(raw: bytes) -> _U8:
    """解码字节流为 EXIF 转正后的 BGR `np.ndarray`（`uint8`, `HxWx3`）。

    只把解码后的内存数组交给下游 —— **绝不把用户路径/URL 传给 YOLO**
    （Ultralytics `source=` 原生支持 rtsp/mp4/摄像头索引，是注入面）。
    """
    if len(raw) == 0:
        raise VisionError(
            VALIDATION_ERROR,
            "输入图片字节流为空（0 字节），无法解码",
            {"reason": "EMPTY_BUFFER", "size_bytes": 0},
        )

    # 先通过 cv2.imdecode 确认可由 OpenCV 正常解码（空 buffer -> 422；损坏字节 -> 415）
    _ = decode_imdecode_bgr(raw)

    prev_max_pixels = Image.MAX_IMAGE_PIXELS
    effective_max_pixels = (
        min(prev_max_pixels, config.MAX_IMAGE_PIXELS)
        if prev_max_pixels is not None
        else config.MAX_IMAGE_PIXELS
    )
    Image.MAX_IMAGE_PIXELS = effective_max_pixels
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            try:
                with Image.open(io.BytesIO(raw)) as pil_img:
                    width, height = pil_img.size
                    pixels = width * height
                    if (
                        pixels > config.MAX_IMAGE_PIXELS
                        or max(width, height) > config.MAX_IMAGE_EDGE
                    ):
                        raise VisionError(
                            IMAGE_PIXEL_LIMIT_EXCEEDED,
                            f"图片尺寸 {width}x{height} 超出安全像素上限",
                            {
                                "width": width,
                                "height": height,
                                "pixels": pixels,
                                "limit": config.MAX_IMAGE_PIXELS,
                            },
                        )
                    upright = ImageOps.exif_transpose(pil_img)
                    if upright is None:
                        upright = pil_img
                    rgb = upright.convert("RGB")
                    array = np.asarray(rgb, dtype=np.uint8)
            except VisionError:
                raise
            except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
                raise VisionError(
                    IMAGE_PIXEL_LIMIT_EXCEEDED,
                    "图片像素总量超过安全上限，疑似解压炸弹",
                    {"width": 0, "height": 0, "pixels": 0, "limit": config.MAX_IMAGE_PIXELS},
                ) from exc
            except (UnidentifiedImageError, OSError) as exc:
                raise VisionError(
                    UNSUPPORTED_IMAGE_FORMAT,
                    f"图片解码失败：{exc}",
                    {
                        "detected_mime": None,
                        "file_index": 0,
                        "expected": sorted(config.ALLOWED_EXTENSIONS),
                    },
                ) from exc
    finally:
        Image.MAX_IMAGE_PIXELS = (
            prev_max_pixels if prev_max_pixels is not None else config.MAX_IMAGE_PIXELS
        )

    if array.ndim != 3 or array.shape[2] != 3:
        raise VisionError(
            UNSUPPORTED_IMAGE_FORMAT,
            "解码结果不是三通道图像",
            {
                "detected_mime": None,
                "file_index": 0,
                "expected": sorted(config.ALLOWED_EXTENSIONS),
            },
        )

    bgr = cv2.cvtColor(array, cv2.COLOR_RGB2BGR)
    return np.asarray(bgr, dtype=np.uint8)


def decode_to_bgr(path: Path) -> _U8:
    """读取文件字节并委托 `decode_bytes_to_bgr` 解码为 EXIF 转正后的 BGR `np.ndarray`。"""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise VisionError(
            UNSUPPORTED_IMAGE_FORMAT,
            f"图片读取失败：{exc}",
            {
                "detected_mime": None,
                "file_index": 0,
                "expected": sorted(config.ALLOWED_EXTENSIONS),
            },
        ) from exc
    return decode_bytes_to_bgr(raw)
