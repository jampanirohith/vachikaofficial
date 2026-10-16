# Current Project Plan — Vachika Official

**Current release authority — 1.5.1-unified-db-mms-offset-v3**

This root file is intentionally concise; implementation details and subsystem contracts live in `docs/ARCHITECTURE.md` and `docs/CONFIGURATION.md`.

## Product contract

- **Playlist authority:** Spotify Web API. The frozen Spotify playlist snapshot preserves the exact order and includes every playlist position.
- **Spotify audio:** never downloaded. Spotify supplies track/album/artist metadata, artwork and ISRC only.
- `ytmusicapi` searches the catalog and selects an identity/duration-compatible recording. `yt-dlp` downloads the exact selected YT Music URL; it is never invoked with a search query for audio acquisition.
- The **ISRC duplicate gate** considers finalized songs only, and the operator may keep the previous or current result.
- Synchronized Telugu lyric sourcing happens before expensive alignment/8D/Reel stages. Missing synchronized lyrics are explicitly skipped rather than sent into later stages.
- The source-audio and selected-video reference-audio caches are content/provenance checked. Demucs uses isolated attempt output directories and a manifest for the successful output.
- Word-level Telugu alignment is performed by the active `src/phase2core/` engine through `src/alignment/adapter.py`. Keep this package.
- Whole-song 8D audio is produced before hook selection. The Reel's video timestamps use the absolute selected source-video timeline after applying the accepted offset.
- YouTube offset matching uses MMS/CTC over the opening LRCLIB phrase in both the acquired YT Music reference audio and the exact selected YouTube video audio. Weak forced-alignment windows are filtered relative to the strongest match; the earliest remaining credible occurrence is selected, and paired word timestamps determine a robust median offset. Energy-envelope matching is not used as a fallback.
- The final song package records audio, lyric, 8D, visual selection, offset, font and Reel provenance. Promotion happens only after artifact validation.

## Normal run behavior

1. A new playlist ID is fetched once, persisted, and the first normal run exits after snapshot completion.
2. Later runs process actionable tracks in stored Spotify order.
3. Stage/workspace checkpoints are reused only when cache identity and artifact checks pass.
4. `--resume` reconciles interrupted entries before processing; `--load-playlist` is the explicit snapshot-only command; `--dry-run` is read-only and makes no API calls.
5. `--prepare-models` (alias `--download-models`) downloads the active MMS/CTC+Telugu adapter, HTDemucs and Silero VAD weights without processing songs.

## Supported runtime

Python 3.12+, FFmpeg/FFprobe, SQLite and the dependencies pinned in `requirements.txt`. CUDA is preferred for supported PyTorch stages; CPU fallback is enabled by default. NVIDIA NVENC is preferred for rendering and software encoding is the fallback.

## Change policy

- Preserve `src/phase2core/` and `src/rendercore/`; they are active implementations.
- Do not ship generated per-song state, model weights, hook queues, credentials or cookies.
- Add regression tests for output behavior and cache invalidation when changing pipeline stages.
- Treat offset thresholds and media-selection changes as output-affecting behavior and validate them against a representative test set before changing defaults.


## Unified storage and MMS offset update (1.5.1)

- Use one active SQLite database at `db/vachika.db`; consolidate playlist, song, Reel and Phase-2 operational state with namespaced Phase-2 tables. Import legacy stores and Phase-2 checkpoint rows into the unified schema, then keep verified originals only under backups for rollback.
- Generate `db/songs.json` atomically from SQLite as a human-readable catalog; detailed per-song manifests remain under `songs/final/` and are progressively updated.
- Supported explicit commands: `--download-models`, `--load-playlist`, and `--process-playlist`. Model preparation is independent of Spotify access.
- Run offset before heavy audio processing: fetch Spotify metadata, obtain LRCLIB synced lyrics, acquire YT Music reference audio, select/download the visual YouTube audio, run MMS/CTC search on the same LRCLIB phrase in both audio timelines, then robustly compare word timestamps. There is no energy-envelope fallback.
