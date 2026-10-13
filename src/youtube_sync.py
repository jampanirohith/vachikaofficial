from __future__ import annotations

from pathlib import Path

import numpy as np

from .utils import run


DEFAULT_SR = 8000
DEFAULT_FRAME_MS = 25
DEFAULT_COARSE_STEP_MS = 250
DEFAULT_FINE_STEP_MS = 25


def _decode(path, sr=DEFAULT_SR):
    """Decode audio to mono float32 at a low sample rate for deterministic matching."""
    import soundfile as sf

    raw = Path(path).with_suffix(".offsetsrc.wav")
    run(
        [
            "ffmpeg", "-y", "-v", "error", "-i", str(path),
            "-ac", "1", "-ar", str(sr), "-f", "wav", str(raw),
        ],
        timeout=900,
    )
    try:
        y, _ = sf.read(raw, dtype="float32")
    finally:
        raw.unlink(missing_ok=True)
    return np.asarray(y, dtype=np.float32)


def _energy_envelope(x, sr, frame_ms=DEFAULT_FRAME_MS):
    """Build a normalized RMS-energy sequence for the complete audio."""
    frame = max(1, int(round(sr * frame_ms / 1000)))
    n = len(x) // frame
    if n <= 0:
        return np.empty(0, dtype=np.float32)

    x = x[: n * frame]
    blocks = x.reshape(n, frame)
    env = np.sqrt(np.mean(blocks * blocks, axis=1) + 1e-12)
    env = np.log1p(env * 100.0)
    env -= np.mean(env)
    norm = np.linalg.norm(env)
    if norm > 1e-9:
        env /= norm
    return env.astype(np.float32)


def _correlation(a, b):
    """Pearson-like normalized correlation for two equal-length features."""
    m = min(len(a), len(b))
    if m < 10:
        return 0.0
    a = np.asarray(a[:m], dtype=np.float64)
    b = np.asarray(b[:m], dtype=np.float64)
    a -= np.mean(a)
    b -= np.mean(b)
    na = np.linalg.norm(a)
    nb = np.linalg.norm(b)
    if na <= 1e-12 or nb <= 1e-12:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def _score_offset(ref_env, yt_env, offset_frames):
    end = offset_frames + len(ref_env)
    if offset_frames < 0 or end > len(yt_env):
        return -1.0
    return _correlation(ref_env, yt_env[offset_frames:end])


def _scan_full_song(
    ref_env,
    yt_env,
    frame_ms=DEFAULT_FRAME_MS,
    coarse_step_ms=DEFAULT_COARSE_STEP_MS,
    fine_step_ms=DEFAULT_FINE_STEP_MS,
):
    """Find the best position where the complete reference fits in YouTube audio."""
    if len(ref_env) == 0:
        raise RuntimeError("YOUTUBE_OFFSET_FAILED: empty reference audio")
    if len(yt_env) < len(ref_env):
        raise RuntimeError("YOUTUBE_OFFSET_FAILED: YouTube audio is shorter than reference song")

    max_offset_frames = len(yt_env) - len(ref_env)
    coarse_step = max(1, int(round(coarse_step_ms / frame_ms)))
    fine_step = max(1, int(round(fine_step_ms / frame_ms)))

    coarse = []
    for off in range(0, max_offset_frames + 1, coarse_step):
        coarse.append((_score_offset(ref_env, yt_env, off), off))
    if coarse[-1][1] != max_offset_frames:
        coarse.append((_score_offset(ref_env, yt_env, max_offset_frames), max_offset_frames))

    coarse.sort(key=lambda item: item[0], reverse=True)
    _, coarse_best = coarse[0]

    radius = max(coarse_step, int(round(coarse_step_ms / frame_ms)))
    start = max(0, coarse_best - radius)
    end = min(max_offset_frames, coarse_best + radius)
    fine = [(_score_offset(ref_env, yt_env, off), off) for off in range(start, end + 1, fine_step)]
    if end not in {off for _, off in fine}:
        fine.append((_score_offset(ref_env, yt_env, end), end))
    fine.sort(key=lambda item: item[0], reverse=True)
    best_score, best_frame = fine[0]

    # Keep the top diagnostics, deduplicated by tested offset.
    diagnostics = []
    seen = set()
    for score, off in fine[:20] + coarse[:10]:
        if off in seen:
            continue
        seen.add(off)
        diagnostics.append({
            "candidate_offset_ms": int(off * frame_ms),
            "total_score": float(score),
            "accepted": off == best_frame,
            "rejection_reason": None if off == best_frame else "lower_score_than_selected",
        })

    selected = {
        "candidate_offset_ms": int(best_frame * frame_ms),
        "total_score": float(best_score),
        "reference_duration_ms": int(len(ref_env) * frame_ms),
        "youtube_duration_ms": int(len(yt_env) * frame_ms),
        "max_possible_offset_ms": int(max_offset_frames * frame_ms),
        "method": "full_song_energy_envelope_v2",
        "frame_ms": int(frame_ms),
        "coarse_step_ms": int(coarse_step_ms),
        "fine_step_ms": int(fine_step_ms),
        # These legacy component fields are intentionally None. The old matcher
        # exposed four component scores; the new matcher has one whole-song score.
        "waveform_score": None,
        "energy_score": None,
        "peak_valley_score": None,
        "transition_score": None,
    }
    return selected, diagnostics


def find_offset(original_mp3, youtube_audio):
    """Fit the complete reference song inside the complete YouTube audio.

    No hard 30-second limit is used. Valid offsets are exactly:
        0 <= offset <= youtube_duration - reference_duration
    """
    sr = DEFAULT_SR
    frame_ms = DEFAULT_FRAME_MS
    ref = _decode(original_mp3, sr)
    yt = _decode(youtube_audio, sr)
    ref_env = _energy_envelope(ref, sr, frame_ms)
    yt_env = _energy_envelope(yt, sr, frame_ms)
    return _scan_full_song(
        ref_env,
        yt_env,
        frame_ms=frame_ms,
        coarse_step_ms=DEFAULT_COARSE_STEP_MS,
        fine_step_ms=DEFAULT_FINE_STEP_MS,
    )
