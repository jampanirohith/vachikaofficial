# v1.4.28.1 — Telugu LRC script gate

- Added a hard post-selection lyrics gate requiring at least one Telugu Unicode code point (U+0C00–U+0C7F) in the complete selected LRC.
- Romanized/Latin-only synchronized lyrics are now terminally skipped before alignment, visual search, 8D, hook, or reel generation.
- The gate applies to both freshly fetched and cached LRC selections.
- Bumped the lyrics fingerprint strategy so existing cached lyrics are re-evaluated under the new rule.
- Added regression tests for Telugu acceptance, Romanized rejection, gate ordering, and fingerprint invalidation.

## v1.4.28-gpu-hardening
- Made HTDemucs CUDA-first with `--device cuda` routing and device-aware cached-stem validation; CPU-generated stems are rebuilt when CUDA becomes available.
- Made Telugu MMS/CTC alignment CUDA-first and record requested/actual device provenance.
- Made Silero VAD CUDA-first with safe CPU fallback and device-aware activity-cache invalidation.
- Kept NVENC as the preferred Reel/video encoder with runtime detection and `libx264` fallback.
- Added centralized CUDA device resolution and diagnostics.
- Active tests: 74 passed; Python compileall passed.
- Physical NVIDIA validation was not possible in the packaging environment because its installed PyTorch is CPU-only.

## v1.4.27-final
- Fixed a fresh-run crash when tracked `songs/final/hook_queue.json` contains an entry whose generated song package is missing locally.
- The pipeline now detects stale queue entries, marks them `stale` with `SOURCE_PACKAGE_MISSING`, and continues through the normal acquisition/alignment/8D pipeline instead of raising `HOOK_QUEUE_SOURCE_MISSING`.
- This specifically supports clean Git checkouts where `hook_queue.json` is tracked but generated audio/LRC/8D/JSON files are intentionally ignored.
- Added regression coverage for stale hook-queue source recovery.
- Unified active test suite: 72 passed.

## v1.4.24-final
- Added `hook.skip` configuration to defer interactive hook selection without rerunning expensive stages.
- Added persistent `songs/final/hook_queue.json` with song/path/LRC/hook timing and exact selected YouTube visual URL + offset provenance.
- Added Reel-only resume path: next run consumes hook times from the queue JSON and builds the Reel directly, without Spotify/ytmusic/lyrics/Demucs/alignment/8D reruns or a second YouTube search.
- Queue accepts human-editable `hook_start_time` / `hook_end_time` (`MM:SS.xxx`) and derives milliseconds automatically.
- Completed queue entries are retained as audit history rather than deleted.

# 1.4.23

- Fixed `NameError: reel_final is not defined` after successful Reel rendering.
- Explicitly defines canonical `reels/generated/<name>_reel.mp4` and `.json` paths before copying, validating, hashing, and updating the database.
- Removed stale `targets[...]` references from final song/Reel bookkeeping.
- Added regression tests for canonical Reel final-path definition and stale target references.
- All 68 tests pass when executed file-by-file.

# 1.4.22

- Fixed a Reel-stage SQLite crash caused by calling `ReelsDB.upsert()` with the arguments in the wrong order.
- Corrected all Reel persistence calls to use the canonical `upsert(table, pk, key, data)` contract.
- Added regression coverage so Reel table names are always supplied as the first argument.

# 1.4.20

- Fixed a whole-song 8D manifest crash when the Demucs result mapping contained metadata keys such as `device: "cpu"`.
- The 8D renderer now canonicalizes and validates the four required audio stems (`vocals`, `drums`, `bass`, `other`) before reading or hashing them.
- 8D manifests now hash only actual stem files, never Demucs metadata values.
- Added regression coverage for mixed Demucs stem/metadata mappings.

# 1.4.19

- Fixed 8D render duration validation parameter shadowing the `duration_ms()` helper.
- Added regression coverage for the render function API.

## 1.4.18-final

- Fixed 8D lyric-density gain vector truncation when audio frame count is not divisible by the 100 ms block size.
- Added regression coverage for non-block-aligned whole-song frame counts.

## 1.4.17-final

- Fixed 8D render crash caused by unequal Demucs stem frame counts (`ValueError: operands could not be broadcast together`).
- All stems are now cropped to one canonical frame count before spatial mixing.
- Added regression tests for stem-length normalization and drums mixing.

## 1.4.16-final

- Fixed a runtime API mismatch where `src/pipeline.py` called `SongsDB.set_lyrics_words(...)` but `SongsDB` did not implement it.
- Added atomic/idempotent persistence of aligned lyric lines and word timings into `lyric_lines` and `lyric_words`.
- Added a pipeline-to-SongsDB API contract test to catch missing database methods before release.
- Added alignment line/word persistence regression coverage.
- Verified the complete active test suite: 58 passed.

## 1.4.15-final

- Fixed model-cache placement: MMS/Hugging Face and Demucs/PyTorch checkpoints are now stored under the project-level `models/` directory, not inside per-song `temp/<song>/alignment/models`.
- Model caches are persistent and shared across songs, so a checkpoint is downloaded once and reused for subsequent songs.
- Added persistent cache environment routing for Hugging Face, Transformers, PyTorch, Silero VAD, and Demucs subprocesses.
- Added regression coverage for project-persistent model caching and Demucs cache propagation.
- Verified the complete active test suite: 56 passed.

## 1.4.14-final

- Fixed the Phase-2 runtime database schema packaging defect that caused alignment to crash with `FileNotFoundError: src\sql\001_initial.sql`.
- Added the Phase-2 `001_initial.sql` schema to the active unified `src/sql/` runtime tree and kept the archived schema as a compatibility fallback.
- Added a regression test that constructs a fresh Phase-2 database and verifies the required core tables and schema version.
- Revalidated the full unified test suite and Python compilation.

## 1.4.13-final

- Visual YouTube search is now explicitly single-resolution per song. The selected non-lyrics `title + album + video song hd` result is persisted and reused; hook selection cannot trigger a second visual search.
- Reference audio download requires and uses the exact selected YouTube URL.
- Added regression tests for single visual selection and exact-URL reuse.
- Kept CUDA as primary with CPU fallback and pre-hook final-package promotion.

# Changelog

## Current correction — unauthenticated YT Music search
- Removed the mandatory YT Music `browser.json` authentication gate from the active acquisition and visual-reference paths.
- YT Music catalog search now always uses the unauthenticated `YTMusic()` client.
- Replaced closest-duration-only selection with weighted title/artist/album/duration candidate scoring, while retaining the Spotify-duration tolerance gate.
- No alternate search/download fallback was added.
- Validation: 79 tests passed; Python compileall passed.



## 1.4.8-final
- Visual YouTube search is exactly `title + album + video song hd`; skip `lyric`, `lyrics`, `lyrical`; first remaining result; no duration filtering.
- Visual reference audio downloads the exact selected YouTube URL rather than reconstructing a YT Music URL.
- LRCLIB exact `/api/get` is followed by `/api/search` fallback for synced lyrics.
- Lyrics are fetched before visual-video acquisition, preventing later video failures from hiding an available synced LRC.

# 1.4.7-final

- YT Music audio selection now evaluates the full search window and chooses the closest duration within the Spotify duration gate.
- YT Music visual selection now evaluates multiple queries and duration-compatible non-lyrics candidates instead of failing on the first search shape.
- Added bounded HTTP timeouts around ytmusicapi bootstrap/search to prevent indefinite visitor-ID/network hangs.
- Reused YT Music client within a pipeline configuration to avoid duplicate authentication/bootstrap calls.
- Demucs now immediately uses configured CPU fallback when CUDA is unavailable and can fall back to CPU after runtime CUDA failures.
- Default runtime enables CPU fallback.

# Changelog

## 1.4.7-final

- Fixed MP3 ID3 embedding on Mutagen 1.47+: TDRC release-date frames are now replaced using the frame HashKey instead of indexing the ID3TimeStamp value, eliminating the `ID3TimeStamp + str` TypeError that incorrectly turned no-LRC skips into pipeline errors.
- Managed TXXX fields such as `SPOTIFY_ISRC` are now replaced rather than duplicated during recovery/retry embedding.
- Added regression coverage for TDRC replacement and managed TXXX replacement.
- Fixed the acquisition metadata contract: yt-dlp provenance is retained under `stream`/`yt_dlp`, while canonical title/artist/album/duration fields are now written at the top level.
- Added backward-compatible metadata normalization for nested/legacy `master.info.json` files, so existing downloaded masters do not need to be reacquired after this upgrade.
- Pass the frozen Spotify playlist entry as a final metadata fallback during acquisition normalization.
- Persist the selected ytmusicapi candidate metadata in the acquisition info JSON for auditable provenance and deterministic recovery.
- Added regression coverage for nested info.json metadata and acquisition normalization.

## 1.4.5-final

- Restored yt-dlp as the actual YT Music audio downloader after ytmusicapi direct-stream acquisition proved unreliable.
- Kept ytmusicapi as the only audio discovery/selection layer: `title + album`, first known-duration result within Spotify-duration tolerance.
- yt-dlp receives only the selected `https://music.youtube.com/watch?v=...` URL and runs with `noplaylist=true` / `bestaudio/best`.
- Added atomic temporary output handling and final duration verification against Spotify.
- Default final audio is MP3 at 320 kbps through FFmpeg; actual source format/bitrate/sample-rate provenance is stored in the acquisition info JSON.
- Added optional yt-dlp browser-cookie and cookie-file configuration for the user's authenticated environment.
- Updated doctor, requirements, README, project manifest, and active tests to reflect the new contract.


## 1.4.3-final

- Spotify is now the sole playlist source and source-of-entry authority.
- First run for a new playlist ID is snapshot-only: one complete Spotify Web API scan, atomic `playlist.db` commit, then exit.
- Playlist order is frozen exactly as returned by Spotify and never re-ranked.
- Removed active view-count, play-count, popularity, engagement, and priority logic.
- Spotify full track/album/artist catalog data and raw JSON are retained per song, including exact original album artwork bytes.
- ISRC is the canonical duplicate identifier and is normalized before duplicate lookup.
- Removed all active yt-dlp code, requirements, downloader modules, and fallbacks.
- YT Music media search now uses `title + album`, chooses the first known-duration result within Spotify-duration tolerance, and downloads only direct streams exposed by `ytmusicapi` using HTTP + FFmpeg.
- Hardened first-run lifecycle so song/Reel databases are initialized only after the Spotify snapshot is committed.
- Retained the Phase 2 Telugu MMS/CTC alignment, canonical word-level LRC/SYLT, Demucs reuse, whole-song 8D, hook, YouTube global offset, Reel rendering, cache, recovery, and final atomic validation pipeline.
- Added/updated regression tests for Spotify-first ingestion, no-priority schema, direct YT Music media, ISRC duplicate semantics, and Windows path persistence.

## Historical releases

Earlier Phase 1/2/3 and v1.1.x/v1.3.x changes remain in the archived project material and historical audit records. They are not active runtime instructions when they conflict with `PROJECT_PLAN.md`.

## 1.4.2-final
- Finalized Spotify-first playlist authority and exact playlist-order processing contract.
- Added schema-version quarantine so stale/unknown databases cannot be silently mutated by the new runtime.
- Removed remaining legacy downloader identifiers from active runtime provenance/docs.

## 1.4.3
- Fix the SongsDB foreign-key lifecycle so every song parent row is created before `stage_runs` and `audit_events`.
- Ensure bootstrap `basename` and `duration_ms` satisfy required columns without nested SQLite reads.
- Add regression tests for Spotify-stage failure and audit persistence.

## 1.4.4
- Fix `songs.duration_ms` bootstrap when Spotify playlist rows omit duration by normalizing NULL to 0 before authoritative Spotify catalog enrichment.
- Make stage/audit writes defensively create the parent song row so error handling cannot produce a secondary foreign-key failure.
- Add regression tests for NULL-duration bootstrap and orphan stage/audit calls.

## 1.4.8-final
- YouTube visual selection now uses exactly `title + album + video song hd`.
- Visual selection ignores duration completely and selects the first result whose title does not contain `lyric`, `lyrics`, or `lyrical`.
- LRCLIB keeps the exact `/api/get` lookup, then falls back to `/api/search` with title+artist and title queries and ranks only candidates with valid synced lyrics.
- Finalized songs continue to publish both the line-synced `.lrc` and word-level `.lrc` artifacts.
