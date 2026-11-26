"""Run the complete browser suite in isolated synchronous and asynchronous processes.

Usage: uv run --no-sync python tests/e2e/run_split.py --log-dir /absolute/path
Each test process has a separate baseline, logs, screenshots, and stack-guard lease.
"""

from __future__ import annotations

import argparse
import inspect
import json
import os
import signal
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path
from typing import Any

import pytest
from pytest_asyncio.plugin import PytestAsyncioFunction

ROOT = Path(__file__).resolve().parents[2]
SUPPORTED_PYTEST_ASYNCIO = "1.5.0"


def collection_matches(actual: dict[str, str], expected: dict[str, str]) -> bool:
    return actual == expected


class Collection:
    def __init__(self) -> None:
        self.items: dict[str, str] = {}
        self.errors: list[str] = []

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        for item in session.items:
            name = item.nodeid
            obj = getattr(item, "obj", None)
            is_async = isinstance(item, PytestAsyncioFunction)
            is_coroutine = inspect.iscoroutinefunction(inspect.unwrap(obj)) if obj else False
            if is_async != is_coroutine:
                self.errors.append(f"ambiguous async classification: {name}")
            if name in self.items:
                self.errors.append(f"duplicate test: {name}")
            self.items[name] = "async" if is_async else "sync"


def child(mode: str, log_dir: Path, expected: dict[str, str] | None) -> int:
    import tests.e2e.conftest as fixtures

    fixtures.LOG_DIR = log_dir / "run"
    fixtures.ARTIFACT_DIR = log_dir / "artifacts"
    collection = Collection()
    args = ["tests/e2e", "--collect-only", "-q"] if mode == "collect" else [*(expected or []), "-q"]
    if mode == "run":

        class Check(Collection):
            def pytest_collection_finish(self, session: pytest.Session) -> None:
                super().pytest_collection_finish(session)
                if not collection_matches(self.items, expected or {}) or self.errors:
                    raise pytest.UsageError(
                        "selected collection or classification differs from manifest"
                    )

        collection = Check()
    status = int(pytest.main(args, plugins=[collection]))
    if mode == "collect":
        (log_dir / "collection.json").write_text(
            json.dumps({"items": collection.items, "errors": collection.errors}, indent=2),
            encoding="utf-8",
        )
    return status if not collection.errors else 2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log-dir", type=Path, required=True)
    parser.add_argument("--child", choices=("collect", "run"))
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()
    if version("pytest-asyncio") != SUPPORTED_PYTEST_ASYNCIO:
        raise RuntimeError("Unsupported pytest-asyncio version; review collection classification")
    log_dir = args.log_dir.resolve()
    if not args.child:
        log_dir.mkdir(parents=True, exist_ok=False)
    else:
        log_dir.mkdir(parents=True, exist_ok=True)
    if args.child:
        expected = json.loads(args.manifest.read_text(encoding="utf-8")) if args.manifest else None
        return child(args.child, log_dir, expected)

    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    base = [sys.executable, str(Path(__file__).resolve()), "--log-dir", str(log_dir)]
    with (log_dir / "collect.log").open("w", encoding="utf-8") as output:
        collected = subprocess.run(
            [*base, "--child", "collect"],
            cwd=ROOT,
            env=env,
            stdout=output,
            stderr=subprocess.STDOUT,
            check=False,
        )
    if collected.returncode != 0:
        print(f"collection exit={collected.returncode}; see {log_dir / 'collect.log'}")
        return collected.returncode
    manifest: dict[str, Any] = json.loads((log_dir / "collection.json").read_text(encoding="utf-8"))
    items: dict[str, str] = manifest["items"]
    groups = {
        kind: [name for name, value in items.items() if value == kind] for kind in ("sync", "async")
    }
    sync, asynchronous = set(groups["sync"]), set(groups["async"])
    if (
        manifest["errors"]
        or not sync
        or not asynchronous
        or sync & asynchronous
        or sync | asynchronous != set(items)
    ):
        print("collection partition invalid")
        return 2

    def terminate(_signum: int, _frame: Any) -> None:
        raise SystemExit("terminated by signal")

    signal.signal(signal.SIGTERM, terminate)
    owner = f"e2e-{os.getpid()}"
    exits: dict[str, dict[str, int | str | None]] = {}
    for kind in ("sync", "async"):
        group_dir = log_dir / kind
        group_dir.mkdir(parents=True, exist_ok=True)
        group_manifest = group_dir / "manifest.json"
        group_manifest.write_text(
            json.dumps({name: kind for name in groups[kind]}, indent=2), encoding="utf-8"
        )
        state: dict[str, int | str | None] = {"claim": None, "pytest": None, "release": None}
        exits[kind] = state

        def save_state() -> None:
            (log_dir / "exit-codes.json").write_text(json.dumps(exits, indent=2), encoding="utf-8")

        with (group_dir / "guard.log").open("w", encoding="utf-8") as guard:
            try:
                claim = subprocess.run(
                    ["./deploy/stack-guard.sh", "claim-e2e", "--owner", owner],
                    cwd=ROOT,
                    stdout=guard,
                    stderr=subprocess.STDOUT,
                    check=False,
                )
            except BaseException as exc:
                state["claim"] = f"exception: {type(exc).__name__}: {exc}"
                save_state()
                raise
            state["claim"] = claim.returncode
            if claim.returncode != 0:
                save_state()
                break
            try:
                save_state()
                with (group_dir / "pytest.log").open("w", encoding="utf-8") as output:
                    run = subprocess.run(
                        [
                            sys.executable,
                            str(Path(__file__).resolve()),
                            "--log-dir",
                            str(group_dir),
                            "--child",
                            "run",
                            "--manifest",
                            str(group_manifest),
                        ],
                        cwd=ROOT,
                        env=env,
                        stdout=output,
                        stderr=subprocess.STDOUT,
                        check=False,
                    )
                state["pytest"] = run.returncode
            except BaseException as exc:
                state["pytest"] = f"exception: {type(exc).__name__}: {exc}"
                raise
            finally:
                try:
                    release = subprocess.run(
                        ["./deploy/stack-guard.sh", "release-e2e", "--owner", owner],
                        cwd=ROOT,
                        stdout=guard,
                        stderr=subprocess.STDOUT,
                        check=False,
                    )
                    state["release"] = release.returncode
                except BaseException as exc:
                    state["release"] = f"exception: {type(exc).__name__}: {exc}"
                    raise
                finally:
                    save_state()
        print(f"{kind}: {len(groups[kind])} tests, statuses={state}")
        if state["release"] != 0:
            break
    return (
        0
        if len(exits) == 2
        and all(all(code == 0 for code in state.values()) for state in exits.values())
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
