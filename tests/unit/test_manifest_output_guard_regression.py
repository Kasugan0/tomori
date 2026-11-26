"""`prepare_dataset` refuses nested directories in any of the six split dirs.

Fails fast before touching anything: an existing stale file in an earlier split
dir must survive when a later split dir contains a nested directory (empty or a
symlink to a directory). Flat stale files are cleaned and regenerated normally.
"""

import json
from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]

from tests.unit.test_vision_etl_and_eda import _seed_minimal_source
from tomori.vision.dataset_prep import DatasetPrepConfig, DatasetSourceSpec, prepare_dataset

_SPECS = [
    ("train", "sample_0", "0 0.500000 0.500000 0.200000 0.300000\n"),
    ("val", "sample_1", "1 0.25 0.25 0.20 0.20\n"),
    ("test", "sample_2", "2 0.70 0.70 0.20 0.20\n"),
]
_NAMES = {0: "streetlight_on", 1: "streetlight_off", 2: "streetlight_damaged"}


def _cfg(src: Path, out: Path) -> DatasetPrepConfig:
    return DatasetPrepConfig(
        sources=(DatasetSourceSpec(name="d1", root_dir=src),),
        output_dir=out,
        dedupe_phash=False,
        allow_small_groups=True,
    )


def _split_dirs(out: Path) -> list[Path]:
    return [out / sub / split for split in ("train", "val", "test") for sub in ("images", "labels")]


@pytest.mark.parametrize("nested_sub", ["labels/test", "images/val", "labels/val", "images/test"])
@pytest.mark.parametrize("nested_kind", ["empty_dir", "dir_symlink"])
def test_nested_dir_in_split_fails_before_any_cleanup(
    tmp_path: Path, nested_sub: str, nested_kind: str
) -> None:
    src = tmp_path / "src"
    _seed_minimal_source(src, specs=_SPECS, yaml_names=_NAMES)
    out = tmp_path / "out"
    stale_paths = [d / "stale.txt" for d in _split_dirs(out)]
    for p in stale_paths:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("keep me", encoding="utf-8")

    nested = out / nested_sub / "nested_thing"
    if nested_kind == "empty_dir":
        nested.mkdir(parents=True)
    else:
        real_dir = tmp_path / "real_dir"
        real_dir.mkdir()
        (real_dir / "inner.jpg").write_bytes(b"junk")
        nested.parent.mkdir(parents=True, exist_ok=True)
        nested.symlink_to(real_dir, target_is_directory=True)

    with pytest.raises(RuntimeError, match="拒绝自动清理|嵌套子目录"):
        prepare_dataset(_cfg(src, out))

    # 全六个 split 旧文件全部保留，且嵌套物本身未被触碰
    for p in stale_paths:
        assert p.read_text(encoding="utf-8") == "keep me"
    if nested_kind == "empty_dir":
        assert nested.is_dir()
    else:
        assert nested.is_symlink() and (real_dir / "inner.jpg").is_file()
    assert not (out / "prep_manifest.json").exists()
    assert not (out / "data.yaml").exists()


def test_flat_stale_files_cleaned_and_regenerated(tmp_path: Path) -> None:
    src = tmp_path / "src"
    _seed_minimal_source(src, specs=_SPECS, yaml_names=_NAMES)
    out = tmp_path / "out"
    stale_paths = [d / "stale.txt" for d in _split_dirs(out)]
    for p in stale_paths:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("old", encoding="utf-8")

    res = prepare_dataset(_cfg(src, out))
    assert res.total_kept_images == 3
    for p in stale_paths:
        assert not p.exists()
    assert (out / "prep_manifest.json").is_file()
    manifest = json.loads((out / "prep_manifest.json").read_text(encoding="utf-8"))
    assert sum(split["images"] for split in manifest["splits"].values()) == 3


@pytest.mark.parametrize("link_target", ["images", "labels", "images/train", "labels/test"])
def test_split_root_symlink_fails_and_preserves_external_target(
    tmp_path: Path, link_target: str
) -> None:
    """`images`/`labels` 或其 split 根为目录符号链接 → fail-fast，外部目标分毫不动。"""
    src = tmp_path / "src"
    _seed_minimal_source(src, specs=_SPECS, yaml_names=_NAMES)
    out = tmp_path / "out"
    external = tmp_path / "external" / link_target.replace("/", "_")
    (external / "sentinel").mkdir(parents=True)
    (external / "sentinel" / "inner.txt").write_text("sentinel", encoding="utf-8")
    (external / "loose.txt").write_text("sentinel2", encoding="utf-8")

    link = out / link_target
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(external, target_is_directory=True)

    with pytest.raises(RuntimeError):
        prepare_dataset(_cfg(src, out))

    assert link.is_symlink()
    assert (external / "sentinel" / "inner.txt").read_text(encoding="utf-8") == "sentinel"
    assert (external / "loose.txt").read_text(encoding="utf-8") == "sentinel2"


@pytest.mark.parametrize("entry", ["data.yaml", "prep_manifest.json"])
def test_output_file_symlink_fails_and_preserves_external_file(tmp_path: Path, entry: str) -> None:
    """`data.yaml`/`prep_manifest.json` 为外部文件符号链接 → fail-fast，不得覆写外部文件。"""
    src = tmp_path / "src"
    _seed_minimal_source(src, specs=_SPECS, yaml_names=_NAMES)
    out = tmp_path / "out"
    out.mkdir()
    external_file = tmp_path / "external" / entry
    external_file.parent.mkdir()
    external_file.write_text("do-not-touch", encoding="utf-8")
    link = out / entry
    link.symlink_to(external_file)

    with pytest.raises(RuntimeError):
        prepare_dataset(_cfg(src, out))

    assert link.is_symlink()
    assert external_file.read_text(encoding="utf-8") == "do-not-touch"


def test_regular_file_in_later_split_blocks_without_partial_cleanup(tmp_path: Path) -> None:
    """后序 split 根为普通文件 → fail-fast；前序 split 旧文件不得被提前清掉。"""
    src = tmp_path / "src"
    _seed_minimal_source(src, specs=_SPECS, yaml_names=_NAMES)
    out = tmp_path / "out"
    stale_paths = [d / "stale.txt" for d in _split_dirs(out)]
    for p in stale_paths:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("keep me", encoding="utf-8")
    # 把最后一个 split 根替换为普通文件（目录非空时先删目录再放文件）
    blocker_dir = out / "labels" / "test"
    blocker_dir.joinpath("stale.txt").unlink()
    blocker_dir.rmdir()
    blocker_dir.write_text("i am a file", encoding="utf-8")

    with pytest.raises(RuntimeError):
        prepare_dataset(_cfg(src, out))

    for p in stale_paths[:-1]:
        assert p.read_text(encoding="utf-8") == "keep me"
    assert blocker_dir.read_text(encoding="utf-8") == "i am a file"


@pytest.mark.parametrize("entry", ["data.yaml", "prep_manifest.json"])
def test_output_file_hardlink_fails_and_preserves_external_file(tmp_path: Path, entry: str) -> None:
    """metadata 与外部文件同 inode 硬链接（st_nlink>1）→ fail-fast，不得截断外部内容。"""
    src = tmp_path / "src"
    _seed_minimal_source(src, specs=_SPECS, yaml_names=_NAMES)
    out = tmp_path / "out"
    out.mkdir()
    external_file = tmp_path / "external" / entry
    external_file.parent.mkdir()
    external_file.write_text("hardlinked-sentinel", encoding="utf-8")
    link = out / entry
    link.hardlink_to(external_file)
    sentinels = [directory / "stale.txt" for directory in _split_dirs(out)]
    for sentinel in sentinels:
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.write_text("preserve-split", encoding="utf-8")

    with pytest.raises(RuntimeError):
        prepare_dataset(_cfg(src, out))

    assert link.samefile(external_file)
    assert external_file.read_text(encoding="utf-8") == "hardlinked-sentinel"
    assert link.read_text(encoding="utf-8") == "hardlinked-sentinel"
    for sentinel in sentinels:
        assert sentinel.read_text(encoding="utf-8") == "preserve-split"


def test_existing_plain_metadata_files_are_replaced(tmp_path: Path) -> None:
    """单链接既有 `data.yaml`/`prep_manifest.json` 属正常复用：被新内容替换且可解析。"""
    src = tmp_path / "src"
    _seed_minimal_source(src, specs=_SPECS, yaml_names=_NAMES)
    out = tmp_path / "out"
    out.mkdir()
    (out / "data.yaml").write_text("stale-yaml", encoding="utf-8")
    (out / "prep_manifest.json").write_text("stale-manifest", encoding="utf-8")

    res = prepare_dataset(_cfg(src, out))
    assert res.total_kept_images == 3
    data_yaml = (out / "data.yaml").read_text(encoding="utf-8")
    parsed_yaml = yaml.safe_load(data_yaml)
    assert parsed_yaml["names"] == {**_NAMES, 3: "streetlight_occluded"}
    assert parsed_yaml["path"] == str(out.resolve())
    manifest = json.loads((out / "prep_manifest.json").read_text(encoding="utf-8"))
    assert manifest["output_dir"] == str(out.resolve())
