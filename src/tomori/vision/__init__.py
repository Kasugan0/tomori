"""公共照明静态巡查照片分析子系统（`src/tomori/vision/`）。

业务红线（任何改动不得突破，`V1–V3` / `N1–N5`）：
1. 只处理单帧静态图片，不接实时监控，不接 RTSP / 视频流。
2. 不连接、不控制任何灯具硬件设备。
3. 不做能耗预测。
4. AI 只产出故障**候选**，必须经巡查员人工确认（`requires_inspector_confirmation=True`）。
5. 永不替代现场断电与电气安全检查（`replaces_electrical_safety_check=False`），
   永不产出 `FLICKERING` / `ELECTRIC_HAZARD`。

注意：本包顶层仅导出轻量契约、配置与客户端符号；CLI 子模块（`dataset_prep` / `eda` /
`trainer`）通过 PEP 562 `__getattr__` 延迟按需导入，既保证纯后端环境缺失 `--extra vision`
时安全导入，又避免 `python -m tomori.vision.<cli>` 触发 `runpy RuntimeWarning`。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tomori.vision.client import (
    ReadinessStatus,
    VisionClient,
    analyze_bytes,
    analyze_image,
    validate_onnx_session,
)
from tomori.vision.config import Settings, load_settings
from tomori.vision.contract import FaultCandidate, FaultCandidateResult
from tomori.vision.errors import VisionError, make_degraded_error

if TYPE_CHECKING:
    from tomori.vision.dataset_prep import (
        DatasetPrepConfig,
        DatasetSourceSpec,
        PrepResult,
        prepare_dataset,
    )
    from tomori.vision.eda import DatasetFacts, analyze_dataset, discover_dataset, run_eda
    from tomori.vision.trainer import TrainArtifacts, TrainConfig, run_training_pipeline

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "DatasetPrepConfig": ("tomori.vision.dataset_prep", "DatasetPrepConfig"),
    "DatasetSourceSpec": ("tomori.vision.dataset_prep", "DatasetSourceSpec"),
    "PrepResult": ("tomori.vision.dataset_prep", "PrepResult"),
    "prepare_dataset": ("tomori.vision.dataset_prep", "prepare_dataset"),
    "DatasetFacts": ("tomori.vision.eda", "DatasetFacts"),
    "analyze_dataset": ("tomori.vision.eda", "analyze_dataset"),
    "discover_dataset": ("tomori.vision.eda", "discover_dataset"),
    "run_eda": ("tomori.vision.eda", "run_eda"),
    "TrainArtifacts": ("tomori.vision.trainer", "TrainArtifacts"),
    "TrainConfig": ("tomori.vision.trainer", "TrainConfig"),
    "run_training_pipeline": ("tomori.vision.trainer", "run_training_pipeline"),
}


def __getattr__(name: str) -> Any:
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    mod_name, attr_name = target
    mod = __import__(mod_name, fromlist=[attr_name])
    val = getattr(mod, attr_name)
    globals()[name] = val
    return val


def __dir__() -> list[str]:
    return sorted({*globals().keys(), *_LAZY_EXPORTS.keys()})


__all__ = [
    "DatasetFacts",
    "DatasetPrepConfig",
    "DatasetSourceSpec",
    "FaultCandidate",
    "FaultCandidateResult",
    "PrepResult",
    "ReadinessStatus",
    "Settings",
    "TrainArtifacts",
    "TrainConfig",
    "VisionClient",
    "VisionError",
    "analyze_bytes",
    "analyze_dataset",
    "analyze_image",
    "discover_dataset",
    "load_settings",
    "make_degraded_error",
    "prepare_dataset",
    "run_eda",
    "run_training_pipeline",
    "validate_onnx_session",
]
