"""输入白名单：四层硬安全门禁。

层级顺序与 `docs/API.md` §5.1 逐步对齐，
但视觉侧**独立再校验一遍**（纵深防御，视觉侧不是信任边界）。

1. 字节非空 + 体积上限      → 0 字节 422 VALIDATION_ERROR；> 15 MiB 413 IMAGE_SIZE_EXCEEDED
2. 扩展名 + 魔数（不信 MIME）→ 415 UNSUPPORTED_IMAGE_FORMAT
3. 动图多帧 → 415 UNSUPPORTED_IMAGE_FORMAT；视频容器 / 实时流 → 415 MEDIA_VIDEO_STREAM_FORBIDDEN
4. 像素上限（只读头先于解码）→ 422 IMAGE_PIXEL_LIMIT_EXCEEDED
"""

from __future__ import annotations

import io
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from PIL import Image, UnidentifiedImageError

from tomori.vision import config
from tomori.vision.errors import (
    IMAGE_PIXEL_LIMIT_EXCEEDED,
    IMAGE_SIZE_EXCEEDED,
    MEDIA_VIDEO_STREAM_FORBIDDEN,
    UNSUPPORTED_IMAGE_FORMAT,
    VALIDATION_ERROR,
    VisionError,
)

_JPEG_MAGIC = b"\xff\xd8\xff"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_MIME_BY_FORMAT = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}


@dataclass(frozen=True)
class ValidatedImage:
    """通过白名单后交付给下游的不可变描述。"""

    source_name: str
    detected_format: str  # "JPEG" | "PNG" | "WEBP"
    detected_mime: str
    size_bytes: int
    width: int
    height: int


def sniff_format(head: bytes) -> str | None:
    """按魔数判定格式，返回 "JPEG"/"PNG"/"WEBP"，无法判定返回 None。"""
    if head.startswith(_JPEG_MAGIC):
        return "JPEG"
    if head.startswith(_PNG_MAGIC):
        return "PNG"
    # RIFF....WEBP：偏移 0..3 是 RIFF，偏移 8..11 是 WEBP
    if len(head) >= 12 and head[0:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "WEBP"
    return None


def looks_like_video(head: bytes) -> bool:
    """识别视频容器魔数。RIFF 单独出现不算视频（AVI 是 RIFF+AVI ）。"""
    if len(head) >= 12 and head[0:4] == b"RIFF" and head[8:12] == b"AVI ":
        return True
    for magic in (b"\x00\x00\x00\x18ftyp", b"\x00\x00\x00\x1cftyp", b"\x00\x00\x00\x20ftyp"):
        if head.startswith(magic):
            return True
    if len(head) >= 8 and head[4:8] == b"ftyp":
        return True
    return head.startswith(
        (b"\x1a\x45\xdf\xa3", b"\x00\x00\x01\xba", b"\x00\x00\x01\xb3", b"FLV\x01", b"OggS")
    )


def reject_stream_url(value: str) -> None:
    """拒绝任何形似流地址的输入（防绕过静态图片限制）。"""
    lowered = value.strip().lower()
    # pathlib.Path 会把 `rtsp://` 规范化为 `rtsp:/`，需同时拦截两种前缀形态
    collapsed_schemes = tuple(scheme.replace("://", ":/") for scheme in config.STREAM_URL_SCHEMES)
    if lowered.startswith(config.STREAM_URL_SCHEMES) or lowered.startswith(collapsed_schemes):
        raise VisionError(
            MEDIA_VIDEO_STREAM_FORBIDDEN,
            "仅支持静态巡查图片，不接受视频流地址",
            {"detected_kind": "STREAM_URL"},
        )
    path_part = lowered.split("?", 1)[0].split("#", 1)[0]
    if path_part.endswith(config.STREAM_URL_SUFFIXES):
        raise VisionError(
            MEDIA_VIDEO_STREAM_FORBIDDEN,
            "仅支持静态巡查图片，不接受视频流清单文件",
            {"detected_kind": "STREAM_URL"},
        )


def _check_extension(path: Path) -> None:
    suffix = path.suffix.lower()
    if suffix not in config.ALLOWED_EXTENSIONS:
        raise VisionError(
            UNSUPPORTED_IMAGE_FORMAT,
            f"不支持的图片扩展名 {suffix or '(无)'}，仅支持 JPEG / PNG / WebP",
            {
                "detected_mime": None,
                "file_index": 0,
                "expected": sorted(config.ALLOWED_EXTENSIONS),
                "detected_suffix": suffix,
            },
        )


def _check_size(size_bytes: int, max_bytes: int) -> None:
    if size_bytes <= 0:
        raise VisionError(
            VALIDATION_ERROR,
            "上传图片为空文件（0 字节），无法处理",
            {"file_index": 0, "size_bytes": size_bytes, "reason": "EMPTY_BUFFER"},
        )
    if size_bytes > max_bytes:
        raise VisionError(
            IMAGE_SIZE_EXCEEDED,
            f"单张图片 {size_bytes} 字节超过上限 {max_bytes} 字节",
            {"file_index": 0, "size_bytes": size_bytes, "max_bytes": max_bytes},
        )


def _check_reject_stream_url_input(source: str) -> None:
    reject_stream_url(source)


def _check_container(head: bytes) -> str:
    detected = sniff_format(head)
    if detected is not None:
        return detected
    # 不是合法图片：优先判为视频流，否则判为非白名单格式
    if looks_like_video(head):
        raise VisionError(
            MEDIA_VIDEO_STREAM_FORBIDDEN,
            "检测到视频容器特征，仅支持单帧静态巡查图片",
            {"detected_kind": "VIDEO_MAGIC"},
        )
    raise VisionError(
        UNSUPPORTED_IMAGE_FORMAT,
        "文件头不符合 JPEG / PNG / WebP 魔数，疑似扩展名伪装",
        {"detected_mime": None, "file_index": 0, "expected": sorted(config.ALLOWED_EXTENSIONS)},
    )


def _check_frames_and_pixels(source: Path | IO[bytes]) -> tuple[int, int]:
    """用 Pillow 只读头部：拦多帧动图（415 UNSUPPORTED_IMAGE_FORMAT）与像素超限（422）。"""
    prev_max_pixels = Image.MAX_IMAGE_PIXELS
    effective_max_pixels = (
        min(prev_max_pixels, config.MAX_IMAGE_PIXELS)
        if prev_max_pixels is not None
        else config.MAX_IMAGE_PIXELS
    )
    Image.MAX_IMAGE_PIXELS = effective_max_pixels
    try:
        with warnings.catch_warnings():
            # Pillow 在 1× MAX_IMAGE_PIXELS 发 Warning、2× 抛 Error；我们统一当错误处理
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(source) as probe:
                n_frames = int(getattr(probe, "n_frames", 1))
                width, height = probe.size
    except Image.DecompressionBombError as exc:
        raise VisionError(
            IMAGE_PIXEL_LIMIT_EXCEEDED,
            "图片像素总量超过安全上限，疑似解压炸弹",
            {"width": 0, "height": 0, "pixels": 0, "limit": config.MAX_IMAGE_PIXELS},
        ) from exc
    except Image.DecompressionBombWarning as exc:
        raise VisionError(
            IMAGE_PIXEL_LIMIT_EXCEEDED,
            "图片像素总量超过安全上限，疑似解压炸弹",
            {"width": 0, "height": 0, "pixels": 0, "limit": config.MAX_IMAGE_PIXELS},
        ) from exc
    except UnidentifiedImageError as exc:
        raise VisionError(
            UNSUPPORTED_IMAGE_FORMAT,
            "无法识别图片内容，文件可能已损坏或并非图片",
            {"detected_mime": None, "file_index": 0, "expected": sorted(config.ALLOWED_EXTENSIONS)},
        ) from exc
    except OSError as exc:
        raise VisionError(
            UNSUPPORTED_IMAGE_FORMAT,
            f"图片解码头信息失败：{exc}",
            {"detected_mime": None, "file_index": 0, "expected": sorted(config.ALLOWED_EXTENSIONS)},
        ) from exc
    finally:
        Image.MAX_IMAGE_PIXELS = (
            prev_max_pixels if prev_max_pixels is not None else config.MAX_IMAGE_PIXELS
        )

    if n_frames != 1:
        # 二次裁决（docs/API.md §3.5）：动图归格式问题 UNSUPPORTED_IMAGE_FORMAT，不污染流红线统计
        raise VisionError(
            UNSUPPORTED_IMAGE_FORMAT,
            f"检测到 {n_frames} 帧动图载荷，仅支持单帧静态图片",
            {"detected_kind": "ANIMATED_IMAGE", "n_frames": n_frames},
        )
    pixels = width * height
    if pixels > config.MAX_IMAGE_PIXELS:
        raise VisionError(
            IMAGE_PIXEL_LIMIT_EXCEEDED,
            f"图片像素 {pixels} 超过上限 {config.MAX_IMAGE_PIXELS}",
            {"width": width, "height": height, "pixels": pixels, "limit": config.MAX_IMAGE_PIXELS},
        )
    if max(width, height) > config.MAX_IMAGE_EDGE:
        raise VisionError(
            IMAGE_PIXEL_LIMIT_EXCEEDED,
            f"图片单边尺寸 {max(width, height)} 超过上限 {config.MAX_IMAGE_EDGE}",
            {"width": width, "height": height, "pixels": pixels, "limit": config.MAX_IMAGE_PIXELS},
        )
    return width, height


def validate_bytes(
    raw: bytes,
    filename: str | None = None,
    *,
    max_bytes: int | None = None,
) -> ValidatedImage:
    """对内存字节流执行四层白名单校验（严格按 `docs/API.md` §5.1 优先级）。"""
    size_bytes = len(raw)
    _check_size(size_bytes, max_bytes if max_bytes is not None else config.MAX_UPLOAD_BYTES)
    if filename is not None:
        _check_reject_stream_url_input(filename)
        _check_extension(Path(filename))
    detected_format = _check_container(raw[:16])
    width, height = _check_frames_and_pixels(io.BytesIO(raw))
    return ValidatedImage(
        source_name=Path(filename).name if filename else "<bytes>",
        detected_format=detected_format,
        detected_mime=_MIME_BY_FORMAT[detected_format],
        size_bytes=size_bytes,
        width=width,
        height=height,
    )


def validate_file(path: Path, *, max_bytes: int | None = None) -> ValidatedImage:
    """对磁盘静态图执行四层白名单校验（严格按 `docs/API.md` §5.1 优先级）。"""
    if not path.is_file():
        _check_reject_stream_url_input(str(path))
        raise VisionError(
            UNSUPPORTED_IMAGE_FORMAT,
            f"路径不是可读文件：{path}",
            {"detected_mime": None, "file_index": 0, "expected": sorted(config.ALLOWED_EXTENSIONS)},
        )
    size_bytes = path.stat().st_size
    _check_size(size_bytes, max_bytes if max_bytes is not None else config.MAX_UPLOAD_BYTES)
    _check_reject_stream_url_input(str(path))
    _check_extension(path)
    with path.open("rb") as fh:
        head = fh.read(16)
    detected_format = _check_container(head)
    width, height = _check_frames_and_pixels(path)
    return ValidatedImage(
        source_name=path.name,
        detected_format=detected_format,
        detected_mime=_MIME_BY_FORMAT[detected_format],
        size_bytes=size_bytes,
        width=width,
        height=height,
    )
