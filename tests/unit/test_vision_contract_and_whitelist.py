"""Unit tests for `src/tomori/vision/`: whitelist, cv2.imdecode, D-30, and V1-V3 guards."""

from __future__ import annotations

import io
import json
import re
import struct
import zlib
from pathlib import Path
from typing import Any

import pytest
from PIL import Image
from pydantic import ValidationError

from tomori.vision import config, contract, errors, image_io, mapping, trainer, whitelist
from tomori.vision.baseline import RawDetection
from tomori.vision.client import VisionClient, validate_onnx_session
from tomori.vision.errors import VisionError
from tomori.vision.metrics import ImageQuality, RoiMetrics


def _make_static_bytes(fmt: str, size: tuple[int, int] = (32, 32)) -> bytes:
    buf = io.BytesIO()
    img = Image.new("RGB", size, (40, 80, 120))
    img.save(buf, format=fmt)
    return buf.getvalue()


def _make_animated_bytes(fmt: str) -> bytes:
    buf = io.BytesIO()
    frame1 = Image.new("RGB", (16, 16), (255, 0, 0))
    frame2 = Image.new("RGB", (16, 16), (0, 255, 0))
    frame1.save(buf, format=fmt, save_all=True, append_images=[frame2], duration=50, loop=0)
    return buf.getvalue()


def _make_minimal_png_with_dimensions(width: int, height: int) -> bytes:
    """Build a valid tiny PNG claiming `width x height` in IHDR so Pillow triggers 40MP guard."""

    def _chunk(tag: bytes, payload: bytes) -> bytes:
        crc = zlib.crc32(tag + payload) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + tag + payload + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    idat = zlib.compress(b"\x00\x00")
    return (
        b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", idat) + _chunk(b"IEND", b"")
    )


def _sample_roi_metrics(*, roi_v_p95: float = 215.0, contrast: float = 95.0) -> RoiMetrics:
    return RoiMetrics(
        roi_v_mean=180.0,
        roi_v_p95=roi_v_p95,
        bg_ring_v_median=max(10.0, roi_v_p95 - contrast),
        luminance_contrast=contrast,
        green_foliage_ratio=0.02,
        edge_density=0.05,
        roi_saturated_ratio=0.10,
    )


def _sample_quality(scene: str) -> ImageQuality:
    return ImageQuality(
        quality_grade="pass",
        laplacian_var=120.0,
        brightness_mean_v=30.0 if scene == "night" else 130.0,
        overexposed_ratio=0.0,
        underexposed_ratio=0.0,
        scene_lighting=scene,
        warnings=[],
    )


# ── 1. VISION_WEIGHTS_NOT_READY eliminated -> 503 VISION_DISABLED; P4 in SEVERITY_HINTS ──


def test_no_vision_weights_not_ready_and_p4_in_severity_hints() -> None:
    """Confirm `VISION_WEIGHTS_NOT_READY` is absent and `"P4"` is present in `SEVERITY_HINTS`."""
    assert "VISION_WEIGHTS_NOT_READY" not in errors.HTTP_STATUS_BY_CODE
    assert "VISION_WEIGHTS_NOT_READY" not in set(map(str, vars(errors).values()))
    assert "P4" in config.SEVERITY_HINTS
    assert config.SEVERITY_HINTS == ("none", "P1", "P2", "P3", "P4")


def test_disabled_or_strict_missing_weights_returns_503_vision_disabled(tmp_path: Path) -> None:
    """Disabled flag or `strict_custom` with missing weights must raise `503 VISION_DISABLED`."""
    jpg_bytes = _make_static_bytes("JPEG")

    disabled_settings = config.Settings(
        upload_dir=tmp_path,
        max_upload_bytes=config.MAX_UPLOAD_BYTES,
        yolo_weights_path=tmp_path / "missing.pt",
        yolo_inference_enabled=False,
        yolo_inference_mode="auto",
        yolo_img_size=1280,
        yolo_conf_threshold=0.25,
        yolo_device="cpu",
    )
    with pytest.raises(VisionError) as exc_disabled:
        VisionClient(disabled_settings).analyze_bytes(jpg_bytes, filename="ok.jpg")
    assert exc_disabled.value.http_status == 503
    assert exc_disabled.value.code == "VISION_DISABLED"
    assert exc_disabled.value.details["fallback"] == {
        "action": "MANUAL_REPORT",
        "resubmit_required": True,
    }

    strict_settings = config.Settings(
        upload_dir=tmp_path,
        max_upload_bytes=config.MAX_UPLOAD_BYTES,
        yolo_weights_path=tmp_path / "nonexistent_best.pt",
        yolo_inference_enabled=True,
        yolo_inference_mode="strict_custom",
        yolo_img_size=1280,
        yolo_conf_threshold=0.25,
        yolo_device="cpu",
    )
    with pytest.raises(VisionError) as exc_strict:
        VisionClient(strict_settings).analyze_bytes(jpg_bytes, filename="ok.jpg")
    assert exc_strict.value.http_status == 503
    assert exc_strict.value.code == "VISION_DISABLED"


def test_onnx_shape_1280_passes_and_640_or_1024_rejected_with_503(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ONNX gate accepts `[1,3,1280,1280]->[1,300,6]` and rejects `640`/`1024` with 503."""
    import json

    monkeypatch.delenv("YOLO_IMG_SIZE", raising=False)
    monkeypatch.chdir(tmp_path)  # 隔离 `./vision/reports/streetlight_v1/metrics.json` 相对路径探测
    assert config.EXPECTED_YOLO_IMG_SIZE == 1280
    assert config.load_settings().yolo_img_size == 1280

    class _Node:
        def __init__(self, name: str, shape: list[int]) -> None:
            self.name = name
            self.shape = shape

    class _FakeOnnxSession:
        def __init__(self, in_shape: list[int], out_shape: list[int]) -> None:
            self._in_shape = in_shape
            self._out_shape = out_shape

        def get_inputs(self) -> list[_Node]:
            return [_Node("images", self._in_shape)]

        def get_outputs(self) -> list[_Node]:
            return [_Node("output0", self._out_shape)]

    ok_metrics = tmp_path / "metrics_ok.json"
    ok_metrics.write_text(
        json.dumps({"imgsz": 1280, "names": list(config.RAW_CLASSES)}),
        encoding="utf-8",
    )
    in_name, out_name = validate_onnx_session(
        _FakeOnnxSession([1, 3, 1280, 1280], [1, 300, 6]),
        metrics_path=ok_metrics,
    )
    assert (in_name, out_name) == ("images", "output0")

    for bad_imgsz in (640, 1024):
        with pytest.raises(VisionError) as exc_shape:
            validate_onnx_session(
                _FakeOnnxSession([1, 3, bad_imgsz, bad_imgsz], [1, 300, 6]),
                metrics_path=ok_metrics,
            )
        assert exc_shape.value.http_status == 503
        assert exc_shape.value.code == "VISION_DISABLED"
        assert isinstance(exc_shape.value, ValueError)
        assert exc_shape.value.details["error_kind"] == "INVALID_ARGUMENT"

    stale_metrics = tmp_path / "metrics_1024.json"
    stale_metrics.write_text(
        json.dumps({"imgsz": 1024, "names": list(config.RAW_CLASSES)}),
        encoding="utf-8",
    )
    with pytest.raises(VisionError) as exc_meta:
        validate_onnx_session(
            _FakeOnnxSession([1, 3, 1280, 1280], [1, 300, 6]),
            metrics_path=stale_metrics,
        )
    assert exc_meta.value.http_status == 503
    assert exc_meta.value.code == "VISION_DISABLED"
    assert exc_meta.value.details["error_kind"] == "INVALID_ARGUMENT"


def test_validate_metrics_names_nc3_prefix_subset(tmp_path: Path) -> None:
    """`metrics.json` `names` 必须 `config.RAW_CLASSES` 前缀子集（`nc=3`/`4`/`2`/`1` 合法）；
    跳类/`len>4`/非法名/空 `names` 拒绝（`occluded` 弃用 `nc=3` 兼容）."""
    from tomori.vision.client import _validate_metrics_names

    ok_all4 = tmp_path / "names_all4.json"
    ok_all4.write_text(json.dumps({"names": list(config.RAW_CLASSES)}), encoding="utf-8")
    ok_nc3 = tmp_path / "names_nc3.json"
    ok_nc3.write_text(
        json.dumps(
            {"names": {0: "streetlight_on", 1: "streetlight_off", 2: "streetlight_damaged"}}
        ),
        encoding="utf-8",
    )
    ok_nc1 = tmp_path / "names_nc1.json"
    ok_nc1.write_text(json.dumps({"names": ["streetlight_on"]}), encoding="utf-8")

    # 合法：list/dict `nc` 变体全部 `RAW_CLASSES` 前缀子集
    for path in (ok_all4, ok_nc3, ok_nc1):
        _validate_metrics_names(json.loads(path.read_text())["names"])

    # 非法：跳类/`len>4`/非法名/空 `names`
    cases: dict[str, dict[str, Any]] = {
        "skip_damaged": {"names": ["streetlight_on", "streetlight_off", "streetlight_occluded"]},
        "skip_off": {
            "names": {
                0: "streetlight_on",
                1: "streetlight_damaged",
                2: "streetlight_occluded",
            }
        },
        "over4": {"names": ["a", "b", "c", "d", "e"]},
        "bad_name": {"names": ["streetlight_bad"]},
        "empty": {"names": []},
    }
    for label, payload in cases.items():
        with pytest.raises((VisionError, ValueError)) as exc:
            _validate_metrics_names(payload["names"])
        if hasattr(exc.value, "code"):
            assert exc.value.code == "VISION_DISABLED", label
        assert "INVALID_ARGUMENT" in str(exc.value) or "INVALID_ARGUMENT" in repr(
            getattr(exc.value, "details", {})
        ), label

    # `dict` 键非法（非数字）
    with pytest.raises((VisionError, ValueError)) as exc_dict:
        _validate_metrics_names({"bad": "x"})
    assert "INVALID_ARGUMENT" in str(exc_dict.value) or "INVALID_ARGUMENT" in repr(
        getattr(exc_dict.value, "details", {})
    )


def test_train_config_defaults_and_redline_guards(tmp_path: Path) -> None:
    """TrainConfig enforces `yolo26s.pt`, `imgsz=1280`, `epochs=500`, `patience=20`, aug limits."""
    data_yaml = tmp_path / "data.yaml"
    cfg = trainer.TrainConfig(data_yaml=data_yaml)

    assert cfg.base_model.name == "yolo26s.pt"
    assert cfg.imgsz == 1280
    assert cfg.epochs == 500
    assert cfg.patience == 20
    assert cfg.flipud == 0.0
    assert cfg.hsv_v == 0.25
    assert cfg.hsv_s == 0.4


@pytest.mark.parametrize(
    ("overrides", "expected_message"),
    [
        (
            {"base_model": Path("/root/autodl-tmp/tomori/weights/yolo26n.pt")},
            "D-06 硬约束",
        ),
        ({"imgsz": 640}, "D-28 硬约束"),
        ({"imgsz": 1024}, "D-28 硬约束"),
        ({"flipud": 0.5}, "增强禁区：路灯训练必须保持 flipud=0.0"),
        ({"hsv_v": 0.4}, "增强禁区：hsv_v 必须 <= 0.25"),
        ({"hsv_s": 0.7}, "增强禁区：hsv_s 必须 <= 0.4"),
    ],
)
def test_train_config_rejects_forbidden_model_imgsz_and_augmentations(
    tmp_path: Path,
    overrides: dict[str, object],
    expected_message: str,
) -> None:
    """TrainConfig must raise the exact guard message (not just any ValueError)."""
    kwargs: dict[str, object] = {"data_yaml": tmp_path / "data.yaml", **overrides}
    with pytest.raises(ValueError, match=re.escape(expected_message)):
        trainer.TrainConfig(**kwargs)  # type: ignore[arg-type]


# ── 2. Four-layer whitelist & priority order ──


@pytest.mark.parametrize(
    ("fmt", "filename", "expected_mime"),
    [
        ("JPEG", "lamp.jpg", "image/jpeg"),
        ("PNG", "lamp.png", "image/png"),
        ("WEBP", "lamp.webp", "image/webp"),
    ],
)
def test_whitelist_accepts_static_jpeg_png_webp(
    fmt: str,
    filename: str,
    expected_mime: str,
) -> None:
    """Static JPEG/PNG/WebP images pass all 4 whitelist layers."""
    raw = _make_static_bytes(fmt, size=(48, 24))
    res = whitelist.validate_bytes(raw, filename=filename)
    assert res.detected_format == fmt
    assert res.detected_mime == expected_mime
    assert res.width == 48
    assert res.height == 24


def test_whitelist_animated_images_return_415_unsupported_image_format() -> None:
    """Animated GIF, APNG, and animated WebP all resolve to `415 UNSUPPORTED_IMAGE_FORMAT`."""
    gif_bytes = _make_animated_bytes("GIF")
    with pytest.raises(VisionError) as exc_gif:
        whitelist.validate_bytes(gif_bytes, filename="anim.gif")
    assert exc_gif.value.http_status == 415
    assert exc_gif.value.code == "UNSUPPORTED_IMAGE_FORMAT"

    apng_bytes = _make_animated_bytes("PNG")
    with pytest.raises(VisionError) as exc_apng:
        whitelist.validate_bytes(apng_bytes, filename="anim.png")
    assert exc_apng.value.http_status == 415
    assert exc_apng.value.code == "UNSUPPORTED_IMAGE_FORMAT"

    # Disguised `.jpg` extension with APNG bytes -> step 4 catches as UNSUPPORTED_IMAGE_FORMAT
    with pytest.raises(VisionError) as exc_apng_jpg:
        whitelist.validate_bytes(apng_bytes, filename="disguised_apng.jpg")
    assert exc_apng_jpg.value.http_status == 415
    assert exc_apng_jpg.value.code == "UNSUPPORTED_IMAGE_FORMAT"

    webp_anim_bytes = _make_animated_bytes("WEBP")
    with pytest.raises(VisionError) as exc_webp:
        whitelist.validate_bytes(webp_anim_bytes, filename="anim.webp")
    assert exc_webp.value.http_status == 415
    assert exc_webp.value.code == "UNSUPPORTED_IMAGE_FORMAT"


def test_whitelist_videos_and_streams_return_415_media_video_stream_forbidden() -> None:
    """Video containers (even `.jpg` alias) and stream URLs -> 415 MEDIA_VIDEO_STREAM_FORBIDDEN."""
    mp4_header = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"
    with pytest.raises(VisionError) as exc_mp4:
        whitelist.validate_bytes(mp4_header, filename="fake.jpg")
    assert exc_mp4.value.http_status == 415
    assert exc_mp4.value.code == "MEDIA_VIDEO_STREAM_FORBIDDEN"

    mkv_header = b"\x1a\x45\xdf\xa3" + b"\x00" * 16
    with pytest.raises(VisionError) as exc_mkv:
        whitelist.validate_bytes(mkv_header, filename="fake.webp")
    assert exc_mkv.value.http_status == 415
    assert exc_mkv.value.code == "MEDIA_VIDEO_STREAM_FORBIDDEN"

    avi_header = b"RIFF\x24\x00\x00\x00AVI " + b"\x00" * 8
    with pytest.raises(VisionError) as exc_avi:
        whitelist.validate_bytes(avi_header, filename="fake.png")
    assert exc_avi.value.http_status == 415
    assert exc_avi.value.code == "MEDIA_VIDEO_STREAM_FORBIDDEN"

    for stream_input in ("rtsp://10.0.0.1/cam1", "https://example.com/live/cam.m3u8?token=1"):
        with pytest.raises(VisionError) as exc_stream:
            whitelist.reject_stream_url(stream_input)
        assert exc_stream.value.http_status == 415
        assert exc_stream.value.code == "MEDIA_VIDEO_STREAM_FORBIDDEN"


def test_whitelist_size_exceeded_returns_413_and_beats_video_and_pixels() -> None:
    """> 15 MiB -> `413 IMAGE_SIZE_EXCEEDED`, evaluated before format/video/pixel checks."""
    limit = 15 * 1024 * 1024
    assert limit == config.MAX_UPLOAD_BYTES

    oversized_mp4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * (limit + 1 - 12)
    with pytest.raises(VisionError) as exc_info:
        whitelist.validate_bytes(oversized_mp4, filename="too_big.jpg")
    assert exc_info.value.http_status == 413
    assert exc_info.value.code == "IMAGE_SIZE_EXCEEDED"


def test_whitelist_pixel_limit_40mp_returns_422_and_restores_pil_limit() -> None:
    """> 40,000,000 px -> `422 IMAGE_PIXEL_LIMIT_EXCEEDED`, restoring `Image.MAX_IMAGE_PIXELS`."""
    assert config.MAX_IMAGE_PIXELS == 40_000_000
    prev_pil_limit = Image.MAX_IMAGE_PIXELS

    # 8001 x 5000 = 40,005,000 > 40,000,000 (while both sides <= MAX_IMAGE_EDGE 12,000)
    bomb_png = _make_minimal_png_with_dimensions(8001, 5000)
    with pytest.raises(VisionError) as exc_bomb:
        whitelist.validate_bytes(bomb_png, filename="bomb.png")
    assert exc_bomb.value.http_status == 422
    assert exc_bomb.value.code == "IMAGE_PIXEL_LIMIT_EXCEEDED"
    assert prev_pil_limit == Image.MAX_IMAGE_PIXELS

    # Single edge > 12,000 also triggers 422 IMAGE_PIXEL_LIMIT_EXCEEDED
    wide_png = _make_minimal_png_with_dimensions(12_001, 10)
    with pytest.raises(VisionError) as exc_edge:
        whitelist.validate_bytes(wide_png, filename="too_wide.png")
    assert exc_edge.value.http_status == 422
    assert exc_edge.value.code == "IMAGE_PIXEL_LIMIT_EXCEEDED"


# ── 3. cv2.imdecode dual-path isolation (0-byte & corrupt bytes never crash) ──


def test_cv2_imdecode_dual_path_zero_bytes_and_corrupt_bytes() -> None:
    """`cv2.imdecode`: 0-byte -> 422 VALIDATION_ERROR; corrupt -> 415 UNSUPPORTED_IMAGE_FORMAT."""
    # Path 1: 0 bytes intercepted before cv2.imdecode (never raises raw cv2.error !buf.empty())
    with pytest.raises(VisionError) as exc_empty:
        image_io.decode_imdecode_bgr(b"")
    assert exc_empty.value.http_status == 422
    assert exc_empty.value.code == "VALIDATION_ERROR"
    assert exc_empty.value.details["reason"] == "EMPTY_BUFFER"

    with pytest.raises(VisionError) as exc_empty_full:
        image_io.decode_bytes_to_bgr(b"")
    assert exc_empty_full.value.http_status == 422
    assert exc_empty_full.value.code == "VALIDATION_ERROR"

    # Path 2: non-empty corrupt bytes -> cv2.imdecode returns None -> cleanly mapped 415
    corrupt_jpeg = b"\xff\xd8\xff\xe0CORRUPT_PAYLOAD_NOT_DECODABLE"
    with pytest.raises(VisionError) as exc_corrupt:
        image_io.decode_imdecode_bgr(corrupt_jpeg)
    assert exc_corrupt.value.http_status == 415
    assert exc_corrupt.value.code == "UNSUPPORTED_IMAGE_FORMAT"
    assert exc_corrupt.value.details["reason"] == "CORRUPT_IMAGE_BYTES"

    with pytest.raises(VisionError) as exc_corrupt_full:
        image_io.decode_bytes_to_bgr(corrupt_jpeg)
    assert exc_corrupt_full.value.http_status == 415
    assert exc_corrupt_full.value.code == "UNSUPPORTED_IMAGE_FORMAT"


# ── 4. D-30 nighttime/twilight streetlight_on reference return & V1-V3 guards ──


@pytest.mark.parametrize("scene", ["night", "twilight"])
def test_d30_streetlight_on_returned_as_normal_lit_non_fault_candidate(scene: str) -> None:
    """D-30: `night`/`twilight` `streetlight_on` must return with `is_fault_candidate=False`."""
    det = RawDetection(
        box=(20, 20, 80, 80),
        raw_class="streetlight_on",
        detector_confidence=0.91,
        metrics=_sample_roi_metrics(roi_v_p95=230.0, contrast=110.0),
    )
    quality = _sample_quality(scene)

    result = mapping.build_result(
        detections=[det],
        quality=quality,
        image_width=200,
        image_height=200,
        inference_mode="yolo26_onnx_e2e",
        model_version="yolo26s-streetlight-v1",
        original_image_url="lamp_night.jpg",
    )

    assert len(result.candidates) == 1
    cand = result.candidates[0]
    assert cand.raw_class == "streetlight_on"
    assert cand.is_fault_candidate is False
    assert cand.visual_signal_state == "NORMAL_LIT"
    assert cand.candidate_fault_type is None
    assert cand.suggested_severity_hint == "none"
    assert cand.requires_inspector_confirmation is True
    assert cand.replaces_electrical_safety_check is False
    assert result.primary_fault_type_suggestion is None


def test_day_streetlight_on_suspect_vs_day_streetlight_off_suppressed() -> None:
    """Daytime `streetlight_on` -> OTHER/P4 suspect; daytime `streetlight_off` -> suppressed."""
    quality_day = _sample_quality("day")
    on_det = RawDetection(
        box=(10, 10, 50, 50),
        raw_class="streetlight_on",
        detector_confidence=0.88,
        metrics=_sample_roi_metrics(roi_v_p95=235.0, contrast=105.0),
    )
    off_det = RawDetection(
        box=(60, 10, 100, 50),
        raw_class="streetlight_off",
        detector_confidence=0.85,
        metrics=_sample_roi_metrics(roi_v_p95=90.0, contrast=15.0),
    )

    on_cand = mapping.map_detection(
        on_det,
        quality_day,
        image_width=200,
        image_height=200,
        inference_mode="yolo26_onnx_e2e",
        candidate_id="cand-on",
    )
    assert on_cand.is_fault_candidate is True
    assert on_cand.candidate_fault_type == "OTHER"
    assert on_cand.visual_signal_state == "DAYTIME_ALWAYS_ON_SUSPECT"
    assert on_cand.suggested_severity_hint == "P4"

    off_cand = mapping.map_detection(
        off_det,
        quality_day,
        image_width=200,
        image_height=200,
        inference_mode="yolo26_onnx_e2e",
        candidate_id="cand-off",
    )
    assert off_cand.is_fault_candidate is False
    assert off_cand.candidate_fault_type is None
    assert off_cand.visual_signal_state == "DAYTIME_UNPOWERED_EXPECTED"
    assert off_cand.suggested_severity_hint == "none"
    assert off_cand.opencv_roi_metrics.fusion_status == "daytime_unpowered_suppressed"


@pytest.mark.parametrize(
    "forbidden",
    ["ELECTRIC_HAZARD", "FLICKERING", "STREETLIGHT_ELECTRIC_HAZARD", "STREETLIGHT_FLICKERING"],
)
def test_v3_forbidden_fault_types_rejected_with_500_across_mapping_and_contract(
    forbidden: str,
) -> None:
    """V3: `ELECTRIC_HAZARD` and `FLICKERING` can never be emitted by mapping or contract."""
    assert "ELECTRIC_HAZARD" in mapping.FORBIDDEN_FAULT_TYPES
    assert "FLICKERING" in mapping.FORBIDDEN_FAULT_TYPES
    assert "ELECTRIC_HAZARD" not in config.FAULT_TYPES
    assert "FLICKERING" not in config.FAULT_TYPES

    if forbidden in {"ELECTRIC_HAZARD", "FLICKERING"}:
        with pytest.raises(VisionError) as exc_ft:
            mapping.validate_fault_type(forbidden)
        assert exc_ft.value.http_status == 500
        assert exc_ft.value.code == "INTERNAL_ERROR"

    bad_det = RawDetection(
        box=(10, 10, 50, 50),
        raw_class=forbidden,
        detector_confidence=0.90,
        metrics=_sample_roi_metrics(),
    )
    with pytest.raises(VisionError) as exc_map:
        mapping.map_detection(
            bad_det,
            _sample_quality("night"),
            image_width=200,
            image_height=200,
            inference_mode="yolo26_onnx_e2e",
            candidate_id="cand-bad",
        )
    assert exc_map.value.http_status == 500
    assert exc_map.value.code == "INTERNAL_ERROR"


def test_v1_v2_and_baseline_cap_enforced_by_pydantic_contract() -> None:
    """V1/V2 safety booleans, baseline 0.65 cap, and V3 literals are strictly enforced."""
    roi_out = contract.OpencvRoiMetricsOut(
        roi_v_mean=100.0,
        roi_v_p95=120.0,
        bg_ring_v_median=105.0,
        luminance_contrast=15.0,
        green_foliage_ratio=0.0,
        edge_density=0.04,
        fusion_status="baseline_heuristic",
    )

    with pytest.raises(ValidationError):
        contract.FaultCandidate(
            candidate_id="c-1",
            raw_class="streetlight_off",
            candidate_fault_type="LAMP_OUT",
            visual_signal_state="LAMP_OUT",
            is_fault_candidate=True,
            bbox_norm=(0.1, 0.1, 0.5, 0.5),
            bbox_px=(10, 10, 50, 50),
            yolo_confidence=0.5,
            fused_confidence=0.5,
            suggested_severity_hint="P2",
            opencv_roi_metrics=roi_out,
            diagnostic_summary="test",
            requires_inspector_confirmation=False,  # type: ignore[arg-type]
        )

    with pytest.raises(ValidationError):
        contract.FaultCandidate(
            candidate_id="c-2",
            raw_class="streetlight_off",
            candidate_fault_type="LAMP_OUT",
            visual_signal_state="LAMP_OUT",
            is_fault_candidate=True,
            bbox_norm=(0.1, 0.1, 0.5, 0.5),
            bbox_px=(10, 10, 50, 50),
            yolo_confidence=0.5,
            fused_confidence=0.5,
            suggested_severity_hint="P2",
            opencv_roi_metrics=roi_out,
            diagnostic_summary="test",
            replaces_electrical_safety_check=True,  # type: ignore[arg-type]
        )

    with pytest.raises(ValidationError):
        contract.FaultCandidateResult(
            analysis_id="a-1",
            inference_mode="baseline_pretrained_opencv",
            ai_is_baseline_mode=True,
            model_version="baseline-opencv-heuristic-v1",
            image_width=100,
            image_height=100,
            original_image_url="x.jpg",
            annotated_image_url=None,
            image_quality=contract.ImageQualityOut(
                quality_grade="pass",
                laplacian_var=100.0,
                brightness_mean_v=50.0,
                overexposed_ratio=0.0,
                underexposed_ratio=0.0,
                scene_lighting="night",
            ),
            primary_fault_type_suggestion="LAMP_OUT",
            max_fused_confidence=0.75,
            candidates=[
                contract.FaultCandidate(
                    candidate_id="c-3",
                    raw_class="streetlight_off",
                    candidate_fault_type="LAMP_OUT",
                    visual_signal_state="LAMP_OUT",
                    is_fault_candidate=True,
                    bbox_norm=(0.1, 0.1, 0.5, 0.5),
                    bbox_px=(10, 10, 50, 50),
                    yolo_confidence=0.75,
                    fused_confidence=0.75,
                    suggested_severity_hint="P2",
                    opencv_roi_metrics=roi_out,
                    diagnostic_summary="test",
                )
            ],
        )


def test_end_to_end_vision_client_baseline_enforces_safety_flags(tmp_path: Path) -> None:
    """Real OpenCV baseline on synthetic JPEG satisfies all V1/V2/0.65 invariants."""
    raw = _make_static_bytes("JPEG", size=(120, 120))
    auto_settings = config.Settings(
        upload_dir=tmp_path,
        max_upload_bytes=config.MAX_UPLOAD_BYTES,
        yolo_weights_path=tmp_path / "missing.pt",
        yolo_inference_enabled=True,
        yolo_inference_mode="auto",
        yolo_img_size=1280,
        yolo_conf_threshold=0.25,
        yolo_device="cpu",
    )
    res = VisionClient(auto_settings).analyze_bytes(raw, filename="night_sample.jpg")
    assert res.inference_mode == "baseline_pretrained_opencv"
    assert res.ai_is_baseline_mode is True
    assert res.max_fused_confidence <= config.BASELINE_CONFIDENCE_CAP
    assert res.requires_inspector_confirmation is True
    assert res.replaces_electrical_safety_check is False
