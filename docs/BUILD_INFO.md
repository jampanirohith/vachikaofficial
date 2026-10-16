# Build and validation record — 1.5.1-unified-db-mms-offset-v3

## Release contract

- `db/vachika.db` is the only active SQLite database. Playlist, song, Reel, stage, audit, offset-evidence and namespaced Phase-2 checkpoints are stored in this file.
- `db/songs.json` is an atomic catalog export derived from SQLite. The clean ZIP contains an empty scaffold only; first database initialization replaces it with the actual catalog. Do not hand-edit it as an operational source.
- `songs/final/<song>.json` is the progressive per-song processing/provenance manifest.
- A clean source release contains no live databases or user song catalog, credentials, model weights, generated song media, hook queue, cache directories, Python bytecode or test cache. The empty `db/songs.json` scaffold is included intentionally.
- Old `playlist.db`, `songs.db`, `reels.db`, and per-workspace `phase2.sqlite` filenames remain only in compatibility migration, recovery/backup conversion, tests and historical documentation. They are not active runtime destinations. Successfully migrated old DBs are backed up and removed from their active locations after import/integrity checks.

## Corrective work

- Removed unreferenced production imports and updated current documentation so it describes the single-file runtime and supported command aliases.
- Removed superseded pre-unification audit reports that prescribed the previous multi-database and energy-offset architecture.
- Removed stale root-level release version fields from `config.json.example`; `project.version` and `project.pipeline_version` are the canonical and matching version fields.
- The MMS/CTC offset method uses LRCLIB lyrics to find matching word anchors in both the acquired YT Music reference audio and the exact selected YouTube-video audio. It calculates the robust median of paired timestamp differences and rejects low-confidence/inconsistent evidence. There is no energy-envelope fallback.
- Legacy database migration uses SQLite's online backup API, integrity checks, ID remapping and foreign-key validation, including legacy per-song Phase-2 checkpoint rows. Old offsets are invalidated and deferred-hook entries are checked against the offset method version.
- `songs.json` is refreshed on skip/error/normal pipeline transitions covered by the catalog contract; per-song manifests are progressively updated with source, lyric-anchor, offset, audio and Reel evidence.

## Validation performed for this release

- `pytest -q`: **121 passed**.
- `python -m compileall -q main.py src tests scripts`: passed.
- `python main.py --help`: passed.
- Fresh `python main.py --dry-run` with no `config.json` or runtime DB: passed, read-only, no network call or DB initialization.
- Static import resolution: 91 Python files scanned, 263 `src.*` import targets checked, zero unresolved targets.
- Release archive checks: ZIP CRC, listed SHA-256/size entries and fresh-extraction checks are validated as part of release packaging and recorded in `docs/BUILD_MANIFEST.json`.

## Not claimed as verified

A fully authenticated song-to-Reel run was not executed in the packaging environment. Spotify OAuth and live YT Music/LRCLIB/YouTube calls, model downloads/inference, FFmpeg media output, CUDA/NVENC behavior and actual offset accuracy require credentials/network, representative real song/video pairs and target hardware. The MMS anchor thresholds should be benchmarked on annotated real media before production-quality accuracy is claimed. Ruff and mypy are not claimed as passing because they are not installed in this environment.
