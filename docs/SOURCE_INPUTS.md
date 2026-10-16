# Source inputs and preservation

This replacement package contains the repaired Spotify-first pipeline, the canonical domain package tree, assets, SQL schemas, setup scripts, tests and current documentation. Earlier source-archive snapshots are not included; no `archives/` directory is required at runtime.

Obsolete pre-unification audit reports were excluded from the release because their old database and offset recommendations contradict the current architecture. The substantive changes are preserved in `docs/CHANGELOG.md` and `docs/UPGRADING_1_5_1.md`. Inactive YT Music playlist-ingestion/downloader/auth paths, forwarding modules, generated runtime files and old cache folders were excluded after checking their callers/tests. The active word-alignment engine remains under `src/phase2core/`, and active media-rendering primitives remain under `src/rendercore/`.

The `ideas.txt` note is retained from the provided project archive as original planning context; the decisions implemented from it are recorded in `PROJECT_PLAN.md` and the 1.5.1 changelog. The current pipeline contract is specified by `README.md`, `PROJECT_PLAN.md`, `docs/ARCHITECTURE.md`, `src/README.md`, and `docs/CONFIGURATION.md`. The release manifest and `docs/BUILD_INFO.md` describe the current source tree and its validation results.
