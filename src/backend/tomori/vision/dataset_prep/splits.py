"""`dataset_prep` `_assign_group_splits`：组+单图混合字典序分层装箱（`Union-Find` 分组不变）."""

from __future__ import annotations

import itertools
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Final

from tomori.vision.dataset_prep.core import (
    _deterministic_hash,
    _ImageSample,
    _UnionFind,
)

# 分层装箱枚举上限：`enum_groups`（多图组）最多枚举 `3^k` 种分配，截断到此规模防组合爆炸。
_SPLIT_ENUM_LIMIT: Final[int] = 12
# `occluded`（类 id=3）在 val/test 各自需达到的框数下限；仅当全库 `occluded` 达到其 2 倍时启用。
_OCCLUDED_MIN_PER_SPLIT: Final[int] = 30


def _assign_group_splits(
    samples: list[_ImageSample],
    *,
    train_ratio: float,
    val_ratio: float,
    test_ratio: float,
    seed: int,
    allow_small_groups: bool,
    forbid_preaug_in_val_test: bool,
    occluded_min_per_split: int = _OCCLUDED_MIN_PER_SPLIT,
    enumeration_limit: int = _SPLIT_ENUM_LIMIT,
) -> dict[str, Any]:
    """组 + 单图混合的字典序三目标按类分层装箱（Union-Find 分组不变）.

    字典序目标：
      1. 硬约束——所有存在类别在 train/val/test 三集全非零；
         且若全库 `occluded` 框数 `>= 2 * occluded_min_per_split`，
         则要求 `occluded` 在 `val` 与 `test` 各 `>= occluded_min_per_split`
         （迫使含 `occluded` 的大组落入不同集合）；
      2. 主目标——最小化 `Σ|class_box_share - ratio|`（各类别框数占比偏差和）；
      3. 次目标——最小化 `Σ|img_share - ratio|`；
      4. 决胜——在满足硬约束下，倾向把含较多 `occluded` 的大组放入 `test`。

    返回切分目标信息（`split_assignment` / `objective`），供 `prep_manifest.json` 落盘。
    """
    n = len(samples)
    if n == 0:
        raise ValueError("清洗后无任何有效样本，无法切分")

    uf = _UnionFind(n)
    by_video_key: dict[str, int] = {}
    by_rf_key: dict[str, int] = {}
    by_phash_cluster: dict[int, int] = {}
    by_sha: dict[str, int] = {}

    for idx, s in enumerate(samples):
        if s.video_id is not None:
            vkey = f"{s.source_name}::video::{s.video_id}"
            prev = by_video_key.get(vkey)
            if prev is not None:
                uf.union(prev, idx)
            else:
                by_video_key[vkey] = idx
        if s.rf_orig_stem is not None:
            rkey = f"{s.source_name}::rf::{s.rf_orig_stem}"
            prev_r = by_rf_key.get(rkey)
            if prev_r is not None:
                uf.union(prev_r, idx)
            else:
                by_rf_key[rkey] = idx
        prev_c = by_phash_cluster.get(s.phash_cluster)
        if prev_c is not None:
            uf.union(prev_c, idx)
        else:
            by_phash_cluster[s.phash_cluster] = idx
        prev_sha = by_sha.get(s.content_sha256)
        if prev_sha is not None:
            uf.union(prev_sha, idx)
        else:
            by_sha[s.content_sha256] = idx

    groups_map: dict[int, list[_ImageSample]] = defaultdict(list)
    for idx, s in enumerate(samples):
        groups_map[uf.find(idx)].append(s)

    # 组特征汇总
    @dataclass
    class _GroupMeta:
        gid: int
        members: list[_ImageSample]
        size: int
        cls_boxes: Counter[int]
        val_test_eligible: bool
        tie_key: str

    groups: list[_GroupMeta] = []
    total_cls_boxes: Counter[int] = Counter()
    total_images = len(samples)

    for gid, members in groups_map.items():
        cls_boxes: Counter[int] = Counter()
        for m in members:
            for cid, *_ in m.boxes:
                cls_boxes[cid] += 1
                total_cls_boxes[cid] += 1
        eligible = True
        if forbid_preaug_in_val_test:
            eligible = all(m.val_test_eligible for m in members)
        groups.append(
            _GroupMeta(
                gid=gid,
                members=members,
                size=len(members),
                cls_boxes=cls_boxes,
                val_test_eligible=eligible,
                tie_key=_deterministic_hash(seed, members[0].group_key),
            )
        )

    eligible_groups = [g for g in groups if g.val_test_eligible]
    if len(groups) < 3 and not allow_small_groups:
        raise ValueError(
            f"独立防泄漏分组仅 {len(groups)} 个（< 3），无法同时保证 train/val/test 非空"
        )
    if len(eligible_groups) < 2 and not allow_small_groups:
        raise ValueError(
            f"可用于 val/test 的非增广独立分组仅 {len(eligible_groups)} 个（< 2），"
            "无法保证 val/test 纯净非空"
        )

    ratios = {"train": train_ratio, "val": val_ratio, "test": test_ratio}
    splits_order: tuple[str, str, str] = ("train", "val", "test")
    total_images = len(samples)
    present_classes = {cid for cid, cnt in total_cls_boxes.items() if cnt > 0}

    # --- 1) 基础状态：预增广组（`val_test_eligible=False`）全部锁入 `train` ---
    base_imgs = {"train": 0, "val": 0, "test": 0}
    base_cls: dict[str, Counter[int]] = {s: Counter() for s in splits_order}
    for g in sorted(
        (g for g in groups if not g.val_test_eligible),
        key=lambda g: (-g.size, g.tie_key),
    ):
        base_imgs["train"] += g.size
        for cid, cnt in g.cls_boxes.items():
            base_cls["train"][cid] += cnt

    # --- 2) 枚举池（多图组）与单图池 ---
    # 枚举池按「含最稀有类框数 → 组大小」降序截断到 `_SPLIT_ENUM_LIMIT`。
    rarest_cid = (
        min(present_classes, key=lambda c: (total_cls_boxes[c], c)) if present_classes else 0
    )
    enum_groups = sorted(
        (g for g in groups if g.val_test_eligible and g.size > 1),
        key=lambda g: (-g.cls_boxes.get(rarest_cid, 0), -g.size, g.tie_key),
    )[:_SPLIT_ENUM_LIMIT]
    enum_gids = {g.gid for g in enum_groups}
    # 单图池：所有可入 val/test 但未进枚举池的组（单图组 + 被截断的多图组），按稀有度加权排序。
    single_groups = sorted(
        (g for g in groups if g.val_test_eligible and g.gid not in enum_gids),
        key=lambda g: (
            -sum(
                g.cls_boxes.get(cid, 0) / max(1, total_cls_boxes.get(cid, 1)) for cid in g.cls_boxes
            ),
            -g.size,
            g.tie_key,
        ),
    )

    # --- 3) `occluded` 下限需求：全库 ≥ `2 * occluded_min_per_split` 时强制双集各 ≥ 该值 ---
    min_occl = (
        occluded_min_per_split if total_cls_boxes.get(3, 0) >= 2 * occluded_min_per_split else 0
    )
    occl_in_singles = sum(g.cls_boxes.get(3, 0) for g in single_groups)

    def _greedy_fill(
        imgs: dict[str, int],
        cls: dict[str, Counter[int]],
        out_assign: dict[int, str],
    ) -> None:
        """稀有度加权贪心：把单图池逐组分配到三集，使各类别框数占比最贴近 `ratio`."""
        for g in single_groups:
            best_sp = splits_order[0]
            best_key: tuple[float, float] | None = None
            for sp in splits_order:
                d_cls = 0.0
                for cid in g.cls_boxes:
                    tot = total_cls_boxes[cid]
                    if tot <= 0:
                        continue
                    before = abs(cls[sp][cid] / tot - ratios[sp])
                    after = abs((cls[sp][cid] + g.cls_boxes[cid]) / tot - ratios[sp])
                    d_cls += after - before
                before_i = abs(imgs[sp] / total_images - ratios[sp])
                after_i = abs((imgs[sp] + g.size) / total_images - ratios[sp])
                key = (d_cls, after_i - before_i)
                if best_key is None or key < best_key:
                    best_key, best_sp = key, sp
            out_assign[g.gid] = best_sp
            imgs[best_sp] += g.size
            for cid, cnt in g.cls_boxes.items():
                cls[best_sp][cid] += cnt

    def _deviation(cls: dict[str, Counter[int]], imgs: dict[str, int]) -> tuple[float, float]:
        """返回 `(Σ|类框数占比 - ratio|, Σ|图数占比 - ratio|)`（百分点）."""
        cdev = sum(
            abs(cls[sp][c] / tot - ratios[sp]) * 100.0
            for c, tot in total_cls_boxes.items()
            if tot > 0
            for sp in splits_order
        )
        idev = sum(abs(imgs[sp] / total_images - ratios[sp]) * 100.0 for sp in splits_order)
        return cdev, idev

    # --- 4) 枚举 `enum_groups` 全部 `3^k` 分配，字典序求最优 ---
    k = len(enum_groups)
    n_eval = n_feasible = 0
    best_key: tuple[int, int, int, float, float, int] | None = None
    best_combo: tuple[int, ...] | None = None
    best_cls: dict[str, Counter[int]] = {}
    best_assign: dict[int, str] = {}
    for combo in itertools.product(range(3), repeat=k):
        n_eval += 1
        m_occl = [0, 0, 0]
        for i, sp_idx in enumerate(combo):
            m_occl[sp_idx] += enum_groups[i].cls_boxes.get(3, 0)
        # occluded 可行性预检：val/test 各需 ≥min_occl 框（含单图池兜底）
        if min_occl > 0:
            need_v = max(0, min_occl - m_occl[1])
            need_t = max(0, min_occl - m_occl[2])
            if need_v + need_t > occl_in_singles:
                continue
        n_feasible += 1
        imgs = dict(base_imgs)
        cls: dict[str, Counter[int]] = {s: Counter(base_cls[s]) for s in splits_order}
        out_assign: dict[int, str] = {}
        for i, sp_idx in enumerate(combo):
            sp = splits_order[sp_idx]
            g = enum_groups[i]
            out_assign[g.gid] = sp
            imgs[sp] += g.size
            for cid, cnt in g.cls_boxes.items():
                cls[sp][cid] += cnt
        _greedy_fill(imgs, cls, out_assign)
        empty_splits = sum(1 for sp in splits_order if imgs[sp] == 0)
        missing_cells = sum(
            1 for c in present_classes for sp in splits_order if cls[sp].get(c, 0) == 0
        )
        hard_ok = (
            empty_splits == 0
            and missing_cells == 0
            and (
                min_occl == 0
                or (cls["val"].get(3, 0) >= min_occl and cls["test"].get(3, 0) >= min_occl)
            )
        )
        cdev, idev = _deviation(cls, imgs)
        tie = cls["test"].get(3, 0) - cls["val"].get(3, 0)
        key = (
            empty_splits,
            missing_cells,
            0 if hard_ok else 1,
            round(cdev, 4),
            round(idev, 4),
            -tie,
        )
        if best_key is None or key < best_key:
            best_key, best_combo, best_cls, best_assign = (
                key,
                combo,
                cls,
                out_assign,
            )

    if best_combo is None:
        raise RuntimeError(
            "分层切分无可行解：在给定组原子性约束下无法满足「三集四类全非零"
            + (f" 且 occluded 双集各 ≥{min_occl} 框」" if min_occl > 0 else "」")
        )

    # --- 5) 应用最优分配 ---
    for g in groups:
        sp = "train" if not g.val_test_eligible else best_assign.get(g.gid, "train")
        for m in g.members:
            m.split = sp

    # --- 6) 上报切分目标信息 ---
    split_assignment: dict[str, Any] = {
        enum_groups[i].members[0].group_key: splits_order[combo_i]
        for i, combo_i in enumerate(best_combo)
    }
    split_assignment["_single_groups"] = {
        "train": sum(1 for g in single_groups if best_assign.get(g.gid) == "train"),
        "val": sum(1 for g in single_groups if best_assign.get(g.gid) == "val"),
        "test": sum(1 for g in single_groups if best_assign.get(g.gid) == "test"),
    }
    return {
        "split_assignment": split_assignment,
        "objective": {
            "method": "group+single lexicographic stratified bin-packing",
            "targets": {"train": train_ratio, "val": val_ratio, "test": test_ratio},
            "hard_constraints": {
                "all_present_classes_nonzero_in_each_split": True,
                "occluded_min_per_split": min_occl,
            },
            "result": {
                "hard_ok": bool(best_key is not None and best_key[2] == 0),
                "empty_splits": best_key[0] if best_key else None,
                "missing_class_cells": best_key[1] if best_key else None,
                "class_box_deviation_pp": best_key[3] if best_key else None,
                "image_deviation_pp": best_key[4] if best_key else None,
                "occluded_boxes_val": best_cls["val"].get(3, 0),
                "occluded_boxes_test": best_cls["test"].get(3, 0),
                "combos_evaluated": n_eval,
                "combos_feasible": n_feasible,
            },
        },
    }
