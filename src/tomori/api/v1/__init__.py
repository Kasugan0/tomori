"""`/api/v1` 路由总装：1 个健康探针 + 60 个业务端点（12 + 12 + 14 + 10 + 12）。"""

from __future__ import annotations

from fastapi import APIRouter

from tomori.api.v1.auth import router as auth_router
from tomori.api.v1.dispatches import router as dispatches_router
from tomori.api.v1.health import router as health_router
from tomori.api.v1.master_data import router as master_data_router
from tomori.api.v1.operations import router as operations_router
from tomori.api.v1.tickets import router as tickets_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(auth_router)
api_router.include_router(master_data_router)
api_router.include_router(tickets_router)
api_router.include_router(dispatches_router)
api_router.include_router(operations_router)

__all__ = ["api_router"]
