from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import soundfile as sf
from mutagen.id3 import ID3
from mutagen.mp3 import MP3

from .utils import atomic_write_bytes, require_binary, run_command


class AudioManager:
    def __init__(self, sample_rate: int = 16000):
        self.sample_rate = int(sample_rate)

    def inspect_mp3(self, path: Path) -> dict[str, object]:
        try:
            audio = MP3(path, ID3=ID3)
        except Exception as exc:
            raise ValueError(f"INVALID_MP3: {path}: {exc}") from exc
        return {
            "duration_ms": int(round(audio.info.length * 1000)),
            "sample_rate": getattr(audio.info, "sample_rate", None),
            "channels": getattr(audio.info, "channels", None),
            "bitrate": getattr(audio.info, "bitrate", None),
        }

    def decode_to_wav(self, source: Path, destination: Path) -> Path:
        """Decode source audio to a 16 kHz mono float WAV atomically.

        The temporary file deliberately retains the final media extension (for
        example ``source_16k.tmp.wav``) and the output muxer is explicitly set
        to WAV.  FFmpeg on Windows cannot infer the output container from a
        filename ending in ``.wav.tmp``.
        """
        ffmpeg = require_binary("ffmpeg")
        destination.parent.mkdir(parents=True, exist_ok=True)
        suffix = destination.suffix or ".wav"
        tmp = destination.with_name(f".{destination.stem}.tmp-{os.getpid()}{suffix}")
        cmd = [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(source), "-vn",
            "-ac", "1", "-ar", str(self.sample_rate),
            "-c:a", "pcm_f32le",
            "-f", "wav",
            str(tmp),
        ]
        result = run_command(cmd)
        if result.returncode != 0 or not tmp.exists():
            tmp.unlink(missing_ok=True)
            detail = result.stdout[-6000:].strip()
            raise RuntimeError(f"AUDIO_DECODE_FAILED: {detail or 'ffmpeg produced no output'}")
        try:
            tmp.replace(destination)
        finally:
            tmp.unlink(missing_ok=True)
        return destination

    def load_wav(self, path: Path) -> tuple[np.ndarray, int]:
        data, sr = sf.read(path, dtype="float32", always_2d=False)
        if data.ndim != 1:
            data = np.mean(data, axis=1, dtype=np.float32)
        data = np.asarray(data, dtype=np.float32)
        if not np.isfinite(data).all():
            raise ValueError("AUDIO_INVALID: non-finite audio samples")
        return data, int(sr)

    def slice_wav(self, source: Path, destination: Path, start_ms: int, end_ms: int) -> Path:
        data, sr = self.load_wav(source)
        if sr != self.sample_rate:
            raise ValueError(f"AUDIO_INVALID: expected {self.sample_rate} Hz working audio, got {sr}")
        start = max(0, int(round(start_ms * sr / 1000)))
        end = min(len(data), int(round(end_ms * sr / 1000)))
        if end <= start:
            raise ValueError("AUDIO_INVALID: empty audio slice")
        destination.parent.mkdir(parents=True, exist_ok=True)
        sf.write(destination, data[start:end], sr, subtype="FLOAT")
        return destination

    @staticmethod
    def duration_ms_from_wav(path: Path) -> int:
        info = sf.info(path)
        return int(round(info.frames / info.samplerate * 1000))
