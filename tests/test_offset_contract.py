from src.video import synchronization as youtube_sync
import numpy as np


def test_full_song_offset_only_accepts_positions_where_reference_fits():
    ref_env = np.array([0.1, 0.8, 0.2, 0.9, 0.3, 0.7, 0.4, 1.0, 0.2, 0.6, 0.1, 0.5], dtype=np.float32)
    yt_env = np.concatenate([
        np.zeros(8, dtype=np.float32),
        ref_env,
        np.zeros(5, dtype=np.float32),
    ])

    chosen, candidates = youtube_sync._scan_full_song(
        ref_env, yt_env, frame_ms=25, coarse_step_ms=50, fine_step_ms=25
    )

    assert chosen["candidate_offset_ms"] == 200
    assert 0 <= chosen["candidate_offset_ms"] <= chosen["max_possible_offset_ms"]
    assert chosen["reference_duration_ms"] == len(ref_env) * 25
    assert chosen["reference_duration_ms"] <= chosen["youtube_duration_ms"]
    assert chosen["waveform_score"] is None
    assert chosen["energy_score"] is None
    assert candidates


def test_full_song_offset_rejects_youtube_shorter_than_reference():
    ref_env = np.ones(20, dtype=np.float32)
    yt_env = np.ones(10, dtype=np.float32)
    try:
        youtube_sync._scan_full_song(ref_env, yt_env)
    except RuntimeError as exc:
        assert "shorter than reference song" in str(exc)
    else:
        raise AssertionError("expected YOUTUBE_OFFSET_FAILED")
