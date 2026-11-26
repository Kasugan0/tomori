"""Real-browser E2E fixtures: real PG17 + real `uvicorn` + real `pnpm vite dev` + real Chromium.

Port discipline (`docs/RUNBOOK.md` §4.4): E2E uses `18142` / `15174` (plus `18143` for the
`strict_custom` vision-fallback backend) and never occupies the dev ports `18141` / `15173`.
The whole session runs under the `./deploy/stack-guard.sh claim-e2e` window and additionally
takes the same `/tmp/tomori-integration-pg.lock` `fcntl.flock` that `tests/integration/`
uses, so an E2E run and an integration run can never truncate `tomori_test` concurrently.

IMPORTANT — event-loop constraint (`asyncio_mode=auto` + `pytest-playwright`):
`pytest-asyncio` wraps every fixture it can reach inside the `pytest-playwright` runner loop,
so **no session fixture here may call `asyncio.run()`**. The 17-table reset therefore uses
a plain synchronous engine (`psycopg` sync driver) instead of the async one.

Baseline state is produced by the application's own startup path: the 17 business tables are
truncated, the 3 roles + 5 v1 business rules restored, and the real `uvicorn` lifespan then
runs `seed_demo_data()` to create accounts, regions, scope authorizations, lighting points and
facility assets. No business state is hand-forged.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from playwright.sync_api import Page, expect

from tests.conftest import (
    _DEFAULT_ROLES,
    _default_business_rule_seeds,
    _ordered_business_table_names,
)
from tests.integration.conftest import (
    DEMO_CREDENTIALS,
    make_night_normal_lit_png,
    make_occluded_fault_png,
    make_static_image,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
E2E_BACKEND_PORT = 18142
E2E_FRONTEND_PORT = 15174
E2E_BACKEND_URL = f"http://127.0.0.1:{E2E_BACKEND_PORT}"
E2E_FRONTEND_URL = f"http://127.0.0.1:{E2E_FRONTEND_PORT}"
STRICT_VISION_BACKEND_PORT = 18143
STRICT_VISION_BACKEND_URL = f"http://127.0.0.1:{STRICT_VISION_BACKEND_PORT}"
STRICT_VISION_FRONTEND_PORT = 15175
STRICT_VISION_FRONTEND_URL = f"http://127.0.0.1:{STRICT_VISION_FRONTEND_PORT}"
E2E_DATABASE_URL = "postgresql+psycopg://tomori:tomori_dev_password@127.0.0.1:25432/tomori_test"
SYNC_DATABASE_URL = "postgresql+psycopg://tomori:tomori_dev_password@127.0.0.1:25432/tomori_test"
PG_SESSION_LOCK_PATH = Path("/tmp/tomori-integration-pg.lock")
LOG_DIR = REPO_ROOT / ".team" / "notes" / "tester" / "logs" / "e2e-run"
ARTIFACT_DIR = REPO_ROOT / ".team" / "notes" / "tester" / "logs" / "e2e-artifacts"


# ── subprocess / network helpers ──────────────────────────────────────────────
def _e2e_env() -> dict[str, str]:
    """Environment for the real `uvicorn` E2E backend (isolated `tomori_test`)."""
    env = dict(os.environ)
    env.update(
        {
            "APP_ENV": "e2e",
            "SEED_DEMO": "true",
            "DATABASE_URL": E2E_DATABASE_URL,
            "E2E_DATABASE_URL": E2E_DATABASE_URL,
            "TEST_DATABASE_URL": E2E_DATABASE_URL,
            "JWT_SECRET_KEY": "tomori-stage2-isolated-test-jwt-secret-key-not-for-prod",
            "BACKEND_HOST": "127.0.0.1",
            "BACKEND_PORT": str(E2E_BACKEND_PORT),
            "CORS_ORIGINS": f"http://127.0.0.1:{E2E_FRONTEND_PORT}",
            "UPLOAD_DIR": str(LOG_DIR / "uploads"),
            "MAX_UPLOAD_SIZE_MB": "15",
            "YOLO_INFERENCE_ENABLED": "true",
            "YOLO_INFERENCE_MODE": "auto",
            "YOLO_WEIGHTS_PATH": str(LOG_DIR / "missing_weights.pt"),
            "YOLO_DEVICE": "cpu",
            "YOLO_AUTOINSTALL": "false",
        }
    )
    return env


def _wait_http_ok(url: str, *, timeout: float, proc: subprocess.Popen[bytes] | None = None) -> None:
    deadline = time.monotonic() + timeout
    last = "no attempt"
    while time.monotonic() < deadline:
        if proc is not None and proc.poll() is not None:
            raise RuntimeError(f"子进程提前退出（exit={proc.returncode}），无法等待 {url}")
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:  # noqa: S310 - fixed localhost URL
                if resp.status < 500:
                    return
                last = f"HTTP {resp.status}"
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last = repr(exc)
        time.sleep(0.25)
    raise RuntimeError(f"等待 {url} 就绪超时（{timeout}s），最后错误：{last}")


def _port_is_listening(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _spawn(cmd: list[str], *, log_name: str, env: dict[str, str]) -> subprocess.Popen[bytes]:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handle = (LOG_DIR / log_name).open("wb")
    return subprocess.Popen(  # noqa: S603 - fixed argv, no shell
        cmd,
        cwd=str(REPO_ROOT),
        env=env,
        stdout=handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )


def _terminate(proc: subprocess.Popen[bytes], *, label: str, timeout: float = 20.0) -> None:
    if proc.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        proc.communicate(timeout=timeout)
    finally:
        print(f"[e2e] stopped {label} (exit={proc.returncode})")


# ── database baseline (synchronous: no asyncio.run inside fixture) ───────────
def _reset_and_reseed_rules() -> None:
    """Truncate the 17 business tables and restore only roles + v1 business rules."""
    from sqlalchemy import create_engine, text
    from sqlalchemy.pool import NullPool

    quoted = ", ".join(f'"{name}"' for name in _ordered_business_table_names())
    engine = create_engine(SYNC_DATABASE_URL, poolclass=NullPool)
    try:
        with engine.begin() as conn:
            conn.execute(text(f"TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE"))
            for code, name, desc in _DEFAULT_ROLES:
                conn.execute(
                    text(
                        "INSERT INTO roles (code, name, description) "
                        "VALUES (:code, :name, :description)"
                    ),
                    {"code": code, "name": name, "description": desc},
                )
            for rule_code, rule_value, description in _default_business_rule_seeds():
                conn.execute(
                    text(
                        "INSERT INTO business_rules "
                        "(rule_code, version, rule_value, description, is_active) "
                        "VALUES (:rule_code, 1, CAST(:rule_value AS jsonb), :description, true)"
                    ),
                    {
                        "rule_code": rule_code,
                        "rule_value": json.dumps(rule_value, ensure_ascii=False),
                        "description": description,
                    },
                )
    finally:
        engine.dispose()


# ── session fixtures ─────────────────────────────────────────────────────────
@pytest.fixture(scope="session")
def base_url(pytestconfig: pytest.Config) -> str | None:
    """Module-level `base_url` session fixture replacing `pytest_playwright._BaseUrlFallback`.

    `pytest-playwright` (without `pytest-base-url`) registers `_BaseUrlFallback.base_url`
    as a bound instance method with `scope="session"`, which triggers
    `PytestRemovedIn10Warning` on pytest 9.2+. Defining the same fixture at module scope
    keeps `--base-url` / `base_url` ini semantics intact with zero warning.
    """
    raw = pytestconfig.getoption("--base-url") or pytestconfig.getini("base_url") or None
    return str(raw) if raw else None


@pytest.fixture(scope="session", autouse=True)
def e2e_pg_session_lock() -> Iterator[None]:
    """Serialize E2E vs. integration runs against `tomori_test` (same lock file)."""
    PG_SESSION_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with PG_SESSION_LOCK_PATH.open("w", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _run_alembic_upgrade_head() -> None:
    """Run `alembic upgrade head` as a subprocess.

    `migrations/env.py` calls `asyncio.run(...)`, which raises inside the
    `pytest-playwright` event loop that pytest-asyncio keeps running for session
    fixtures; a subprocess keeps the migration on a clean interpreter.
    """
    env = dict(os.environ)
    env.update(
        {
            "APP_ENV": "test",
            "DATABASE_URL": E2E_DATABASE_URL,
            "TEST_DATABASE_URL": E2E_DATABASE_URL,
            "JWT_SECRET_KEY": "tomori-stage2-isolated-test-jwt-secret-key-not-for-prod",
        }
    )
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["uv", "run", "alembic", "-c", "alembic.ini", "upgrade", "head"],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"alembic upgrade head 失败（exit={result.returncode}）:\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


@pytest.fixture(scope="session")
def e2e_migrated_db(e2e_pg_session_lock: None) -> Iterator[str]:
    """Ensure `tomori_test` schema is at Alembic head (subprocess, loop-safe)."""
    _ = e2e_pg_session_lock
    _run_alembic_upgrade_head()
    yield E2E_DATABASE_URL


@pytest.fixture(scope="session")
def e2e_baseline(e2e_migrated_db: str, e2e_pg_session_lock: None) -> Iterator[str]:
    """Reset `tomori_test` to the post-migration baseline before the backend boots."""
    _ = (e2e_migrated_db, e2e_pg_session_lock)
    (LOG_DIR / "uploads").mkdir(parents=True, exist_ok=True)
    _reset_and_reseed_rules()
    yield E2E_DATABASE_URL
    _reset_and_reseed_rules()


@pytest.fixture(scope="session")
def backend_server(e2e_baseline: str) -> Iterator[str]:
    """Start the real `uvicorn` on 18142; its lifespan seeds demo data itself."""
    _ = e2e_baseline
    for port in (E2E_BACKEND_PORT, E2E_FRONTEND_PORT):
        if _port_is_listening(port):
            raise RuntimeError(f"端口 {port} 已被占用，E2E 必须独占 18142/15174")

    proc = _spawn(
        [
            "uv",
            "run",
            "uvicorn",
            "tomori.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(E2E_BACKEND_PORT),
        ],
        log_name="backend.log",
        env=_e2e_env(),
    )
    try:
        _wait_http_ok(f"{E2E_BACKEND_URL}/api/v1/health", timeout=150, proc=proc)
        yield E2E_BACKEND_URL
    finally:
        _terminate(proc, label="backend:18142")
        if _port_is_listening(E2E_BACKEND_PORT):
            raise RuntimeError(f"后端进程已回收但端口 {E2E_BACKEND_PORT} 仍在监听（僵尸进程）")


def _start_frontend(*, port: int, api_target: str, log_name: str) -> subprocess.Popen[bytes]:
    """Spawn the real Vite dev server on `port`, proxying `/api/v1` to `api_target`."""
    if shutil.which("pnpm") is None:
        raise RuntimeError("未找到 pnpm，无法启动 E2E 前端")
    if not (REPO_ROOT / "src" / "frontend" / "node_modules").is_dir():
        raise RuntimeError(
            "src/frontend/node_modules 缺失，请先执行 pnpm --dir src/frontend install"
        )

    env = dict(os.environ)
    env["VITE_API_TARGET"] = api_target
    proc = _spawn(
        [
            "pnpm",
            "--dir",
            "src/frontend",
            "dev",
            "--mode",
            "e2e",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        log_name=log_name,
        env=env,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        _wait_http_ok(f"{base}/", timeout=180, proc=proc)
        _wait_http_ok(f"{base}/api/v1/health", timeout=60, proc=proc)
    except Exception:
        _terminate(proc, label=f"frontend:{port}")
        raise
    return proc


@pytest.fixture(scope="session")
def frontend_server(backend_server: str) -> Iterator[str]:
    """Start the real Vite dev server on 15174 proxying to the auto-mode backend."""
    _ = backend_server
    proc = _start_frontend(
        port=E2E_FRONTEND_PORT, api_target=E2E_BACKEND_URL, log_name="frontend.log"
    )
    try:
        yield E2E_FRONTEND_URL
    finally:
        _terminate(proc, label="frontend:15174")
        if _port_is_listening(E2E_FRONTEND_PORT):
            raise RuntimeError(f"前端进程已回收但端口 {E2E_FRONTEND_PORT} 仍在监听（僵尸进程）")


@pytest.fixture(scope="session")
def strict_vision_frontend(strict_vision_backend: str) -> Iterator[str]:
    """Real Vite dev server on 15175 proxying to the `strict_custom` backend on 18143."""
    _ = strict_vision_backend
    if _port_is_listening(STRICT_VISION_FRONTEND_PORT):
        raise RuntimeError(f"端口 {STRICT_VISION_FRONTEND_PORT} 已被占用")
    proc = _start_frontend(
        port=STRICT_VISION_FRONTEND_PORT,
        api_target=STRICT_VISION_BACKEND_URL,
        log_name="frontend-strict-vision.log",
    )
    try:
        yield STRICT_VISION_FRONTEND_URL
    finally:
        _terminate(proc, label="frontend:15175")
        if _port_is_listening(STRICT_VISION_FRONTEND_PORT):
            raise RuntimeError(f"前端进程已回收但端口 {STRICT_VISION_FRONTEND_PORT} 仍在监听")


@pytest.fixture(scope="session")
def strict_vision_backend(e2e_baseline: str) -> Iterator[str]:
    """Second real `uvicorn` on 18143 with `strict_custom` -> guaranteed 503 VISION_DISABLED."""
    _ = e2e_baseline
    if _port_is_listening(STRICT_VISION_BACKEND_PORT):
        raise RuntimeError(f"端口 {STRICT_VISION_BACKEND_PORT} 已被占用")

    env = _e2e_env()
    env["YOLO_INFERENCE_MODE"] = "strict_custom"
    env["BACKEND_PORT"] = str(STRICT_VISION_BACKEND_PORT)
    proc = _spawn(
        [
            "uv",
            "run",
            "uvicorn",
            "tomori.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(STRICT_VISION_BACKEND_PORT),
        ],
        log_name="backend-strict-vision.log",
        env=env,
    )
    try:
        _wait_http_ok(f"{STRICT_VISION_BACKEND_URL}/api/v1/health", timeout=150, proc=proc)
        yield STRICT_VISION_BACKEND_URL
    finally:
        _terminate(proc, label="backend:18143")
        if _port_is_listening(STRICT_VISION_BACKEND_PORT):
            raise RuntimeError(f"后端进程已回收但端口 {STRICT_VISION_BACKEND_PORT} 仍在监听")


@pytest.fixture(scope="session")
def e2e_artifacts() -> Iterator[Path]:
    """Screenshot directory; the per-test count backs the `artifacts N` evidence figure."""
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    for stale in ARTIFACT_DIR.glob("*.png"):
        stale.unlink()
    yield ARTIFACT_DIR


@pytest.fixture(scope="session")
def e2e_upload_files(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """Materialize real static inspection images for `<input type=file>` uploads."""
    base = tmp_path_factory.mktemp("e2e-uploads")
    files = {
        "night_normal_lit": base / "night_normal_lit.png",
        "occluded_fault": base / "occluded_fault.png",
        "repair_photo": base / "repair_photo.jpg",
        "acceptance_photo": base / "acceptance_photo.jpg",
    }
    files["night_normal_lit"].write_bytes(make_night_normal_lit_png())
    files["occluded_fault"].write_bytes(make_occluded_fault_png())
    files["repair_photo"].write_bytes(make_static_image("JPEG", bg_color=(12, 88, 144)))
    files["acceptance_photo"].write_bytes(make_static_image("JPEG", bg_color=(30, 160, 90)))
    return files


@pytest.fixture(scope="session")
def frontend_url(frontend_server: str) -> str:
    return frontend_server


@pytest.fixture(autouse=True)
def _artifact_capture(
    request: pytest.FixtureRequest,
    page: Page,
    e2e_artifacts: Path,
) -> Iterator[None]:
    """Always persist exactly one screenshot per test -> `artifacts N == collected N`."""
    yield
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", request.node.nodeid)[-120:]
    try:
        page.screenshot(path=str(e2e_artifacts / f"{safe}.png"), full_page=True)
    except Exception as exc:  # an artifact failure must never mask the real verdict
        print(f"[e2e] artifact screenshot skipped for {request.node.nodeid}: {exc!r}")


# ── interaction helpers (testids are the frozen contract) ────────────────────
MESSAGE_BOX_NOTE = (
    "Element Plus `ElMessageBox.confirm` 渲染在 Vue app 之外且 `src/frontend/src` 未外包封装，"
    "无法承载 data-testid；确认弹窗只能按 role=button + 可见名称定位。"
)


def open_app(page: Page, frontend_url: str, path: str = "/login") -> None:
    """Navigate to a route and wait until the SPA finished its first render."""
    page.goto(f"{frontend_url}{path}", wait_until="networkidle")


# auditor `batch19` `P3-2`: `auth-username-input` 偶发 `30s` 渲染超时（vite 冷编译/资源抖动）。
# 统一登录/登出入口加「visibility 等待 + 指数退避重试 + 失败落诊断快照」。
_LOGIN_BACKOFF_SECONDS: tuple[float, ...] = (0.0, 2.0, 5.0)


def _diagnose(page: Page, *, label: str) -> str:
    """落最后截图 + URL/title，供 flake 诊断（与 `_artifact_capture` 同目录）。"""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    shot = LOG_DIR / f"{label}-diag-{int(time.time())}.png"
    import contextlib

    with contextlib.suppress(Exception):  # 截图失败不能让诊断本身再失败
        page.screenshot(path=str(shot), full_page=True)
    url, title = "<unavailable>", "<unavailable>"
    with contextlib.suppress(Exception):
        url, title = page.url, page.title()
    return f"url={url!r} title={title!r} screenshot={shot}"


def login(page: Page, frontend_url: str, account_key: str) -> None:
    """Log in through the real form using only frozen `data-testid` anchors.

    `auth-username-input` render flake（`P3-2`）：`expect().to_be_visible` 确认 +
    `(0, 2, 5)s` 指数退避最多 3 次导航重试；仍失败则落截图与页面上下文后抛出。
    """
    username, secret = DEMO_CREDENTIALS[account_key]
    last_exc: Exception | None = None
    for _attempt, delay in enumerate(_LOGIN_BACKOFF_SECONDS, start=1):
        if delay > 0:
            # 指数退避休眠节拍（backoff），不是元素等待 —— `auth-username-input` 可见性
            # 由下方 `expect().to_be_visible` 保证；此处仅让下一次重试在冷编译后错峰发起。
            page.wait_for_timeout(int(delay * 1000))
        try:
            open_app(page, frontend_url, "/login")
            expect(page.get_by_test_id("auth-username-input")).to_be_visible(timeout=30_000)
            break
        except Exception as exc:  # noqa: BLE001 — 重试循环需捕获一切动作性失败
            last_exc = exc
    else:
        raise AssertionError(
            f"login({account_key}): auth-username-input 在 "
            f"{len(_LOGIN_BACKOFF_SECONDS)} 次尝试后仍不可见 — "
            f"{_diagnose(page, label='login-flake')}"
        ) from last_exc
    page.get_by_test_id("auth-username-input").fill(username)
    page.get_by_test_id("auth-password-input").fill(secret)
    page.get_by_test_id("auth-login-submit").click()
    page.wait_for_function("() => !window.location.pathname.startsWith('/login')", timeout=20_000)


def logout(page: Page) -> None:
    """Log out via the frozen `auth-logout-btn`（桌面/移动双锚取第一个可见）。"""
    btn = page.get_by_test_id("auth-logout-btn").first
    expect(btn).to_be_visible(timeout=30_000)
    btn.click()
    page.wait_for_function("() => window.location.pathname.startsWith('/login')", timeout=20_000)


def confirm_message_box(page: Page, confirm_label: str) -> None:
    """Accept an `ElMessageBox.confirm` dialog (no testid available, see `MESSAGE_BOX_NOTE`)."""
    page.get_by_role("button", name=confirm_label, exact=True).click()


def select_option(page: Page, select_testid: str, option_testid: str) -> None:
    """Drive an `el-select` (`:teleported=false`) and pick an option by its testid."""
    page.get_by_test_id(select_testid).click()
    option = page.get_by_test_id(option_testid)
    option.wait_for(state="visible", timeout=10_000)
    option.click()


def _localhost_client() -> httpx.Client:
    """httpx client that ignores proxy env vars.

    The host's `no_proxy` contains a bracketed `[::1]` entry, which `httpx` 0.28 cannot
    parse (`ValueError: invalid literal for int() ... ':1]'`). Chromium handles the same
    env correctly, so only the Python-side assertions need `trust_env=False`.
    """
    return httpx.Client(trust_env=False, timeout=30)


def api_login(backend_url: str, account_key: str) -> str:
    """Direct backend login returning a Bearer token (for real captured error payloads)."""
    username, secret = DEMO_CREDENTIALS[account_key]
    with _localhost_client() as client:
        resp = client.post(
            f"{backend_url}/api/v1/auth/login",
            json={"username": username, "password": secret},
        )
    resp.raise_for_status()
    payload: dict[str, Any] = resp.json()
    return str(payload["access_token"])


def create_lighting_point(backend_url: str, admin_token: str, *, point_code: str) -> int:
    """Create a real lighting point through `POST /lighting-points` (production path).

    AI-candidate tests need a point that has never produced an active ticket, otherwise the
    real `409 DUPLICATE_ACTIVE_TICKET` guard fires across tests.
    """
    payload = {
        "point_code": point_code,
        "region_id": 2,
        "name": f"E2E 点位 {point_code}",
        "road_name": "E2E 测试路段",
        "road_level": "BRANCH",
        "longitude": "113.6812340",
        "latitude": "34.7891230",
        "lamp_count": 1,
        "power_supply_type": "GRID",
    }
    with _localhost_client() as client:
        created = client.post(
            f"{backend_url}/api/v1/lighting-points",
            headers={"Authorization": f"Bearer {admin_token}"},
            json=payload,
        )
    if created.status_code != 201:
        raise RuntimeError(f"创建 E2E 照明点位失败（{created.status_code}）：{created.text}")
    body: dict[str, Any] = created.json()
    return int(body["id"])


def api_get(backend_url: str, token: str, path: str) -> httpx.Response:
    """Authenticated GET against the real backend (independent of the browser context)."""
    with _localhost_client() as client:
        return client.get(f"{backend_url}{path}", headers={"Authorization": f"Bearer {token}"})


def api_post(backend_url: str, token: str, path: str, *, json_body: Any = None) -> httpx.Response:
    """Authenticated POST against the real backend."""
    with _localhost_client() as client:
        return client.post(
            f"{backend_url}{path}",
            headers={"Authorization": f"Bearer {token}"},
            json=json_body,
        )


# ── collection guard ─────────────────────────────────────────────────────────
_E2E_DIR = str(Path(__file__).resolve().parent)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Run E2E only when explicitly targeted (`pytest tests/e2e`) or `TOMORI_E2E=1`.

    `docs/DESIGN.md:1269` defines `uv run pytest tests/e2e` as a **separate serial gate**
    under the `claim-e2e` lock, because it needs two real servers plus Chromium. `testpaths`
    in `pyproject.toml` is `["tests"]`, so a plain `uv run pytest tests` would otherwise
    silently pull these 22 items into the unit/integration gate and try to boot uvicorn and
    vite. Skipping (rather than erroring) keeps that gate fast and hermetic while leaving
    the documented E2E command fully functional. Only items living under this directory are
    ever touched — `tests/unit` and `tests/integration` are never affected.
    """
    if os.environ.get("TOMORI_E2E") == "1":
        return
    if any("tests/e2e" in str(arg) for arg in config.args):
        return
    skip_marker = pytest.mark.skip(
        reason="E2E 需显式指定 `pytest tests/e2e` 或 TOMORI_E2E=1（claim-e2e 独占窗口）"
    )
    for item in items:
        if str(item.path).startswith(_E2E_DIR):
            item.add_marker(skip_marker)
