# Source package map

All application implementation modules now live under domain packages. The only root-level Python module beyond `__init__.py` is `config.py`, which is the shared runtime configuration object. The previous root-level forwarding modules have been removed; internal imports, scripts, and tests use the canonical package paths below.

## Package layout

| Package | Responsibility | Key modules |
|---|---|---|
| `src/providers/` | External catalog/provider clients and source selection inputs | `spotify.py`, `spotify_auth.py`, `spotify_playlist.py`, `acquisition.py`, `ytm_media.py`, `lrclib.py` |
| `src/audio/` | Source-audio decoding, Demucs, 8D output and MP3 tags | `source_audio.py`, `demucs.py`, `eightd.py`, `mp3_metadata.py` |
| `src/video/` | Visual-source selection, MMS lyric-offset synchronization and Reel orchestration | `selection.py`, `youtube_source.py`, `lyric_offset.py`, `reel.py` |
| `src/orchestration/` | Stage ordering, hook decisions, retry/recovery and final orchestration | `pipeline.py`, `hook.py`, `recovery.py` |
| `src/storage/` | SQLite interfaces, configured DB maintenance and canonical song manifests | `playlist_db.py`, `song_db.py`, `reel_db.py`, `unified_db.py`, `song_catalog.py`, `maintenance.py`, `json_record.py` |
| `src/common/` | Reusable config-independent utilities and policies | `hashing.py`, `utils.py`, `metadata.py`, `gpu.py`, `model_cache.py`, `ytdlp_utils.py`, `http_utils.py` |
| `src/diagnostics/` | Environment and dependency diagnostics | `doctor.py` |
| `src/alignment/` | Application adapter into Phase 2 alignment | `adapter.py` |
| `src/phase2core/` | Active Telugu word-level alignment engine | CTC/MMS, VAD, chunking, Telugu normalization, repair and validation |
| `src/rendercore/` | Active FFmpeg/ASS rendering primitives | Video/audio probing, font resolution, trim, vertical rendering and assembly |
| `src/sql/` | Canonical Phase 2 SQL DDL, namespaced into the unified operational database | `001_initial.sql` |

## Dependency direction

- `main.py` parses commands and dispatches to provider, storage, diagnostics and orchestration modules. It contains no audio/video transforms.
- `orchestration/` coordinates stages; provider clients and transform modules do not decide the entire song workflow.
- `providers/` discovers or retrieves external information. The YT Music downloader accepts an already-selected URL; it does not search for a substitute recording.
- `audio/` and `video/` operate on explicit paths/timestamps and include provenance for generated artifacts.
- `storage/` owns DB access and final manifest serialization. SQL assets are loaded from canonical schema files, not separately duplicated in the DB wrapper.
- `phase2core/` and `rendercore/` are retained as active subsystems rather than treated as obsolete duplicate implementations.

## Manifest and schema ownership

`src/storage/json_record.py` owns the versioned final-song record contract (`unified_song_master_v2` / schema version 2). It records config/source fingerprints, algorithm versions, output artifact hashes and a sidecar hash for the JSON file itself. The unified operational database lives at `db/vachika.db`. Playlist, song, Reel and Phase 2 tables share that file with distinct schema/table namespaces. Existing per-song Phase 2 checkpoint rows are imported with remapped IDs into `phase2_*` tables and then archived with integrity-verified SQLite backups; the active adapter uses a unique stable song identity. The per-song JSON is the detailed processing record and `db/songs.json` is a catalog export.

## Runtime files

The sole active SQLite DB is `db/vachika.db`; `db/songs.json` is a generated catalog. Model checkpoints, song workspaces, generated audio/video, logs, selected-media caches and the hook queue are runtime state. Keep them in configured paths; do not move them into `src/` or ship them inside source archives.

## Change policy

Add behavioral tests before changing stage outputs or cache keys. Re-run the suite after package/path changes. A module should be removed only after its runtime, CLI, scripts, tests and documentation references have been migrated or explicitly retired together.
