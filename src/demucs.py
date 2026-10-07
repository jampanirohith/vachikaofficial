from __future__ import annotations

import json
import os
import shutil
import uuid
from pathlib import Path

from .gpu import cuda_available, resolve_device
from .hashing import sha256_file
from .utils import run
from .model_cache import configure_model_cache


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
        if requested.startswith("cuda") and not self._cuda_ok():
            if allow:
                return fallback
            raise RuntimeError("CUDA_REQUIRED: NVIDIA CUDA was requested but PyTorch reports CUDA unavailable.")
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

        # Legacy builds sometimes left four stems without a manifest. They are safe
        # to reuse only for a CPU request; a CUDA-capable request deliberately rebuilds
        # them so GPU use is not silently skipped.
        if not manifest and desired_device == "cpu" and all(p.exists() and p.stat().st_size > 1000 for p in expected.values()):
            rid = uuid.uuid4().hex
            manifest = {
                "run_id": rid, "device": "cpu",
                "requested_device": self._requested_device(),
                "cuda_available_at_run": False,
                "model": self.cfg.get("demucs.model", "htdemucs"),
                "segment_seconds": int(self.cfg.get("demucs.segment_seconds") or 0),
                "attempts": [{"device": "cpu", "status": "reused_legacy_no_manifest"}],
                "source_sha256": sha256_file(source),
                "stems": {k: {"path": str(v), "sha256": sha256_file(v)} for k, v in expected.items()},
            }
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            return {
                **{k: str(v) for k, v in expected.items()},
                "device": "cpu", "model": manifest["model"], "run_id": rid,
                "manifest": str(manifest_path),
            }

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
        attempts = []
        raw_root = self.work / "audio" / "demucs_raw"
        raw_root.mkdir(parents=True, exist_ok=True)
        chosen = requested

        try:
            self._run(source, raw_root, requested, self.cfg.get("demucs.segment_seconds"))
            attempts.append({"device": requested, "status": "success", "segment_seconds": self.cfg.get("demucs.segment_seconds")})
        except Exception as exc:
            first_text = str(exc)
            attempts.append({"device": requested, "status": "failed", "error": first_text})
            if requested.startswith("cuda") and "out of memory" in first_text.lower() and self.cfg.get("demucs.retry_segment_seconds", 8):
                retry = self.cfg.get("demucs.retry_segment_seconds", 8)
                try:
                    self._run(source, raw_root, requested, retry)
                    attempts.append({"device": requested, "status": "success", "segment_seconds": retry, "reason": "cuda_oom_retry"})
                except Exception as retry_exc:
                    attempts.append({"device": requested, "status": "failed", "segment_seconds": retry, "error": str(retry_exc)})
                    exc = retry_exc
            if not any(a.get("status") == "success" for a in attempts):
                if allow and fallback != requested:
                    self._run(source, raw_root, fallback, self.cfg.get("demucs.segment_seconds"))
                    attempts.append({"device": fallback, "status": "success", "segment_seconds": self.cfg.get("demucs.segment_seconds"), "reason": "configured_fallback"})
                    chosen = fallback
                else:
                    raise RuntimeError(f"DEMUCS_FAILED: {exc}") from exc

        found = list(raw_root.rglob("*.wav"))
        for stem in self.STEMS:
            candidate = next((p for p in found if p.stem.lower() == stem), None)
            if candidate is None:
                raise RuntimeError(f"DEMUCS_FAILED: missing {stem}.wav")
            shutil.copy2(candidate, expected[stem])

        rid = uuid.uuid4().hex
        manifest = {
            "run_id": rid,
            "device": chosen,
            "requested_device": self._requested_device(),
            "cuda_available_at_run": cuda_available(),
            "model": self.cfg.get("demucs.model", "htdemucs"),
            "segment_seconds": int(self.cfg.get("demucs.segment_seconds") or 0),
            "attempts": attempts,
            "source_sha256": sha256_file(source),
            "stems": {k: {"path": str(v), "sha256": sha256_file(v)} for k, v in expected.items()},
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return {
            **{k: str(v) for k, v in expected.items()},
            "device": chosen,
            "model": self.cfg.get("demucs.model", "htdemucs"),
            "run_id": rid,
            "manifest": str(manifest_path),
        }
