from __future__ import annotations

import shutil
import os
from pathlib import Path

from .utils import run_command
from ..model_cache import configure_model_cache
from ..gpu import resolve_device


class DemucsIsolator:
    def __init__(self, model_name: str, device: str, fallback_device: str, segment: int | None = None,
                 require_cuda: bool = False, allow_cpu_fallback: bool = True, retry_segment: int | float | None = 8, model_cache_dir: str | Path | None = None):
        self.model_name = model_name
        self.device = resolve_device(device, fallback_device, allow_cpu_fallback=allow_cpu_fallback)
        self.fallback_device = fallback_device
        self.segment = int(segment) if segment is not None else None
        self.require_cuda = bool(require_cuda)
        self.allow_cpu_fallback = bool(allow_cpu_fallback)
        self.retry_segment = int(retry_segment) if retry_segment is not None else None
        self.model_cache_dir = Path(model_cache_dir).resolve() if model_cache_dir else None

    @staticmethod
    def _cuda_available() -> bool:
        try:
            import torch
            return bool(torch.cuda.is_available())
        except Exception:
            return False

    @staticmethod
    def _looks_like_cuda_oom(error: str) -> bool:
        text = error.lower()
        return any(token in text for token in (
            "cuda out of memory", "out of memory", "cublas_status_alloc_failed",
            "cuda error: out of memory", "memoryerror",
        ))

    def _run(self, source: Path, output_root: Path, device: str, segment: int | None = None) -> Path:
        python = shutil.which("python") or shutil.which("python3")
        if not python:
            raise FileNotFoundError("Python executable not found")
        output_root.mkdir(parents=True, exist_ok=True)
        env = None
        if self.model_cache_dir is not None:
            configure_model_cache(self.model_cache_dir)
            env = dict(os.environ)
        cmd = [
            python, "-m", "demucs.separate",
            "-n", self.model_name,
            "--two-stems=vocals",
            "--float32",
            "--device", device,
            "-o", str(output_root),
        ]
        if segment is not None:
            cmd += ["--segment", str(segment)]
        cmd.append(str(source))
        result = run_command(cmd, env=env)
        if result.returncode != 0:
            raise RuntimeError(result.stdout[-8000:])
        candidates = sorted(output_root.rglob("vocals.wav"))
        if not candidates:
            raise FileNotFoundError("DEMUCS_FAILED: vocals.wav not found")
        return candidates[-1]

    def isolate(self, source: Path, destination: Path) -> dict[str, object]:
        # Device was resolved during construction; CUDA is preferred whenever
        # PyTorch exposes it, with the configured fallback only when necessary.
        if self.require_cuda and str(device).startswith("cuda") and self.device != str(device):
            raise RuntimeError("CUDA_REQUIRED: Demucs requested CUDA but the configured CUDA runtime is unavailable.")

        output_root = destination.parent / "demucs_out"
        attempts: list[dict[str, object]] = []
        chosen_device = self.device
        stem: Path | None = None

        try_device = self.device
        try_segment = self.segment
        try:
            stem = self._run(source, output_root, try_device, try_segment)
            attempts.append({"device": try_device, "segment_seconds": try_segment, "status": "success"})
        except Exception as first_error:
            first_text = str(first_error)
            attempts.append({"device": try_device, "segment_seconds": try_segment, "status": "failed", "error": first_text})

            # Prefer a second CUDA attempt with a smaller segment on VRAM pressure.
            if try_device == "cuda" and self.retry_segment is not None and self._looks_like_cuda_oom(first_text):
                try:
                    stem = self._run(source, output_root, "cuda", self.retry_segment)
                    chosen_device = "cuda"
                    attempts.append({"device": "cuda", "segment_seconds": self.retry_segment, "status": "success", "reason": "cuda_oom_retry"})
                except Exception as second_error:
                    second_text = str(second_error)
                    attempts.append({"device": "cuda", "segment_seconds": self.retry_segment, "status": "failed", "error": second_text})
                    first_error = second_error
                    first_text = second_text

            if stem is None:
                if self.allow_cpu_fallback and self.fallback_device != try_device:
                    stem = self._run(source, output_root, self.fallback_device, self.segment)
                    chosen_device = self.fallback_device
                    attempts.append({"device": self.fallback_device, "segment_seconds": self.segment, "status": "success", "reason": "cpu_fallback"})
                else:
                    raise RuntimeError(f"DEMUCS_FAILED: {first_text}") from first_error

        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(stem, destination)
        return {
            "device": chosen_device,
            "model": self.model_name,
            "path": str(destination),
            "status": "success",
            "attempts": attempts,
        }
