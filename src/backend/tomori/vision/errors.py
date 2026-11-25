"""领域错误与 `error.code` → HTTP 状态码的一对一映射。

状态码口径唯一权威：`docs/API.md` §3（34 码唯一映射表）。
`code` 全局唯一对应一个 HTTP 状态码，前端才能用一张表驱动错误处理、E2E 才能写单值断言。

视觉侧失败只有三条互斥 `503`（`docs/DESIGN.md` §5.2）：
`VISION_DISABLED`（配置关闭 / 权重不可用，含 `strict_custom` 门禁未通过）、
`VISION_TIMEOUT`、`VISION_INFERENCE_FAILED`。三者一律携带
`details.fallback = {"action": "MANUAL_REPORT", "resubmit_required": True}` 与
`details.analysis_stage`，并按码给出非空 `message_key`（降级契约 D1–D3）。
**权重未就绪 / 缺失 / 禁用一律归 `VISION_DISABLED`**，不得自造私有码。
"""

from __future__ import annotations

from typing import Any, Final, Literal

# --- 媒体与容量（`docs/API.md` §3.3）---
IMAGE_SIZE_EXCEEDED: Final = "IMAGE_SIZE_EXCEEDED"
UNSUPPORTED_IMAGE_FORMAT: Final = "UNSUPPORTED_IMAGE_FORMAT"
MEDIA_VIDEO_STREAM_FORBIDDEN: Final = "MEDIA_VIDEO_STREAM_FORBIDDEN"
IMAGE_PIXEL_LIMIT_EXCEEDED: Final = "IMAGE_PIXEL_LIMIT_EXCEEDED"

# --- 请求校验与容量（`docs/API.md` §3.2 / §3.4）---
VALIDATION_ERROR: Final = "VALIDATION_ERROR"
TOO_MANY_FILES: Final = "TOO_MANY_FILES"
NO_FILES: Final = "NO_FILES"

# --- 服务端与视觉降级（`docs/API.md` §3.4）---
INTERNAL_ERROR: Final = "INTERNAL_ERROR"
VISION_DISABLED: Final = "VISION_DISABLED"
VISION_TIMEOUT: Final = "VISION_TIMEOUT"
VISION_INFERENCE_FAILED: Final = "VISION_INFERENCE_FAILED"

HTTP_STATUS_BY_CODE: Final[dict[str, int]] = {
    IMAGE_SIZE_EXCEEDED: 413,
    UNSUPPORTED_IMAGE_FORMAT: 415,
    MEDIA_VIDEO_STREAM_FORBIDDEN: 415,
    IMAGE_PIXEL_LIMIT_EXCEEDED: 422,
    VALIDATION_ERROR: 422,
    TOO_MANY_FILES: 422,
    NO_FILES: 422,
    INTERNAL_ERROR: 500,
    VISION_DISABLED: 503,
    VISION_TIMEOUT: 503,
    VISION_INFERENCE_FAILED: 503,
}

# 视觉失败发生的阶段（`docs/DESIGN.md` §5.2 三条互斥 `503` 表）
AnalysisStage = Literal["CONFIG_CHECK", "DECODE", "INFERENCE", "POSTPROCESS"]

# 人工兜底引导：`503` 本身信息量为零，前端靠它立即切「人工报障」表单（D3）
MANUAL_FALLBACK: Final[dict[str, Any]] = {"action": "MANUAL_REPORT", "resubmit_required": True}

_DEGRADED_MESSAGE_KEY: Final[dict[str, str]] = {
    VISION_DISABLED: "vision.disabled.manual_fallback",
    VISION_TIMEOUT: "vision.timeout.manual_fallback",
    VISION_INFERENCE_FAILED: "vision.failed.manual_fallback",
}


class VisionError(Exception):
    """视觉域可预期的拒绝：携带稳定 `code`、唯一 HTTP 状态码与可审计 `details`。

    `503` 一律自动补齐 `details.fallback`，`message_key` 按码兜底，调用方无法漏带兜底引导。
    """

    def __init__(
        self,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
        http_status: int | None = None,
        message_key: str | None = None,
    ) -> None:
        resolved = http_status if http_status is not None else HTTP_STATUS_BY_CODE.get(code)
        if resolved is None:
            raise KeyError(f"未登记的错误码: {code}")
        super().__init__(message)
        self.code = code
        self.http_status = resolved
        self.message = message
        degraded_message_key = _DEGRADED_MESSAGE_KEY.get(code)
        self.message_key = message_key if message_key is not None else degraded_message_key
        self.details = dict(details or {})
        if resolved == 503:
            self.details.setdefault("fallback", dict(MANUAL_FALLBACK))

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "details": self.details,
        }
        if self.message_key is not None:
            payload["message_key"] = self.message_key
        return {"error": payload}

    def __str__(self) -> str:
        return f"[{self.http_status} {self.code}] {self.message}"


def make_degraded_error(
    code: str,
    message: str,
    *,
    analysis_stage: AnalysisStage,
    details: dict[str, Any] | None = None,
) -> VisionError:
    """构造视觉降级错误：强制携带 `details.fallback`、`details.analysis_stage` 与 `message_key`。

    只接受三条互斥降级码；其他码直接 `VisionError(...)` 构造，不走本构造函数。
    """
    if code not in _DEGRADED_MESSAGE_KEY:
        raise KeyError(f"非视觉降级错误码: {code}")
    return VisionError(code, message, {**(details or {}), "analysis_stage": analysis_stage})
