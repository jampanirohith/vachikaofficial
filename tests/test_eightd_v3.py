from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import soundfile as sf

from src.audio.eightd import (
    ENGINE_VERSION,
    _fit_stems,
    _measure_output,
    _room_send,
    _read_v3_settings,
    _spatialize_stem,
    render,
)


def _tone(sr: int, seconds: float, frequency: float, phase: float = 0.0) -> np.ndarray:
    t = np.arange(int(sr * seconds), dtype=np.float32) / np.float32(sr)
    return (0.16 * np.sin(2 * np.pi * frequency * t + phase)).astype(np.float32)


def _default_settings(role: str) -> dict:
    from src.audio.eightd import _DEFAULT_SPATIAL
    return dict(_DEFAULT_SPATIAL[role])


def test_fit_stems_duplicates_mono_and_aligns_frame_counts():
    fitted, n = _fit_stems({
        "vocals": np.ones((1, 100), dtype=np.float32),
        "drums": np.ones((2, 95), dtype=np.float32),
    })
    assert n == 95
    assert fitted["vocals"].shape == (2, 95)
    assert np.array_equal(fitted["vocals"][0], fitted["vocals"][1])


def test_stem_spatializer_preserves_original_stereo_side_information():
    sr, seconds = 22050, 5.0
    left = _tone(sr, seconds, 440.0)
    right = _tone(sr, seconds, 660.0, phase=0.3)
    source = np.vstack((left, right))
    rendered = _spatialize_stem(source, sr, "other", _default_settings("other"))
    assert rendered.shape == source.shape
    assert np.isfinite(rendered).all()
    assert np.sqrt(np.mean(((rendered[0] - rendered[1]) * 0.5) ** 2)) > 0.01
    metrics = _measure_output(rendered, sr)
    assert metrics["side_to_mid_ratio"] > 0.05
    assert metrics["pan_motion"]["balance_range"] > 0.05


def test_vocal_motion_is_present_but_has_limited_rear_depth():
    sr, seconds = 22050, 8.0
    mono = _tone(sr, seconds, 220.0)
    source = np.vstack((mono, mono))
    settings = _default_settings("vocals")
    rendered = _spatialize_stem(source, sr, "vocals", settings)
    assert rendered.shape == (2, len(mono))
    assert np.isfinite(rendered).all()
    metrics = _measure_output(rendered, sr)
    assert metrics["pan_motion"]["balance_range"] > 0.04
    # This config is a front-of-room sway rather than a full rear orbit.
    assert settings["back_depth"] <= 0.25


def test_bass_low_end_remains_mono_compatible_while_harmonics_move():
    sr, seconds = 22050, 5.0
    t = np.arange(int(sr * seconds), dtype=np.float32) / np.float32(sr)
    bass = (0.2 * np.sin(2 * np.pi * 60 * t) + 0.08 * np.sin(2 * np.pi * 330 * t)).astype(np.float32)
    source = np.vstack((bass, bass))
    rendered = _spatialize_stem(source, sr, "bass", _default_settings("bass"))
    # The mid channel remains coherent in the low band despite moving upper harmonics.
    from scipy.signal import butter, sosfilt
    sos = butter(3, 100 / (sr / 2), output="sos")
    low_l, low_r = sosfilt(sos, rendered[0]), sosfilt(sos, rendered[1])
    corr = float(np.corrcoef(low_l, low_r)[0, 1])
    assert corr > 0.90


def test_room_send_has_a_stereo_tail_and_is_finite():
    sr = 16000
    source = np.zeros(sr * 2, dtype=np.float32)
    source[0] = 1.0
    wet = _room_send(source, sr, 0.6, 42)
    assert wet.shape == (2, len(source))
    assert np.isfinite(wet).all()
    assert np.max(np.abs(wet[:, int(sr * 0.10):])) > 1e-5
    assert not np.array_equal(wet[0], wet[1])


def test_full_render_writes_v3_manifest_and_stereo_mp3(tmp_path):
    if not shutil.which("ffmpeg"):
        import pytest
        pytest.skip("ffmpeg is required for the audio-render integration test")
    sr, seconds = 16000, 2.5
    t = np.arange(int(sr * seconds), dtype=np.float32) / np.float32(sr)
    sources = {
        "vocals": (0.10 * np.sin(2 * np.pi * 440 * t)).astype(np.float32),
        "drums": (0.07 * np.sin(2 * np.pi * 95 * t) * (0.5 + 0.5 * np.sin(2 * np.pi * 2 * t))).astype(np.float32),
        "bass": (0.10 * np.sin(2 * np.pi * 70 * t) + 0.03 * np.sin(2 * np.pi * 220 * t)).astype(np.float32),
        "other": (0.06 * np.sin(2 * np.pi * 620 * t + 0.4)).astype(np.float32),
    }
    stem_paths = {}
    for index, (name, mono) in enumerate(sources.items()):
        # Slight original stereo differences ensure that the renderer's side path runs.
        stereo = np.column_stack((mono, np.roll(mono, index + 1))).astype(np.float32)
        path = tmp_path / f"{name}.wav"
        sf.write(path, stereo, sr)
        stem_paths[name] = path
    source_mp3 = tmp_path / "source.wav"
    sf.write(source_mp3, np.column_stack((sources["vocals"], sources["vocals"])), sr)

    cfg = {
        "_config_hash": "test-config",
        "audio_8d": {
            "sample_rate": sr,
            "bitrate": "192k",
            "fade_in_ms": 25,
            "fade_out_ms": 50,
            "min_mono_compatibility_ratio": 0.20,
            "v3": {"room": {"master_send": 0.025, "vocal_rt60_seconds": 0.5, "master_rt60_seconds": 0.45}},
        },
    }
    output, manifest = render(cfg, tmp_path / "work", stem_paths, int(seconds * 1000), [], source_mp3)
    assert output.is_file()
    assert manifest["engine_version"] == ENGINE_VERSION
    assert manifest["validation"]["status"] == "passed"
    assert manifest["validation"]["metrics"]["pan_motion"]["balance_range"] > 0.02
    assert manifest["spatial_processing"]["original_stereo_side_preserved"] is True
    saved_manifest = json.loads((output.parent / "manifest.json").read_text(encoding="utf-8"))
    assert saved_manifest["output_sha256"] == manifest["output_sha256"]
    decoded, output_sr = sf.read(output, always_2d=True)
    assert output_sr == sr
    assert decoded.shape[1] == 2


def test_v3_defaults_do_not_inherit_the_weak_legacy_v2_width_settings():
    movement, _room, _global = _read_v3_settings({
        "audio_8d": {"other_width": 0.1, "drums_width": 0.02, "vocal_center": 1.0}
    })
    assert movement["other"]["width"] > 0.9
    assert movement["drums"]["width"] > 0.8
    assert movement["vocals"]["width"] > 0.3


def test_v3_settings_are_overridable_through_nested_runtime_config():
    movement, room, global_settings = _read_v3_settings({
        "audio_8d": {
            "sample_rate": 32000,
            "v3": {
                "movement": {"vocals": {"width": 0.41}},
                "room": {"master_send": 0.03},
                "peak_target": 0.87,
            },
        }
    })
    assert movement["vocals"]["width"] == 0.41
    assert room["master_send"] == 0.03
    assert global_settings["sample_rate"] == 32000
    assert global_settings["peak_target"] == 0.87


def test_new_engine_version_forces_cached_v2_audio_to_be_rebuilt():
    assert ENGINE_VERSION == "unified-8d-v3-binaural-room"
    assert ENGINE_VERSION != "unified-8d-v2"
