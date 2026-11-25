"""纯领域异常、34 个权威 error.code 与 HTTP 状态码唯一映射。

本模块仅依赖标准库，绝不导入 FastAPI / Starlette，供 `tomori.services.*`
与纯单元测试安全导入。唯一权威：`docs/API.md` §3。
"""

from __future__ import annotations

from typing import Any, Final

# §3.1 认证与权限（9）
INVALID_CREDENTIALS: Final = "INVALID_CREDENTIALS"
TOKEN_EXPIRED: Final = "TOKEN_EXPIRED"
TOKEN_INVALID: Final = "TOKEN_INVALID"
TOKEN_MISSING: Final = "TOKEN_MISSING"
FORBIDDEN_ROLE: Final = "FORBIDDEN_ROLE"
FORBIDDEN_REGION_SCOPE: Final = "FORBIDDEN_REGION_SCOPE"
ACCOUNT_DISABLED: Final = "ACCOUNT_DISABLED"
RESOURCE_NOT_FOUND: Final = "RESOURCE_NOT_FOUND"
EVIDENCE_NOT_ANNOTATED: Final = "EVIDENCE_NOT_ANNOTATED"

# §3.2 状态与业务规则（12）
DUPLICATE_ACTIVE_TICKET: Final = "DUPLICATE_ACTIVE_TICKET"
IDEMPOTENCY_REQUEST_IN_PROGRESS: Final = "IDEMPOTENCY_REQUEST_IN_PROGRESS"
INVALID_STATE_TRANSITION: Final = "INVALID_STATE_TRANSITION"
AI_CANDIDATE_NOT_CONFIRMED: Final = "AI_CANDIDATE_NOT_CONFIRMED"
ELECTRICAL_SAFETY_CHECK_REQUIRED: Final = "ELECTRICAL_SAFETY_CHECK_REQUIRED"
DISPATCH_TECHNICIAN_NOT_ELIGIBLE: Final = "DISPATCH_TECHNICIAN_NOT_ELIGIBLE"
TECHNICIAN_OVERLOADED: Final = "TECHNICIAN_OVERLOADED"
POINT_HAS_ACTIVE_TICKET: Final = "POINT_HAS_ACTIVE_TICKET"
RULE_VALUE_IMMUTABLE: Final = "RULE_VALUE_IMMUTABLE"
INVALID_RULE_PAYLOAD: Final = "INVALID_RULE_PAYLOAD"
IDEMPOTENCY_PAYLOAD_MISMATCH: Final = "IDEMPOTENCY_PAYLOAD_MISMATCH"
VALIDATION_ERROR: Final = "VALIDATION_ERROR"

# §3.3 媒体与容量（6）
IDEMPOTENCY_KEY_REQUIRED: Final = "IDEMPOTENCY_KEY_REQUIRED"
IDEMPOTENCY_KEY_INVALID: Final = "IDEMPOTENCY_KEY_INVALID"
IMAGE_SIZE_EXCEEDED: Final = "IMAGE_SIZE_EXCEEDED"
UNSUPPORTED_IMAGE_FORMAT: Final = "UNSUPPORTED_IMAGE_FORMAT"
MEDIA_VIDEO_STREAM_FORBIDDEN: Final = "MEDIA_VIDEO_STREAM_FORBIDDEN"
IMAGE_PIXEL_LIMIT_EXCEEDED: Final = "IMAGE_PIXEL_LIMIT_EXCEEDED"

# §3.4 服务端与视觉降级（7）
TOO_MANY_FILES: Final = "TOO_MANY_FILES"
NO_FILES: Final = "NO_FILES"
INTERNAL_ERROR: Final = "INTERNAL_ERROR"
SERVICE_UNAVAILABLE: Final = "SERVICE_UNAVAILABLE"
VISION_DISABLED: Final = "VISION_DISABLED"
VISION_TIMEOUT: Final = "VISION_TIMEOUT"
VISION_INFERENCE_FAILED: Final = "VISION_INFERENCE_FAILED"

HTTP_STATUS_BY_CODE: Final[dict[str, int]] = {
    INVALID_CREDENTIALS: 401,
    TOKEN_EXPIRED: 401,
    TOKEN_INVALID: 401,
    TOKEN_MISSING: 401,
    FORBIDDEN_ROLE: 403,
    FORBIDDEN_REGION_SCOPE: 403,
    ACCOUNT_DISABLED: 403,
    RESOURCE_NOT_FOUND: 404,
    EVIDENCE_NOT_ANNOTATED: 404,
    DUPLICATE_ACTIVE_TICKET: 409,
    IDEMPOTENCY_REQUEST_IN_PROGRESS: 409,
    INVALID_STATE_TRANSITION: 422,
    AI_CANDIDATE_NOT_CONFIRMED: 422,
    ELECTRICAL_SAFETY_CHECK_REQUIRED: 422,
    DISPATCH_TECHNICIAN_NOT_ELIGIBLE: 422,
    TECHNICIAN_OVERLOADED: 422,
    POINT_HAS_ACTIVE_TICKET: 422,
    RULE_VALUE_IMMUTABLE: 422,
    INVALID_RULE_PAYLOAD: 422,
    IDEMPOTENCY_PAYLOAD_MISMATCH: 422,
    VALIDATION_ERROR: 422,
    IDEMPOTENCY_KEY_REQUIRED: 400,
    IDEMPOTENCY_KEY_INVALID: 400,
    IMAGE_SIZE_EXCEEDED: 413,
    UNSUPPORTED_IMAGE_FORMAT: 415,
    MEDIA_VIDEO_STREAM_FORBIDDEN: 415,
    IMAGE_PIXEL_LIMIT_EXCEEDED: 422,
    TOO_MANY_FILES: 422,
    NO_FILES: 422,
    INTERNAL_ERROR: 500,
    SERVICE_UNAVAILABLE: 503,
    VISION_DISABLED: 503,
    VISION_TIMEOUT: 503,
    VISION_INFERENCE_FAILED: 503,
}

MESSAGE_KEY_BY_CODE: Final[dict[str, str]] = {
    INVALID_CREDENTIALS: "error.invalid_credentials",
    TOKEN_EXPIRED: "error.token_expired",
    TOKEN_INVALID: "error.token_invalid",
    TOKEN_MISSING: "error.token_missing",
    FORBIDDEN_ROLE: "error.forbidden_role",
    FORBIDDEN_REGION_SCOPE: "error.forbidden_region_scope",
    ACCOUNT_DISABLED: "error.account_disabled",
    RESOURCE_NOT_FOUND: "error.resource_not_found",
    EVIDENCE_NOT_ANNOTATED: "error.evidence_not_annotated",
    DUPLICATE_ACTIVE_TICKET: "error.duplicate_active_ticket",
    IDEMPOTENCY_REQUEST_IN_PROGRESS: "error.idempotency_request_in_progress",
    INVALID_STATE_TRANSITION: "error.invalid_state_transition",
    AI_CANDIDATE_NOT_CONFIRMED: "error.ai_candidate_not_confirmed",
    ELECTRICAL_SAFETY_CHECK_REQUIRED: "error.electrical_safety_check_required",
    DISPATCH_TECHNICIAN_NOT_ELIGIBLE: "error.dispatch_technician_not_eligible",
    TECHNICIAN_OVERLOADED: "error.technician_overloaded",
    POINT_HAS_ACTIVE_TICKET: "error.point_has_active_ticket",
    RULE_VALUE_IMMUTABLE: "error.rule_value_immutable",
    INVALID_RULE_PAYLOAD: "error.invalid_rule_payload",
    IDEMPOTENCY_PAYLOAD_MISMATCH: "error.idempotency_payload_mismatch",
    VALIDATION_ERROR: "error.validation_error",
    IDEMPOTENCY_KEY_REQUIRED: "error.idempotency_key_required",
    IDEMPOTENCY_KEY_INVALID: "error.idempotency_key_invalid",
    IMAGE_SIZE_EXCEEDED: "error.image_size_exceeded",
    UNSUPPORTED_IMAGE_FORMAT: "error.unsupported_image_format",
    MEDIA_VIDEO_STREAM_FORBIDDEN: "error.media_video_stream_forbidden",
    IMAGE_PIXEL_LIMIT_EXCEEDED: "error.image_pixel_limit_exceeded",
    TOO_MANY_FILES: "error.too_many_files",
    NO_FILES: "error.no_files",
    INTERNAL_ERROR: "error.internal_error",
    SERVICE_UNAVAILABLE: "error.service_unavailable",
    VISION_DISABLED: "vision.disabled.manual_fallback",
    VISION_TIMEOUT: "vision.timeout.manual_fallback",
    VISION_INFERENCE_FAILED: "vision.failed.manual_fallback",
}

assert len(HTTP_STATUS_BY_CODE) == 34
assert set(HTTP_STATUS_BY_CODE) == set(MESSAGE_KEY_BY_CODE)


class AppError(Exception):
    """后端统一领域异常（`error.code` 必须属于 34 码权威表）。"""

    def __init__(
        self,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
        *,
        message_key: str | None = None,
    ) -> None:
        http_status = HTTP_STATUS_BY_CODE.get(code)
        if http_status is None:
            raise KeyError(f"未登记的权威错误码: {code}")
        super().__init__(message)
        self.code = code
        self.http_status = http_status
        self.message = message
        self.message_key = message_key or MESSAGE_KEY_BY_CODE[code]
        self.details: dict[str, Any] | None = dict(details) if details is not None else None
