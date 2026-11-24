"""Unit tests for `tomori.vision` ETL (`dataset_prep.py`), EDA (`eda.py`), and PEP 562 lazy exports.

All inputs are built purely in memory / `tmp_path` — no remote SSH, no real dataset downloads,
and no training pipeline execution.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw

from tomori.vision.dataset_prep import (
    DatasetPrepConfig,
    DatasetSourceSpec,
    SourceStats,
    _clip_and_validate_xyxy,
    _parse_yolo_labels,
    _transform_norm_xyxy_for_exif,
    compute_phash64,
    hamming_distance64,
    prepare_dataset,
)
from tomori.vision.eda import _imgsz_small_object_retention, run_eda


def _write_solid_pattern_img(
    path: Path,
    *,
    width: int = 200,
    height: int = 200,
    pattern: str = "bars",
    exif_orientation: int | None = None,
) -> None:
    """Create distinct deterministic textures whose pHash distance is large across patterns."""
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (width, height), (20, 25, 30))
    draw = ImageDraw.Draw(img)
    if pattern == "bars":
        for y in range(0, height, 20):
            draw.rectangle((0, y, width - 1, y + 8), fill=(240, 220, 120))
    elif pattern == "checker":
        step = 25
        for y in range(0, height, step):
            for x in range(0, width, step):
                if ((x // step) + (y // step)) % 2 == 0:
                    draw.rectangle((x, y, x + step - 1, y + step - 1), fill=(20, 210, 240))
    elif pattern == "diag":
        for off in range(-height, width, 24):
            draw.line((off, 0, off + height, height), fill=(250, 90, 60), width=6)
    elif pattern == "cross":
        draw.rectangle((width // 2 - 12, 0, width // 2 + 12, height - 1), fill=(255, 255, 255))
        draw.rectangle((0, height // 2 - 12, width - 1, height // 2 + 12), fill=(255, 255, 255))
    elif pattern == "circle":
        draw.ellipse(
            (width // 4, height // 4, width * 3 // 4, height * 3 // 4), fill=(90, 240, 110)
        )

    if exif_orientation is not None:
        exif = img.getexif()
        exif[274] = exif_orientation
        img.save(path, format="JPEG", quality=95, exif=exif)
    else:
        ext = path.suffix.lower()
        fmt = "PNG" if ext == ".png" else "JPEG"
        img.save(path, format=fmt)


def _seed_minimal_source(
    root: Path,
    *,
    specs: list[tuple[str, str, str]],
    yaml_names: dict[int, str],
) -> None:
    """Create a small YOLO dataset where each entry is `(split_sub, stem, yolo_lines)`."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "data.yaml").write_text(
        "names:\n" + "\n".join(f"  {k}: {v}" for k, v in yaml_names.items()) + "\n",
        encoding="utf-8",
    )
    patterns = ("bars", "checker", "diag", "cross")
    for idx, (split_sub, stem, yolo_lines) in enumerate(specs):
        img_path = root / split_sub / "images" / f"{stem}.jpg"
        lbl_path = root / split_sub / "labels" / f"{stem}.txt"
        lbl_path.parent.mkdir(parents=True, exist_ok=True)
        _write_solid_pattern_img(img_path, width=256, height=256, pattern=patterns[idx % 4])
        lbl_path.write_text(yolo_lines, encoding="utf-8")


def test_polygon_to_bbox_5col_vs_polygon_precision(tmp_path: Path) -> None:
    """YOLO 5-col box and >5-col polygon both convert accurately to `[xc, yc, bw, bh]`."""
    src = tmp_path / "poly_src"
    specs = [
        (
            "train",
            "sample_0",
            "0 0.500000 0.500000 0.200000 0.300000\n3 0.10 0.20 0.40 0.25 0.35 0.70 0.12 0.65\n",
        ),
        ("val", "sample_1", "1 0.25 0.25 0.20 0.20\n"),
        ("test", "sample_2", "2 0.70 0.70 0.20 0.20\n"),
    ]
    _seed_minimal_source(
        src,
        specs=specs,
        yaml_names={
            0: "streetlight_on",
            1: "streetlight_off",
            2: "streetlight_damaged",
            3: "streetlight_occluded",
        },
    )

    out = tmp_path / "out_poly"
    res = prepare_dataset(
        DatasetPrepConfig(
            sources=(DatasetSourceSpec(name="poly", root_dir=src),),
            output_dir=out,
            dedupe_phash=False,
            allow_small_groups=True,
        )
    )
    assert res.total_kept_images == 3
    manifest = json.loads(res.manifest_path.read_text(encoding="utf-8"))
    fmt_counts = manifest["sources"][0]["format_box_counts"]
    assert fmt_counts["yolo_bbox"] == 3
    assert fmt_counts["yolo_polygon"] == 1

    lbl_hits = list((out / "labels").rglob("*_sample_0.txt"))
    assert len(lbl_hits) == 1
    rows = [
        [float(x) for x in line.split()]
        for line in lbl_hits[0].read_text(encoding="utf-8").splitlines()
    ]
    assert len(rows) == 2
    assert rows[0] == [0.0, 0.5, 0.5, 0.2, 0.3]
    cls_p, xc_p, yc_p, bw_p, bh_p = rows[1]
    assert int(cls_p) == 3
    assert abs(xc_p - 0.25) < 1e-5
    assert abs(yc_p - 0.45) < 1e-5
    assert abs(bw_p - 0.30) < 1e-5
    assert abs(bh_p - 0.50) < 1e-5


def test_exif_orientation_6_coco_and_voc_transform_matches_decoded_pixels(tmp_path: Path) -> None:
    """EXIF Orientation=6 rotates raw 200x100 -> upright 100x200; transformed bbox hits marker."""
    x1, y1, x2, y2 = _transform_norm_xyxy_for_exif(150 / 200, 10 / 100, 190 / 200, 30 / 100, 6)
    assert abs(x1 - 0.70) < 1e-6 and abs(x2 - 0.90) < 1e-6
    assert abs(y1 - 0.75) < 1e-6 and abs(y2 - 0.95) < 1e-6

    def _make_exif6_marker_jpg(dest: Path, *, bg_rgb: tuple[int, int, int]) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        raw_img = Image.new("RGB", (200, 100), bg_rgb)
        d = ImageDraw.Draw(raw_img)
        d.rectangle((150, 10, 190, 30), fill=(255, 240, 200))
        exif = raw_img.getexif()
        exif[274] = 6
        raw_img.save(dest, format="JPEG", quality=98, exif=exif)

    coco_root = tmp_path / "coco_exif"
    _make_exif6_marker_jpg(coco_root / "images" / "rot_coco.jpg", bg_rgb=(5, 5, 10))
    _write_solid_pattern_img(
        coco_root / "images" / "plain_a.jpg", width=160, height=160, pattern="bars"
    )
    _write_solid_pattern_img(
        coco_root / "images" / "plain_b.jpg", width=160, height=160, pattern="checker"
    )
    (coco_root / "_annotations.coco.json").write_text(
        json.dumps(
            {
                "categories": [{"id": 1, "name": "damaged"}],
                "images": [
                    {"id": 1, "file_name": "rot_coco.jpg", "width": 200, "height": 100},
                    {"id": 2, "file_name": "plain_a.jpg", "width": 160, "height": 160},
                    {"id": 3, "file_name": "plain_b.jpg", "width": 160, "height": 160},
                ],
                "annotations": [
                    {"id": 1, "image_id": 1, "category_id": 1, "bbox": [150, 10, 40, 20]},
                    {"id": 2, "image_id": 2, "category_id": 1, "bbox": [20, 20, 40, 40]},
                    {"id": 3, "image_id": 3, "category_id": 1, "bbox": [40, 40, 40, 40]},
                ],
            }
        ),
        encoding="utf-8",
    )

    voc_root = tmp_path / "voc_exif"
    _make_exif6_marker_jpg(voc_root / "images" / "rot_voc.jpg", bg_rgb=(8, 8, 14))
    (voc_root / "Annotations").mkdir(parents=True, exist_ok=True)
    (voc_root / "Annotations" / "rot_voc.xml").write_text(
        """<annotation>
  <size><width>200</width><height>100</height></size>
  <object>
    <name>occluded</name>
    <bndbox><xmin>150</xmin><ymin>10</ymin><xmax>190</xmax><ymax>30</ymax></bndbox>
  </object>
</annotation>
""",
        encoding="utf-8",
    )

    out = tmp_path / "out_exif"
    res = prepare_dataset(
        DatasetPrepConfig(
            sources=(
                DatasetSourceSpec(name="coco_src", root_dir=coco_root, format_hint="coco"),
                DatasetSourceSpec(name="voc_src", root_dir=voc_root, format_hint="voc"),
            ),
            output_dir=out,
            dedupe_phash=False,
            allow_small_groups=True,
        )
    )
    assert res.total_kept_images == 4

    for sample_tag, expected_cls in (("rot_coco", 2), ("rot_voc", 3)):
        img_hits = list((out / "images").rglob(f"*{sample_tag}.jpg"))
        lbl_hits = list((out / "labels").rglob(f"*{sample_tag}.txt"))
        assert len(img_hits) == 1 and len(lbl_hits) == 1

        bgr = cv2.imdecode(
            np.frombuffer(img_hits[0].read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR
        )
        assert bgr is not None
        h_up, w_up = bgr.shape[:2]
        assert (w_up, h_up) == (100, 200)

        cls_id, xc, yc, bw, bh = (
            float(v) for v in lbl_hits[0].read_text(encoding="utf-8").strip().split()
        )
        assert int(cls_id) == expected_cls
        assert abs(xc - 0.80) < 0.01
        assert abs(yc - 0.85) < 0.01
        assert abs(bw - 0.20) < 0.01
        assert abs(bh - 0.20) < 0.01

        px_x1 = int((xc - 0.5 * bw) * w_up) + 2
        px_x2 = int((xc + 0.5 * bw) * w_up) - 2
        px_y1 = int((yc - 0.5 * bh) * h_up) + 2
        px_y2 = int((yc + 0.5 * bh) * h_up) - 2
        inside_mean = float(bgr[px_y1:px_y2, px_x1:px_x2].mean())
        outside_mean = float(bgr[:40, :40].mean())
        assert inside_mean > 180.0 and outside_mean < 20.0


def test_drop_unmapped_clip_01_degenerate_2px_and_video_n1_rejection(tmp_path: Path) -> None:
    """Verify explicit DROP (never guessing utility_pole), [0,1] clip, <2px filter, video skip."""
    clipped_box, was_clipped, deg = _clip_and_validate_xyxy(
        -0.05, 0.10, 0.25, 1.08, img_w=200, img_h=200, min_box_px=2.0
    )
    assert was_clipped is True and deg is False and clipped_box is not None
    xc, yc, bw, bh = clipped_box
    assert abs(bw - 0.25) < 1e-6 and abs(bh - 0.90) < 1e-6
    assert abs(xc - 0.125) < 1e-6 and abs(yc - 0.55) < 1e-6

    tiny_box, _, deg_tiny = _clip_and_validate_xyxy(
        0.10, 0.10, 0.107, 0.30, img_w=200, img_h=200, min_box_px=2.0
    )
    assert tiny_box is None and deg_tiny is True

    src = tmp_path / "etl_src"
    (src / "train" / "images").mkdir(parents=True, exist_ok=True)
    (src / "train" / "labels").mkdir(parents=True, exist_ok=True)
    (src / "train" / "images" / "bad_stream.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42")
    _write_solid_pattern_img(
        src / "train" / "images" / "clip_ok.jpg", width=200, height=200, pattern="bars"
    )
    (src / "train" / "labels" / "clip_ok.txt").write_text(
        "0 -0.05 0.10 0.30 0.40\n0 0.50 0.50 0.005 0.20\n1 0.50 0.50 0.20 0.50\n",
        encoding="utf-8",
    )
    for idx, pat in enumerate(("checker", "diag"), start=1):
        _write_solid_pattern_img(
            src / "train" / "images" / f"extra_{idx}.jpg", width=200, height=200, pattern=pat
        )
        (src / "train" / "labels" / f"extra_{idx}.txt").write_text(
            "0 0.50 0.50 0.20 0.20\n", encoding="utf-8"
        )

    (src / "data.yaml").write_text(
        "names:\n  0: broken_lamp\n  1: utility_pole\n", encoding="utf-8"
    )

    parsed_boxes, _ = _parse_yolo_labels(
        src / "train" / "labels" / "clip_ok.txt",
        {0: (2, "broken_lamp"), 1: (None, "utility_pole")},
        upright_w=200,
        upright_h=200,
        min_box_px=2.0,
        stats=SourceStats(name="t", root_dir="."),
    )
    assert len(parsed_boxes) == 1 and parsed_boxes[0][0] == 2

    out = tmp_path / "out_etl"
    res = prepare_dataset(
        DatasetPrepConfig(
            sources=(DatasetSourceSpec(name="etl", root_dir=src),),
            output_dir=out,
            dedupe_phash=False,
            allow_small_groups=True,
        )
    )
    assert res.total_skipped_videos == 1
    assert list(out.rglob("*.mp4")) == []
    manifest = json.loads(res.manifest_path.read_text(encoding="utf-8"))
    st = manifest["sources"][0]
    assert st["skipped_videos"] == 1
    assert st["clipped_boxes"] >= 1
    assert st["dropped_degenerate_boxes"] == 1
    assert st["dropped_unmapped_boxes"] == 1
    assert st["dropped_classes_counts"] == {"utility_pole": 1}


def test_rf_prefix_collision_stem_false_merge_vs_phash_and_sha256(tmp_path: Path) -> None:
    """D7 anti-false-green: same stem `100` diff images vs different stems same image."""

    def _build_d7_trap(root: Path) -> None:
        root.mkdir(parents=True, exist_ok=True)
        (root / "data.yaml").write_text("names:\n  0: damaged\n  1: working\n", encoding="utf-8")
        tr_img = root / "train" / "images"
        tr_lbl = root / "train" / "labels"
        te_img = root / "test" / "images"
        te_lbl = root / "test" / "labels"
        tr_lbl.mkdir(parents=True, exist_ok=True)
        te_lbl.mkdir(parents=True, exist_ok=True)

        # Pair A: BOTH named `100_jpg.rf.<hash>` in `train/`, VISUALLY DIFFERENT!
        a1 = tr_img / "100_jpg.rf.aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.jpg"
        a2 = tr_img / "100_jpg.rf.bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.jpg"
        _write_solid_pattern_img(a1, width=256, height=256, pattern="bars")
        _write_solid_pattern_img(a2, width=256, height=256, pattern="checker")
        assert hamming_distance64(compute_phash64(a1), compute_phash64(a2)) > 6
        (tr_lbl / f"{a1.stem}.txt").write_text("0 0.5 0.5 0.2 0.2\n", encoding="utf-8")
        (tr_lbl / f"{a2.stem}.txt").write_text("0 0.5 0.5 0.3 0.3\n", encoding="utf-8")

        # Pair B: DIFFERENT stems (`105` vs `POSTEEEEEES-CAIDOS-50-`), IDENTICAL bytes!
        b1 = tr_img / "105_jpg.rf.cccccccccccccccccccccccccccccccc.jpg"
        b2 = te_img / "POSTEEEEEES-CAIDOS-50-_jpg.rf.dddddddddddddddddddddddddddddddd.jpg"
        _write_solid_pattern_img(b1, width=256, height=256, pattern="diag")
        b2.parent.mkdir(parents=True, exist_ok=True)
        b2.write_bytes(b1.read_bytes())
        (tr_lbl / f"{b1.stem}.txt").write_text("1 0.4 0.4 0.2 0.2\n", encoding="utf-8")
        (te_lbl / f"{b2.stem}.txt").write_text("1 0.4 0.4 0.2 0.2\n", encoding="utf-8")

        c1 = te_img / "200_jpg.rf.eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee.jpg"
        _write_solid_pattern_img(c1, width=256, height=256, pattern="cross")
        (te_lbl / f"{c1.stem}.txt").write_text("0 0.6 0.6 0.2 0.2\n", encoding="utf-8")

    trap_src = tmp_path / "d7_trap"
    _build_d7_trap(trap_src)

    # Mode 1: `rf_cluster_mode="stem"` falsely merges Pair A
    out_stem = tmp_path / "out_stem"
    res_stem = prepare_dataset(
        DatasetPrepConfig(
            sources=(DatasetSourceSpec(name="d7", root_dir=trap_src, rf_cluster_mode="stem"),),
            output_dir=out_stem,
            dedupe_rf_siblings=True,
            dedupe_phash=True,
            allow_small_groups=True,
        )
    )
    m_stem = json.loads(res_stem.manifest_path.read_text(encoding="utf-8"))
    st_stem = m_stem["sources"][0]
    assert st_stem["deduped_rf_siblings"] == 1
    assert st_stem["deduped_sha256"] == 1
    assert res_stem.total_kept_images == 3

    # Mode 2: `rf_cluster_mode="phash"` preserves BOTH `100` patterns while SHA256 catches Pair B
    out_phash = tmp_path / "out_phash"
    res_phash = prepare_dataset(
        DatasetPrepConfig(
            sources=(DatasetSourceSpec(name="d7", root_dir=trap_src, rf_cluster_mode="phash"),),
            output_dir=out_phash,
            dedupe_rf_siblings=True,
            dedupe_phash=True,
            allow_small_groups=True,
        )
    )
    m_phash = json.loads(res_phash.manifest_path.read_text(encoding="utf-8"))
    st_phash = m_phash["sources"][0]
    assert st_phash["deduped_rf_siblings"] == 0
    assert st_phash["deduped_sha256"] == 1
    assert res_phash.total_kept_images == 4


def test_stratified_group_split_all_13_leakage_checks_zero_and_no_preaug_in_val_test(
    tmp_path: Path,
) -> None:
    """Verify all 13 anti-leakage checks == 0 and preaugmented train copies stay out of val/test."""
    clean_src = tmp_path / "split_clean_src"
    clean_src.mkdir(parents=True, exist_ok=True)
    names_yaml = (
        "names:\n  0: streetlight_on\n  1: streetlight_off\n"
        "  2: streetlight_damaged\n  3: streetlight_occluded\n"
    )
    (clean_src / "data.yaml").write_text(names_yaml, encoding="utf-8")

    csv_rows = ["relative_path,file_name,video_id"]
    patterns = ("bars", "checker", "diag", "cross")

    for vid_idx in range(4):
        cls_id = vid_idx
        for frame_idx in range(2):
            fname = f"v{vid_idx}_f{frame_idx}.jpg"
            img_p = clean_src / "images" / fname
            lbl_p = clean_src / "labels" / f"v{vid_idx}_f{frame_idx}.txt"
            lbl_p.parent.mkdir(parents=True, exist_ok=True)
            _write_solid_pattern_img(
                img_p,
                width=200 + vid_idx * 4 + frame_idx,
                height=200,
                pattern=patterns[vid_idx % 4],
            )
            lbl_p.write_text(f"{cls_id} 0.5 0.5 0.2 0.2\n", encoding="utf-8")
            csv_rows.append(f"images/{fname},{fname},VID_{vid_idx}")
    (clean_src / "metadata.csv").write_text("\n".join(csv_rows) + "\n", encoding="utf-8-sig")

    # Separate preaugmented Roboflow source (`has_preaugmentation=True` -> train-only)
    preaug_src = tmp_path / "split_preaug_src"
    preaug_src.mkdir(parents=True, exist_ok=True)
    (preaug_src / "data.yaml").write_text(names_yaml, encoding="utf-8")
    (preaug_src / "train" / "images").mkdir(parents=True, exist_ok=True)
    (preaug_src / "train" / "labels").mkdir(parents=True, exist_ok=True)
    for idx, tag in enumerate(
        ("aaaa1111aaaa1111aaaa1111aaaa1111", "bbbb2222bbbb2222bbbb2222bbbb2222")
    ):
        rf_p = preaug_src / "train" / "images" / f"preaug_lamp_jpg.rf.{tag}.jpg"
        rf_l = preaug_src / "train" / "labels" / f"preaug_lamp_jpg.rf.{tag}.txt"
        _write_solid_pattern_img(rf_p, width=240 + idx * 4, height=240, pattern="circle")
        rf_l.write_text("2 0.3 0.3 0.2 0.2\n", encoding="utf-8")

    out = tmp_path / "out_split"
    res = prepare_dataset(
        DatasetPrepConfig(
            sources=(
                DatasetSourceSpec(name="d1_clean", root_dir=clean_src, has_preaugmentation=False),
                DatasetSourceSpec(
                    name="rf_preaug",
                    root_dir=preaug_src,
                    has_preaugmentation=True,
                    rf_cluster_mode="stem",
                ),
            ),
            output_dir=out,
            dedupe_phash=False,
            dedupe_rf_siblings=False,
            forbid_preaug_in_val_test=True,
        )
    )

    assert len(res.leakage_checks) == 13
    assert all(v == 0 for v in res.leakage_checks.values()), res.leakage_checks
    assert res.leakage_checks["val_test_preaugmented_count"] == 0
    assert res.split_image_counts["train"] > 0
    assert res.split_image_counts["val"] > 0
    assert res.split_image_counts["test"] > 0
    assert list((out / "images" / "val").glob("*preaug*")) == []
    assert list((out / "images" / "test").glob("*preaug*")) == []
    assert (out / "data.yaml").is_file()
    assert res.manifest_path.is_file()


def test_lexicographic_class_stratified_split_occluded_floor_and_proportion(
    tmp_path: Path,
) -> None:
    """组+单图混合分层装箱：`occluded` 双集 ≥30、单图源类偏差 ≤3pp、泄漏 0、训练集非空."""
    src = tmp_path / "strat_src"
    src.mkdir(parents=True)
    names_yaml = (
        "names:\n  0: streetlight_on\n  1: streetlight_off\n"
        "  2: streetlight_damaged\n  3: streetlight_occluded\n"
    )
    (src / "data.yaml").write_text(names_yaml, encoding="utf-8")
    csv_rows = ["relative_path,file_name,video_id,frame_id"]

    def _img(stem: str, pattern_idx: int) -> None:
        # 每图按文件名 sha256 生成唯一内容（否则 SHA256 会因字节相同去重）
        seed = int.from_bytes(__import__("hashlib").sha256(stem.encode()).digest()[:8], "big")
        rng = np.random.default_rng(seed)
        arr = rng.integers(0, 256, (220, 220, 3), dtype=np.uint8)
        dst = src / "images" / stem
        dst.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(arr).save(dst, format="JPEG", quality=90)

    def _lbl(stem: str, text: str) -> None:
        lbl = src / "labels" / f"{Path(stem).stem}.txt"
        lbl.parent.mkdir(parents=True, exist_ok=True)
        lbl.write_text(text, encoding="utf-8")

    # vidA：600 图，~500 `on` + ~100 `occluded` —— 必然进 `train`（含 `occluded` 供给）
    for i in range(600):
        fn = f"va_{i:03d}.jpg"
        _img(fn, i)
        _lbl(fn, "3 0.5 0.5 0.2 0.2\n" if i >= 500 else "0 0.5 0.5 0.2 0.2\n")
        csv_rows.append(f"images/{fn},{fn},vidA,{i}")

    # vidB / vidC：各 40 图 `occluded`，约束迫使二者落入不同 split
    for i in range(40):
        fn = f"vb_{i:03d}.jpg"
        _img(fn, i + 1)
        _lbl(fn, "3 0.5 0.5 0.2 0.2\n")
        csv_rows.append(f"images/{fn},{fn},vidB,{i}")
    for i in range(40):
        fn = f"vc_{i:03d}.jpg"
        _img(fn, i + 2)
        _lbl(fn, "3 0.5 0.5 0.2 0.2\n")
        csv_rows.append(f"images/{fn},{fn},vidC,{i}")

    # 单图源（无 `video_id`）：150 `damaged` + 100 `off` + 30 `on`
    for i in range(150):
        fn = f"sd_{i:03d}.jpg"
        _img(fn, i + 3)
        _lbl(fn, "2 0.5 0.5 0.2 0.2\n")
    for i in range(100):
        fn = f"so_{i:03d}.jpg"
        _img(fn, i + 4)
        _lbl(fn, "1 0.5 0.5 0.2 0.2\n")
    for i in range(30):
        fn = f"sn_{i:03d}.jpg"
        _img(fn, i + 5)
        _lbl(fn, "0 0.5 0.5 0.2 0.2\n")

    (src / "metadata.csv").write_text("\n".join(csv_rows) + "\n", encoding="utf-8-sig")

    out = tmp_path / "out"
    res = prepare_dataset(
        DatasetPrepConfig(
            sources=(DatasetSourceSpec("d1", src),),
            output_dir=out,
            link_mode="copy",
            seed=42,
            allow_small_groups=True,
            train_ratio=0.8,
            val_ratio=0.1,
            test_ratio=0.1,
        )
    )
    m = json.loads(res.manifest_path.read_text(encoding="utf-8"))

    # 1) 13 项防泄漏断言全部 0
    assert len(res.leakage_checks) == 13
    assert all(v == 0 for v in res.leakage_checks.values())

    # 2) 硬约束：三集全非空 + `occluded` 双集各 `>= 30`
    assert res.split_image_counts["train"] > 0
    assert res.split_image_counts["val"] > 0
    assert res.split_image_counts["test"] > 0
    assert m["split_objective"]["result"]["hard_ok"] is True
    assert m["split_objective"]["result"]["occluded_boxes_val"] >= 30
    assert m["split_objective"]["result"]["occluded_boxes_test"] >= 30

    # 3) `occluded` 也必须落在 `train`（非「灌进 val/test」）
    assert m["splits"]["train"]["class_box_counts"]["streetlight_occluded"] > 0

    # 4) 单图源 `damaged`（全为单图）在三集占比偏差 ≤ 3pp
    for sp in ("train", "val", "test"):
        assert m["class_proportion_deviation"]["streetlight_damaged"][sp]["deviation_pp"] <= 3.0

    # 5) manifest 新增三字段齐全
    assert "class_proportion_deviation" in m
    assert "split_assignment" in m
    assert "split_objective" in m

    # 6) 变异杀灭：两个含 `occluded` 的 `video_id` 组必须落入不同 split（否则 ≥30 约束被击穿）
    sa = m["split_assignment"]
    assert sa["d1::video::vidB"] != sa["d1::video::vidC"], (
        "含 occluded 的两个组被分到同一集合，硬约束未生效"
    )


def test_lexicographic_split_mutation_kills_broken_objective(tmp_path: Path) -> None:
    """变异验证：同一 fixture 下，破坏 `occluded` 下限约束的分配必然违反硬约束."""
    src = tmp_path / "mut_src"
    src.mkdir(parents=True)
    names_yaml = (
        "names:\n  0: streetlight_on\n  1: streetlight_off\n"
        "  2: streetlight_damaged\n  3: streetlight_occluded\n"
    )
    (src / "data.yaml").write_text(names_yaml, encoding="utf-8")
    csv_rows = ["relative_path,file_name,video_id,frame_id"]

    def _img(stem: str) -> None:
        # 每图按文件名 sha256 生成唯一内容（否则 SHA256 会因字节相同去重）
        seed = int.from_bytes(__import__("hashlib").sha256(stem.encode()).digest()[:8], "big")
        rng = np.random.default_rng(seed)
        arr = rng.integers(0, 256, (220, 220, 3), dtype=np.uint8)
        dst = src / "images" / stem
        dst.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(arr).save(dst, format="JPEG", quality=90)

    def _lbl(stem: str, text: str) -> None:
        lbl = src / "labels" / f"{Path(stem).stem}.txt"
        lbl.parent.mkdir(parents=True, exist_ok=True)
        lbl.write_text(text, encoding="utf-8")

    # 把全部 `occluded` 塞进一个大组（模拟「若只灌入一集」的退化解）
    for i in range(80):
        fn = f"oa_{i:03d}.jpg"
        _img(fn)
        _lbl(fn, "3 0.5 0.5 0.2 0.2\n")
        csv_rows.append(f"images/{fn},{fn},vidO,{i}")
    # 另一组仍含 `occluded`（作为「应被分走」的组）
    for i in range(45):
        fn = f"ob_{i:03d}.jpg"
        _img(fn)
        _lbl(fn, "3 0.5 0.5 0.2 0.2\n")
        csv_rows.append(f"images/{fn},{fn},vidP,{i}")
    for i in range(60):
        fn = f"mx_{i:03d}.jpg"
        _img(fn)
        _lbl(fn, f"{i % 3} 0.5 0.5 0.2 0.2\n")

    (src / "metadata.csv").write_text("\n".join(csv_rows) + "\n", encoding="utf-8-sig")

    out = tmp_path / "out"
    res = prepare_dataset(
        DatasetPrepConfig(
            sources=(DatasetSourceSpec("d1", src),),
            output_dir=out,
            link_mode="copy",
            seed=42,
            allow_small_groups=True,
            train_ratio=0.8,
            val_ratio=0.1,
            test_ratio=0.1,
        )
    )
    m = json.loads(res.manifest_path.read_text(encoding="utf-8"))
    # 硬约束必须成立：两个集合都不会出现 0 `occluded`
    assert m["split_objective"]["result"]["occluded_boxes_val"] >= 30
    assert m["split_objective"]["result"]["occluded_boxes_test"] >= 30
    sa = m["split_assignment"]
    # vidO/vidP 两组含 `occluded`，不得被同时塞进同一个 val/test（若算法坏则同集）
    val_vids = {k for k, v in sa.items() if v == "val"}
    test_vids = {k for k, v in sa.items() if v == "test"}
    assert not ({"d1::video::vidO", "d1::video::vidP"} <= val_vids)
    assert not ({"d1::video::vidO", "d1::video::vidP"} <= test_vids)


def test_eda_native_pixels_kept_capped_at_one_and_mixed_resolutions(tmp_path: Path) -> None:
    """Mixed 640x640 + 1280x720: `native_pixels_kept <= 1.0` and all 3 scale metrics consistent."""
    dims = [(640, 640), (640, 640), (1280, 720), (1280, 720)]
    bw = [20.0, 24.0, 16.0, 18.0]
    bh = [20.0, 24.0, 16.0, 18.0]

    r1280 = _imgsz_small_object_retention(
        bw, bh, 1280, native_dims=dims, default_w=640, default_h=640
    )
    r1024 = _imgsz_small_object_retention(
        bw, bh, 1024, native_dims=dims, default_w=1280, default_h=720
    )

    assert r1280["mode_letterbox_scale"] == 2.0
    assert r1280["letterbox_scale"] == 1.5
    assert r1280["native_pixels_kept"] == 1.0
    assert r1280["mode_native_pixels_kept"] == 1.0
    assert r1280["downsampled_boxes_ratio"] == 0.0
    assert r1280["upscaled_boxes_ratio"] == 0.5

    assert r1024["mode_letterbox_scale"] == 0.8
    assert r1024["letterbox_scale"] == 1.2
    assert r1024["native_pixels_kept"] == 0.82
    assert r1024["native_pixels_kept"] <= 1.0
    assert r1024["downsampled_boxes_ratio"] == 0.5
    assert r1024["upscaled_boxes_ratio"] == 0.5

    ds_root = tmp_path / "eda_mix"
    (ds_root / "images").mkdir(parents=True, exist_ok=True)
    (ds_root / "labels").mkdir(parents=True, exist_ok=True)
    (ds_root / "data.yaml").write_text("names:\n  0: streetlight_on\n", encoding="utf-8")
    for idx, (w, h) in enumerate([(640, 640), (640, 640), (1280, 720)], start=1):
        _write_solid_pattern_img(
            ds_root / "images" / f"m_{idx}.jpg", width=w, height=h, pattern="bars"
        )
        (ds_root / "labels" / f"m_{idx}.txt").write_text("0 0.5 0.5 0.02 0.02\n", encoding="utf-8")

    eda_out = tmp_path / "eda_out"
    report: dict[str, Any] = run_eda(
        [("mix", ds_root)], output_dir=eda_out, brightness_sample_size=0
    )
    cmp = report["imgsz_comparison"]
    assert cmp["kept_1280"] <= 1.0
    assert cmp["kept_1024"] <= 1.0
    assert cmp["detail_1280"]["native_pixels_kept"] <= 1.0
    assert cmp["detail_1024"]["native_pixels_kept"] <= 1.0
    assert cmp["detail_1280"]["downsampled_boxes_ratio"] == 0.0
    assert (eda_out / "dataset_eda.json").is_file()
    assert (eda_out / "dataset-eda-report.md").is_file()


def test_vision_init_pep562_lazy_export_does_not_import_ultralytics() -> None:
    """Subprocess importing `tomori.vision` + ETL/EDA never loads ultralytics/torch."""
    probe = """
import sys
import tomori.vision as tv
assert "ultralytics" not in sys.modules, "eagerly imported ultralytics"
assert "torch" not in sys.modules, "eagerly imported torch"
assert "tomori.vision.dataset_prep" not in sys.modules
assert "tomori.vision.eda" not in sys.modules
assert "tomori.vision.trainer" not in sys.modules
_ = tv.DatasetPrepConfig
_ = tv.DatasetFacts
assert "tomori.vision.dataset_prep" in sys.modules
assert "tomori.vision.eda" in sys.modules
assert "tomori.vision.trainer" not in sys.modules
assert "ultralytics" not in sys.modules
assert "torch" not in sys.modules
print("PEP562_LAZY_OK")
"""
    proc = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    assert "PEP562_LAZY_OK" in proc.stdout
