from __future__ import annotations

import json
import os
import shutil
import uuid
from pathlib import Path

from src.common.gpu import cuda_available
from src.common.hashing import sha256_file
from src.common.utils import run
from src.common.model_cache import configure_model_cache


class DemucsRunner:
    """Run HTDemucs on the best available configured device.

    A cached stem set is reusable only when its source/model/config/device identity
    still matches. This is important when a machine first ran on CPU and later
    gains a working NVIDIA CUDA environment.
    """

    STEMS = ("vocals", "drums", "bass", "other")

    def __init__(self, cfg, work, logger, model_cache_dir=None):
        self.cfg = cfg
        self.work = Path(work)
        self.logger = logger
        configured = cfg.get("paths.models_dir") if model_cache_dir is None else model_cache_dir
        self.model_cache_dir = Path(configured) if configured else None

    def _cuda_ok(self) -> bool:
        # Kept as a small seam for tests and diagnostics.
        return cuda_available()

    def _requested_device(self) -> str:
        return str(self.cfg.get("runtime.device", "cuda")).lower()

    def _resolve_device(self) -> str:
        requested = self._requested_device()
        fallback = str(self.cfg.get("runtime.fallback_device", "cpu")).lower()
        allow = bool(self.cfg.get("runtime.allow_cpu_fallback", True))
        require_cuda = bool(self.cfg.get("runtime.require_cuda", False))
        if requested.startswith("cuda") and not self._cuda_ok():
            if require_cuda or not allow:
                raise RuntimeError("CUDA_REQUIRED: NVIDIA CUDA was requested but PyTorch reports CUDA unavailable.")
            return fallback
        return requested

    def _run(self, source, outroot, device, segment=None):
        py = shutil.which("python") or shutil.which("python3") or "python"
        env = None
        if self.model_cache_dir is not None:
            configure_model_cache(self.model_cache_dir)
            env = dict(os.environ)
        cmd = [
            py, "-m", "demucs.separate",
            "-n", str(self.cfg.get("demucs.model", "htdemucs")),
            "--float32", "--device", device,
            "-o", str(outroot),
        ]
        if segment:
            cmd += ["--segment", str(int(segment))]
        cmd.append(str(source))
        return run(cmd, timeout=int(self.cfg.get("demucs.timeout_seconds", 7200)), env=env)

    def _manifest_matches(self, manifest, source, expected, desired_device):
        if not manifest or manifest.get("source_sha256") != sha256_file(source):
            return False
        if manifest.get("model") != self.cfg.get("demucs.model", "htdemucs"):
            return False
        if manifest.get("device") != desired_device:
            return False
        if int(manifest.get("segment_seconds") or 0) != int(self.cfg.get("demucs.segment_seconds") or 0):
            return False
        if int(manifest.get("retry_segment_seconds") or 0) != int(self.cfg.get("demucs.retry_segment_seconds") or 0):
            return False
        for path in expected.values():
            if not path.exists() or path.stat().st_size <= 1000:
                return False
        stored = manifest.get("stems") or {}
        for name, path in expected.items():
            if stored.get(name, {}).get("sha256") != sha256_file(path):
                return False
        return True

    def run(self, source):
        out = self.work / "audio" / "stems"
        out.mkdir(parents=True, exist_ok=True)
        expected = {s: out / f"{s}.wav" for s in self.STEMS}
        manifest_path = out / "demucs_manifest.json"
        desired_device = self._resolve_device()

        manifest = {}
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception:
                manifest = {}

        # Never adopt legacy stems without a manifest: their source/model/device
        # provenance is unknowable and they may belong to a different song.
        if self._manifest_matches(manifest, source, expected, desired_device):
            rid = str(manifest.get("run_id") or uuid.uuid4().hex)
            if manifest.get("run_id") != rid:
                manifest["run_id"] = rid
                manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            if self.logger:
                self.logger.info("Demucs: reusing %s stems on %s", self.cfg.get("demucs.model", "htdemucs"), desired_device)
            return {
                **{k: str(v) for k, v in expected.items()},
                "device": desired_device,
                "model": manifest.get("model", self.cfg.get("demucs.model", "htdemucs")),
                "run_id": rid,
                "manifest": str(manifest_path),
            }

        requested = desired_device
        fallback = str(self.cfg.get("runtime.fallback_device", "cpu")).lower()
        allow = bool(self.cfg.get("runtime.allow_cpu_fallback", True))
        require_cuda = bool(self.cfg.get("runtime.require_cuda", False))
        attempts = []
        raw_root = self.work / "audio" / "demucs_raw"
        raw_root.mkdir(parents=True, exist_ok=True)
        chosen = requested
        chosen_dir: Path | None = None

        def execute_attempt(device: str, segment_seconds: int | None, reason: str | None = None) -> Path:
            # A distinct output directory per attempt prevents a retry or old partial
            # run from contributing one or more stale stems to the successful result.
            attempt_id = uuid.uuid4().hex
            attempt_dir = raw_root / f"attempt_{attempt_id}_{device.replace(':', '_')}"
            attempt_dir.mkdir(parents=True, exist_ok=False)
            attempt = {"attempt_id": attempt_id, "device": device, "segment_seconds": segment_seconds,
                       "output_dir": str(attempt_dir), "status": "running"}
            if reason:
                attempt["reason"] = reason
            attempts.append(attempt)
            try:
                self._run(source, attempt_dir, device, segment_seconds)
                for stem in self.STEMS:
                    candidates = [p for p in attempt_dir.rglob("*.wav") if p.is_file() and p.stem.lower() == stem]
                    if len(candidates) != 1:
                        raise RuntimeError(f"Expected exactly one {stem}.wav from this Demucs attempt, found {len(candidates)}")
                    if candidates[0].stat().st_size <= 1000:
                        raise RuntimeError(f"Demucs produced an unusable {stem}.wav in {attempt_dir}")
                attempt["status"] = "success"
                return attempt_dir
            except Exception as exc:
                attempt["status"] = "failed"
                attempt["error"] = str(exc)
                raise

        configured_segment = int(self.cfg.get("demucs.segment_seconds") or 0) or None
        retry_segment = int(self.cfg.get("demucs.retry_segment_seconds") or 0) or None
        first_error: Exception | None = None
        try:
            chosen_dir = execute_attempt(requested, configured_segment)
        except Exception as exc:
            first_error = exc
            is_cuda_oom = requested.startswith("cuda") and "out of memory" in str(exc).lower()
            if is_cuda_oom and retry_segment and retry_segment != configured_segment:
                try:
                    chosen_dir = execute_attempt(requested, retry_segment, "cuda_oom_retry")
                except Exception as retry_exc:
                    first_error = retry_exc
            if chosen_dir is None:
                if allow and not require_cuda and fallback != requested:
                    try:
                        chosen_dir = execute_attempt(fallback, configured_segment, "configured_fallback")
                        chosen = fallback
                    except Exception as fallback_exc:
                        raise RuntimeError(f"DEMUCS_FAILED: requested device failed ({first_error}); fallback failed ({fallback_exc})") from fallback_exc
                else:
                    raise RuntimeError(f"DEMUCS_FAILED: {first_error}") from first_error

        # Only use stem files produced inside the successful attempt directory.
        assert chosen_dir is not None
        for stem in self.STEMS:
            candidate = next(p for p in chosen_dir.rglob("*.wav") if p.is_file() and p.stem.lower() == stem)
            temp_expected = expected[stem].with_suffix(".wav.tmp")
            temp_expected.unlink(missing_ok=True)
            shutil.copy2(candidate, temp_expected)
            temp_expected.replace(expected[stem])

        rid = uuid.uuid4().hex
        successful_attempt = next((a for a in reversed(attempts) if a.get("status") == "success" and a.get("output_dir") == str(chosen_dir)), {})
        manifest = {
            "run_id": rid,
            "device": chosen,
            "requested_device": self._requested_device(),
            "cuda_available_at_run": cuda_available(),
            "model": self.cfg.get("demucs.model", "htdemucs"),
            # Keep the configured run policy stable for cache validation and also
            # expose the actual segmentation used by the successful attempt.
            "segment_seconds": int(self.cfg.get("demucs.segment_seconds") or 0),
            "retry_segment_seconds": int(self.cfg.get("demucs.retry_segment_seconds") or 0),
            "successful_segment_seconds": successful_attempt.get("segment_seconds"),
            "attempts": attempts,
            "successful_attempt_dir": str(chosen_dir),
            "source_sha256": sha256_file(source),
            "stems": {k: {"path": str(v), "sha256": sha256_file(v), "source_attempt_dir": str(chosen_dir)} for k, v in expected.items()},
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return {
            **{k: str(v) for k, v in expected.items()},
            "device": chosen,
            "model": self.cfg.get("demucs.model", "htdemucs"),
            "run_id": rid,
            "manifest": str(manifest_path),
        }
