"""FastAPI 统一错误信封与异常处理器（包装 tomori.errors 与 tomori.vision.errors）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from tomori.services.exceptions import (
    ACCOUNT_DISABLED,
    AI_CANDIDATE_NOT_CONFIRMED,
    DISPATCH_TECHNICIAN_NOT_ELIGIBLE,
    DUPLICATE_ACTIVE_TICKET,
    ELECTRICAL_SAFETY_CHECK_REQUIRED,
    EVIDENCE_NOT_ANNOTATED,
    FORBIDDEN_REGION_SCOPE,
    FORBIDDEN_ROLE,
    HTTP_STATUS_BY_CODE,
    IDEMPOTENCY_KEY_INVALID,
    IDEMPOTENCY_KEY_REQUIRED,
    IDEMPOTENCY_PAYLOAD_MISMATCH,
    IDEMPOTENCY_REQUEST_IN_PROGRESS,
    IMAGE_PIXEL_LIMIT_EXCEEDED,
    IMAGE_SIZE_EXCEEDED,
    INTERNAL_ERROR,
    INVALID_CREDENTIALS,
    INVALID_RULE_PAYLOAD,
    INVALID_STATE_TRANSITION,
    MEDIA_VIDEO_STREAM_FORBIDDEN,
    MESSAGE_KEY_BY_CODE,
    NO_FILES,
    POINT_HAS_ACTIVE_TICKET,
    RESOURCE_NOT_FOUND,
    RULE_VALUE_IMMUTABLE,
    SERVICE_UNAVAILABLE,
    TECHNICIAN_OVERLOADED,
    TOKEN_EXPIRED,
    TOKEN_INVALID,
    TOKEN_MISSING,
    TOO_MANY_FILES,
    UNSUPPORTED_IMAGE_FORMAT,
    VALIDATION_ERROR,
    VISION_DISABLED,
    VISION_INFERENCE_FAILED,
    VISION_TIMEOUT,
    AppError,
)
from tomori.vision.errors import VisionError

__all__ = [
    "ACCOUNT_DISABLED",
    "AI_CANDIDATE_NOT_CONFIRMED",
    "DISPATCH_TECHNICIAN_NOT_ELIGIBLE",
    "DUPLICATE_ACTIVE_TICKET",
    "ELECTRICAL_SAFETY_CHECK_REQUIRED",
    "EVIDENCE_NOT_ANNOTATED",
    "FORBIDDEN_REGION_SCOPE",
    "FORBIDDEN_ROLE",
    "HTTP_STATUS_BY_CODE",
    "IDEMPOTENCY_KEY_INVALID",
    "IDEMPOTENCY_KEY_REQUIRED",
    "IDEMPOTENCY_PAYLOAD_MISMATCH",
    "IDEMPOTENCY_REQUEST_IN_PROGRESS",
    "IMAGE_PIXEL_LIMIT_EXCEEDED",
    "IMAGE_SIZE_EXCEEDED",
    "INTERNAL_ERROR",
    "INVALID_CREDENTIALS",
    "INVALID_RULE_PAYLOAD",
    "INVALID_STATE_TRANSITION",
    "MEDIA_VIDEO_STREAM_FORBIDDEN",
    "MESSAGE_KEY_BY_CODE",
    "NO_FILES",
    "POINT_HAS_ACTIVE_TICKET",
    "RESOURCE_NOT_FOUND",
    "RULE_VALUE_IMMUTABLE",
    "SERVICE_UNAVAILABLE",
    "TECHNICIAN_OVERLOADED",
    "TOKEN_EXPIRED",
    "TOKEN_INVALID",
    "TOKEN_MISSING",
    "TOO_MANY_FILES",
    "UNSUPPORTED_IMAGE_FORMAT",
    "VALIDATION_ERROR",
    "VISION_DISABLED",
    "VISION_INFERENCE_FAILED",
    "VISION_TIMEOUT",
    "AppError",
    "ErrorBody",
    "ErrorEnvelope",
    "build_error_response",
    "register_exception_handlers",
]

_SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


class ErrorBody(BaseModel):
    code: str
    message: str
    message_key: str
    details: dict[str, Any] | None = None
    request_id: str
    timestamp: str


class ErrorEnvelope(BaseModel):
    error: ErrorBody


def _now_iso() -> str:
    return datetime.now(_SHANGHAI_TZ).isoformat()


def _sanitize_details(details: dict[str, Any] | None) -> dict[str, Any] | None:
    """剔除潜在敏感内部信息（路径/堆栈/SQL/凭证字段），守住 docs/API.md §4 红线。"""
    if details is None:
        return None
    forbidden_keys = {
        "traceback",
        "stack",
        "sql",
        "query",
        "file_path",
        "storage_path",
        "secret",
        "jwt_secret_key",
        "password",
        "password_hash",
    }
    return {k: v for k, v in details.items() if k.lower() not in forbidden_keys}


def build_error_response(
    *,
    code: str,
    message: str,
    request_id: str,
    details: dict[str, Any] | None = None,
    message_key: str | None = None,
) -> JSONResponse:
    http_status = HTTP_STATUS_BY_CODE.get(code, status.HTTP_500_INTERNAL_SERVER_ERROR)
    resolved_key = message_key or MESSAGE_KEY_BY_CODE.get(code, "error.internal_error")
    payload = ErrorEnvelope(
        error=ErrorBody(
            code=code,
            message=message,
            message_key=resolved_key,
            details=_sanitize_details(details),
            request_id=request_id,
            timestamp=_now_iso(),
        )
    )
    return JSONResponse(
        status_code=http_status,
        content=payload.model_dump(mode="json"),
        headers={"X-Request-Id": request_id},
    )


def register_exception_handlers(app: FastAPI) -> None:
    """将 AppError / VisionError / RequestValidationError / 兜底 Exception 统一转换为唯一信封。"""

    @app.exception_handler(AppError)
    async def _handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        request_id = getattr(request.state, "request_id", "unknown")
        return build_error_response(
            code=exc.code,
            message=exc.message,
            message_key=exc.message_key,
            details=exc.details,
            request_id=request_id,
        )

    @app.exception_handler(VisionError)
    async def _handle_vision_error(request: Request, exc: VisionError) -> JSONResponse:
        """解包 VisionError 并补齐 request_id / timestamp / 默认 message_key（D1–D3）。"""
        request_id = getattr(request.state, "request_id", "unknown")
        code = exc.code if exc.code in HTTP_STATUS_BY_CODE else VISION_INFERENCE_FAILED
        details = dict(exc.details) if exc.details else {}
        if HTTP_STATUS_BY_CODE[code] == status.HTTP_503_SERVICE_UNAVAILABLE:
            fallback = dict(details.get("fallback") or {})
            fallback.setdefault("action", "MANUAL_REPORT")
            fallback.setdefault("resubmit_required", True)
            fallback.setdefault("message_key", exc.message_key or MESSAGE_KEY_BY_CODE[code])
            details["fallback"] = fallback
        return build_error_response(
            code=code,
            message=exc.message,
            message_key=exc.message_key or MESSAGE_KEY_BY_CODE[code],
            details=details,
            request_id=request_id,
        )

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        request_id = getattr(request.state, "request_id", "unknown")
        cleaned_errors = [
            {
                "loc": [str(part) for part in err.get("loc", ())],
                "msg": str(err.get("msg", "")),
                "type": str(err.get("type", "")),
            }
            for err in exc.errors()
        ]
        return build_error_response(
            code=VALIDATION_ERROR,
            message="请求参数校验失败",
            details={"errors": cleaned_errors},
            request_id=request_id,
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected_error(request: Request, _exc: Exception) -> JSONResponse:
        request_id = getattr(request.state, "request_id", "unknown")
        return build_error_response(
            code=INTERNAL_ERROR,
            message="系统内部异常，请凭 request_id 联系运维排查",
            details=None,
            request_id=request_id,
        )
