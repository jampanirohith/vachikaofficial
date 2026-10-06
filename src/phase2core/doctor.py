from __future__ import annotations

import importlib
import importlib.metadata
import platform
import shutil


def doctor() -> dict[str, str]:
    results: dict[str, str] = {"python": platform.python_version(), "platform": platform.platform()}
    for package, module in [
        ("torch", "torch"), ("transformers", "transformers"), ("demucs", "demucs"),
        ("silero-vad", "silero_vad"), ("mutagen", "mutagen"), ("soundfile", "soundfile"),
        ("librosa", "librosa"), ("torchcodec", "torchcodec"), ("numpy", "numpy"),
    ]:
        try:
            importlib.import_module(module)
            results[package] = importlib.metadata.version(package)
        except Exception as exc:
            results[package] = f"ERROR: {exc}"
    try:
        import torch
        available = bool(torch.cuda.is_available())
        results["cuda_available"] = str(available)
        results["torch_cuda_runtime"] = str(torch.version.cuda)
        results["torch_build"] = str(torch.__version__)
        if available:
            results["cuda_device"] = torch.cuda.get_device_name(0)
            results["cuda_device_count"] = str(torch.cuda.device_count())
            results["cuda_vram_mb"] = str(round(torch.cuda.get_device_properties(0).total_memory / 1024 / 1024))
        else:
            results["cuda_device"] = "NONE"
            results["cuda_vram_mb"] = "0"
    except Exception as exc:
        results["cuda_available"] = f"ERROR: {exc}"
        results["torch_cuda_runtime"] = "ERROR"
    results["ffmpeg"] = shutil.which("ffmpeg") or "MISSING"
    results["ffprobe"] = shutil.which("ffprobe") or "MISSING"
    return results
