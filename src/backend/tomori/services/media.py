"""4 层静态图像安全网关与本地磁盘存储服务（纯领域模块，绝不导入 FastAPI）。

严格对齐 `docs/API.md` §3.3 / §3.5 / §5 / §5.1 与 `docs/DESIGN.md` §4.3 / §5.1–§5.2：
1. 数量校验：`validate_upload_count`（`422 NO_FILES` / `422 TOO_MANY_FILES`）。
2. 流字段拦截：`reject_stream_fields`
   （`rtsp://`·`rtmp://`·`.m3u8`·`.mpd`·`stream_url`·`camera_*`
   → `415 MEDIA_VIDEO_STREAM_FORBIDDEN`）。
3. 5 步错误优先级与 4 层白名单：`validate_and_normalize_image`
   - ① 0 字节 → `422 VALIDATION_ERROR`；体积 > `max_bytes`（15 MiB）→ `413 IMAGE_SIZE_EXCEEDED`
   - ②/③ 视频容器魔数 / `video/*` MIME / 视频或流扩展名 → `415 MEDIA_VIDEO_STREAM_FORBIDDEN`；
          其他非白名单扩展名 / MIME / 魔数 → `415 UNSUPPORTED_IMAGE_FORMAT`
   - ④ 容器头多帧检测（PNG `acTL` 先于 `IDAT`、WebP `VP8X` 动画位 /
          Pillow `is_animated` / `n_frames > 1`）→ `415 UNSUPPORTED_IMAGE_FORMAT`
   - ⑤ 解码前像素与单边上限（总像素 `<= 40_000_000`、单边 `<= 12_000`、
          `DecompressionBombWarning` 转错误）→ `422 IMAGE_PIXEL_LIMIT_EXCEEDED`
   - ⑥ 解码 + `ImageOps.exif_transpose()` 方向归一化 + 重新编码纯净静态图字节 + `sha256`
4. 落盘与安全路径解析：`save_upload_file`、`cleanup_files`（D2 回滚）、
   `resolve_evidence_paths`（防 `..` 路径穿越）。
"""

from __future__ import annotations

import hashlib
import io
import re
import struct
import threading
import uuid
import warnings
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Final
from zoneinfo import ZoneInfo

from PIL import Image, ImageOps, UnidentifiedImageError

from tomori.services.exceptions import (
    IMAGE_PIXEL_LIMIT_EXCEEDED,
    IMAGE_SIZE_EXCEEDED,
    MEDIA_VIDEO_STREAM_FORBIDDEN,
    NO_FILES,
    TOO_MANY_FILES,
    UNSUPPORTED_IMAGE_FORMAT,
    VALIDATION_ERROR,
    AppError,
)
from tomori.settings import MAX_FILES_PER_REQUEST, MAX_IMAGE_PIXELS

__all__ = [
    "ALLOWED_IMAGE_EXTENSIONS",
    "ALLOWED_IMAGE_MIMES",
    "MAX_IMAGE_EDGE",
    "MAX_UPLOAD_BYTES",
    "MediaPathTraversalError",
    "ValidatedMediaFile",
    "cleanup_files",
    "reject_stream_fields",
    "resolve_evidence_paths",
    "save_upload_file",
    "validate_and_normalize_image",
    "validate_upload_count",
]

MAX_UPLOAD_BYTES: Final[int] = 15 * 1024 * 1024
MAX_IMAGE_EDGE: Final[int] = 12_000

ALLOWED_IMAGE_EXTENSIONS: Final[frozenset[str]] = frozenset({".jpg", ".jpeg", ".png", ".webp"})
ALLOWED_IMAGE_MIMES: Final[frozenset[str]] = frozenset({"image/jpeg", "image/png", "image/webp"})

_ACCEPTED_CONTENT_TYPES: Final[frozenset[str]] = frozenset(
    {"image/jpeg", "image/jpg", "image/png", "image/webp", "application/octet-stream"}
)
_VIDEO_EXTENSIONS: Final[frozenset[str]] = frozenset(
    {
        ".mp4",
        ".m4v",
        ".mov",
        ".avi",
        ".mkv",
        ".webm",
        ".flv",
        ".wmv",
        ".mpeg",
        ".mpg",
        ".m2v",
        ".3gp",
        ".3g2",
        ".ogv",
        ".rm",
        ".rmvb",
        ".asf",
        ".m3u8",
        ".mpd",
        ".ts",
        ".m4s",
    }
)
_STREAM_MANIFEST_MIMES: Final[frozenset[str]] = frozenset(
    {
        "application/vnd.apple.mpegurl",
        "application/x-mpegurl",
        "audio/mpegurl",
        "application/dash+xml",
    }
)

_JPEG_MAGIC: Final[bytes] = b"\xff\xd8\xff"
_PNG_MAGIC: Final[bytes] = b"\x89PNG\r\n\x1a\n"
_NON_VIDEO_FTYP_BRANDS: Final[frozenset[bytes]] = frozenset(
    {b"heic", b"heix", b"heim", b"heis", b"mif1", b"msf1", b"avif", b"avis"}
)
_FORMAT_META: Final[dict[str, tuple[str, str]]] = {
    "JPEG": ("jpg", "image/jpeg"),
    "PNG": ("png", "image/png"),
    "WEBP": ("webp", "image/webp"),
}

_STREAM_URL_SCHEMES: Final[tuple[str, ...]] = (
    "rtsp://",
    "rtsps://",
    "rtmp://",
    "rtmps://",
    "mms://",
    "srt://",
)
_STREAM_COLLAPSED_SCHEMES: Final[tuple[str, ...]] = tuple(
    scheme.replace("://", ":/") for scheme in _STREAM_URL_SCHEMES
)
_STREAM_SCHEME_RE: Final[re.Pattern[str]] = re.compile(
    r"(?i)(?:^|[\s\"'(=,;])(?:rtsp|rtsps|rtmp|rtmps|mms|srt):/"
)
_STREAM_MANIFEST_SUFFIXES: Final[tuple[str, ...]] = (".m3u8", ".mpd")
_FILENAME_STREAM_SUFFIXES: Final[tuple[str, ...]] = (".m3u8", ".mpd", ".ts", ".m4s")

_SUSPICIOUS_KEY_EXACT: Final[frozenset[str]] = frozenset(
    {
        "stream_url",
        "stream_uri",
        "video_url",
        "video_uri",
        "video_stream",
        "live_stream",
        "live_url",
        "rtsp_url",
        "rtmp_url",
        "hls_url",
        "mpd_url",
        "camera",
        "camera_url",
        "camera_uri",
        "camera_stream",
        "camera_ip",
    }
)
_SUSPICIOUS_KEY_PREFIXES: Final[tuple[str, ...]] = (
    "camera_",
    "stream_",
    "rtsp_",
    "rtmp_",
    "video_stream",
    "live_stream",
)
_SUSPICIOUS_KEY_SUFFIXES: Final[tuple[str, ...]] = (
    "_stream_url",
    "_stream_uri",
    "_rtsp_url",
    "_rtmp_url",
)

_SAFE_ANALYSIS_ID_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHANGHAI_TZ: Final[ZoneInfo] = ZoneInfo("Asia/Shanghai")
_PIL_LOCK = threading.Lock()


class MediaPathTraversalError(AppError, ValueError):
    """存储路径或 analysis_id 存在越界 / 路径穿越风险（同时兼容 AppError 与 ValueError）。"""


@dataclass(frozen=True, slots=True)
class ValidatedMediaFile:
    """通过 4 层安全网关并完成 EXIF 归一化后的静态图片载荷。"""

    content: bytes
    ext: str
    mime: str
    width: int
    height: int
    sha256: str
    size_bytes: int


def validate_upload_count(
    count: int,
    *,
    min_count: int = 1,
    max_count: int = MAX_FILES_PER_REQUEST,
) -> None:
    """校验单次请求上传文件个数范围（默认 1..10；人工报障传 `min_count=0`）。"""
    if count < 0:
        raise AppError(
            VALIDATION_ERROR,
            "上传文件数量不能为负数",
            {"count": count, "min_count": min_count, "max_count": max_count},
        )
    if count < min_count:
        if count == 0:
            raise AppError(
                NO_FILES,
                "至少需要上传 1 张静态巡查图片",
                {"count": count, "min_count": min_count, "max_count": max_count},
            )
        raise AppError(
            VALIDATION_ERROR,
            f"上传文件数量 {count} 少于最低要求 {min_count}",
            {"count": count, "min_count": min_count, "max_count": max_count},
        )
    if count > max_count:
        raise AppError(
            TOO_MANY_FILES,
            f"单次请求最多允许上传 {max_count} 个文件，当前为 {count} 个",
            {"count": count, "min_count": min_count, "max_count": max_count},
        )


def _is_suspicious_form_key(key: str) -> bool:
    lowered = key.strip().lower()
    if not lowered:
        return False
    return (
        lowered in _SUSPICIOUS_KEY_EXACT
        or lowered.startswith(_SUSPICIOUS_KEY_PREFIXES)
        or lowered.endswith(_SUSPICIOUS_KEY_SUFFIXES)
    )


def _contains_stream_indicator(
    value: str,
    *,
    suffixes: tuple[str, ...] = _STREAM_MANIFEST_SUFFIXES,
) -> bool:
    stripped = value.strip()
    if not stripped:
        return False
    lowered = stripped.lower()
    if lowered.startswith(_STREAM_URL_SCHEMES) or lowered.startswith(_STREAM_COLLAPSED_SCHEMES):
        return True
    if _STREAM_SCHEME_RE.search(stripped):
        return True
    for token in lowered.split():
        cleaned = token.strip("\"'()[]{}<>,;")
        path_part = cleaned.split("?", 1)[0].split("#", 1)[0]
        if path_part.endswith(suffixes):
            return True
    return False


def _inspect_value_for_streams(value: Any, *, key_name: str | None = None) -> None:
    if value is None or isinstance(value, (bytes, bytearray, memoryview, int, float, bool)):
        return
    if isinstance(value, str):
        if _contains_stream_indicator(value):
            details: dict[str, Any] = {"detected_kind": "STREAM_URL"}
            if key_name is not None:
                details["field"] = key_name
            raise AppError(
                MEDIA_VIDEO_STREAM_FORBIDDEN,
                "系统仅支持静态图片上传，严禁提交实时视频流地址或清单",
                details,
            )
        return
    if isinstance(value, Mapping):
        for sub_key, sub_val in value.items():
            sub_key_str = str(sub_key)
            if _is_suspicious_form_key(sub_key_str):
                raise AppError(
                    MEDIA_VIDEO_STREAM_FORBIDDEN,
                    f"禁止提交实时流/摄像头字段 {sub_key_str}",
                    {"detected_kind": "STREAM_FIELD", "field": sub_key_str},
                )
            _inspect_value_for_streams(sub_val, key_name=sub_key_str)
        return
    if isinstance(value, Iterable):
        for item in value:
            _inspect_value_for_streams(item, key_name=key_name)


def reject_stream_fields(
    fields: Mapping[str, Any] | Iterable[Any] | str | None = None,
    *,
    field_names: Iterable[str] | None = None,
    values: Iterable[Any] | None = None,
    **extra_fields: Any,
) -> None:
    """拦截表单键名（`stream_url`/`camera_*`）或字段值中的实时视频流地址/清单（415）。"""
    if field_names is not None:
        for key in field_names:
            key_str = str(key)
            if _is_suspicious_form_key(key_str):
                raise AppError(
                    MEDIA_VIDEO_STREAM_FORBIDDEN,
                    f"禁止提交实时流/摄像头字段 {key_str}",
                    {"detected_kind": "STREAM_FIELD", "field": key_str},
                )

    if values is not None:
        for val in values:
            _inspect_value_for_streams(val)

    if fields is not None:
        if isinstance(fields, (str, Mapping)):
            _inspect_value_for_streams(fields)
        else:
            for item in fields:
                if isinstance(item, tuple) and len(item) == 2:
                    key, val = item
                    key_str = str(key)
                    if _is_suspicious_form_key(key_str):
                        raise AppError(
                            MEDIA_VIDEO_STREAM_FORBIDDEN,
                            f"禁止提交实时流/摄像头字段 {key_str}",
                            {"detected_kind": "STREAM_FIELD", "field": key_str},
                        )
                    _inspect_value_for_streams(val, key_name=key_str)
                else:
                    _inspect_value_for_streams(item)

    if extra_fields:
        _inspect_value_for_streams(extra_fields)


def _sniff_image_format(head: bytes) -> str | None:
    if head.startswith(_JPEG_MAGIC):
        return "JPEG"
    if head.startswith(_PNG_MAGIC):
        return "PNG"
    if len(head) >= 12 and head[0:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "WEBP"
    return None


def _looks_like_video_magic(head: bytes) -> bool:
    if len(head) >= 12 and head[0:4] == b"RIFF" and head[8:12] == b"AVI ":
        return True
    if len(head) >= 8 and head[4:8] == b"ftyp":
        brand = head[8:12] if len(head) >= 12 else b""
        if brand not in _NON_VIDEO_FTYP_BRANDS:
            return True
    if head.startswith(
        (
            b"\x1a\x45\xdf\xa3",
            b"\x00\x00\x01\xba",
            b"\x00\x00\x01\xb3",
            b"FLV\x01",
            b"OggS",
            b"\x30\x26\xb2\x75\x8e\x66\xcf\x11",
        )
    ):
        return True
    stripped_head = head.lstrip()
    if stripped_head.startswith(b"#EXTM3U"):
        return True
    return stripped_head.startswith(b"<") and b"<MPD" in stripped_head


def _extract_filename_suffix(filename: str) -> str:
    basename = filename.strip().rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    return Path(basename).suffix.lower()


def _png_has_actl_before_idat(raw: bytes) -> bool:
    offset = 8
    total = len(raw)
    while offset + 8 <= total:
        length = struct.unpack(">I", raw[offset : offset + 4])[0]
        chunk_type = raw[offset + 4 : offset + 8]
        if chunk_type == b"acTL":
            return True
        if chunk_type in (b"IDAT", b"IEND"):
            return False
        next_offset = offset + 12 + length
        if next_offset <= offset or next_offset > total:
            return False
        offset = next_offset
    return False


def _webp_has_animation_flag(raw: bytes) -> bool:
    return (
        len(raw) >= 21
        and raw[0:4] == b"RIFF"
        and raw[8:12] == b"WEBP"
        and raw[12:16] == b"VP8X"
        and (raw[20] & 0x02) != 0
    )


def _extract_header_dimensions(raw: bytes, detected_format: str) -> tuple[int, int]:
    """在 Pillow 触发解压炸弹异常后，从容器头解析真实宽高用于诊断 details。"""
    if detected_format == "PNG":
        if len(raw) >= 24 and raw[12:16] == b"IHDR":
            w, h = struct.unpack(">II", raw[16:24])
            return int(w), int(h)
        return 0, 0

    if detected_format == "WEBP":
        if len(raw) < 30 or raw[:4] != b"RIFF" or raw[8:12] != b"WEBP":
            return 0, 0
        chunk = raw[12:16]
        if chunk == b"VP8 " and raw[23:26] == b"\x9d\x01\x2a":
            w, h = struct.unpack("<HH", raw[26:30])
            return w & 0x3FFF, h & 0x3FFF
        if chunk == b"VP8L" and raw[20] == 0x2F:
            b0, b1, b2, b3 = raw[21], raw[22], raw[23], raw[24]
            w = 1 + (((b1 & 0x3F) << 8) | b0)
            h = 1 + (((b3 & 0x0F) << 10) | (b2 << 2) | ((b1 & 0xC0) >> 6))
            return w, h
        if chunk == b"VP8X":
            w = 1 + int.from_bytes(raw[24:27], "little")
            h = 1 + int.from_bytes(raw[27:30], "little")
            return w, h
        return 0, 0

    if detected_format == "JPEG":
        pos = 2
        total = len(raw)
        while pos < total:
            while pos < total and raw[pos] != 0xFF:
                pos += 1
            while pos < total and raw[pos] == 0xFF:
                pos += 1
            if pos >= total:
                break
            marker = raw[pos]
            pos += 1
            if marker in (0x00, 0x01, 0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                continue
            if pos + 2 > total:
                break
            seg_len = struct.unpack(">H", raw[pos : pos + 2])[0]
            if seg_len < 2 or pos + seg_len > total:
                break
            if (0xC0 <= marker <= 0xCF) and marker not in (0xC4, 0xC8, 0xCC):
                if pos + 7 <= total:
                    h, w = struct.unpack(">HH", raw[pos + 3 : pos + 7])
                    return int(w), int(h)
                break
            pos += seg_len
    return 0, 0


def _check_dimensions(
    width: int,
    height: int,
    *,
    max_pixels: int,
    file_index: int,
) -> None:
    if width <= 0 or height <= 0:
        raise AppError(
            UNSUPPORTED_IMAGE_FORMAT,
            f"图片尺寸非法：{width}x{height}",
            {"file_index": file_index, "width": width, "height": height},
        )
    pixels = width * height
    if pixels > max_pixels or width > MAX_IMAGE_EDGE or height > MAX_IMAGE_EDGE:
        raise AppError(
            IMAGE_PIXEL_LIMIT_EXCEEDED,
            f"图片尺寸 {width}x{height}（{pixels} 像素）超过安全上限",
            {
                "file_index": file_index,
                "width": width,
                "height": height,
                "pixels": pixels,
                "max_pixels": max_pixels,
            },
        )


def _encode_clean_image(
    image: Image.Image,
    *,
    detected_format: str,
    max_bytes: int,
    file_index: int,
) -> bytes:
    out_img = image
    encoded = b""

    if detected_format == "JPEG":
        if out_img.mode == "P":
            out_img = out_img.convert("RGBA").convert("RGB")
        elif out_img.mode not in ("RGB", "L"):
            out_img = out_img.convert("RGB")
        for quality in (92, 85):
            buf = io.BytesIO()
            out_img.save(buf, format="JPEG", quality=quality, optimize=True)
            encoded = buf.getvalue()
            if len(encoded) <= max_bytes:
                return encoded
    elif detected_format == "PNG":
        if out_img.mode == "P" and "transparency" in out_img.info:
            out_img = out_img.convert("RGBA")
        elif out_img.mode not in ("1", "L", "LA", "P", "RGB", "RGBA"):
            out_img = out_img.convert("RGBA" if "A" in out_img.getbands() else "RGB")
        buf = io.BytesIO()
        out_img.save(buf, format="PNG", optimize=True)
        encoded = buf.getvalue()
        if len(encoded) <= max_bytes:
            return encoded
    elif detected_format == "WEBP":
        if out_img.mode == "P":
            out_img = out_img.convert("RGBA" if "transparency" in out_img.info else "RGB")
        elif out_img.mode not in ("RGB", "RGBA"):
            out_img = out_img.convert("RGBA" if "A" in out_img.getbands() else "RGB")
        for quality in (90, 82):
            buf = io.BytesIO()
            out_img.save(buf, format="WEBP", quality=quality)
            encoded = buf.getvalue()
            if len(encoded) <= max_bytes:
                return encoded
    else:
        raise AppError(
            UNSUPPORTED_IMAGE_FORMAT,
            f"不支持的图片格式: {detected_format}",
            {"file_index": file_index},
        )

    raise AppError(
        IMAGE_SIZE_EXCEEDED,
        f"第 {file_index + 1} 个文件规范化后大小 {len(encoded)} 字节超过上限 {max_bytes} 字节",
        {"file_index": file_index, "size_bytes": len(encoded), "max_bytes": max_bytes},
    )


def validate_and_normalize_image(
    raw: bytes,
    *,
    filename: str | None = None,
    content_type: str | None = None,
    file_index: int = 0,
    max_bytes: int = MAX_UPLOAD_BYTES,
    max_pixels: int = MAX_IMAGE_PIXELS,
) -> ValidatedMediaFile:
    """执行 5 步错误优先级校验、4 层白名单拦截与 EXIF 转正重编码。"""
    if max_bytes <= 0:
        raise ValueError("max_bytes 必须为正整数")
    if max_pixels <= 0:
        raise ValueError("max_pixels 必须为正整数")

    # ① 体积校验：0 字节 → 422 VALIDATION_ERROR；超上限 → 413（先于视频/格式检查）
    raw_size = len(raw)
    if raw_size == 0:
        raise AppError(
            VALIDATION_ERROR,
            "上传图片为空文件（0 字节），无法处理",
            {"file_index": file_index, "size_bytes": 0, "reason": "EMPTY_BUFFER"},
        )
    if raw_size > max_bytes:
        raise AppError(
            IMAGE_SIZE_EXCEEDED,
            f"第 {file_index + 1} 个文件大小 {raw_size} 字节超过上限 {max_bytes} 字节",
            {"file_index": file_index, "size_bytes": raw_size, "max_bytes": max_bytes},
        )

    # ②/③ 流标识与视频容器拦截（优先于通用 415 UNSUPPORTED_IMAGE_FORMAT）
    clean_filename = filename.strip() if filename is not None and filename.strip() else None
    if clean_filename is not None and _contains_stream_indicator(
        clean_filename, suffixes=_FILENAME_STREAM_SUFFIXES
    ):
        raise AppError(
            MEDIA_VIDEO_STREAM_FORBIDDEN,
            "仅支持静态巡查图片，严禁提交实时视频流地址或清单",
            {"file_index": file_index, "detected_kind": "STREAM_URL"},
        )

    if _looks_like_video_magic(raw[:512]):
        raise AppError(
            MEDIA_VIDEO_STREAM_FORBIDDEN,
            "检测到视频容器魔数，仅支持单帧静态巡查图片",
            {"file_index": file_index, "detected_kind": "VIDEO_MAGIC"},
        )

    normalized_mime: str | None = None
    if content_type is not None and content_type.strip():
        normalized_mime = content_type.split(";", 1)[0].strip().lower()
        if normalized_mime.startswith("video/") or normalized_mime in _STREAM_MANIFEST_MIMES:
            raise AppError(
                MEDIA_VIDEO_STREAM_FORBIDDEN,
                f"禁止上传视频类型载荷（{normalized_mime}），仅支持静态图片",
                {
                    "file_index": file_index,
                    "detected_kind": "VIDEO_MIME",
                    "content_type": normalized_mime,
                },
            )

    suffix: str | None = None
    if clean_filename is not None:
        suffix = _extract_filename_suffix(clean_filename)
        if suffix in _VIDEO_EXTENSIONS:
            raise AppError(
                MEDIA_VIDEO_STREAM_FORBIDDEN,
                f"禁止上传视频文件扩展名 {suffix}，仅支持静态图片",
                {
                    "file_index": file_index,
                    "detected_kind": "VIDEO_EXTENSION",
                    "extension": suffix,
                },
            )

    # 静态图片扩展名、MIME 与魔数白名单
    if suffix is not None and suffix not in ALLOWED_IMAGE_EXTENSIONS:
        raise AppError(
            UNSUPPORTED_IMAGE_FORMAT,
            f"不支持的图片扩展名 {suffix or '(无)'}，仅支持 JPEG / PNG / WebP",
            {
                "file_index": file_index,
                "detected_suffix": suffix,
                "expected": sorted(ALLOWED_IMAGE_EXTENSIONS),
            },
        )

    if normalized_mime is not None and normalized_mime not in _ACCEPTED_CONTENT_TYPES:
        raise AppError(
            UNSUPPORTED_IMAGE_FORMAT,
            f"不支持的媒体类型 {normalized_mime}，仅支持 JPEG / PNG / WebP",
            {
                "file_index": file_index,
                "detected_mime": normalized_mime,
                "expected": sorted(ALLOWED_IMAGE_MIMES),
            },
        )

    detected_format = _sniff_image_format(raw[:16])
    if detected_format is None:
        raise AppError(
            UNSUPPORTED_IMAGE_FORMAT,
            "文件魔数不符合 JPEG / PNG / WebP 静态图片签名",
            {
                "file_index": file_index,
                "detected_mime": normalized_mime,
                "expected": sorted(ALLOWED_IMAGE_EXTENSIONS),
            },
        )

    # ④ 容器头多帧动图检测（先于像素解压与尺寸解码）
    if detected_format == "PNG" and _png_has_actl_before_idat(raw):
        raise AppError(
            UNSUPPORTED_IMAGE_FORMAT,
            "检测到 APNG 多帧动图，仅支持单帧静态图片",
            {"file_index": file_index, "detected_kind": "ANIMATED_IMAGE"},
        )
    if detected_format == "WEBP" and _webp_has_animation_flag(raw):
        raise AppError(
            UNSUPPORTED_IMAGE_FORMAT,
            "检测到动态 WebP 多帧图片，仅支持单帧静态图片",
            {"file_index": file_index, "detected_kind": "ANIMATED_IMAGE"},
        )

    # ⑤ 读头像素防炸弹 + 解码 + EXIF 转正 + 纯净重编码
    with _PIL_LOCK:
        prev_max_pixels = Image.MAX_IMAGE_PIXELS
        Image.MAX_IMAGE_PIXELS = max_pixels
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(raw)) as img:
                    if img.format not in _FORMAT_META:
                        raise AppError(
                            UNSUPPORTED_IMAGE_FORMAT,
                            f"图片容器格式 {img.format} 不在静态图片白名单内",
                            {"file_index": file_index, "detected_format": img.format},
                        )
                    n_frames = int(getattr(img, "n_frames", 1))
                    if getattr(img, "is_animated", False) or n_frames > 1:
                        raise AppError(
                            UNSUPPORTED_IMAGE_FORMAT,
                            f"检测到 {n_frames} 帧动图，仅支持单帧静态图片",
                            {
                                "file_index": file_index,
                                "detected_kind": "ANIMATED_IMAGE",
                                "n_frames": n_frames,
                            },
                        )

                    raw_w, raw_h = img.size
                    _check_dimensions(raw_w, raw_h, max_pixels=max_pixels, file_index=file_index)

                    img.load()
                    transposed = ImageOps.exif_transpose(img)
                    work_img = transposed if transposed is not None else img
                    try:
                        norm_w, norm_h = work_img.size
                        _check_dimensions(
                            norm_w, norm_h, max_pixels=max_pixels, file_index=file_index
                        )
                        clean_bytes = _encode_clean_image(
                            work_img,
                            detected_format=detected_format,
                            max_bytes=max_bytes,
                            file_index=file_index,
                        )
                    finally:
                        if work_img is not img:
                            work_img.close()
        except AppError:
            raise
        except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            probe_w, probe_h = _extract_header_dimensions(raw, detected_format)
            raise AppError(
                IMAGE_PIXEL_LIMIT_EXCEEDED,
                "图片像素总量超过安全上限，疑似解压炸弹",
                {
                    "file_index": file_index,
                    "width": probe_w,
                    "height": probe_h,
                    "pixels": probe_w * probe_h,
                    "max_pixels": max_pixels,
                },
            ) from exc
        except (UnidentifiedImageError, OSError, ValueError, EOFError, SyntaxError) as exc:
            raise AppError(
                UNSUPPORTED_IMAGE_FORMAT,
                f"图片解码失败，文件可能已损坏：{exc}",
                {"file_index": file_index, "reason": "DECODE_FAILED"},
            ) from exc
        finally:
            Image.MAX_IMAGE_PIXELS = prev_max_pixels

    ext, mime = _FORMAT_META[detected_format]
    sha256_hex = hashlib.sha256(clean_bytes).hexdigest()
    return ValidatedMediaFile(
        content=clean_bytes,
        ext=ext,
        mime=mime,
        width=norm_w,
        height=norm_h,
        sha256=sha256_hex,
        size_bytes=len(clean_bytes),
    )


def save_upload_file(
    validated: ValidatedMediaFile,
    upload_dir: Path,
    *,
    now: datetime | None = None,
) -> tuple[str, Path]:
    """将校验并转正后的图片字节写入 `{upload_dir}/{yyyy}/{mm}/{uuid4}.{ext}`。"""
    if not validated.content:
        raise AppError(VALIDATION_ERROR, "无法保存空图片载荷", {"reason": "EMPTY_BUFFER"})

    clean_ext = validated.ext.lstrip(".").lower()
    if f".{clean_ext}" not in ALLOWED_IMAGE_EXTENSIONS:
        raise AppError(
            UNSUPPORTED_IMAGE_FORMAT,
            f"非法的图片存储扩展名: {validated.ext}",
            {"ext": validated.ext},
        )

    if now is None:
        ts = datetime.now(_SHANGHAI_TZ)
    elif now.tzinfo is None:
        ts = now.replace(tzinfo=_SHANGHAI_TZ)
    else:
        ts = now.astimezone(_SHANGHAI_TZ)

    yyyy = f"{ts.year:04d}"
    mm = f"{ts.month:02d}"
    file_name = f"{uuid.uuid4()}.{clean_ext}"
    relative_path = f"{yyyy}/{mm}/{file_name}"

    target_dir = upload_dir / yyyy / mm
    target_dir.mkdir(parents=True, exist_ok=True)
    full_path = target_dir / file_name
    try:
        full_path.write_bytes(validated.content)
    except Exception:
        cleanup_files((full_path,))
        raise
    return relative_path, full_path


def cleanup_files(paths: Iterable[Path]) -> None:
    """Best-effort 删除磁盘文件（用于视觉降级 D2 整笔回滚）。"""
    for path in paths:
        try:
            candidate = Path(path)
            if not candidate.is_dir():
                candidate.unlink(missing_ok=True)
        except OSError:
            pass


def _validate_relative_storage_path(storage_path: str) -> PurePosixPath:
    if not storage_path or not storage_path.strip() or "\x00" in storage_path:
        raise MediaPathTraversalError(
            VALIDATION_ERROR,
            "证据存储路径无效",
            {"reason": "INVALID_STORAGE_PATH"},
        )
    if "\\" in storage_path or storage_path.startswith("/"):
        raise MediaPathTraversalError(
            VALIDATION_ERROR,
            "禁止绝对路径或反斜杠路径",
            {"reason": "PATH_TRAVERSAL_BLOCKED"},
        )
    if len(storage_path) >= 2 and storage_path[1] == ":":
        raise MediaPathTraversalError(
            VALIDATION_ERROR,
            "禁止盘符路径",
            {"reason": "PATH_TRAVERSAL_BLOCKED"},
        )
    posix_path = PurePosixPath(storage_path)
    if posix_path.is_absolute() or Path(storage_path).is_absolute():
        raise MediaPathTraversalError(
            VALIDATION_ERROR,
            "禁止绝对路径",
            {"reason": "PATH_TRAVERSAL_BLOCKED"},
        )
    parts = posix_path.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise MediaPathTraversalError(
            VALIDATION_ERROR,
            "检测到路径穿越片段，已拒绝访问",
            {"reason": "PATH_TRAVERSAL_BLOCKED"},
        )
    return posix_path


def resolve_evidence_paths(
    storage_path: str,
    analysis_id: str | uuid.UUID | None,
    upload_dir: Path,
) -> tuple[Path, Path | None]:
    """安全解析原图与可选标注图路径，严格阻断 `..` 与符号链接目录逃逸。"""
    rel_path = _validate_relative_storage_path(storage_path)
    base_dir = upload_dir.resolve()
    raw_path = (base_dir / rel_path).resolve()
    if not raw_path.is_relative_to(base_dir) or raw_path == base_dir:
        raise MediaPathTraversalError(
            VALIDATION_ERROR,
            "证据文件路径越界，已拒绝访问",
            {"reason": "PATH_TRAVERSAL_BLOCKED"},
        )

    if analysis_id is None:
        return raw_path, None

    aid = str(analysis_id).strip()
    if not aid:
        return raw_path, None
    if (
        "\x00" in aid
        or "/" in aid
        or "\\" in aid
        or ".." in aid
        or not _SAFE_ANALYSIS_ID_RE.fullmatch(aid)
    ):
        raise MediaPathTraversalError(
            VALIDATION_ERROR,
            "非法的 analysis_id，已拒绝路径解析",
            {"reason": "INVALID_ANALYSIS_ID"},
        )

    vision_dir = (base_dir / "vision").resolve()
    if not vision_dir.is_relative_to(base_dir) or vision_dir == base_dir:
        raise MediaPathTraversalError(
            VALIDATION_ERROR,
            "标注目录存在越界风险，已拒绝访问",
            {"reason": "PATH_TRAVERSAL_BLOCKED"},
        )

    annotated_path = (vision_dir / f"{aid}_annotated.jpg").resolve()
    if not annotated_path.is_relative_to(vision_dir) or annotated_path == vision_dir:
        raise MediaPathTraversalError(
            VALIDATION_ERROR,
            "标注图片路径越界，已拒绝访问",
            {"reason": "PATH_TRAVERSAL_BLOCKED"},
        )

    return raw_path, annotated_path
