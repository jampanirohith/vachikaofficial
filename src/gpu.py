from __future__ import annotations

from typing import Any


def cuda_available() -> bool:
    """Return True only when the active PyTorch installation exposes CUDA."""
    try:
        import torch
        return bool(torch.cuda.is_available())
    except Exception:
        return False


def resolve_device(requested: str | None, fallback: str = "cpu", *, allow_cpu_fallback: bool = True) -> str:
    """Resolve the requested compute device without ever pretending CUDA is available."""
    requested = str(requested or "cpu").strip().lower()
    fallback = str(fallback or "cpu").strip().lower()
    if requested.startswith("cuda"):
        if cuda_available():
            return requested
        if allow_cpu_fallback:
            return fallback
        raise RuntimeError(
            "CUDA_REQUIRED: NVIDIA CUDA was requested but PyTorch reports CUDA unavailable."
        )
    return requested


def device_report() -> dict[str, Any]:
    report: dict[str, Any] = {"cuda_available": False, "torch_version": None, "torch_cuda": None,
                              "device_count": 0, "devices": []}
    try:
        import torch
        report["torch_version"] = str(torch.__version__)
        report["torch_cuda"] = str(torch.version.cuda)
        report["cuda_available"] = bool(torch.cuda.is_available())
        if report["cuda_available"]:
            report["device_count"] = int(torch.cuda.device_count())
            report["devices"] = [
                {
                    "index": i,
                    "name": torch.cuda.get_device_name(i),
                    "total_vram_mb": int(torch.cuda.get_device_properties(i).total_memory // (1024 * 1024)),
                }
                for i in range(report["device_count"])
            ]
    except Exception as exc:
        report["error"] = str(exc)
    return report
