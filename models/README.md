# Persistent model cache

All large ML model artifacts are stored under this project-owned `models/` directory and reused across songs.

- `huggingface/hub/` — Hugging Face MMS processor/model/adapters.
- `torch/hub/checkpoints/` — PyTorch/Demucs pretrained checkpoints.
- `cache/` — auxiliary torch/cache assets.

Per-song `temp/<song>/` directories contain only transient processing files. A model checkpoint must never be intentionally downloaded into a per-song temp directory.

If a checkpoint already exists in a previous user-level cache, the pipeline may not automatically move it; the first run after this change can therefore download it once into this project cache. Subsequent songs reuse the project cache.
