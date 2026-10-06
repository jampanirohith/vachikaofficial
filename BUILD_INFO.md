# Build information — Unified Spotify-first final

## Build

Version: `1.4.19-final`

This build integrates the supplied Phase 1, Phase 2, and Phase 3 projects under the current Spotify-first unified contract.


## Release 1.4.18 fix

- Fixed a second whole-song 8D frame-alignment bug in the lyric-density gain timeline.
- The density timeline now uses ceiling block count so the generated gain vector always covers the full canonical audio frame count, including non-block-aligned songs.
- Added regression coverage for the exact non-block-aligned frame-count failure observed at runtime.

## Release 1.4.17 fix

- Fixed whole-song 8D rendering when Demucs stems contain small frame-count differences after decoding/resampling.
- Canonicalized the shortest valid stem frame count and cropped **every** stem, including drums, before mixing.
- Added regression coverage for unequal stem lengths and the exact drums mixing path that previously caused NumPy broadcasting failure.
- Verified the complete active test suite after the fix.

## Active behavioral contract

- Spotify is the sole playlist/source-of-entry authority.
- The first normal run for a new playlist ID performs exactly one complete Spotify Web API playlist snapshot, atomically records every playlist entry in exact Spotify order in `playlist.db`, prints completion, and exits.
- The first run performs no per-track Spotify detail calls, no Spotify artwork download, no YT Music search/media, no lyrics, no Demucs, no MMS/CTC, no 8D, no hook prompt, no visual-video search, no offset matching, and no Reel rendering.
- Later runs never re-fetch an existing playlist ID; they process the frozen snapshot one song at a time in ascending stored playlist position.
- There is no active playlist view-count, play-count, popularity, engagement, or ranking logic.
- Spotify supplies the complete per-song track/album/artist catalog payload, raw catalog JSON, exact album artwork bytes, and canonical ISRC.
- Spotify audio is never downloaded.
- ISRC is the canonical duplicate identifier. Duplicate lookup is limited to finalized prior songs and never to in-progress songs.
- YT Music is media-only. Search uses `title + album`, and the first result whose known duration is within the configured Spotify-duration tolerance is selected.
- YT Music media discovery uses `ytmusicapi.search(title + album)` and closest Spotify-duration selection; the selected `music.youtube.com` URL is handed to yt-dlp for high-quality audio download and FFmpeg MP3 conversion. yt-dlp is never used for search.
- Demucs is run once per source version and shared by alignment and whole-song 8D.
- Whole-song 8D covers the complete original song and is persisted to `songs/final` before hook selection.
- YouTube visual-video selection is performed exactly once per song; the first non-lyrics result from `title + album + video song hd` is persisted and its exact `youtube.com/watch?v=...` URL is reused for reference-audio download, offset matching, and Reel rendering. Hook selection never performs another visual search.
- Hook timing is manual terminal input and persisted in integer milliseconds.
- Final song/Reel promotion is gated by validation and performed safely.
- SQLite persistence converts Windows `Path` objects to strings before binding.
- Historical Phase 1/2/3 source trees and plans remain archived for provenance and are not active runtime authority.

## Release 1.4.16 fix

- Fixed the unified pipeline calling `SongsDB.set_lyrics_words(...)` without the method existing in the active SongsDB implementation.
- Added an atomic, idempotent SongsDB persistence method for `lyric_lines` and `lyric_words`, including line/word foreign-key linkage and alignment-run replacement on retry/cache reuse.
- Added an AST contract test that verifies every `songs_db.<method>()` call in `src/pipeline.py` exists on `SongsDB`.
- Added an end-to-end SQLite regression test for alignment lyric-line/word persistence.
- Verified the complete active test suite after the fix.

## Release 1.4.15 fix

- All ML model downloads now use one project-persistent `models/` cache shared across the entire playlist. Per-song alignment temp directories no longer own a model cache.
- Hugging Face MMS assets use `models/huggingface/hub/`; PyTorch/Demucs checkpoints use `models/torch/`; auxiliary torch cache uses `models/cache/`.
- The cache environment is propagated to Demucs subprocesses and initialized before Phase-2 VAD/MMS model loading.
- Added regression coverage for persistent model-cache routing and Demucs cache propagation.

## Release 1.4.14 fix

- Phase-2 alignment database initialization now has a packaged runtime schema at `src/sql/001_initial.sql`, with an archived-schema fallback and explicit missing-resource diagnostics.

## Field defects incorporated

- Missing `playlist_snapshot()` logger argument.
- Recovery import ordering on `--reconcile`.
- Passing a plain dictionary instead of the full `Config` object to acquisition.
- SQLite `WindowsPath` binding failure after a successful media download.
- Cryptic old YT Music playlist parser/authentication failure is no longer relevant because Spotify owns playlist ingestion.
- Obsolete playlist popularity/count lookup path removed completely from the active runtime.
- First-run snapshot is committed before downstream song/Reel databases are initialized.
- YT Music direct-stream acquisition rejects unknown-duration search results instead of accepting an unverified match.
- ISRC duplicate comparisons normalize ISRC formatting before lookup.
- Windows FFmpeg explicit WAV-container handling retained.
- Demucs cache/stage identity and SQLite child-row replacement safety retained.

## Validation

- Unified active tests: 58 passed after the alignment SongsDB API fix.
- Archived Phase 1/2/3 suites are retained in `archives/`; they were not re-executed in this packaging pass.
- Python compilation: passed.
- `playlist.db`, `songs.db`, `reels.db`: SQLite integrity `ok`, user version `400`.
- Active requirements include yt-dlp for audio acquisition only.

## Production boundary

The packaging environment is not a Windows/CUDA/online production runner. No real Spotify, YT Music, LRCLIB, Hugging Face, CUDA, NVENC, or full media execution is claimed here. Run `python main.py --doctor` on the target Windows machine before production processing.

Release 1.4.3-final: Spotify-first playlist snapshot architecture locked; historical direct-stream wording is superseded by the 1.4.5+ selected-URL yt-dlp contract.


Release 1.4.3 regression fix: SongsDB parent-row bootstrap now occurs before any stage_runs or audit_events insert, preventing SQLite FOREIGN KEY failures when Spotify metadata acquisition fails. Required bootstrap fields are normalized in one connection for Windows-safe SQLite behavior.

Release 1.4.4: fixed NULL duration bootstrap and hardened SQLite parent/child lifecycle.

Release 1.4.5: YT Music search remains ytmusicapi-only; actual audio acquisition now uses yt-dlp on the selected YT Music URL with best-available audio and 320 kbps MP3 post-processing, with optional authenticated browser/cookie support.

- LRCLIB exact `/api/get` lookup is followed by `/api/search` fallback for synced lyrics; lyrics are fetched before visual-video acquisition so available LRC is not lost behind a later video-stage failure.


## 1.4.19 final fix
The 8D renderer no longer shadows the imported `duration_ms()` helper with its expected-duration parameter. Output duration validation now computes the actual MP3 duration and compares it against the expected acquisition duration.
