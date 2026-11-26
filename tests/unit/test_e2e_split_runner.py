"""Exercise split runner failure accounting without starting servers."""

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from tests.e2e import run_split


def test_preserves_pytest_and_release_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dest = tmp_path / "result"
    monkeypatch.setattr(sys, "argv", ["run_split", "--log-dir", str(dest)])

    def fake_run(command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        if "collect" in command:
            (dest / "collection.json").write_text(
                json.dumps({"items": {"a": "sync", "b": "async"}, "errors": []})
            )
            return subprocess.CompletedProcess(command, 0)
        if "claim-e2e" in command:
            return subprocess.CompletedProcess(command, 0)
        if "release-e2e" in command:
            return subprocess.CompletedProcess(command, 8)
        return subprocess.CompletedProcess(command, 7)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert run_split.main() == 1
    states = json.loads((dest / "exit-codes.json").read_text())
    assert states["sync"] == {"claim": 0, "pytest": 7, "release": 8}
    assert "async" not in states


def test_exception_still_releases_guard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dest = tmp_path / "result"
    monkeypatch.setattr(sys, "argv", ["run_split", "--log-dir", str(dest)])
    calls: list[str] = []

    def fake_run(command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        if "collect" in command:
            (dest / "collection.json").write_text(
                json.dumps({"items": {"a": "sync", "b": "async"}, "errors": []})
            )
            return subprocess.CompletedProcess(command, 0)
        if "claim-e2e" in command:
            return subprocess.CompletedProcess(command, 0)
        if "release-e2e" in command:
            calls.append("release")
            return subprocess.CompletedProcess(command, 0)
        raise RuntimeError("injected failure")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="injected failure"):
        run_split.main()
    state = json.loads((dest / "exit-codes.json").read_text())["sync"]
    assert calls == ["release"] and state["release"] == 0
    assert "injected failure" in state["pytest"]


def test_state_write_error_still_releases_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dest = tmp_path / "result"
    monkeypatch.setattr(sys, "argv", ["run_split", "--log-dir", str(dest)])
    calls: list[str] = []
    original_write = Path.write_text
    failed = False

    def fake_write(path: Path, *args: Any, **kwargs: Any) -> int:
        nonlocal failed
        if path.name == "exit-codes.json" and not failed:
            failed = True
            raise OSError("injected state write failure")
        return original_write(path, *args, **kwargs)

    def fake_run(command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        if "collect" in command:
            (dest / "collection.json").write_text(
                json.dumps({"items": {"a": "sync", "b": "async"}, "errors": []})
            )
        if "release-e2e" in command:
            calls.append("release")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(Path, "write_text", fake_write)
    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(OSError, match="state write failure"):
        run_split.main()
    assert calls == ["release"]


def test_child_rejects_changed_classification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_main(_args: list[str], plugins: list[Any]) -> int:
        item = SimpleNamespace(nodeid="test_changed", obj=lambda: None)
        session = cast(pytest.Session, SimpleNamespace(items=[item]))
        plugins[0].pytest_collection_finish(session)
        return 0

    monkeypatch.setattr(pytest, "main", fake_main)
    with pytest.raises(pytest.UsageError, match="classification"):
        run_split.child("run", tmp_path, {"test_changed": "async"})
