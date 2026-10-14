from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path

from src.phase2core.utils import run_command
from src.common.model_cache import configure_model_cache
from src.common.gpu import resolve_device
from src.common.hashing import sha256_file


class DemucsIsolator:
    """Separate vocals with attempt-isolated output and explicit fallback policy."""

    def __init__(
        self,
        model_name: str,
        device: str,
        fallback_device: str,
        segment: int | None = None,
        require_cuda: bool = False,
        allow_cpu_fallback: bool = True,
        retry_segment: int | float | None = 8,
        model_cache_dir: str | Path | None = None,
    ):
        self.model_name = str(model_name)
        self.requested_device = str(device or "cpu").strip().lower()
        self.fallback_device = str(fallback_device or "cpu").strip().lower()
        self.segment = int(segment) if segment is not None else None
        self.retry_segment = int(retry_segment) if retry_segment is not None else None
        self.require_cuda = bool(require_cuda)
        self.allow_cpu_fallback = bool(allow_cpu_fallback) and not self.require_cuda
        self.device = resolve_device(
            self.requested_device,
            self.fallback_device,
            allow_cpu_fallback=self.allow_cpu_fallback,
        )
        self.model_cache_dir = Path(model_cache_dir).resolve() if model_cache_dir else None

    @staticmethod
    def _looks_like_cuda_oom(error: str) -> bool:
        text = str(error).lower()
        return any(
            token in text
            for token in (
                "cuda out of memory",
                "out of memory",
                "cublas_status_alloc_failed",
                "cuda error: out of memory",
                "memoryerror",
            )
        )

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
            python,
            "-m",
            "demucs.separate",
            "-n",
            self.model_name,
            "--two-stems=vocals",
            "--float32",
            "--device",
            device,
            "-o",
            str(output_root),
        ]
        if segment is not None:
            cmd += ["--segment", str(segment)]
        cmd.append(str(source))
        result = run_command(cmd, env=env)
        if result.returncode != 0:
            raise RuntimeError(str(result.stdout)[-8000:])
        candidates = [p for p in output_root.rglob("vocals.wav") if p.is_file()]
        if len(candidates) != 1:
            raise FileNotFoundError(f"DEMUCS_FAILED: expected exactly one vocals.wav, found {len(candidates)}")
        if candidates[0].stat().st_size <= 1000:
            raise RuntimeError(f"DEMUCS_FAILED: vocals.wav is empty or too small: {candidates[0]}")
        return candidates[0]

    def isolate(self, source: Path, destination: Path) -> dict[str, object]:
        source, destination = Path(source), Path(destination)
        if self.require_cuda and self.requested_device.startswith("cuda") and not self.device.startswith("cuda"):
            raise RuntimeError("CUDA_REQUIRED: Demucs requested CUDA but the configured CUDA runtime is unavailable.")

        root = destination.parent / "demucs_out"
        root.mkdir(parents=True, exist_ok=True)
        attempts: list[dict[str, object]] = []

        def run_attempt(device: str, segment: int | None, reason: str | None = None):
            attempt_id = uuid.uuid4().hex
            attempt_dir = root / f"attempt_{attempt_id}_{device.replace(':', '_')}"
            attempt_dir.mkdir(parents=True, exist_ok=False)
            record: dict[str, object] = {
                "attempt_id": attempt_id,
                "device": device,
                "segment_seconds": segment,
                "output_dir": str(attempt_dir),
                "status": "running",
            }
            if reason:
                record["reason"] = reason
            attempts.append(record)
            try:
                stem = self._run(source, attempt_dir, device, segment)
                if not stem.resolve().is_relative_to(attempt_dir.resolve()):
                    raise RuntimeError("DEMUCS_FAILED: returned vocals stem is outside the isolated attempt directory")
                if not stem.is_file() or stem.stat().st_size <= 1000:
                    raise RuntimeError("DEMUCS_FAILED: isolated vocals stem is missing or too small")
                record["status"] = "success"
                record["vocals_sha256"] = sha256_file(stem)
                record["vocals_path"] = str(stem)
                return stem, attempt_dir
            except Exception as exc:
                record["status"] = "failed"
                record["error"] = f"{type(exc).__name__}: {exc}"
                raise

        chosen_device = self.device
        try:
            stem, chosen_dir = run_attempt(chosen_device, self.segment)
        except Exception as first_error:
            first_text = str(first_error)
            stem = None
            chosen_dir = None
            if (
                chosen_device.startswith("cuda")
                and self.retry_segment is not None
                and self.retry_segment != self.segment
                and self._looks_like_cuda_oom(first_text)
            ):
                try:
                    stem, chosen_dir = run_attempt(chosen_device, self.retry_segment, "cuda_oom_retry")
                except Exception as retry_error:
                    first_text = str(retry_error)
            if stem is None:
                if self.allow_cpu_fallback and self.fallback_device != chosen_device:
                    try:
                        stem, chosen_dir = run_attempt(self.fallback_device, self.segment, "configured_fallback")
                        chosen_device = self.fallback_device
                    except Exception as fallback_error:
                        raise RuntimeError(
                            f"DEMUCS_FAILED: requested device failed ({first_text}); fallback failed ({fallback_error})"
                        ) from fallback_error
                else:
                    raise RuntimeError(f"DEMUCS_FAILED: {first_text}") from first_error

        assert stem is not None and chosen_dir is not None
        destination.parent.mkdir(parents=True, exist_ok=True)
        tmp_destination = destination.with_name(f".{destination.stem}.tmp-{uuid.uuid4().hex}{destination.suffix}")
        try:
            shutil.copy2(stem, tmp_destination)
            if tmp_destination.stat().st_size <= 1000:
                raise RuntimeError("DEMUCS_FAILED: copied vocals stem is too small")
            tmp_destination.replace(destination)
        finally:
            tmp_destination.unlink(missing_ok=True)

        return {
            "device": chosen_device,
            "requested_device": self.requested_device,
            "model": self.model_name,
            "path": str(destination),
            "sha256": sha256_file(destination),
            "status": "success",
            "successful_attempt_dir": str(chosen_dir),
            "attempts": attempts,
        }
