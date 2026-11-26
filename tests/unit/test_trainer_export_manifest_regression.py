"""Export-only manifests identify the actual evaluated weight and output files."""

import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tomori.vision import trainer


@pytest.mark.parametrize("relative", ["weights/custom.pt", "weights/last.pt", "outside.pt"])
def test_export_manifest_describes_actual_weights(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relative: str
) -> None:
    weight = tmp_path / relative
    weight.parent.mkdir(parents=True, exist_ok=True)
    weight.write_bytes(b"selected weight")
    sibling = weight.parent / "last.pt"
    if sibling != weight:
        sibling.write_bytes(b"unselected checkpoint")
    calls = []

    class FakeModel:
        def __init__(self, path: str) -> None:
            calls.append(Path(path))

        def val(self, **kwargs: object) -> object:
            return object()

        def export(self, **kwargs: object) -> str:
            output = weight.with_suffix(".onnx")
            output.write_bytes(b"exported model")
            return str(output)

    monkeypatch.setitem(
        sys.modules, "ultralytics", SimpleNamespace(YOLO=FakeModel, __version__="test")
    )
    monkeypatch.setattr(trainer, "validate_data_yaml", lambda _: ({}, ["lamp"], 1))
    monkeypatch.setattr(trainer, "extract_det_metrics", lambda *_: {"mAP50": 0.5, "mAP50_95": 0.4})
    monkeypatch.setattr(trainer, "_verify_exported_onnx", lambda *_, **__: {"valid": True})
    cfg = trainer.TrainConfig(
        data_yaml=tmp_path / "data.yaml", export_only=True, weights_override=weight
    )
    result = trainer.run_training_pipeline(cfg)
    assert calls == [weight.resolve()]
    run_dir = tmp_path
    assert result.run_dir == run_dir
    metrics = json.loads(result.metrics_json.read_text())
    assert metrics["head_contract"]["deploy_artifact"] == str(
        result.best_e2e_onnx.relative_to(run_dir)
    )

    def digest(p: Path) -> str:
        return hashlib.sha256(p.read_bytes()).hexdigest()

    assert metrics["sha256"] == {
        weight.name: digest(weight),
        "best_e2e.onnx": digest(result.best_e2e_onnx),
    }
    entries = {}
    for line in result.sha256sums.read_text().splitlines():
        checksum, name = line.split(maxsplit=1)
        entries[name.strip()] = checksum
    expected = {
        str(weight.relative_to(run_dir)),
        str(result.best_e2e_onnx.relative_to(run_dir)),
        "metrics.json",
    }
    assert set(entries) == expected
    for name, checksum in entries.items():
        assert checksum == digest(run_dir / name)
