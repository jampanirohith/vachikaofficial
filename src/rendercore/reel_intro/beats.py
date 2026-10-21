"""Beat times and segment planning.

librosa is used when installed. The numpy fallback is a plain onset-envelope
autocorrelation and is only meant to keep the pipeline running without it.
"""
import numpy as np
from scipy.signal import stft


def beat_times(mono, sr):
    try:
        import librosa
        _, b = librosa.beat.beat_track(y=mono.astype(np.float32), sr=sr, units="time")
        return np.asarray(b, dtype=np.float64), "librosa"
    except ImportError:
        return _fallback_beats(mono, sr), "fallback"


def _fallback_beats(mono, sr, lo_bpm=60, hi_bpm=140, hop=512):
    f, tt, Z = stft(mono, fs=sr, nperseg=1024, noverlap=1024 - hop)
    mag = np.log1p(np.abs(Z))
    flux = np.maximum(np.diff(mag, axis=1), 0).sum(axis=0)
    flux = np.maximum(flux - np.convolve(flux, np.ones(21) / 21, mode="same"), 0)
    fr = sr / hop
    ac = np.correlate(flux, flux, mode="full")[len(flux) - 1:]
    lags = np.arange(int(fr * 60 / hi_bpm), int(fr * 60 / lo_bpm))
    lag = int(lags[np.argmax(ac[lags])])
    # parabolic refinement -> fractional period, so the grid does not drift
    y0, y1, y2 = ac[lag - 1], ac[lag], ac[lag + 1]
    P = lag + 0.5 * (y0 - y2) / (y0 - 2 * y1 + y2 + 1e-12)
    best, best_o = -1.0, 0.0
    for o in np.arange(0, P, 0.5):
        idx = np.round(o + P * np.arange(int((len(flux) - o) / P))).astype(int)
        idx = idx[idx < len(flux)]
        sc = flux[idx].sum()
        if sc > best:
            best, best_o = sc, o
    t0 = (best_o + 1) * hop / sr                       # +1: flux[i] sits between STFT frames i and i+1
    period = P / fr
    return np.arange(t0, len(mono) / sr, period)


def plan_segment(beats, first_vocal, song_len, seg_len, lead_in=3.0, fade_lead=0.3, min_beat=0.45):
    """Choose where in the song the Reel starts.

    The first beat lands fade_lead seconds into the Reel, so the 0.3 s audio
    fade-in finishes just as it arrives. The start is the latest beat that
    still leaves lead_in seconds before the first sung line.
    """
    beats = np.asarray(beats)
    ok = beats[(beats - fade_lead >= 0) & (first_vocal - (beats - fade_lead) >= lead_in)]
    if len(ok):
        b0 = ok.max()
    else:                                                   # vocals start early in the song
        later = beats[beats - fade_lead >= 0]
        b0 = later.min() if len(later) else fade_lead
    start = max(0.0, b0 - fade_lead)
    end = min(song_len, start + seg_len)
    rel = beats - start
    rel = rel[(rel >= 0) & (rel <= end - start)]
    after = rel[rel >= min_beat]
    return dict(start=float(start), end=float(end), first_vocal_rel=float(first_vocal - start),
                beat_t=float(after[0]) if len(after) else 0.6, beats_rel=rel)


def snap_end(beats, wanted_end, song_len):
    """First beat at or after the wanted end (last lyric + a short tail)."""
    later = np.asarray(beats)[np.asarray(beats) >= wanted_end]
    return float(later[0]) if len(later) else float(song_len)
