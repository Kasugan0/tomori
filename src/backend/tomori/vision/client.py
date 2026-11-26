"""进程内视觉推理客户端（`docs/DESIGN.md` §5.1–§5.3）。

关键隔离与工程纪律：
1. **函数内延迟导入重依赖（`D1`）**：模块顶层仅导入 Python 标准库与轻量内模块
   `tomori.vision.config` / `tomori.vision.contract` / `tomori.vision.errors`，绝不在顶层导入
   `cv2`、`numpy`、`onnxruntime`、`ultralytics` 或 `PIL`，保证纯后端环境缺失 `vision` extra 时
   应用启动不被阻塞。
2. **禁止运行期自动装包**：任何导入或调用 `ultralytics` 前强制设置 `YOLO_AUTOINSTALL=False`
   （当 `settings.yolo_autoinstall` 为 `False` 时）。
3. **ONNX Runtime 启动期形状校验**：加载 `best_e2e.onnx` 时校验 `sess.get_inputs()[0].shape`
   (`== [1, 3, 1280, 1280]`)、`sess.get_outputs()[0].shape` (`== [1, 300, 6]`) 及 `metrics.json`
   中 `imgsz`；不符时抛出含 `INVALID_ARGUMENT` 诊断并在客户端层转为 `503 VISION_DISABLED`。
4. **互斥 `503` 降级与安全红线**：`YOLO_INFERENCE_ENABLED=false` 或 `strict_custom`/`require_custom`
   无自定义权重均返回 `503 VISION_DISABLED`（该码是唯一权威，视觉侧不保留私有未就绪码）；
   推理超时（`VISION_TIMEOUT_SECONDS=60.0`）返回 `503 VISION_TIMEOUT`；其余意外失败返回
   `503 VISION_INFERENCE_FAILED`。
"""

from __future__ import annotations

import concurrent.futures
import importlib.util
import json
import math
import os
import sys
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tomori.vision import config
from tomori.vision.contract import FaultCandidateResult, InferenceMode
from tomori.vision.errors import (
    VISION_DISABLED,
    VISION_INFERENCE_FAILED,
    VISION_TIMEOUT,
    VisionError,
    make_degraded_error,
)

if TYPE_CHECKING:
    from tomori.vision.baseline import RawDetection

__all__ = [
    "ReadinessStatus",
    "VisionClient",
    "analyze_bytes",
    "analyze_image",
    "validate_onnx_session",
]

_VALID_INFERENCE_MODES = frozenset({"auto", "strict_custom"})

# 超时无法终止运行中的线程；槽位直到线程实际结束才释放。
_INFER_POOL = concurrent.futures.ThreadPoolExecutor(
    max_workers=1, thread_name_prefix="vision-infer"
)
_INFER_SLOT = threading.BoundedSemaphore(1)


class _OnnxInvalidArgumentError(VisionError, ValueError):
    """ONNX 启动期形状/配置校验失败：既是 `VisionError(503 VISION_DISABLED)` 也是 `ValueError`."""


@dataclass(frozen=True)
class ReadinessStatus:
    """视觉引擎就绪探针结果。"""

    ready: bool
    mode: InferenceMode | None
    weights_path: str | None
    detail: str


def _parse_bool(raw: str) -> bool:
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _coerce_settings(raw: Any, *, fallback: config.Settings | None = None) -> config.Settings:
    """将 `config.Settings`、`tomori.settings.Settings` 或鸭子类型配置统一转为 `config.Settings`."""
    if isinstance(raw, config.Settings):
        return raw
    base = fallback if fallback is not None else config.load_settings()
    max_bytes_raw: Any = getattr(raw, "max_upload_bytes", None)
    if max_bytes_raw is None:
        max_bytes_raw = getattr(raw, "max_upload_size_bytes", None)
    if max_bytes_raw is None:
        max_bytes_raw = base.max_upload_bytes
    weights_raw = getattr(raw, "yolo_weights_path", base.yolo_weights_path)
    upload_raw = getattr(raw, "upload_dir", base.upload_dir)
    timeout_raw = getattr(raw, "vision_timeout_seconds", config.VISION_TIMEOUT_SECONDS)

    return config.Settings(
        upload_dir=Path(upload_raw),
        max_upload_bytes=int(max_bytes_raw),
        yolo_weights_path=Path(weights_raw),
        yolo_inference_enabled=bool(
            getattr(raw, "yolo_inference_enabled", base.yolo_inference_enabled)
        ),
        yolo_inference_mode=str(getattr(raw, "yolo_inference_mode", base.yolo_inference_mode))
        .strip()
        .lower(),
        yolo_img_size=int(getattr(raw, "yolo_img_size", base.yolo_img_size)),
        yolo_conf_threshold=float(getattr(raw, "yolo_conf_threshold", base.yolo_conf_threshold)),
        yolo_device=str(getattr(raw, "yolo_device", base.yolo_device)),
        yolo_fallback_model=str(
            getattr(raw, "yolo_fallback_model", base.yolo_fallback_model)
        ).strip(),
        yolo_iou_threshold=float(getattr(raw, "yolo_iou_threshold", base.yolo_iou_threshold)),
        yolo_autoinstall=bool(getattr(raw, "yolo_autoinstall", base.yolo_autoinstall)),
        vision_timeout_seconds=float(timeout_raw),
    )


def _apply_env_overrides(settings: config.Settings) -> config.Settings:
    """允许环境变量覆盖缓存的 `tomori.settings` 字段，超时常量仍绑定 `60.0`s."""
    env = os.environ
    upload_dir = Path(env["UPLOAD_DIR"]) if "UPLOAD_DIR" in env else settings.upload_dir
    max_upload_bytes = (
        int(env["MAX_UPLOAD_SIZE_MB"]) * 1024 * 1024
        if "MAX_UPLOAD_SIZE_MB" in env
        else settings.max_upload_bytes
    )
    yolo_weights_path = (
        Path(env["YOLO_WEIGHTS_PATH"]) if "YOLO_WEIGHTS_PATH" in env else settings.yolo_weights_path
    )
    yolo_inference_enabled = (
        _parse_bool(env["YOLO_INFERENCE_ENABLED"])
        if "YOLO_INFERENCE_ENABLED" in env
        else settings.yolo_inference_enabled
    )
    yolo_inference_mode = (
        env["YOLO_INFERENCE_MODE"].strip().lower()
        if "YOLO_INFERENCE_MODE" in env
        else settings.yolo_inference_mode
    )
    yolo_img_size = int(env["YOLO_IMG_SIZE"]) if "YOLO_IMG_SIZE" in env else settings.yolo_img_size
    yolo_conf_threshold = (
        float(env["YOLO_CONF_THRESHOLD"])
        if "YOLO_CONF_THRESHOLD" in env
        else settings.yolo_conf_threshold
    )
    yolo_iou_threshold = (
        float(env["YOLO_IOU_THRESHOLD"])
        if "YOLO_IOU_THRESHOLD" in env
        else settings.yolo_iou_threshold
    )
    yolo_device = env.get("YOLO_DEVICE", settings.yolo_device)
    yolo_fallback_model = (
        env["YOLO_FALLBACK_MODEL"].strip()
        if "YOLO_FALLBACK_MODEL" in env
        else settings.yolo_fallback_model
    )
    yolo_autoinstall = (
        _parse_bool(env["YOLO_AUTOINSTALL"])
        if "YOLO_AUTOINSTALL" in env
        else settings.yolo_autoinstall
    )
    return config.Settings(
        upload_dir=upload_dir,
        max_upload_bytes=max_upload_bytes,
        yolo_weights_path=yolo_weights_path,
        yolo_inference_enabled=yolo_inference_enabled,
        yolo_inference_mode=yolo_inference_mode,
        yolo_img_size=yolo_img_size,
        yolo_conf_threshold=yolo_conf_threshold,
        yolo_device=yolo_device,
        yolo_fallback_model=yolo_fallback_model,
        yolo_iou_threshold=yolo_iou_threshold,
        yolo_autoinstall=yolo_autoinstall,
        vision_timeout_seconds=config.VISION_TIMEOUT_SECONDS,
    )


def _resolve_settings(settings: Any | None = None) -> config.Settings:
    """优先使用传入配置；否则尝试读取 `tomori.settings`，失败时回退 `config.load_settings()`."""
    if settings is not None:
        return _coerce_settings(settings)

    fallback = config.load_settings()
    try:
        app_settings_mod: Any = __import__("tomori.settings", fromlist=["get_settings"])
        get_settings = getattr(app_settings_mod, "get_settings", None)
        app_cfg = get_settings() if callable(get_settings) else None
    except Exception:
        app_cfg = None

    if app_cfg is None:
        return fallback

    coerced = _coerce_settings(app_cfg, fallback=fallback)
    return _apply_env_overrides(coerced)


def _load_metrics_metadata(
    weights_path: Path | None,
    metrics_path: Path | None = None,
) -> dict[str, Any] | None:
    """读 `metrics.json`：`metrics_path` 优先，`weights_path` 同目录次查（无硬编码 run 目录）."""
    candidates: list[Path] = []
    if metrics_path is not None:
        candidates.append(metrics_path)
    if weights_path is not None:
        candidates.append(weights_path.with_name("metrics.json"))

    for path in candidates:
        if not path.is_file():
            continue
        try:
            raw_data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise _OnnxInvalidArgumentError(
                VISION_DISABLED,
                f"INVALID_ARGUMENT: 无法解析模型指标文件 metrics.json: {exc}",
                {
                    "analysis_stage": "CONFIG_CHECK",
                    "error_kind": "INVALID_ARGUMENT",
                },
            ) from exc
        if not isinstance(raw_data, dict):
            raise _OnnxInvalidArgumentError(
                VISION_DISABLED,
                "INVALID_ARGUMENT: metrics.json 顶层必须为 JSON 对象",
                {
                    "analysis_stage": "CONFIG_CHECK",
                    "error_kind": "INVALID_ARGUMENT",
                },
            )
        return raw_data
    return None


def _validate_metrics_names(names_obj: Any) -> None:
    expected = list(config.RAW_CLASSES)
    parsed: list[str] | None = None
    if isinstance(names_obj, list):
        parsed = [str(item) for item in names_obj]
    elif isinstance(names_obj, dict):
        try:
            ordered = sorted(names_obj.items(), key=lambda kv: int(kv[0]))
        except (TypeError, ValueError) as exc:
            raise _OnnxInvalidArgumentError(
                VISION_DISABLED,
                f"INVALID_ARGUMENT: metrics.json names 映射键非法: {names_obj!r}",
                {
                    "analysis_stage": "CONFIG_CHECK",
                    "error_kind": "INVALID_ARGUMENT",
                },
            ) from exc
        parsed = [str(val) for _, val in ordered]

    # `names` 必须等于 `RAW_CLASSES` 前缀子集（`trainer.py` `RAW_CLASSES[:nc]` 同义）——
    # `nc=3`/`4`/`2`/`1` 均合法；跳类/`len>4`/非法名/空 `names` 拒绝（`occluded` 弃用 `nc=3` 兼容）
    if parsed is not None and (
        not parsed or len(parsed) > len(expected) or parsed != expected[: len(parsed)]
    ):
        raise _OnnxInvalidArgumentError(
            VISION_DISABLED,
            f"INVALID_ARGUMENT: metrics.json 类别列表不匹配（须为 config.RAW_CLASSES 前缀子集）: "
            f"期望前缀 {expected}，实际 {parsed}",
            {
                "analysis_stage": "CONFIG_CHECK",
                "error_kind": "INVALID_ARGUMENT",
                "expected_names": expected,
                "actual_names": parsed,
            },
        )


def validate_onnx_session(
    session: Any,
    *,
    expected_imgsz: int = config.EXPECTED_YOLO_IMG_SIZE,
    max_det: int = config.ONNX_MAX_DET,
    weights_path: Path | None = None,
    metrics_path: Path | None = None,
) -> tuple[str, str]:
    """校验 ONNX Runtime `InferenceSession` 输入输出静态张量形状，返回 `(input_name, output_name)`.

    校验 `sess.get_inputs()[0].shape == [1, 3, expected_imgsz, expected_imgsz]` 与
    `sess.get_outputs()[0].shape == [1, max_det, 6]`，并核对 `metrics.json` 中的 `imgsz`；
    若不符则抛出带 `INVALID_ARGUMENT` 诊断的异常（同时兼容 `VisionError(503)` 与 `ValueError`）。
    """
    inputs = session.get_inputs()
    outputs = session.get_outputs()
    if not inputs or not outputs:
        raise _OnnxInvalidArgumentError(
            VISION_DISABLED,
            "INVALID_ARGUMENT: ONNX 模型缺少有效输入或输出节点",
            {"analysis_stage": "CONFIG_CHECK", "error_kind": "INVALID_ARGUMENT"},
        )

    input_node = inputs[0]
    output_node = outputs[0]
    in_shape = list(input_node.shape)
    out_shape = list(output_node.shape)
    expected_in = [1, 3, expected_imgsz, expected_imgsz]
    expected_out = [1, max_det, 6]

    if in_shape != expected_in or out_shape != expected_out:
        raise _OnnxInvalidArgumentError(
            VISION_DISABLED,
            (
                f"INVALID_ARGUMENT: ONNX 模型静态张量形状不匹配: "
                f"期望输入 {expected_in} / 输出 {expected_out}，"
                f"实际输入 {in_shape} / 输出 {out_shape}"
            ),
            {
                "analysis_stage": "CONFIG_CHECK",
                "error_kind": "INVALID_ARGUMENT",
                "expected_input_shape": expected_in,
                "actual_input_shape": in_shape,
                "expected_output_shape": expected_out,
                "actual_output_shape": out_shape,
            },
        )

    metadata = _load_metrics_metadata(weights_path, metrics_path=metrics_path)
    if metadata is not None:
        recorded_imgsz = metadata.get("imgsz")
        if recorded_imgsz is not None:
            if isinstance(recorded_imgsz, bool) or not isinstance(recorded_imgsz, int):
                raise _OnnxInvalidArgumentError(
                    VISION_DISABLED,
                    (
                        f"INVALID_ARGUMENT: metrics.json imgsz 类型非法: "
                        f"期望整数 {expected_imgsz}，实际 {recorded_imgsz!r}"
                    ),
                    {
                        "analysis_stage": "CONFIG_CHECK",
                        "error_kind": "INVALID_ARGUMENT",
                        "expected_imgsz": expected_imgsz,
                        "metrics_imgsz": str(recorded_imgsz),
                    },
                )
            if recorded_imgsz != expected_imgsz:
                raise _OnnxInvalidArgumentError(
                    VISION_DISABLED,
                    (
                        f"INVALID_ARGUMENT: metrics.json imgsz={recorded_imgsz} "
                        f"与配置 YOLO_IMG_SIZE={expected_imgsz} 不一致"
                    ),
                    {
                        "analysis_stage": "CONFIG_CHECK",
                        "error_kind": "INVALID_ARGUMENT",
                        "expected_imgsz": expected_imgsz,
                        "metrics_imgsz": recorded_imgsz,
                    },
                )
        if "names" in metadata and metadata["names"] is not None:
            _validate_metrics_names(metadata["names"])

    return str(input_node.name), str(output_node.name)


def _letterbox_and_normalize(
    bgr: Any,
    imgsz: int = config.EXPECTED_YOLO_IMG_SIZE,
) -> tuple[Any, float, float, float]:
    """等比缩放并填充灰色 `(114, 114, 114)` 至 `(imgsz, imgsz)`，返回 NCHW `float32` 张量与偏移."""
    cv2: Any = __import__("cv2")
    np: Any = __import__("numpy")

    h, w = int(bgr.shape[0]), int(bgr.shape[1])
    if h <= 0 or w <= 0:
        raise ValueError(f"输入图像尺寸非法: {w}x{h}")

    scale = min(imgsz / float(w), imgsz / float(h))
    new_w = max(1, min(imgsz, int(round(w * scale))))
    new_h = max(1, min(imgsz, int(round(h * scale))))
    if (w, h) != (new_w, new_h):
        interp = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
        resized = cv2.resize(bgr, (new_w, new_h), interpolation=interp)
    else:
        resized = bgr

    pad_w = (imgsz - new_w) // 2
    pad_h = (imgsz - new_h) // 2
    canvas = np.full((imgsz, imgsz, 3), 114, dtype=np.uint8)
    canvas[pad_h : pad_h + new_h, pad_w : pad_w + new_w] = resized

    rgb = cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB)
    chw = np.transpose(rgb.astype(np.float32) / 255.0, (2, 0, 1))
    blob = np.ascontiguousarray(np.expand_dims(chw, axis=0), dtype=np.float32)
    return blob, scale, float(pad_w), float(pad_h)


_letterbox_bgr = _letterbox_and_normalize


def _as_list(value: Any) -> list[Any]:
    if hasattr(value, "detach"):
        value = value.detach()
    if hasattr(value, "cpu"):
        value = value.cpu()
    if hasattr(value, "tolist"):
        result: list[Any] = value.tolist()
        return result
    return list(value)


def _is_module_available(module_name: str) -> bool:
    return module_name in sys.modules or importlib.util.find_spec(module_name) is not None


def _render_annotated_image(
    bgr: Any,
    result: FaultCandidateResult,
    output_dir: Path,
) -> str | None:
    """在图像副本上绘制检测框、标签与底部半透明水印条，仅目录可写时落盘."""
    cv2: Any = __import__("cv2")

    canvas = bgr.copy()
    h, w = int(canvas.shape[0]), int(canvas.shape[1])

    for cand in result.candidates:
        x1, y1, x2, y2 = cand.bbox_px
        label_tag: str
        if cand.is_fault_candidate:
            color = (0, 69, 255) if cand.suggested_severity_hint in {"P1", "P2"} else (0, 180, 255)
            label_tag = cand.candidate_fault_type or cand.raw_class
        else:
            color = (60, 190, 90)
            label_tag = f"{cand.raw_class}:OK"

        cv2.rectangle(canvas, (x1, y1), (x2, y2), color, 2)
        caption = f"{label_tag} {cand.fused_confidence:.2f}"
        text_y = y1 - 6 if y1 >= 22 else min(h - 32, y1 + 18)
        cv2.putText(
            canvas,
            caption,
            (x1, text_y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            color,
            2,
            cv2.LINE_AA,
        )

    bar_h = max(28, min(46, h // 12))
    y_top = max(0, h - bar_h)
    overlay = canvas.copy()
    cv2.rectangle(overlay, (0, y_top), (w, h), (0, 0, 0), -1)
    cv2.addWeighted(overlay, 0.72, canvas, 0.28, 0.0, dst=canvas)

    watermark = "AI CANDIDATE - REQUIRES INSPECTOR CONFIRMATION"
    if result.ai_is_baseline_mode:
        watermark = f"{watermark} [BASELINE]"
    font_scale = max(0.35, min(0.60, w / 1100.0))
    cv2.putText(
        canvas,
        watermark,
        (10, h - max(8, bar_h // 3)),
        cv2.FONT_HERSHEY_SIMPLEX,
        font_scale,
        (240, 240, 240),
        1,
        cv2.LINE_AA,
    )

    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        out_path = output_dir / f"{result.analysis_id}_annotated.jpg"
        ok, encoded = cv2.imencode(".jpg", canvas)
        if not ok:
            return None
        out_path.write_bytes(encoded.tobytes())
        return str(out_path)
    except OSError:
        return None


class VisionClient:
    """进程内视觉分析客户端。"""

    def __init__(self, settings: config.Settings | Any | None = None) -> None:
        self.settings: config.Settings = _resolve_settings(settings)
        self._lock = threading.Lock()
        self._onnx_cache: dict[tuple[Any, ...], tuple[Any, str, str]] = {}
        self._pt_cache: dict[tuple[Any, ...], Any] = {}

    def _resolve_backend(
        self, *, require_custom: bool = False
    ) -> tuple[InferenceMode, Path | None]:
        if not self.settings.yolo_inference_enabled:
            raise make_degraded_error(
                VISION_DISABLED,
                "视觉推理已禁用（YOLO_INFERENCE_ENABLED=false）",
                analysis_stage="CONFIG_CHECK",
            )

        mode = self.settings.yolo_inference_mode.strip().lower()
        if mode not in _VALID_INFERENCE_MODES:
            raise make_degraded_error(
                VISION_DISABLED,
                f"无效的视觉推理模式配置: {self.settings.yolo_inference_mode}",
                analysis_stage="CONFIG_CHECK",
                details={"inference_mode": self.settings.yolo_inference_mode},
            )

        weights_path = self.settings.yolo_weights_path
        if weights_path.suffix.lower() == ".onnx" and weights_path.is_file():
            return "yolo26_onnx_e2e", weights_path

        sibling_onnx = weights_path.with_name("best_e2e.onnx")
        if sibling_onnx.is_file():
            return "yolo26_onnx_e2e", sibling_onnx

        if weights_path.suffix.lower() == ".pt" and weights_path.is_file():
            return "yolo26_pt", weights_path

        if require_custom or mode == "strict_custom":
            raise make_degraded_error(
                VISION_DISABLED,
                "自定义视觉权重不可用，已拒绝静默降级至基线模式",
                analysis_stage="CONFIG_CHECK",
                details={
                    "inference_mode": mode,
                    "require_custom": require_custom,
                },
            )
        return "baseline_pretrained_opencv", None

    def _select_onnx_providers(self, ort: Any) -> list[str]:
        device = self.settings.yolo_device.strip().lower()
        if device == "cpu":
            return ["CPUExecutionProvider"]
        get_providers = getattr(ort, "get_available_providers", None)
        available = list(get_providers()) if callable(get_providers) else ["CPUExecutionProvider"]
        preferred = [
            provider
            for provider in ("CUDAExecutionProvider", "CPUExecutionProvider")
            if provider in available
        ]
        return preferred or available or ["CPUExecutionProvider"]

    def _get_validated_onnx_session(self, onnx_path: Path) -> tuple[Any, str, str]:
        try:
            ort: Any = __import__("onnxruntime")
            stat = onnx_path.stat()
            providers = tuple(self._select_onnx_providers(ort))
            cache_key = (
                str(onnx_path),
                stat.st_mtime_ns,
                stat.st_size,
                self.settings.yolo_img_size,
                providers,
                id(ort.InferenceSession),
            )
            with self._lock:
                cached = self._onnx_cache.get(cache_key)
                if cached is not None:
                    return cached

            sess = ort.InferenceSession(str(onnx_path), providers=list(providers))
            input_name, output_name = validate_onnx_session(
                sess,
                expected_imgsz=self.settings.yolo_img_size,
                max_det=config.ONNX_MAX_DET,
                weights_path=onnx_path,
            )
            entry = (sess, input_name, output_name)
            with self._lock:
                self._onnx_cache[cache_key] = entry
            return entry
        except VisionError:
            raise
        except Exception as exc:
            raise make_degraded_error(
                VISION_DISABLED,
                f"INVALID_ARGUMENT: ONNX 模型加载或启动期校验失败: {exc}",
                analysis_stage="CONFIG_CHECK",
                details={"error_kind": "INVALID_ARGUMENT"},
            ) from exc

    def _get_pt_model(self, pt_path: Path) -> Any:
        if not self.settings.yolo_autoinstall:
            os.environ["YOLO_AUTOINSTALL"] = "False"
        ultralytics_mod: Any = __import__("ultralytics")
        yolo_cls = ultralytics_mod.YOLO
        stat = pt_path.stat()
        cache_key = (str(pt_path), stat.st_mtime_ns, stat.st_size, id(yolo_cls))
        with self._lock:
            cached = self._pt_cache.get(cache_key)
            if cached is not None:
                return cached
        model = yolo_cls(str(pt_path))
        with self._lock:
            self._pt_cache[cache_key] = model
        return model

    def check_readiness(
        self,
        *,
        require_custom: bool = False,
        raise_on_error: bool = False,
    ) -> ReadinessStatus:
        """检查本地推理配置与权重门禁状态；默认返回状态对象，可选在未就绪时抛 `503`."""
        try:
            mode, weight_file = self._resolve_backend(require_custom=require_custom)
            if mode == "yolo26_onnx_e2e" and weight_file is not None:
                self._get_validated_onnx_session(weight_file)
            elif (
                mode == "yolo26_pt"
                and weight_file is not None
                and not _is_module_available("ultralytics")
            ):
                raise make_degraded_error(
                    VISION_DISABLED,
                    "PyTorch 权重存在但运行环境未安装 ultralytics（vision extra）",
                    analysis_stage="CONFIG_CHECK",
                )
            return ReadinessStatus(
                ready=True,
                mode=mode,
                weights_path=str(weight_file) if weight_file is not None else None,
                detail=f"视觉推理就绪（模式：{mode}）",
            )
        except VisionError as exc:
            if raise_on_error:
                raise
            return ReadinessStatus(
                ready=False,
                mode=None,
                weights_path=str(self.settings.yolo_weights_path),
                detail=str(exc),
            )
        except Exception as exc:
            err = make_degraded_error(
                VISION_DISABLED,
                f"视觉推理就绪检查失败: {exc}",
                analysis_stage="CONFIG_CHECK",
            )
            if raise_on_error:
                raise err from exc
            return ReadinessStatus(
                ready=False,
                mode=None,
                weights_path=str(self.settings.yolo_weights_path),
                detail=str(err),
            )

    def _run_onnx_infer(
        self,
        bgr: Any,
        onnx_session_bundle: tuple[Any, str, str],
    ) -> tuple[Any, float, float, float]:
        sess, input_name, output_name = onnx_session_bundle
        blob, scale, pad_w, pad_h = _letterbox_and_normalize(
            bgr,
            imgsz=self.settings.yolo_img_size,
        )
        try:
            raw_outputs = sess.run([output_name], {input_name: blob})
        except Exception as exc:
            if "INVALID_ARGUMENT" in str(exc) or type(exc).__name__ == "InvalidArgument":
                raise make_degraded_error(
                    VISION_INFERENCE_FAILED,
                    f"INVALID_ARGUMENT: ONNX Runtime 推理输入校验失败: {exc}",
                    analysis_stage="INFERENCE",
                    details={"error_kind": "INVALID_ARGUMENT"},
                ) from exc
            raise
        return raw_outputs, scale, pad_w, pad_h

    def _postprocess_onnx(
        self,
        bgr: Any,
        hsv: Any,
        raw_outputs: Any,
        scale: float,
        pad_w: float,
        pad_h: float,
        onnx_path: Path,
    ) -> tuple[list[RawDetection], str]:
        np: Any = __import__("numpy")
        from tomori.vision.baseline import RawDetection
        from tomori.vision.metrics import roi_metrics

        arr = np.asarray(raw_outputs[0], dtype=np.float32)
        if arr.ndim == 3 and arr.shape[0] == 1:
            preds = arr[0]
        elif arr.ndim == 2 and arr.shape[1] == 6:
            preds = arr
        else:
            raise ValueError(f"ONNX 输出张量维度异常: {tuple(arr.shape)}")

        img_h, img_w = int(bgr.shape[0]), int(bgr.shape[1])
        detections: list[RawDetection] = []
        for row in preds:
            rx1, ry1, rx2, ry2, conf_raw, cls_raw = (
                float(row[0]),
                float(row[1]),
                float(row[2]),
                float(row[3]),
                float(row[4]),
                float(row[5]),
            )
            if not all(math.isfinite(v) for v in (rx1, ry1, rx2, ry2, conf_raw, cls_raw)):
                continue
            if conf_raw < self.settings.yolo_conf_threshold:
                continue
            cls_id = int(cls_raw)
            if cls_id < 0 or cls_id >= len(config.RAW_CLASSES):
                continue

            ox1 = int(round((rx1 - pad_w) / scale))
            oy1 = int(round((ry1 - pad_h) / scale))
            ox2 = int(round((rx2 - pad_w) / scale))
            oy2 = int(round((ry2 - pad_h) / scale))

            x1 = max(0, min(ox1, img_w))
            y1 = max(0, min(oy1, img_h))
            x2 = max(0, min(ox2, img_w))
            y2 = max(0, min(oy2, img_h))
            if x2 <= x1 or y2 <= y1:
                continue

            box = (x1, y1, x2, y2)
            detections.append(
                RawDetection(
                    box=box,
                    raw_class=config.RAW_CLASSES[cls_id],
                    detector_confidence=conf_raw,
                    metrics=roi_metrics(bgr, hsv, box),
                )
            )
        return detections, f"yolo26s-onnx-{onnx_path.stem}"

    def _run_pt_infer(self, bgr: Any, pt_path: Path) -> Any:
        np: Any = __import__("numpy")
        if not isinstance(bgr, np.ndarray):
            raise TypeError("安全边界禁止向 YOLO.predict 传非内存 ndarray 输入")
        if not self.settings.yolo_autoinstall:
            os.environ["YOLO_AUTOINSTALL"] = "False"

        model = self._get_pt_model(pt_path)
        # 安全红线：绝不向 source= 传文件路径或 URL，仅传已解码的内存 BGR ndarray
        return model.predict(
            source=bgr,
            imgsz=self.settings.yolo_img_size,
            conf=self.settings.yolo_conf_threshold,
            iou=self.settings.yolo_iou_threshold,
            device=self.settings.yolo_device,
            verbose=False,
        )

    def _postprocess_pt(
        self,
        bgr: Any,
        hsv: Any,
        raw_results: Any,
        pt_path: Path,
    ) -> tuple[list[RawDetection], str]:
        from tomori.vision.baseline import RawDetection
        from tomori.vision.metrics import roi_metrics

        detections: list[RawDetection] = []
        img_h, img_w = int(bgr.shape[0]), int(bgr.shape[1])
        results_list = list(raw_results) if raw_results is not None else []
        if results_list:
            first_res = results_list[0]
            boxes = getattr(first_res, "boxes", None)
            if boxes is not None:
                xyxy_list = _as_list(boxes.xyxy)
                conf_list = _as_list(boxes.conf)
                cls_list = _as_list(boxes.cls)
                for coords, conf_val, cls_val in zip(xyxy_list, conf_list, cls_list, strict=True):
                    conf_f = float(conf_val)
                    cls_f = float(cls_val)
                    if not (math.isfinite(conf_f) and math.isfinite(cls_f)):
                        continue
                    if conf_f < self.settings.yolo_conf_threshold:
                        continue
                    cls_id = int(cls_f)
                    if cls_id < 0 or cls_id >= len(config.RAW_CLASSES):
                        continue
                    cx1, cy1, cx2, cy2 = (
                        float(coords[0]),
                        float(coords[1]),
                        float(coords[2]),
                        float(coords[3]),
                    )
                    if not all(math.isfinite(v) for v in (cx1, cy1, cx2, cy2)):
                        continue
                    x1 = max(0, min(int(round(cx1)), img_w))
                    y1 = max(0, min(int(round(cy1)), img_h))
                    x2 = max(0, min(int(round(cx2)), img_w))
                    y2 = max(0, min(int(round(cy2)), img_h))
                    if x2 <= x1 or y2 <= y1:
                        continue
                    box = (x1, y1, x2, y2)
                    detections.append(
                        RawDetection(
                            box=box,
                            raw_class=config.RAW_CLASSES[cls_id],
                            detector_confidence=conf_f,
                            metrics=roi_metrics(bgr, hsv, box),
                        )
                    )
        return detections, f"yolo26s-pt-{pt_path.stem}"

    def _run_pipeline_on_bgr(
        self,
        bgr: Any,
        *,
        original_image_url: str,
        scene_lighting_hint: str,
        save_annotated: bool,
        require_custom: bool = False,
        analysis_id: str | None = None,
    ) -> FaultCandidateResult:
        from tomori.vision import baseline, mapping, metrics

        mode, weight_file = self._resolve_backend(require_custom=require_custom)
        onnx_bundle: tuple[Any, str, str] | None = None
        if mode == "yolo26_onnx_e2e" and weight_file is not None:
            onnx_bundle = self._get_validated_onnx_session(weight_file)

        try:
            hsv = metrics.to_hsv(bgr)
            quality = metrics.assess_quality(bgr, hsv)
            if scene_lighting_hint in {"day", "night", "twilight"}:
                quality = metrics.ImageQuality(
                    quality_grade=quality.quality_grade,
                    laplacian_var=quality.laplacian_var,
                    brightness_mean_v=quality.brightness_mean_v,
                    overexposed_ratio=quality.overexposed_ratio,
                    underexposed_ratio=quality.underexposed_ratio,
                    scene_lighting=scene_lighting_hint,
                    warnings=quality.warnings,
                )
        except VisionError:
            raise
        except Exception as exc:
            raise make_degraded_error(
                VISION_INFERENCE_FAILED,
                f"图像质量评估阶段异常: {exc}",
                analysis_stage="DECODE",
            ) from exc

        timeout_s = float(self.settings.vision_timeout_seconds)

        def _infer() -> Any:
            try:
                if mode == "yolo26_onnx_e2e" and onnx_bundle is not None:
                    return self._run_onnx_infer(bgr, onnx_bundle)
                if mode == "yolo26_pt" and weight_file is not None:
                    return self._run_pt_infer(bgr, weight_file)
                return baseline.detect(bgr, quality)
            finally:
                _INFER_SLOT.release()

        if not _INFER_SLOT.acquire(blocking=False):
            raise make_degraded_error(
                VISION_TIMEOUT,
                "视觉推理繁忙，请稍后重试",
                analysis_stage="INFERENCE",
            )
        try:
            future = _INFER_POOL.submit(_infer)
        except BaseException:
            _INFER_SLOT.release()
            raise
        future.add_done_callback(lambda done: _INFER_SLOT.release() if done.cancelled() else None)
        try:
            raw_infer_out = future.result(timeout=timeout_s)
        except VisionError:
            raise
        except (concurrent.futures.TimeoutError, TimeoutError) as exc:
            # 仍在排队则可移除出池；已在运行的推理在共享池内至多占 1 个槽位（界内可控）
            future.cancel()
            raise make_degraded_error(
                VISION_TIMEOUT,
                f"视觉推理超过 {timeout_s:g} 秒上限",
                analysis_stage="INFERENCE",
                details={"timeout_seconds": timeout_s},
            ) from exc
        except Exception as exc:
            raise make_degraded_error(
                VISION_INFERENCE_FAILED,
                f"视觉推理执行异常: {exc}",
                analysis_stage="INFERENCE",
            ) from exc

        try:
            detections: list[RawDetection]
            model_version: str
            if mode == "yolo26_onnx_e2e" and weight_file is not None:
                raw_outputs, scale, pad_w, pad_h = raw_infer_out
                detections, model_version = self._postprocess_onnx(
                    bgr, hsv, raw_outputs, scale, pad_w, pad_h, weight_file
                )
            elif mode == "yolo26_pt" and weight_file is not None:
                detections, model_version = self._postprocess_pt(
                    bgr, hsv, raw_infer_out, weight_file
                )
            else:
                detections = list(raw_infer_out)
                model_version = "baseline-opencv-heuristic-v1"

            resolved_id = analysis_id if analysis_id is not None else str(uuid.uuid4())
            img_h, img_w = int(bgr.shape[0]), int(bgr.shape[1])
            draft_result = mapping.build_result(
                detections=detections,
                quality=quality,
                image_width=img_w,
                image_height=img_h,
                inference_mode=mode,
                model_version=model_version,
                original_image_url=original_image_url,
                annotated_image_url=None,
                analysis_id=resolved_id,
            )
            annotated_url = (
                _render_annotated_image(bgr, draft_result, self.settings.output_dir)
                if save_annotated
                else None
            )
            if annotated_url is None:
                return draft_result
            return draft_result.model_copy(update={"annotated_image_url": annotated_url})
        except VisionError as exc:
            if exc.http_status in {413, 415, 422} or exc.code in {
                VISION_DISABLED,
                VISION_TIMEOUT,
                VISION_INFERENCE_FAILED,
            }:
                raise
            raise make_degraded_error(
                VISION_INFERENCE_FAILED,
                f"视觉结果后处理异常: {exc}",
                analysis_stage="POSTPROCESS",
            ) from exc
        except Exception as exc:
            raise make_degraded_error(
                VISION_INFERENCE_FAILED,
                f"视觉结果后处理异常: {exc}",
                analysis_stage="POSTPROCESS",
            ) from exc

    def analyze_bytes(
        self,
        raw: bytes,
        filename: str | None = None,
        *,
        original_image_url: str | None = None,
        scene_lighting_hint: str = "auto",
        save_annotated: bool = True,
        require_custom: bool = False,
        analysis_id: str | None = None,
    ) -> FaultCandidateResult:
        """执行字节流白名单校验、配置门禁、OpenCV/EXIF 解码与视觉推理."""
        try:
            from tomori.vision import whitelist

            validated = whitelist.validate_bytes(
                raw,
                filename=filename,
                max_bytes=self.settings.max_upload_bytes,
            )
        except VisionError as exc:
            if exc.http_status in {413, 415, 422} or exc.code in {
                VISION_DISABLED,
                VISION_TIMEOUT,
                VISION_INFERENCE_FAILED,
            }:
                raise
            raise make_degraded_error(
                VISION_INFERENCE_FAILED,
                f"白名单校验异常: {exc}",
                analysis_stage="DECODE",
            ) from exc
        except Exception as exc:
            raise make_degraded_error(
                VISION_INFERENCE_FAILED,
                f"白名单校验异常: {exc}",
                analysis_stage="DECODE",
            ) from exc

        _ = self._resolve_backend(require_custom=require_custom)

        try:
            from tomori.vision import image_io

            bgr = image_io.decode_bytes_to_bgr(raw)
        except VisionError as exc:
            if exc.http_status in {413, 415, 422} or exc.code in {
                VISION_DISABLED,
                VISION_TIMEOUT,
                VISION_INFERENCE_FAILED,
            }:
                raise
            raise make_degraded_error(
                VISION_INFERENCE_FAILED,
                f"图像解码阶段异常: {exc}",
                analysis_stage="DECODE",
            ) from exc
        except ImportError as exc:
            raise make_degraded_error(
                VISION_DISABLED,
                f"视觉运行时依赖未安装: {exc}",
                analysis_stage="CONFIG_CHECK",
            ) from exc
        except Exception as exc:
            raise make_degraded_error(
                VISION_INFERENCE_FAILED,
                f"图像解码阶段异常: {exc}",
                analysis_stage="DECODE",
            ) from exc

        return self._run_pipeline_on_bgr(
            bgr,
            original_image_url=original_image_url or validated.source_name,
            scene_lighting_hint=scene_lighting_hint,
            save_annotated=save_annotated,
            require_custom=require_custom,
            analysis_id=analysis_id,
        )

    def analyze_file(
        self,
        path: Path | str,
        *,
        original_image_url: str | None = None,
        scene_lighting_hint: str = "auto",
        save_annotated: bool = True,
        require_custom: bool = False,
        analysis_id: str | None = None,
    ) -> FaultCandidateResult:
        """执行本地文件四重白名单校验、配置门禁、EXIF 解码与视觉推理."""
        file_path = Path(path)

        try:
            from tomori.vision import whitelist

            validated = whitelist.validate_file(
                file_path,
                max_bytes=self.settings.max_upload_bytes,
            )
        except VisionError as exc:
            if exc.http_status in {413, 415, 422} or exc.code in {
                VISION_DISABLED,
                VISION_TIMEOUT,
                VISION_INFERENCE_FAILED,
            }:
                raise
            raise make_degraded_error(
                VISION_INFERENCE_FAILED,
                f"文件白名单校验异常: {exc}",
                analysis_stage="DECODE",
            ) from exc
        except Exception as exc:
            raise make_degraded_error(
                VISION_INFERENCE_FAILED,
                f"文件白名单校验异常: {exc}",
                analysis_stage="DECODE",
            ) from exc

        _ = self._resolve_backend(require_custom=require_custom)

        try:
            from tomori.vision import image_io

            bgr = image_io.decode_to_bgr(file_path)
        except VisionError as exc:
            if exc.http_status in {413, 415, 422} or exc.code in {
                VISION_DISABLED,
                VISION_TIMEOUT,
                VISION_INFERENCE_FAILED,
            }:
                raise
            raise make_degraded_error(
                VISION_INFERENCE_FAILED,
                f"图像解码阶段异常: {exc}",
                analysis_stage="DECODE",
            ) from exc
        except ImportError as exc:
            raise make_degraded_error(
                VISION_DISABLED,
                f"视觉运行时依赖未安装: {exc}",
                analysis_stage="CONFIG_CHECK",
            ) from exc
        except Exception as exc:
            raise make_degraded_error(
                VISION_INFERENCE_FAILED,
                f"图像解码阶段异常: {exc}",
                analysis_stage="DECODE",
            ) from exc

        return self._run_pipeline_on_bgr(
            bgr,
            original_image_url=original_image_url or f"file://{validated.source_name}",
            scene_lighting_hint=scene_lighting_hint,
            save_annotated=save_annotated,
            require_custom=require_custom,
            analysis_id=analysis_id,
        )


def analyze_image(
    path: Path | str,
    settings: config.Settings | Any | None = None,
    *,
    original_image_url: str | None = None,
    scene_lighting_hint: str = "auto",
    save_annotated: bool = True,
    require_custom: bool = False,
    analysis_id: str | None = None,
) -> FaultCandidateResult:
    """进程内便捷入口：分析单张静态巡查图像文件."""
    return VisionClient(settings=settings).analyze_file(
        path,
        original_image_url=original_image_url,
        scene_lighting_hint=scene_lighting_hint,
        save_annotated=save_annotated,
        require_custom=require_custom,
        analysis_id=analysis_id,
    )


def analyze_bytes(
    raw: bytes,
    filename: str | None = None,
    *,
    settings: config.Settings | Any | None = None,
    original_image_url: str | None = None,
    scene_lighting_hint: str = "auto",
    save_annotated: bool = True,
    require_custom: bool = False,
    analysis_id: str | None = None,
) -> FaultCandidateResult:
    """进程内便捷入口：分析静态巡查图像内存字节流."""
    return VisionClient(settings=settings).analyze_bytes(
        raw,
        filename=filename,
        original_image_url=original_image_url,
        scene_lighting_hint=scene_lighting_hint,
        save_annotated=save_annotated,
        require_custom=require_custom,
        analysis_id=analysis_id,
    )
