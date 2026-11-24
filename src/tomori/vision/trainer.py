"""YOLO26s 远端 GPU 训练、双头离线评估与端到端 ONNX 导出流水线。

硬约束（对齐 `docs/DESIGN.md` §5.3、`docs/RUNBOOK.md` §6.4–§6.5 与 `D-06` / `D-28`）：
1. **唯一锁定底模**：`yolo26s.pt`（严禁 `n`/`m`/`l`/`x`）；
2. **唯一锁定分辨率**：`imgsz=1280`（`2×640`，对 D1 `81.86%` 的 `1280×720` 原图实现 `1.0×`
   零下采样，保留 `100%` vs `64%` 像素并将 `<16px` 小目标比例从 `14.72%` 降至 `5.81%`）；
3. **训练周期与早停**：默认 `epochs=500`、`patience=20`（`EarlyStopping` 监控纯 `mAP50-95`）；
   注意若在远早于 `500` 轮处早停，线性 `lrf=0.01` 仍留 `70%–92%` LR、`close_mosaic=10`（需第 491
   轮触发）与 `E2ELoss` 渐进转移不会走满，可在 `--mosaic-polish-epochs` 显式追加无 Mosaic 短微调。
4. **增强禁区**：`flipud=0.0`、`hsv_v <= 0.25`、`hsv_s <= 0.4`（防昼夜洗白引入标签噪声）；
5. **AutoBatch 显存探测**：`batch=-1` 时通过 `ultralytics.utils.autobatch.check_train_batch_size`
   按约 60% 显存上限探测，并向下取 2 的幂锁死传给 `model.train`；
6. **ONNX 导出与形状门禁**：`export(format="onnx", imgsz=1280, nms=False, dynamic=False,
   simplify=True)` 使用 Ultralytics 默认 opset（不写 `opset=17`），复制为 `best_e2e.onnx`，
   经 `onnxruntime.InferenceSession` 校验 `[1, 3, 1280, 1280] -> [1, 300, 6]` 后输出
   `metrics.json` 与 `SHA256SUMS`。
7. **禁止运行期自动装包 & D1 延迟导入**：`YOLO_AUTOINSTALL=False`，顶层仅导入标准库与轻量内模块。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tomori.vision import config


@dataclass(frozen=True)
class TrainConfig:
    """远端 `yolo26s.pt @ 1280` 训练与导出配置（构造函数强制守卫项目红线）。"""

    data_yaml: Path
    base_model: Path = Path("/root/autodl-tmp/tomori/weights/yolo26s.pt")
    project_dir: Path = Path("/root/autodl-tmp/tomori/runs")
    run_name: str = "streetlight_v1"
    model_version: str = "yolo26s-streetlight-v1.0"
    imgsz: int = config.EXPECTED_YOLO_IMG_SIZE
    epochs: int = 500
    patience: int = 20
    batch: int = -1  # -1 = 官方 AutoBatch（约 60% 显存上限）探测并向下取 2 的幂锁死
    fallback_batch: int = 16
    device: str = "0"
    amp: bool = True
    optimizer: str = "auto"
    lr0: float = 0.01
    lrf: float = 0.01
    cos_lr: bool = False
    close_mosaic: int = 10
    mosaic_polish_epochs: int = 0
    mosaic: float = 0.8
    mixup: float = 0.05
    copy_paste: float = 0.15
    flipud: float = 0.0
    fliplr: float = 0.5
    degrees: float = 5.0
    hsv_v: float = 0.25
    hsv_s: float = 0.4
    seed: int = 42
    deterministic: bool = True
    workers: int = 8
    exist_ok: bool = True
    dry_run: bool = False
    export_only: bool = False
    weights_override: Path | None = None

    def __post_init__(self) -> None:
        if not self.export_only and self.base_model.name != config.FALLBACK_MODEL_NAME:
            raise ValueError(
                f"D-06 硬约束：唯一允许底模为 {config.FALLBACK_MODEL_NAME}，"
                f"禁止使用 {self.base_model.name}"
            )
        if self.imgsz != config.EXPECTED_YOLO_IMG_SIZE:
            raise ValueError(
                f"D-28 硬约束：唯一允许分辨率 imgsz={config.EXPECTED_YOLO_IMG_SIZE}，"
                f"禁止使用 {self.imgsz}"
            )
        if self.flipud != 0.0:
            raise ValueError(f"增强禁区：路灯训练必须保持 flipud=0.0，收到 {self.flipud}")
        if not (0.0 <= self.hsv_v <= 0.25):
            raise ValueError(f"增强禁区：hsv_v 必须 <= 0.25（防昼夜洗白），收到 {self.hsv_v}")
        if not (0.0 <= self.hsv_s <= 0.4):
            raise ValueError(f"增强禁区：hsv_s 必须 <= 0.4（防昼夜洗白），收到 {self.hsv_s}")
        if self.batch != -1 and self.batch < 1:
            raise ValueError(f"batch 必须为 -1（AutoBatch）或 >= 1，收到 {self.batch}")
        if self.fallback_batch < 1:
            raise ValueError(f"fallback_batch 必须 >= 1，收到 {self.fallback_batch}")
        if self.epochs < 1:
            raise ValueError(f"epochs 必须 >= 1，收到 {self.epochs}")
        if self.patience < 0:
            raise ValueError(f"patience 必须 >= 0，收到 {self.patience}")
        if not (0.0 < self.lrf <= 1.0):
            raise ValueError(f"lrf 必须在 (0, 1]，收到 {self.lrf}")
        if self.mosaic_polish_epochs < 0:
            raise ValueError(f"mosaic_polish_epochs 必须 >= 0，收到 {self.mosaic_polish_epochs}")


@dataclass(frozen=True)
class TrainArtifacts:
    """训练、评估、ONNX 导出与完整性文件产物路径。"""

    run_dir: Path
    best_pt: Path
    best_e2e_onnx: Path
    metrics_json: Path
    sha256sums: Path
    resolved_batch: int
    probed_batch: int | None
    val_map50: float | None
    val_map50_95: float | None
    dry_run: bool = False


def floor_power_of_two(value: int, *, min_batch: int = 1, max_batch: int = 64) -> int:
    """将 AutoBatch 探测值向下取 2 的幂并限制在安全范围，保证显存余量与可复现性。"""
    clamped = max(min_batch, min(max_batch, int(value)))
    return 1 << (clamped.bit_length() - 1)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_yaml_dict(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"data.yaml 不存在: {path}")
    yaml: Any = __import__("yaml")
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError(f"data.yaml 顶层必须为字典: {path}")
    return loaded


def validate_data_yaml(data_yaml: Path) -> tuple[dict[str, Any], int]:
    """核验 `data.yaml` 类别数量与顺序严格对齐 `config.RAW_CLASSES`，并统计训练集图像数。"""
    parsed = _load_yaml_dict(data_yaml)
    names_raw = parsed.get("names")
    expected = list(config.RAW_CLASSES)
    if isinstance(names_raw, list):
        actual = [str(x) for x in names_raw]
    elif isinstance(names_raw, dict):
        actual = [str(v) for _, v in sorted(names_raw.items(), key=lambda kv: int(kv[0]))]
    else:
        raise ValueError(f"data.yaml 缺少合法 names 字段: {data_yaml}")

    if actual != expected:
        raise ValueError(
            f"data.yaml names 与 config.RAW_CLASSES 不一致: 期望 {expected}，实际 {actual}"
        )

    nc_val = parsed.get("nc")
    if nc_val is not None and int(nc_val) != len(expected):
        raise ValueError(f"data.yaml nc={nc_val} 与期望 {len(expected)} 不一致")

    train_count = 0
    root_str = parsed.get("path")
    train_rel = parsed.get("train")
    if isinstance(train_rel, str):
        base_dir = Path(root_str) if isinstance(root_str, str) else data_yaml.parent
        train_dir = base_dir / train_rel
        if train_dir.is_dir():
            train_count = sum(
                1
                for p in train_dir.rglob("*")
                if p.is_file() and p.suffix.lower() in config.ALLOWED_EXTENSIONS
            )
    return parsed, train_count


def _resolve_batch_size(cfg: TrainConfig, dataset_size: int) -> tuple[int | None, int]:
    """`batch=-1` 时调官方 `check_train_batch_size`（60% 显存）探测并向下取 2 的幂锁死。"""
    if cfg.batch > 0:
        return None, cfg.batch

    os.environ["YOLO_AUTOINSTALL"] = "False"
    torch: Any = __import__("torch")
    ultralytics_mod: Any = __import__("ultralytics")
    autobatch_mod: Any = __import__(
        "ultralytics.utils.autobatch",
        fromlist=["check_train_batch_size"],
    )

    if cfg.device.strip().lower() == "cpu" or not torch.cuda.is_available():
        return None, cfg.fallback_batch

    device_idx = cfg.device.split(",")[0].strip()
    torch_device = torch.device(f"cuda:{device_idx}" if device_idx.isdigit() else device_idx)
    torch.backends.cudnn.benchmark = False

    yolo_probe = ultralytics_mod.YOLO(str(cfg.base_model))
    try:
        yolo_probe.model.to(torch_device)
        probed = int(
            autobatch_mod.check_train_batch_size(
                yolo_probe.model,
                imgsz=cfg.imgsz,
                amp=cfg.amp,
                batch=-1,
                dataset_size=max(0, dataset_size),
            )
        )
        locked = floor_power_of_two(probed, min_batch=1, max_batch=64)
        return probed, locked
    finally:
        del yolo_probe
        if torch.cuda.is_available():
            torch.cuda.empty_cache()


def extract_det_metrics(det_metrics: Any, class_names: Sequence[str]) -> dict[str, Any]:
    """按 `ultralytics==8.4.216` `DetMetrics` / `Metric` 精确字段抽取总体与逐类指标。"""
    if det_metrics is None:
        return {}
    box = getattr(det_metrics, "box", None)
    if box is None:
        return {}

    per_class: dict[str, dict[str, Any]] = {
        cname: {
            "class_id": cid,
            "present_in_split": False,
            "precision": 0.0,
            "recall": 0.0,
            "f1": 0.0,
            "ap50": 0.0,
            "ap50_95": 0.0,
        }
        for cid, cname in enumerate(class_names)
    }

    ap_indices = [int(x) for x in list(getattr(box, "ap_class_index", []))]
    p_arr = list(getattr(box, "p", []))
    r_arr = list(getattr(box, "r", []))
    f1_arr = list(getattr(box, "f1", []))
    ap50_arr = list(getattr(box, "ap50", []))
    ap_arr = list(getattr(box, "ap", []))

    for pos, cls_id in enumerate(ap_indices):
        if 0 <= cls_id < len(class_names):
            cname = class_names[cls_id]
            per_class[cname] = {
                "class_id": cls_id,
                "present_in_split": True,
                "precision": round(float(p_arr[pos]), 5) if pos < len(p_arr) else 0.0,
                "recall": round(float(r_arr[pos]), 5) if pos < len(r_arr) else 0.0,
                "f1": round(float(f1_arr[pos]), 5) if pos < len(f1_arr) else 0.0,
                "ap50": round(float(ap50_arr[pos]), 5) if pos < len(ap50_arr) else 0.0,
                "ap50_95": round(float(ap_arr[pos]), 5) if pos < len(ap_arr) else 0.0,
            }

    speed_raw = getattr(det_metrics, "speed", {}) or {}
    speed_ms = {str(k): round(float(v), 3) for k, v in speed_raw.items()}
    fitness_fn = getattr(box, "fitness", None)
    fitness_val = (
        float(fitness_fn()) if callable(fitness_fn) else float(getattr(det_metrics, "fitness", 0.0))
    )

    return {
        "mAP50": round(float(box.map50), 5),
        "mAP50_95": round(float(box.map), 5),
        "mAP75": round(float(box.map75), 5),
        "mean_precision": round(float(box.mp), 5),
        "mean_recall": round(float(box.mr), 5),
        "fitness": round(fitness_val, 5),
        "speed_ms": speed_ms,
        "per_class": per_class,
    }


def _verify_exported_onnx(onnx_path: Path, expected_imgsz: int) -> dict[str, Any]:
    """用 `onnxruntime.InferenceSession` 核对 `[1, 3, imgsz, imgsz] -> [1, 300, 6]` 与元数据。"""
    from tomori.vision.client import validate_onnx_session

    ort: Any = __import__("onnxruntime")
    sess = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
    in_name, out_name = validate_onnx_session(
        sess,
        expected_imgsz=expected_imgsz,
        max_det=config.ONNX_MAX_DET,
    )
    meta_map = dict(sess.get_modelmeta().custom_metadata_map)
    return {
        "input_name": in_name,
        "output_name": out_name,
        "input_shape": list(sess.get_inputs()[0].shape),
        "output_shape": list(sess.get_outputs()[0].shape),
        "custom_metadata_map": meta_map,
    }


def _write_sha256sums(run_dir: Path, rel_targets: Sequence[str]) -> Path:
    lines: list[str] = []
    for rel in rel_targets:
        target = run_dir / rel
        if target.is_file():
            lines.append(f"{_sha256_file(target)}  {rel}")
    sha_path = run_dir / "SHA256SUMS"
    sha_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return sha_path


def _schedule_diagnostics(
    *,
    epochs: int,
    stopped_epoch_1based: int | None,
    lrf: float,
    cos_lr: bool,
    close_mosaic: int,
) -> dict[str, Any]:
    """依据 `ultralytics==8.4.216` 源码公式计算早停时的 LR/Mosaic/E2ELoss 真实状态。"""
    if stopped_epoch_1based is None:
        return {}
    x = max(0, stopped_epoch_1based - 1)
    if cos_lr:
        lr_ratio = ((1.0 - math.cos(x * math.pi / max(1, epochs))) / 2.0) * (lrf - 1.0) + 1.0
    else:
        lr_ratio = max(1.0 - x / max(1, epochs), 0.0) * (1.0 - lrf) + lrf
    close_trigger_epoch = (epochs - close_mosaic + 1) if close_mosaic > 0 else None
    mosaic_closed = bool(close_mosaic > 0 and x >= (epochs - close_mosaic))
    o2m_weight = max(1.0 - x / max(epochs - 1, 1), 0.0) * (0.8 - 0.1) + 0.1
    return {
        "stopped_epoch": stopped_epoch_1based,
        "early_stopped": stopped_epoch_1based < epochs,
        "lr_remaining_ratio": round(lr_ratio, 5),
        "close_mosaic_trigger_epoch": close_trigger_epoch,
        "close_mosaic_triggered": mosaic_closed,
        "e2e_loss_o2m_weight": round(o2m_weight, 4),
        "e2e_loss_o2o_weight": round(1.0 - o2m_weight, 4),
    }


def run_training_pipeline(cfg: TrainConfig) -> TrainArtifacts:
    """执行（或 `--dry-run` 预演）YOLO26s@1280 训练、val/test 评估、ONNX 导出与清单落盘."""
    os.environ["YOLO_AUTOINSTALL"] = "False"
    data_dict, train_count = validate_data_yaml(cfg.data_yaml)

    if cfg.dry_run:
        run_dir = cfg.project_dir / cfg.run_name
        weights_dir = run_dir / "weights"
        resolved_batch = cfg.batch if cfg.batch > 0 else cfg.fallback_batch
        return TrainArtifacts(
            run_dir=run_dir,
            best_pt=weights_dir / "best.pt",
            best_e2e_onnx=weights_dir / "best_e2e.onnx",
            metrics_json=run_dir / "metrics.json",
            sha256sums=run_dir / "SHA256SUMS",
            resolved_batch=resolved_batch,
            probed_batch=None,
            val_map50=None,
            val_map50_95=None,
            dry_run=True,
        )

    ultralytics_mod: Any = __import__("ultralytics")
    yolo_cls = ultralytics_mod.YOLO

    probed_batch: int | None = None
    if cfg.export_only:
        if cfg.weights_override is None or not cfg.weights_override.is_file():
            raise FileNotFoundError(
                f"--export-only 需要有效的 --weights 文件: {cfg.weights_override}"
            )
        best_pt = cfg.weights_override.resolve()
        run_dir = best_pt.parent.parent if best_pt.parent.name == "weights" else best_pt.parent
        weights_dir = best_pt.parent
        last_pt = weights_dir / "last.pt"
        resolved_batch = cfg.batch if cfg.batch > 0 else cfg.fallback_batch
    else:
        if not cfg.base_model.is_file():
            raise FileNotFoundError(f"底模文件不存在: {cfg.base_model}")
        probed_batch, resolved_batch = _resolve_batch_size(cfg, train_count)
        model = yolo_cls(str(cfg.base_model))
        model.train(
            data=str(cfg.data_yaml.resolve()),
            project=str(cfg.project_dir.resolve()),
            name=cfg.run_name,
            epochs=cfg.epochs,
            patience=cfg.patience,
            imgsz=cfg.imgsz,
            batch=resolved_batch,
            device=cfg.device,
            amp=cfg.amp,
            optimizer=cfg.optimizer,
            lr0=cfg.lr0,
            lrf=cfg.lrf,
            cos_lr=cfg.cos_lr,
            close_mosaic=cfg.close_mosaic,
            mosaic=cfg.mosaic,
            mixup=cfg.mixup,
            copy_paste=cfg.copy_paste,
            flipud=cfg.flipud,
            fliplr=cfg.fliplr,
            degrees=cfg.degrees,
            hsv_v=cfg.hsv_v,
            hsv_s=cfg.hsv_s,
            seed=cfg.seed,
            deterministic=cfg.deterministic,
            workers=cfg.workers,
            exist_ok=cfg.exist_ok,
            # 早停判据头 == 部署头：`DetectionTrainer.get_validator()` 以 `copy(self.args)` 构造
            # validator，而 `BaseValidator.__call__` 内 `model.end2end = self.args.nms is False`
            # （`engine/validator.py:23`）决定 `self.validate()` 走 `one2one` 还是 `one2many`。
            # 故必须在 train 传 `nms=False`，否则 EarlyStopping 监控的是 `one2many`+外接 NMS，
            # 与 `export(..., nms=False)` 交付的 `one2one` 端到端头错位。
            nms=False,
            plots=True,
            val=True,
        )
        trainer_obj = getattr(model, "trainer", None)
        run_dir = (
            Path(trainer_obj.save_dir)
            if trainer_obj is not None and getattr(trainer_obj, "save_dir", None) is not None
            else (cfg.project_dir / cfg.run_name)
        )
        weights_dir = run_dir / "weights"
        best_pt = (
            Path(trainer_obj.best)
            if trainer_obj is not None and getattr(trainer_obj, "best", None) is not None
            else (weights_dir / "best.pt")
        )
        last_pt = (
            Path(trainer_obj.last)
            if trainer_obj is not None and getattr(trainer_obj, "last", None) is not None
            else (weights_dir / "last.pt")
        )
        if not best_pt.is_file():
            raise FileNotFoundError(f"训练结束但未生成 best.pt: {best_pt}")
        stopped_epoch = getattr(trainer_obj, "epoch", None)
        stopped_epoch_1based = int(stopped_epoch) + 1 if stopped_epoch is not None else None
        # 若显式开启两阶段精修且因早停未触发 close_mosaic，从 best.pt 追加无 Mosaic 微调
        if (
            cfg.mosaic_polish_epochs > 0
            and stopped_epoch_1based is not None
            and cfg.close_mosaic > 0
            and (stopped_epoch_1based - 1) < (cfg.epochs - cfg.close_mosaic)
        ):
            polish_model = yolo_cls(str(best_pt))
            polish_model.train(
                data=str(cfg.data_yaml.resolve()),
                project=str(cfg.project_dir.resolve()),
                name=f"{cfg.run_name}_polish",
                epochs=cfg.mosaic_polish_epochs,
                patience=0,
                imgsz=cfg.imgsz,
                batch=resolved_batch,
                device=cfg.device,
                amp=cfg.amp,
                optimizer=cfg.optimizer,
                lr0=cfg.lr0 * 0.2,
                lrf=0.05,
                cos_lr=True,
                mosaic=0.0,
                mixup=0.0,
                copy_paste=0.0,
                flipud=cfg.flipud,
                fliplr=cfg.fliplr,
                degrees=cfg.degrees,
                hsv_v=cfg.hsv_v,
                hsv_s=cfg.hsv_s,
                seed=cfg.seed,
                deterministic=cfg.deterministic,
                workers=cfg.workers,
                exist_ok=cfg.exist_ok,
                nms=False,
                plots=True,
                val=True,
            )
            p_trainer = getattr(polish_model, "trainer", None)
            p_best = (
                Path(p_trainer.best)
                if p_trainer is not None and getattr(p_trainer, "best", None) is not None
                else None
            )
            if p_best is not None and p_best.is_file():
                best_pt = p_best
    if cfg.export_only:
        stopped_epoch_1based = None

    best_model = yolo_cls(str(best_pt))
    class_names = list(config.RAW_CLASSES)

    # --- 部署头（one2one 端到端）即早停判据头 ---
    # `EarlyStopping`（`utils/torch_utils.py:1120-1169`）监控训练内 `self.validate()` 的
    # `mAP50-95`，而该指标由 `DetectionValidator` 按 `args.nms is False` 选 `one2one` 头计算。
    # 此处独立 `val` 用同一 `nms=False`，复现与部署产物 `best_e2e.onnx` 完全相同的前向路径。
    val_e2e_raw = best_model.val(
        data=str(cfg.data_yaml.resolve()),
        imgsz=cfg.imgsz,
        split="val",
        device=cfg.device,
        nms=False,
        plots=True,
    )
    val_metrics_e2e = extract_det_metrics(val_e2e_raw, class_names)

    # --- 对照头（one2many + 外接 NMS）仅供诊断，不参与早停选择、不参与部署 ---
    val_default_raw = best_model.val(
        data=str(cfg.data_yaml.resolve()),
        imgsz=cfg.imgsz,
        split="val",
        device=cfg.device,
        nms=None,
        plots=False,
    )
    val_metrics_default = extract_det_metrics(val_default_raw, class_names)

    test_summary_default: dict[str, Any] = {}
    test_summary_e2e: dict[str, Any] = {}
    if isinstance(data_dict.get("test"), str):
        test_default = best_model.val(
            data=str(cfg.data_yaml.resolve()),
            imgsz=cfg.imgsz,
            split="test",
            device=cfg.device,
            nms=None,
            plots=False,
        )
        test_summary_default = extract_det_metrics(test_default, class_names)
        test_e2e = best_model.val(
            data=str(cfg.data_yaml.resolve()),
            imgsz=cfg.imgsz,
            split="test",
            device=cfg.device,
            nms=False,
            plots=False,
        )
        test_summary_e2e = extract_det_metrics(test_e2e, class_names)

    # 导出端到端无 NMS ONNX（不硬写 opset=17，使用 Ultralytics 默认 opset）
    exported_str = best_model.export(
        format="onnx",
        imgsz=cfg.imgsz,
        nms=False,
        dynamic=False,
        simplify=True,
    )
    exported_onnx = Path(exported_str)
    if not exported_onnx.is_file():
        raise FileNotFoundError(f"ONNX 导出未生成文件: {exported_onnx}")

    best_e2e_onnx = weights_dir / "best_e2e.onnx"
    if exported_onnx.resolve() != best_e2e_onnx.resolve():
        shutil.copy2(exported_onnx, best_e2e_onnx)

    onnx_check = _verify_exported_onnx(best_e2e_onnx, expected_imgsz=cfg.imgsz)

    prep_manifest_path = cfg.data_yaml.parent / "prep_manifest.json"
    dataset_manifest: dict[str, Any] | None = None
    if prep_manifest_path.is_file():
        try:
            dataset_manifest = json.loads(prep_manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            dataset_manifest = None

    sha256_map: dict[str, str] = {
        "best.pt": _sha256_file(best_pt),
        "best_e2e.onnx": _sha256_file(best_e2e_onnx),
    }
    if last_pt.is_file():
        sha256_map["last.pt"] = _sha256_file(last_pt)

    metrics_payload: dict[str, Any] = {
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "model_version": cfg.model_version,
        "base_model": cfg.base_model.name,
        "ultralytics_version": str(getattr(ultralytics_mod, "__version__", "unknown")),
        "imgsz": cfg.imgsz,
        "nms": False,
        "end2end": True,
        # 双头契约：显式声明「早停判据头」「部署头」「对照头」，避免再出现头错位静默失配
        "head_contract": {
            "deploy_head": "one2one_end2e",
            "deploy_nms": False,
            "early_stopping_head": "one2one_end2e",
            "early_stopping_nms": False,
            "comparison_head": "one2many_external_nms",
            "comparison_nms": None,
            "deploy_artifact": "weights/best_e2e.onnx",
            "note": (
                "EarlyStopping 监控训练内 validate() 的 mAP50-95；训练已传 nms=False，"
                "故早停判据头与 best_e2e.onnx 部署头同为 one2one 端到端头。"
            ),
        },
        "names": {str(idx): cname for idx, cname in enumerate(class_names)},
        # 早停判据头 == 部署头（权威指标）
        "val_mAP50": val_metrics_e2e.get("mAP50"),
        "val_mAP50_95": val_metrics_e2e.get("mAP50_95"),
        "val_metrics_e2e_head": val_metrics_e2e,
        "val_metrics_default_head": val_metrics_default,
        "test_metrics_default_head": test_summary_default,
        "test_metrics_e2e_head": test_summary_e2e,
        "onnx_verification": onnx_check,
        "train_config": {
            "epochs": cfg.epochs,
            "patience": cfg.patience,
            "requested_batch": cfg.batch,
            "probed_batch": probed_batch,
            "resolved_batch": resolved_batch,
            "imgsz": cfg.imgsz,
            "device": cfg.device,
            "amp": cfg.amp,
            "optimizer": cfg.optimizer,
            "lr0": cfg.lr0,
            "lrf": cfg.lrf,
            "cos_lr": cfg.cos_lr,
            "nms": False,  # 早停判据头选择：False = one2one 端到端头（== 部署头）
            "flipud": cfg.flipud,
            "fliplr": cfg.fliplr,
            "hsv_v": cfg.hsv_v,
            "hsv_s": cfg.hsv_s,
            "degrees": cfg.degrees,
            "mosaic": cfg.mosaic,
            "mixup": cfg.mixup,
            "copy_paste": cfg.copy_paste,
            "close_mosaic": cfg.close_mosaic,
            "mosaic_polish_epochs": cfg.mosaic_polish_epochs,
            "fallback_batch": cfg.fallback_batch,
            "workers": cfg.workers,
            "exist_ok": cfg.exist_ok,
            "seed": cfg.seed,
            "deterministic": cfg.deterministic,
        },
        "early_stopping_diagnostics": _schedule_diagnostics(
            epochs=cfg.epochs,
            stopped_epoch_1based=stopped_epoch_1based,
            lrf=cfg.lrf,
            cos_lr=cfg.cos_lr,
            close_mosaic=cfg.close_mosaic,
        ),
        "sha256": sha256_map,
        "dataset_manifest": dataset_manifest,
    }

    metrics_json = run_dir / "metrics.json"
    metrics_json.write_text(
        json.dumps(metrics_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    sha_targets = [
        "weights/best.pt",
        "weights/best_e2e.onnx",
        "weights/last.pt",
        "results.csv",
        "args.yaml",
        "metrics.json",
    ]
    sha256sums = _write_sha256sums(run_dir, sha_targets)

    return TrainArtifacts(
        run_dir=run_dir,
        best_pt=best_pt,
        best_e2e_onnx=best_e2e_onnx,
        metrics_json=metrics_json,
        sha256sums=sha256sums,
        resolved_batch=resolved_batch,
        probed_batch=probed_batch,
        val_map50=val_metrics_e2e.get("mAP50"),
        val_map50_95=val_metrics_e2e.get("mAP50_95"),
        dry_run=False,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Tomori YOLO26s@1280 远端 GPU 训练、双头验证与端到端 ONNX 导出 CLI"
    )
    parser.add_argument("--data", type=Path, required=True, help="数据集 data.yaml 路径")
    parser.add_argument(
        "--model",
        type=Path,
        default=Path("/root/autodl-tmp/tomori/weights/yolo26s.pt"),
        help="唯一允许 yolo26s.pt",
    )
    parser.add_argument(
        "--project",
        type=Path,
        default=Path("/root/autodl-tmp/tomori/runs"),
    )
    parser.add_argument("--name", default="streetlight_v1")
    parser.add_argument("--model-version", default="yolo26s-streetlight-v1.0")
    parser.add_argument("--imgsz", type=int, default=config.EXPECTED_YOLO_IMG_SIZE)
    parser.add_argument("--epochs", type=int, default=500)
    parser.add_argument("--patience", type=int, default=20)
    parser.add_argument("--batch", type=int, default=-1, help="-1 为 AutoBatch 向下取 2 的幂")
    parser.add_argument("--fallback-batch", type=int, default=16)
    parser.add_argument("--device", default="0")
    parser.add_argument("--optimizer", default="auto")
    parser.add_argument("--lr0", type=float, default=0.01)
    parser.add_argument("--lrf", type=float, default=0.01)
    parser.add_argument("--cos-lr", action="store_true")
    parser.add_argument("--close-mosaic", type=int, default=10)
    parser.add_argument("--mosaic-polish-epochs", type=int, default=0)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--flipud", type=float, default=0.0)
    parser.add_argument("--hsv-v", type=float, default=0.25)
    parser.add_argument("--hsv-s", type=float, default=0.4)
    parser.add_argument("--degrees", type=float, default=5.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--no-amp", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--export-only", action="store_true")
    parser.add_argument(
        "--weights", type=Path, default=None, help="--export-only 时的 best.pt 路径"
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    cfg = TrainConfig(
        data_yaml=args.data,
        base_model=args.model,
        project_dir=args.project,
        run_name=args.name,
        model_version=args.model_version,
        imgsz=args.imgsz,
        epochs=args.epochs,
        patience=args.patience,
        batch=args.batch,
        fallback_batch=args.fallback_batch,
        device=args.device,
        amp=not args.no_amp,
        optimizer=args.optimizer,
        lr0=args.lr0,
        lrf=args.lrf,
        cos_lr=args.cos_lr,
        close_mosaic=args.close_mosaic,
        mosaic_polish_epochs=args.mosaic_polish_epochs,
        workers=args.workers,
        flipud=args.flipud,
        hsv_v=args.hsv_v,
        hsv_s=args.hsv_s,
        degrees=args.degrees,
        seed=args.seed,
        dry_run=args.dry_run,
        export_only=args.export_only,
        weights_override=args.weights,
    )
    artifacts = run_training_pipeline(cfg)
    payload = {k: (str(v) if isinstance(v, Path) else v) for k, v in asdict(artifacts).items()}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
