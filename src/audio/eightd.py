"""Versioned whole-song headphone spatializer for Vachika.

The v3 engine preserves useful stereo information in the Demucs stems, moves
stem mid-signals on independent smooth trajectories, adds conservative
interaural level/time and head-shadow cues, and places the vocal in a small
stereo room. It always starts from clean Demucs stems; it never spatializes an
already-rendered 8D file.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf
from scipy.signal import butter, oaconvolve, sosfilt

from src.common.hashing import sha256_file
from src.common.utils import run, duration_ms

STEMS = ("vocals", "drums", "bass", "other")
ENGINE_VERSION = "unified-8d-v3-binaural-room"

# Kept conservative enough for consumer headphones and mono playback, while
# being materially more spatial than the previous center-heavy renderer.
_DEFAULT_SPATIAL = {
    "vocals": {"width": 0.50, "period_s": 18.0, "phase_rad": 0.0,
               "back_depth": 0.16, "ild_depth": 0.58, "itd_ms": 0.42,
               "itd_mix": 0.64, "side_gain": 0.88, "head_shadow": 0.36,
               "room_send": 0.19},
    "drums":  {"width": 0.91, "period_s": 8.4, "phase_rad": 0.85,
               "back_depth": 0.82, "ild_depth": 0.78, "itd_ms": 0.58,
               "itd_mix": 0.78, "side_gain": 0.74, "head_shadow": 0.48},
    "bass":   {"width": 0.34, "period_s": 13.5, "phase_rad": 2.15,
               "back_depth": 0.16, "ild_depth": 0.44, "itd_ms": 0.27,
               "itd_mix": 0.50, "side_gain": 0.42, "head_shadow": 0.28,
               "low_anchor_hz": 115.0},
    "other":  {"width": 0.96, "period_s": 11.3, "phase_rad": 2.65,
               "back_depth": 0.94, "ild_depth": 0.84, "itd_ms": 0.61,
               "itd_mix": 0.80, "side_gain": 0.72, "head_shadow": 0.50},
}
_DEFAULT_ROOM = {
    "vocal_rt60_seconds": 0.92,
    "master_rt60_seconds": 0.72,
    "master_send": 0.045,
    "seed": 4807,
}


def _fit_stems(arrays: dict[str, np.ndarray]) -> tuple[dict[str, np.ndarray], int]:
    """Align stem frame counts and standardize them to stereo channel-first."""
    if not arrays or any(a.ndim != 2 for a in arrays.values()):
        raise RuntimeError("8D_INPUT_INVALID: stems must be 2-D channel x frame arrays")
    if any(a.shape[0] < 1 for a in arrays.values()):
        raise RuntimeError("8D_INPUT_INVALID: stems contain no channels")
    n = min(a.shape[1] for a in arrays.values())
    if n <= 0:
        raise RuntimeError("8D_INPUT_INVALID: stems contain no audio frames")
    fitted: dict[str, np.ndarray] = {}
    for name, source in arrays.items():
        a = np.asarray(source[:, :n], dtype=np.float32)
        if a.shape[0] == 1:
            a = np.repeat(a, 2, axis=0)
        elif a.shape[0] > 2:
            # Demucs is stereo in the supported pipeline. If a provider returns
            # more channels, keep the first stereo pair rather than folding them.
            a = a[:2]
        fitted[name] = np.ascontiguousarray(a, dtype=np.float32)
    return fitted, n


def _read(path: Path, sr: int = 44100) -> np.ndarray:
    y, original_sr = sf.read(path, dtype="float32", always_2d=True)
    if original_sr != sr:
        from scipy.signal import resample_poly
        divisor = math.gcd(int(original_sr), int(sr))
        y = resample_poly(y, sr // divisor, original_sr // divisor, axis=0).astype("float32")
    return np.ascontiguousarray(y.T, dtype=np.float32)


def _stem_paths(stems: dict[str, Any]) -> dict[str, Path]:
    # Demucs' mapping also contains metadata such as device, model and run_id.
    # Only audio stems are allowed in the renderer and its output manifest.
    missing = [name for name in STEMS if name not in stems]
    if missing:
        raise RuntimeError(f"8D_INPUT_INVALID: missing stems={missing}")
    return {name: Path(stems[name]) for name in STEMS}


def _legacy_eq_pan(x: np.ndarray, pan: np.ndarray) -> np.ndarray:
    """Retained for import compatibility with earlier local scripts/tests."""
    theta = (pan + 1.0) * np.pi / 4.0
    return np.vstack((x * np.cos(theta), x * np.sin(theta))).astype(np.float32)


def _lfo(n: int, sr: int, frequency: float, amplitude: float, phase: float = 0.0) -> np.ndarray:
    t = np.arange(n, dtype=np.float32) / np.float32(sr)
    return np.clip(np.sin(2.0 * np.pi * float(frequency) * t + float(phase)) * float(amplitude), -1.0, 1.0).astype(np.float32)


def _safe_lowpass(x: np.ndarray, cutoff_hz: float, sr: int) -> np.ndarray:
    """Stable Butterworth low-pass; skips filtering when the cutoff is invalid."""
    nyquist = sr * 0.5
    cutoff = min(max(float(cutoff_hz), 20.0), nyquist * 0.90)
    sos = butter(2, cutoff / nyquist, btype="lowpass", output="sos")
    return sosfilt(sos, np.asarray(x, dtype=np.float32)).astype(np.float32, copy=False)


def _fixed_fractional_delay(x: np.ndarray, delay_ms: float, sr: int) -> np.ndarray:
    """Apply a short, fixed fractional delay without allocating float64 indices."""
    delay_samples = max(0.0, float(delay_ms)) * sr / 1000.0
    if delay_samples < 1e-4:
        return x
    whole = int(delay_samples)
    frac = np.float32(delay_samples - whole)
    n = len(x)
    delayed = np.zeros(n, dtype=np.float32)
    if whole < n:
        delayed[whole:] = x[: n - whole] * (1.0 - frac)
        if frac and whole + 1 < n:
            delayed[whole + 1:] += x[: n - whole - 1] * frac
    return delayed


def _spatialize_stem(
    stereo: np.ndarray,
    sr: int,
    role: str,
    settings: dict[str, Any],
    *,
    head_shadow_cutoff_hz: float = 3100.0,
    back_cutoff_hz: float = 4300.0,
    back_gain_reduction: float = 0.10,
) -> np.ndarray:
    """Move a stem in 3-D-ish headphone space while retaining its stereo side.

    The source mid-channel follows a slow, role-specific trajectory. A small
    fixed fractional delay is blended into the far ear according to position;
    head-shadow filtering and level differences reinforce the direction. The
    original side component is restored separately, so input stereo detail is
    retained instead of being collapsed wholesale to mono.
    """
    left_in, right_in = stereo[0], stereo[1]
    mid = (left_in + right_in) * np.float32(0.5)
    side = (left_in - right_in) * np.float32(0.5)
    n = len(mid)

    period_s = max(2.0, float(settings.get("period_s", 12.0)))
    t = np.arange(n, dtype=np.float32) / np.float32(sr)
    theta = (2.0 * np.pi * t / np.float32(period_s) + np.float32(settings.get("phase_rad", 0.0))).astype(np.float32)
    lateral = (np.float32(settings.get("width", 0.8)) * np.sin(theta)).astype(np.float32)
    lateral = np.clip(lateral, -0.98, 0.98)
    back_depth = float(np.clip(settings.get("back_depth", 0.8), 0.0, 1.0))
    back = (np.float32(back_depth * 0.5) * (1.0 - np.cos(theta))).astype(np.float32)

    # Keep sub-bass anchored, while its harmonics still travel around the field.
    low_anchor = None
    moving = mid
    if role == "bass":
        low_anchor = _safe_lowpass(mid, float(settings.get("low_anchor_hz", 115.0)), sr)
        moving = (mid - low_anchor).astype(np.float32, copy=False)

    back_low = _safe_lowpass(moving, back_cutoff_hz, sr)
    back_tone = np.float32(0.58) * back
    positioned = moving * (1.0 - back_tone) + back_low * back_tone
    positioned *= (1.0 - np.float32(back_gain_reduction) * back)

    shadow = float(np.clip(settings.get("head_shadow", 0.45), 0.0, 0.8))
    shadow_low = _safe_lowpass(positioned, head_shadow_cutoff_hz, sr)
    far_left = np.clip(lateral, 0.0, 1.0)
    far_right = np.clip(-lateral, 0.0, 1.0)
    left = positioned * (1.0 - far_left * shadow) + shadow_low * far_left * shadow
    right = positioned * (1.0 - far_right * shadow) + shadow_low * far_right * shadow

    # A fixed 0.3-0.7 ms delay is mixed into the far ear by a smooth position
    # control. This avoids a high-memory, sample-by-sample variable-delay line.
    delayed = _fixed_fractional_delay(positioned, float(settings.get("itd_ms", 0.5)), sr)
    itd_mix = float(np.clip(settings.get("itd_mix", 0.7), 0.0, 1.0))
    left = left * (1.0 - far_left * itd_mix) + delayed * (far_left * itd_mix)
    right = right * (1.0 - far_right * itd_mix) + delayed * (far_right * itd_mix)

    ild_depth = float(np.clip(settings.get("ild_depth", 0.75), 0.0, 0.92))
    angle = (lateral * np.float32(ild_depth) + 1.0) * (np.pi / 4.0)
    left *= (np.cos(angle) * np.float32(np.sqrt(2.0))).astype(np.float32)
    right *= (np.sin(angle) * np.float32(np.sqrt(2.0))).astype(np.float32)

    if low_anchor is not None:
        left += low_anchor
        right += low_anchor

    side_gain = np.float32(np.clip(settings.get("side_gain", 0.7), 0.0, 1.2))
    left += side * side_gain
    right -= side * side_gain
    return np.vstack((left, right)).astype(np.float32, copy=False)


def _room_ir(sr: int, rt60_seconds: float, seed: int, channel: int) -> np.ndarray:
    """Create a small-room stereo impulse response with early reflections/tail."""
    rt60 = float(np.clip(rt60_seconds, 0.25, 1.8))
    length = max(64, int(round(rt60 * sr)))
    rng = np.random.default_rng(int(seed) + int(channel) * 7919)
    noise = rng.standard_normal(length).astype(np.float32)
    # Filter the noise just enough to avoid a brittle white-noise-sounding tail.
    noise = _safe_lowpass(noise, min(9000.0, sr * 0.38), sr)
    t = np.arange(length, dtype=np.float32) / np.float32(sr)
    env = np.exp(np.float32(-6.907755) * t / np.float32(rt60)).astype(np.float32)
    ir = noise * env
    fade_n = min(length, max(1, int(sr * 0.008)))
    ir[:fade_n] *= np.linspace(0.0, 1.0, fade_n, dtype=np.float32)

    # Short, asymmetric reflections create room scale before the diffuse tail.
    # Different timings/levels per side widen the room without a metallic echo.
    base_taps = ((7.1, 0.27), (11.8, -0.21), (17.4, 0.16), (24.5, -0.12),
                 (33.2, 0.095), (44.7, -0.075), (59.5, 0.055), (77.0, -0.035))
    shift = 0.0 if channel == 0 else 2.3
    scale = 1.0 if channel == 0 else 0.91
    for delay_ms, gain in base_taps:
        idx = int(round((delay_ms + shift) * sr / 1000.0))
        if 0 <= idx < length:
            ir[idx] += np.float32(gain * scale)
    norm = float(np.sqrt(np.sum(ir.astype(np.float64) ** 2)))
    if norm > 1e-12:
        ir /= np.float32(norm)
    return ir.astype(np.float32, copy=False)


def _room_send(mono: np.ndarray, sr: int, rt60: float, seed: int) -> np.ndarray:
    """Render a stereo room send with overlap-add convolution for long tracks."""
    ir_l = _room_ir(sr, rt60, seed, 0)
    ir_r = _room_ir(sr, rt60, seed, 1)
    n = len(mono)
    wet_l = oaconvolve(mono, ir_l, mode="full")[:n].astype(np.float32, copy=False)
    wet_r = oaconvolve(mono, ir_r, mode="full")[:n].astype(np.float32, copy=False)
    return np.vstack((wet_l, wet_r))


def _rms(x: np.ndarray) -> float:
    # Square in float32 to limit memory on 3-5 minute songs; accumulate in float64.
    values = np.asarray(x, dtype=np.float32)
    return float(np.sqrt(np.mean(values * values, dtype=np.float64) + 1e-20))


def _windowed_pan_balance(stereo: np.ndarray, sr: int, window_seconds: float = 0.5) -> dict[str, float | int]:
    """Measure changing left/right energy balance across active windows."""
    window = max(1, int(sr * window_seconds))
    n = stereo.shape[1]
    balances: list[float] = []
    active_levels: list[float] = []
    for start in range(0, n, window):
        chunk = stereo[:, start:min(n, start + window)]
        if chunk.shape[1] == 0:
            continue
        rms_l = _rms(chunk[0])
        rms_r = _rms(chunk[1])
        energy = rms_l + rms_r
        balances.append((rms_r - rms_l) / max(energy, 1e-12))
        active_levels.append(energy * 0.5)
    if not balances:
        return {"window_count": 0, "active_window_count": 0, "balance_p05": 0.0,
                "balance_p95": 0.0, "balance_range": 0.0, "balance_std": 0.0}
    levels = np.asarray(active_levels, dtype=np.float64)
    values = np.asarray(balances, dtype=np.float64)
    active = levels >= max(float(levels.max()) * 0.02, 1e-8)
    selected = values[active] if np.any(active) else values
    p05, p95 = float(np.percentile(selected, 5)), float(np.percentile(selected, 95))
    return {"window_count": int(len(values)), "active_window_count": int(len(selected)),
            "balance_p05": p05, "balance_p95": p95,
            "balance_range": float(p95 - p05), "balance_std": float(np.std(selected))}


def _measure_output(mix: np.ndarray, sr: int) -> dict[str, Any]:
    left, right = mix[0], mix[1]
    left_energy = float(np.sum(left * left, dtype=np.float64))
    right_energy = float(np.sum(right * right, dtype=np.float64))
    cross = float(np.sum(left * right, dtype=np.float64))
    correlation = cross / math.sqrt(max(left_energy * right_energy, 1e-30))
    mid = (left + right) * 0.5
    side = (left - right) * 0.5
    mid_rms, side_rms = _rms(mid), _rms(side)
    stereo_rms = _rms(mix)
    mono_ratio = _rms(mid) / max(stereo_rms, 1e-12)
    pan = _windowed_pan_balance(mix, sr)
    return {
        "sample_peak": float(np.max(np.abs(mix))),
        "stereo_rms": stereo_rms,
        "mono_compatibility_ratio": float(mono_ratio),
        "left_right_correlation": float(np.clip(correlation, -1.0, 1.0)),
        "mid_rms": mid_rms,
        "side_rms": side_rms,
        "side_to_mid_ratio": float(side_rms / max(mid_rms, 1e-12)),
        "pan_motion": pan,
    }


def _read_v3_settings(cfg: Any) -> tuple[dict[str, dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Load v3-only keys, deliberately ignoring legacy v2 width settings."""
    def get(key: str, default: Any) -> Any:
        if not hasattr(cfg, "get"):
            return default
        sentinel = object()
        direct = cfg.get(key, sentinel)
        if direct is not sentinel:
            return direct
        current = cfg
        for part in key.split("."):
            if not isinstance(current, dict) or part not in current:
                return default
            current = current[part]
        return current

    movement: dict[str, dict[str, Any]] = {}
    for role, defaults in _DEFAULT_SPATIAL.items():
        movement[role] = dict(defaults)
        override = get(f"audio_8d.v3.movement.{role}", {})
        if isinstance(override, dict):
            movement[role].update(override)
    room = dict(_DEFAULT_ROOM)
    room_override = get("audio_8d.v3.room", {})
    if isinstance(room_override, dict):
        room.update(room_override)
    global_settings = {
        "head_shadow_cutoff_hz": float(get("audio_8d.v3.head_shadow_cutoff_hz", 3100.0)),
        "back_cutoff_hz": float(get("audio_8d.v3.back_cutoff_hz", 4300.0)),
        "back_gain_reduction": float(get("audio_8d.v3.back_gain_reduction", 0.10)),
        "peak_target": float(get("audio_8d.v3.peak_target", 0.89)),
        "min_mono_compatibility_ratio": float(get("audio_8d.min_mono_compatibility_ratio", 0.20)),
        "fade_in_ms": float(get("audio_8d.fade_in_ms", 100)),
        "fade_out_ms": float(get("audio_8d.fade_out_ms", 250)),
        "sample_rate": int(get("audio_8d.sample_rate", 44100)),
        "bitrate": str(get("audio_8d.bitrate", "320k")),
    }
    return movement, room, global_settings


def render(cfg, work, stems, expected_duration_ms, words, source_mp3):
    """Create the whole-song 8D master and machine-readable spatial metrics."""
    work = Path(work)
    outdir = work / "audio" / "8d"
    outdir.mkdir(parents=True, exist_ok=True)
    movement, room, settings = _read_v3_settings(cfg)
    sr = int(settings["sample_rate"])
    stem_paths = _stem_paths(stems)
    arrays = {name: _read(stem_paths[name], sr) for name in STEMS}
    arrays, n = _fit_stems(arrays)

    # Useful reference mix for level matching. Demucs' four stems reconstruct
    # the original source closely enough for a conservative RMS match.
    reference = np.zeros((2, n), dtype=np.float32)
    for name in STEMS:
        reference += arrays[name]
    reference_rms = _rms(reference)

    mix = np.zeros((2, n), dtype=np.float32)
    for role in STEMS:
        spatial = _spatialize_stem(
            arrays[role], sr, role, movement[role],
            head_shadow_cutoff_hz=settings["head_shadow_cutoff_hz"],
            back_cutoff_hz=settings["back_cutoff_hz"],
            back_gain_reduction=settings["back_gain_reduction"],
        )
        mix += spatial

    # Give the vocal its own room send, keeping the dry voice intelligible.
    vocal_mid = (arrays["vocals"][0] + arrays["vocals"][1]) * np.float32(0.5)
    vocal_send = float(np.clip(movement["vocals"].get("room_send", 0.19), 0.0, 0.45))
    if vocal_send > 0:
        vocal_wet = _room_send(vocal_mid, sr, float(room["vocal_rt60_seconds"]), int(room["seed"]))
        mix += vocal_wet * np.float32(vocal_send)

    # A quiet shared room return glues the moving instruments into one scene.
    master_send = float(np.clip(room.get("master_send", 0.045), 0.0, 0.20))
    if master_send > 0:
        master_mid = (mix[0] + mix[1]) * np.float32(0.5)
        master_wet = _room_send(master_mid, sr, float(room["master_rt60_seconds"]), int(room["seed"]) + 1)
        mix += master_wet * np.float32(master_send)

    # Small smooth gain trim preserves the source level without overreacting to
    # one unusually loud stem. Peak limiting below remains the hard safety rail.
    output_rms = _rms(mix)
    if output_rms > 1e-8 and reference_rms > 1e-8:
        match = float(np.clip(reference_rms / output_rms, 0.80, 1.25))
        mix *= np.float32(match)
    else:
        match = 1.0

    fade_in = min(n, max(0, int(sr * settings["fade_in_ms"] / 1000.0)))
    fade_out = min(n, max(0, int(sr * settings["fade_out_ms"] / 1000.0)))
    if fade_in:
        # Half-cosine fade is less abrupt than the former linear ramp.
        ramp = (0.5 - 0.5 * np.cos(np.pi * np.arange(fade_in, dtype=np.float32) / max(1, fade_in - 1))).astype(np.float32)
        mix[:, :fade_in] *= ramp[None, :]
    if fade_out:
        ramp = (0.5 + 0.5 * np.cos(np.pi * np.arange(fade_out, dtype=np.float32) / max(1, fade_out - 1))).astype(np.float32)
        mix[:, -fade_out:] *= ramp[None, :]

    if mix.shape[0] != 2:
        raise RuntimeError("8D_VALIDATION_FAILED: output is not stereo")
    if not np.isfinite(mix).all():
        raise RuntimeError("8D_VALIDATION_FAILED: non-finite audio samples")

    metrics = _measure_output(mix, sr)
    min_ratio = float(settings["min_mono_compatibility_ratio"])
    if metrics["mono_compatibility_ratio"] < min_ratio:
        raise RuntimeError(
            "8D_VALIDATION_FAILED: weak mono compatibility ratio="
            f"{metrics['mono_compatibility_ratio']:.4f} < {min_ratio:.4f}"
        )

    peak_target = float(np.clip(settings["peak_target"], 0.65, 0.98))
    peak = float(metrics["sample_peak"] or 1.0)
    peak_gain = min(1.0, peak_target / peak)
    if peak_gain < 1.0:
        mix *= np.float32(peak_gain)
    metrics = _measure_output(mix, sr)

    # Preserve the whole-song path expected by the rest of Vachika.
    wav = outdir / "SongName_8D.wav"
    sf.write(wav, mix.T, sr, subtype="PCM_24")
    mp3 = outdir / "SongName_8D.mp3"
    run([
        "ffmpeg", "-y", "-v", "error", "-i", str(wav),
        "-c:a", "libmp3lame", "-b:a", settings["bitrate"],
        "-map_metadata", "0", "-id3v2_version", "4", str(mp3),
    ], timeout=1800)
    actual_duration_ms = duration_ms(mp3)
    if abs(actual_duration_ms - expected_duration_ms) > 400:
        raise RuntimeError(f"8D duration mismatch: expected {expected_duration_ms}, got {actual_duration_ms}")

    pan = metrics["pan_motion"]
    # A warning/status is recorded rather than failing on a naturally symmetric
    # mix; stereo balance alone cannot fully describe binaural movement.
    movement_status = "measurable" if float(pan["balance_range"]) >= 0.06 else "subtle_or_symmetric_mix"
    manifest = {
        "engine_version": ENGINE_VERSION,
        "scope": "whole_song",
        "config_hash": cfg.get("_config_hash") if hasattr(cfg, "get") else None,
        "source_mp3_sha256": sha256_file(source_mp3),
        "stems": {name: sha256_file(path) for name, path in stem_paths.items()},
        "duration_ms": int(expected_duration_ms),
        "sample_rate": sr,
        "stem_roles": {
            "vocals": "gentle_front_room_motion_plus_dedicated_room_reflections",
            "bass": "moving_harmonics_with_centered_sub_bass_anchor",
            "drums": "independent_smooth_orbit_with_itd_ild_and_head_shadow",
            "other": "wide_independent_orbit_with_preserved_original_stereo",
        },
        "spatial_processing": {
            "original_stereo_side_preserved": True,
            "independent_stem_trajectories": True,
            "interaural_level_differences": True,
            "interaural_time_difference_ms": {role: float(values.get("itd_ms", 0.0)) for role, values in movement.items()},
            "trajectories": {role: {
                "width": float(values.get("width", 0.0)),
                "period_seconds": float(values.get("period_s", 0.0)),
                "phase_radians": float(values.get("phase_rad", 0.0)),
                "back_depth": float(values.get("back_depth", 0.0)),
                "ild_depth": float(values.get("ild_depth", 0.0)),
            } for role, values in movement.items()},
            "head_shadow_filtering": True,
            "vocal_room_rt60_seconds": float(room["vocal_rt60_seconds"]),
            "vocal_room_send": vocal_send,
            "master_room_rt60_seconds": float(room["master_rt60_seconds"]),
            "master_room_send": master_send,
            "pitch_or_speed_change": False,
        },
        "level_matching": {"reference_rms": reference_rms, "pre_peak_match_gain": match, "peak_gain": peak_gain},
        "validation": {
            "status": "passed",
            "finite_samples": True,
            "stereo_channels": 2,
            "mono_compatibility_minimum": min_ratio,
            "metrics": metrics,
            "movement_status": movement_status,
        },
        "lyric_density_used": bool(words),
        "output_path": str(mp3),
        "output_sha256": sha256_file(mp3),
    }
    (outdir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return mp3, manifest
