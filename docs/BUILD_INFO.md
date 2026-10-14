# Build and validation record

**Package version:** `1.4.30-src-organized`

This is the complete replacement source tree. Active application code is grouped into domain packages under `src/`; the Telugu word-alignment engine (`src/phase2core/`) and media-rendering engine (`src/rendercore/`) remain intact. The archive includes configuration templates, SQL schemas, fonts, tests, setup scripts and current/historical documentation. It excludes credentials, cookies, databases, model checkpoints, generated media, hook-queue state, bytecode and test caches.

## Reproducible checks

From the extracted project root:

```powershell
python -m pip install -r requirements.txt
python -m pip install -r requirements-dev.txt
python main.py --doctor
python main.py --prepare-models
python -m compileall -q main.py src tests scripts
pytest -q
```

The release suite contains **110 passing tests**. Python compilation passed for `main.py`, `src/`, `tests/`, and `scripts/`. The static import-target audit checked **234 application/test/script import targets** with no unresolved `src.*` import targets. Both `config.json` and `config.json.example` parse as JSON and are byte-for-byte identical safe templates. CLI help completed without startup noise. The read-only dry-run contract is covered by regression tests: with no database present, it does not initialize a database, make network calls, or create the database directory.

`docs/BUILD_MANIFEST.json` inventories the release files and records their SHA-256 digests (excluding the manifest itself to avoid a recursive checksum). Its validation section records the measured local checks and the unavailable external checks.

## Checks not represented as passed

A real song-to-Reel pipeline job was **not** executed in this packaging environment. Live Spotify/YT Music/LRCLIB calls, model-server downloads and inference, real FFmpeg media rendering, audio/video quality, and target-machine CUDA/NVENC behavior require the user's credentials, target media, downloaded models and hardware. Offset thresholds need empirical calibration on annotated recordings. Ruff and mypy were unavailable in the build environment and were not run. Syntax compilation, import checks and unit tests do not replace those checks.
