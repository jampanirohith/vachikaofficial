# Complete project audit — Vachika Official 1.4.30-src-organized

**Status:** current post-refactor audit. The file `HISTORICAL_PRE_REPAIR_AUDIT.md` is retained as a historical record of the original archive and its findings; its old file paths and pre-repair counts are not current instructions. This document records the disposition of those findings after the repairs and the package migration.

## Executive result

The source tree has been reorganized into canonical domain packages. Active runtime code, tests and the authentication helper now import those canonical paths; the previous root-level forwarding modules have been removed. The two active engines remain intact in `src/phase2core/` and `src/rendercore/`. No project source was silently discarded as part of the package move beyond paths that the historical audit identified as inactive and that were removed from the supplied repaired baseline.

The post-refactor suite passes **110 tests**. Python compilation, fresh-archive extraction, configuration parsing/equality, import resolution, CLI help and read-only dry-run behavior have been checked. Measured results and file hashes are recorded in `BUILD_MANIFEST.json`.

A passing unit suite does not prove the quality of a live song-to-Reel job. Real service credentials, model downloads/inference, media sources, FFmpeg and the target machine's GPU/NVENC setup are not available to this packaging check. Likewise, music-alignment thresholds and 8D audio quality need validation with real annotated/reference songs and listening tests; no claim is made that these settings are universally calibrated.

## Canonical `src/` layout

| Package | Active responsibility | Main files |
|---|---|---|
| `src/providers/` | Spotify, Spotify OAuth/playlist, YT Music search/acquisition, LRCLIB | `spotify.py`, `spotify_auth.py`, `spotify_playlist.py`, `acquisition.py`, `ytm_media.py`, `lrclib.py` |
| `src/audio/` | Source decoding, stem separation, 8D and MP3 metadata | `source_audio.py`, `demucs.py`, `eightd.py`, `mp3_metadata.py` |
| `src/video/` | Visual-source selection, full-song offset, video-source and Reel workflow | `selection.py`, `youtube_source.py`, `synchronization.py`, `reel.py` |
| `src/orchestration/` | Song-stage sequencing, hook state, interrupted-run recovery | `pipeline.py`, `hook.py`, `recovery.py` |
| `src/storage/` | Central SQLite stores, DB maintenance and song-manifest serialization | `playlist_db.py`, `song_db.py`, `reel_db.py`, `maintenance.py`, `json_record.py` |
| `src/common/` | Hashing, metadata, GPU policy, model cache, command and HTTP helpers | `hashing.py`, `metadata.py`, `gpu.py`, `model_cache.py`, `utils.py`, `http_utils.py`, `ytdlp_utils.py` |
| `src/diagnostics/` | Runtime dependency and environment checks | `doctor.py` |
| `src/alignment/` | Adapter to the word-alignment engine | `adapter.py` |
| `src/phase2core/` | Active Telugu MMS/CTC word-alignment engine | Chunking, VAD, normalization, repair, validation, LRC and alignment DB |
| `src/rendercore/` | Active visual/audio render primitives | Source download/trim, probing, ASS/font handling, vertical render, assembly |
| `src/sql/` | Phase 2 workspace schema asset | `001_initial.sql` |

Only `src/config.py` remains as an application-wide module at the `src/` root; the other root entries are `__init__.py` and this source map. All internal callers, the Spotify OAuth setup script and regression tests use canonical imports. This prevents the earlier two-place layout (implementation package plus root-level alias) from continuing to grow.

## Historical findings and disposition

| Historical finding | Disposition | Evidence / remaining caveat |
|---|---|---|
| Duplicate-ISRC branch had a wrong bound-method argument count | **Fixed** | Regression test invokes the decision path and checks the call signature. |
| Reel trim treated a full video download as a guard-relative section | **Fixed** | Reel trim uses `hook_start + accepted_offset`; regression test checks mapped absolute trim time. |
| Reference-audio cache could survive a visual-video change | **Fixed** | Selected video ID/URL, derivation recipe, MP3/WAV hashes and outputs are provenance-bound; mismatch rebuilds. |
| 16 kHz/full-resolution source WAV could be reused from a different source | **Fixed** | Source hash, output hash, derivation parameters, decoded duration and audio layout must agree before cache reuse. |
| Demucs could pick a stale/partial stem from another attempt | **Fixed** | Each retry/device fallback has an isolated output folder; only a validated successful attempt is promoted. |
| Alignment timing repairs were overwritten during final merge | **Fixed** | Final serialized timing output retains the repaired timeline. |
| Custom `--config` path could be lost by downstream alignment | **Fixed** | Loaded config preserves the exact file and project root; no-argument config load is independent of current working directory. |
| Offset finder always accepted the top score and ignored ambiguity | **Fixed, thresholds need empirical calibration** | Minimum score and distinct-peak margin are applied and included in cache identity; `0.20` and `0.04` remain starting thresholds. |
| CUDA required/fallback settings were contradictory or ignored | **Fixed** | Config validation and main/Phase 2 model and Demucs paths share one policy; tests cover rejection and fallback behavior. |
| OAuth callback did not require exact path and state | **Fixed** | Exact callback endpoint and PKCE state are validated. |
| Dry run had network/DB/directory side effects | **Fixed** | CLI regression verifies read-only behavior when the configured DB does not exist. |
| CLI `--migrate-db` name overstated its behavior | **Clarified** | `--check-db` is the explicit name; `--migrate-db` remains a legacy alias for integrity checking and is documented as not performing schema migration. |
| Source/visual download timeout was not enforced end-to-end | **Fixed for yt-dlp downloads** | Configured progress-hook deadline and socket timeout are recorded/enforced. Provider HTTP requests use client-local timeouts. |
| Requests timeout helper patched `Session.request` globally | **Fixed** | `ytmusicapi` receives its own `requests.Session` with a client-local default timeout. Regression tests verify the global method is unchanged. |
| Configured DB directory was ignored by backup/restore | **Fixed** | Maintenance functions receive the effective config DB path; restore validates staging and supports rollback. |
| Font name/path provenance could disagree with the file used | **Fixed** | Font manager reads the embedded SFNT family; ASS rendering, Reel JSON, song JSON and Reel DB use resolved identity/path. Invalid explicit `font_path` fails clearly. |
| Final song JSON schema drifted across stage writers | **Implemented versioned contract** | `src/storage/json_record.py` writes `unified_song_master_v2`, schema version 2, algorithm IDs, config/source fingerprints, artifact hashes and a `.json.sha256` sidecar. Intermediate checkpoints remain distinct from final output. |
| DB schema logic could drift between embedded DDL and SQL files | **Resolved for Phase 2; central schemas are file-backed** | Phase 2 DB reads `src/sql/001_initial.sql`; the three central DB classes read their single corresponding DDL file under `database/`. Schema changes still require explicit versioned migrations. |
| `src/` was flat and mixed unrelated responsibilities | **Refactored** | Active code is grouped under provider/audio/video/storage/orchestration/common/diagnostic/alignment packages; root aliases and confirmed dead paths were removed after internal callers were migrated. |
| Downloaded 16 kHz/full-resolution caches needed one provenance contract | **Fixed** | Manifests include schema/derivation version, source hash, output hash, sample rate/channels/codec and duration checks. |
| Minimum Python runtime was undocumented | **Fixed** | Root README declares Python 3.12+. |

## Manifest contract

The canonical final song manifest is built and written through `src/storage/json_record.py`:

- `schema_version=2` and `record_schema="unified_song_master_v2"` identify the contract.
- `unified_pipeline.manifest` records creation time, pipeline version, config/source hashes, algorithm versions, output artifact paths, hashes, sizes/existence, validation status and content fingerprint.
- A file cannot contain a non-recursive hash of itself; the writer reports the JSON as a self-reference and stores its SHA-256 beside it in `<song>.json.sha256`.
- `playlist.db`, `songs.db`, `reels.db` and each workspace's Phase 2 DB have distinct ownership. General event data should not be moved between these stores without a deliberate schema/migration design.

## Asset and output identity

The configured Reel font filename identifies the asset (`Suravaram-Regular.ttf` by default), while `lyrics.font_name` is the embedded font family (`Suravaram`). The same resolved values are used for ASS generation and provenance. The packaged Baloo font is identified as `Baloo Tammudu 2` when selected; the pipeline no longer writes a hard-coded family label.

All final output paths in song/reel JSON are derived from configured output directories rather than fixed `songs/final` or `reels/generated` assumptions. Cached artifacts are tied to their selected input, derivation recipe, relevant processing settings and output hashes.

## Automated verification

The release validation record in `BUILD_INFO.md` and `BUILD_MANIFEST.json` describes the final tree. Local results: `python -m compileall -q main.py src tests scripts` passed; `pytest -q` passed with 110 tests; CLI `--help` completed without startup noise; a static audit checked 234 `src.*` import targets without a missing target; the read-only dry-run contract passed; both config JSON files parse and are identical. The final ZIP is also checked by extraction into a new directory, ZIP integrity verification, per-file manifest hash verification, and a fresh test/compilation run.

The release ZIP must not contain `__pycache__`, `.pytest_cache`, generated DBs, model weights, tokens, private cookies or a live hook queue. Target-machine and real-media checks remain explicitly listed below; they are not counted as passed.

## Remaining target-data checks

1. Run a small controlled song-to-Reel job on the user's target machine with a valid Spotify token, network access and prepared model weights.
2. Inspect Telugu word timing, repeated phrases, compounds, music-only gaps, 8D clipping/loudness/stereo compatibility, and video/audio sync from the actual outputs.
3. Calibrate offset thresholds on an annotated set containing official music videos, intros, edits, remixes, live versions and unrelated candidates; quantify false accepts and false rejects before changing the defaults.
4. Run Ruff and mypy in an environment where `requirements-dev.txt` can be installed. Syntax compilation and tests do not replace these optional static checks.

These checks are listed as remaining because they require target data, credentials, hardware or tools unavailable during the local packaging pass; they are not represented as completed.
