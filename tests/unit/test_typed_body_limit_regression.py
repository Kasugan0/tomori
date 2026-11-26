"""`RequestBodyLimitMiddleware` 对三类真实路由的分块超限回归（task-27）。

驱动 `main.create_app()` 真实装配栈（CORS → RequestId → RequestBodyLimit →
ExceptionMiddleware → api_router），以纯 ASGI `receive`/`send` 逐块投喂，不启动
lifespan、不建 DB 连接。三类被测路径：

- 类型化 JSON：`POST /api/v1/auth/login`（`LoginIn`，FastAPI 在 solve_dependencies
  之前 `await request.body()`，`except HTTPException` 直通 `RequestBodyLimitError`）。
- 人工工单 JSON：`POST /api/v1/tickets`（端点内 `await request.json()`，即 tickets.py
  `create_manual_ticket` 的非 multipart 分支）。
- 人工工单 multipart：`POST /api/v1/tickets`（端点内 `await request.form()`）。

归因纪律（防重合掩盖）：全程不发送 `Content-Length`，端点内
`_reject_oversized_body` 的声明长度守卫不可能触发；且断言
`error.details == {"max_bytes": LIMIT}`——`declared_bytes`/`filename` 形态只能
来自端点守卫，等值断言把 413 唯一归因到中间件累积计数。

无双响应：断言恰好一条 `http.response.start`；若响应已开始后被再次处理，
`wrap_app_handling_exceptions` 会抛 `RuntimeError` 令 `await app(...)` 失败。

仅替换必要依赖：`get_current_principal`（免 JWT/账号库查询，返回 INSPECTOR 主体
以过 `require_roles`）与 `get_session`（惰性 async-gen 桩——超限在
`request.json()`/`request.form()` 处先行抛出，桩对象永不被使用，零 DB 接触）。
`get_request_context` 保持真实（纯函数无 DB）。
"""

import json
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from starlette.types import Message, Scope

import tomori.main as main
from tomori.api.deps import CurrentPrincipal, get_current_principal
from tomori.db.session import get_session
from tomori.models.account import Account
from tomori.services.exceptions import IMAGE_SIZE_EXCEEDED, VALIDATION_ERROR
from tomori.settings import MAX_FILES_PER_REQUEST

_MAX_UPLOAD_BYTES = 1024
# 与 create_app() 装配公式一致：max_upload_size_bytes * MAX_FILES_PER_REQUEST + 1 MiB
LIMIT = _MAX_UPLOAD_BYTES * MAX_FILES_PER_REQUEST + 1024 * 1024

_LOGIN = "/api/v1/auth/login"
_TICKETS = "/api/v1/tickets"
_JSON = [(b"content-type", b"application/json")]
_MULTIPART = [(b"content-type", b"multipart/form-data; boundary=lim")]


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    monkeypatch.setattr(
        main,
        "get_settings",
        lambda: SimpleNamespace(
            max_upload_size_bytes=_MAX_UPLOAD_BYTES,
            cors_origin_list=[],
        ),
    )
    app = main.create_app()

    inspector = CurrentPrincipal(
        account=Account(id=1, username="inspector", status="ACTIVE", token_version=1),
        role_code="INSPECTOR",
    )
    app.dependency_overrides[get_current_principal] = lambda: inspector

    async def _session_override() -> Any:
        yield SimpleNamespace()

    app.dependency_overrides[get_session] = _session_override
    return app


async def _drive(
    app: FastAPI,
    headers: list[tuple[bytes, bytes]],
    path: str,
    chunks: list[bytes],
) -> list[dict[str, Any]]:
    """以纯 ASGI 消息驱动真实 app；故意不带 `content-length`（见模块 docstring）。"""
    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": headers,
        "server": ("test", 80),
        "client": ("test", 1234),
    }
    queue = iter(enumerate(chunks))
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        try:
            index, chunk = next(queue)
        except StopIteration:
            return {"type": "http.disconnect"}
        return {"type": "http.request", "body": chunk, "more_body": index < len(chunks) - 1}

    async def send(message: Message) -> None:
        sent.append(dict(message))

    await app(scope, receive, send)
    return sent


def _envelope(sent: list[dict[str, Any]]) -> dict[str, Any]:
    """断言无双响应并返回解析后的错误信封。"""
    starts = [m for m in sent if m["type"] == "http.response.start"]
    assert len(starts) == 1, f"无双响应要求恰好一条 response.start，实际 {len(starts)} 条"
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    envelope: dict[str, Any] = json.loads(body)
    envelope["_status"] = starts[0]["status"]
    return envelope


_MULTIPART_FILE_PREFIX = (
    b'--lim\r\nContent-Disposition: form-data; name="files"; filename="f.bin"\r\n'
    b"Content-Type: application/octet-stream\r\n\r\n"
)


def _multipart_chunks(sizes: list[int]) -> list[bytes]:
    """合法 multipart 文件分块：垃圾字节会让 python_multipart 先 FormParserError(400)，

    非文件字段又会命中 starlette `max_part_size` 默认 1MiB —— 只有带 `filename` 的
    文件分块能使流越过解析器抵达中间件计数器。"""
    body = _MULTIPART_FILE_PREFIX + b"d" * (sum(sizes) - len(_MULTIPART_FILE_PREFIX))
    chunks, offset = [], 0
    for size in sizes:
        chunks.append(body[offset : offset + size])
        offset += size
    return chunks


_OVER_LIMIT_SIZES = [
    pytest.param(lambda limit: [limit + 1], id="first-chunk-over"),
    pytest.param(lambda limit: [limit - 1, 2], id="crosses-on-second-chunk"),
    pytest.param(lambda limit: [limit // 3] * 4, id="crosses-mid-stream"),
]

_ROUTES = [
    pytest.param(_LOGIN, _JSON, "json", id="typed-json-login"),
    pytest.param(_TICKETS, _JSON, "json", id="manual-json-tickets"),
    pytest.param(_TICKETS, _MULTIPART, "multipart", id="manual-multipart-tickets"),
]


@pytest.mark.parametrize("path,headers,kind", _ROUTES)
@pytest.mark.parametrize("sizes", _OVER_LIMIT_SIZES)
async def test_chunked_body_over_limit_returns_single_413(
    app: FastAPI,
    path: str,
    headers: list[tuple[bytes, bytes]],
    kind: str,
    sizes: Any,
) -> None:
    """分块累积超限 → 中间件唯一 413 `IMAGE_SIZE_EXCEEDED`，且恰好一条响应。"""
    resolved = sizes(LIMIT)
    chunks = _multipart_chunks(resolved) if kind == "multipart" else [b"x" * n for n in resolved]
    sent = await _drive(app, headers, path, chunks)
    envelope = _envelope(sent)
    assert envelope["_status"] == 413
    assert envelope["error"]["code"] == IMAGE_SIZE_EXCEEDED
    assert envelope["error"]["details"] == {"max_bytes": LIMIT}


def _padded(body: bytes, limit: int) -> bytes:
    """精确造出 `limit` 字节：尾部补 JSON 容忍的空白。"""
    assert len(body) <= limit
    return body + b" " * (limit - len(body))


@pytest.mark.parametrize(
    "path,headers,body",
    [
        pytest.param(_LOGIN, _JSON, b"x", id="typed-json-login"),
        pytest.param(_TICKETS, _JSON, b"{}", id="manual-json-tickets"),
        pytest.param(
            _TICKETS,
            _MULTIPART,
            b'--lim\r\nContent-Disposition: form-data; name="title"\r\n\r\nx\r\n--lim--\r\n',
            id="manual-multipart-tickets",
        ),
    ],
)
async def test_chunked_body_at_exact_limit_passes_middleware(
    app: FastAPI,
    path: str,
    headers: list[tuple[bytes, bytes]],
    body: bytes,
) -> None:
    """累积 `size == max_bytes` 不触发（`>` 非 `>=`）：越过中间件后由下游校验失败。

    - login：`request.json()` JSONDecodeError → `VALIDATION_ERROR`（422，前置校验层，
      不触依赖）。
    - manual JSON：`{}` 通过 JSON 解析 → `TicketCreateJsonIn.model_validate` 失败 →
      `VALIDATION_ERROR`（422），同时证明 JSON 体已被完整读出。
    - manual multipart：恰好 `limit` 字节的合法帧通过流式解析 → 缺必填字段 →
      `VALIDATION_ERROR`（422）。
    三者共同把「放行」归因于中间件计数器未越界，而非某守卫恰好缺席。
    """
    full = _padded(body, LIMIT)
    chunks = [full[:-1], full[-1:]]
    assert sum(len(c) for c in chunks) == LIMIT
    sent = await _drive(app, headers, path, chunks)
    envelope = _envelope(sent)
    assert envelope["_status"] == 422
    assert envelope["error"]["code"] == VALIDATION_ERROR
