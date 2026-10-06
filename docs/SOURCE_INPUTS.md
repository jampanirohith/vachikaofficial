# Source inputs and preservation

This final Spotify-first build was reconstructed from the three operator-supplied phase ZIPs that were analyzed during integration:

- `phase1_project_FINAL_ISRC_DUPLICATE.zip`
- `PHASE2_PROJECT_FINAL_1.3.0 (1).zip`
- `PHASE3_FINAL_COMPLETE_PROJECT_V1.0.16 (1).zip`

The extracted source trees are retained under `archives/`. The active runtime is the project root and is governed by `PROJECT_PLAN.md`. Historical phase code and plans are not imported as active runtime components.

The current architecture intentionally supersedes earlier source choices:

- Spotify is the sole playlist/source-of-entry authority.
- A new playlist ID is fetched once, all playlist items are persisted in exact Spotify order, and the first normal run exits before any song processing.
- There is no active view/play/popularity lookup or ranking stage.
- Spotify full track/album/artist catalog metadata, raw JSON, artwork bytes and ISRC are collected during per-song processing.
- Spotify audio is never downloaded.
- YT Music is used only to locate/acquire media through `ytmusicapi`; the active runtime has no legacy downloader dependency.
- ISRC remains the canonical finalized-song duplicate identity.

The original phase requirements and historical runtime failures remain preserved for provenance and auditability.
