import json
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI, Request
from starlette.types import Message, Scope

import tomori.main as main


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    monkeypatch.setattr(
        main,
        "get_settings",
        lambda: SimpleNamespace(max_upload_size_bytes=1024, cors_origin_list=[]),
    )
    app = main.create_app()

    @app.post("/body-limit-test")
    async def consume(request: Request) -> dict[str, int]:
        body = await request.body()
        return {"size": len(body)}

    return app


async def request(
    app: FastAPI, chunks: list[bytes], content_length: int | None = None
) -> tuple[int, dict[str, Any]]:
    headers = [(b"content-type", b"multipart/form-data; boundary=boundary")]
    if content_length is not None:
        headers.append((b"content-length", str(content_length).encode()))
    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/body-limit-test",
        "raw_path": b"/body-limit-test",
        "query_string": b"",
        "headers": headers,
        "server": ("test", 80),
        "client": ("test", 1234),
    }
    messages = iter(enumerate(chunks))
    sent: list[Message] = []

    async def receive() -> Message:
        index, chunk = next(messages)
        return {"type": "http.request", "body": chunk, "more_body": index < len(chunks) - 1}

    async def send(message: Message) -> None:
        sent.append(message)

    await app(scope, receive, send)
    start = next(message for message in sent if message["type"] == "http.response.start")
    body = b"".join(
        message.get("body", b"") for message in sent if message["type"] == "http.response.body"
    )
    result: dict[str, Any] = json.loads(body)
    return int(start["status"]), result


def multipart(size: int) -> bytes:
    prefix = b'--boundary\r\nContent-Disposition: form-data; name="file"; filename="x.jpg"\r\n\r\n'
    suffix = b"\r\n--boundary--\r\n"
    return prefix + b"x" * (size - len(prefix) - len(suffix)) + suffix


@pytest.mark.parametrize("content_length", [None, 1])
async def test_multipart_stream_exceeding_limit(app: FastAPI, content_length: int | None) -> None:
    limit = 10 * 1024 + 1024 * 1024
    body = multipart(limit + 1)
    chunks = [body[:400_000], body[400_000:limit], body[limit:]]
    status, result = await request(app, chunks, content_length)
    assert status == 413
    assert result["error"]["code"] == "IMAGE_SIZE_EXCEEDED"


@pytest.mark.parametrize("size", [128, 10 * 1024 + 1024 * 1024])
async def test_multipart_stream_at_or_below_limit(app: FastAPI, size: int) -> None:
    body = multipart(size)
    status, result = await request(app, [body[:50], body[50:]])
    assert status == 200
    assert result == {"size": size}
