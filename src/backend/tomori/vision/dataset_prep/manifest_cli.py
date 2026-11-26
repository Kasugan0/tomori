"""`dataset_prep` 编排（`prepare_dataset`）、manifest 落盘与 CLI 入口."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from collections import Counter, defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tomori.vision import config
from tomori.vision.dataset_prep.core import (
    VIDEO_EXTENSIONS,
    DatasetPrepConfig,
    DatasetSourceSpec,
    PrepResult,
    SourceStats,
    _build_id_map,
    _clean_samples,
    _cluster_and_dedupe_phash,
    _dedupe_exact_and_rf,
    _discover_coco_index,
    _find_voc_xml_file,
    _find_yolo_label_file,
    _ImageSample,
    _inspect_image,
    _is_lfs_pointer,
    _load_metadata_video_map,
    _lookup_coco_entry,
    _out_names,
    _parse_coco_entry,
    _parse_roboflow_stem_and_split,
    _parse_voc_xml,
    _parse_yaml_or_text_names,
    _parse_yolo_labels,
    _sha256_file,
    compute_phash64,
)
from tomori.vision.dataset_prep.splits import _assign_group_splits


def _materialize_file(src: Path, dst: Path, link_mode: str) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() or dst.is_symlink():
        dst.unlink()
    if link_mode == "symlink":
        os.symlink(src.resolve(), dst)
    elif link_mode == "hardlink":
        try:
            os.link(src, dst)
        except OSError:
            shutil.copy2(src, dst)
    else:
        shutil.copy2(src, dst)


def _write_data_yaml(output_dir: Path, *, names: dict[int, str]) -> Path:
    yaml_path = output_dir / "data.yaml"
    lines = [
        f"path: {output_dir.resolve()}",
        "train: images/train",
        "val: images/val",
        "test: images/test",
        f"nc: {len(names)}",
        "names:",
    ]
    for cid, cname in names.items():
        lines.append(f"  {cid}: {cname}")
    yaml_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return yaml_path


def prepare_dataset(cfg: DatasetPrepConfig) -> PrepResult:
    """执行多源异构数据 ETL、归一化、去重合并、防泄漏分层切分与落盘."""
    all_samples: list[_ImageSample] = []
    stats_by_source: dict[str, SourceStats] = {}
    sample_seq = 0

    for spec in cfg.sources:
        root = spec.root_dir
        if not root.is_dir():
            raise FileNotFoundError(f"数据集目录不存在: {root}")
        st = SourceStats(
            name=spec.name, root_dir=str(root.resolve()), rf_cluster_mode=spec.rf_cluster_mode
        )
        stats_by_source[spec.name] = st

        source_names = _parse_yaml_or_text_names(root)
        id_map = _build_id_map(spec, source_names)
        video_map = _load_metadata_video_map(root)
        coco_idx = _discover_coco_index(root) if spec.format_hint in {"auto", "coco"} else None
        seen_videos: set[str] = set()
        seen_rf_stems: set[str] = set()
        detected_formats: set[str] = set()

        for file_path in sorted(root.rglob("*")):
            if not file_path.is_file():
                continue
            rel_parts = file_path.relative_to(root).parts
            if any(part.startswith(".") for part in rel_parts):
                continue

            ext = file_path.suffix.lower()
            if ext in VIDEO_EXTENSIONS:
                st.skipped_videos += 1
                if len(st.skipped_video_samples) < 20:
                    st.skipped_video_samples.append(str(file_path.relative_to(root)))
                continue

            if ext not in config.ALLOWED_EXTENSIONS:
                if ext not in {".txt", ".csv", ".yaml", ".yml", ".json", ".xml", ".md"}:
                    st.skipped_non_images += 1
                continue

            rf_stem, split_hint = _parse_roboflow_stem_and_split(file_path, root)
            if spec.include_splits is not None and split_hint not in spec.include_splits:
                continue
            if spec.rf_cluster_mode == "phash":
                rf_stem = None

            st.raw_images_found += 1
            if file_path.stat().st_size <= 0 or _is_lfs_pointer(file_path):
                st.skipped_corrupt_or_empty += 1
                continue

            try:
                raw_w, raw_h, ori, upright_w, upright_h = _inspect_image(file_path)
            except Exception:
                st.skipped_corrupt_or_empty += 1
                continue

            if ori != 1:
                st.exif_non_identity_images += 1

            # --- 异构标注抽取（优先按 format_hint / 自动探知：YOLO TXT / COCO JSON / VOC XML）---
            boxes: list[tuple[int, float, float, float, float]] = []
            lbl_path: Path | None = None
            has_label_source = False
            had_raw_rows = False

            yolo_lbl = (
                _find_yolo_label_file(root, file_path)
                if spec.format_hint in {"auto", "yolo"}
                else None
            )
            coco_entry = (
                _lookup_coco_entry(coco_idx, root, file_path)
                if coco_idx is not None and spec.format_hint in {"auto", "coco"}
                else None
            )
            voc_xml = (
                _find_voc_xml_file(root, file_path) if spec.format_hint in {"auto", "voc"} else None
            )

            if yolo_lbl is not None and (spec.format_hint == "yolo" or spec.format_hint == "auto"):
                has_label_source = True
                lbl_path = yolo_lbl
                detected_formats.add("yolo")
                if ori in {5, 6, 7, 8}:
                    st.yolo_exif_rotated_images += 1
                boxes, had_raw_rows = _parse_yolo_labels(
                    yolo_lbl,
                    id_map,
                    upright_w=upright_w,
                    upright_h=upright_h,
                    min_box_px=cfg.min_box_px,
                    stats=st,
                )
            elif coco_entry is not None and spec.format_hint in {"auto", "coco"}:
                has_label_source = True
                detected_formats.add("coco")
                boxes, had_raw_rows = _parse_coco_entry(
                    coco_entry,
                    spec,
                    raw_w=raw_w,
                    raw_h=raw_h,
                    ori=ori,
                    upright_w=upright_w,
                    upright_h=upright_h,
                    min_box_px=cfg.min_box_px,
                    stats=st,
                )
            elif voc_xml is not None and spec.format_hint in {"auto", "voc"}:
                has_label_source = True
                lbl_path = voc_xml
                detected_formats.add("voc")
                boxes, had_raw_rows = _parse_voc_xml(
                    voc_xml,
                    spec,
                    raw_w=raw_w,
                    raw_h=raw_h,
                    ori=ori,
                    upright_w=upright_w,
                    upright_h=upright_h,
                    min_box_px=cfg.min_box_px,
                    stats=st,
                )

            if not has_label_source and not spec.include_unlabeled:
                st.skipped_missing_labels += 1
                continue

            if had_raw_rows and not boxes and not spec.keep_emptied_by_drop:
                st.skipped_emptied_by_drop += 1
                continue

            try:
                phash64 = compute_phash64(file_path)
            except Exception:
                st.skipped_corrupt_or_empty += 1
                continue

            rel_str = str(file_path.relative_to(root)).replace("\\", "/")
            sub_rel = rel_str[len("images/") :] if rel_str.startswith("images/") else None
            vid = (
                video_map.get(rel_str)
                or (video_map.get(sub_rel) if sub_rel is not None else None)
                or video_map.get(file_path.name)
            )
            if rf_stem is not None:
                seen_rf_stems.add(rf_stem)

            is_preaug = bool(spec.has_preaugmentation and split_hint == "train")
            if vid is not None:
                seen_videos.add(vid)
                group_key = f"{spec.name}::video::{vid}"
            elif rf_stem is not None:
                group_key = f"{spec.name}::rf::{rf_stem}"
            else:
                group_key = f"{spec.name}::file::{rel_str}"

            sample_seq += 1
            all_samples.append(
                _ImageSample(
                    sample_id=f"{spec.name}_{sample_seq:06d}_{file_path.stem}",
                    source_name=spec.name,
                    image_path=file_path,
                    label_path=lbl_path,
                    video_id=vid,
                    rf_orig_stem=rf_stem,
                    source_split_hint=split_hint,
                    is_preaugmented=is_preaug,
                    val_test_eligible=not is_preaug,
                    group_key=group_key,
                    upright_width=upright_w,
                    upright_height=upright_h,
                    exif_orientation=ori,
                    boxes=boxes,
                    phash64=phash64,
                    content_sha256=_sha256_file(file_path),
                )
            )

        st.detected_format = (
            "+".join(sorted(detected_formats)) if detected_formats else spec.format_hint
        )
        st.unique_video_ids = len(seen_videos)
        st.unique_rf_stems = len(seen_rf_stems)

    # 先做精确 SHA256 去重与 Roboflow `.rf.` 同源识别（含 preaugmented 标记）
    after_exact = _dedupe_exact_and_rf(
        all_samples,
        dedupe_rf_siblings=cfg.dedupe_rf_siblings,
        stats_by_source=stats_by_source,
    )

    kept_samples = _cluster_and_dedupe_phash(
        after_exact,
        phash_threshold=cfg.phash_threshold,
        dedupe=cfg.dedupe_phash,
        stats_by_source=stats_by_source,
    )
    # merged_v6 清洗管线：夜滤（`mean_v` > `clean_day_mean_v_max` 且全 `on` 才剔帧）+
    # `occluded` 自证重标（`best.pt` 判 `on` `conf>=0.65` → 改 `3`→`0`，其余删框）；
    # `damaged` 全保留（`D-30` 按「设施损坏」语义）；全部基于现有数据、零人工标注
    kept_samples, clean_stats = _clean_samples(kept_samples, cfg=cfg)
    out_names = _out_names(
        cfg
    )  # `nc`/`names`/`target_names`/`split_class_*` 输出类表（`nc=3` 兼容）
    for s in kept_samples:
        stats_by_source[s.source_name].kept_images += 1
        if s.is_preaugmented:
            stats_by_source[s.source_name].preaugmented_images += 1

    split_info = _assign_group_splits(
        kept_samples,
        train_ratio=cfg.train_ratio,
        val_ratio=cfg.val_ratio,
        test_ratio=cfg.test_ratio,
        seed=cfg.seed,
        allow_small_groups=cfg.allow_small_groups,
        forbid_preaug_in_val_test=cfg.forbid_preaug_in_val_test,
    )

    # 切分后硬校验：video_id / rf_orig_stem / phash_cluster / content_sha256 跨分片交集必须严格为 0
    split_videos: dict[str, set[str]] = defaultdict(set)
    split_rf: dict[str, set[str]] = defaultdict(set)
    split_clusters: dict[str, set[int]] = defaultdict(set)
    split_shas: dict[str, set[str]] = defaultdict(set)
    val_test_preaug = 0

    for s in kept_samples:
        if s.video_id is not None:
            split_videos[s.split].add(f"{s.source_name}::{s.video_id}")
        if s.rf_orig_stem is not None:
            split_rf[s.split].add(f"{s.source_name}::{s.rf_orig_stem}")
        split_clusters[s.split].add(s.phash_cluster)
        split_shas[s.split].add(s.content_sha256)
        if cfg.forbid_preaug_in_val_test and s.split in {"val", "test"} and s.is_preaugmented:
            val_test_preaug += 1

    leakage_checks = {
        "video_id_train_val_overlap": len(split_videos["train"] & split_videos["val"]),
        "video_id_train_test_overlap": len(split_videos["train"] & split_videos["test"]),
        "video_id_val_test_overlap": len(split_videos["val"] & split_videos["test"]),
        "rf_stem_train_val_overlap": len(split_rf["train"] & split_rf["val"]),
        "rf_stem_train_test_overlap": len(split_rf["train"] & split_rf["test"]),
        "rf_stem_val_test_overlap": len(split_rf["val"] & split_rf["test"]),
        "phash_cluster_train_val_overlap": len(split_clusters["train"] & split_clusters["val"]),
        "phash_cluster_train_test_overlap": len(split_clusters["train"] & split_clusters["test"]),
        "phash_cluster_val_test_overlap": len(split_clusters["val"] & split_clusters["test"]),
        "content_sha256_train_val_overlap": len(split_shas["train"] & split_shas["val"]),
        "content_sha256_train_test_overlap": len(split_shas["train"] & split_shas["test"]),
        "content_sha256_val_test_overlap": len(split_shas["val"] & split_shas["test"]),
        "val_test_preaugmented_count": val_test_preaug,
    }
    if any(v != 0 for v in leakage_checks.values()):
        raise RuntimeError(f"检测到跨 split 数据泄漏或预增广污染: {leakage_checks}")

    # 落盘输出目录：先清空六个 split 目录既有文件再写本批，
    # 防止换分组/种子复用输出目录时旧 split 残留样本造成跨 split 污染
    # （内存校验只覆盖本批样本，盘上残留不参与 leakage_checks）
    out_dir = cfg.output_dir
    for split_name in ("train", "val", "test"):
        for sub in ("images", "labels"):
            split_dir = out_dir / sub / split_name
            if split_dir.is_dir():
                for stale in split_dir.iterdir():
                    if stale.is_file() or stale.is_symlink():
                        stale.unlink()
            split_dir.mkdir(parents=True, exist_ok=True)

    split_image_counts: Counter[str] = Counter()
    split_box_counts: Counter[str] = Counter()
    split_source_counts: dict[str, Counter[str]] = defaultdict(Counter)
    split_class_boxes: dict[str, Counter[str]] = defaultdict(Counter)
    split_class_images: dict[str, Counter[str]] = defaultdict(Counter)

    for s in kept_samples:
        split_image_counts[s.split] += 1
        split_source_counts[s.split][s.source_name] += 1
        img_dst = out_dir / "images" / s.split / f"{s.sample_id}{s.image_path.suffix.lower()}"
        lbl_dst = out_dir / "labels" / s.split / f"{s.sample_id}.txt"
        _materialize_file(s.image_path, img_dst, cfg.link_mode)

        label_lines = [
            f"{cls_id} {xc:.6f} {yc:.6f} {bw:.6f} {bh:.6f}" for cls_id, xc, yc, bw, bh in s.boxes
        ]
        lbl_dst.write_text("\n".join(label_lines) + ("\n" if label_lines else ""), encoding="utf-8")

        split_box_counts[s.split] += len(s.boxes)
        present_classes = {cls_id for cls_id, *_ in s.boxes}
        for cls_id in present_classes:
            split_class_images[s.split][out_names[cls_id]] += 1
        for cls_id, *_ in s.boxes:
            split_class_boxes[s.split][out_names[cls_id]] += 1

    # 逐类别框数占比与目标比例偏差（供切分质量审计）
    class_totals = {
        cname: sum(split_class_boxes[sp][cname] for sp in ("train", "val", "test"))
        for cname in out_names.values()
    }
    class_proportion_deviation: dict[str, dict[str, dict[str, Any]]] = {}
    for cname, tot in class_totals.items():
        if tot <= 0:
            continue
        entry: dict[str, dict[str, Any]] = {}
        for sp, tgt in (
            ("train", cfg.train_ratio),
            ("val", cfg.val_ratio),
            ("test", cfg.test_ratio),
        ):
            cnt = split_class_boxes[sp][cname]
            share = cnt / tot
            entry[sp] = {
                "boxes": cnt,
                "share": round(share, 4),
                "target_share": tgt,
                "deviation_pp": round(abs(share - tgt) * 100.0, 3),
            }
        class_proportion_deviation[cname] = entry

    data_yaml_path = _write_data_yaml(out_dir, names=out_names)
    manifest_payload: dict[str, Any] = {
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "output_dir": str(out_dir.resolve()),
        "target_names": out_names,
        "config": {
            "train_ratio": cfg.train_ratio,
            "val_ratio": cfg.val_ratio,
            "test_ratio": cfg.test_ratio,
            "phash_threshold": cfg.phash_threshold,
            "dedupe_phash": cfg.dedupe_phash,
            "dedupe_rf_siblings": cfg.dedupe_rf_siblings,
            "min_box_px": cfg.min_box_px,
            "forbid_preaug_in_val_test": cfg.forbid_preaug_in_val_test,
            "seed": cfg.seed,
            "link_mode": cfg.link_mode,
            "clean_day_mean_v_max": cfg.clean_day_mean_v_max,
            "clean_occluded_model": (
                str(cfg.clean_occluded_model) if cfg.clean_occluded_model else None
            ),
            "clean_drop_occluded": cfg.clean_drop_occluded,
        },
        "etl_funnel_totals": {
            "raw_images_found": sum(st.raw_images_found for st in stats_by_source.values()),
            "skipped_videos": sum(st.skipped_videos for st in stats_by_source.values()),
            "skipped_corrupt_or_empty": sum(
                st.skipped_corrupt_or_empty for st in stats_by_source.values()
            ),
            "skipped_missing_labels": sum(
                st.skipped_missing_labels for st in stats_by_source.values()
            ),
            "skipped_emptied_by_drop": sum(
                st.skipped_emptied_by_drop for st in stats_by_source.values()
            ),
            "exif_non_identity_images": sum(
                st.exif_non_identity_images for st in stats_by_source.values()
            ),
            "raw_annotations_total": sum(
                st.raw_annotations_total for st in stats_by_source.values()
            ),
            "clipped_boxes": sum(st.clipped_boxes for st in stats_by_source.values()),
            "dropped_unmapped_boxes": sum(
                st.dropped_unmapped_boxes for st in stats_by_source.values()
            ),
            "dropped_degenerate_boxes": sum(
                st.dropped_degenerate_boxes for st in stats_by_source.values()
            ),
            "dropped_invalid_boxes": sum(
                st.dropped_invalid_boxes for st in stats_by_source.values()
            ),
            "kept_boxes_total": sum(st.kept_boxes_total for st in stats_by_source.values()),
            "deduped_sha256": sum(st.deduped_sha256 for st in stats_by_source.values()),
            "deduped_rf_siblings": sum(st.deduped_rf_siblings for st in stats_by_source.values()),
            "deduped_phash": sum(st.deduped_phash for st in stats_by_source.values()),
            "kept_images": len(kept_samples),
        },
        "sources": [
            {
                "name": st.name,
                "root_dir": st.root_dir,
                "detected_format": st.detected_format,
                "raw_images_found": st.raw_images_found,
                "skipped_videos": st.skipped_videos,
                "skipped_video_samples": st.skipped_video_samples,
                "skipped_non_images": st.skipped_non_images,
                "skipped_missing_labels": st.skipped_missing_labels,
                "skipped_corrupt_or_empty": st.skipped_corrupt_or_empty,
                "skipped_emptied_by_drop": st.skipped_emptied_by_drop,
                "exif_non_identity_images": st.exif_non_identity_images,
                "yolo_exif_rotated_images": st.yolo_exif_rotated_images,
                "raw_annotations_total": st.raw_annotations_total,
                "clipped_boxes": st.clipped_boxes,
                "dropped_unmapped_boxes": st.dropped_unmapped_boxes,
                "dropped_degenerate_boxes": st.dropped_degenerate_boxes,
                "dropped_invalid_boxes": st.dropped_invalid_boxes,
                "kept_boxes_total": st.kept_boxes_total,
                "format_box_counts": st.format_box_counts,
                "mapped_classes_counts": st.mapped_classes_counts,
                "dropped_classes_counts": st.dropped_classes_counts,
                "deduped_sha256": st.deduped_sha256,
                "deduped_rf_siblings": st.deduped_rf_siblings,
                "deduped_phash": st.deduped_phash,
                "preaugmented_images": st.preaugmented_images,
                "kept_images": st.kept_images,
                "unique_video_ids": st.unique_video_ids,
                "unique_rf_stems": st.unique_rf_stems,
                "rf_cluster_mode": st.rf_cluster_mode,
            }
            for st in stats_by_source.values()
        ],
        "splits": {
            sp: {
                "images": split_image_counts[sp],
                "boxes": split_box_counts[sp],
                "unique_video_ids": len(split_videos[sp]),
                "unique_rf_stems": len(split_rf[sp]),
                "unique_phash_clusters": len(split_clusters[sp]),
                "source_image_counts": dict(sorted(split_source_counts[sp].items())),
                "class_image_counts": {
                    cname: split_class_images[sp][cname] for cname in out_names.values()
                },
                "class_box_counts": {
                    cname: split_class_boxes[sp][cname] for cname in out_names.values()
                },
            }
            for sp in ("train", "val", "test")
        },
        "class_proportion_deviation": class_proportion_deviation,
        "split_assignment": split_info["split_assignment"],
        "split_objective": split_info["objective"],
        "leakage_checks": leakage_checks,
        "clean_stats": clean_stats,
    }
    manifest_path = out_dir / "prep_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    return PrepResult(
        output_dir=out_dir,
        data_yaml_path=data_yaml_path,
        manifest_path=manifest_path,
        total_kept_images=len(kept_samples),
        total_skipped_videos=sum(st.skipped_videos for st in stats_by_source.values()),
        total_deduped_phash=sum(st.deduped_phash for st in stats_by_source.values()),
        split_image_counts={sp: split_image_counts[sp] for sp in ("train", "val", "test")},
        leakage_checks=leakage_checks,
    )


def _parse_source_arg(raw: str) -> DatasetSourceSpec:
    """解析 `--source name=/path[|opts]` 或传统 `name=/path[:0=0,1=1,2=3]` 参数.

    扩展格式支持以 `;` 分隔的键值对：
    `name=/path|preaug=1,format=coco,map=Damaged:2;NormalPole:DROP`
    同时保持旧语法 `name=/path[:0=0,1=1,2=3]` 完全兼容。
    """
    if "=" not in raw:
        raise ValueError(f"--source 格式应为 name=/path[:id_map][|opts]，收到: {raw}")
    name, rest = raw.split("=", 1)

    has_preaug = False
    format_hint = "auto"
    class_name_map: dict[str, int | str | None] | None = None
    include_unlabeled = False
    include_splits: tuple[str, ...] | None = None
    rf_cluster_mode = "stem"

    if "|" in rest:
        rest, opt_str = rest.split("|", 1)
        for token in opt_str.split(","):
            token = token.strip()
            if not token:
                continue
            if "=" not in token:
                raise ValueError(f"非法选项 {token!r}")
            ok, ov = token.split("=", 1)
            ok = ok.strip().lower()
            ov = ov.strip()
            if ok == "preaug":
                has_preaug = ov.lower() in {"1", "true", "yes"}
            elif ok == "format":
                format_hint = ov.lower()
            elif ok == "unlabeled":
                include_unlabeled = ov.lower() in {"1", "true", "yes"}
            elif ok == "splits":
                include_splits = tuple(s.strip() for s in ov.split(";") if s.strip())
            elif ok == "rfmode":
                rf_cluster_mode = ov.lower()
            elif ok == "map":
                class_name_map = {}
                for pair in ov.split(";"):
                    pair = pair.strip()
                    if not pair:
                        continue
                    src_k, dst_v = pair.split(":", 1)
                    class_name_map[src_k.strip()] = (
                        int(dst_v) if dst_v.strip().isdigit() else dst_v.strip()
                    )
            else:
                raise ValueError(f"未知 --source 选项 {ok!r}")

    explicit_map: dict[int, int | None] | None = None
    if ":" in rest:
        path_str, map_str = rest.rsplit(":", 1)
        explicit_map = {}
        for item in map_str.split(","):
            item = item.strip()
            if not item:
                continue
            src_k, dst_v = item.split("=", 1)
            dv = dst_v.strip()
            explicit_map[int(src_k)] = None if dv.upper() == "DROP" else int(dv)
    else:
        path_str = rest
    return DatasetSourceSpec(
        name=name.strip(),
        root_dir=Path(path_str),
        explicit_id_map=explicit_map,
        class_name_map=class_name_map,
        format_hint=format_hint,
        include_unlabeled=include_unlabeled,
        has_preaugmentation=has_preaug,
        include_splits=include_splits,
        rf_cluster_mode=rf_cluster_mode,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Tomori YOLO26 多源异构路灯数据集 ETL、pHash/RF 去重与防泄漏分层切分 CLI"
    )
    parser.add_argument(
        "--source",
        action="append",
        required=True,
        help="数据集来源，格式 name=/path[:0=0,1=1,2=3][|preaug=1,format=auto,map=A:2;B:DROP]",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="清洗切分后标准 YOLO 数据集输出目录",
    )
    parser.add_argument("--train-ratio", type=float, default=0.70)
    parser.add_argument("--val-ratio", type=float, default=0.15)
    parser.add_argument("--test-ratio", type=float, default=0.15)
    parser.add_argument("--phash-threshold", type=int, default=6)
    parser.add_argument(
        "--no-dedupe", action="store_true", help="仅做组防泄漏切分，不删 pHash 重复"
    )
    parser.add_argument(
        "--dedupe-rf-siblings",
        action="store_true",
        help="对同一 Roboflow `.rf.` 原图 stem 的多副本仅保留 1 个规范代表",
    )
    parser.add_argument("--min-box-px", type=float, default=2.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--link-mode",
        choices=("hardlink", "copy", "symlink"),
        default="hardlink",
    )
    parser.add_argument("--allow-small-groups", action="store_true")
    # merged_v6 清洗开关（`task-161` 实测依据；默认全关保持 merged_v5 兼容）
    parser.add_argument(
        "--clean-day-mean-v-max",
        type=float,
        default=0.0,
        help=">0 时剔除「`mean_v` 超阈值且全为 `on`」的白天帧",
    )
    parser.add_argument(
        "--clean-occluded-model",
        type=Path,
        default=None,
        help="`best.pt`：`occluded` 自证重标（`conf>=0.65` 判 `on` → `3`→`0`）",
    )
    parser.add_argument(
        "--drop-occluded",
        "--nc3",
        action="store_true",
        dest="clean_drop_occluded",
        help="`task-165`：`occluded` 弃用——删 `cid==3` + `nc`/`names`/`target_names` 改 `3` 类",
    )
    parser.add_argument(
        "--clean-damaged-lamp-model",
        type=Path,
        default=None,
        help="`best.pt`：`d3` `damaged` 灯头区过滤（`conf>=0.25` 检出灯具框，"
        "`damaged` GT 须 `IoU>=0.1` 或中心点落灯具框内才保留）",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    specs = tuple(_parse_source_arg(item) for item in args.source)
    cfg = DatasetPrepConfig(
        sources=specs,
        output_dir=args.output_dir,
        train_ratio=args.train_ratio,
        val_ratio=args.val_ratio,
        test_ratio=args.test_ratio,
        phash_threshold=args.phash_threshold,
        dedupe_phash=not args.no_dedupe,
        dedupe_rf_siblings=args.dedupe_rf_siblings,
        min_box_px=args.min_box_px,
        seed=args.seed,
        link_mode=args.link_mode,
        allow_small_groups=args.allow_small_groups,
        clean_day_mean_v_max=args.clean_day_mean_v_max,
        clean_occluded_model=args.clean_occluded_model,
        clean_drop_occluded=args.clean_drop_occluded,
        clean_damaged_lamp_model=args.clean_damaged_lamp_model,
    )
    res = prepare_dataset(cfg)
    print(
        json.dumps(
            {
                "output_dir": str(res.output_dir),
                "data_yaml": str(res.data_yaml_path),
                "manifest": str(res.manifest_path),
                "total_kept_images": res.total_kept_images,
                "total_skipped_videos": res.total_skipped_videos,
                "total_deduped_phash": res.total_deduped_phash,
                "split_image_counts": res.split_image_counts,
                "leakage_checks": res.leakage_checks,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
