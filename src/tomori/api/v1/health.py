"""系统存活与就绪探针（GET /api/v1/health，不计入 docs/API.md §2 60 条业务端点）。

遵守 D1 视觉隔离纪律：
- 延迟导入 `tomori.vision.client`，若未装 vision extra 或权重未就绪，
  `/health` 仍返回 200（`status="ok"`），在 `vision` 字段诚实上报 `ready=false`。
- 仅当数据库连接失败时返回 503 `SERVICE_UNAVAILABLE`。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tomori.db.session import get_session
from tomori.services.exceptions import SERVICE_UNAVAILABLE, AppError

router = APIRouter(tags=["health"])


@router.get("/health", summary="服务健康检查（DB 连通性与视觉引擎就绪态）")
async def health_check(
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    try:
        val = await session.scalar(text("SELECT 1"))
        if val != 1:
            raise RuntimeError("SELECT 1 returned unexpected result")
    except Exception as exc:
        raise AppError(
            SERVICE_UNAVAILABLE,
            "数据库连接不可用",
            {"component": "postgres"},
        ) from exc

    vision_status: dict[str, Any] = {
        "ready": False,
        "mode": None,
        "detail": "uninitialized",
    }
    try:
        from tomori.vision.client import VisionClient

        probe = VisionClient().check_readiness()
        vision_status = {
            "ready": probe.ready,
            "mode": probe.mode,
            "detail": probe.detail,
        }
    except Exception as exc:
        vision_status = {
            "ready": False,
            "mode": None,
            "detail": f"vision unavailable: {type(exc).__name__}",
        }

    return {
        "status": "ok",
        "database": "ok",
        "vision": vision_status,
    }
