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
