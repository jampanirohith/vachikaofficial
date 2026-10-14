# Pipeline architecture

## Active processing path

| Stage | Entry point / module | Responsibility | Persistent evidence |
|---|---|---|---|
| CLI and frozen playlist | `main.py`, `src/providers/spotify.py`, `src/providers/spotify_playlist.py`, `src/storage/playlist_db.py` | Snapshot Spotify items once, preserve exact order, control status/retry/recovery | configured `playlist.db` |
| Track identity and media | `src/orchestration/pipeline.py`, `src/common/metadata.py`, `src/providers/acquisition.py`, `src/providers/ytm_media.py`, `src/video/selection.py` | Spotify metadata/artwork, ISRC duplicate gate, YT Music identity/duration matching, exact selected-URL download | `songs.db`, per-song workspace manifests |
| Synchronized lyrics | `src/providers/lrclib.py` | LRCLIB `/api/get`, `/api/search` fallback, synchronized Telugu-script gate | lyric source and final song manifest |
| Source audio and stems | `src/audio/source_audio.py`, `src/audio/demucs.py`, `src/common/model_cache.py`, `src/common/gpu.py` | Lossless and 16 kHz derivatives; HTDemucs with attempt-isolated retries and source manifests | source manifests, Demucs manifest |
| Word alignment | `src/alignment/adapter.py`, `src/phase2core/` | VAD, chunking, MMS/CTC forced alignment, Telugu normalization, word timing repair, LRC/SYLT and validation | per-song `phase2.sqlite`, alignment JSON/LRC |
| Whole-song 8D | `src/audio/eightd.py` | Mix separated stems into a whole-song 8D output and validate output properties | 8D stage rows and output hashes |
| Visual selection and offset | `src/video/youtube_source.py`, `src/video/synchronization.py` | Select one visual video, download reference audio for that exact URL, qualify full-song offset | selected-video JSON and offset diagnostics |
| Reel rendering | `src/video/reel.py`, `src/rendercore/` | Crop 8D audio, trim the selected source on its absolute timeline, vertical layout, word-level ASS overlay and validation | `reels.db`, Reel JSON and validation report |
| Data and recovery | `src/storage/`, `src/orchestration/recovery.py`, `src/diagnostics/doctor.py` | SQLite access/maintenance, stage evidence, interrupted-run reconciliation and diagnostics | three central DBs and run-state JSON |

## Data ownership

- `playlist.db`: immutable playlist snapshot, position/order and playlist-entry lifecycle.
- `songs.db`: canonical song details, source/lyrics/alignment/8D information and song artifact state.
- `reels.db`: hook, visual-video offset, subtitle/font, output and Reel-validation state.
- Per-song `phase2.sqlite`: temporary alignment engine state for a workspace; it is not a replacement for the central `songs.db`.
- Final song JSON is the versioned artifact/provenance record, and SQLite is the operational index and lifecycle audit. Generated temporary files must not be treated as final outputs.

## Cache identity rules

- Full-resolution and 16 kHz audio derivatives are validated against the source SHA-256, derivation parameters and output hash; duration and audio layout must remain consistent.
- Demucs output is reusable only when source hash, model, device policy, segmentation settings and expected stem hashes match. Every attempt/fallback has a separate output directory.
- Reference audio is bound to the exact selected video ID/URL, derivative recipe and MP3/WAV hashes. A selected-video change invalidates the complete reference-audio cache.
- Complete source-video downloads store the selected request identity, duration and output hash. Reel trim timestamps are absolute source-video timestamps after applying the accepted audio/video offset.
- Model caches record model identity and a per-model preparation result. A missing/corrupt cache must fail clearly or be rebuilt; it must not be silently accepted based on file size alone.

## Manifest and schema authority

`src/storage/json_record.py` is the single writer for the final song JSON schema (`unified_song_master_v2`, schema version 2). Stage checkpoints remain separate operational state; they are not alternative final-song schemas. The JSON manifest includes source/config fingerprints, method identifiers, artifact metadata, validation status and a sidecar SHA-256 for the JSON itself.

`src/sql/001_initial.sql` is the authoritative DDL for the per-song Phase 2 database, loaded directly by `src/phase2core/db.py`. The central operational databases use `database/playlist_schema.sql`, `database/songs_schema.sql` and `database/reels_schema.sql` through their storage classes.

## Runtime and external dependencies

The app can validate CLI/configuration/cache/DB contracts without downloading models or accessing services. A real song-to-Reel run requires valid Spotify credentials, working provider/network access, model weights, FFmpeg/FFprobe and validation on the intended CPU/GPU/encoder configuration. See `docs/FULL_PROJECT_AUDIT.md` for what was and was not verified in the packaged environment.
