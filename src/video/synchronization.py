from __future__ import annotations

from pathlib import Path

import numpy as np

from src.common.utils import run


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
    min_match_score=0.20,
    min_peak_margin=0.04,
    ambiguity_window_ms=1000,
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

    # Estimate a distinct competing peak away from the selected local peak. Nearby
    # points around one broad maximum are not treated as independent alternatives.
    ambiguity_frames = max(0, int(round(ambiguity_window_ms / frame_ms)))
    distinct_competitors = [
        (score, off) for score, off in coarse
        if abs(off - best_frame) > ambiguity_frames
    ]
    runner_up_score = float(distinct_competitors[0][0]) if distinct_competitors else None
    peak_margin = (float(best_score) - runner_up_score) if runner_up_score is not None else None
    rejection_reasons = []
    if float(best_score) < float(min_match_score):
        rejection_reasons.append('score_below_minimum')
    if peak_margin is not None and peak_margin < float(min_peak_margin):
        rejection_reasons.append('ambiguous_competing_peak')
    accepted = not rejection_reasons

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
            "accepted": bool(off == best_frame and accepted),
            "rejection_reason": (";".join(rejection_reasons) if off == best_frame and rejection_reasons else (None if off == best_frame else "lower_score_than_selected")),
        })

    selected = {
        "candidate_offset_ms": int(best_frame * frame_ms),
        "total_score": float(best_score),
        "runner_up_score": runner_up_score,
        "peak_margin": peak_margin,
        "min_match_score": float(min_match_score),
        "min_peak_margin": float(min_peak_margin),
        "ambiguity_window_ms": int(ambiguity_window_ms),
        "accepted": accepted,
        "rejection_reasons": rejection_reasons,
        "reference_duration_ms": int(len(ref_env) * frame_ms),
        "youtube_duration_ms": int(len(yt_env) * frame_ms),
        "max_possible_offset_ms": int(max_offset_frames * frame_ms),
        "method": "full_song_energy_envelope_v3",
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


def find_offset(original_mp3, youtube_audio, config=None):
    """Match the complete reference song, with confidence and ambiguity diagnostics.

    ``config`` is the ``youtube_offset`` section of the project config. Rejected
    matches are returned with ``accepted=False`` so the pipeline can persist the
    evidence and route the visual stage to review instead of silently trusting the
    highest score from an unrelated recording.
    """
    settings = dict(config or {})
    sr = DEFAULT_SR
    frame_ms = int(settings.get('frame_ms', DEFAULT_FRAME_MS))
    coarse_step_ms = int(settings.get('coarse_step_ms', DEFAULT_COARSE_STEP_MS))
    fine_step_ms = int(settings.get('fine_step_ms', DEFAULT_FINE_STEP_MS))
    ref = _decode(original_mp3, sr)
    yt = _decode(youtube_audio, sr)
    ref_env = _energy_envelope(ref, sr, frame_ms)
    yt_env = _energy_envelope(yt, sr, frame_ms)
    return _scan_full_song(
        ref_env,
        yt_env,
        frame_ms=frame_ms,
        coarse_step_ms=coarse_step_ms,
        fine_step_ms=fine_step_ms,
        min_match_score=float(settings.get('min_match_score', 0.20)),
        min_peak_margin=float(settings.get('min_peak_margin', 0.04)),
        ambiguity_window_ms=int(settings.get('ambiguity_window_ms', 1000)),
    )
