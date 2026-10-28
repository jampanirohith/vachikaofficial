from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import numpy as np

from src.rendercore.audio_io import probe
from src.rendercore.reel_intro import FPS, H, W, IntroOverlay, Timeline
from src.rendercore.reel_intro.beats import beat_times
from src.rendercore.utils import ffmpeg_has_encoder, nvenc_video_args


_SAMPLE_RATE = 44_100
_FRAME_BYTES = W * H * 3
_REQUIRED_ASSETS = (
    "meta.json", "stroke.png", "param_map.npy", "dot.png", "wordmark.png",
    "bar_0.png", "bar_1.png", "bar_2.png", "bar_3.png", "bar_4.png",
)


def _read_exact(stream, count: int) -> bytes:
    parts = []
    remaining = count
    while remaining:
        part = stream.read(remaining)
        if not part:
            break
        parts.append(part)
        remaining -= len(part)
    return b"".join(parts)


def _read_audio_float_stereo(path: Path) -> np.ndarray:
    """Decode an existing Vachika 8D crop to float32 stereo, without re-spatialising it."""
    cmd = [
        "ffmpeg", "-v", "error", "-i", str(path), "-vn",
        "-f", "f32le", "-ac", "2", "-ar", str(_SAMPLE_RATE), "pipe:1",
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            f"INTRO_AUDIO_DECODE_FAILED ({result.returncode}): "
            + result.stderr.decode("utf-8", errors="replace")[-3000:]
        )
    samples = np.frombuffer(result.stdout, dtype="<f4")
    if samples.size < 2 or samples.size % 2:
        raise RuntimeError("INTRO_AUDIO_DECODE_FAILED: audio produced no complete stereo samples")
    audio = samples.reshape(-1, 2).copy()
    if not np.isfinite(audio).all():
        raise RuntimeError("INTRO_AUDIO_DECODE_FAILED: audio contains non-finite samples")
    return audio


def _loudness_envelope(stereo: np.ndarray, fps: int = FPS) -> np.ndarray:
    """Per-frame RMS normalised for the five logo bars (same recipe as the intro project)."""
    if len(stereo) == 0:
        return np.zeros(1, dtype=np.float32)
    mono = np.abs(stereo).mean(axis=1)
    frame = _SAMPLE_RATE / float(fps)
    n = max(1, int(len(mono) / frame))
    levels = []
    for i in range(n):
        start = int(round(i * frame))
        end = min(len(mono), int(round((i + 1) * frame)))
        if end <= start:
            levels.append(0.0)
        else:
            levels.append(float(np.sqrt(np.mean(mono[start:end] ** 2))))
    r = np.asarray(levels, dtype=np.float32)
    if len(r) >= 3:
        r = np.convolve(r, np.ones(3, dtype=np.float32) / 3, mode="same")
    ref = float(np.percentile(r, 95)) + 1e-9
    return np.clip(r / ref, 0, 1).astype(np.float32)


def _first_beat_time(stereo: np.ndarray) -> float:
    try:
        beats, _method = beat_times(stereo.mean(axis=1), _SAMPLE_RATE)
        candidates = np.asarray(beats, dtype=np.float64)
        candidates = candidates[np.isfinite(candidates) & (candidates >= 0.45)]
        if candidates.size:
            return float(candidates[0])
    except Exception:
        # Keep reel generation working even if a beat detector backend is broken.
        pass
    return 0.60


def _video_encoder_args(cfg: Any, codec: str) -> list[str]:
    if codec == "h264_nvenc":
        return nvenc_video_args(cfg)
    return [
        "-c:v", codec,
        "-preset", str(cfg.get("render.preset", "fast")),
        "-crf", str(cfg.get("render.crf", 18)),
        "-pix_fmt", "yuv420p",
    ]


def _render_once(
    source: Path,
    audio_samples: np.ndarray,
    assets_dir: Path,
    out: Path,
    cfg: Any,
    timeline: Timeline,
    silent_duration_s: float,
    hp_lines: tuple[str, ...],
    beat_t: float,
    codec: str,
    logger=None,
) -> dict[str, Any]:
    decoder_cmd = [
        "ffmpeg", "-v", "error", "-i", str(source), "-an",
        "-vf", f"fps={FPS},scale={W}:{H}:flags=bicubic,format=bgr24",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1",
    ]
    encoder_cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-f", "rawvideo", "-pixel_format", "bgr24", "-video_size", f"{W}x{H}",
        "-framerate", str(FPS), "-i", "pipe:0", "-an",
        *_video_encoder_args(cfg, codec), "-movflags", "+faststart", str(out),
    ]
    overlay = IntroOverlay(
        str(assets_dir),
        env=_loudness_envelope(audio_samples),
        beat_t=beat_t,
        timeline=timeline,
        hp_lines=hp_lines,
        footer_enabled=bool(cfg.get("reel_intro.footer_enabled", True)),
        footer_text=str(cfg.get("reel_intro.footer_text", "Vachika")),
        footer_center_y=int(cfg.get("reel_intro.footer_center_y", 1800)),
    )
    decoder = encoder = None
    frames = 0
    try:
        decoder = subprocess.Popen(decoder_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        encoder = subprocess.Popen(encoder_cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert decoder.stdout is not None and encoder.stdin is not None

        # Prepend real black frames before consuming the source video. Once
        # this prefix ends, source frame 0 aligns with the first audio sample.
        silent_frames = max(0, int(round(float(silent_duration_s) * FPS)))
        black = np.zeros((H, W, 3), dtype=np.uint8)
        for prefix_index in range(silent_frames):
            t = prefix_index / FPS
            frame = overlay.apply(black, t) if t < timeline.duration else black
            encoder.stdin.write(frame.tobytes())
            frames += 1

        source_frames = 0
        while True:
            raw = _read_exact(decoder.stdout, _FRAME_BYTES)
            if len(raw) != _FRAME_BYTES:
                break
            frame = np.frombuffer(raw, dtype=np.uint8).reshape(H, W, 3)
            t = silent_frames / FPS + source_frames / FPS
            # Keep the intro overlay active after its animation ends so the
            # lower-band brand footer remains on every final Reel frame.
            frame = overlay.apply(frame, t)
            encoder.stdin.write(frame.tobytes())
            frames += 1
            source_frames += 1
            if logger and frames % (FPS * 10) == 0:
                logger.info("Intro compositor: processed %d frames", frames)

        decoder.stdout.close()
        dec_err = decoder.stderr.read().decode("utf-8", errors="replace") if decoder.stderr else ""
        dec_rc = decoder.wait()
        encoder.stdin.close()
        enc_out = encoder.stdout.read() if encoder.stdout else b""
        enc_err = encoder.stderr.read().decode("utf-8", errors="replace") if encoder.stderr else ""
        enc_rc = encoder.wait()
        if dec_rc != 0:
            raise RuntimeError(f"INTRO_VIDEO_DECODE_FAILED ({dec_rc}): {dec_err[-3000:]}")
        if enc_rc != 0:
            raise RuntimeError(f"INTRO_VIDEO_ENCODE_FAILED ({enc_rc}): {enc_err[-3000:]}")
        if frames == 0 or not out.is_file() or out.stat().st_size <= 10_000:
            raise RuntimeError("INTRO_VIDEO_ENCODE_FAILED: empty or too-small video output")
        info = probe(out)
        if int(info.get("width", 0)) != W or int(info.get("height", 0)) != H:
            raise RuntimeError(f"INTRO_VIDEO_ENCODE_FAILED: unexpected output dimensions {info}")
        if logger:
            logger.info(
                "Intro compositor: wrote %s (%d frames at %d fps, codec=%s)",
                out, frames, FPS, codec,
            )
        return {
            "path": str(out),
            "frames": frames,
            "fps": FPS,
            "duration_ms": int(info.get("duration_ms", 0)),
            "video_codec": info.get("video_codec"),
            "beat_t_seconds": round(float(beat_t), 4),
            "timeline_duration_ms": int(round(timeline.duration * 1000)),
            "scene_layout": "black_logo_only_then_blurred_video_headphone_prompt_then_sharp_video",
            "logo_scene_duration_ms": int(round(timeline.silent_duration * 1000)),
            "warning_scene_duration_ms": int(round(max(0.0, timeline.duration - timeline.silent_duration) * 1000)),
        }
    except Exception:
        if decoder is not None and decoder.poll() is None:
            decoder.kill()
            decoder.wait()
        if encoder is not None and encoder.poll() is None:
            try:
                if encoder.stdin:
                    encoder.stdin.close()
            except Exception:
                pass
            encoder.kill()
            encoder.wait()
        out.unlink(missing_ok=True)
        raise


def render_intro_video(
    source_video: Path,
    audio_path: Path,
    out_path: Path,
    assets_dir: Path,
    cfg: Any,
    *,
    duration_s: float = 3.2,
    silent_duration_s: float = 2.4,
    hp_lines=("USE HEADPHONES", "FOR BETTER EXPERIENCE"),
    logger=None,
) -> dict[str, Any]:
    """Composite the Vachika opening and prepend its black/silent phase.

    The existing Vachika 8D audio is only analysed for bar motion and beat timing;
    it is not processed a second time by the intro project's separate audio renderer.
    ``silent_duration_s`` is represented as actual black video frames here and as
    actual leading silence in the matching audio crop built by ``video.reel``.
    """
    source_video, audio_path, out_path, assets_dir = map(Path, (source_video, audio_path, out_path, assets_dir))
    footer_enabled = bool(cfg.get("reel_intro.footer_enabled", True))
    required_assets = _REQUIRED_ASSETS + (("instagram_icon.png",) if footer_enabled else ())
    missing = [name for name in required_assets if not (assets_dir / name).is_file()]
    if missing:
        raise FileNotFoundError(
            "REEL_INTRO_ASSETS_MISSING: expected generated intro assets in "
            f"{assets_dir}; missing: {', '.join(missing)}"
        )
    if not source_video.is_file() or not audio_path.is_file():
        raise FileNotFoundError(f"REEL_INTRO_INPUT_MISSING: video={source_video}, audio={audio_path}")
    if isinstance(hp_lines, str):
        hp_lines = (hp_lines,)
    hp_lines = tuple(str(line) for line in hp_lines if str(line).strip())
    if not hp_lines:
        hp_lines = ("USE HEADPHONES", "FOR BETTER EXPERIENCE")

    audio = _read_audio_float_stereo(audio_path)
    beat_t = _first_beat_time(audio)
    silent_duration_s = max(0.0, float(silent_duration_s))
    timeline = Timeline.with_silent_lead(silent_duration_s, duration_s)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.unlink(missing_ok=True)

    preferred = str(cfg.get("render.video_codec", "h264_nvenc"))
    fallback = str(cfg.get("render.video_codec_fallback", "libx264"))
    if preferred == "h264_nvenc" and ffmpeg_has_encoder(preferred):
        try:
            return _render_once(
                source_video, audio, assets_dir, out_path, cfg, timeline,
                silent_duration_s, hp_lines, beat_t, preferred, logger,
            )
        except Exception as exc:
            out_path.unlink(missing_ok=True)
            if logger:
                logger.warning("NVENC intro render failed; retrying with %s: %s", fallback, exc)
    codec = preferred
    if codec == "h264_nvenc" or not ffmpeg_has_encoder(codec):
        codec = fallback if ffmpeg_has_encoder(fallback) else "libx264"
    try:
        return _render_once(
            source_video, audio, assets_dir, out_path, cfg, timeline,
            silent_duration_s, hp_lines, beat_t, codec, logger,
        )
    except Exception as exc:
        out_path.unlink(missing_ok=True)
        raise RuntimeError(f"REEL_INTRO_RENDER_FAILED: {type(exc).__name__}: {exc}") from exc
