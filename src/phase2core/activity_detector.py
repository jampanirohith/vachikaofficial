from __future__ import annotations

from typing import Any

import numpy as np

from .audio import AudioManager
from .types import ActivityResult
from .utils import merge_intervals
from ..gpu import resolve_device


class ActivityDetector:
    def __init__(self, threshold: float = 0.5, min_speech_duration_ms: int = 250,
                 min_silence_duration_ms: int = 300, speech_pad_ms: int = 150,
                 use_vad: bool = True, energy_threshold_db: float = -38.0,
                 device: str = "cuda", fallback_device: str = "cpu",
                 allow_cpu_fallback: bool = True):
        self.threshold = threshold
        self.min_speech_duration_ms = min_speech_duration_ms
        self.min_silence_duration_ms = min_silence_duration_ms
        self.speech_pad_ms = speech_pad_ms
        self.use_vad = use_vad
        self.energy_threshold_db = energy_threshold_db
        self.device = resolve_device(device, fallback_device, allow_cpu_fallback=allow_cpu_fallback)
        self.fallback_device = str(fallback_device or "cpu")
        self.allow_cpu_fallback = bool(allow_cpu_fallback)
        self._model = None

    def _load_model(self):
        if self._model is None:
            try:
                from silero_vad import load_silero_vad
            except ImportError as exc:
                raise RuntimeError("VAD_LOAD_FAILED: install silero-vad") from exc
            self._model = load_silero_vad()
            if self.device.startswith("cuda"):
                try:
                    self._model = self._model.to(self.device)
                except Exception as exc:
                    if not self.allow_cpu_fallback:
                        raise RuntimeError(f"VAD_CUDA_LOAD_FAILED: {exc}") from exc
                    self.device = self.fallback_device
        return self._model

    def _energy_intervals(self, audio: np.ndarray, sr: int, frame_ms: int = 50) -> list[tuple[int, int]]:
        frame = max(1, int(sr * frame_ms / 1000))
        rms: list[float] = []
        for start in range(0, len(audio), frame):
            part = audio[start:start + frame]
            rms.append(float(np.sqrt(np.mean(np.square(part), dtype=np.float64)) + 1e-10))
        if not rms:
            return []
        ref = max(rms)
        active = [20.0 * np.log10(max(v / ref, 1e-8)) >= self.energy_threshold_db for v in rms]
        intervals: list[tuple[int, int]] = []
        start_idx: int | None = None
        for i, state in enumerate(active):
            if state and start_idx is None:
                start_idx = i
            elif not state and start_idx is not None:
                intervals.append((int(start_idx * frame / sr * 1000), int(i * frame / sr * 1000)))
                start_idx = None
        if start_idx is not None:
            intervals.append((int(start_idx * frame / sr * 1000), int(len(active) * frame / sr * 1000)))
        return merge_intervals(intervals, max_gap_ms=frame_ms)

    def detect(self, wav_path) -> dict[str, Any]:
        audio, sr = AudioManager().load_wav(wav_path)
        energy = self._energy_intervals(audio, sr)
        speech: list[tuple[int, int]] = []
        if self.use_vad:
            import torch
            model = self._load_model()
            tensor = torch.from_numpy(audio.astype(np.float32, copy=False)).to(self.device)
            try:
                with torch.no_grad():
                    ts = get_speech_timestamps_compat(
                        tensor, model, sr, self.threshold,
                        self.min_speech_duration_ms, self.min_silence_duration_ms,
                        self.speech_pad_ms,
                    )
            except Exception as exc:
                if not self.device.startswith("cuda") or not self.allow_cpu_fallback:
                    raise RuntimeError(f"VAD_INFERENCE_FAILED: {exc}") from exc
                self.device = self.fallback_device
                try:
                    model = model.to(self.device)
                    tensor = tensor.to(self.device)
                    with torch.no_grad():
                        ts = get_speech_timestamps_compat(
                            tensor, model, sr, self.threshold,
                            self.min_speech_duration_ms, self.min_silence_duration_ms,
                            self.speech_pad_ms,
                        )
                except Exception as second_exc:
                    raise RuntimeError(f"VAD_INFERENCE_FAILED: CUDA and fallback failed: {second_exc}") from second_exc
            duration_sec = len(audio) / sr
            for item in ts:
                start = float(item["start"])
                end = float(item["end"])
                if end > duration_sec * 2:
                    start_ms = int(round(start / sr * 1000))
                    end_ms = int(round(end / sr * 1000))
                else:
                    start_ms = int(round(start * 1000))
                    end_ms = int(round(end * 1000))
                if end_ms > start_ms:
                    speech.append((start_ms, end_ms))
            speech = merge_intervals(speech, max_gap_ms=self.min_silence_duration_ms)
        return {"speech": speech, "energy": energy, "device": self.device}


def get_speech_timestamps_compat(tensor, model, sr: int, threshold: float,
                                 min_speech_duration_ms: int, min_silence_duration_ms: int,
                                 speech_pad_ms: int):
    from silero_vad import get_speech_timestamps
    kwargs = {
        "sampling_rate": sr,
        "threshold": threshold,
        "min_speech_duration_ms": min_speech_duration_ms,
        "min_silence_duration_ms": min_silence_duration_ms,
        "speech_pad_ms": speech_pad_ms,
    }
    try:
        return get_speech_timestamps(tensor, model, return_seconds=True, **kwargs)
    except TypeError:
        return get_speech_timestamps(tensor, model, **kwargs)
