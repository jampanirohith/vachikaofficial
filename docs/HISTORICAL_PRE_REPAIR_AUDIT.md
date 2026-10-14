> **Historical record — pre-repair audit.** This report describes the original ZIP before the cleanup and fixes in version `1.4.29-project-cleanup`. Some findings and file dispositions below have since been addressed or superseded. For the current state, read `FULL_PROJECT_AUDIT.md`, `CHANGELOG.md`, and `BUILD_INFO.md`. The complete replacement ZIP is the source of truth for current files.

---

# Vachika Official — full ZIP and pipeline audit

**Audit date:** 13 October 2026  
**Scope:** `vachikaofficial-main (1).zip`, including the full `src/` tree, entry point, schemas, tests, scripts, configuration example, assets and documentation.  
**Mode:** Analysis only. No project source files were changed. The diagnostic fixtures used to isolate test failures were temporary and removed. Python/test cache files created while running checks exist only in the extracted working copy, not in the original ZIP.

---

## 1. Executive summary

This is a substantial Python media-processing application, not a small collection of scripts. The active route is a Spotify-first song-processing pipeline, with a second, large word-level Telugu lyric-alignment engine and a Reel-rendering subsystem. The high-value implementation already exists; a big-bang cleanup or deleting whole directories would risk breaking essential behavior.

**The right order is: fix data/output correctness first, tighten test coverage second, and reorganize files third.** Preserve the alignment and rendering subpackages. Retire only demonstrably unused/legacy paths after their scripts/tests and output dependencies have been accounted for.

### Findings at a glance

| Area | Audit finding | Priority |
|---|---|---:|
| Duplicate ISRC gate | Bound method is called with one argument too few; the duplicate branch raises `TypeError`. | P0 — fix before ordinary processing |
| Reel video selection | Code requests a section, but yt-dlp downloads the full video; trimming treats it as though it began at the guarded section. The Reel can show the wrong part of the video. | P0 — output correctness |
| YouTube reference-audio cache | Existing `reference_audio.wav` is reused without checking that it belongs to the currently selected video. A new visual selection can be matched against old audio. | P1 |
| Word timing normalization | Final alignment timing repairs are applied, then a new merge overwrites them before final validation/serialization. | P1 |
| 16 kHz derived-audio cache | Existing derived WAV is accepted based on file size alone; the manifest can be rewritten to describe a file derived from a different source. | P1 |
| Demucs output selection | Stem lookup searches every WAV under the persistent raw output directory, not only the successful run's output. Old/partial output can be selected. | P1 |
| Custom config | `Config.load(custom_path)` remembers the correct root but `config_path` always resolves to `<root>/config.json`; the alignment adapter can read the wrong configuration. | P1 |
| Offset quality | Matcher always returns the highest-scoring offset, with no minimum-quality or ambiguity rejection. | P1 |
| Config/runtime contract | Example config forbids CPU fallback while README, GPU audit and tests say CUDA-preferred with CPU fallback. Several offset knobs are stale/no-op. | P1 |
| CLI semantics | `--ingest-only` is parsed but unused; `--resume` only prints a message; `--dry-run` occurs after playlist snapshot code, which can write state/network data. | P1/P2 |
| Spotify OAuth callback | Callback accepts any URL path and does not require returned OAuth `state`; missing-state callbacks are accepted. | P1 security/reliability |
| Font provenance | Example selects Suravaram, but Reel and JSON provenance hardcode Baloo Tammudu 2. | P2 |
| Project packaging | ZIP has no root `config.json` or root `PROJECT_PLAN.md`, although README/tests expect them. | P1 usability/CI |
| Architecture | The active orchestration is concentrated in two files of about 1,250 and 1,148 lines, while legacy flows and duplicate utilities sit beside it. | P2 cleanup |
| Final manifest | Final song JSON has multiple writers across modules/stages; a single versioned manifest contract would reduce drift. | P2 |

### Overall assessment

The code **compiles**, and most unit/contract tests pass. That should not be interpreted as a complete successful media pipeline: this audit did not run a real end-to-end job against Spotify, YT Music, LRCLIB, yt-dlp, FFmpeg, downloaded ML weights, CUDA/NVENC and actual audio/video files. The test suite is strongest at source contracts, data access and mocked/unit behavior; it does not currently catch the most consequential section-trimming and cache-provenance defects described below.

---

## 2. ZIP inventory and checks performed

The ZIP contains **116 files** plus directory entries (130 total ZIP entries). The source inventory includes:

- **86 Python files total**, of which **70 are under `src/`**.
- About **12,185 lines of Python source**.
- 13 test modules.
- Three top-level SQL schemas and one Phase 2 SQL schema.
- Five Windows/Python setup/run scripts.
- Two TTF font assets.
- Configuration and cookie examples; model-cache documentation but no downloaded model checkpoints.
- Eleven documentation/planning files, including very large historical plan drafts.
- `songs/final/hook_queue.json`, which contains old completed entries and a pending hook rather than a clean example queue.

### Static and test checks

1. Compiled all included Python files using the available Python 3.13 interpreter: **success**.
2. Ran the test suite against the untouched extracted layout: **81 passed, 4 failed**.
3. For diagnosis only, temporarily supplied copies of the root-level files the tests expected: this produced **84 passed, 1 failed**. The remaining failure confirmed that the example's CUDA/fallback settings conflict with the intended runtime contract. The temporary files were removed.
4. Checked the static import graph from `main.py`: **58/70 `src/` modules are reachable**; the other 12 are either obsolete, compatibility/utility code, package markers, or used by a setup/test path instead of the active runtime. Import reachability alone is not enough reason to delete a file.
5. `ruff` and `mypy` were not installed in the audit environment, so lint/type-check results are unavailable.

### Important environment limitation

The audit environment's installed Python/PyTorch stack is not the same as the project's pinned CUDA-oriented requirements. Network/model download and actual GPU/media work were not validated. The runtime defects below are based on code-path tracing and local tests, not a claim that the complete multimedia pipeline was run end to end.

---

## 3. Actual pipeline traced from the entry point

`main.py` owns the CLI and the first-run playlist snapshot. The active flow is approximately:

1. Load `config.json`, establish paths and initialize the playlist database.
2. Fetch and persist a frozen Spotify playlist snapshot in order. A new playlist intentionally exits after snapshotting; a second run starts processing.
3. For each actionable playlist item, bootstrap the song row and create resumable per-song workspace state.
4. Fetch complete Spotify track/album/artist/artwork/ISRC metadata.
5. Check finalized songs for duplicate ISRCs and ask whether to keep the old or new result if a duplicate exists.
6. Search YT Music for song candidates; score title, artist, album and duration; select a duration-compatible candidate.
7. Download the exact selected YT Music URL using yt-dlp and FFmpeg; normalize and retain source metadata.
8. Query LRCLIB `/api/get`, then search fallback `/api/search`; apply the Telugu-script/synchronized-lyrics gate before expensive downstream work.
9. Derive working audio and run Demucs separation.
10. Run the alignment adapter (`src/alignment.py`) into the Phase 2 pipeline (`src/phase2core/pipeline.py`): VAD/chunking, MMS/CTC alignment, merger, timing repair and validation.
11. Generate the canonical word timeline, LRC/SYLT tags and enriched MP3.
12. Generate the full-song 8D output from the separated stems.
13. Resolve a YouTube visual video once; download its reference audio and estimate its offset against the full song.
14. Request or resume hook times (or persist a deferred hook entry when `hook.skip` is enabled).
15. Build a vertical Reel from the mapped visual section, cropped 8D audio and word-level lyric overlay; validate and promote artifacts and update databases.

This means `src/phase2core/pipeline.py` is **not an abandoned pipeline**. It is the active word-alignment engine used by `src/alignment.py`. Likewise, `src/rendercore/` is actively used by Reel generation. The cleanup should not remove either package.

---

## 4. Bugs and defects that should be fixed before refactoring

### P0-1. Duplicate ISRC path has a real argument-count bug

- `src/pipeline.py:207`: `_terminal_duplicate(self, key, entry, metadata, duplicate)` requires four explicit arguments after `self`.
- `src/pipeline.py:613`: call is `self._terminal_duplicate(entry, md, duplicate)`, supplying only three.
- Result: if the duplicate branch is reached, Python raises `TypeError` before the operator can choose “keep previous” or “keep current.” The outer pipeline error handler then records this as a pipeline failure.

**Change:** decide the desired signature and make the caller/definition consistent. Add a behavioral test that triggers a duplicate through `process_entry`, verifies the prompt decision, and checks both database outcomes. A test that merely asserts a source substring is insufficient.

### P0-2. Reel grabs the wrong part of the visual video

Relevant code:

- `src/rendercore/video_grabber.py:10-12` calculates `requested_start_ms` and `requested_end_ms` for the guarded section.
- `src/rendercore/video_grabber.py:30-50` invokes yt-dlp on `selected_url` with no download-range extraction and writes the downloaded media to `downloaded_with_guard.mp4`. As written, it downloads the whole available video.
- `src/reel.py:69-79` calculates the absolute mapped hook, then uses `trim_start = min(guard, mapped_start)` for `trim_exact()`.

The trim code behaves as though the downloaded file starts at the requested guarded section, but the file starts at the beginning of the full video. In a typical case where the mapped hook is minutes into the source, the trim begins roughly 1.5 seconds from the start of the video instead of at the mapped hook. The duration check only checks that the full-video duration is long enough, so this mismatch passes validation.

**Change:** choose one consistent strategy:

- **Simplest/robust:** download/cache the full visual video, then trim at the *absolute* mapped start timestamp; or
- Use a reliable section extraction step (`yt-dlp` download ranges or FFmpeg) and record/validate the actual section origin, then trim at `mapped_start - requested_start` within that section.

Cache keys/manifests must include the video ID/URL and actual extracted range. Add a test with a synthetic video whose visual segments differ over time and assert the produced section corresponds to the intended absolute timestamps; checking only output duration/resolution will not catch this.

### P1-1. Reference WAV cache can be from a different selected visual video

- `src/youtube_video.py:74-80` returns `youtube/reference_audio.wav` as soon as it exists and is bigger than 10 KB.
- It does not check the selected video ID/URL, hash, or a provenance manifest before reusing it.
- `src/pipeline.py:799-809` fingerprints the offset against the current `video.video_id`, and `src/pipeline.py:817-828` downloads reference audio when that offset fingerprint changes.

If the selected video changes inside the same work folder, the offset is recomputed but the old reference WAV can be returned for the new video. The offset is then persisted under the new video's fingerprint. This can silently corrupt the Reel mapping.

**Change:** put a manifest beside the WAV with exact selected URL/video ID, yt-dlp version/settings, downloaded MP3 hash, decoded WAV hash, and duration. Reuse only when those values match. Delete/rebuild dependent audio when video selection changes. Add a test selecting video A, then video B in the same workspace.

### P1-2. Alignment final timing repairs are discarded

- `src/phase2core/pipeline.py:808-812` performs the final merge and calls `finalize_output_timing(document, merged_words, duration_ms)`.
- After recording repair warnings and setting the DB state, `src/phase2core/pipeline.py:829` calls `self.merger.merge(document, results, duration_ms)` again.

`Merger.finalize_output_timing()` mutates the `AlignedWord` items in the merged list. The later merge constructs a fresh merged result from chunk results and can discard the repaired timings/reasons. The pipeline may log that repairs were applied while validating/exporting the unrepaired timeline.

**Change:** preserve and use the normalized `merged_words` after finalization; if another merge is essential, reapply finalization after it. Add a focused test where finalization must move/clamp a word, then verify persisted words, LRC and JSON use the repaired values.

### P1-3. `source_16k.wav` cache is not validated against its source

- `src/audio_unified.py:66-78` reuses `audio/source_16k.wav` when it exists and exceeds 10 KB.
- It does not validate the existing `source_manifest.json` before reuse, unlike `prepare_full_source()` at lines 41-48, which verifies both the input MP3 hash and cached WAV hash.
- It subsequently writes a manifest containing the hash of the current input as `derived_from_sha256`, even if the WAV itself was not regenerated.

**Change:** implement the same provenance/integrity checks as `prepare_full_source`: compare current source SHA-256, cached WAV SHA-256, duration and derivation parameters; rebuild atomically on mismatch. Add a test that changes source content while preserving the workspace path.

### P1-4. Demucs selects stems across all old/partial run directories

- `src/demucs.py:139-160` shares the same `audio/demucs_raw` directory across initial attempt, OOM retry and CPU fallback.
- `src/demucs.py:165-170` searches every `*.wav` under that root, then uses the first filename matching each expected stem name.

An old/partial run can leave files in the directory. The selected stem can therefore come from a previous attempt/model/device rather than the latest successful attempt. The manifest then hashes those copied files and records the current source/run details, which can make the stale output look legitimate.

**Change:** allocate a unique output directory for each attempt; select stems only from the successful attempt's exact Demucs output folder; verify the expected stem set, duration and current source/model; then write a manifest that records the specific attempt that produced each output. Do not rely on recursive “first file named vocals.wav” matching.

### P1-5. Custom `--config` paths are not carried through to alignment

- `src/config.py:14-19` correctly loads an arbitrary path `p`.
- `src/config.py:37` implements `config_path` as `self.root / 'config.json'` instead of storing `p`.
- `src/alignment.py:15` reads JSON through `Path(cfg.config_path)`.

If `main.py --config some/other.json` is used, the top-level config may load correctly while the Phase 2 adapter reloads `<config-root>/config.json`. This can silently change runtime/alignment settings or fail if that file is absent.

**Change:** store the resolved file path as `Config.config_file` (or similar) and return that exact value from `config_path`. Add a test using a non-`config.json` filename and assert all downstream stages receive it.

### P1-6. Offset estimation has no confidence/ambiguity acceptance threshold

- `src/youtube_sync.py:76-142` computes a full-song energy-envelope score and picks the maximum-scoring offset.
- `src/youtube_sync.py:109-128` marks the highest score as the selected candidate; it does not reject low absolute scores or nearly tied alternatives.
- `src/pipeline.py` proceeds with the returned offset to Reel generation.

For a wrong recording, noise, a remix, or a video with an unrelated audio segment, there will still be a highest-scoring offset. “Best among tested offsets” is not proof of a valid match.

**Change:** establish a calibrated minimum match score and a top-versus-runner-up margin; include reference/video duration sanity, optionally compare more robust features than only an energy envelope, persist confidence/rejection reasons, and require human review when uncertain. Add tests for correct matches, deliberately wrong matches, silence/low-energy inputs and ambiguous/repeated sections.

### P1-7. Configuration and actual offset algorithm disagree

`config.json.example` includes `reference_duration_ms: 30000`, `max_accepted_offset_ms: 30000`, `scan_step_ms: 250`, and `method_version: unified-offset-v1`. The active `find_offset()` is explicitly a *full-song* matcher (`src/youtube_sync.py:145-163`) and uses module constants for `frame_ms`, coarse and fine steps. `Config.validate()` and pipeline fingerprints refer to `frame_ms`, `coarse_step_ms`, and `fine_step_ms`, which are not the keys exposed in the example. Documentation/history also refer to an older 30-second matcher.

**Change:** retain the full-song matcher if that is the intended product behavior (the active tests favor it), expose only the real supported knobs, make the algorithm version explicit, and ensure its cache fingerprint uses the same parameters the algorithm actually reads. Remove or correctly implement dead keys. Add a test that changes each supported setting and proves it changes behavior/fingerprint.

### P1-8. CUDA policy is internally contradictory

- `config.json.example` says `require_cuda: true` and `allow_cpu_fallback: false`.
- `README.md`, `docs/GPU_CUDA_AUDIT.md`, and `tests/test_unified_runtime_contracts.py` describe CUDA preference with CPU fallback (`require_cuda: false`, `allow_cpu_fallback: true`).
- `src/alignment.py:17` forces fallback on when constructing the Phase 2 config, while `src/demucs.py` applies the top-level no-fallback policy.

A machine without a compatible CUDA GPU can proceed in alignment but fail in Demucs, despite the README's promise. The example's behavior is not the intended tested contract.

**Change:** pick one policy; based on the existing README/tests, set CUDA as preferred and permit CPU fallback by default, with a separate explicit “CUDA required” mode if needed. Pass the resolved policy consistently into Demucs, MMS/VAD and NVENC selection through `src/gpu.py` rather than independently reimplementing it. Align config, doctor output, docs and tests.

### P1-9. OAuth callback doesn't require an exact path or state

- `src/spotify_auth.py:39-77`: the HTTP handler records query fields and sets `done=True` for every GET, without checking the callback path.
- `src/spotify_auth.py:107-111`: only rejects state mismatch when a state is present; a code with no returned state is accepted.

**Change:** require exact callback path, a valid code, and a returned state that matches the generated state; reject missing/mismatched state. Handle OAuth error and invalid callback responses without terminating a valid flow. Align the documented/default redirect URI with the configured one and test valid, missing-state, wrong-state, wrong-path and error callbacks.

### P1-10. `--dry-run`, `--resume`, and `--ingest-only` do not do what the names imply

- `main.py:210` parses `--ingest-only`, but no implementation reads `args.ingest_only`.
- `main.py:280-281` merely prints a resume message. State/checkpoints are already read by normal execution, so the flag has no distinct behavior.
- `main.py:262-272` takes the first-run snapshot before `main.py:287-289` handles `--dry-run`. Running dry-run for a new playlist can still contact Spotify and persist the snapshot.

**Change:** either implement documented distinct semantics or remove these flags. For dry-run, state clearly whether reads/network calls are allowed; it should not write snapshots/DB state if it promises no writes. Add CLI behavioral tests instead of just checking parser/source strings.

### P2-1. Visual-video cache fingerprint omits artist

`src/pipeline.py:775-780` fingerprints title and album but not `md.artist`, despite `select_video()` being called with the artist at line 793. If artist changes while title/album remain the same, the previous selection can be reused.

**Change:** include normalized artist, normalized query, search provider/rules and selector version in the fingerprint.

### P2-2. Visual video downloader ignores the normal nested cookie configuration

`src/rendercore/video_grabber.py:33` checks `ytmusic.cookies_file`, but `config.json.example` documents the cookie settings under `ytmusic.yt_dlp.cookie_file` and `cookies_from_browser`. The audio/reference downloader uses the nested settings through `YTMusicMediaClient` while the visual video downloader has a separate option-building path.

**Change:** provide a shared yt-dlp options/config helper for visual downloads and audio downloads; resolve cookie paths relative to `cfg.root` consistently. Keep cookie files out of source control and logs.

### P2-3. Font selection and manifest disagree

`config.json.example` specifies `Suravaram-Regular.ttf` / `Suravaram-Regular`, and both Baloo Tammudu 2 and Suravaram fonts are shipped. `src/reel.py:86-90` loads the configured filename, but `src/reel.py:107` reports Baloo in the JSON unconditionally. `src/pipeline.py:464` and `src/pipeline.py:1074` similarly hardcode Baloo in metadata.

**Change:** resolve actual font family and font file once; pass it to ASS rendering and report the same resolved identity in song/reel JSON and DB rows. Verify the font file exists and is embeddable/useful; do not claim a font that wasn’t used.

### P2-4. HTTP timeout helper monkey-patches Requests globally

`src/acquisition.py` and `src/youtube_video.py` both replace `requests.sessions.Session.request` temporarily so ytmusicapi calls get a timeout. This is global mutable process state and can affect unrelated concurrent requests; nested uses can also restore the wrong function if calls overlap.

**Change:** use a client/session adapter or explicit per-client timeout handling where possible. If the dependency forces a patch, centralize it, lock the patch scope, document the limitation and test nested/concurrent calls.

### P2-5. Backup/restore code ignores configured database path

`src/db_maintenance.py` resolves files under `<project-root>/db` instead of consistently consuming `cfg.db_dir` / `paths.database_dir`. It can back up or restore the wrong location when the config changes. `migrate_databases()` is currently an integrity check, not a schema migration.

**Change:** pass the config-selected DB directory through all maintenance calls and rename the integrity-only operation or implement versioned migrations. Test a non-default DB directory.

### P2-6. Minimum supported Python version is undocumented

`src/rendercore/assembler.py` contains backslashes inside an f-string expression; that syntax relies on the Python 3.12 f-string grammar. All files compiled under the available Python 3.13 interpreter, but the README does not establish a minimum Python version.

**Change:** either declare Python >=3.12 and test that version, or precompute escaped values outside the f-string to keep compatibility with earlier Python versions. State and enforce the minimum version in setup instructions.

### P2-7. Downloaded 16-bit alignment cache/manifest should share the same integrity contract as full PCM

The full-source cache already verifies source and output hashes. The 16 kHz representation should also include the derivation parameters, source format/hash, output hash and decoder/tool version. Beyond fixing stale reuse, keep source and normalized-audio artifacts traceable to one another.

---

## 5. Organization of `src/`: recommended target

Avoid moving all files immediately. First fix the bugs above in place; add regression tests; only then move files with imports and paths updated in small groups. A safe target is:

```text
src/
  config.py                     # public config facade; exact config path
  cli_support/                  # doctor, maintenance and recovery commands
  pipeline/
    __init__.py
    orchestrator.py             # split current src/pipeline.py gradually
    hook_queue.py
    state.py                    # fingerprints, checkpoints, invalidation
    manifests.py                # one versioned final-package contract
  providers/
    spotify.py                  # Spotify client/metadata
    spotify_playlist.py
    ytmusic_search.py           # discovery and candidate scoring
    media_download.py           # selected-URL yt-dlp boundary
    lyrics.py                   # LRCLIB retrieval and Telugu gate
    youtube_video_search.py     # visual search; exact selected URL
  storage/
    playlist_db.py
    songs_db.py
    reels_db.py
    maintenance.py
  audio/
    source_audio.py
    demucs.py
    alignment_adapter.py
    eightd.py
    mp3_metadata.py
    phase2core/                 # preserve the word-level alignment engine
  video/
    selection.py
    synchronization.py          # full-song offset finder
    reel.py
    rendercore/                 # preserve ASS, audio, trim and render functions
  common/
    hashing.py
    paths.py
    commands.py
    gpu.py
    models.py
    errors.py                   # only if a common error hierarchy is actually used
```

This is a destination, not a recommendation to perform one large move right now. During migration, keep compatibility imports (thin re-export modules) where useful so the public CLI/tests don’t break all at once. `main.py` stays the user-facing CLI and should be small: argument parsing, command dispatch, and process exit codes.

### Suggested responsibility rules

- **Providers** perform discovery/API access; they do not decide song/reel workflow state.
- **Acquisition** receives an already-selected source URL; it does not launch a second search.
- **Audio/Video transforms** operate on explicit source paths and timestamps and return outputs plus provenance.
- **Pipeline orchestration** handles ordering, retries, user decisions and checkpoint invalidation; it should not contain rendering details or dozens of SQL statements.
- **Storage** owns schema initialization, queries and transactions; it should not implement media logic.
- **Manifests** are created and validated by one module with an explicit schema version.
- **Config** owns relative path resolution, typed/validated policy and its exact source path.

### Files that are probable retirement candidates (after dependency cleanup)

- `src/artwork.py` and `src/downloader.py`: an older YT Music artwork/downloader path, not reachable from the current entry-point graph; `artwork.py` is used by the old downloader.
- `src/playlist_ingest.py`: old YT Music playlist-ingestion architecture; active ingestion is Spotify-first through `src/spotify_playlist.py`. The old module still has a test, so remove or archive that test at the same time if this path is intentionally retired.
- `src/spotify_overlay.py`: obsolete overlay helper not referenced by the active runtime.
- `src/spotify_isrc.py`: thin wrapper not used by the active pipeline, which now gets ISRC through `SpotifyClient`; verify any external use before removal.
- `src/errors.py`: unused generic error type. Either adopt a single shared exception hierarchy or remove it.
- `src/phase2core/doctor.py` and `src/phase2core/recovery.py`: not called by the active root-level CLI path; consolidate into the existing `src/doctor.py` / `src/recovery.py` only if their functionality is not needed elsewhere.
- The direct-stream `ytmusicapi.get_song` / signature-timestamp / format-decoding methods in `src/ytm_media.py` appear to be leftover: active acquisition does YT Music search then yt-dlp on the selected URL. **Do not delete the whole file**; its selected-URL yt-dlp download methods are active for song audio and visual reference audio. Remove only confirmed-unused direct-stream helpers after test/reference checks.

### Files that should be retained

- `src/phase2core/` as a package (except the two individual candidates above, if unused); this is core alignment, not a dead copy.
- `src/rendercore/` as a package; it is core Reel rendering.
- `src/spotify_auth.py` because `scripts/setup_spotify_auth.py` calls it, even though the main processing import graph does not.
- `src/recovery.py`, `src/doctor.py`, the three DB modules, `src/spotify.py`, `src/spotify_playlist.py`, `src/acquisition.py`, `src/lrclib.py`, `src/alignment.py`, `src/demucs.py`, `src/audio_unified.py`, `src/eightd.py`, `src/youtube_finder.py`, `src/youtube_video.py`, `src/youtube_sync.py`, and `src/reel.py` because they are part of the active pipeline.

---

## 6. Database boundaries and persistent JSON

### Current database responsibilities

The project has three main SQLite stores plus a per-song Phase 2 workspace DB:

- **`playlist.db`**: frozen Spotify playlist snapshot, order and playlist-entry lifecycle/status.
- **`songs.db`**: song identity/metadata, stage/audit records, source/lyrics/alignment details, and word-level data.
- **`reels.db`**: Reel rows and child details for hook, YouTube sync, audio, video, lyric overlay and validation.
- **`alignment/db/phase2.sqlite` inside each workspace**: alignment-engine processing state/chunks/results. This is a separate per-song work DB, not one of the central three databases.

The three central schema files are actively used. `src/sql/001_initial.sql` is part of the Phase 2 database contract, but there is also schema setup logic in `src/phase2core/db.py`; audit for schema drift and establish one authoritative DDL/migration path.

### Recommendation for the “put the remaining details in playlist DB” idea

Do **not** simply move all data into `playlist.db`. Playlist membership/order, song entities/lyrics, and generated Reel artifacts are distinct relationships. Combining them without a clear relational design creates cross-entity foreign-key and update problems.

Instead, define the intended boundaries explicitly:

- Keep playlist order and per-entry processing state in `playlist.db`.
- Keep canonical song information, source identity, alignment/lyrics and final song artifact metadata in `songs.db`.
- Keep Reel output, hook, video-sync confidence, subtitle/font and validation data in `reels.db`.
- If the stated objective is that `songs.db` contain *only song details* (not generic process history), move general run/event telemetry to an explicitly designed `pipeline_runs` / `pipeline_events` schema and decide whether that belongs in a fourth database or in the playlist orchestration store. Make the choice deliberately; don’t move song-specific failures into a playlist-only row that obscures a song reused in another playlist.
- Define which source of truth owns final word timings: the finalized song DB/JSON or the per-work Phase 2 DB. Avoid unversioned competing copies.

### Final JSON/manifest is currently split across several writers

The final JSON is influenced by the Phase 2 JSON manager/updater, `src/json_record.py`, and `src/pipeline.py`, and is written at different stages (before and after hook/Reel processing). That split makes it easy for fields such as font, offset confidence, timestamps and artifact paths to be missing or inconsistent depending on where the pipeline stopped.

Create one versioned final manifest schema and update it through controlled stage updates. It should capture at least:

- `schema_version`, pipeline/code version, config hash, input/output SHA-256 values;
- Spotify track/album/artist IDs, ISRC and canonical source metadata;
- exact YT Music audio selection, query, candidate scoring and downloaded media provenance;
- LRCLIB strategy/version, selected lyric source, Telugu/sync gate and lyric SHA;
- model identifiers/revisions, actual device used, Demucs attempt/model/device and each stem hash;
- alignment word count, quality/validation metrics, timing repairs and exported word/LRC/SYLT hashes;
- 8D input/output stem hashes and generation settings;
- hook start/end and how those values were selected;
- selected visual URL/video ID, reference audio hash, offset algorithm/version, score, confidence, ambiguity margin and acceptance decision;
- actual video time range used, render/font/subtitle settings, validation report and final output paths/hashes.

The final JSON under `songs/final/` should represent **the complete processing record for one song**, but should not become an unstructured dumping ground for duplicate/obsolete provider payloads. Keep raw provider responses in a source/provenance field or adjacent audit file and version the manifest.

---

## 7. Configuration, docs and packaging inconsistencies

1. **Missing root `config.json`:** ZIP ships `config.json.example`, but the active config loader defaults to `config.json`. README tells the user to set values in `config.json` but never explicitly instructs them to copy `config.json.example` first. Add a clear copy step, keep private values local, and consider an actionable error when the config is absent.
2. **Missing root `PROJECT_PLAN.md`:** the actual plan is `docs/PROJECT_PLAN.md`, while README and a test refer to root `PROJECT_PLAN.md`. Fix the link/test path or add a short root pointer file; do not duplicate the full 3,098-line document.
3. **CUDA contradiction:** correct `config.json.example` to match the intended fallback policy, or change README/GPU audit/tests if strict CUDA is truly required. Existing evidence favors fallback being allowed by default.
4. **Offset options are stale:** align the config example, config validation, fingerprint, code constants, active tests and plan with the actual full-song matcher; delete stale 30-second settings if they no longer have meaning.
5. **Redirect URI mismatch:** config example uses `http://127.0.0.1:8080`; the auth module's default is `http://127.0.0.1:8888/callback`. The setup script uses the config value, but all user instructions and registered URI settings must agree. In addition, tighten the callback/state checks noted above.
6. **YT Music authentication docs are misleading for the active flow:** `docs/YTMUSIC_AUTH_WINDOWS.md` and `scripts/setup_ytmusic_auth.py/.ps1` suggest a YT Music authentication setup, while the active catalog search is deliberately unauthenticated. Optional cookies are a yt-dlp *media access* configuration, not a required catalog-search credential. Rewrite or retire these docs/scripts accordingly.
7. **No model pre-download/prewarm command:** `models/README.md` explains persistent caches, and the application has a doctor command, but no dedicated command prepares all required MMS/tokenizer/VAD/Demucs assets before the first song. Add a safe `--download-models`/`--prepare-models` operation that performs no song processing, records model IDs/revisions/checksums, reports disk space and failures, and uses the same project-owned cache as actual inference.
8. **`archives/` claims are false for this ZIP:** `docs/SOURCE_INPUTS.md`/audit notes mention retained source/archive material, but there is no `archives/` directory in the ZIP and `.gitignore` excludes it. Update the docs so they accurately describe what is shipped; never depend on missing archive files for config/runtime.
9. **Build/audit docs are stale:** `docs/BUILD_INFO.md`, `docs/BUILD_MANIFEST.json`, `docs/FULL_PROJECT_AUDIT.md` and `docs/GPU_CUDA_AUDIT.md` include claims/policy/test counts that do not consistently match the current source or this suite. Regenerate them once defects are fixed; do not use older audit claims as proof of current behavior.
10. **Very large historical drafts:** `docs/PROJECT_PLAN_ORIGINAL.md` (~22,299 lines) and `docs/initial_complete_plan.md` (~21,827 lines) duplicate earlier design history and include obsolete architecture/offset assumptions. Keep one concise current spec in `docs/PROJECT_PLAN.md`; if the history matters, move the originals to an optional `docs/history/` folder or out of the runtime ZIP.
11. **`ideas.txt` is a scratchpad, not release documentation:** convert valid ideas into a prioritized roadmap/issue list, then remove this file from the user-facing package.
12. **Hook queue ships live state:** `songs/final/hook_queue.json` contains old completed items and a pending item, including source identifiers, timestamps and Windows path strings. It is not a clean template. Exclude run state from the sample release and provide an empty template (for example `hook_queue.example.json`) if needed. Do not overwrite real queue state during a migration.
13. **Git ignore vs sample state:** `.gitignore` ignores `songs/` and generated Reel outputs but the ZIP ships the hook queue under `songs/`. Clarify what is source-controlled/sample data versus runtime output, and make the package reproducible from a clean checkout.
14. **User setup documentation:** README has spelling/format issues, does not tell the user to create the config from the example, and does not clearly state the minimum supported Python version. Refresh after configuration/CLI contracts are settled.

---

## 8. Complete file-by-file review: all 70 Python files under `src/`

**Status labels:** `KEEP` = active/core; `REFACTOR` = retain but adjust/reshape; `RETIRE CANDIDATE` = not active in the main pipeline, subject to dependency checks; `PACKAGE` = empty package marker, retain.

### Top-level `src/` modules

| File | Role and pipeline relationship | Finding / recommended action |
|---|---|---|
| `src/__init__.py` | Package marker. | **PACKAGE.** Keep; no standalone behavior to fix. |
| `src/acquisition.py` | Active YT Music search, candidate scoring, selected-URL media acquisition and source normalization. | **KEEP/REFACTOR.** Good boundary between search and download. Remove global Requests monkey-patching by adopting safe timeout handling. Split discovery/scoring from downloader orchestration later. |
| `src/alignment.py` | Active adapter from unified pipeline to `phase2core.Pipeline`. | **KEEP/REFACTOR.** Fix custom `config_path` propagation; simplify compressed one-line operations; make GPU policy explicit instead of silently overriding settings. |
| `src/artwork.py` | Older YT Music artwork scraping/thumbnail-discovery path. | **RETIRE CANDIDATE.** Only used from the old `src/downloader.py` path, not current Spotify artwork enrichment. Verify consumers, then remove or move to `legacy/`. |
| `src/audio_unified.py` | Active full PCM decode and 16 kHz mono derivative creation. | **KEEP.** Full WAV cache already validates source/output hashes; give `source_16k.wav` the equivalent provenance checks. |
| `src/config.py` | Central configuration loading, dotted-key lookup and path resolution. | **KEEP/REFACTOR.** Store the exact config filename; `config_path` is wrong for custom filenames. Validate actual, supported offset keys and runtime policies rather than stale keys. |
| `src/db_maintenance.py` | CLI backup, restore, integrity and “migration” utilities. | **KEEP/REFACTOR.** Respect configured database directory; rename integrity-only logic or implement real versioned migrations. Test non-default paths. |
| `src/db_playlist.py` | Active frozen playlist snapshot/order/lifecycle database. | **KEEP.** Maintain transactional order/status rules. Add tests around dry-run and first-run behavior. |
| `src/db_reels.py` | Active Reel tables/upserts. | **KEEP/REFACTOR.** Very compressed style makes SQL/schema review difficult; align field names with `database/reels_schema.sql`, remove duplicated wide-row data only after migration plan. |
| `src/db_songs.py` | Active songs table, metadata, ISRC duplicate lifecycle, alignment words and stage/audit details. | **KEEP/REFACTOR.** Dense one-line SQL methods should be formatted and typed. Revisit whether generic pipeline audit rows belong here, based on the intended DB boundary. |
| `src/demucs.py` | Active unified Demucs separation, retry/fallback and caching. | **KEEP/REFACTOR.** Fix recursive stale-stem selection; isolate each attempt directory; unify GPU policy with `src/gpu.py`; record the exact successful attempt. |
| `src/doctor.py` | Active environment/dependency/CUDA/FFmpeg diagnostics. | **KEEP.** Update it to report the chosen CUDA/CPU policy, font availability, cache paths and model-preparation readiness consistently. |
| `src/downloader.py` | Older generalized YT-dlp/downloader API. | **RETIRE CANDIDATE.** Not imported by the current main graph; old path also keeps `src/artwork.py` reachable. Confirm no external user import before removal. |
| `src/eightd.py` | Active full-song 8D/stem mix generation. | **KEEP.** Algorithm has useful canonical stem-length and validation checks. Remove unused import/local `peak_after`, add tests for non-finite/empty/mismatched input and repeatable output metadata. |
| `src/errors.py` | Generic `PipelineError` definition. | **RETIRE OR ADOPT.** No active usage found. Either introduce a deliberate shared hierarchy or remove to avoid implying an error contract no code uses. |
| `src/gpu.py` | Shared CUDA/device availability and resolution utilities. | **KEEP.** Make this the single policy resolver for all Torch stages; Demucs currently duplicates resolution logic. |
| `src/hashing.py` | Shared SHA-256/stable JSON hash helpers. | **KEEP.** Ensure cache fingerprints include every actual input/version and hashes are checked before reusing derived artifacts. |
| `src/hook.py` | Active hook input/reuse validation. | **KEEP.** Pair with a dedicated queue manager/CLI for pending-hook state; current queue is persistent but editing/completion workflow is not discoverable enough. |
| `src/json_record.py` | Creates/writes song metadata JSON. | **KEEP/REFACTOR.** Fold its responsibility into a versioned manifest contract with Phase 2 JSON updater and top-level pipeline writes. |
| `src/lrclib.py` | Active synced-lyrics lookup/search fallback and Telugu-script detection. | **KEEP.** Add explicit tests for no lyrics, wrong script, unsynced results and sparse Telugu coverage; ensure selected lyric provenance is carried into the final manifest. |
| `src/metadata.py` | Normalizes metadata from provider/download JSON; large, wide schema. | **KEEP/REFACTOR.** Split canonical Spotify `TrackMetadata` from `AcquisitionProvenance`/yt-dlp raw metadata; avoid an excessively broad normalized type retaining obsolete provider concepts. Preserve manifest backward compatibility during transition. |
| `src/model_cache.py` | Configures project-owned Hugging Face/Torch caches. | **KEEP.** Share its resolved paths with a model pre-download command; ensure CLI, doctor and inference use the same cache location. |
| `src/mp3_metadata.py` | Active MP3 ID3/artwork/lyrics/custom tags embedding. | **KEEP.** Maintain tag replacement tests; ensure embedded timing and output hashes correspond to the final normalized alignment and 8D artifact. |
| `src/pipeline.py` | Main per-song stage orchestrator, checkpoints, duplicate flow, source selection, hook, Reel and artifact promotion. | **KEEP/REFACTOR — highest complexity.** Fix duplicate method call, video selection fingerprint, cache invalidation and manifest/font provenance. Gradually split state/fingerprints, acquisition, audio/alignment, hook and Reel orchestration into focused modules. |
| `src/playlist_ingest.py` | Older YT Music playlist ingestion and item normalization. | **RETIRE CANDIDATE.** Active architecture is Spotify-first; module is not in the main dependency graph but remains covered by `test_ytm_playlist_ingestion.py`. Retire module and test together, or move both to an explicitly supported legacy feature. |
| `src/recovery.py` | Active root-level CLI recovery/reconciliation of DB/workspace state. | **KEEP.** Keep only one top-level recovery contract; add fixtures for partial artifacts, moved final files and stale state. |
| `src/reel.py` | Active hook-video/audio crop, subtitle generation, render, final validation and Reel manifest. | **KEEP/REFACTOR.** Fix trim-origin bug through `VideoGrabber`; remove duplicate `VideoGrabber` import; use resolved font metadata; require offset confidence acceptance before calling. |
| `src/spotify.py` | Active Spotify API client, track/album/artist enrichment, artwork, ISRC. | **KEEP.** Core provider integration. Add resilience tests for paging, expired OAuth, rate limits and partially missing Spotify fields if not already covered elsewhere. |
| `src/spotify_auth.py` | Spotify PKCE authorization used by `scripts/setup_spotify_auth.py`. | **KEEP/FIX.** Require exact callback path and returned state; reject missing state; align redirect URI default/config/docs. |
| `src/spotify_isrc.py` | Thin legacy wrapper around Spotify ISRC lookup. | **RETIRE CANDIDATE.** Active duplicate path uses `SpotifyClient`/normalized ISRC directly; remove only after checking external consumers. |
| `src/spotify_overlay.py` | Older overlay helper that enriches/writes Spotify image metadata. | **RETIRE CANDIDATE.** No active call found. Active client already enriches Spotify artwork. Remove/move to legacy once confirmed unused. |
| `src/spotify_playlist.py` | Active Spotify playlist item normalization and full snapshot fetch. | **KEEP.** This is the current playlist provider path; keep the one-fetch/no-extra-API contract and exact source ordering. |
| `src/utils.py` | Active common atomic JSON/text, time parsing, subprocess, FFprobe and duration helpers. | **KEEP/REFACTOR.** Overlaps with `rendercore.utils` and `phase2core.utils`; avoid a risky global merge before module boundaries are settled. Consolidate common command/atomic-write functions later. |
| `src/youtube_finder.py` | Active single search/selection for visual YouTube video. | **KEEP.** Strengthen explicit result validation and selection confidence; persist query, candidate list/rules and exact URL; no second search during hook work. |
| `src/youtube_sync.py` | Active global offset estimation across full-song/reference audio. | **KEEP/FIX.** Add confidence/ambiguity rejection; align config knobs and method version with active constants; add wrong-source and repeated-section tests. |
| `src/youtube_video.py` | Active selected visual URL caching, reference audio download and helper initialization. | **KEEP/FIX.** WAV cache must be validated against selected URL/video identity; include provenance hashes; use shared timeout/cookie option helpers; visual-selection fingerprint should include artist. |
| `src/ytm_media.py` | Active shared yt-dlp wrapper for already-selected YT Music audio and YouTube reference-audio URLs; contains older direct-stream methods too. | **KEEP/PRUNE.** Keep `_validate_ytmusic_url`, `_validate_youtube_video_url`, `_download_audio_with_ytdlp`, `download_audio_mp3`, `download_youtube_reference_mp3`. Remove only the now-unused direct stream/signature-timestamp/format-selection path after tests and callers are confirmed. Update the misleading docstring to describe the active selected-URL/yt-dlp contract. |

### `src/phase2core/` — alignment engine

| File | Role and recommendation |
|---|---|
| `src/phase2core/__init__.py` | **PACKAGE.** Keep package marker. |
| `src/phase2core/activity_detector.py` | **KEEP.** Active VAD/activity detection; audit device/fallback behavior against the common GPU policy. |
| `src/phase2core/audio.py` | **KEEP.** Phase 2 audio-loading/representation layer; keep sample-rate/channel assumptions explicit and tested. |
| `src/phase2core/chunker.py` | **KEEP.** Active lyric/audio chunk construction. Add edge coverage for zero-width/gap/boundary cases if missing. |
| `src/phase2core/config.py` | **KEEP, clarify naming.** Phase 2's own alignment config is distinct from `src/config.py`; document that boundary so developers don’t mix the two types. |
| `src/phase2core/ctc_aligner.py` | **KEEP.** Active CTC word/token alignment; preserve as core functionality. |
| `src/phase2core/db.py` | **KEEP/REFACTOR.** Active workspace alignment state; reconcile its embedded schema with `src/sql/001_initial.sql` and add schema versioning. |
| `src/phase2core/demucs_isolator.py` | **KEEP/REVIEW.** Phase 2 helper for source/stem handling; confirm whether all methods are used on the main path before consolidation with top-level `src/demucs.py`. Don’t remove merely because a second Demucs wrapper exists. |
| `src/phase2core/doctor.py` | **RETIRE/CONSOLIDATE CANDIDATE.** Not reached from the active CLI graph; consolidate useful checks into `src/doctor.py`, or expose it explicitly as a Phase 2 diagnostic if needed. |
| `src/phase2core/embedder.py` | **KEEP.** Active Phase 2 MP3 tag embedding functionality; verify responsibility does not duplicate `src/mp3_metadata.py` and choose one canonical writer for production tags. |
| `src/phase2core/json_manager.py` | **KEEP/REFACTOR.** Active JSON state helper; centralize common schema/atomic behavior with top-level manifest writer, but preserve Phase 2 internal data it owns. |
| `src/phase2core/json_updater.py` | **KEEP/REFACTOR.** Used for enriched alignment JSON updates; consolidate final-package schema updates while retaining an alignment-specific updater if useful. |
| `src/phase2core/lrc_generator.py` | **KEEP.** Active LRC writing; add regression test that final timing repairs survive into serialized words/LRC. |
| `src/phase2core/lrc_reader.py` | **KEEP.** Active LRC parsing/timestamp/blank-marker logic; keep exact format and boundary tests. |
| `src/phase2core/merger.py` | **KEEP/FIX.** Core canonical word merge and final timing normalization. Pipeline currently discards the normalized result by merging again. Add direct timing-repair tests. |
| `src/phase2core/mms_model.py` | **KEEP.** Active multilingual MMS model wrapper/cache/device handling. Integrate its fallback with the shared runtime policy and predownload command. |
| `src/phase2core/pipeline.py` | **KEEP — active core.** Large alignment pipeline (~1,148 lines), not obsolete. Split only after behavioral tests exist. Fix the post-finalization merge that discards final timing repair. |
| `src/phase2core/recovery.py` | **RETIRE/CONSOLIDATE CANDIDATE.** Not used by root recovery flow; compare its chunk/file recovery logic with `src/recovery.py` before removing. |
| `src/phase2core/scanner.py` | **KEEP.** Alignment input scanning/metadata registration, used by Phase 2 path. |
| `src/phase2core/telugu_normalizer.py` | **KEEP.** Core Telugu normalization; retain test corpus for number/Unicode/punctuation and preserve source forms in output metadata if needed. |
| `src/phase2core/tokenizer.py` | **KEEP.** Active reference word/token logic. |
| `src/phase2core/types.py` | **KEEP.** Core Phase 2 dataclasses/types. Use these types at internal boundaries rather than loosely typed dictionaries where practical. |
| `src/phase2core/utils.py` | **KEEP/REVIEW.** Phase 2 atomic file/hash/audio helpers; overlaps with top-level and render utilities. Consolidate cautiously and keep compatibility contracts. |
| `src/phase2core/validator.py` | **KEEP.** Core alignment output quality validator. Surface failures/low confidence consistently in final manifest. |
| `src/phase2core/word_builder.py` | **KEEP.** Core canonical aligned-word construction. Preserve word identity/ordering across retries and final repairs. |

### `src/rendercore/` — Reel rendering

| File | Role and recommendation |
|---|---|
| `src/rendercore/__init__.py` | **PACKAGE.** Keep marker. |
| `src/rendercore/ass_generator.py` | **KEEP.** Active word-by-word subtitle/ASS generation. Test font fallback and escaped Telugu/ASS markup; ensure configured style equals manifest style. |
| `src/rendercore/assembler.py` | **KEEP/FIX COMPATIBILITY.** Active FFmpeg final assembly. Fix/minimize backslashes inside f-string expressions or declare Python >=3.12 explicitly. |
| `src/rendercore/audio_io.py` | **KEEP.** Active FFprobe/audio decode/crop/read/write operations; add exact selected-section timing tests for video/audio sync. |
| `src/rendercore/font_manager.py` | **KEEP.** Active font resolution. Return/report actual font family/file and ensure the manifest uses it. |
| `src/rendercore/utils.py` | **KEEP/REFACTOR.** Active render helpers/commands/hashing. Shares responsibilities with other utils modules; avoid premature consolidation but establish common path/command rules. |
| `src/rendercore/video_grabber.py` | **KEEP/FIX P0.** Active visual download/guard/trim path, but current implementation downloads a full video and trim logic assumes guarded-section origin. Make actual download range and trim coordinates consistent; add regression tests. |
| `src/rendercore/video_renderer.py` | **KEEP.** Active vertical-video rendering and encoding. Test NVENC fallback, exact dimensions, duration and actual lyric/video alignment separately. |

### `src/sql/`

| File | Role and recommendation |
|---|---|
| `src/sql/001_initial.sql` | **KEEP, consolidate schema source.** Phase 2 SQL schema used in tests/packaging while `phase2core/db.py` also defines schema logic. Make one canonical schema or add migration/version tests to prevent drift. |

---

## 9. Review of every non-`src/` code/config/data file

### Root/runtime files

| File | Review |
|---|---|
| `main.py` | Active CLI and pipeline dispatch. Fix `--ingest-only` (currently parsed but unused), clarify/remove informational-only `--resume`, make dry-run non-mutating if that is its promise, and pass custom config path through correctly. Keep snapshot-only first run if intentional and documented. |
| `config.json.example` | Correct shape to intended runtime. Current CPU fallback settings contradict README/tests; offset keys do not match the full-song matcher; font name/file disagrees with Reel manifest; cookie configuration belongs under `ytmusic.yt_dlp`. Add schema/config validation test that loads the actual example. |
| `cookies.txt.example` | Safe placeholder/template only. Clarify it is optional and must never contain real cookies/secrets when committed. Prefer one nested cookie config and one common downloader helper. |
| `requirements.txt` | Runtime dependencies are pinned for a CUDA-oriented Torch stack and include audio/video/model/network packages. Validate pins on the explicitly supported Python and Windows environment in CI; test actual FFmpeg/yt-dlp/model compatibility rather than treating successful import/compilation as sufficient. |
| `requirements-dev.txt` | Contains development tooling such as pytest/lint/type-check utilities. `ruff`/`mypy` were unavailable in this audit runtime; wire them into repeatable CI and make failures visible. |
| `pytest.ini` | Test discovery setup. Retain, but make tests use paths available in the packaged layout (`docs/PROJECT_PLAN.md`, example config) or provide explicit root pointers. |
| `.gitignore` | Ignores generated song/reel/runtime assets and archives, while a live-looking hook queue is still shipped inside `songs/final/`. Align ignore rules and packaging policy; keep credentials, downloaded model weights and generated audio/video out of the source ZIP by default. |
| `ideas.txt` | Project scratchpad with future requirements: pre-download models before first run, clarify DB ownership, make song JSON the complete record, improve 8D/offset/alignment. Turn these into roadmap issues and remove the scratchpad from the release ZIP once captured. |
| `models/README.md` | Useful model cache guidance. Extend with the missing safe pre-download command and show how to verify/cache the exact revisions without starting song processing. |
| `songs/final/hook_queue.json` | Runtime state, not sample source. Contains completed and pending entries plus source IDs/timestamps/output path state. Do not ship real queue state; replace with an empty example template or omit it. Preserve user queue contents during any later migration. |

### Assets and databases

| File | Review |
|---|---|
| `assets/fonts/BalooTammudu2-ExtraBold.ttf` | Valid asset candidate; config and manifest should report it only when actually selected. |
| `assets/fonts/Suravaram-Regular.ttf` | Valid asset candidate; currently selected by example config, but final JSON hardcodes Baloo. Decide one default font or accurately support both. |
| `database/playlist_schema.sql` | Active central schema for playlist store; retain and version migrations. |
| `database/songs_schema.sql` | Active central schema for song, lyrics, stage/audit and word data; review responsibilities with `db_songs.py` and the desired narrower “song details only” boundary. |
| `database/reels_schema.sql` | Active central schema for Reel and normalized child tables; reconcile duplicated/wide row columns with child records and `db_reels.py` method naming. |

### Scripts

| File | Review |
|---|---|
| `scripts/install_windows_cuda.ps1` | Keep as Windows setup helper. Verify its Torch/CUDA wheel pin agrees with `requirements.txt` and document whether the script replaces or only installs the PyTorch packages. |
| `scripts/run.ps1` | Thin runner; keep if it handles Windows environment/path setup consistently, otherwise simplify. |
| `scripts/setup_spotify_auth.py` | Needed: calls `src/spotify_auth.py`. Keep; make its redirect URI guidance agree with config and enforce strict PKCE state/path checking in the helper. |
| `scripts/setup_ytmusic_auth.py` | Probably obsolete as a required setup step: active catalog search intentionally uses unauthenticated YT Music search; cookies are a separate optional yt-dlp media access setting. Remove or relabel as an optional/deprecated utility if truly needed. |
| `scripts/setup_ytmusic_auth.ps1` | Same contract problem as the Python setup helper. Retire or document the narrow optional use; do not present it as required for normal search/acquisition. |
| `scripts/verify_windows_gpu.ps1` | Keep. Align its acceptance criteria and displayed fallback policy with the single canonical runtime configuration. |

### Documentation files

| File | Review |
|---|---|
| `README.md` | Current high-level flow is useful, but setup lacks explicit copy-from-example step, links to root `PROJECT_PLAN.md` which is absent, and needs CLI/runtime contract cleanup. It says CPU fallback is expected, unlike current config example. |
| `docs/BUILD_INFO.md` | Treat as generated metadata; rebuild after fixes because current counts/version details are stale relative to test/source state. |
| `docs/BUILD_MANIFEST.json` | Regenerate from the final accepted tree rather than manually editing; validate paths/hashes/version and remove references to nonexistent shipped archive files. |
| `docs/CHANGELOG.md` | Keep history, but reconcile release/version claims to actual tested behavior; do not claim the video-section/caching pipeline is correct before new tests pass. |
| `docs/FULL_PROJECT_AUDIT.md` | Superseded/stale audit. Replace with or link to a current report after implementation. This audit finds defects not reflected in existing claims. |
| `docs/GPU_CUDA_AUDIT.md` | Conflicts with `config.json.example` and some active policies. Keep only after one unified CUDA/CPU fallback contract is established and tested. |
| `docs/PROJECT_PLAN.md` | Keep as the single current detailed spec; fix README/tests links to its actual location. Update offset behavior, config names, cache invalidation and Reel timing design. |
| `docs/PROJECT_PLAN_ORIGINAL.md` | Very large historical plan (~22,299 lines) with earlier architecture assumptions. Move out of the main reading path or into `docs/history/`; do not use as executable spec. |
| `docs/SOURCE_INPUTS.md` | Currently references archived source inputs not included in the ZIP (`archives/` is absent and ignored). Correct the statement or ship the promised archive intentionally. |
| `docs/YTMUSIC_AUTH_WINDOWS.md` | Does not cleanly match the active unauthenticated YT Music search contract. Rewrite to distinguish catalog search (no account auth) from optional yt-dlp cookie access, or remove. |
| `docs/initial_complete_plan.md` | Very large older draft (~21,827 lines); duplicates/contradicts the current plan. Move into optional history or remove from a clean distributable ZIP. |

### Test modules — every shipped test file

| File | What it covers / what to change |
|---|---|
| `tests/test_acquisition_config_contract.py` | Covers selected YT Music search/download boundaries and normalization. Keep. Add cache provenance and selected-source mismatch tests. |
| `tests/test_ffmpeg_windows_fix.py` | Covers explicit WAV output and full-source decode behavior with mocks. Keep; extend to validate stale 16 kHz cache rejection. |
| `tests/test_foreign_key_lifecycle.py` | Covers parent-song creation before stage/audit rows. Keep; important SQLite integrity coverage. |
| `tests/test_lyric_word_fade.py` | Covers word-by-word ASS fade behavior. Keep; add selected font-family/provenance checks if font selection is configurable. |
| `tests/test_mp3_metadata.py` | Covers ID3 timestamp and managed TXXX tag updates. Keep. |
| `tests/test_offset_contract.py` | Covers valid whole-song fit and reference-longer-than-video rejection. Keep, but add quality threshold, ambiguous match and wrong-recording cases; current coverage doesn’t reject false high-scoring matches. |
| `tests/test_spotify_api_contract.py` | Covers full Spotify metadata/album artist/ISRC retrieval. Keep. Consider rate-limit/partial-field cases. |
| `tests/test_spotify_isrc_only.py` | Covers finalized-song ISRC duplicate semantics. Keep and add an actual duplicate `process_entry` test to expose the `_terminal_duplicate` argument-count error. |
| `tests/test_unified_cli_contracts.py` | Covers first-run snapshot, config passed to acquisition and CLI contract. Extend to prove `--dry-run` performs no writes and `--ingest-only`/`--resume` have real defined behavior. |
| `tests/test_unified_contracts.py` | Covers Spotify ordering, hook limits, plan text and Reel version contract. One current failure assumes root `PROJECT_PLAN.md`; fix the path/contract instead of treating the missing file as a code pipeline failure. |
| `tests/test_unified_runtime_contracts.py` | Largest contract test file. Useful lifecycle/runtime checks, but many assertions inspect source strings or exact implementation text. Make config tests load a real example and add behavioral tests for config path, cache fingerprints, Demucs retry isolation, video trim origin and timing-repair serialization. |
| `tests/test_v148_selection_and_lyrics.py` | Covers LRCLIB search fallback, Telugu gate, single visual search and exact source URL. Keep; add a regression around same workspace changing the selected visual video and the cached reference WAV. |
| `tests/test_ytm_playlist_ingestion.py` | Exercises old YT Music playlist ingestion which is not the active Spotify-first playlist path. Either mark as a supported legacy subsystem and test it separately, or retire this test with `src/playlist_ingest.py`. |

---

## 10. Current test failures and what they mean

The untouched packaged tree produced **81 passed / 4 failed** across the included suite:

1. `tests/test_unified_contracts.py::test_plan_spotify_first_direct_ytm_isrc_duplicate` — cannot find root `PROJECT_PLAN.md`; current plan lives under `docs/`.
2. `tests/test_unified_runtime_contracts.py::test_runtime_config_prefers_cuda_with_cpu_fallback` — cannot find root `config.json`.
3. `tests/test_unified_runtime_contracts.py::test_unified_config_exposes_canonical_config_path` — cannot find root `config.json`.
4. `tests/test_unified_runtime_contracts.py::test_hook_skip_config_and_persistent_queue_contract` — cannot find root `config.json`.

For diagnosis only, temporary copies of the expected root files yielded **84 passed / 1 failed**. The remaining assertion expects `runtime.require_cuda is False` and `runtime.allow_cpu_fallback is True`; the supplied `config.json.example` says the opposite. This confirms a real config-contract inconsistency rather than just a missing test fixture.

The tests do not include a regression that validates the *actual timestamp content* of the selected Reel section, that changes visual video A to B in the same work folder, or that proves final timing repairs survive into the exported timeline. Those should be added before reorganizing modules.

---

## 11. Priority-ordered change plan

### Phase A — correctness and reproducibility, leave paths in place

1. Fix duplicate ISRC argument mismatch.
2. Fix video section extraction/trim coordinate system; test exact source timestamps.
3. Add selected-video provenance to reference WAV cache and invalidate it on video changes.
4. Fix final alignment merge/finalization order.
5. Validate/rebuild the 16 kHz derived-audio cache by source/output hash.
6. Isolate Demucs outputs per attempt and bind each stem to the successful attempt.
7. Fix custom config-file path propagation.
8. Add offset confidence/ambiguity rejection before Reel generation.
9. Standardize CUDA/fallback policy and expose only functional offset settings.
10. Strictly validate OAuth callback path/state.
11. Correct dry-run/ingest-only/resume semantics and add CLI tests.
12. Resolve configured font identity and unify yt-dlp config/cookies/timeouts.

### Phase B — align tests, examples and docs

1. Make every test pass against a clean ZIP/extracted checkout without hand-created files.
2. Make the real `config.json.example` a test fixture; ensure test paths reference `docs/PROJECT_PLAN.md` or a root pointer.
3. Add integration-style tests with small synthetic audio/video files (not live YouTube/network) for offset/trim/timing and cache invalidation.
4. Refresh README setup, redirect URI instructions, CPU fallback story and model-cache preparation.
5. Create empty hook queue template and exclude live run state.
6. Regenerate build/audit metadata and align plan language with the actual full-song offset algorithm.
7. Install/run `ruff`/`mypy` in CI, after cleaning existing findings rather than guessing them.

### Phase C — safe `src/` refactor

1. Split `src/pipeline.py` without changing behavior: extract state/fingerprint helpers, manifest operations, hook queue, acquisition stage orchestration and Reel stage orchestration. Keep a compatibility `Pipeline` facade.
2. Consolidate duplicate command/hash/path utilities by clear common boundaries rather than a mass text replacement.
3. Refactor database wrappers and version schema migrations; use one canonical SQL contract per DB.
4. Move providers, audio and video modules into packages only after imports/tests and persisted paths have been reviewed. Maintain compatibility exports for one migration period if useful.
5. Remove confirmed-unused legacy files together with their tests, docs and call sites.
6. Keep `phase2core` and `rendercore` as named subpackages; split their long pipelines only when a behavior-focused test suite is in place.
7. Produce a clean distributable ZIP with no `__pycache__`, `.pytest_cache`, local credentials, model weights, generated audio/video or real hook-queue state.

---

## 12. Acceptance gates before considering cleanup complete

The project should not be called cleaned up until all of these pass:

- A clean checkout of the actual ZIP runs `python main.py --doctor` with actionable pass/fail details.
- A clean checkout loads the sample config after documented setup, and custom `--config` paths work through every stage.
- `pytest` passes against the unmodified checkout, including a real duplicate-ISRC branch test.
- A synthetic video test verifies the Reel's selected visual segment starts at the expected absolute mapped time, not at the source beginning.
- Changing selected video A to B forces reference WAV regeneration and recalculates offset against B.
- Incorrect/ambiguous audio matches are rejected or routed for review rather than silently accepted.
- Final timing normalization remains visible in the canonical word timeline, LRC/SYLT and final JSON.
- Stale `source_16k.wav` and Demucs files are rejected when provenance does not match.
- CUDA/CPU behavior matches one configuration contract in Demucs, MMS/VAD and rendering diagnostics.
- Reel JSON reports the actual font and actual time range used, not hard-coded labels.
- All persistent output manifests contain their source/output hashes, algorithm/version identifiers and validation status.
- `ruff` and (if adopted) `mypy` are run as repeatable checks.
- Documentation, example configuration, SQL migrations, test paths and the packaged tree agree.
- Real end-to-end GPU/network/model run is performed in the supported Windows environment before relying on performance or CUDA claims.

---

## 13. Scope statement

This is a detailed static/code/test review of the supplied archive and its current tests, with explicit tracing of the main processing route and the most consequential cache/temporal data-flow bugs. It is **not** a claim that real Spotify/YT Music/LRCLIB services or GPU/FFmpeg/model inference were exercised end-to-end. The project source was not edited during this audit. The recommended next step is to implement Phase A in place and add the regression tests before moving any files.
