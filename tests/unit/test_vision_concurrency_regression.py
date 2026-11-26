"""Regression tests for the vision inference slot lifecycle."""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from threading import BoundedSemaphore, Event
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest

from tomori.vision import baseline, client, mapping, metrics
from tomori.vision.errors import VISION_TIMEOUT, VisionError


@pytest.fixture
def pipeline(monkeypatch: pytest.MonkeyPatch) -> Callable[[], Any]:
    vision = object.__new__(client.VisionClient)
    monkeypatch.setattr(
        vision, "settings", SimpleNamespace(vision_timeout_seconds=0.1), raising=False
    )
    monkeypatch.setattr(vision, "_resolve_backend", lambda **_: ("baseline", None))
    monkeypatch.setattr(metrics, "to_hsv", lambda bgr: None)
    monkeypatch.setattr(metrics, "assess_quality", lambda bgr, hsv: object())
    monkeypatch.setattr(mapping, "build_result", lambda **kwargs: "completed")
    monkeypatch.setattr(client, "_INFER_SLOT", BoundedSemaphore(1))
    image = SimpleNamespace(shape=(1, 1, 3))

    def run() -> Any:
        return vision._run_pipeline_on_bgr(
            image,
            original_image_url="test",
            scene_lighting_hint="auto",
            save_annotated=False,
        )

    return run


def test_running_timeout_holds_slot_until_worker_finishes(
    monkeypatch: pytest.MonkeyPatch, pipeline: Callable[[], Any]
) -> None:
    started = Event()
    release = Event()

    def detect(bgr: Any, quality: Any) -> list[Any]:
        started.set()
        assert release.wait(5)
        return []

    monkeypatch.setattr(baseline, "detect", detect)
    with ThreadPoolExecutor(max_workers=1) as pool:
        monkeypatch.setattr(client, "_INFER_POOL", pool)
        try:
            with pytest.raises(VisionError) as timeout:
                pipeline()
            assert timeout.value.code == VISION_TIMEOUT
            assert started.is_set()
            with pytest.raises(VisionError) as busy:
                pipeline()
            assert busy.value.code == VISION_TIMEOUT
            assert "繁忙" in str(busy.value)
        finally:
            release.set()
        pool.shutdown(wait=True)
    with ThreadPoolExecutor(max_workers=1) as pool:
        monkeypatch.setattr(client, "_INFER_POOL", pool)
        assert pipeline() == "completed"


def test_queued_timeout_cancellation_releases_slot(
    monkeypatch: pytest.MonkeyPatch, pipeline: Callable[[], Any]
) -> None:
    started = Event()
    release = Event()
    detect = Mock(return_value=[])
    monkeypatch.setattr(baseline, "detect", detect)
    with ThreadPoolExecutor(max_workers=1) as pool:
        monkeypatch.setattr(client, "_INFER_POOL", pool)

        def occupy_worker() -> None:
            started.set()
            assert release.wait(5)

        blocker = pool.submit(occupy_worker)
        try:
            assert started.wait(5)
            with pytest.raises(VisionError) as timeout:
                pipeline()
            assert timeout.value.code == VISION_TIMEOUT
            assert client._INFER_SLOT.acquire(blocking=False)
            client._INFER_SLOT.release()
        finally:
            release.set()
            blocker.result(timeout=5)
        assert pipeline() == "completed"
        assert detect.call_count == 1


def test_submit_failure_releases_slot(
    monkeypatch: pytest.MonkeyPatch, pipeline: Callable[[], Any]
) -> None:
    failing_pool = Mock()
    failing_pool.submit.side_effect = RuntimeError("submit failed")
    monkeypatch.setattr(client, "_INFER_POOL", failing_pool)
    with pytest.raises(RuntimeError, match="submit failed"):
        pipeline()
    monkeypatch.setattr(baseline, "detect", lambda bgr, quality: [])
    with ThreadPoolExecutor(max_workers=1) as pool:
        monkeypatch.setattr(client, "_INFER_POOL", pool)
        assert pipeline() == "completed"
