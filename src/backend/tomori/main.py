"""FastAPI 装配：中间件、异常处理、CORS、生命周期与 60 个业务端点挂载。"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from typing import cast

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from tomori.api.errors import register_exception_handlers
from tomori.api.v1 import api_router
from tomori.services.auth import seed_demo_data
from tomori.settings import get_settings
from tomori.workers.notifier import notifier_loop
from tomori.workers.sla_scanner import sla_scanner_loop

logger = logging.getLogger(__name__)

_next_call = Callable[[Request], Awaitable[Response]]


class RequestIdMiddleware:
    """纯 ASGI 中间件：注入 `request_id`（沿用上游 X-Request-Id）并回写响应头。"""

    def __init__(self, app: Callable[..., Awaitable[None]]) -> None:
        self.app = app

    async def __call__(
        self,
        scope: dict[str, object],
        receive: Callable[..., Awaitable[dict[str, object]]],
        send: Callable[..., Awaitable[None]],
    ) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive=receive)
        incoming = request.headers.get("x-request-id")
        request_id = incoming.strip() if incoming and incoming.strip() else str(uuid.uuid4())
        scope.setdefault("state", {})
        state = scope["state"]
        assert isinstance(state, dict)
        state["request_id"] = request_id
        state["request_started_at"] = datetime.now(UTC)

        async def _send(message: dict[str, object]) -> None:
            if message.get("type") == "http.response.start":
                # 响应头 `X-Request-Id` 必须恰好 1 条：错误信封（api/errors.py）会自行写入同值
                # 头，此处先剥离既有条目再追加，避免同一响应出现重复头。
                raw_headers = cast(Sequence[tuple[bytes, bytes]], message.get("headers") or ())
                headers = [
                    (raw_name, raw_value)
                    for raw_name, raw_value in raw_headers
                    if raw_name.lower() != b"x-request-id"
                ]
                headers.append((b"x-request-id", request_id.encode("latin-1")))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, _send)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    stop_event = asyncio.Event()
    tasks: list[asyncio.Task[None]] = []

    from tomori.db.session import get_async_sessionmaker

    session_factory = get_async_sessionmaker()
    try:
        async with session_factory() as session:
            await seed_demo_data(session)
            await session.commit()
    except Exception:
        logger.exception("Demo data seeding skipped (database unavailable or already seeded)")

    if settings.app_env != "test":
        tasks.append(asyncio.create_task(sla_scanner_loop(stop_event), name="sla-scanner"))
        tasks.append(asyncio.create_task(notifier_loop(stop_event), name="notifier"))
        logger.info("Background workers started: %s", [t.get_name() for t in tasks])

    try:
        yield
    finally:
        stop_event.set()
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="公共照明故障工单系统 API",
        description="郑州市公共照明设施故障报修、派工、返修与 48h 法定时限管理后端服务",
        version="1.0.0",
        openapi_url="/openapi.json",
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        lifespan=lifespan,
    )

    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-Id", "X-Idempotent-Replay", "Location"],
    )
    register_exception_handlers(app)
    app.include_router(api_router, prefix="/api/v1")
    return app


app = create_app()
