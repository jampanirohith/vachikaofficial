# Source inputs and preservation

This replacement package contains the repaired Spotify-first pipeline, the canonical domain package tree, assets, SQL schemas, setup scripts, tests and current documentation. Earlier source-archive snapshots are not included; no `archives/` directory is required at runtime.

The original and previous repaired ZIP audits are retained as historical context in `docs/HISTORICAL_PRE_REPAIR_AUDIT.md`. Inactive YT Music playlist-ingestion/downloader/auth paths, forwarding modules, generated runtime files, scratchpad plans and old cache folders were excluded after checking their callers/tests. The active word-alignment engine remains under `src/phase2core/`, and active media-rendering primitives remain under `src/rendercore/`.

The current pipeline contract is specified by `PROJECT_PLAN.md`, `docs/ARCHITECTURE.md`, `src/README.md`, and `docs/CONFIGURATION.md`. `docs/FULL_PROJECT_AUDIT.md` tracks all findings and the remaining target-data checks.
