# Pipeline architecture

## Active processing path

| Stage | Entry point / module | Responsibility | Persistent evidence |
|---|---|---|---|
| CLI and frozen playlist | `main.py`, `src/providers/spotify.py`, `src/providers/spotify_playlist.py`, `src/storage/playlist_db.py` | Snapshot Spotify items once, preserve exact order, control status/retry/recovery | `db/vachika.db` + derived `db/songs.json` |
| Track identity and metadata | `src/orchestration/pipeline.py`, `src/common/metadata.py`, `src/providers/spotify.py` | Spotify track/album/artist metadata, artwork, ISRC duplicate gate | unified DB, song JSON |
| Synchronized lyrics | `src/providers/lrclib.py` | LRCLIB `/api/get`, `/api/search` fallback, synchronized Telugu-script gate; lyrics are the only transcript source for offset search | lyric source and progressive song manifest |
| Reference/visual audio and MMS offset | `src/providers/acquisition.py`, `src/providers/ytm_media.py`, `src/video/selection.py`, `src/video/youtube_source.py`, `src/video/lyric_offset.py` | Select/download the YT Music reference recording, select one visual YouTube video and download its audio, search the same opening LRCLIB phrase in both timelines with MMS/CTC, calculate robust median offset from paired word timestamps | source selection state, both anchor JSONs, offset JSON and unified DB records |
| Source audio and stems | `src/audio/source_audio.py`, `src/audio/demucs.py`, `src/common/model_cache.py`, `src/common/gpu.py` | Provenance-bound lossless/16 kHz derivatives; HTDemucs with attempt-isolated retries and validated output promotion | source manifests, Demucs manifest, unified DB stages |
| Full word alignment | `src/alignment/adapter.py`, `src/phase2core/` | VAD, chunking, full-song MMS/CTC forced alignment, Telugu normalization, timing repair, LRC/SYLT and validation | namespaced `phase2_*` tables in `db/vachika.db`, alignment JSON/LRC |
| Whole-song 8D | `src/audio/eightd.py` | Mix separated stems into a whole-song 8D output and validate output properties | 8D stage rows and output hashes |
| Hook and Reel rendering | `src/orchestration/hook.py`, `src/video/reel.py`, `src/rendercore/` | Select hook, trim the selected video on its absolute timeline using the MMS offset, vertical layout, word-level ASS overlay and artifact validation | `db/vachika.db`, progressive song JSON, Reel JSON and validation report |
| Data and recovery | `src/storage/`, `src/orchestration/recovery.py`, `src/diagnostics/doctor.py` | SQLite access/maintenance, stage evidence, atomic catalog export and interrupted-run reconciliation | unified `vachika.db`, derived `songs.json`, per-song state and run manifests |

## Data ownership

- `db/vachika.db`: the sole active SQLite database for playlist snapshots/order, song metadata and source selections, processing stages, alignment checkpoints, hook/Reel records, quality state and recovery evidence.
- `db/songs.json`: generated catalog export. SQLite is authoritative; the JSON is regenerated atomically and must not be edited as operational state.
- `songs/final/<song>.json`: full per-song record, updated after meaningful stages; includes metadata, LRCLIB source, both MMS anchors, offset formula/evidence, output paths/hashes, model and algorithm versions, hook/Reel status and validation.
- `db/backups/`: durable legacy database and pre-restore archives. Legacy per-song Phase-2 databases are archived after verified backup; live Phase-2 checkpoints are namespaced as `phase2_*` tables in `vachika.db`.

The first database-enabled command imports old `playlist.db`, `songs.db` and `reels.db` records into the unified database and archives the originals. Migration uses common-column imports, ID-remapped Phase-2 checkpoint migration, integrity checks and online SQLite backups; legacy copies are retained only under `db/backups/` for rollback/review. Normal runtime does not create separate `playlist.db`, `songs.db`, `reels.db` or per-song `phase2.sqlite` files.

## Cache identity rules

- Full-resolution and 16 kHz audio derivatives are validated against the source SHA-256, derivation parameters and output hash; duration and audio layout must remain consistent.
- Demucs output is reusable only when source hash, model, device policy, segmentation settings and expected stem hashes match. Every attempt/fallback has a separate output directory.
- The YT Music song-reference acquisition is bound to its selected result and source hash. The visual-video audio is separately bound to the exact selected YouTube ID/URL, derivative recipe and MP3/WAV hashes. A selected-video change invalidates the video-audio anchor and offset caches.
- Complete source-video downloads store the selected request identity, duration and output hash. Reel trim timestamps are absolute source-video timestamps after applying the accepted audio/video offset.
- Model caches record model identity and a per-model preparation result. A missing/corrupt cache must fail clearly or be rebuilt; it must not be silently accepted based on file size alone.

## Manifest and schema authority

`src/storage/json_record.py` is the single writer for the final song JSON schema (`unified_song_master_v2`, schema version 2). Stage checkpoints remain separate operational state; they are not alternative final-song schemas. The JSON manifest includes source/config fingerprints, method identifiers, artifact metadata, validation status and a sidecar SHA-256 for the JSON itself.

`src/sql/001_initial.sql` defines the Phase-2 table group. At runtime, `src/phase2core/db.py` applies the `phase2_` table/index namespace and creates those tables inside the same `db/vachika.db` file used by the playlist, song and Reel repositories. The central logical table groups are declared in `database/playlist_schema.sql`, `database/songs_schema.sql` and `database/reels_schema.sql`; those are SQL source files, not database files. Each group uses a namespaced schema-version table, while all groups share one physical SQLite database.

## Runtime and external dependencies

The app can validate CLI/configuration/cache/DB contracts without downloading models or accessing services. A real song-to-Reel run requires valid Spotify credentials, working provider/network access, model weights, FFmpeg/FFprobe and validation on the intended CPU/GPU/encoder configuration. See `docs/BUILD_INFO.md` for the exact validations completed and the runtime checks that still require credentials, model weights, media access or target hardware.
