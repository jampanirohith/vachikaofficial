from __future__ import annotations

import os
from pathlib import Path


def configure_model_cache(root: str | Path) -> Path:
    """Configure one project-owned, persistent cache for all ML model downloads.

    The cache is deliberately outside per-song work directories so large model
    checkpoints are downloaded once and reused across the entire playlist.
    """
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    hf_home = root / "huggingface"
    hf_hub = hf_home / "hub"
    hf_assets = hf_home / "assets"
    torch_home = root / "torch"
    xdg_cache = root / "cache"
    for path in (hf_home, hf_hub, hf_assets, torch_home, xdg_cache):
        path.mkdir(parents=True, exist_ok=True)

    os.environ["HF_HOME"] = str(hf_home)
    os.environ["HF_HUB_CACHE"] = str(hf_hub)
    os.environ["HF_ASSETS_CACHE"] = str(hf_assets)
    # Transformers still consults this legacy variable in some versions.
    os.environ["TRANSFORMERS_CACHE"] = str(hf_hub)
    # Demucs downloads its pretrained checkpoints through torch.hub.
    os.environ["TORCH_HOME"] = str(torch_home)
    os.environ["XDG_CACHE_HOME"] = str(xdg_cache)
    return root


def prepare_models(cfg) -> dict:
    """Download/cache every ML model used by the active pipeline.

    This command intentionally runs each warm-up independently and records each
    result, so a missing dependency/network failure does not hide which model was
    successfully cached. It does not initialize databases or contact Spotify.
    """
    import gc
    import json
    from datetime import datetime, timezone

    root = cfg.path("paths.models_dir") or (cfg.root / "models")
    configure_model_cache(root)
    report = {
        "schema_version": 1,
        "prepared_at_utc": datetime.now(timezone.utc).isoformat(),
        "models_dir": str(root.resolve()),
        "models": {},
    }

    # MMS/CTC: use the same project loader and Telugu adapter path as inference,
    # but load onto CPU so preparation works without a GPU and doesn't consume
    # GPU memory needed by the rest of the workstation.
    try:
        from src.phase2core.mms_model import MMSModel
        mms = MMSModel(
            model_name=str(cfg.get("models.mms_model", "facebook/mms-1b-all")),
            language=str(cfg.get("models.language_iso3", "tel")),
            device="cpu",
            fallback_device="cpu",
            cache_dir=root,
            allow_download=True,
            revision=cfg.get("models.mms_revision"),
            require_cuda=False,
            allow_cpu_fallback=True,
        )
        mms.load()
        report["models"]["mms_ctc"] = {
            "status": "ready",
            "model": str(cfg.get("models.mms_model", "facebook/mms-1b-all")),
            "language": str(cfg.get("models.language_iso3", "tel")),
            "revision": cfg.get("models.mms_revision"),
        }
        del mms
        gc.collect()
    except Exception as exc:
        report["models"]["mms_ctc"] = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
        gc.collect()

    # HTDemucs downloads its model through torch.hub. Loading on CPU warms that
    # exact cache without requiring CUDA or processing any source audio.
    try:
        from demucs.pretrained import get_model
        demucs_name = str(cfg.get("demucs.model", cfg.get("models.demucs_model", "htdemucs")))
        demucs_model = get_model(name=demucs_name)
        report["models"]["demucs"] = {"status": "ready", "model": demucs_name}
        del demucs_model
        gc.collect()
    except Exception as exc:
        report["models"]["demucs"] = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
        gc.collect()

    # Silero VAD is small but downloaded by its own loader/cache mechanism.
    try:
        from silero_vad import load_silero_vad
        vad_model = load_silero_vad()
        report["models"]["silero_vad"] = {"status": "ready", "model": "silero-vad"}
        del vad_model
        gc.collect()
    except Exception as exc:
        report["models"]["silero_vad"] = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
        gc.collect()

    report["ok"] = all(item.get("status") == "ready" for item in report["models"].values())
    manifest = root / "preparation_manifest.json"
    temporary = manifest.with_name(manifest.name + ".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(manifest)
    return report
