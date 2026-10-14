# Current Project Plan — Vachika Official

**Current release authority — 1.4.30-src-organized**

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
- YouTube offset matching uses the **full-song energy envelope**. A result below the minimum score or too close to a distinct competing peak is rejected for review rather than silently trusted.
- The final song package records audio, lyric, 8D, visual selection, offset, font and Reel provenance. Promotion happens only after artifact validation.

## Normal run behavior

1. A new playlist ID is fetched once, persisted, and the first normal run exits after snapshot completion.
2. Later runs process actionable tracks in stored Spotify order.
3. Stage/workspace checkpoints are reused only when cache identity and artifact checks pass.
4. `--resume` reconciles interrupted entries before processing; `--ingest-only` exits after snapshot verification; `--dry-run` is read-only and makes no API calls.
5. `--prepare-models` downloads the active MMS/CTC+Telugu adapter, HTDemucs and Silero VAD weights without processing songs.

## Supported runtime

Python 3.12+, FFmpeg/FFprobe, SQLite and the dependencies pinned in `requirements.txt`. CUDA is preferred for supported PyTorch stages; CPU fallback is enabled by default. NVIDIA NVENC is preferred for rendering and software encoding is the fallback.

## Change policy

- Preserve `src/phase2core/` and `src/rendercore/`; they are active implementations.
- Do not ship generated per-song state, model weights, hook queues, credentials or cookies.
- Add regression tests for output behavior and cache invalidation when changing pipeline stages.
- Treat offset thresholds and media-selection changes as output-affecting behavior and validate them against a representative test set before changing defaults.
