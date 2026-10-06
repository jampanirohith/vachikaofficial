# UNIFIED TELUGU MUSIC → WORD-LEVEL LRC → WHOLE-SONG 8D → REEL PIPELINE

## Ultra-Detailed / Ultra-Ultra Project Plan

**Document type:** Unified master engineering specification  
**Status:** Proposed integrated architecture based on the three supplied phase specifications plus the decisions made during the integration discussion  
**Domain:** YouTube Music playlist ingestion, song enrichment, synchronized Telugu lyrics, word-level CTC alignment, whole-song stem-based 8D audio, manual hook selection, synchronized YouTube visual extraction, and final vertical Reel generation  
**Primary operating mode:** One song fully processed at a time; continue to the next actionable song only after the current song reaches a terminal state  
**Primary language:** Telugu (`te` / ISO-639-3 `tel`) with support for mixed-language lyric references where present  
**Platform target:** Windows-first, NVIDIA/CUDA-first for expensive ML workloads, FFmpeg/NVENC-first for final video encoding  

> **Important:** This document is the integrated authority for the unified project. The original Phase 1, Phase 2, and Phase 3 documents are retained verbatim in the archival appendices at the end of this file so that no original requirement or historical context is lost. Where the archived documents conflict with a decision made during integration, the integrated decision in this document takes precedence.

---

# 1. PURPOSE AND FINAL PRODUCT

The project is no longer three independently run applications. It is one end-to-end system that takes a YouTube Music playlist and turns each usable song into a complete song package plus a Reel.

The intended user-facing result for a successfully completed song is:

```text
songs/
└── final/
    ├── SongName.mp3
    ├── SongName.lrc
    ├── SongName_wordlevel.lrc
    ├── SongName.json
    └── SongName_8D.mp3
```

and the visual Reel is stored separately:

```text
reels/
└── generated/
    ├── SongName_reel.mp4
    └── SongName_reel.json
```

## 1.1 Meaning of the five final song files

### `SongName.mp3`

The final normal/local song master. It is derived from the original acquired song audio and receives the final consolidated player-facing metadata. Existing metadata must be preserved rather than destructively reconstructed. The MP3 may contain synchronized lyric metadata, including the canonical Phase 2 word-level `SYLT`, plus durable custom application metadata such as the selected YouTube video identity and the calculated YouTube offset.

### `SongName.lrc`

The original synchronized line-level LRC obtained through the approved lyrics source. The wording is preserved. It is not replaced by the word-level export.

### `SongName_wordlevel.lrc`

The final word-level LRC exported from the canonical Phase 2 alignment timeline. It is not produced by re-parsing a rounded LRC. It is an export of the canonical millisecond word spans.

### `SongName.json`

The cumulative master song record. It preserves the original source JSON and adds the unified pipeline namespaces and provenance. It contains playlist identity, source metadata, Spotify information where available, selected YouTube video, the calculated YouTube song offset, lyric provenance, Demucs information, alignment details, quality information, whole-song 8D information, the manual hook, Reel information, hashes, versions, status, and final output paths.

### `SongName_8D.mp3`

A **complete whole-song** 8D master, from 0:00 through the exact source-song end. It is generated once from the entire original song using the separated Demucs stems and the approved advanced spatial-processing pipeline. It is **used only as an audio source for the final Reel stage**. It is never used for YouTube synchronization, MMS, CTC alignment, word timing, lyric parsing, hook detection, or source matching.

---

# 2. INTEGRATED DECISIONS LOCKED DURING THIS DISCUSSION

These are explicit integration decisions and supersede conflicting standalone mechanics in the historical documents.

1. The three phases are one unified project and one orchestrated song lifecycle.
2. The individual engines remain modular; the orchestrator passes typed contracts/artifacts instead of creating hidden implementation dependencies.
3. `hook_timeline.json` is completely removed.
4. The user enters hook start and hook end manually in the terminal for each song.
5. Hook input is captured only after the word-level lyric result and the complete whole-song 8D master are ready.
6. Hook values are converted immediately to canonical integer milliseconds and persisted before Reel generation so an interrupted Reel stage can resume without asking again.
7. The YouTube song/video offset is calculated early, immediately after the selected visual YouTube video is known and its reference audio is available.
8. The YouTube offset matcher uses the first **30 seconds** of the original local song as the reference, not 15 seconds.
9. The 30-second reference is scanned across the YouTube song audio timeline using multiple structural signals: normalized waveform shape, energy/RMS envelope, high/low transitions, peaks/valleys, and related low-rate timing structure.
10. Candidate offsets are ranked by combined score.
11. Only candidates whose accepted offset is within the hard configured limit of **0–30,000 ms** are eligible by default.
12. If the globally highest-scoring candidate exceeds 30 seconds, it is rejected and the next-highest valid candidate is considered.
13. If no candidate meets the offset limit and the selected YouTube video cannot be trusted for synchronization, the song enters an explicit YouTube-sync review/failure state rather than silently changing the hook or silently selecting a different video.
14. The calculated offset is stored as a persistent song artifact in the database, master JSON, and final MP3 metadata.
15. The same stored global offset is later applied to the manually entered hook. There is no per-hook rematching.
16. If the song has **no synchronized LRC**, it is not sent through Phase 2, 8D, hook input, or Reel generation.
17. A no-synced-LRC song is marked `SKIPPED_NO_SYNCED_LRC`, treated as terminal for the normal processing run, and is not automatically selected again on future normal runs.
18. A skipped song retains enough metadata/provenance to explain why it was skipped; recommended storage is a dedicated skipped directory rather than `songs/final/`.
19. Demucs is executed once per source version and its full stem result is shared by the word-alignment engine and the whole-song 8D engine.
20. Audio decoding, LRC parsing, source probing, source hashing, and other reusable preprocessing are performed once per source identity and cached.
21. The advanced 8D process operates on the **entire song**, not on the hook.
22. The complete whole-song 8D output is saved as `SongName_8D.mp3`.
23. The whole-song 8D output is used **only** by the final Reel generator.
24. The Reel generator cuts the manual hook interval directly from the already-generated whole-song 8D master. No second 8D render is performed for the hook.
25. The final song package is not published to `songs/final/` until the whole song has passed all required final validation gates. Intermediate artifacts remain under the per-song work directory.
26. Final promotion is atomic or staged safely; the system must not expose half-completed final packages as completed.
27. The project uses three persistent SQLite databases: `playlist.db`, `songs.db`, and `reels.db`. `playlist.db` owns the frozen one-time playlist ingestion and queue; `songs.db` owns retained song processing and song artifacts; `reels.db` owns Reel lifecycle and outputs.
28. Processing state and quality state remain separate.
29. Resume/idempotency/caching are dependency-aware. A changed hook should not trigger another download, alignment, or whole-song 8D render.
30. The final Reel remains separate from the five-file song package.

---


# 2A. NEW AUTHORITATIVE OVERRIDES — PLAYLIST INGESTION AND SPOTIFY METADATA

This section supersedes any conflicting playlist-ingestion and Spotify-overlay rules in the archived Phase 1 material. The archived specifications remain preserved verbatim for historical reference, but the following rules govern the unified implementation.

## 2A.1 Playlist IDs are processed as frozen jobs

The user supplies one active playlist ID through `config.json`.

The unified program does **not** refresh or re-read that playlist on every normal run.

The behavior is:

```text
config playlist ID
        |
        v
playlist.db lookup
        |
        +-- playlist ID absent
        |      -> ingest entire playlist ONCE
        |      -> record every usable/unusable entry
        |      -> finish ingestion
        |
        +-- playlist ID present and unfinished
        |      -> DO NOT re-ingest
        |      -> resume existing database queue
        |
        +-- playlist ID present and DONE
               -> DO NOT re-ingest
               -> display playlist-done message
               -> tell user to change playlist ID in config.json
               -> exit normal processing
```

There is no hidden periodic playlist synchronization in the normal pipeline.

The playlist becomes a frozen processing dataset once its initial ingestion has committed.

## 2A.2 First ingestion records the entire playlist

On first encounter of a new playlist ID, the application retrieves the complete playlist and records the complete set of playlist entries before processing the first song.

The ingestion phase records, where available:

```text
playlist ID
playlist name/title
playlist position
YT Music video ID
YT Music URL
title
artist(s)
album
duration
view count
play count
raw playlist-item JSON
initial status
processing priority/rank
```

The playlist database therefore represents a frozen snapshot of the playlist as it was ingested.

## 2A.3 YouTube Music ID is unique in `playlist.db`

The unified project no longer creates a second queue row for an already-known YT Music video ID.

`playlist_entries.ytm_video_id` is globally unique in `playlist.db`.

Therefore:

```text
playlist A
    YTM123

playlist B
    YTM123
```

results in one queue entry for `YTM123` rather than two independent processing entries.

The appearance in the second playlist is retained through a separate membership record so that the system does not lose playlist history merely because duplicate queue work is suppressed.

This explicitly supersedes the archived Phase 1 rule that allowed repeated playlist occurrences with the same YT Music source ID to remain separate processing entries.

## 2A.4 Playlist serial assignment after priority calculation

For the unified project, the permanent processing serial is assigned from the initial frozen playlist snapshot **after view/play priority is resolved**.

The serial therefore identifies the queue item in the order the unified project will process it.

The original `playlist_position` remains stored independently and is never overwritten.

Tie-breaking is deterministic:

```text
1. higher priority count first
2. lower original playlist position first
3. lower serial allocation order as final tie-break
```

If no usable view/play count is available for an entry, its priority count is `NULL` and it is placed after entries with known positive counts while preserving original playlist position among unresolved ties.

## 2A.5 View/play priority metric

The requested processing priority is based on the song's available view/play count.

The ingestion layer should resolve a single `priority_count` and an explicit `priority_metric`:

```text
priority_metric = 'plays'
priority_count  = known play count
```

or:

```text
priority_metric = 'views'
priority_count  = known view count
```

or, when neither is available:

```text
priority_metric = 'unavailable'
priority_count  = NULL
```

The exact source field must be preserved in the raw playlist-item/source metadata.

The system must **not** confuse the selected visual YouTube video's view count with the YT Music playlist/song priority count. They are different metadata concepts.

Where the YT Music playlist response does not expose a usable count directly, the implementation may perform a lightweight metadata lookup for the individual YT Music source during the one-time ingestion phase. This lookup exists only to resolve the ingestion priority; it must not trigger a full song download merely for ranking.

## 2A.6 Queue ordering

Normal song processing uses:

```sql
ORDER BY
    CASE WHEN priority_count IS NULL THEN 1 ELSE 0 END ASC,
    priority_count DESC,
    playlist_position ASC,
    playlist_serial ASC;
```

The queue ordering is frozen after initial ingestion.

The application must not reshuffle pending songs on later runs based on newly observed YouTube view counts unless the user explicitly chooses a future re-prioritization feature. Normal operation uses the original frozen priority snapshot.

## 2A.7 No playlist refresh after completion

When every newly ingested playlist queue item has reached a terminal state, `playlist.db.playlists.playlist_done` is set to `1` and `ingestion_status` becomes `done`.

The program must print a clear terminal message such as:

```text
============================================================
PLAYLIST COMPLETE
============================================================
Playlist ID: <ID>
Total entries: <N>
Completed: <N>
Skipped: <N>
Duplicates: <N>
Errors: <N>

This playlist is finished and will not be refreshed automatically.
Update `ytmusic_playlist_id` in config.json to process a new playlist.
============================================================
```

A completed playlist ID must never be silently ingested again during a normal run.

## 2A.8 Partially completed playlist

If the application stops before the playlist is done:

```text
playlist.db
    already contains all initial entries
```

Therefore startup must simply resume the existing queue.

The application must not fetch the same playlist again merely because some songs remain pending, processing, needs-review, or retryable-error.

## 2A.9 New playlist after old playlist is done

The user manually changes:

```json
"ytmusic_playlist_id": "NEW_PLAYLIST_ID"
```

Then on the next normal run:

```text
new playlist ID not present in playlist.db
        |
        v
one-time ingestion
        |
        v
add only YT Music IDs not already present in playlist.db
        |
        v
record duplicate-YTM-ID appearances as membership/history
        |
        v
process newly queued songs
```

If a newly configured playlist contains only YT Music IDs already present globally, the playlist can be marked complete immediately after its membership snapshot is committed.

## 2A.10 Duplicate YT Music ID is different from ISRC duplicate detection

There are now two intentionally different duplicate concepts:

### Playlist-level YT Music ID deduplication

Purpose:

```text
prevent the same YT Music source from being queued twice globally
```

Key:

```text
ytM_video_id
```

Action:

```text
existing YTM ID -> do not create another playlist queue row
```

### Retained-song ISRC duplicate detection

Purpose:

```text
identify two distinct YT Music sources that represent the same catalog recording
```

Key:

```text
canonical ISRC
```

Action:

```text
detect -> operator chooses keep_previous / keep_current
```

The two mechanisms must not be conflated.

## 2A.11 Spotify becomes the preferred major-metadata overlay

When Spotify matching succeeds, Spotify is the preferred source for the major catalog fields that Spotify actually supplies.

The source/YouTube metadata is **not deleted** merely because Spotify lacks a corresponding field.

Conceptually:

```text
YouTube Music / yt-dlp source metadata
             |
             +----------------------------+
                                          |
                                      Spotify match
                                          |
                                          v
                              MAJOR-FIELD OVERLAY
                                          |
                     +--------------------+--------------------+
                     |                                         |
                     v                                         v
              Spotify supplies field                  Spotify lacks field
                     |                                         |
                     v                                         v
               use Spotify value                         keep source value
```

This is a field-by-field overlay, not a destructive replacement of the whole metadata object.

## 2A.12 Major Spotify fields to prefer when valid

For a matched Spotify track/album, prefer valid Spotify values for:

```text
track title
track artist(s)
album name
album artist(s)
release date
release-date precision
track number
disc number
album type
album total track count
explicit flag
canonical ISRC when valid
Spotify track ID
Spotify album ID
Spotify URL
Spotify URI
Spotify catalog duration (stored separately from actual local duration)
album label when supplied
album copyrights when supplied
Spotify popularity in detailed provenance
```

Where artist-level Spotify data is retrieved and valid, additional catalog information such as artist IDs/URLs/genres may also be recorded.

Fields such as the following must remain when Spotify does not supply a reliable replacement:

```text
YT Music source ID
YT Music source URL
source uploader/channel details
source views/plays
source description
source categories/tags
YouTube visual-video ID and URL
YouTube visual-video views/date/channel details
LRCLIB provenance
playlist identity
application serial
local file hashes
project provenance
composer, publisher, license, or other source fields
```

The rule is:

> **Spotify updates what it can authoritatively provide; it never blanks a meaningful existing source value solely because Spotify omitted that field.**

## 2A.13 Actual local audio duration remains authoritative

Spotify track duration is catalog metadata.

The real local MP3 duration remains the authoritative physical-audio boundary used by the pipeline.

Therefore:

```text
Spotify duration != replacement for local duration
```

The database stores both when available.

## 2A.14 Spotify artwork

When Spotify is successfully matched and returns a valid album image:

```text
select largest valid image by area/dimensions
        |
        v
 download exact bytes
        |
        v
validate image
        |
        v
use Spotify image as final front-cover artwork
```

The Spotify image bytes are preserved without resampling or recompression.

The previous YT Music artwork provenance must remain recorded in the JSON/source-artifact history even when Spotify artwork becomes the final displayed cover.

If Spotify artwork is unavailable or invalid, retain/use the best valid YT Music artwork according to the existing fallback rules.

## 2A.15 Field provenance

The master JSON should record provenance at both object and field level where practical.

Representative structure:

```json
{
  "metadata": {
    "title": "...",
    "title_source": "spotify",
    "artist": "...",
    "artist_source": "spotify",
    "album": "...",
    "album_source": "spotify",
    "composer": "...",
    "composer_source": "ytm_or_source"
  }
}
```

This makes the non-destructive overlay auditable.

## 2A.16 MP3 metadata after Spotify overlay

The final MP3 should contain the best consolidated player-facing values.

For major catalog fields with a valid Spotify match:

```text
TIT2 -> Spotify title
TPE1 -> Spotify track artist(s)
TPE2 -> Spotify album artist(s) when available
TALB -> Spotify album
TDRC -> Spotify release date when valid
TRCK -> Spotify track number when available
TPOS -> Spotify disc number when available
TSRC -> canonical valid ISRC
TPUB -> Spotify album label when supplied, otherwise existing/source publisher
TCOP -> Spotify copyright when supplied, otherwise existing/source copyright
```

Source-only/application metadata remains in its existing/custom frames and provenance.

`TLEN` always reflects the actual final/local audio duration, not merely Spotify catalog duration.

## 2A.17 Spotify failure behavior

Spotify is an enrichment service, not the only source of song identity.

If Spotify authentication, network access, matching, or artwork retrieval fails and project configuration allows nonfatal enrichment failure:

```text
retain YT Music/source metadata
retain existing artwork when valid
record Spotify failure
continue the song pipeline
```

The pipeline must never blank good source metadata merely because Spotify is unavailable.

## 2A.18 Spotify matching rule retained from source specification

Unless explicitly revised in a later project decision, the current candidate-selection contract remains:

```text
search = title + album
process Spotify results in returned order
compare candidate duration against actual local audio duration
accept the first candidate within configured duration tolerance
```

Once accepted, the matched track and album become the preferred major-field metadata source.

No additional hidden fuzzy scoring should be introduced merely because the overlay is comprehensive.


# 3. HIGH-LEVEL SYSTEM ARCHITECTURE

```text
                         YOUTUBE MUSIC PLAYLIST
                                  |
                                  v
                         PLAYLIST INGESTION
                                  |
                         permanent serials
                                  |
                                  v
                          NEXT ACTIONABLE SONG
                                  |
                                  v
                         ONE SOURCE ACQUISITION
                                  |
                    +-------------+-------------+
                    |             |             |
                    v             v             v
                  AUDIO        SOURCE       ARTWORK
                               INFO JSON
                    |             |             |
                    +-------------+-------------+
                                  |
                                  v
                       SOURCE PROBE + HASH
                                  |
                                  v
                       METADATA NORMALIZATION
                                  |
                                  v
                         SPOTIFY ENRICHMENT
                           optional only
                                  |
                                  v
                           CANONICAL ISRC
                                  |
                                  v
                         ISRC DUPLICATE GATE
                                  |
                                  v
                     YOUTUBE VISUAL VIDEO SEARCH
                                  |
                                  v
                       SELECTED YOUTUBE VIDEO
                                  |
                                  v
                      YOUTUBE REFERENCE AUDIO
                                  |
                                  v
                  30-SECOND GLOBAL OFFSET MATCH
                                  |
                                  v
                  SAVE VIDEO + OFFSET PROVENANCE
                                  |
                                  v
                        LRCLIB SYNCED LRC
                            /          \
                           /            \
                     NO SYNCED LRC     SYNCED LRC
                          |                |
                          v                v
                       TERMINAL        ONE LRC PARSE
                       SKIPPED               |
                                          v
                                  ONE ORIGINAL AUDIO DECODE
                                          |
                                          v
                                       DEMUCS
                                          |
                         +----------------+----------------+
                         |                                 |
                         v                                 v
                  WORD ALIGNMENT PATH                WHOLE-SONG 8D PATH
                         |                                 |
                   VAD + energy                           |
                   normalization                          |
                   LRC anchors                            |
                   chunking                               |
                   MMS                                    |
                   CTC                                    |
                   token->word                            |
                   overlap merge                          |
                   chronology                             |
                   quality                                |
                         |                                 |
                         v                                 |
                 CANONICAL WORD TIMELINE                   |
                         |                                 |
                         v                                 |
                Song_wordlevel.lrc                        |
                         |                                 |
                         +-------------------+-------------+
                                             |
                                             v
                                  ADVANCED WHOLE-SONG 8D
                                             |
                                             v
                                      Song_8D.mp3
                                             |
                                             | ONLY REEL AUDIO SOURCE
                                             v
                                  TERMINAL HOOK INPUT
                                             |
                                  +----------+----------+
                                  |                     |
                                  v                     v
                           YOUTUBE INTERVAL      8D AUDIO INTERVAL
                           + stored offset       + stored 8D master
                                  |                     |
                                  +----------+----------+
                                             |
                                             v
                                    WORD-LEVEL LYRICS
                                             |
                                             v
                                      REEL RENDERING
                                             |
                                             v
                                  FINAL REEL VALIDATION
                                             |
                                             v
                                  FINAL JSON/MP3 ASSEMBLY
                                             |
                                             v
                                    ATOMIC PROMOTION
                                             |
                          +------------------+------------------+
                          |                                     |
                          v                                     v
                    songs/final/                         reels/generated/
                    Song.mp3                             Song_reel.mp4
                    Song.lrc                             Song_reel.json
                    Song_wordlevel.lrc
                    Song.json
                    Song_8D.mp3
                          |
                          v
                     FINALIZED
                          |
                          v
                      NEXT SONG
```

---

# 4. DESIGN PRINCIPLES

## 4.1 One song is the atomic processing unit

The normal production loop is:

```text
Song 001
  -> complete or terminal skip/error
Song 002
  -> complete or terminal skip/error
Song 003
  -> ...
```

The system must not require a separate “finish all Phase 1 songs, then run Phase 2, then run Phase 3” workflow.

GPU models and caches remain loaded/reusable as performance permits, but the state machine advances song-by-song.

## 4.2 Source truth, processing truth, export truth

The project maintains three layers:

```text
SOURCE
  original/acquired audio
  source LRC
  source JSON
  source metadata

PROCESSING
  decoded PCM
  Demucs stems
  activity maps
  normalized lyric representation
  MMS emissions
  CTC alignments
  canonical word timeline
  8D automation/intermediates
  Reel intermediates

PUBLISHED EXPORTS
  Song.mp3
  Song.lrc
  Song_wordlevel.lrc
  Song.json
  Song_8D.mp3
  Song_reel.mp4
  Song_reel.json
```

No rendered export becomes a hidden upstream processing dependency.

## 4.3 Preserve, then add

Existing JSON fields, metadata, artwork, identifiers, URLs, and historical data are preserved. New unified information is added under controlled namespaces rather than replacing arbitrary existing data.

## 4.4 Canonical timeline first

The canonical word timeline is created once. All downstream word-timing exports derive from it.

## 4.5 No false precision

Low-confidence timing must be represented honestly through scores, source labels, interpolation flags, review reasons, or missing states. The pipeline must not turn a weak estimate into a supposedly exact timestamp simply because a serializer needs a number.

## 4.6 Deterministic behavior

Given identical source files, models, versions, configuration, and hook input, the logical processing result should be reproducible as closely as the pinned runtime permits.

## 4.7 Explicit fallback behavior

Fallbacks are allowed only where the architecture specifies them. A fallback must be logged, represented in provenance, and must not silently change the meaning of the result.

---

# 5. DIRECTORY STRUCTURE

Recommended unified project:

```text
unified_music_reel_project/
|
├── main.py
├── config.json
├── requirements.txt
├── requirements-dev.txt
├── README.md
├── BUILD_INFO.md
├── CHANGELOG.md
├── PROJECT_PLAN.md
├── .gitignore
|
├── db/
│   ├── playlist.db
│   ├── songs.db
│   ├── reels.db
│   └── backups/
|
├── songs/
│   ├── final/
│   │   ├── <basename>.mp3
│   │   ├── <basename>.lrc
│   │   ├── <basename>_wordlevel.lrc
│   │   ├── <basename>.json
│   │   └── <basename>_8D.mp3
│   │
│   └── skipped/
│       └── no_synced_lrc/
│           ├── <basename>.mp3
│           └── <basename>.json
|
├── reels/
│   └── generated/
│       ├── <basename>_reel.mp4
│       └── <basename>_reel.json
|
├── work/
│   └── <song_key>/
│       ├── state.json
│       ├── acquisition/
│       ├── source/
│       ├── metadata/
│       ├── youtube/
│       │   ├── selected_video.json
│       │   ├── reference_audio.wav
│       │   └── offset.json
│       ├── lyrics/
│       │   ├── source.lrc
│       │   ├── parsed.json
│       │   └── wordlevel.lrc
│       ├── audio/
│       │   ├── source_full.wav
│       │   ├── source_16k.wav
│       │   ├── stems/
│       │   │   ├── vocals.wav
│       │   │   ├── drums.wav
│       │   │   ├── bass.wav
│       │   │   └── other.wav
│       │   ├── analysis/
│       │   └── 8d/
│       │       ├── automation.json
│       │       ├── processed_stems/
│       │       └── full_8d_master.wav
│       ├── alignment/
│       │   ├── normalized_reference.json
│       │   ├── chunks.json
│       │   ├── emissions/
│       │   ├── chunk_results/
│       │   ├── canonical_words.json
│       │   └── quality.json
│       ├── hook/
│       │   ├── hook.json
│       │   └── lyric_subset.json
│       ├── video/
│       │   ├── guarded_clip.mp4
│       │   └── exact_clip.mp4
│       ├── reel/
│       │   ├── frames/
│       │   ├── audio_hook.mp3
│       │   ├── lyric_render.mp4
│       │   └── reel.mp4
│       ├── validation/
│       └── provenance/
|
├── assets/
│   ├── fonts/
│   └── other/
|
├── models/
│   ├── mms/
│   ├── demucs/
│   └── vad/
|
├── logs/
│   └── unified_pipeline.log
|
├── src/
│   ├── config.py
│   ├── models.py
│   ├── db.py
│   ├── state_machine.py
│   ├── inventory.py
│   ├── acquisition.py
│   ├── metadata.py
│   ├── spotify.py
│   ├── duplicate.py
│   ├── youtube_discovery.py
│   ├── youtube_sync.py
│   ├── lrclib.py
│   ├── artwork.py
│   ├── hashing.py
│   ├── audio_io.py
│   ├── demucs.py
│   ├── activity.py
│   ├── lrc_parser.py
│   ├── telugu_normalizer.py
│   ├── tokenizer.py
│   ├── chunker.py
│   ├── mms.py
│   ├── ctc_aligner.py
│   ├── word_timeline.py
│   ├── alignment_validator.py
│   ├── wordlevel_lrc.py
│   ├── audio_8d.py
│   ├── hook_input.py
│   ├── video.py
│   ├── lyric_renderer.py
│   ├── reel.py
│   ├── mp3_metadata.py
│   ├── json_record.py
│   ├── packager.py
│   ├── validator.py
│   ├── cache.py
│   ├── recovery.py
│   ├── provenance.py
│   └── pipeline.py
|
└── tests/
    ├── unit/
    ├── integration/
    ├── fixtures/
    └── failure_injection/
```

The exact directory names may evolve, but the boundaries and published-file contract should remain stable.

---

# 6. USER WORKFLOW

## 6.1 Normal user workflow

The user performs:

```text
python main.py
```

The system ingests/synchronizes the playlist and starts the first actionable song.

Everything through the whole-song 8D stage is automatic.

When the song is ready for the manual hook, the terminal displays:

```text
============================================================
MANUAL HOOK SELECTION
============================================================

Song: 001_Gelupu Thalupule_Mani Sharma, Sreerama Chandra
Original duration: 05:18.000

Enter hook START (MM:SS.xxx):
>
```

Then:

```text
Enter hook END (MM:SS.xxx):
>
```

Example:

```text
> 02:12.920
> 03:11.720
```

The system prints:

```text
Start:    132920 ms
End:      191720 ms
Duration:  58800 ms

✓ start >= 0
✓ end > start
✓ end <= source duration
✓ YouTube mapped interval can be checked
✓ whole-song 8D master available
```

Then Reel processing proceeds automatically.

## 6.2 Hook input is not stored in a hook configuration file

There is no `hook_timeline.json` in the unified project.

The terminal is the human interface.

The entered hook is persisted in the database and `SongName.json` so interrupted processing can resume safely.

## 6.3 Restart behavior

If the program stops after a valid hook has been entered, the restart procedure reads the saved hook state. It does not ask the user again unless the saved hook is invalid, stale because the source identity changed, or explicitly cleared through a manual command.

## 6.4 No-synced-LRC behavior

A song with no accepted synchronized LRCLIB result never reaches the hook prompt.

```text
LRCLIB response
    ↓
no synchronized lyrics
    ↓
status = SKIPPED_NO_SYNCED_LRC
terminal = true
    ↓
save skip provenance
    ↓
move to next playlist song
```

---

# 7. MASTER SONG IDENTITY

The system must distinguish playlist occurrence identity from retained song identity.

## 7.1 Playlist occurrence identity

Every occurrence in the playlist receives a permanent integer serial number in YT Music playlist order.

Rules:

- serials never change;
- serials are never reused;
- repeated tracks remain separate playlist occurrences;
- unavailable playlist entries retain their serials;
- an occurrence can later become usable without receiving a new serial.

## 7.2 Retained song identity

When an ISRC exists and is valid, it is the sole duplicate identifier.

No fallback fuzzy matching is performed using title, artist, album, duration, filename, YouTube ID, audio hash, or composite similarity.

## 7.3 Unified song key

Recommended internal identity:

```text
song_key = playlist_serial + retained-song identity
```

The exact database key can be a generated internal ID, but the permanent playlist serial must remain available as a durable external lineage identifier.

---

# 8. UNIFIED DATABASE

The current unified project uses three persistent SQLite databases:

```text
db/
├── playlist.db
├── songs.db
└── reels.db
```

The three-file split is deliberate. `playlist.db` is the frozen playlist-ingestion and processing queue; `songs.db` is the retained-song and song-processing database; `reels.db` is the Reel/hook/render database. They are linked by stable application identifiers rather than hidden module/database dependencies.

## 8.1 Core tables

### `playlist_entries`

Fields:

```text
serial_number PRIMARY KEY
playlist_position
ytm_playlist_id
ytm_video_id
ytm_url
raw_playlist_item_json
title
artist
album
duration
status
error_code
error_message
terminal
created_at
updated_at
```

### `songs`

Contains normalized retained-song metadata and lineage.

Important fields include:

```text
song_id
serial_number
isrc
normalized metadata
source hashes
source file information
Spotify IDs / provenance
YouTube selected-video ID / URL / title
YouTube offset
lyrics status
source/final file paths
final hashes
pipeline state
quality state
created_at
updated_at
```

### `processing_runs`

One row per pipeline run or attempt.

Fields:

```text
run_id
song_id
pipeline_version
config_hash
started_at
finished_at
status
failure_code
```

### `stage_runs`

Tracks each stage execution and artifact identity.

```text
run_id
song_id
stage_name
stage_version
input_fingerprint
output_fingerprint
started_at
finished_at
status
error_code
```

### `chunks`

Stores Phase 2 alignment chunks and quality information.

### `word_timeline`

Stores canonical word timing. Each word should include:

```text
song_id
line_index
word_index
original_text
normalized_text
start_ms
end_ms
score
source
interpolation_flag
review_reason
```

### `hooks`

Stores the terminal-entered hook.

```text
song_id
start_ms
end_ms
duration_ms
input_method
entered_at
source_mp3_sha256
```

### `reels`

Stores Reel generation state and output provenance.

### `artifacts`

Maps stage outputs to hashes and dependency fingerprints.

## 8.2 Unified state invariants

- A terminal skipped song is not selected by the normal pending query.
- A finalized song has all required final package artifacts.
- A finalized Reel points to a finalized song package.
- A `wordlevel_lrc_ready` state requires a valid canonical timeline.
- `audio_8d_ready` requires a whole-song 8D output with duration consistent with source audio.
- `hook_ready` requires valid start/end and matching source identity.
- A promoted final artifact is never referenced until validation has passed.
- Source hashes recorded for a stage must correspond to the actual source bytes used.

---

# 9. PIPELINE STATE MACHINE

## 9.1 Primary states

```text
PENDING
    ↓
ACQUIRING
    ↓
SOURCE_READY
    ↓
ENRICHED
    ↓
DUPLICATE_CHECKED
    ↓
YOUTUBE_VIDEO_READY
    ↓
YOUTUBE_OFFSET_READY
    ↓
LYRICS_READY
    ↓
STEMS_READY
    ↓
ALIGNMENT_READY
    ↓
WORDLEVEL_LRC_READY
    ↓
AUDIO_8D_READY
    ↓
HOOK_REQUIRED
    ↓
HOOK_READY
    ↓
VIDEO_SEGMENT_READY
    ↓
REEL_AUDIO_READY
    ↓
LYRIC_RENDER_READY
    ↓
REEL_RENDERED
    ↓
FINAL_VALIDATED
    ↓
FINALIZED
```

## 9.2 Terminal states

```text
SKIPPED_NO_SYNCED_LRC
DUPLICATE_KEEP_PREVIOUS
FINALIZED
```

## 9.3 Review/failure states

```text
NEEDS_REVIEW
RETRYABLE_ERROR
FAILED
```

Examples:

```text
YOUTUBE_OFFSET_REVIEW
ALIGNMENT_NEEDS_REVIEW
HOOK_INVALID
VIDEO_INTERVAL_UNAVAILABLE
AUDIO_8D_VALIDATION_FAILED
FINAL_VALIDATION_FAILED
```

## 9.4 Pipeline status versus quality status

These are independent.

Example:

```text
pipeline_status = FINALIZED
quality_status  = NEEDS_REVIEW
```

This permits the application to report that processing completed while a human should inspect specific timing quality evidence.

---

# 10. STAGE 0 — DOCTOR / ENVIRONMENT CHECK

`python main.py --doctor` should report, at minimum:

- Python version;
- FFmpeg availability/version;
- FFprobe availability/version;
- CUDA driver visibility;
- PyTorch CUDA availability;
- actual CUDA device name;
- Demucs availability;
- MMS model/cache status;
- VAD model/cache status;
- NVIDIA NVENC availability in FFmpeg;
- required font availability or controlled download path;
- required project directories;
- write permissions;
- database access;
- song inventory health;
- LRC pairing health;
- model-cache health;
- JavaScript runtime status required by the installed yt-dlp path.

The system must distinguish:

```text
OS/driver sees GPU
```

from:

```text
PyTorch can actually execute CUDA
```

The Phase 2 field-run history documents the specific failure in which `nvidia-smi` saw an NVIDIA GPU but PyTorch was CPU-only, causing `Torch not compiled with CUDA enabled`. This becomes a startup diagnostic requirement for the unified project.

---

# 11. STAGE 1 — PLAYLIST INGESTION

Use `ytmusicapi` to obtain the complete playlist.

Rules inherited from Phase 1:

- returned playlist order is authoritative;
- no alphabetical/duration/popularity sorting;
- unavailable entries do not crash ingestion;
- unavailable entries preserve serial and position;
- existing serials are reused when a previously unavailable item becomes usable.

For each playlist occurrence, store the raw playlist-item JSON as a protected provenance record.

The ingestion stage should be idempotent: rerunning playlist ingestion updates known records while preserving permanent serial identity.

---

# 12. STAGE 2 — ONE COMPLETE SOURCE ACQUISITION

For the next actionable playlist occurrence, create a song-specific work directory.

Use one complete initial `yt-dlp` acquisition to obtain:

```text
master.mp3
master.info.json
available thumbnails/artwork
```

Do not use `--add-metadata` for the initial acquisition. The acquisition artifact is not the final tagged library MP3. Mutagen is the final metadata writer.

The acquired audio source is the YT Music source belonging to the playlist occurrence. The pipeline does not search general YouTube for a replacement song audio source.

Validate:

- MP3 exists and opens;
- source JSON exists and is valid;
- duration is readable;
- artwork is readable where supplied;
- the acquired media identifies the expected source.

---

# 13. STAGE 3 — SOURCE PROBE / HASH / METADATA NORMALIZATION

Probe the audio with FFprobe using explicit UTF-8 handling on Windows:

```python
subprocess.run(
    ...,
    text=True,
    encoding="utf-8",
    errors="replace",
)
```

The probe must tolerate:

- empty output;
- invalid JSON;
- multiple streams;
- JPEG/MJPEG artwork streams;
- metadata containing Telugu/non-ASCII text.

Select the actual audio stream, not the artwork stream.

Record:

```text
duration
sample rate
channels
codec
container
bitrate
file size
```

Calculate SHA-256 of the actual source bytes.

The actual decoded source audio duration is authoritative for later boundary validation.

---

# 14. STAGE 4 — METADATA LAYERS

Maintain three layers.

## Layer A — raw source metadata

The complete `master.info.json` and original source/API structures.

## Layer B — normalized application metadata

Examples:

```text
normalized_title
normalized_artist
normalized_album
normalized_album_artist
normalized_release_date
normalized_isrc
normalized_track_number
normalized_disc_number
```

## Layer C — final MP3 metadata

A concise player-facing projection derived from the normalized model and final provenance.

## 14.1 Metadata precedence

Use the Phase 1 precedence model:

### Title

Primary source/yt-dlp normalized title; Spotify matched track may overlay when accepted.

### Artist

Primary source/yt-dlp normalized artist; Spotify may overlay when matched.

### Album

Primary source album; Spotify may overlay/enrich when matched.

### Album artist

Prefer matched Spotify album-artist data when available; otherwise source metadata.

### Release date

Use the music release date rather than treating upload date as release date. Spotify may enrich/overlay the release date when a match is accepted.

### Track/disc

Spotify catalog values may enrich source values. Playlist serial is never used as the track number.

### Publisher/label

Source publisher when available; Spotify album label can enrich/fill.

### Copyright

Source copyright when available; Spotify album copyright data can enrich/fill.

### Duration

Actual downloaded MP3 duration is authoritative. Spotify duration is used for matching. YouTube video duration remains video metadata.

### ISRC

Priority:

```text
1. valid matched Spotify ISRC
2. valid source/yt-dlp ISRC
3. missing
```

---

# 15. STAGE 5 — OPTIONAL SPOTIFY ENRICHMENT

Spotify is enrichment only. It is never the audio source.

Authentication:

- Client Credentials flow;
- environment variables may override config when enabled;
- secrets never logged or stored in sidecars.

Search:

```text
{title} {album}
```

Candidate selection:

- preserve Spotify returned order;
- use actual downloaded audio duration as the comparison reference;
- accept the first result within the configured duration tolerance;
- do not introduce a scoring/ranking system.

Import useful fields:

- track ID/name/URL/URI;
- artists and IDs;
- album ID/name/URL/type;
- release date/precision;
- total tracks;
- duration;
- explicit flag;
- popularity;
- external IDs including ISRC;
- album label/copyrights;
- artwork metadata.

Spotify artwork rule:

- select the largest returned image;
- download the exact bytes;
- validate readability;
- preserve bytes unchanged;
- embed unchanged as front-cover APIC;
- record source URL, dimensions, MIME, byte length, hash.

Spotify failure is nonfatal unless configuration explicitly says otherwise.

---

# 16. STAGE 6 — ISRC DUPLICATE GATE

Canonicalize ISRC by removing spaces/hyphens and uppercasing the alphanumeric representation. Validate the expected standard format. Invalid ISRC is treated as missing.

If a valid ISRC exists, query the unified `songs` table/index.

No title/artist/duration/audio-hash similarity is used.

## 16.1 Keep previous

- existing retained song remains untouched;
- current acquisition is discarded;
- playlist occurrence becomes terminal duplicate;
- no new retained-song row.

## 16.2 Keep current

- fully build and validate replacement before deleting old physical artifacts;
- perform retained-row transition transactionally;
- maintain serial lineage;
- remove previous physical artifacts only after the new package is committed successfully.

---

# 17. STAGE 7 — YOUTUBE MUSIC-VIDEO DISCOVERY

Build exactly:

```text
{title} {album} official video song
```

Process results in returned order.

Skip a result only when its title contains `lyrics`, case-insensitively.

Select the first remaining result immediately.

Do not score YouTube search results by popularity, duration, channel, views, or manual weights.

Do not automatically replace the selected video just because the later synchronization quality is poor. If the selected video cannot produce a valid synchronization offset under the integrated rules, record an explicit sync failure/review state.

Store:

```text
selected video ID
URL
title
search query
result index
fetched count
available metadata
raw selected video info where acquired
```

---

# 18. STAGE 8 — YOUTUBE REFERENCE AUDIO

After selecting the visual YouTube video, obtain its audio as a synchronization reference.

This audio is **not** the final Reel audio.

The original local song remains the source for the final Reel audio.

The reference audio may be temporary and can be cached under:

```text
work/<song_key>/youtube/reference_audio.wav
```

Record its source video ID and hash.

---

# 19. STAGE 9 — ADVANCED 30-SECOND YOUTUBE SONG OFFSET MATCH

This stage runs early, before Phase 2 and before the no-synced-LRC skip decision.

## 19.1 Objective

Determine where the beginning of the original song occurs in the selected YouTube visual-video audio.

The system is solving:

```text
Original[0 : 30s]
        ↓
where does this 30s pattern occur in
YouTubeAudio[t : t+30s] ?
```

The result is one global song offset:

```text
youtube_time = local_time + offset_ms
```

## 19.2 Reference interval

Use exactly the first 30 seconds of the original local song, subject to minimum source-duration validation. If the song itself is shorter than 30 seconds, the implementation must use the entire available song and record the shorter reference duration explicitly rather than fabricating audio.

## 19.3 Search scope

Scan the YouTube reference audio across the complete available timeline for candidate windows of approximately the same duration as the reference.

The search may use a coarse-to-fine strategy for performance:

```text
coarse scan across complete YouTube audio
        ↓
retain strong candidates
        ↓
fine-resolution scoring around candidates
        ↓
valid-offset filtering
        ↓
final candidate selection
```

## 19.4 Matching signals

The matcher should use structurally robust signals rather than raw amplitude alone.

### A. Normalized waveform shape

Convert both signals to a common mono/low-rate representation, normalize amplitude and optionally remove DC offset, then calculate a normalized correlation/distance.

### B. Energy envelope

Calculate a smoothed short-time RMS or energy envelope. Normalize it so differences in mastering loudness do not dominate.

### C. High/low transitions

Detect the timing of meaningful rises and falls in the energy envelope. Compare their order and approximate normalized positions.

### D. Peaks/valleys

Detect robust local maxima/minima after smoothing. Compare peak/valley ordering and relative positions.

### E. Optional supporting low-rate spectral/transition feature

Use a computationally small supporting representation such as reduced spectral-energy shape or onset/transition density. This is a supporting signal, not an alternate transcript or beat tracker.

## 19.5 Recommended initial score

The exact weights remain configurable and should be tuned against test songs. A reasonable initial baseline for implementation is:

```text
waveform correlation       0.35
energy correlation         0.30
peak/valley similarity     0.20
transition similarity      0.15
                           -----
                           1.00
```

All sub-scores should be normalized to a consistent range before combination.

This is an integrated implementation recommendation, not a claim that the original plans locked these exact percentages.

## 19.6 Candidate offset constraint

The accepted song offset must be within:

```text
0 ms <= offset_ms <= 30,000 ms
```

The purpose of this constraint is to reject visually plausible repeated-structure matches that occur much later in the video.

If the highest-scoring candidate is:

```text
offset = 47,300 ms
score  = 0.962
```

it is rejected because it violates the offset limit.

The system then considers the next-highest-scoring candidate that satisfies the limit, for example:

```text
offset = 6,840 ms
score  = 0.941
```

and accepts that candidate if it passes the remaining validation gates.

## 19.7 Candidate ranking policy

Within the valid offset range, rank by combined score.

If two valid candidates are effectively tied within a configurable near-best tolerance, prefer the earlier candidate. This retains the original plan's protection against accidentally selecting a later repeated chorus when an earlier structurally similar match exists.

## 19.8 Offset validation

Before accepting the candidate, validate:

- reference and candidate window durations are compatible;
- score is finite;
- all component scores are finite;
- offset is finite;
- offset is within 0–30 seconds;
- candidate window lies within the available YouTube audio;
- local source identity matches the hash used to calculate the offset.

## 19.9 Saved result

Store:

```json
{
  "method": "first_30s_global_anchor",
  "reference_duration_ms": 30000,
  "offset_ms": 6840,
  "score": 0.941,
  "waveform_score": 0.95,
  "energy_score": 0.94,
  "peak_valley_score": 0.91,
  "transition_score": 0.96,
  "max_allowed_offset_ms": 30000,
  "selected_candidate_rank": 2
}
```

Also retain enough top-candidate evidence to explain rejected candidates, especially candidates rejected solely for exceeding the 30-second constraint.

## 19.10 Persistence destinations

The accepted global offset is stored in all three durable layers:

### Database

```text
youtube_offset_ms
```

### Master JSON

```text
youtube_video.offset_ms
youtube_video.offset_method
youtube_video.offset_score
...
```

### Final MP3 metadata

Recommended concise custom fields:

```text
TXXX:UNIFIED_YOUTUBE_VIDEO_ID
TXXX:UNIFIED_YOUTUBE_VIDEO_URL
TXXX:UNIFIED_YOUTUBE_OFFSET_MS
TXXX:UNIFIED_YOUTUBE_OFFSET_METHOD
```

The master JSON remains the detailed provenance authority.

## 19.11 Critical source separation

This stage uses:

```text
ORIGINAL LOCAL SONG AUDIO
```

and:

```text
YOUTUBE REFERENCE AUDIO
```

It must **never** use `SongName_8D.mp3`.

---

# 20. STAGE 10 — LRCLIB SYNCHRONIZED LYRICS

Use only the permitted:

```text
GET /api/get
```

endpoint.

Do not call `/api/search`.

Use current song metadata and actual source duration for lookup.

Accept only synchronized lyrics suitable for `.lrc` processing.

Plain-only lyrics do not satisfy the word-level pipeline input contract.

## 20.1 No synced LRC terminal skip

If no valid synchronized LRC is available:

```text
lyrics_status = no_synced_lrc
pipeline_status = SKIPPED_NO_SYNCED_LRC
terminal = true
```

Do not execute:

```text
Demucs
MMS
CTC
word-level LRC
whole-song 8D
hook prompt
YouTube hook extraction
Reel rendering
```

The accepted YouTube video and offset have already been recorded before this decision, as required by the integrated ordering.

## 20.2 Skipped-song storage

Recommended:

```text
songs/skipped/no_synced_lrc/
    SongName.mp3
    SongName.json
```

The MP3 may contain the consolidated acquisition metadata and YouTube offset; no `.lrc`, word-level LRC, or 8D master is published for a skipped song.

The database prevents the skipped song from being selected on subsequent normal runs.

An explicit manual retry/reset command may be provided later to re-open the state if the operator wants to try again after the lyrics source changes.

---

# 21. STAGE 11 — PARSE THE LRC ONCE

Parse the accepted synchronized LRC into a canonical source lyric model.

Support ordinary line timestamps and the inline word-timestamp structure used by the source material.

Example:

```text
[00:25.18]గెలుపు [00:25.98]తలుపులే [00:27.34]తీసే [00:29.52]ఆకాశమే
```

Canonical parse concept:

```json
{
  "line_index": 0,
  "start_ms": 25180,
  "text": "గెలుపు తలుపులే తీసే ఆకాశమే",
  "words": [
    {"text": "గెలుపు", "start_ms": 25180},
    {"text": "తలుపులే", "start_ms": 25980},
    {"text": "తీసే", "start_ms": 27340},
    {"text": "ఆకాశమే", "start_ms": 29520}
  ]
}
```

For source lines with word timing, retain those timestamps as coarse anchors/reference information. If a source format lacks a direct final word-end timestamp, derive a provisional end from the next word start or next line start while clearly distinguishing provisional boundaries from the final CTC-derived end spans.

The source lyric wording is preserved exactly for display/export.

---

# 22. STAGE 12 — AUDIO DECODE ONCE

Decode the source MP3 into a lossless working representation.

Preferred processing representation:

```text
PCM
float-compatible
lossless WAV
```

Do not repeatedly decode/re-encode MP3 between stages.

For alignment, derive:

```text
16 kHz
mono
lossless PCM/WAV
```

The Windows FFmpeg temporary-file rule from Phase 2 is mandatory:

- use a final `.wav` suffix for WAV temporary output, or
- explicitly pass `-f wav`.

The robust production implementation should prefer both where practical.

The source audio sample count and duration should be preserved deterministically when preparing the stem/alignment timeline.

---

# 23. STAGE 13 — DEMUCS ONCE, SHARED BY DOWNSTREAM ENGINES

This is one of the most important unified-project optimizations.

Run Demucs only once per source identity/model configuration.

Expected stem set:

```text
vocals
drums
bass
other
```

The stem cache becomes a shared dependency:

```text
DEMUCS
  |
  +--> Phase 2 alignment
  |
  +--> whole-song 8D
  |
  `--> diagnostics/provenance
```

## 23.1 Model

Baseline from the source material:

```text
htdemucs
```

Prefer a vocals-focused separation route for alignment where supported, while still retaining the full 4-stem result needed by the 8D engine.

## 23.2 GPU

CUDA first.

If CUDA OOM or a compatible inference failure occurs, the recovery strategy may:

1. release tensor/model references;
2. clear cached CUDA memory where safe;
3. reduce segment size where supported;
4. retry;
5. fall back to CPU if configured and the retry fails.

Always record actual device usage.

## 23.3 Stem validation

Each expected stem must:

- exist;
- be readable;
- contain finite samples;
- have a usable signal;
- be duration-aligned to the source timeline within tolerance.

If Demucs produces a slightly different sample count, deterministically pad/trim the stems to the source timeline before downstream processing.

## 23.4 Shared artifact identity

The stem cache is reusable only when all relevant dependencies match:

```text
source_mp3_sha256
Demucs model name
Demucs model revision
Demucs configuration
processing version
```

---

# 24. STAGE 14 — FULL-SONG AUDIO ANALYSIS

Use the original/full mix and available stems to compute diagnostics and downstream control features.

Analyze as applicable:

- RMS/energy;
- peak amplitude;
- short-term energy envelope;
- spectral centroid;
- spectral rolloff;
- spectral flux;
- onset density;
- chroma;
- log-mel features;
- energy contour;
- lyric-free/silence regions;
- stem activity;
- vocal activity;
- arrangement density.

The analysis cannot override manual hook selection.

This analysis may also supply structural control points for the whole-song 8D automation, but it never replaces the source lyric reference or the canonical word alignment.

---

# 25. STAGE 15 — VOCAL ACTIVITY / VAD

Use Silero VAD plus energy/RMS activity as supporting evidence.

Suggested starting configuration inherited from the Phase 2 design:

```text
threshold = 0.50
min_speech_duration_ms = 250
min_silence_duration_ms = 300
speech_pad_ms = 150
```

These remain configurable and must be benchmarked.

Important semantic rule:

```text
VAD activity != proof of instrumental truth
```

A VAD gap can be evidence for chunking/instrumental analysis, but it must not be treated as a perfect semantic classifier.

---

# 26. STAGE 16 — REVERSIBLE TELUGU NORMALIZATION

Maintain a display/reference representation and a model-alignment representation.

```text
ORIGINAL DISPLAY TEXT
       |
       +-----> final lyrics / wordlevel LRC / Reel text
       |
       v
REVERSIBLE NORMALIZED TEXT
       |
       +-----> tokenizer / MMS / CTC
```

Normalization must not destroy the original lyric wording.

Possible transformations may include punctuation handling, whitespace normalization, numeric expansion, symbol handling, or equivalent model-compatible transformations defined by the locked normalization layer.

For every transformed unit, maintain enough mapping to return to the original displayed word.

Unsupported references must be explicit rather than silently deleted.

Use fields such as:

```text
normalization_status
unsupported
interpolated
review_reason
```

---

# 27. STAGE 17 — LRC ANCHOR MODEL AND REFERENCE-AWARE CHUNKING

LRC timestamps are coarse anchors. They are not treated as immutable final word timestamps.

They are used to:

- constrain search regions;
- define chunks;
- identify long lyric-free intervals;
- detect blank-marker boundaries;
- validate final drift.

## 27.1 Blank markers

A line such as:

```text
[04:12.82]
```

with no lyric text is a structural marker.

Rules:

- strongly influence chunk boundaries;
- influence lyric-start constraints;
- a word whose **start** occurs inside the explicit blank-marker region is invalid and should trigger re-alignment/review;
- a word that begins before the marker and ends slightly after it is not automatically invalid merely because the acoustic span crosses the structural marker.

## 27.2 Chunk duration

Use approximately 12 seconds as the preferred target with soft bounds rather than a rigid hard size. The source plan describes roughly 4–20 seconds as a practical range.

Chunks should respect:

- line boundaries;
- blank gaps;
- activity changes;
- vocal density;
- context needs.

Maintain distinct concepts:

```text
logical lyric range
model audio context range
```

The model may see overlap/context that is not automatically emitted as final lyric ownership.

---

# 28. STAGE 18 — MMS TELUGU ACOUSTIC EVIDENCE

Use:

```text
facebook/mms-1b-all
```

with the explicit Telugu adapter/language configuration.

The model is an acoustic evidence generator, not the source of lyric wording.

Correct architecture:

```text
source LRC reference text
       +
MMS frame-level emissions
       |
       v
CTC forced alignment
       |
       v
token spans
       |
       v
word spans
```

Forbidden final architecture:

```text
MMS ASR argmax transcript
       |
       v
replace supplied lyrics
```

The source lyric wording remains canonical for display.

## 28.1 Model caching

The large MMS checkpoint is downloaded once into the configured cache and reused. It must not be downloaded once per song.

Windows symlink warnings are cache-efficiency concerns, not alignment failures.

## 28.2 GPU lifecycle

Default production policy:

```text
one GPU worker
Demucs
release/reduce live resources
MMS
release/reduce live resources
next song
```

Do not launch multiple large MMS/CTC workers simultaneously on a single constrained GPU merely because the CPU has spare cores.

---

# 29. STAGE 19 — TRUE CTC FORCED ALIGNMENT

For each chunk:

```text
normalized reference tokens
       +
frame-level MMS log probabilities
       |
       v
CTC trellis / Viterbi-style alignment
       |
       v
token start/end spans
```

The CTC implementation must correctly handle repeated labels and reference-token positions.

Do not use deprecated TorchAudio alignment APIs as an architectural dependency. The project should isolate its own CTC alignment algorithm so the algorithm remains controlled and testable.

The token alignment is then converted to word spans using the token-to-word mapping produced from the original lyric reference.

Each canonical word should have at least:

```text
original_word
normalized_word
start_ms
end_ms
score
source
```

plus provenance fields where useful.

---

# 30. STAGE 20 — DUAL-SIGNAL ALIGNMENT FALLBACK

Primary alignment signal:

```text
Demucs vocal-bearing signal
```

The original mix must remain available as a fallback.

If a vocal-stem alignment is low-confidence:

```text
vocal alignment low confidence
        |
        v
align same reference against original mix
        |
        v
compare validated quality
        |
        v
retain the better validated result
```

Do not silently substitute the fallback. Record which signal was used and why.

---

# 31. STAGE 21 — CHUNK QUALITY, OVERLAP DEDUPLICATION, AND GLOBAL MERGE

Every chunk must pass both:

```text
intra-chunk chronology validation
inter-chunk chronology validation
```

before the global merge is accepted.

## 31.1 Immutable merge inputs

Chunk candidates must be treated as immutable inputs. Each repair pass creates new result objects rather than mutating candidates and reusing already-modified timings.

## 31.2 Overlap deduplication

Because chunks may overlap for context, the merge layer must determine which chunk owns each final word/timing.

The merge must preserve:

- line/word identity;
- chronological order;
- source provenance;
- best validated candidate.

## 31.3 Global chronology

Never repair chronology by sorting timestamps independently of their words.

Correct flow:

```text
identify offending candidate/chunk
        ↓
resolve candidate/provenance conflict
        ↓
re-align affected region if necessary
        ↓
merge new immutable results
        ↓
validate again
```

## 31.4 Quality metrics

Track at least:

- overall alignment score;
- per-word score;
- low-confidence word count;
- P10-style lower-tail quality metric;
- anchor drift;
- interpolation count;
- missing/unsupported word count;
- blank-marker violations;
- chronology violations;
- chunk failures/retries.

The system should allow a completed run to have a `needs_review` quality state.

---

# 32. STAGE 22 — CANONICAL WORD TIMELINE

The canonical representation is the unified song's authoritative word timeline.

Conceptual structure:

```text
CanonicalSongTimeline
|
├── lines[]
│   ├── line_index
│   ├── source_timestamp_ms
│   ├── aligned_start_ms
│   ├── aligned_end_ms
│   └── words[]
│        ├── original_text
│        ├── normalized_text
│        ├── start_ms
│        ├── end_ms
│        ├── score
│        ├── source
│        ├── interpolated
│        └── review_reason
|
└── global metadata
```

Use integer milliseconds as the canonical time unit.

Do not use floating-point seconds as the authoritative storage format.

A timestamp-only representation is insufficient; retain both start and end spans.

---

# 33. STAGE 23 — WORD-LEVEL LRC EXPORT

Generate exactly one final word-level LRC export from the canonical word timeline:

```text
SongName_wordlevel.lrc
```

Rules:

- preserve original lyric wording;
- use canonical word start times;
- preserve global chronology;
- do not recompute timings independently in the serializer;
- do not sort words separately from their lexical identities;
- do not reparsed rounded LRC to recreate canonical timing.

If Phase 2 also embeds the canonical result into MP3 `SYLT`, both the external word-level LRC and embedded SYLT must originate from the same canonical timeline.

---

# 34. STAGE 24 — FINAL SONG MP3 METADATA ASSEMBLY

The final `SongName.mp3` must be created from the original source audio and updated surgically.

Preserve:

- existing ID3 frames;
- existing artwork;
- existing identifiers;
- existing USLT/SYLT where the project does not own them;
- TXXX/UFID/WXXX data;
- unknown supported metadata.

Add/update only the fields owned by the unified project.

## 34.1 Standard fields

Where meaningful:

```text
TIT2
TPE1
TPE2
TALB
TDRC
TRCK
TPOS
TCON
TCOM
TPUB
TCOP
TLAN
TBPM
TCMP
TENC
TLEN
TSRC
APIC
SYLT
USLT
```

## 34.2 Unified custom metadata

Recommended fields:

```text
TXXX:UNIFIED_PLAYLIST_SERIAL
TXXX:UNIFIED_PLAYLIST_POSITION
TXXX:UNIFIED_YTM_VIDEO_ID
TXXX:UNIFIED_YOUTUBE_VIDEO_ID
TXXX:UNIFIED_YOUTUBE_OFFSET_MS
TXXX:UNIFIED_YOUTUBE_OFFSET_METHOD
TXXX:UNIFIED_SPOTIFY_TRACK_ID
TXXX:UNIFIED_SPOTIFY_ALBUM_ID
TXXX:UNIFIED_CANONICAL_ISRC
TXXX:UNIFIED_LYRICS_STATUS
TXXX:UNIFIED_PIPELINE_VERSION
TXXX:UNIFIED_AUDIO_8D_STATUS
```

The exact custom-frame naming can be finalized during implementation, but the offset field is mandatory in the integrated design.

## 34.3 SYLT

The canonical word-level timing may be embedded in a dedicated project-owned synchronized lyrics frame.

Do not delete an existing unrelated SYLT frame merely because the unified project adds its own.

---

# 35. STAGE 25 — ADVANCED WHOLE-SONG 8D PROCESSING

This is a major integrated redesign of the standalone Phase 3 audio stage.

## 35.1 Critical definition

The 8D stage processes the **entire original song**:

```text
0:00 ----------------------------- END
```

For example:

```text
Original song = 05:18.000
8D master    = 05:18.000
```

There is no manual hook at this stage.

## 35.2 Inputs

The whole-song 8D stage may use:

```text
original song timeline
Demucs vocals
Demucs drums
Demucs bass
Demucs other
LRC line structure
canonical word-level timing
full-song activity map
8D configuration
```

The output of this stage is strictly downstream and will be consumed only by the Reel generator.

## 35.3 Forbidden use of 8D output

`SongName_8D.mp3` must never be used as:

```text
YouTube sync input
MMS input
CTC input
word timing input
LRC parsing input
source matching input
hook detection input
```

Those stages use the original source audio and/or their designated processing artifacts.

## 35.4 Stem treatment

### Vocals

Primary goal: stable lyric intelligibility.

Recommended behavior:

- center/center-weighted;
- controlled stereo width;
- subtle movement only;
- no dramatic left-right jumps.

### Bass

Primary goal: stable low-frequency image and mono compatibility.

Recommended behavior:

- center/mono-compatible;
- avoid excessive low-frequency stereo separation;
- maintain stable phase.

### Drums

Primary goal: rhythmic width and subtle motion.

Recommended behavior:

- preserve stable center impression;
- moderate stereo widening where source material supports it;
- smooth movement rather than random bouncing.

### Other

Primary 8D motion carrier.

Recommended behavior:

- smooth equal-power left/right movement;
- gradual width automation;
- phrase/section-aware movement;
- controlled stereo depth.

## 35.5 Advanced movement model

The source Phase 3 design calls for smooth sinusoidal/LFO-style motion, gradual width changes, and phase-coherent movement. The unified engine should make those concepts explicit.

Recommended automation hierarchy:

```text
SECTION / PHRASE LEVEL
        ↓
slow spatial trajectory
        ↓
word/line-informed local modulation
        ↓
smooth crossfades / easing
        ↓
stem-specific constraints
```

Avoid frame-to-frame random pan changes.

## 35.6 LRC-aware 8D automation

The LRC and canonical word timeline can be used as **control data for generating the 8D effect**.

Examples:

- verse sections may use moderate movement;
- chorus sections may receive wider motion;
- long instrumental/lyric-free regions may permit greater supporting-stem motion;
- word/line boundaries can be used as smooth automation landmarks.

This does not mean the 8D file becomes an analysis source. The direction is only:

```text
LRC/word timeline + stems -> 8D
```

not:

```text
8D -> LRC/alignment
```

## 35.7 Spatial processing recommendations

The implementation may use:

- equal-power stereo panning;
- M/S width control;
- multiband width constraints;
- controlled high-frequency movement;
- phase-safe stereo processing;
- carefully limited decorrelation where validated;
- smooth automation curves.

Avoid aggressive artificial Haas-delay effects on bass or vocals because they can compromise mono compatibility and lyric intelligibility.

Every advanced effect must be tested for:

- clipping;
- phase instability;
- mono collapse;
- excessive spectral coloration;
- timing drift.

## 35.8 No timing modification

The 8D engine must not:

- pitch shift;
- time stretch;
- speed up;
- slow down;
- move events in time.

The 8D master must preserve exact musical duration.

## 35.9 Loudness/peak control

Apply controlled final gain/peak handling so the spatial processing does not create clipping.

The exact loudness target can remain configurable and should be fixed after listening/measurement tests.

Do not use normalization as a substitute for correct mixing.

## 35.10 Whole-song output validation

Require:

- file exists;
- decodes successfully;
- stereo output;
- finite samples;
- no obvious clipping beyond configured tolerance;
- duration matches source;
- no unexpected sample-rate drift;
- no unintended pitch/speed change;
- output hash recorded.

## 35.11 8D manifest

Create an internal manifest with:

```text
source MP3 hash
stem hashes
Demucs model/version
8D engine version
8D config hash
movement parameters
stem balance parameters
loudness/peak settings
input/output duration
output sample rate
output channels
output hash
```

---

# 36. STAGE 26 — WHOLE-SONG 8D PUBLICATION ARTIFACT

After the 8D master passes its internal validation, retain it as:

```text
work/<song_key>/audio/8d/full_8d_master.*
```

Then, after final song validation and promotion, publish:

```text
songs/final/SongName_8D.mp3
```

The published file is the complete song.

The path and hash are stored in `SongName.json`:

```json
"audio_8d": {
  "status": "ready",
  "path": "songs/final/SongName_8D.mp3",
  "duration_ms": 318000,
  "sha256": "...",
  "usage": "reel_only"
}
```

---

# 37. STAGE 27 — TERMINAL MANUAL HOOK INPUT

The user now selects the Reel segment manually.

## 37.1 Accepted time formats

Support the time forms established in Phase 3:

```text
02:12.92
02:12.920
00:05
01:02:03.500
132.920
```

Internally convert everything to integer milliseconds.

## 37.2 Prompt

```text
============================================================
REEL HOOK SELECTION
============================================================
Song: <title>
Duration: <HH:MM:SS.mmm>

Hook START (MM:SS.xxx):
>

Hook END (MM:SS.xxx):
>
```

## 37.3 Validation

Require:

```text
start_ms >= 0
end_ms > start_ms
end_ms <= actual source duration
```

No Reel-duration maximum is imposed by the unified design. The selected interval is the authority.

## 37.4 Immediate persistence

After validation:

```text
hooks.start_ms
hooks.end_ms
hooks.duration_ms
source_mp3_sha256
input_method = terminal
```

are saved to the database and master working state.

Then Reel processing starts.

---

# 38. STAGE 28 — MAP THE MANUAL HOOK TO YOUTUBE

Use the previously stored song-level offset:

```text
video_start_ms = hook_start_ms + youtube_offset_ms
video_end_ms   = hook_end_ms   + youtube_offset_ms
```

No new audio match is performed.

No per-hook rematching.

No hook modification.

No automatic extension/shortening.

Example:

```text
hook:
02:12.920 -> 03:11.720

stored offset:
+6.840 sec

YouTube visual interval:
02:19.760 -> 03:18.560
```

The local Reel audio interval remains:

```text
02:12.920 -> 03:11.720
```

## 38.1 Video-availability validation

Check:

```text
video_start_ms >= 0
video_end_ms <= YouTube video duration
```

If the mapped interval is unavailable:

```text
VIDEO_INTERVAL_UNAVAILABLE
```

Do not silently alter the hook or use a different timing.

---

# 39. STAGE 29 — VIDEO GUARD BAND AND EXACT TRIM

A small guard band improves extraction safety.

Baseline:

```text
guard_before_ms = 1500
guard_after_ms  = 1500
```

Download/extract the guarded interval around the mapped hook.

Then remove the guard explicitly and produce an exact hook clip.

The final visual clip duration must match the manual hook duration within the configured technical tolerance.

The implementation must not accidentally use the guard-extended duration as the Reel duration.

---

# 40. STAGE 30 — STATIC VERTICAL VIDEO FRAMING

Target:

```text
1080 x 1920
9:16
```

Source video is scaled to approximately:

```text
80% of 1920 = 1536 pixels tall
```

Then centered vertically and horizontally.

The source may be cropped as needed.

No intentional black bars.

No face tracking.

No body tracking.

No pose tracking.

No identity tracking.

No smart-crop subject switching.

The historical Phase 3 plan documents these systems as superseded and removed; the integrated design keeps them removed.

---

# 41. STAGE 31 — FINAL REEL AUDIO: CROP THE WHOLE-SONG 8D MASTER

The Reel audio does **not** run 8D processing again.

Source:

```text
songs/final/SongName_8D.mp3
```

Crop exactly:

```text
hook_start_ms -> hook_end_ms
```

to create:

```text
work/<song_key>/reel/audio_hook.mp3
```

Validate that:

```text
8D hook duration ≈ manual hook duration
```

This operation is an exact crop of the existing full-song 8D product.

---

# 42. STAGE 32 — WORD-LEVEL LYRIC SUBSET FOR THE REEL

Use the canonical word timeline and/or `SongName_wordlevel.lrc` as an export, but do not re-create word timings.

Select lyric lines whose canonical timing overlaps the manual hook interval.

Shift them by:

```text
relative_ms = absolute_ms - hook_start_ms
```

The original absolute timeline remains unchanged in the master data.

The Reel lyric render operates in Reel-relative time.

## 42.1 Line-by-line behavior

At time `t`:

- identify the active line;
- display only that current line;
- previous line disappears when next line begins;
- words inside the active line use their canonical word timings.

## 42.2 Word emphasis

- current word: strongest emphasis;
- already completed words: subtler;
- future words: subtler;
- complete current LRC line remains visible until the next line replaces it.

## 42.3 Position

Center-oriented lyric placement.

## 42.4 Typography

Primary:

```text
Baloo Tammudu 2 ExtraBold
```

Fallback fonts may be used only when the intended font is unavailable.

Ensure Telugu shaping and glyph support are correct.

---

# 43. STAGE 33 — REEL ASSEMBLY

The final Reel combines exactly three synchronized products:

```text
YouTube visual hook
       +
8D audio hook cropped from whole-song 8D
       +
word-level lyric render
```

The final local audio is never YouTube audio.

The YouTube audio was only synchronization evidence.

The visual Reel therefore represents:

```text
LOCAL SONG TIMELINE
        <---- authoritative ---->

            HOOK
              |
      +-------+-------+
      |               |
      v               v
8D audio crop    lyric timing
      |               |
      +-------+-------+
              |
              v
       YouTube visual
       mapped by global offset
```

---

# 44. VIDEO ENCODING

Preferred encoder:

```text
h264_nvenc
```

Fallback:

```text
libx264
```

The renderer should:

1. detect NVENC capability;
2. attempt a conservative supported parameter set;
3. monitor FFmpeg process status;
4. if FFmpeg exits immediately due to encoder/option failure, report the real cause;
5. retry with CPU encoding when permitted.

The frame writer must check that FFmpeg is still alive before continuing to write frames to stdin, avoiding misleading `Invalid argument` errors from a dead pipe.

Do not assume every FFmpeg build supports the same version-specific NVENC flags.

---

# 45. MASTER `SongName.json` STRUCTURE

The JSON is the long-term record for the song.

A recommended integrated top-level structure is:

```json
{
  "schema_version": 1,
  "pipeline_version": "unified-1.0.0",
  "identity": {},
  "playlist": {},
  "song": {},
  "source": {},
  "spotify": {},
  "youtube_video": {},
  "lyrics": {},
  "audio": {},
  "demucs": {},
  "analysis": {},
  "alignment": {},
  "audio_8d": {},
  "hook": {},
  "reel": {},
  "artwork": {},
  "files": {},
  "validation": {},
  "pipeline": {},
  "history": {}
}
```

## 45.1 Preservation rule

Start from the complete original Phase 1/previous JSON object and deep-copy it.

Do not delete unknown keys.

Add or update only controlled unified namespaces.

## 45.2 YouTube section

Must include:

```json
"youtube_video": {
  "video_id": "...",
  "url": "...",
  "title": "...",
  "search_query": "...",
  "search_result_index": 0,
  "offset_ms": 6840,
  "offset_method": "first_30s_global_anchor",
  "offset_score": 0.941,
  "offset_components": {
    "waveform": 0.95,
    "energy": 0.94,
    "peak_valley": 0.91,
    "transition": 0.96
  },
  "max_allowed_offset_ms": 30000
}
```

## 45.3 Alignment section

Include:

```text
input hashes
model name/revision
normalizer revision
chunk configuration
word count
scores
quality state
review reasons
canonical timeline reference
```

## 45.4 8D section

Include:

```text
status
usage = reel_only
source hash
stem hashes
model/version
config hash
parameters
duration
sample rate
channels
output path
output hash
```

## 45.5 Hook section

```json
"hook": {
  "input_method": "terminal",
  "start_ms": 132920,
  "end_ms": 191720,
  "duration_ms": 58800,
  "source_mp3_sha256": "..."
}
```

## 45.6 Final-output section

Include all five song files and their hashes:

```text
song_mp3
source_lrc
wordlevel_lrc
metadata_json
full_song_8d
```

Also include Reel outputs separately.

---

# 46. FINAL SONG PACKAGE VALIDATION

Before promotion to `songs/final/`, validate the complete package as a coherent set.

## 46.1 File checks

Require:

```text
Song.mp3 exists
Song.lrc exists
Song_wordlevel.lrc exists
Song.json exists
Song_8D.mp3 exists
```

## 46.2 Cross-file checks

Validate:

- same basename identity;
- source hash references are consistent;
- JSON points to all final artifacts;
- actual MP3 duration matches metadata;
- source LRC belongs to source song;
- word-level LRC is based on the same canonical timeline in JSON;
- whole-song 8D duration matches source duration;
- hook references lie inside source duration;
- Reel hook audio references the correct 8D master hash;
- YouTube mapped interval matches stored offset and hook values.

## 46.3 Source protection

Verify that protected source files remain unchanged.

---

# 47. FINAL REEL VALIDATION

Require:

- final MP4 exists;
- MP4 decodes;
- resolution `1080x1920`;
- aspect ratio 9:16;
- duration matches hook duration within configured tolerance;
- audio is stereo;
- video codec is valid H.264;
- audio codec is valid AAC or configured target;
- lyric overlay is present where expected;
- output JSON exists and is valid;
- `overall=true`;
- `failed_checks=[]` for a fully accepted Reel.

Record:

```text
source song hash
8D master hash
hook start/end
YouTube video ID
YouTube offset
mapped visual interval
renderer version
encoder
resolution
FPS
output hash
```

---

# 48. FINALIZATION AND ATOMIC PROMOTION

No published `songs/final` files are created as “final” after Phase 1 alone.

All outputs remain under the song work directory until the complete final package is ready.

## 48.1 Promotion sequence

Recommended:

```text
1. finish all stages
2. validate full song package
3. validate final Reel
4. assemble final JSON
5. write/stage final MP3
6. stage source LRC
7. stage word-level LRC
8. stage whole-song 8D MP3
9. stage Reel MP4
10. stage Reel JSON
11. hash final staged artifacts
12. perform final consistency check
13. atomically promote song files
14. atomically promote Reel files
15. update DB transaction
16. mark FINALIZED
17. clean successful work directory
```

The implementation may use versioned staging directories and rename operations when Windows filesystem behavior makes multi-file atomicity impossible to guarantee as one physical transaction.

## 48.2 Database ordering

Never set `FINALIZED` before output validation.

Never delete an existing retained package before the replacement package has passed validation.

---

# 49. DEPENDENCY-AWARE CACHE AND RESUMABILITY

This is a central optimization.

## 49.1 Stage fingerprints

Each reusable stage gets an input fingerprint appropriate to that stage.

### Acquisition fingerprint

```text
playlist source identity
YT Music item identity
acquisition config
```

### YouTube offset fingerprint

```text
source MP3 hash
selected YouTube video ID
reference duration
sync algorithm version
sync configuration
```

### LRC parse fingerprint

```text
source LRC hash
LRC parser version
```

### Demucs fingerprint

```text
source MP3 hash
Demucs model/version/config
```

### Alignment fingerprint

```text
source MP3 hash
source LRC hash
Demucs stem hashes
MMS model/version
normalizer version
chunk config
CTC implementation version
alignment config
```

### Whole-song 8D fingerprint

```text
source MP3 hash
Demucs stem hashes
canonical word timeline hash
LRC hash
8D configuration hash
8D engine version
```

### Reel fingerprint

```text
selected YouTube video ID
youtube offset
hook start/end
8D master hash
word timeline hash
renderer version
video configuration
```

## 49.2 Examples

### Only hook changes

Re-run:

```text
hook validation
YouTube interval mapping
video extraction
8D crop
lyric subset/render
Reel
```

Do not rerun:

```text
acquisition
Spotify
YouTube video search
YouTube song offset
LRC retrieval
Demucs
MMS
CTC
whole-song 8D
```

### LRC changes

Invalidate:

```text
LRC parse
alignment
word-level LRC
whole-song 8D if its automation uses the timeline
Reel
```

Retain where valid:

```text
acquisition
metadata
Spotify
YouTube video
YouTube offset
Demucs
```

### 8D config changes

Invalidate:

```text
whole-song 8D
Reel
```

Retain:

```text
alignment
word-level LRC
YouTube offset
```

### YouTube video changes

Invalidate:

```text
YouTube reference audio
offset
video extraction
Reel
```

Retain:

```text
Demucs
alignment
word-level LRC
whole-song 8D
```

### Source MP3 changes

Invalidate all downstream artifacts.

---

# 50. RESUME RULES

A stopped run continues from the last completed safe stage.

The system should use both:

```text
database state
+
per-stage artifact manifests
```

A stage is reusable only when its expected output exists, validates, and its dependency fingerprint matches the stored fingerprint.

Do not treat the mere existence of a file as proof that the stage completed correctly.

For a failed song, preserve enough temporary artifacts for diagnosis according to the retention policy.

On successful finalization, temporary processing artifacts may be deleted.

---

# 51. ERROR TAXONOMY

The unified error codes should identify the true subsystem.

## Inventory / source

```text
MP3_MISSING
LRC_MISSING
JSON_MISSING
JSON_INVALID
SOURCE_MP3_INVALID
SOURCE_HASH_FAILED
SOURCE_DURATION_FAILED
```

## Acquisition

```text
ACQUISITION_FAILED
YT_DLP_FAILED
ARTWORK_ACQUISITION_FAILED
```

## Metadata/catalog

```text
METADATA_PARSE_FAILED
SPOTIFY_AUTH_FAILED
SPOTIFY_SEARCH_FAILED
SPOTIFY_MATCH_FAILED
ISRC_INVALID
DUPLICATE_REVIEW_REQUIRED
```

## YouTube

```text
YOUTUBE_VIDEO_NOT_FOUND
YOUTUBE_REFERENCE_AUDIO_FAILED
YOUTUBE_OFFSET_FAILED
YOUTUBE_OFFSET_NO_VALID_CANDIDATE
YOUTUBE_OFFSET_LIMIT_EXCEEDED
YOUTUBE_INTERVAL_UNAVAILABLE
```

`YOUTUBE_OFFSET_LIMIT_EXCEEDED` should be used when no valid candidate remains within 30 seconds; it should not be used merely because the highest-scoring rejected candidate exceeded the limit while a lower-scoring valid candidate was successfully selected.

## Lyrics

```text
LRCLIB_REQUEST_FAILED
NO_SYNCED_LRC
LRC_PARSE_FAILED
LRC_INVALID
```

`NO_SYNCED_LRC` is a **terminal skip condition**, not a generic failure.

## Audio

```text
AUDIO_DECODE_FAILED
AUDIO_DURATION_MISMATCH
DEMUCS_LOAD_FAILED
DEMUCS_INFERENCE_FAILED
DEMUCS_OOM
STEM_INVALID
VAD_FAILED
ACTIVITY_ANALYSIS_FAILED
```

## Alignment

```text
NORMALIZATION_FAILED
TOKENIZATION_FAILED
MMS_LOAD_FAILED
MMS_INFERENCE_FAILED
MMS_OOM
CTC_ALIGNMENT_FAILED
CHUNK_LOW_CONFIDENCE
CHUNK_CHRONOLOGY_FAILED
OVERLAP_CONFLICT
GLOBAL_CHRONOLOGY_FAILED
BLANK_MARKER_START_VIOLATION
ANCHOR_DRIFT
ALIGNMENT_NEEDS_REVIEW
```

## 8D

```text
AUDIO_8D_FAILED
AUDIO_8D_CLIPPING
AUDIO_8D_PHASE_FAILED
AUDIO_8D_DURATION_MISMATCH
AUDIO_8D_VALIDATION_FAILED
```

## Hook

```text
HOOK_REQUIRED
HOOK_PARSE_FAILED
HOOK_OUT_OF_RANGE
HOOK_INVALID
```

## Video/render

```text
VIDEO_DOWNLOAD_FAILED
VIDEO_TRIM_FAILED
LYRIC_RENDER_FAILED
VIDEO_RENDER_FAILED
REEL_ASSEMBLY_FAILED
REEL_VALIDATION_FAILED
```

## Finalization

```text
JSON_WRITE_FAILED
MP3_METADATA_FAILED
FINAL_VALIDATION_FAILED
FINAL_PROMOTION_FAILED
SOURCE_MODIFIED
```

---

# 52. WINDOWS-SPECIFIC REQUIREMENTS

The entire project is designed to run correctly on Windows.

## 52.1 UTF-8 subprocess handling

Always explicitly handle FFmpeg/FFprobe/yt-dlp subprocess output as UTF-8 with replacement/error tolerance.

## 52.2 Unicode filenames

Song names and artist names may contain Telugu and other non-ASCII characters.

Use:

```python
encoding="utf-8"
errors="replace"
```

for text output, and:

```python
json.dump(..., ensure_ascii=False)
```

for JSON.

## 52.3 Paths

Normalize config paths to `Path` objects before calling `.as_posix()` or similar methods.

Never assume a configuration value is already a `Path`.

## 52.4 Temporary WAV files

Do not give FFmpeg a file ending in `.tmp` and expect it to infer WAV format. Use `.wav` suffix or `-f wav`.

## 52.5 FFmpeg stdin

Check process liveness before writing frames to a live encoder process.

## 52.6 File handles

Use deterministic context management. Close FFmpeg/decoder processes and file handles before promotion/cleanup.

---

# 53. SECURITY / CREDENTIALS

- Spotify credentials remain outside source control.
- Prefer environment variables.
- Never log Spotify secrets.
- Never log cookie contents.
- Never place credentials/tokens in source JSON, final song JSON, or Reel JSON.
- Cookies remain local and ignored by version control.
- HTTP requests use configured timeouts.
- Network failures are classified without leaking credentials.

---

# 54. LOGGING

Every song should produce clear stage-oriented logs.

Recommended important lines:

```text
PLAYLIST
SOURCE ACQUISITION
SOURCE VALIDATION
METADATA
SPOTIFY
ISRC
DUPLICATE
YOUTUBE VIDEO SELECTED
YOUTUBE 30s OFFSET MATCH
YOUTUBE OFFSET SAVED
LRCLIB
SYNCED LRC ACCEPTED / SKIPPED
DEMUCS DEVICE
ALIGNMENT
WORD-LEVEL LRC
WHOLE-SONG 8D
8D VALIDATED
HOOK REQUIRED
HOOK ENTERED
YOUTUBE HOOK INTERVAL
VIDEO TRIM
8D HOOK CROP
LYRIC RENDER
REEL ENCODER
REEL VALIDATION
FINAL PACKAGE VALIDATION
PROMOTION
FINALIZED
```

For errors include:

```text
stage
error code
song identity
artifact path
input fingerprint where useful
exact failed check
```

Example:

```text
ERROR YOUTUBE_OFFSET_NO_VALID_CANDIDATE
song=001_Gelupu...
max_offset_ms=30000
best_valid_candidate=none
highest_candidate_offset_ms=47300
highest_candidate_score=0.962
```

---

# 55. FINAL MP3 / JSON RESPONSIBILITY MATRIX

| Information | MP3 | Master JSON | Database | Reel JSON |
|---|---:|---:|---:|---:|
| Title/artist/album | Yes | Yes | Yes | Reference |
| Playlist serial | Yes, concise | Yes | Yes | Yes |
| Source hash | optional concise | Yes | Yes | Yes |
| Spotify IDs | Yes, concise | Yes | Yes | Reference |
| YouTube video ID | Yes | Yes | Yes | Yes |
| YouTube offset | **Yes** | **Yes** | **Yes** | Yes |
| Offset score/components | concise optional | **Yes** | summary | Yes |
| Source LRC | external file | path/provenance | path | reference |
| Canonical word timing | SYLT | **Yes** | **Yes** | reference |
| Word-level LRC | external file | path/provenance | path | reference |
| Demucs model/stems | no raw details | **Yes** | summary | Yes |
| Whole-song 8D | output file | **Yes** | path/hash | **source for Reel** |
| Hook | no requirement | **Yes** | **Yes** | **Yes** |
| Reel output | no | **Yes** | **Yes** | **Yes** |
| Raw API blobs | No | Yes | selected/summary | No |
| Credentials | No | **Never** | **Never** | **Never** |

---

# 56. FINAL FILE PACKAGE CONTRACT

## Successful song

```text
songs/final/
├── SongName.mp3
├── SongName.lrc
├── SongName_wordlevel.lrc
├── SongName.json
└── SongName_8D.mp3
```

## Successful Reel

```text
reels/generated/
├── SongName_reel.mp4
└── SongName_reel.json
```

## Skipped no-synced-LRC song

Recommended:

```text
songs/skipped/no_synced_lrc/
├── SongName.mp3
└── SongName.json
```

No `.lrc`, `SongName_wordlevel.lrc`, `SongName_8D.mp3`, or Reel is produced for this terminal skip.

---

# 57. COMMAND-LINE INTERFACE

Recommended unified CLI:

```text
python main.py
```

Normal behavior:

- ingest/synchronize playlist;
- process next actionable song;
- prompt for hook at `HOOK_REQUIRED`;
- finish song;
- continue to next actionable song.

Useful operational commands:

```text
python main.py --doctor
python main.py --status
python main.py --ingest-only
python main.py --single 001
python main.py --retry-errors
python main.py --resume
python main.py --reconcile
python main.py --force-rebuild <stage> <song>
python main.py --reset-skipped <song>
```

No command should silently undo the terminal-skip rule. Reopening a skipped song must be deliberate.

`--force-rebuild` should invalidate only the requested stage and its downstream dependents whenever possible.

---

# 58. TESTING STRATEGY

Testing should be deterministic and offline whenever possible by mocking network boundaries.

## 58.1 Playlist/inventory tests

- normal playlist ingestion;
- returned-order preservation;
- permanent serial assignment;
- repeated occurrences;
- unavailable entry;
- serial reuse after availability returns;
- state reconciliation.

## 58.2 Acquisition tests

- yt-dlp command contract;
- exactly one initial acquisition;
- no `--add-metadata`;
- info JSON preservation;
- thumbnail availability;
- source validation.

## 58.3 Metadata tests

- normalization;
- source/Spotify precedence;
- duration authority;
- ISRC normalization.

## 58.4 Duplicate tests

- valid ISRC duplicate;
- missing ISRC;
- invalid ISRC;
- keep previous;
- keep current replacement safety.

## 58.5 YouTube discovery tests

- exact query;
- case-insensitive `lyrics` filtering;
- first accepted result;
- all-results-lyrics case;
- selected-video metadata preservation.

## 58.6 YouTube 30-second synchronization tests

Synthetic audio with known offset:

```text
local reference starts at YouTube +5.2 sec
```

Verify recovered offset.

Synthetic high/low pattern test.

Candidate-ranking test:

```text
candidate A = higher raw score but offset > 30 sec
candidate B = slightly lower score but offset < 30 sec
```

Verify B is selected.

Tie/near-best test verifies earliest valid candidate preference.

No-valid-candidate test verifies explicit failure/review.

Verify offset stored identically in DB/JSON/MP3 metadata.

## 58.7 LRC tests

- ordinary line timestamps;
- inline word timestamps;
- blank markers;
- duplicate lyric lines;
- chronology;
- punctuation;
- mixed Latin/Telugu;
- number policy;
- missing word timings.

## 58.8 Demucs/stem tests

- model loads;
- expected stems exist;
- device selection;
- CUDA OOM fallback;
- duration/sample-count alignment;
- cache reuse.

## 58.9 MMS/CTC tests

- Telugu adapter load;
- tokenization;
- repeated labels;
- synthetic CTC alignment;
- token-to-word grouping;
- intra-chunk chronology;
- inter-chunk chronology;
- immutable merge behavior;
- blank-marker rules;
- interpolation flags;
- anchor drift validation.

## 58.10 Whole-song 8D tests

Verify:

```text
input duration = output duration
output is stereo
all stems contribute according to configuration
vocals remain intelligible/centered
bass remains center-compatible
drums move smoothly
other carries primary movement
no pitch/speed alteration
no unbounded clipping
```

Also verify the 8D master is not accidentally used by any upstream alignment/matching function.

## 58.11 Hook tests

- terminal prompt;
- accepted time formats;
- invalid start;
- end <= start;
- end beyond duration;
- persistence after entry;
- restart without re-prompt.

## 58.12 Reel tests

- exact video duration;
- exact 8D crop duration;
- lyrics correctly shifted into hook-relative time;
- only current line visible;
- current word emphasis;
- 1080x1920;
- 80% video panel;
- NVENC path;
- CPU fallback;
- dead encoder pipe handling.

## 58.13 Finalization tests

- five-file final package;
- Reel package;
- JSON preservation;
- MP3 metadata preservation;
- hashes;
- atomic promotion;
- DB transaction;
- source immutability.

## 58.14 Failure injection

Inject failures at:

```text
playlist
acquisition
metadata
Spotify
YouTube search
YouTube reference audio
offset match
LRCLIB
Demucs
MMS
CTC
merge
8D
hook input
video download
video trim
lyric render
encoder
final validation
promotion
DB commit
```

Verify the state machine resumes safely.

---

# 59. PERFORMANCE OPTIMIZATIONS

## 59.1 Share expensive artifacts

The largest shared artifacts are:

```text
original decoded PCM
Demucs stems
LRC parse
canonical word timeline
whole-song 8D
YouTube reference audio
YouTube global offset
```

## 59.2 One-pass expensive operations

One song should normally require:

```text
1 source acquisition
1 source decode
1 Demucs separation
1 YouTube reference extraction
1 global YouTube offset search
1 canonical alignment pass with chunk retries
1 whole-song 8D render
1 manual hook entry
1 final 8D crop for Reel audio
1 video extraction
1 Reel render
```

There should be no second Demucs pass just for Reel audio.

There should be no second 8D render for the hook.

There should be no second YouTube match for the hook.

## 59.3 Model cache

Cache MMS/Demucs/VAD models globally, outside per-song work directories.

## 59.4 GPU discipline

One GPU worker by default on a constrained GPU.

Do not trade correctness for concurrency.

## 59.5 Coarse-to-fine YouTube offset scan

Use cheaper low-rate features to reject most candidate windows before high-resolution scoring.

## 59.6 Parallelism policy

CPU-only tasks may be parallelized carefully, but expensive GPU model inference remains serialized by default.

The one-song logical sequence remains the authoritative state model even if internal CPU preprocessing overlaps with non-dependent work.

---

# 60. QUALITY REVIEW WORKFLOW

The project should produce a review queue rather than hiding uncertain output.

Review reasons may include:

```text
low alignment confidence
high anchor drift
blank-marker start violation
many interpolated words
YouTube offset weak but valid
whole-song 8D phase warning
video interval near duration boundary
lyric shaping issue
```

A human review command can inspect:

```text
python main.py --review 001
```

The review screen/log should show:

```text
song metadata
source duration
selected YouTube video
offset + score
LRC stats
alignment quality
8D status
hook start/end
mapped video interval
Reel validation
```

---

# 61. ROLLOUT PLAN

Do not start with the entire playlist.

Recommended progression:

```text
1 sample song
     ↓
5-song pilot
     ↓
25 songs
     ↓
100 songs
     ↓
500 songs
     ↓
full corpus
```

At each gate verify:

- no source mutation;
- metadata preserved;
- JSON preserved;
- YouTube offset correctness;
- no-synced-LRC skip behavior;
- word-level timing quality;
- whole-song 8D quality;
- hook/Reel sync;
- final validation;
- resume/idempotency;
- GPU stability;
- model cache reuse.

---

# 62. TROUBLESHOOTING PRIORITY

When output is wrong, inspect in this order:

```text
1. source MP3/LRC pairing
2. source duration
3. source audio decode
4. Demucs stem quality
5. LRC reference content
6. normalization/reference mapping
7. chunk boundaries
8. MMS language adapter
9. CTC path
10. token-to-word mapping
11. overlap merge
12. global chronology
13. whole-song 8D processing
14. YouTube offset
15. hook mapping
16. video trim
17. lyric render
18. final assembly
```

Do not immediately blame the model if the source reference or chunking is wrong.

---

# 63. EXPLICITLY RETAINED HISTORICAL SAFEGUARDS

The integration keeps the lessons from the field-tested histories.

## 63.1 Windows FFmpeg `.tmp` output-format failure

Never rely on `.tmp` to imply WAV.

## 63.2 CPU-only PyTorch

Doctor must verify actual CUDA execution capability.

## 63.3 SYLT chronology failure

Global chronology must be solved before SYLT export.

## 63.4 LRC chronology failure

Chronology must be a canonical alignment property, not a serializer repair.

## 63.5 Blank marker over-strictness

A word ending slightly after a structural blank marker is not automatically invalid when its start is correctly before the marker.

## 63.6 Same-chunk backward timestamps

Validate chronology within chunks as well as across chunks.

## 63.7 Mutable merge state

Use immutable candidate inputs for merge/retry passes.

## 63.8 Smart Crop failure

Do not resurrect face/pose/identity tracking.

## 63.9 Bad whole-track synchronization strategy

Do not resurrect complex DTW/global whole-track matching that produced obviously wrong large offsets. Use the controlled 30-second anchor strategy.

## 63.10 Duration cap history

Do not reintroduce a 30-second or 40-second Reel maximum. Manual hook duration is authoritative.

## 63.11 Lyric display history

Use one line at a time with current-word emphasis, not a persistent multi-line context block.

---

# 64. OUT-OF-SCOPE / NOT TO BE REINTRODUCED

Unless a future revision explicitly changes the specification, do not add:

```text
automatic hook selection
hook ranking
hook recommendation
hook shortening/extension
Smart Crop
face tracking
pose tracking
identity tracking
per-hook YouTube rematching
DTW-based hook search
complex YouTube candidate scoring for discovery
ASR-generated replacement lyrics
Whisper as the primary lyric transcript source
YouTube audio as final Reel audio
Instagram automatic publishing
```

The YouTube **offset matcher itself** does have a combined matching score because that is now explicitly requested for the 30-second synchronization step. This score is for synchronization candidates, not for selecting among YouTube search results.

---

# 65. SOURCE-CONTRACT TO INTEGRATED-CONTRACT MAPPING

## Phase 1 retained contracts

- playlist order is authoritative;
- permanent serials;
- repeated occurrences preserved;
- unavailable source handling;
- one initial yt-dlp acquisition;
- source metadata and raw JSON preservation;
- Spotify optional enrichment;
- ISRC-only duplicate identity;
- YouTube search query and first acceptable result;
- LRCLIB `/api/get` only;
- synchronized lyrics only;
- artwork precedence and preservation;
- concise final MP3 metadata;
- detailed sidecar JSON;
- validation before completion;
- hashing;
- atomic promotion;
- recovery/retry.

## Phase 2 retained contracts

- exact basename contracts;
- source immutability;
- full JSON preservation;
- MP3 metadata preservation;
- LRC as lyric/reference text;
- LRC timestamps as coarse anchors;
- Demucs vocal isolation;
- VAD/activity as supporting evidence;
- reversible Telugu normalization;
- MMS Telugu adapter;
- frame-level emissions;
- real CTC reference forced alignment;
- start/end word spans;
- integer millisecond canonical time;
- overlap deduplication;
- intra- and inter-chunk chronology validation;
- blank-marker semantics;
- immutable merge state;
- final LRC export from canonical alignment;
- dedicated SYLT;
- quality versus pipeline state;
- model cache;
- GPU-first policy;
- resume/idempotency;
- data lineage and provenance.

## Phase 3 retained contracts

- manual hook rather than automatic selection;
- no Reel duration cap;
- global YouTube offset rather than hook-specific rematching;
- 1080x1920;
- 9:16;
- 80% video panel;
- static centered framing;
- local original song as final audio source;
- stem-based spatial processing;
- line-by-line lyrics;
- current-word highlighting;
- centered lyrics;
- Baloo Tammudu 2 ExtraBold target;
- NVENC-first;
- CPU fallback;
- final Reel validation;
- independent Reel provenance.

## Integrated replacements

The following standalone mechanics are intentionally changed:

```text
hook_timeline.json
        -> removed; terminal hook input

15s YouTube anchor
        -> 30s YouTube anchor

best candidate regardless of offset
        -> highest-scoring valid candidate with offset <= 30 sec

Phase 3 hook-only 8D
        -> whole-song advanced 8D master

8D generated after/inside hook stage
        -> 8D generated before hook prompt and before Reel

Phase 2/3 separate final packages
        -> one unified five-file final song package

published songs/final after an intermediate phase
        -> publish only after whole song final validation

three separate project databases
        -> one unified database with logical tables
```

---

# 66. COMPLETE OPERATIONAL PIPELINE — FINAL LOCKED VERSION

This is the pipeline to implement.

```text
START
 |
 v
DOCTOR / ENVIRONMENT CHECK
 |
 v
INGEST / SYNCHRONIZE PLAYLIST
 |
 v
SELECT NEXT ACTIONABLE PLAYLIST OCCURRENCE
 |
 v
CREATE / RECOVER SONG WORKSPACE
 |
 v
ONE YT-DLP SOURCE ACQUISITION
 |
 v
SOURCE PROBE + SOURCE HASH
 |
 v
METADATA NORMALIZATION
 |
 v
OPTIONAL SPOTIFY ENRICHMENT
 |
 v
CANONICAL ISRC
 |
 v
ISRC DUPLICATE GATE
 |                 \
 |                  \-- DUPLICATE KEEP PREVIOUS -> TERMINAL -> NEXT SONG
 |
 v
SELECT FIRST ACCEPTABLE YOUTUBE VIDEO
 |
 v
EXTRACT YOUTUBE REFERENCE AUDIO
 |
 v
30-SECOND ORIGINAL-SONG GLOBAL MATCH
 |
 +--> scan complete YouTube timeline
 |
 +--> waveform comparison
 |
 +--> energy/high-low comparison
 |
 +--> peaks/valleys
 |
 +--> transition structure
 |
 +--> combined score
 |
 +--> reject offsets > 30 sec
 |
 +--> choose highest valid candidate
 |
 v
SAVE YOUTUBE VIDEO + OFFSET
 |
 v
LRCLIB GET /api/get
 |
 +-------------------------------+
 |                               |
 v                               v
NO SYNCED LRC                    SYNCED LRC
 |                               |
 v                               v
SKIPPED_NO_SYNCED_LRC        PARSE LRC ONCE
 |                               |
 v                               v
SAVE SKIP METADATA              DECODE ORIGINAL AUDIO ONCE
TERMINAL                         |
 |                               v
 |                             DEMUCS ONCE
 |                               |
 |               +---------------+---------------+
 |               |                               |
 |               v                               v
 |           ALIGNMENT                        ANALYSIS
 |               |                               |
 |            VAD + energy                       |
 |            normalization                      |
 |            LRC anchors                        |
 |            chunking                           |
 |            MMS                                |
 |            CTC                                |
 |            token->word                       |
 |            overlap merge                     |
 |            chronology                        |
 |            quality                            |
 |               |                               |
 |               v                               |
 |        CANONICAL WORD TIMELINE               |
 |               |                               |
 |               v                               |
 |       SongName_wordlevel.lrc                  |
 |               |                               |
 |               +---------------+---------------+
 |                               |
 |                               v
 |                     WHOLE-SONG ADVANCED 8D
 |                               |
 |                        0:00 -> END
 |                               |
 |                     SongName_8D.mp3
 |                               |
 |                               v
 |                         TERMINAL PROMPT
 |                               |
 |                     Enter hook START
 |                     Enter hook END
 |                               |
 |                               v
 |                         VALIDATE HOOK
 |                               |
 |                               v
 |                  MAP HOOK USING SAVED OFFSET
 |                               |
 |                               v
 |                     EXTRACT YOUTUBE CLIP
 |                               |
 |                               v
 |                  CROP HOOK FROM FULL-SONG 8D
 |                               |
 |                               v
 |                    SELECT LYRIC SUBSET
 |                               |
 |                               v
 |                  LINE/WORD LYRIC RENDER
 |                               |
 |                               v
 |                    ASSEMBLE FINAL REEL
 |                               |
 |                               v
 |                     VALIDATE FINAL REEL
 |                               |
 |                               v
 |                 VALIDATE FIVE-FILE SONG PACKAGE
 |                               |
 |                               v
 |                       FINAL JSON ASSEMBLY
 |                               |
 |                               v
 |                       HASH FINAL ARTIFACTS
 |                               |
 |                               v
 |                        ATOMIC PROMOTION
 |                               |
 |                               v
 |                         DB TRANSACTION
 |                               |
 |                               v
 |                           FINALIZED
 |                               |
 +-------------------------------+
                                 |
                                 v
                              NEXT SONG
```

---

# 67. FINAL IMPLEMENTATION CHECKLIST

## Project architecture

- [ ] One unified orchestrator
- [ ] Phase engines modular
- [ ] No hidden cross-phase imports
- [ ] One unified database
- [ ] Source immutability preserved
- [ ] One song processed end-to-end

## Playlist / acquisition

- [ ] YTMusic order preserved
- [ ] permanent serials
- [ ] repeated entries preserved
- [ ] unavailable entries safe
- [ ] one source acquisition
- [ ] source metadata preserved
- [ ] no `--add-metadata`

## Metadata / Spotify / duplicates

- [ ] metadata precedence locked
- [ ] Spotify optional
- [ ] Spotify artwork byte-preserving
- [ ] canonical ISRC
- [ ] ISRC-only duplicate detection
- [ ] replacement-safe duplicate handling

## YouTube synchronization

- [ ] first acceptable visual video
- [ ] reference audio acquired
- [ ] original first 30 seconds used
- [ ] waveform matching
- [ ] energy matching
- [ ] high/low structure
- [ ] peak/valley structure
- [ ] combined candidate score
- [ ] hard 30-second offset limit
- [ ] reject top candidate when >30 sec
- [ ] select next-best valid candidate
- [ ] early/near-best tie behavior
- [ ] store offset in DB
- [ ] store offset in JSON
- [ ] store offset in MP3 metadata
- [ ] no hook rematching

## Lyrics / Phase 2

- [ ] LRCLIB `/api/get` only
- [ ] no synced LRC -> terminal skip
- [ ] skip not retried automatically
- [ ] LRC parsed once
- [ ] source wording preserved
- [ ] 16 kHz mono lossless alignment audio
- [ ] Demucs once
- [ ] VAD/activity
- [ ] reversible normalization
- [ ] reference-aware chunking
- [ ] MMS Telugu adapter
- [ ] true CTC alignment
- [ ] token->word mapping
- [ ] word start/end/score/source
- [ ] immutable merge
- [ ] overlap dedup
- [ ] global chronology
- [ ] blank-marker semantics
- [ ] canonical word timeline
- [ ] word-level LRC export
- [ ] dedicated SYLT

## Whole-song 8D

- [ ] complete original song processed
- [ ] vocals/drums/bass/other stems
- [ ] advanced spatial automation
- [ ] LRC/word timeline may guide movement
- [ ] no timing change
- [ ] no pitch shift
- [ ] no speed change
- [ ] mono compatibility check
- [ ] clipping/peak check
- [ ] output duration equals source
- [ ] `SongName_8D.mp3`
- [ ] 8D used only by Reel stage
- [ ] no second hook 8D render

## Hook / Reel

- [ ] no `hook_timeline.json`
- [ ] terminal prompt
- [ ] start/end manually entered
- [ ] integer millisecond persistence
- [ ] restart resumes saved hook
- [ ] no Reel duration cap
- [ ] hook maps via stored offset
- [ ] exact video duration
- [ ] guard-band handling
- [ ] 1080x1920
- [ ] 80% panel
- [ ] static centered framing
- [ ] Baloo Tammudu 2 ExtraBold
- [ ] line-by-line lyrics
- [ ] current-word emphasis
- [ ] crop audio from full-song 8D
- [ ] NVENC first
- [ ] CPU fallback
- [ ] final Reel validation

## Final package

- [ ] `Song.mp3`
- [ ] `Song.lrc`
- [ ] `Song_wordlevel.lrc`
- [ ] `Song.json`
- [ ] `Song_8D.mp3`
- [ ] Reel MP4
- [ ] Reel JSON
- [ ] all hashes recorded
- [ ] final DB transaction
- [ ] terminal/final status correct
- [ ] temp cleaned after success

---

# 68. FINAL DECISION SUMMARY

The unified project is now conceptually:

```text
YouTube Music playlist
        |
        v
one song
        |
        +--> acquire + metadata + Spotify + ISRC
        |
        +--> select YouTube visual video
        |
        +--> calculate 30s-based global song offset
        |       (offset must be <= 30 sec)
        |
        +--> obtain synced LRC
        |       |
        |       +--> no synced LRC -> terminal SKIPPED
        |       |
        |       `--> synced LRC -> continue
        |
        +--> Demucs once
        |
        +--> MMS + true CTC word alignment
        |
        +--> final word-level LRC
        |
        +--> advanced 8D for the WHOLE SONG
        |       |
        |       `--> Song_8D.mp3
        |
        +--> terminal prompt for hook start/end
        |
        +--> crop Reel audio from whole-song 8D
        |
        +--> map hook to YouTube using stored offset
        |
        +--> render video + lyrics
        |
        +--> validate everything
        |
        `--> publish final package
```

The five-file final song package is:

```text
Song.mp3
Song.lrc
Song_wordlevel.lrc
Song.json
Song_8D.mp3
```

The central optimization principle is:

> **Build the complete song intelligence and the complete whole-song 8D product once, cache everything by dependency identity, ask the user for the hook only when the song is ready, and use that stored hook to assemble the final Reel without repeating expensive upstream work.**

---

# 69. ARCHIVAL SOURCE COVERAGE

The three original project specifications are included verbatim after this integrated plan. This is intentional: it preserves their full historical detail, source terminology, implementation notes, test matrices, appendices, and superseded approaches so that this unified specification does not lose information merely because some architecture was changed.

For maintenance purposes:

- the integrated sections above are the active unified design;
- the archived documents below are source/reference material;
- conflicts are resolved according to the integrated decisions in Section 2;
- no archived requirement is considered silently deleted merely because it is not active in the unified runtime.

---

# ARCHIVE A: ORIGINAL PHASE 1 SPECIFICATION

The following document is retained verbatim as supplied.

# PHASE 1 — YouTube Music Playlist Downloader & Enricher

## Complete Ultra-Detailed Project Plan — Current Authoritative Build

**Status:** Current consolidated implementation plan

**Purpose:** This document consolidates the original Phase 1 specification and every subsequent project change discussed during implementation. The **Current Authoritative Specification** sections describe what the implementation is intended to do now. The **Historical Original Specification** appendix preserves the original planning document verbatim so no original requirement is lost. Where later requirements changed an earlier rule, the current rule explicitly takes precedence and the superseded rule is identified.

**Generated:** 2026-09-27

---

# 1. Executive Summary

This project is a standalone Python pipeline that ingests one YouTube Music playlist, assigns every playlist occurrence a permanent serial number, downloads usable YT Music source audio, enriches the song with optional Spotify catalog information, locates one YouTube music-video result using the project's deterministic search rule, obtains only synchronized lyrics from LRCLIB through `GET /api/get`, selects high-quality artwork, writes a concise and player-compatible final ID3 metadata set, embeds synchronized lyrics, creates a same-basename detailed JSON sidecar, validates and hashes the final MP3, and commits the resulting retained-song record into SQLite.

The architecture deliberately separates:

1. **Playlist identity** — permanent serial + playlist position in `playlist.db`.
2. **Retained song identity** — song row in `songs.db`, with **ISRC as the only duplicate identifier** when an ISRC exists.
3. **Source metadata** — detailed YT Music/yt-dlp information.
4. **Catalog enrichment** — optional Spotify information and Spotify artwork.
5. **Video enrichment** — selected YouTube music-video ID/title/URL and core facts.
6. **Lyrics enrichment** — LRCLIB synchronized lyrics only.
7. **Player-facing MP3 metadata** — concise standard ID3 + durable custom identifiers + artwork + lyrics.
8. **Full detailed provenance** — same-basename JSON sidecar containing detailed source/API fields, raw JSON, selection information, artwork provenance, lyric response/matching details, and file hashes.

The current implementation intentionally does **not** dump raw source descriptions, age-limit information, channel internals, downloader internals, or raw API blobs into the main MP3 metadata. Those details belong in the JSON sidecar.

---

# 2. Current Authoritative Rules

These rules are locked for the current build unless explicitly changed in a future revision.

## 2.1 Playlist identity

- Every playlist occurrence receives one permanent integer serial number.
- Serial numbers are assigned according to the order returned by YTMusic playlist ingestion.
- Serial numbers never change.
- Serial numbers are never reused.
- Repeated tracks in the playlist remain separate playlist occurrences.
- The permanent serial is the stable link between a playlist occurrence and its retained file/record.

## 2.2 Playlist ingestion

- The complete playlist is read through `ytmusicapi`.
- Returned playlist order is authoritative.
- No alphabetical, artist, duration, popularity, ISRC, or other sorting is performed.
- An unavailable item (`videoId=None`, `isAvailable=False`) must not crash ingestion.
- An unavailable entry is preserved with its serial and position and recorded as `error` until it can be retried.
- When a previously unavailable occurrence later becomes usable, the existing serial is reused rather than assigning a new serial.

## 2.3 Source download

- The actual song audio comes from the YT Music source associated with the playlist occurrence.
- One complete initial `yt-dlp` acquisition is used for audio, source `info.json`, and thumbnails.
- `--add-metadata` is not used by the initial acquisition.
- FFmpeg/FFprobe are required.
- A supported JavaScript runtime may be required by the installed yt-dlp version for some YouTube extraction paths.

## 2.4 Spotify enrichment

- Spotify enrichment is optional and controlled by configuration.
- Client Credentials authentication is used.
- Credentials may be supplied in `config.json` or via `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET` environment variables.
- Search query is exactly `title + album`.
- Returned Spotify result order is preserved.
- The first returned result within the configured duration tolerance of the actual downloaded YT Music audio duration is selected.
- No Spotify candidate scoring system is used.
- No Spotify result ranking beyond returned order + duration match is introduced.
- Spotify can enrich core music metadata.
- Spotify ISRC is preferred as the canonical ISRC when a Spotify track is successfully matched and supplies a valid ISRC.
- If Spotify does not supply a valid ISRC, valid source ISRC from yt-dlp may be used.
- ISRC is metadata and the sole duplicate identifier; it is not used for any other matching heuristic.
- Spotify artwork uses the largest album image returned by Spotify.
- Spotify artwork bytes are preserved byte-for-byte and are not cropped, resized, recompressed, sharpened, recolored, or otherwise transformed.
- If Spotify artwork is unavailable/invalid for the intended use, the pipeline may fall back to the YT Music artwork pipeline.

## 2.5 ISRC duplicate detection

- ISRC is the **only** retained-song duplicate identifier.
- Canonical ISRC is normalized by removing spaces and hyphens and uppercasing alphanumeric content, then validated against the standard 12-character form.
- Invalid ISRC is treated as missing.
- Missing/invalid ISRC means no duplicate lookup is performed.
- No title, artist, album, duration, YT Music ID, YouTube ID, filename, audio hash, fuzzy similarity, or composite metadata matching is permitted.
- When a duplicate exists, the operator explicitly chooses `keep_previous` or `keep_current`.

## 2.6 Duplicate resolution

### Keep previous

- The existing retained song is untouched.
- The previous MP3/LRC/JSON remains untouched.
- The current temporary acquisition is discarded.
- The current playlist occurrence is marked `duplicate`.
- No current `songs.db` row is created.

### Keep current

- The current replacement is completely built and validated before old physical artifacts are destroyed.
- The old retained row and current retained row transition is performed transactionally.
- The old playlist serial is returned to `pending`.
- The current playlist serial becomes `completed`.
- The old physical artifacts are cleaned after the database commit.
- Playlist serial identities never change.

## 2.7 YouTube music-video discovery

- Search query is built as `{title} {album} official video song` using normalized/current song metadata.
- Search results are consumed in returned order.
- A result is skipped only when its title contains the word `lyrics`, case-insensitively.
- The first remaining result is immediately selected.
- No result score, confidence value, ranking, weighted heuristic, duration comparison, channel comparison, popularity comparison, or manual weighting is performed by the application.
- If every fetched result contains `lyrics`, there is no selected video.
- Lack of an acceptable YouTube result does not fail the song.

## 2.8 LRCLIB lyrics

- Only `GET /api/get` is permitted.
- `/api/search` is never called.
- Lookup uses the current song metadata and duration.
- Only valid synchronized lyrics are accepted.
- Plain-only lyrics are not downloaded as `.lrc` and do not count as synchronized lyrics.
- Synced lyrics are saved alongside the MP3 as `.lrc`.
- Synced lyrics are also embedded into the MP3 using synchronized lyrics metadata plus a compatibility plain-text lyrics projection.
- Songs with synced lyrics are stored under `songs/synced_lyrics/`.
- Songs without synced lyrics are stored under `songs/no_synced_lyrics/`.
- Unsynced songs do not receive an `.lrc` file.

## 2.9 Artwork

- Spotify is the preferred final artwork provider when Spotify enrichment is enabled, a track is matched, and usable album artwork is available.
- The largest Spotify image returned is selected.
- Spotify image bytes are preserved unchanged.
- For YT Music fallback artwork, all useful yt-dlp thumbnail variants may be considered and a page OpenGraph image may be inspected.
- The YT Music fallback prefers a valid square candidate and can normalize a non-square fallback to square JPEG.
- Artwork is embedded as front cover APIC.

## 2.10 MP3 metadata

The MP3 is intentionally concise and player-facing. It contains:

- Title
- Track artist
- Album artist
- Album
- Release date
- Track/disc number when known
- Genre when known
- Composer when known
- Publisher/label when known
- Copyright when known
- Language when known
- BPM when known
- Compilation when known
- Actual MP3 duration
- Canonical ISRC when available
- YT Music source video ID and URL
- Selected YouTube music-video ID, URL and title
- Spotify track ID, album ID, URL and ISRC when matched/available
- Front-cover artwork
- Synchronized lyrics
- A small durable set of application metadata via TXXX/UFID/WXXX

The MP3 does **not** contain the large raw API blobs or verbose source/debug information.

## 2.11 Sidecar JSON

Every finalized MP3 gets a same-basename `.json` sidecar.

The JSON is the detailed record and can contain:

- Complete normalized metadata
- Playlist identity
- YT Music playlist-item JSON
- yt-dlp source `info.json`
- Spotify search/match information
- Spotify track JSON
- Spotify album JSON
- Spotify artwork provenance
- Selected YouTube search information
- Selected YouTube information JSON
- LRCLIB response/matching information
- Downloaded synchronized lyric text
- Artwork provenance and hashes
- MP3 file size/hash
- LRC path/status
- Duplicate decision information
- Build/schema versions

---

# 3. End-to-End Architecture

```text
YouTube Music Playlist
        |
        v
YTMusic API ingestion
        |
        +--> preserve API order
        +--> assign/reuse permanent serials
        |
        v
playlist.db
        |
        v
next pending playlist occurrence
        |
        v
Create temp/{serial}/
        |
        v
ONE yt-dlp acquisition
  |-- master.mp3
  |-- master.info.json
  `-- thumbnails
        |
        +--> source validation
        |
        +--> metadata normalization
        |
        +--> optional Spotify search
        |       |
        |       +--> first duration match
        |       `--> optional largest Spotify artwork
        |
        +--> canonical ISRC determination
        |       |
        |       `--> ISRC-only duplicate lookup
        |                |
        |                +--> unique
        |                |
        |                `--> user decision
        |                         |
        |                         +--> keep previous
        |                         `--> keep current
        |
        +--> YouTube video search
        |       |
        |       `--> first result not containing 'lyrics'
        |
        +--> LRCLIB GET /api/get
        |       |
        |       `--> synchronized lyrics only
        |
        +--> artwork selection
        |
        v
final metadata model
        |
        +-------------------+
        |                   |
        v                   v
concise MP3 tags       detailed sidecar JSON
        |                   |
        +-- APIC             +-- raw source JSON
        +-- SYLT             +-- Spotify JSON
        +-- USLT             +-- YouTube JSON
        +-- standard ID3     +-- LRCLIB data
        +-- TXXX             +-- provenance
        +-- UFID             +-- hashes
        +-- WXXX             +-- file information
        |
        v
validate final MP3
        |
        v
SHA-256
        |
        v
atomic promotion
        |
        +--> songs/.../*.mp3
        +--> songs/.../*.lrc (synced only)
        +--> songs/.../*.json
        |
        v
transactional SQLite commit
        |
        +--> songs.db
        `--> playlist.db status=completed
```

---

# 4. Project Directory

```text
phase1_project/
├── main.py
├── config.json
├── cookies.txt
├── requirements.txt
├── requirements-dev.txt
├── pytest.ini
├── README.md
├── BUILD_INFO.md
├── CHANGELOG.md
├── .gitignore
│
├── src/
│   ├── __init__.py
│   ├── db_playlist.py
│   ├── db_songs.py
│   ├── playlist_ingest.py
│   ├── downloader.py
│   ├── metadata.py
│   ├── duplicate_checker.py
│   ├── spotify.py
│   ├── youtube_finder.py
│   ├── lrclib.py
│   ├── artwork.py
│   ├── embedder.py
│   ├── validator.py
│   ├── hashing.py
│   ├── sidecar.py
│   └── pipeline.py
│
├── scripts/
│   ├── clean_runtime.py
│   ├── init_db.py
│   ├── inspect_mp3.py
│   └── reembed_existing.py
│
├── tests/
│   ├── test_phase1.py
│   └── fixtures/
│       ├── sample.mp3
│       └── sample.jpg
│
├── docs/
│   ├── ACTIVE_SPEC.md
│   ├── ACTIVE_IMPLEMENTATION_RULES.md
│   ├── CURRENT_IMPLEMENTATION_RULES.md
│   ├── DUPLICATE_ISRC.md
│   ├── METADATA_EMBEDDING.md
│   ├── ENHANCEMENTS_SPOTIFY_LRCLIB.md
│   ├── ARTWORK_FIX.md
│   ├── ARCHITECTURE.md
│   ├── TEST_MATRIX.md
│   ├── FINAL_AUDIT*.md
│   ├── HISTORICAL_PHASE1_SPEC.md
│   ├── project_spec.md
│   ├── implementation_analysis.md
│   └── runtime_notes.md
│
├── songs/
│   ├── synced_lyrics/
│   │   ├── <name>.mp3
│   │   ├── <name>.lrc
│   │   └── <name>.json
│   └── no_synced_lyrics/
│       ├── <name>.mp3
│       └── <name>.json
│
├── temp/
├── logs/
│   └── phase1.log
└── db/
    ├── playlist.db
    └── songs.db
```

---

# 5. Runtime Dependencies

## 5.1 Python

- Python 3.11+.

## 5.2 Python packages

The project requires these functional dependencies:

- `ytmusicapi`
- `yt-dlp`
- `mutagen`
- `requests`
- `Pillow`

Development/testing dependencies include pytest tooling.

## 5.3 External binaries

- FFmpeg
- FFprobe
- A supported JavaScript runtime for yt-dlp where the installed yt-dlp extraction path requires it.

## 5.4 Runtime doctor

`python main.py --doctor` checks the configuration/environment and reports missing runtime components before normal processing.

---

# 6. Configuration

The configuration controls integration availability and operational limits but must not silently change architectural rules.

Current example:

```json
{
  "ytmusic_playlist_id": "YOUR_PLAYLIST_ID",
  "ytmusic_auth_file": null,
  "paths": {
    "songs": "songs",
    "songs_with_synced_lyrics": "songs/synced_lyrics",
    "songs_without_synced_lyrics": "songs/no_synced_lyrics",
    "temp": "temp",
    "database": "db",
    "logs": "logs"
  },
  "download": {
    "audio_format": "mp3",
    "audio_quality": "0",
    "write_info_json": true,
    "write_thumbnail": true,
    "convert_thumbnail": "jpg",
    "write_all_thumbnails": true
  },
  "youtube_video_search": {
    "results_to_fetch": 10,
    "skip_title_keyword": "lyrics"
  },
  "spotify": {
    "enabled": false,
    "client_id": "",
    "client_secret": "",
    "use_env": true,
    "market": "IN",
    "search_limit": 10,
    "duration_tolerance_seconds": 2,
    "timeout_seconds": 30,
    "max_retries": 3,
    "fail_on_error": false,
    "artwork": {
      "enabled": true,
      "timeout_seconds": 30,
      "fail_on_error": false
    }
  },
  "lyrics": {
    "enabled": true,
    "timeout_seconds": 30,
    "max_retries": 3,
    "request_delay_seconds": 0.5,
    "user_agent": "Phase1AudioDownloader/1.0 (https://github.com/your-user/your-repo)",
    "fail_on_error": false,
    "endpoint": "/api/get",
    "download_only_synced": true,
    "embed_synced": true,
    "embed_plain_fallback": true
  },
  "retry": {
    "max_attempts": 3,
    "backoff_seconds": 2
  },
  "yt_dlp_binary": "yt-dlp",
  "cookies_file": "cookies.txt",
  "ffmpeg_location": null,
  "js_runtime": "auto",
  "js_runtime_path": null,
  "socket_timeout_seconds": 30,
  "download_timeout_seconds": 3600,
  "youtube_search_timeout_seconds": 120,
  "youtube_metadata_timeout_seconds": 120,
  "max_description_chars": 0,
  "artwork": {
    "og_image_enabled": true,
    "og_image_timeout_seconds": 30,
    "force_square": true,
    "max_dimension": 1200,
    "jpeg_quality": 98
  },
  "filesystem": {
    "max_filename_length": 180
  },
  "duplicate_detection": {
    "enabled": true,
    "identifier": "isrc",
    "on_duplicate": "prompt"
  }
}

```

## 6.1 Configuration semantics

### YouTube Music

- `ytmusic_playlist_id`: playlist to ingest.
- `ytmusic_auth_file`: optional authentication file.

### Paths

- `songs`: root song directory.
- `songs_with_synced_lyrics`: final synced output directory.
- `songs_without_synced_lyrics`: final non-synced output directory.
- `temp`: per-entry work area.
- `database`: SQLite directory.
- `logs`: application logs.

### Download

- `audio_format`: final audio format (`mp3`).
- `audio_quality`: yt-dlp audio quality selection.
- `write_info_json`: source info JSON must be written.
- `write_thumbnail`: artwork acquisition must be attempted.
- `convert_thumbnail`: YT Music fallback thumbnail conversion format.
- `write_all_thumbnails`: collect all available yt-dlp thumbnail variants for selection.

### YouTube video search

- `results_to_fetch`: maximum results fetched.
- `skip_title_keyword`: title exclusion keyword; current value `lyrics`.

### Spotify

- `enabled`: enable/disable enrichment.
- `client_id`, `client_secret`: credentials.
- `use_env`: permit environment-variable credential overrides.
- `market`: Spotify market used for search/catalog requests.
- `search_limit`: number of search results requested.
- `duration_tolerance_seconds`: maximum allowed duration difference.
- request timeout/retry controls.
- `artwork.enabled`: enable Spotify artwork.
- artwork timeout/failure behavior.

### Lyrics

- `enabled`: enable LRCLIB integration.
- `timeout_seconds`: HTTP timeout.
- `max_retries`: retry count.
- `request_delay_seconds`: throttle between calls.
- `user_agent`: HTTP user-agent.
- `fail_on_error`: whether LRCLIB failure should fail the song.
- `endpoint`: must remain `/api/get`.
- `download_only_synced`: synchronized lyrics only.
- `embed_synced`: embed synchronized lyrics.
- `embed_plain_fallback`: current compatibility behavior; no unsynced `.lrc` is created.

### Retry

- `max_attempts`: pipeline attempts.
- `backoff_seconds`: delay between attempts.

### yt-dlp / network

- `yt_dlp_binary`: command/executable.
- `cookies_file`: optional cookies.
- `ffmpeg_location`: optional explicit FFmpeg location.
- `js_runtime`, `js_runtime_path`: JavaScript runtime selection.
- socket/download/search/metadata timeouts.

### Artwork

- `og_image_enabled`: inspect page OpenGraph image for YT Music fallback.
- `og_image_timeout_seconds`: OG request timeout.
- `force_square`: square YT Music fallback normalization.
- `max_dimension`: maximum normalized YT Music fallback size.
- `jpeg_quality`: YT Music fallback JPEG quality.

### Filesystem

- `max_filename_length`: maximum safe final filename length.

### Duplicate detection

- `enabled`: enable ISRC duplicate handling.
- `identifier`: must be `isrc` for current architecture.
- `on_duplicate`: `prompt` for interactive resolution.

---

# 7. Playlist Ingestion

## 7.1 Read configuration

Load `ytmusic_playlist_id` and optional YTMusic authentication.

## 7.2 Create client

Create a `ytmusicapi.YTMusic` client.

## 7.3 Retrieve complete playlist

The playlist ingestion layer requests the complete playlist according to the current ytmusicapi interface and processes returned items in order.

## 7.4 Track extraction

For normal entries, extract:

- `videoId`
- title
- artists
- album
- duration
- availability
- original playlist-item JSON

## 7.5 Unavailable entries

An item such as:

```json
{
  "videoId": null,
  "title": "Nuvvu Navvukuntu",
  "isAvailable": false
}
```

is not an ingestion-fatal error.

The item is preserved. The application stores the metadata that exists, keeps the permanent serial, and records an `error` state explaining that the entry currently has no usable source video ID.

No fake `videoId` is generated.

## 7.6 Existing entries

When the same usable playlist occurrence is ingested again:

- preserve its serial,
- preserve its historical status when applicable,
- refresh source fields as appropriate,
- never recycle serials.

## 7.7 New entries

Allocate the next never-used serial after the existing maximum.

## 7.8 Ingestion order

Initial serial assignment follows API return order. Processing of pending entries follows:

```sql
ORDER BY playlist_position ASC, serial_number ASC
LIMIT 1
```

---

# 8. Playlist Database — Current Schema

`playlist.db` answers: **which playlist occurrences exist and what state are they in?**

Current table:

```sql
CREATE TABLE playlist_entries (
    serial_number INTEGER PRIMARY KEY,
    playlist_position INTEGER NOT NULL,
    ytm_playlist_id TEXT,
    ytm_video_id TEXT,
    ytm_url TEXT,
    title TEXT NOT NULL,
    artist TEXT NOT NULL,
    album TEXT,
    duration INTEGER,
    ytm_playlist_item_json TEXT,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'completed', 'duplicate', 'error')),
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

Indexes:

```sql
CREATE INDEX idx_playlist_status_position
ON playlist_entries(status, playlist_position, serial_number);

CREATE INDEX idx_playlist_video_id
ON playlist_entries(ytm_video_id);
```

## 8.1 Status meanings

### pending
Entry is ready to be processed.

### completed
Entry currently owns the retained song row/file.

### duplicate
Entry was processed and intentionally did not retain its own song because its ISRC duplicated an existing retained song and the operator chose to keep the previous song.

### error
The entry cannot currently be processed successfully, for example because the source is unavailable or an unrecoverable processing error occurred.

---

# 9. Songs Database — Current Schema

`songs.db` answers: **which playlist entries currently own retained songs, and what normalized/file/enrichment metadata belongs to those songs?**

Current table:

```sql
CREATE TABLE songs (
    serial_number INTEGER PRIMARY KEY,
    ytm_playlist_id TEXT,
    title TEXT NOT NULL,
    title_original TEXT,
    primary_artist TEXT,
    artist TEXT,
    artists_json TEXT,
    album TEXT,
    album_artist TEXT,
    isrc TEXT,
    track_number TEXT,
    disc_number TEXT,
    release_date TEXT,
    release_date_source TEXT,
    upload_date TEXT,
    upload_timestamp INTEGER,
    release_timestamp INTEGER,
    modified_date TEXT,
    modified_timestamp INTEGER,
    description TEXT,
    genre TEXT,
    composer TEXT,
    publisher TEXT,
    copyright TEXT,
    license TEXT,
    comment TEXT,
    language TEXT,
    bpm INTEGER,
    compilation INTEGER,
    encoder TEXT,
    duration INTEGER,
    source_duration INTEGER,
    source_ext TEXT,
    source_container TEXT,
    source_codec TEXT,
    source_format_id TEXT,
    source_format_note TEXT,
    source_bitrate INTEGER,
    source_sample_rate INTEGER,
    source_channels INTEGER,
    source_filesize INTEGER,
    source_filesize_approx INTEGER,
    source_language TEXT,
    source_video_id TEXT,
    source_webpage_url TEXT,
    source_original_url TEXT,
    source_display_id TEXT,
    source_webpage_url_basename TEXT,
    source_webpage_url_domain TEXT,
    source_extractor TEXT,
    source_extractor_key TEXT,
    source_channel TEXT,
    source_channel_id TEXT,
    source_channel_url TEXT,
    source_channel_follower_count INTEGER,
    source_channel_is_verified INTEGER,
    source_uploader TEXT,
    source_uploader_id TEXT,
    source_uploader_url TEXT,
    source_views INTEGER,
    source_location TEXT,
    source_availability TEXT,
    source_age_limit INTEGER,
    source_live_status TEXT,
    source_media_type TEXT,
    source_thumbnail TEXT,
    source_thumbnails_json TEXT,
    source_categories_json TEXT,
    source_tags_json TEXT,
    source_playlist TEXT,
    source_playlist_id TEXT,
    source_playlist_count INTEGER,
    source_playlist_index INTEGER,
    source_playlist_uploader TEXT,
    source_playlist_uploader_id TEXT,
    source_playlist_channel TEXT,
    source_playlist_channel_id TEXT,
    source_playlist_webpage_url TEXT,
    ytm_video_id TEXT,
    ytm_url TEXT,
    ytm_playlist_item_json TEXT,
    source_info_json TEXT NOT NULL,
    mp3_path TEXT NOT NULL,
    mp3_size INTEGER,
    mp3_sha256 TEXT,
    yt_video_id TEXT,
    yt_video_url TEXT,
    yt_video_title TEXT,
    yt_video_fulltitle TEXT,
    yt_video_alt_title TEXT,
    yt_video_channel TEXT,
    yt_video_channel_id TEXT,
    yt_video_uploader TEXT,
    yt_video_uploader_id TEXT,
    yt_video_upload_date TEXT,
    yt_video_timestamp INTEGER,
    yt_video_release_date TEXT,
    yt_video_release_timestamp INTEGER,
    yt_video_duration INTEGER,
    yt_video_views INTEGER,
    yt_video_likes INTEGER,
    yt_video_comments INTEGER,
    yt_video_thumbnail TEXT,
    yt_video_description TEXT,
    yt_video_categories_json TEXT,
    yt_video_tags_json TEXT,
    yt_video_extractor TEXT,
    yt_video_extractor_key TEXT,
    yt_video_info_json TEXT,
    yt_video_search_query TEXT,
    yt_video_search_result_index INTEGER,
    yt_video_search_results_fetched INTEGER,
    yt_video_match_method TEXT,
    artwork_source_url TEXT,
    artwork_width INTEGER,
    artwork_height INTEGER,
    artwork_path TEXT,
    artwork_provider TEXT,
    metadata_json_path TEXT,
    spotify_track_id TEXT,
    spotify_track_name TEXT,
    spotify_track_url TEXT,
    spotify_uri TEXT,
    spotify_artists_json TEXT,
    spotify_artist_ids_json TEXT,
    spotify_artist_urls_json TEXT,
    spotify_album_id TEXT,
    spotify_album_name TEXT,
    spotify_album_url TEXT,
    spotify_album_type TEXT,
    spotify_album_release_date TEXT,
    spotify_album_release_precision TEXT,
    spotify_album_total_tracks INTEGER,
    spotify_album_artwork_url TEXT,
    spotify_album_artwork_width INTEGER,
    spotify_album_artwork_height INTEGER,
    spotify_album_label TEXT,
    spotify_album_copyrights_json TEXT,
    spotify_duration_ms INTEGER,
    spotify_duration_seconds INTEGER,
    spotify_duration_delta_ms INTEGER,
    spotify_explicit INTEGER,
    spotify_popularity INTEGER,
    spotify_isrc TEXT,
    spotify_track_number INTEGER,
    spotify_disc_number INTEGER,
    spotify_search_query TEXT,
    spotify_search_result_index INTEGER,
    spotify_raw_json TEXT,
    spotify_album_raw_json TEXT,
    lyrics_status TEXT,
    lyrics_path TEXT,
    lrclib_id INTEGER,
    lrclib_track_name TEXT,
    lrclib_artist_name TEXT,
    lrclib_album_name TEXT,
    lrclib_duration INTEGER,
    lrclib_duration_delta_seconds REAL,
    lrclib_match_method TEXT,
    lrclib_raw_json TEXT,
    lyrics_embedded INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

Indexes:

```sql
CREATE INDEX idx_songs_isrc ON songs(isrc) WHERE isrc IS NOT NULL;
CREATE INDEX idx_songs_yt_video_id ON songs(yt_video_id);
CREATE INDEX idx_songs_ytm_video_id ON songs(ytm_video_id);
```

---

# 10. Database Invariants

After every successful committed state:

1. A serial exists at most once in `playlist.db`.
2. A serial exists at most once in `songs.db`.
3. `completed` playlist entries have a matching `songs.db` row.
4. A completed row points to an existing valid final MP3.
5. `duplicate` playlist entries do not own a retained `songs.db` row.
6. `pending` entries normally do not own a retained `songs.db` row.
7. `error` entries are allowed without a retained song.
8. `songs.isrc` contains the canonical ISRC when one exists.
9. The ISRC index contains non-NULL values only.
10. Duplicate lookup never considers the current serial as an existing duplicate of itself.

---

# 11. State Machine

```text
pending
   |
   v
processing
   |
   +-----------------------------+
   |                             |
   v                             v
success                       failure
   |                             |
   v                             v
ISRC duplicate check           error
   |                             |
   +----------+----------+       |
              |          |       |
              v          v       |
            unique    duplicate  |
              |          |       |
              |      user choice |
              |       /      \   |
              |      v        v  |
              | keep_prev  keep_current
              |      |        |
              |      v        v
              |  duplicate  completed
              |                 |
              +-----------------+

Old completed entry + keep_current:
old serial -> pending
current serial -> completed
```

Error retry:

```text
error -> pending
```

---

# 12. Per-Entry Working Directory

For serial `004`:

```text
temp/004/
```

The directory is the complete temporary workspace for one processing attempt.

Expected acquisition artifacts:

```text
temp/004/
├── master.mp3
├── master.info.json
├── thumbnails / thumbnail variants
└── manifest/recovery metadata when applicable
```

Temporary final production output can be staged separately until validation and promotion.

No temporary artifact is considered a retained library file.

---

# 13. One Complete yt-dlp Acquisition

The downloader must obtain the source package in one initial acquisition operation.

Conceptual behavior:

```bash
yt-dlp \
  -x \
  --audio-format mp3 \
  --audio-quality 0 \
  --write-info-json \
  --write-thumbnail \
  --write-all-thumbnails \
  --convert-thumbnails jpg \
  -o "temp/{serial}/master.%(ext)s" \
  "{ytm_url}"
```

The exact command may adapt to the installed yt-dlp version, but these behaviors must remain:

- acquire source audio;
- write complete source info JSON;
- acquire thumbnails needed for artwork selection;
- do not use `--add-metadata`;
- use the YT Music source URL, not a general YouTube audio search.

---

# 14. Source Validation

Before enrichment:

- `master.mp3` exists.
- `master.info.json` exists.
- JSON parses successfully.
- MP3 is readable.
- MP3 duration can be obtained.
- At least one usable artwork source exists when artwork is required.

Failure behavior:

1. write descriptive error;
2. set playlist entry to `error`;
3. do not create a `songs.db` retained row;
4. preserve serial;
5. retain enough information for retry.

---

# 15. Metadata Normalization

The metadata pipeline has three conceptual layers.

## Layer A — Raw metadata

The exact `master.info.json`, YT Music playlist-item JSON, Spotify API JSON, YouTube selected-result JSON, and LRCLIB response are preserved in the sidecar.

## Layer B — Normalized application model

A structured normalized representation is used internally and persisted to `songs.db`.

## Layer C — MP3 export model

Only selected player-facing fields and important identifiers are mapped into ID3.

---

# 16. Final Metadata Source Precedence

## 16.1 Title

Primary: normalized YT Music/yt-dlp title.

Spotify matched track may overlay the final title when Spotify enrichment is enabled and matched.

## 16.2 Artist

Primary: normalized YT Music/yt-dlp artist information.

Spotify matched track may overlay artist names when matched.

## 16.3 Album

Primary: YT Music/yt-dlp album.

Spotify matched track may overlay album identity/name when matched.

## 16.4 Album artist

Spotify album artists may improve album-artist identity when matched; otherwise source metadata is used when available.

## 16.5 Release date

Use catalog/source release date rather than silently replacing it with upload date.

Spotify release date can enrich/overlay the music release date when a match is accepted.

## 16.6 Track/disc number

Spotify catalog track/disc values can enrich source metadata when matched.

Playlist serial is never used as album track number.

## 16.7 Publisher/label

Use source publisher when present; Spotify album label can fill/enrich when present.

## 16.8 Copyright

Use source copyright when present; Spotify album copyright data can enrich it when present.

## 16.9 Duration

The actual downloaded MP3 duration is authoritative for the song file.

Spotify duration is used only for candidate matching and retained as catalog metadata.

YouTube music-video duration is video metadata and does not replace song duration.

## 16.10 ISRC

Canonical precedence:

1. valid Spotify matched-track ISRC;
2. valid source/yt-dlp ISRC fallback;
3. missing if neither exists.

---

# 17. Spotify Integration — Detailed Plan

## 17.1 Authentication

Use Spotify Web API Client Credentials flow.

Credential priority:

1. environment variables when `use_env=true` and variables exist;
2. config values;
3. disabled/error behavior according to configuration.

The project does not download Spotify audio.

## 17.2 Search

Search query:

```text
{title} {album}
```

The request should identify tracks rather than albums/playlists.

## 17.3 Candidate selection

For each returned result in original order:

1. read Spotify track duration;
2. compare with actual downloaded audio duration;
3. accept the first track whose duration delta is within `duration_tolerance_seconds`.

No post-search scoring is used.

## 17.4 Spotify data imported

Track:

- Spotify track ID
- track name
- Spotify URL
- URI
- artists
- artist IDs
- artist URLs
- album ID/name/URL/type
- album release date/precision
- album total tracks
- track/disc number
- duration in milliseconds/seconds
- explicit flag
- popularity
- external IDs including ISRC

Album:

- album object
- album label
- copyrights
- all album image metadata

## 17.5 Spotify artwork

- select largest returned album image by dimensions/area;
- download exact bytes;
- validate image format/readability;
- preserve bytes unchanged;
- embed unchanged image as APIC;
- record URL, dimensions, MIME type, byte length and SHA-256 in sidecar.

No resampling is done to Spotify artwork.

---

# 18. ISRC — Detailed Plan

## 18.1 Canonicalization

Input examples may contain spaces or hyphens. Canonical representation is uppercase alphanumeric characters in the standard 12-character shape.

Invalid values become missing.

## 18.2 Storage

Canonical ISRC:

```text
songs.isrc
```

Spotify original source value:

```text
songs.spotify_isrc
```

## 18.3 MP3

Write canonical ISRC to:

```text
TSRC
```

and expose it as:

```text
TXXX:isrc
```

only when one exists.

## 18.4 Duplicate query

Only:

```sql
SELECT *
FROM songs
WHERE isrc = ?
LIMIT 1;
```

No fallback matching.

---

# 19. Duplicate Resolution — Transactional Safety

## 19.1 Keep previous

The current temporary work is disposable.

Order:

1. detect duplicate;
2. show current + existing metadata;
3. operator selects previous;
4. mark current playlist entry `duplicate`;
5. commit status;
6. clean current temp artifacts.

## 19.2 Keep current

Never destroy old retained artifacts before the replacement is valid.

Order:

1. detect duplicate;
2. operator selects current;
3. finish Spotify/YouTube/LRCLIB/artwork work;
4. build final MP3;
5. write tags;
6. write lyrics;
7. validate final MP3;
8. generate sidecar;
9. compute hashes;
10. stage replacement files;
11. transactionally replace old/current DB ownership;
12. commit;
13. remove old artifacts;
14. clean temporary workspace.

If a failure occurs before commit, the previous retained song should remain intact.

---

# 20. YouTube Music-Video Search

## 20.1 Query

```text
{title} {album} official video song
```

## 20.2 Filtering

For each result in returned order:

```text
lower(result.title)
```

If it contains `lyrics`, skip it.

Otherwise select immediately.

## 20.3 Selected fields

Main MP3 metadata only needs:

- YouTube video ID
- YouTube video URL
- YouTube video title

Detailed fields remain in JSON, including when available:

- channel
- channel ID
- uploader
- uploader ID
- upload date
- release date
- timestamp
- duration
- views
- likes/comments
- categories/tags
- thumbnail
- raw info JSON

## 20.4 No acceptable result

Set selected-video fields to null/absent and continue successfully.

---

# 21. LRCLIB — Detailed Plan

## 21.1 Allowed endpoint

Only:

```text
GET /api/get
```

is permitted.

The implementation must never call `/api/search`.

## 21.2 Inputs

Use current song metadata:

- track name/title
- artist
- album
- duration

## 21.3 Acceptance

Accept only when:

- response is structurally valid;
- synchronized lyrics are present;
- synchronized lyrics parse into valid timestamped lines.

Plain-only lyrics are rejected for the `.lrc` workflow.

## 21.4 Saved lyrics

Synced:

```text
<name>.lrc
```

and embedded as synchronized lyrics.

Unsynced:

- no `.lrc` file;
- song placed in `songs/no_synced_lyrics`;
- JSON records the lyric lookup/result status.

## 21.5 Lyrics embedding

The final MP3 receives:

- `SYLT` for synchronized timestamped lyrics;
- `USLT` compatibility representation containing lyric text.

The actual `.lrc` file preserves the timestamped external representation.

---

# 22. Artwork — Detailed Plan

## 22.1 Provider precedence

1. Spotify matched track artwork when enabled and valid.
2. YT Music/yt-dlp fallback artwork.

## 22.2 Spotify

Use the largest image returned by Spotify.

Preserve the bytes exactly.

## 22.3 YT Music fallback

The thumbnail problem addressed by the implementation is that generic YouTube thumbnails can be padded/widescreen while YT Music exposes square album-art images.

The fallback process:

1. collect all yt-dlp thumbnails;
2. inspect thumbnail dimensions/URLs;
3. optionally inspect YT Music OpenGraph image;
4. classify square candidates;
5. prefer valid square artwork;
6. use source provenance to break ties;
7. if fallback is not square and square normalization is enabled, center-crop to square;
8. correct EXIF orientation;
9. output normalized JPEG.

Spotify artwork does not go through this transformation stage.

## 22.4 MP3 embedding

Artwork is embedded as front cover `APIC`.

---

# 23. MP3 Metadata Contract

## 23.1 Standard ID3 frames

When available:

```text
TIT2  Title
TPE1  Track artist
TPE2  Album artist
TALB  Album
TDRC  Release date
TRCK  Track number
TPOS  Disc number
TCON  Genre
TCOM  Composer
TPUB  Publisher/label
TCOP  Copyright
TLAN  Language
TBPM  BPM
TCMP  Compilation
TENC  Encoder
TLEN  Actual duration in milliseconds
TSRC  Canonical ISRC
APIC  Front cover
SYLT  Synchronized lyrics
USLT  Lyrics text compatibility
```

Only fields with meaningful values are written, except fields explicitly required by the final validation contract.

## 23.2 TXXX

Keep TXXX concise. Durable application/catalog fields include:

- serial number
- playlist position
- YT Music source ID
- YouTube selected video ID/title
- Spotify track ID
- Spotify album ID
- Spotify ISRC
- canonical ISRC
- lyrics status
- artwork provider/basic properties

## 23.3 UFID

Use machine-readable identifiers for:

- YT Music source ID
- selected YouTube video ID
- Spotify track ID when available

## 23.4 WXXX

Use important URLs:

- YT Music source URL
- selected YouTube video URL
- Spotify track URL
- Spotify album URL when available

## 23.5 Intentionally excluded from main MP3 metadata

Do not embed:

- raw API JSON
- raw yt-dlp blobs
- long source descriptions
- age restrictions
- verbose channel details
- extractor internals
- downloader debug information
- giant thumbnail lists
- format-selection internals

Those go to JSON sidecar.

---

# 24. Sidecar JSON Contract

Every finalized MP3 must have a same-basename JSON.

Example:

```text
001_Urike_Urike_Artist.mp3
001_Urike_Urike_Artist.json
```

If synced:

```text
001_Urike_Urike_Artist.lrc
```

## 24.1 Recommended top-level sections

```json
{
  "schema_version": "...",
  "metadata_export_version": "...",
  "playlist": {},
  "song": {},
  "source": {},
  "spotify": {},
  "youtube_video": {},
  "lyrics": {},
  "artwork": {},
  "files": {},
  "duplicate": {},
  "timestamps": {}
}
```

## 24.2 Raw provenance

The sidecar can contain complete raw objects rather than trying to encode them into ID3.

This is where verbose source data belongs.

---

# 25. Filename Rules

Final filename:

```text
{serial:03d}_{safe_title}_{safe_artist}.mp3
```

Corresponding files:

```text
{same basename}.json
{same basename}.lrc    # only synced lyrics
```

Requirements:

- serial always present;
- Windows-invalid characters removed/replaced;
- no directory traversal;
- reserved Windows names protected;
- filename length bounded;
- readable title/artist preserved where possible;
- database identity never changes because of filename sanitization.

---

# 26. Finalization Order

The safe order is:

```text
1. Read playlist occurrence
2. Create/recover temporary workspace
3. Acquire source once with yt-dlp
4. Validate source artifacts
5. Normalize metadata
6. Attempt Spotify enrichment when enabled
7. Determine canonical ISRC
8. Check ISRC duplicate
9. Resolve duplicate decision if needed
10. Search selected YouTube music video
11. Fetch selected video metadata
12. Obtain accepted LRCLIB synced lyrics through /api/get only
13. Select artwork
14. Construct final MP3 staging path
15. Copy/source audio into staging output
16. Write standard ID3 tags with Mutagen
17. Write concise TXXX fields
18. Write UFID/WXXX identity/URL fields
19. Embed APIC artwork
20. Embed SYLT/USLT lyrics
21. Save
22. Reopen
23. Validate
24. Generate JSON sidecar
25. Hash final MP3
26. Stage MP3/LRC/JSON
27. Atomically promote final files
28. Commit songs.db + playlist.db state transactionally
29. Clean previous duplicate artifacts if applicable
30. Remove temp directory
31. Report result
```

Physical file replacement and DB transitions must be coordinated so the application does not destroy the previous retained song before the replacement is known to be valid.

---

# 27. Validation Contract

Validation occurs on the **fully tagged, fully embedded final MP3**, not on the raw `master.mp3`.

Checks should include:

## File

- exists;
- nonzero size;
- reopenable;
- valid MP3/audio stream;
- readable duration.

## Standard metadata

- title present;
- artist present;
- album present when expected;
- release date valid when present;
- track/disc valid when present;
- standard fields match normalized model.

## ISRC

- `TSRC` matches canonical ISRC when one is available;
- no fake `NULL` string is written.

## Important source/catalog identities

- YT Music ID matches normalized source;
- YouTube selected-video ID/URL match normalized selected result;
- Spotify ID/ISRC match normalized Spotify result when matched.

## Artwork

- APIC exists;
- image is decodable;
- correct provider/bytes/provenance when applicable.

## Lyrics

- synced status matches sidecar;
- SYLT exists for synced songs;
- USLT compatibility projection exists when enabled;
- `.lrc` exists only for synced songs.

## Sidecar

- same basename exists;
- valid JSON;
- references final MP3 path;
- contains file hash/size;
- contains source/catalog provenance.

---

# 28. Hashing

After all metadata, artwork and lyrics embedding are complete:

```text
SHA-256(final MP3 bytes)
```

Store:

```text
songs.mp3_size
songs.mp3_sha256
```

and sidecar JSON file information.

The hash covers the final fully-tagged MP3, not the original `master.mp3`.

---

# 29. Error Handling

## 29.1 Source unavailable

- preserve playlist occurrence;
- status `error`;
- descriptive message;
- no `songs.db` retained row;
- retry later.

## 29.2 yt-dlp failure

- do not mark completed;
- preserve serial;
- clean partial temporary artifacts;
- allow retry.

## 29.3 Spotify failure

Default behavior is nonfatal when `fail_on_error=false`:

- continue without Spotify enrichment;
- continue using source metadata/artwork.

When `fail_on_error=true`, Spotify failure may fail the song entry.

## 29.4 YouTube no match

Nonfatal:

- selected-video fields absent/null;
- song still completes.

## 29.5 LRCLIB no synced lyrics

Nonfatal:

- song completes;
- no `.lrc` file;
- placed in `songs/no_synced_lyrics`;
- sidecar records lookup/result.

## 29.6 Final validation failure

- do not promote final MP3;
- do not insert retained `songs.db` row;
- mark playlist `error`;
- preserve serial;
- clean or retain diagnostics according to recovery policy.

---

# 30. Recovery and Restart

The pipeline is designed to tolerate interruption.

On restart:

1. initialize/migrate databases;
2. inspect playlist state;
3. retry pending entries;
4. optionally retry error entries with `--retry-errors`;
5. detect stale temporary workspaces;
6. never interpret an incomplete `.tmp` file as a completed library file;
7. preserve permanent serials.

For duplicate replacement:

- if crash occurs before commit, previous retained row/file remains the safe fallback;
- if commit succeeds, post-commit cleanup removes old artifacts;
- orphan cleanup must never delete a file still referenced by a committed `songs.db` row.

---

# 31. CLI / Operational Commands

Current main options include:

```text
python main.py
python main.py --config config.json
python main.py --ingest-only
python main.py --no-ingest
python main.py --status
python main.py --doctor
python main.py --retry-errors
python main.py --check-invariants
python main.py --limit N
```

Typical workflow:

```powershell
python main.py --doctor
python main.py
```

Monitoring:

```powershell
python main.py --status
python main.py --check-invariants
```

Retry:

```powershell
python main.py --retry-errors
```

---

# 32. Logging

The application logs important state changes to `logs/phase1.log`.

Useful events:

- playlist ingestion summary;
- serial allocation/reuse;
- unavailable playlist entries;
- source acquisition start/end;
- Spotify match/no-match/error;
- ISRC detection;
- duplicate prompt and decision;
- YouTube search query and selection;
- LRCLIB request/result/status;
- artwork provider and size;
- final validation;
- hash;
- DB commit;
- cleanup.

Credentials/secrets must never be logged.

---

# 33. Security and Credential Handling

- Do not commit actual Spotify credentials.
- Prefer environment variables for credentials.
- `cookies.txt` is a user-local credential artifact and should remain ignored by version control.
- Do not print cookie contents.
- Do not put Spotify client secrets in sidecar JSON.
- Sidecar raw API records must exclude credential headers/tokens.
- Network requests should use configured timeouts.
- File paths are sanitized before final output.
- Temporary directories are scoped by serial number.

---

# 34. Module Responsibilities

## `main.py`

Application entry point and CLI.

Responsibilities:

- parse arguments;
- load config;
- initialize directories/databases;
- run doctor/status/invariant operations;
- start ingestion and processing;
- propagate top-level errors with appropriate exit code.

## `src/playlist_ingest.py`

Responsibilities:

- YTMusic client lifecycle;
- playlist retrieval;
- playlist-item normalization;
- unavailable-entry handling;
- permanent serial allocation/reuse;
- database ingestion.

## `src/downloader.py`

Responsibilities:

- build exact yt-dlp acquisition command;
- invoke yt-dlp;
- set runtime/network arguments;
- validate acquisition artifacts.

## `src/metadata.py`

Responsibilities:

- parse `info.json`;
- normalize fields;
- canonicalize ISRC;
- track/date/duration handling;
- convert raw source data to normalized metadata.

## `src/spotify.py`

Responsibilities:

- client credentials authentication;
- Spotify track search;
- returned-order/duration match;
- track/album retrieval;
- metadata overlay;
- largest artwork selection;
- raw data preservation.

## `src/duplicate_checker.py`

Responsibilities:

- ISRC-only duplicate lookup;
- no fallback heuristic.

## `src/youtube_finder.py`

Responsibilities:

- exact search-query construction;
- result retrieval;
- case-insensitive `lyrics` filtering;
- first acceptable selection;
- selected video metadata extraction.

## `src/lrclib.py`

Responsibilities:

- `/api/get` only;
- throttle/retry;
- synchronized lyric validation;
- LRCLIB result normalization.

## `src/artwork.py`

Responsibilities:

- YT Music thumbnail discovery;
- OpenGraph discovery;
- candidate scoring for square/fallback artwork only;
- YT Music artwork normalization;
- Spotify artwork download with byte preservation.

## `src/embedder.py`

Responsibilities:

- final ID3 creation/writing;
- standard frames;
- concise TXXX;
- UFID/WXXX;
- APIC artwork;
- SYLT/USLT lyrics.

## `src/validator.py`

Responsibilities:

- reopen final MP3;
- validate frames/IDs/artwork/lyrics;
- reject incomplete output before final DB commit.

## `src/sidecar.py`

Responsibilities:

- build same-basename detailed JSON;
- serialize all provenance safely;
- write/re-read sidecar.

## `src/hashing.py`

Responsibilities:

- SHA-256 calculation.

## `src/db_playlist.py`

Responsibilities:

- playlist schema/migrations;
- queries;
- serial integrity;
- status transitions;
- retry/reset.

## `src/db_songs.py`

Responsibilities:

- song schema/migrations;
- canonical ISRC storage/index;
- retained-row operations;
- file metadata persistence;
- song integrity checks.

## `src/pipeline.py`

Responsibilities:

- orchestration;
- per-entry lifecycle;
- duplicate resolution;
- temp workspace management;
- final path calculation;
- transaction coordination;
- overall run/status/invariant logic.

---

# 35. Detailed Data Flow for One Song

Assume:

```text
serial = 001
Title = Urike Urike
Album = Urike Urike (From "Hit 2")
Artist = M.M. Sreelekha, Sid Sriram, Ramya Behara
```

## Step A — Playlist

Create/reuse:

```text
playlist.db
001 | playlist_position=1 | YTM source ID | pending
```

## Step B — Download

Produce:

```text
temp/001/master.mp3
temp/001/master.info.json
thumbnail candidates
```

## Step C — Normalize

Extract title/artist/album/duration/source IDs.

## Step D — Spotify

Search:

```text
Urike Urike Urike Urike (From "Hit 2")
```

Select first duration match within tolerance.

Import Spotify catalog fields, IDs, ISRC, album artwork.

## Step E — Canonical ISRC

If Spotify returns valid ISRC:

```text
canonical_isrc = Spotify ISRC
```

Otherwise use valid source ISRC.

## Step F — Duplicate

Query:

```sql
songs.isrc = canonical_isrc
```

If no result: continue.

If result: prompt operator.

## Step G — YouTube

Search:

```text
Urike Urike Urike Urike (From "Hit 2") official video song
```

Skip results whose titles contain `lyrics`.

Take first remaining result.

## Step H — LRCLIB

Call only:

```text
GET /api/get
```

Use current title/artist/album/duration.

Accept synchronized result only.

## Step I — Artwork

Use largest Spotify artwork if valid; otherwise YT Music fallback artwork.

## Step J — MP3

Write concise standard ID3/custom identity fields, artwork, SYLT and USLT.

## Step K — JSON

Write detailed sidecar.

## Step L — Validate/hash/promote

Reopen → validate → SHA-256 → atomic promotion.

## Step M — Database

Insert `songs.db` row and mark playlist `completed` transactionally.

---

# 36. Sidecar vs MP3 Responsibility Matrix

| Data | MP3 | JSON |
|---|---:|---:|
| Title | Yes | Yes |
| Artist | Yes | Yes |
| Album | Yes | Yes |
| Album artist | Yes | Yes |
| Release date | Yes | Yes |
| Track/disc | Yes | Yes |
| Genre | Yes | Yes |
| Composer | Yes | Yes |
| Publisher/label | Yes | Yes |
| Copyright | Yes | Yes |
| Language | Yes | Yes |
| BPM | Yes | Yes |
| Compilation | Yes | Yes |
| Actual duration | Yes | Yes |
| Canonical ISRC | Yes | Yes |
| YT Music ID | Yes | Yes |
| YouTube video ID | Yes | Yes |
| YouTube video URL/title | Yes | Yes |
| Spotify track/album IDs | Yes | Yes |
| Spotify ISRC | Yes | Yes |
| Artwork | Yes | Yes (provenance) |
| Synced lyrics | Yes | Yes |
| `.lrc` path/status | Small metadata only | Yes |
| Source descriptions | No | Yes |
| Age-limit fields | No | Yes |
| Channel internals | No | Yes |
| Raw yt-dlp JSON | No | Yes |
| Raw YT Music JSON | No | Yes |
| Raw Spotify JSON | No | Yes |
| Raw YouTube JSON | No | Yes |
| Raw LRCLIB JSON | No | Yes |
| Search details | No | Yes |
| File hash | TXXX/sidecar optional; DB authoritative | Yes |

---

# 37. Duplicate Behavior Matrix

| Condition | Action |
|---|---|
| Valid ISRC, no matching song | Process normally |
| Valid ISRC, matching song | Prompt |
| Missing/invalid ISRC | Skip duplicate lookup; process normally |
| Duplicate + keep previous | Current becomes `duplicate`; retained song unchanged |
| Duplicate + keep current | New validated song replaces retained song; old playlist serial returns to `pending` |

---

# 38. Lyrics Output Matrix

| LRCLIB result | MP3 folder | `.lrc` | Embedded lyrics | JSON |
|---|---|---:|---:|---:|
| Valid synced lyrics | `synced_lyrics` | Yes | SYLT + USLT | Yes |
| Plain only | `no_synced_lyrics` | No | No synced lyric embedding | Yes |
| No result | `no_synced_lyrics` | No | No | Yes |
| API error with nonfatal setting | `no_synced_lyrics` | No | No | Yes |

---

# 39. Artwork Output Matrix

| Condition | Artwork source | Transformation |
|---|---|---|
| Spotify matched + valid album images | Spotify largest image | None; bytes preserved |
| Spotify unavailable + valid YT Music artwork | YT Music/yt-dlp | Candidate selection + possible square normalization |
| No usable artwork | None/controlled failure | MP3 completion behavior depends on validation/config |

---

# 40. Testing Strategy

The test suite should be deterministic and offline wherever possible by mocking network boundaries.

Minimum coverage areas:

1. normal playlist ingestion;
2. unavailable playlist entry (`videoId=None`);
3. serial preservation;
4. repeated playlist occurrences;
5. existing database migration;
6. yt-dlp command contract;
7. source validation;
8. metadata normalization;
9. Spotify duration matching;
10. Spotify returned-order selection;
11. Spotify largest artwork selection;
12. Spotify artwork byte-for-byte preservation;
13. Spotify failure nonfatal behavior;
14. ISRC normalization;
15. ISRC duplicate detection;
16. missing ISRC skip;
17. keep-previous duplicate behavior;
18. keep-current replacement safety;
19. YouTube exact query;
20. YouTube lyrics title filtering;
21. all-lyrics/no-result behavior;
22. LRCLIB `/api/get` only;
23. synchronized lyric parsing;
24. plain-only lyric rejection;
25. SYLT/USLT embedding;
26. synced/unsynced output directory rules;
27. complete sidecar creation;
28. metadata validation;
29. artwork validation;
30. hash calculation;
31. Windows filename safety;
32. transaction/recovery behavior;
33. invariant checks;
34. archive extraction/retest.

The latest prepared repository was regression-tested offline after extraction from the release archive.

---

# 41. Acceptance Criteria

The build is considered functionally complete when all of the following are true:

### Playlist

- Complete playlist can be ingested.
- Returned order is preserved.
- Every occurrence has a permanent serial.
- Unavailable entries do not crash the ingestion run.

### Download

- Usable entries download successfully through the YT Music source.
- Complete source JSON is retained.
- Artwork acquisition is available from the same source acquisition phase.

### Spotify

- Can be disabled.
- Correctly authenticates when enabled.
- Searches title + album.
- Uses first duration-matching result.
- Imports catalog identifiers/metadata.
- Extracts ISRC.
- Selects largest album artwork.
- Preserves Spotify artwork bytes unchanged.

### Duplicate

- ISRC-only.
- No fallback matching.
- Missing/invalid ISRC skips check.
- User controls duplicate decision.
- Keep-current is replacement-safe.

### YouTube

- Correct query format.
- Returned order preserved.
- `lyrics` filtering is case-insensitive.
- First acceptable result selected.
- Main MP3 gets YouTube video ID/URL/title.

### Lyrics

- Only `/api/get`.
- Only synchronized lyrics accepted.
- Synced `.lrc` exists with MP3.
- No `.lrc` for unsynced songs.
- Synced lyrics embedded into MP3.

### Metadata

- Main MP3 contains concise player-facing tags.
- Main MP3 contains important external identities.
- Raw/verbose data goes to JSON.
- Sidecar exists for every finalized MP3.

### Finalization

- Final MP3 is reopened and validated.
- Hash is over final MP3 bytes.
- File promotion is atomic.
- Database only reports completed after final artifact is valid.
- Temporary files are cleaned safely.

---

# 42. Known Design Decisions and Their Reasons

## Permanent serial rather than source ID

A playlist occurrence is an occurrence, not just a recording. Repeated source IDs therefore remain independent playlist entries.

## Separate playlist and songs databases

Playlist history/state and retained-library state answer different questions and should not be conflated.

## ISRC as sole duplicate identifier

The design intentionally avoids heuristic duplicate matching. This makes duplicate behavior deterministic and explicitly dependent on one catalog identifier.

## Spotify as enrichment, not audio source

Spotify provides catalog metadata/artwork/ISRC but does not provide the audio used by this downloader.

## YouTube music video is enrichment only

The selected YouTube video does not replace the YT Music audio source and its duration does not replace the audio duration.

## LRCLIB only `/api/get`

The current requirement deliberately prohibits broad `/api/search` discovery and limits lyrics retrieval to metadata-based lookup.

## Raw JSON in sidecar

ID3 is for player-facing metadata. The sidecar preserves complete provenance without polluting the MP3 with raw implementation data.

## Spotify artwork preserved unchanged

Avoids unnecessary transformations and retains the returned catalog artwork as supplied.

## YT Music fallback normalized separately

The YT Music ecosystem can expose padded/widescreen thumbnails, so fallback artwork requires candidate selection and optional square normalization.

---

# 43. Migration / Compatibility Requirements

When an older project database is opened:

- detect schema version;
- add missing columns/indexes safely;
- preserve existing rows and serials;
- migrate Spotify/source ISRC into canonical `songs.isrc` when possible;
- preserve existing retained MP3 paths;
- never recycle serials;
- do not silently discard previous sidecar associations.

When metadata export format changes, increment metadata export version in sidecars and keep migration/re-embedding tools compatible where practical.

---

# 44. Existing-MP3 Re-Embedding

`scripts/reembed_existing.py` exists for rebuilding metadata on already-produced MP3 files without requiring a fresh audio download when sufficient sidecar/database/source information exists.

Recommended safety flow:

```powershell
python scripts/reembed_existing.py --dry-run
python scripts/reembed_existing.py
```

The re-embed process must create a validated replacement before replacing an existing file.

---

# 45. Operational Workflow for a Windows User

## First installation

1. Install Python 3.11+.
2. Install FFmpeg/FFprobe and make sure they are discoverable.
3. Install a supported JavaScript runtime if yt-dlp requires it for the chosen extraction path.
4. Extract the project.
5. Configure `config.json`.
6. Put optional YT Music cookies in `cookies.txt` if needed.
7. Configure Spotify credentials if Spotify enrichment is enabled.

## Initial verification

```powershell
python main.py --doctor
```

## Start pipeline

```powershell
python main.py
```

## Inspect state

```powershell
python main.py --status
python main.py --check-invariants
```

## Retry errors

```powershell
python main.py --retry-errors
```

---

# 46. Example Final Output

For a song with synced lyrics:

```text
songs/
└── synced_lyrics/
    ├── 001_Urike_Urike_M.M._Sreelekha_Sid_Sriram_Ramya_Behara.mp3
    ├── 001_Urike_Urike_M.M._Sreelekha_Sid_Sriram_Ramya_Behara.lrc
    └── 001_Urike_Urike_M.M._Sreelekha_Sid_Sriram_Ramya_Behara.json
```

For a song without synced lyrics:

```text
songs/
└── no_synced_lyrics/
    ├── 002_Another_Song_Artist.mp3
    └── 002_Another_Song_Artist.json
```

The MP3 contains concise metadata/identities/cover/lyrics.

The JSON contains the complete detailed record.

---

# 47. Example Main MP3 Metadata

A representative enriched MP3 can expose:

```text
Title                         Urike Urike
Artist                        M.M. Sreelekha; Sid Sriram; Ramya Behara
Album                         Urike Urike (From "Hit 2")
Album Artist                  catalog/source value
Release Date                  2022-11-10
Genre                         Music
Composer                      M.M. Sreelekha
Publisher                     catalog/source label
Copyright                     catalog/source copyright
Duration                      actual downloaded audio duration
ISRC                          Spotify ISRC when available

YT Music ID                   S072HjMJZY0
YT Music URL                  https://music.youtube.com/watch?v=S072HjMJZY0

YouTube Video ID              A3Im3P0--aE
YouTube Video URL             https://www.youtube.com/watch?v=A3Im3P0--aE
YouTube Video Title           Urike Urike - Video Song ...

Spotify Track ID              <matched Spotify ID>
Spotify Album ID              <matched Spotify album ID>
Spotify Track URL             <matched Spotify URL>

Artwork                       embedded APIC
Lyrics                        synchronized SYLT + USLT
```

The exact values vary by the actual source/API response.

---

# 48. Example Detailed Sidecar Structure

Conceptually:

```json
{
  "schema_version": "...",
  "playlist": {
    "serial_number": 1,
    "playlist_position": 1,
    "ytm_playlist_id": "...",
    "ytm_video_id": "...",
    "ytm_url": "..."
  },
  "song": {
    "title": "...",
    "artist": "...",
    "artists": ["..."],
    "album": "...",
    "album_artist": "...",
    "release_date": "...",
    "duration": 276
  },
  "isrc": {
    "canonical": "...",
    "source": "spotify"
  },
  "spotify": {
    "matched": true,
    "track_id": "...",
    "album_id": "...",
    "isrc": "...",
    "raw_track": {},
    "raw_album": {},
    "artwork": {}
  },
  "youtube_video": {
    "selected": true,
    "video_id": "...",
    "url": "...",
    "title": "...",
    "raw_info": {}
  },
  "lyrics": {
    "status": "synced",
    "lrclib_id": 123,
    "lrc_path": "...",
    "synced_lyrics": "[00:...] ...",
    "raw_response": {}
  },
  "artwork": {
    "provider": "spotify",
    "source_url": "...",
    "width": 640,
    "height": 640,
    "sha256": "..."
  },
  "source": {
    "ytm_playlist_item": {},
    "yt_dlp_info": {}
  },
  "files": {
    "mp3_path": "...",
    "mp3_size": 12345678,
    "mp3_sha256": "...",
    "json_path": "...",
    "lrc_path": "..."
  }
}
```

The exact implementation may include additional fields from the current normalized model.

---

# 49. Full Current Module API Inventory

The current implementation contains these major callable/class responsibilities:

## Artwork module

- `ArtworkError`
- `ArtworkCandidate`
- square/area/provider utilities
- OpenGraph retrieval
- thumbnail candidate discovery
- artwork selection
- normalization
- Spotify artwork download

## Playlist DB

- initialization
- schema migration
- transactions
- max serial
- all rows
- serial lookup
- next pending
- insert/update
- retry errors
- counts
- integrity assertions

## Songs DB

- initialization
- schema migration
- transactions
- insert/update
- ISRC lookup
- serial lookup
- list/count
- integrity assertions

## Downloader

- yt-dlp command builder
- executable/runtime argument handling
- acquisition
- source artifact validation

## Duplicate checker

- ISRC duplicate model
- ISRC lookup

## Embedder

- text conversion
- TXXX
- WXXX
- UFID
- LRC parsing
- lyrics embedding
- core metadata embedding
- final MP3 embedding

## LRCLIB

- GET request
- throttle
- duration comparison
- synced lyric validation
- result normalization
- lookup

## Metadata

- load/dump source JSON
- string/list/date/integer/bool helpers
- ISRC normalization
- album/artist extraction
- URL parsing
- metadata merge
- canonical metadata normalization
- metadata field list

## Pipeline

- duplicate resolver
- project-path resolution
- final filename construction
- playlist record construction
- old serial cleanup
- one-entry processing
- run loop
- invariant checks
- DB coordinator commits

## Playlist ingestion

- artist extraction
- duration conversion
- album extraction
- URL construction
- playlist track extraction
- existing-entry matching
- playlist retrieval
- track retrieval
- ingestion

## Sidecar

- JSON-safe conversion
- sidecar construction
- write/read

## Spotify

- token retrieval
- HTTP request
- artist/album/image helpers
- track search
- metadata overlay

## YouTube finder

- exact search query
- JSON result parsing
- publication fields
- result selection

---

# 50. Quality Gates Before Release

Before calling a build final:

## Source quality

- compile all Python modules;
- no dead/duplicate path handling;
- no accidental `/api/search` usage;
- no accidental non-ISRC duplicate matcher;
- no accidental raw metadata dumping into MP3;
- no credential leakage.

## Tests

- entire test suite passes;
- extracted archive passes tests again;
- current DB schema initializes from empty;
- previous schema migrates;
- invariants pass.

## Manual sample inspection

At least one real MP3 should be inspected using `scripts/inspect_mp3.py` and an external tag viewer/player to confirm:

- title/artist/album display correctly;
- YT Music ID is present;
- YouTube video ID is present;
- Spotify IDs/ISRC are present when matched;
- artwork is correct;
- synchronized lyrics are present when LRCLIB returns them;
- no unwanted verbose fields clutter the main tag display.

## Live integration smoke test

Run on a real Windows machine with:

- valid YTMusic playlist/auth;
- FFmpeg/FFprobe;
- supported yt-dlp JS runtime;
- Spotify credentials if enabled;
- network access to LRCLIB.

---

# 51. Out of Scope

Unless explicitly added later, the following remain outside this phase:

- Demucs vocal separation
- Whisper transcription
- MMS/speech models
- instrumental extraction
- manual metadata editor UI
- audio hashing as duplicate identity
- fuzzy duplicate matching
- title/artist/duration fallback duplicate matching
- generalized YouTube candidate scoring
- automatic editorial judgment about officialness beyond the specified selection rule
- Spotify audio downloading
- LRCLIB `/api/search`
- lyrics other than accepted synchronized lyrics for `.lrc` output

---

# 52. Historical Changes From the Original Plan

The original specification established the permanent serial architecture, two databases, one yt-dlp acquisition, Mutagen-only final tag authority, ISRC duplicate identity, deterministic YouTube filtering, validation, hashing, and cleanup. fileciteturn0file0L15-L38

Subsequent requirements changed/enhanced the system as follows:

1. **Unavailable YTMusic entries:** `videoId=None` is now a recoverable playlist-entry condition rather than an ingestion-fatal exception.
2. **Spotify enrichment:** optional catalog enrichment was added.
3. **Spotify artwork:** largest Spotify album artwork is now preferred and preserved unchanged.
4. **LRCLIB:** only `/api/get` is used and synchronized lyrics are accepted.
5. **Lyrics output:** `.lrc` files are created only for synced lyrics; synced lyrics are also embedded in the MP3.
6. **Sidecar JSON:** every MP3 gets a same-basename detailed JSON record.
7. **MP3 metadata:** verbose source/API material was moved out of the main ID3 tag set; only concise player-facing metadata and important IDs/URLs remain.
8. **ISRC duplicate logic:** the duplicate identifier is restored as ISRC-only, with Spotify ISRC preferred and source ISRC as fallback.

The current build must be understood through the active rules in this document; the original plan is retained below as a historical appendix.

---

# 53. Implementation Principles for Future Changes

Any future modification should preserve these core contracts unless explicitly revising them:

- playlist serials are permanent;
- source identity and playlist occurrence identity remain separate;
- Spotify remains an optional enrichment layer;
- actual audio remains the YT Music source;
- YouTube video remains enrichment only;
- ISRC remains the sole duplicate identifier;
- no `/api/search` for LRCLIB;
- only synchronized lyrics create `.lrc` files;
- the main MP3 remains concise;
- the sidecar remains the detailed provenance store;
- final metadata authority remains Mutagen;
- final MP3 validation occurs before DB completion;
- hashes are computed on final bytes;
- replacement never destroys a previous valid retained song prematurely.

---

# 54. Final Release Checklist

```text
[ ] config.json reviewed
[ ] Spotify credentials configured or disabled intentionally
[ ] cookies configured if needed
[ ] FFmpeg available
[ ] FFprobe available
[ ] JS runtime available/recognized
[ ] python main.py --doctor passes
[ ] databases initialized/migrated
[ ] playlist ingestion tested
[ ] unavailable item handling tested
[ ] Spotify match tested
[ ] Spotify artwork tested
[ ] ISRC duplicate detection tested
[ ] YouTube search tested
[ ] LRCLIB /api/get-only behavior tested
[ ] synced lyrics embedded
[ ] synced .lrc written
[ ] unsynced song correctly routed
[ ] JSON sidecar created
[ ] final MP3 validation passes
[ ] SHA-256 stored
[ ] atomic finalization passes
[ ] DB invariants pass
[ ] extracted release archive passes tests
```

---

# Appendix A — Historical Original Specification

> The following is preserved verbatim from the original `pipeline.md`. It is historical because later user requirements introduced Spotify enrichment, LRCLIB, detailed JSON sidecars, updated artwork behavior, and restored ISRC duplicate detection. The appendix exists so the complete original plan remains available without information loss.

---

# PHASE 1 — YOUTUBE MUSIC PLAYLIST DOWNLOADER & ENRICHER

**Status:** Final project specification

**Purpose:** Implementation-ready specification for Phase 1 of the YouTube Music playlist downloader/enricher.

---

## 0. DOCUMENT PURPOSE

This document is the complete Phase 1 project plan.

It consolidates the final architecture and all decisions established during planning:

- `playlist.db` is the permanent playlist record.
- `songs.db` contains the currently retained/downloaded songs.
- Every playlist entry receives one permanent, unique serial number.
- The serial number never changes and is never reused.
- The same serial number is used in `songs.db` when that playlist entry owns the retained song.
- Playlist order is captured from the YTMusic API in its returned/default order.
- Processing happens one playlist entry at a time.
- The initial acquisition uses one complete `yt-dlp` command to obtain the audio, complete `info.json`, and artwork.
- The initial `yt-dlp` command does **not** use `--add-metadata`.
- Python/Mutagen is the single authority responsible for final ID3 metadata and artwork embedding.
- ISRC is the only duplicate-detection key.
- Missing ISRC is stored as `NULL` and does not trigger duplicate detection.
- Duplicate handling never removes a playlist entry from `playlist.db`.
- When a duplicate is detected, the user chooses whether to keep the previous retained song or the current downloaded song.
- YouTube video discovery uses exactly the query `{title} {album_name} official video song`.
- YouTube results are used in returned order.
- Any result whose title contains the word `lyrics` is skipped, case-insensitively.
- The first remaining result is selected immediately.
- There is no YouTube result scoring, ranking, confidence system, or manual weighting.
- The final MP3 contains rich metadata, artwork, source identity, and selected YouTube music-video metadata.
- The final MP3 is validated before it is committed to `songs.db`.
- The final MP3 receives a SHA-256 hash after all metadata and artwork have been written.
- Temporary files are removed only after successful finalization.
- Lyrics, Demucs, Whisper, MMS, vocal separation, instrumental extraction, and manual metadata editing are outside Phase 1.

This document is intentionally detailed so that the final implementation can be built directly from it without redesigning the architecture during coding.

---

# 1. PROJECT OVERVIEW

## 1.1 Objective

Build a standalone automated pipeline that takes one YouTube Music playlist and produces a local library of richly tagged MP3 files while maintaining a permanent record of every playlist entry.

The project is not simply an audio downloader. It is a playlist ingestion, identity, metadata, duplicate-management, video-enrichment, and MP3 finalization system.

The complete lifecycle is:

```text
YouTube Music Playlist
        |
        v
YTMusic API ingestion
        |
        v
Preserve returned playlist order
        |
        v
Assign permanent serial numbers
        |
        v
playlist.db
        |
        v
Select next pending entry
        |
        v
ONE complete yt-dlp acquisition
        |
        +-------------------+-------------------+
        |                   |                   |
        v                   v                   v
     master.mp3       master.info.json      master.jpg
        |                   |                   |
        +-------------------+-------------------+
                            |
                            v
                  Source validation
                            |
                            v
                  Metadata extraction
                            |
                            v
                    Metadata normalization
                            |
                            v
                       Extract ISRC
                            |
                            v
                    Search songs.db by ISRC
                            |
                 +----------+----------+
                 |                     |
                 v                     v
             no match              duplicate
                 |                     |
                 |                Ask user:
                 |              previous/current
                 |                     |
                 +----------+----------+
                            |
                            v
                 YouTube video search
                            |
                            v
        {title} {album_name} official video song
                            |
                            v
                Skip titles containing lyrics
                            |
                            v
                First remaining result
                            |
                            v
                  Retrieve video metadata
                            |
                            v
                 Use already-downloaded artwork
                            |
                            v
                    Build final MP3
                            |
                            v
                Mutagen writes ALL final tags
                            |
                            v
                     Validate MP3
                            |
                            v
                    Calculate SHA-256
                            |
                            v
                   Atomic final rename
                            |
                            v
                       songs.db
                            |
                            v
                playlist.db -> completed
                            |
                            v
                       Cleanup temp
                            |
                            v
                   Process next pending
```

---

# 2. SCOPE

## 2.1 Phase 1 INCLUDES

- YouTube Music playlist ingestion through `ytmusicapi`.
- Preservation of playlist ordering.
- Permanent serial assignment.
- Two SQLite databases.
- Pending/completed/duplicate/error processing states.
- Direct download of the selected YTMusic source audio.
- Detailed source metadata acquisition using `yt-dlp` `info.json`.
- Artwork acquisition during the same initial `yt-dlp` run.
- ISRC extraction and normalization.
- ISRC-only duplicate detection.
- User-controlled duplicate resolution.
- YouTube official music-video search.
- Simple result filtering based only on the presence of `lyrics` in the result title.
- Selection of the first acceptable result.
- Final metadata normalization.
- Final ID3 tag writing with Mutagen.
- Artwork embedding with Mutagen.
- YouTube metadata embedding using custom ID3 `TXXX` frames.
- Final MP3 validation.
- SHA-256 file hashing.
- SQLite state tracking.
- Temporary working-directory cleanup.
- Recovery from errors through status tracking.

## 2.2 Phase 1 EXCLUDES

- Lyrics retrieval.
- Lyrics embedding.
- Demucs processing.
- Whisper transcription.
- MMS or other speech/audio models.
- Vocal/instrumental separation.
- Manual metadata editing UI.
- Fuzzy duplicate matching.
- Title/artist/duration duplicate matching.
- YouTube candidate scoring.
- YouTube candidate ranking.
- YouTube confidence scoring.
- Automatic editorial judgment about which video is "most official" beyond the specified search rule.

---

# 3. CORE DESIGN PRINCIPLES

## 3.1 Playlist Entry Identity

A playlist entry is a specific occurrence inside the playlist.

Its permanent identity is the serial number.

Example:

```text
001 -> Song A
002 -> Song B
003 -> Song A
```

`001` and `003` are two different playlist entries.

They may refer to the same recording, but they remain different playlist identities.

## 3.2 Serial Number Rules

The serial number:

1. Is assigned once.
2. Is assigned in the order returned by the playlist ingestion process.
3. Is never reused.
4. Is never changed.
5. Is never deleted from `playlist.db`.
6. Is not replaced by ISRC.
7. Is not replaced by YTM video ID.
8. Is used in `songs.db` when the playlist entry owns the current retained song.
9. Is used in the final MP3 filename.
10. Is used when linking physical files back to playlist entries.

## 3.3 Playlist Database vs Song Database

`playlist.db` answers:

> What entries exist in my playlist, and what is their processing state?

`songs.db` answers:

> Which playlist entries currently have retained downloaded songs, and what are the metadata and file details of those retained songs?

They are deliberately separate.

## 3.4 Duplicate Identity

Only ISRC is a duplicate key.

If ISRC is missing:

```text
isrc = NULL
```

No other duplicate matching is attempted.

## 3.5 Final Metadata Authority

`yt-dlp` obtains source material and source metadata.

`yt-dlp` does **not** write the final MP3 metadata.

Mutagen is the single authority for final ID3 metadata.

This prevents competing metadata writers.

---

# 4. SYSTEM ARCHITECTURE

```text
                       +----------------------+
                       | YouTube Music        |
                       | Playlist             |
                       +----------+-----------+
                                  |
                                  v
                       +----------------------+
                       | YTMusic API           |
                       | Playlist ingestion    |
                       +----------+-----------+
                                  |
                                  v
                       +----------------------+
                       | playlist.db           |
                       | Permanent serials     |
                       | Status                |
                       +----------+-----------+
                                  |
                                  v
                       +----------------------+
                       | Processing Queue      |
                       | Next pending serial   |
                       +----------+-----------+
                                  |
                                  v
                       +----------------------+
                       | yt-dlp                |
                       | ONE initial command   |
                       +----------+-----------+
                                  |
                   +--------------+--------------+
                   |              |              |
                   v              v              v
              master.mp3    master.info.json  master.jpg
                   |              |              |
                   +--------------+--------------+
                                  |
                                  v
                       +----------------------+
                       | Metadata Normalizer   |
                       +----------+-----------+
                                  |
                                  v
                       +----------------------+
                       | ISRC Duplicate Check |
                       | songs.db              |
                       +----------+-----------+
                                  |
                         +--------+--------+
                         |                 |
                         v                 v
                       unique          duplicate
                         |                 |
                         |            user decision
                         |                 |
                         +--------+--------+
                                  |
                                  v
                       +----------------------+
                       | YouTube Search       |
                       | title + album +      |
                       | official video song  |
                       +----------+-----------+
                                  |
                                  v
                       +----------------------+
                       | First result whose  |
                       | title lacks lyrics   |
                       +----------+-----------+
                                  |
                                  v
                       +----------------------+
                       | Metadata / Artwork   |
                       | preparation          |
                       +----------+-----------+
                                  |
                                  v
                       +----------------------+
                       | Mutagen              |
                       | Final ID3 writer     |
                       +----------+-----------+
                                  |
                                  v
                       +----------------------+
                       | Final MP3 validation |
                       +----------+-----------+
                                  |
                                  v
                       +----------------------+
                       | SHA-256              |
                       +----------+-----------+
                                  |
                                  v
                       +----------------------+
                       | Atomic finalization  |
                       +----------+-----------+
                                  |
                         +--------+--------+
                         |                 |
                         v                 v
                    songs.db         playlist.db
                                     status=completed
```

---

# 5. PROJECT DIRECTORY STRUCTURE

```text
phase1_project/
│
├── main.py                         # Application entry point
├── config.json                     # User configuration
├── cookies.txt                     # Optional YouTube cookies
├── requirements.txt                # Python dependencies
├── README.md                       # Project documentation
│
├── src/
│   ├── __init__.py
│   ├── db_playlist.py              # playlist.db schema and helpers
│   ├── db_songs.py                 # songs.db schema and helpers
│   ├── playlist_ingest.py          # YTM playlist ingestion
│   ├── downloader.py               # yt-dlp acquisition
│   ├── metadata.py                 # info.json extraction/normalization
│   ├── duplicate_checker.py        # ISRC-only duplicate logic
│   ├── youtube_finder.py           # simple YouTube search/filter
│   ├── embedder.py                 # Mutagen ID3 writer
│   ├── validator.py                # final MP3 validation
│   ├── hashing.py                  # SHA-256 calculation
│   └── pipeline.py                 # end-to-end orchestration
│
├── songs/
│   └── original/                   # Retained final MP3 files
│
├── temp/                            # Per-entry temporary working folders
│
└── db/
    ├── playlist.db
    └── songs.db
```

---

# 6. CONFIGURATION

Suggested `config.json`:

```json
{
    "ytmusic_playlist_id": "YOUR_PLAYLIST_ID",

    "paths": {
        "songs": "songs/original",
        "temp": "temp",
        "database": "db"
    },

    "download": {
        "audio_format": "mp3",
        "audio_quality": "0",
        "write_info_json": true,
        "write_thumbnail": true,
        "convert_thumbnail": "jpg"
    },

    "youtube_video_search": {
        "results_to_fetch": 10,
        "skip_title_keyword": "lyrics"
    },

    "retry": {
        "max_attempts": 3
    }
}
```

Configuration values must not change the architectural rules.

In particular:

- duplicate matching remains ISRC-only;
- YouTube result scoring remains disabled;
- `lyrics` remains the only search-result title exclusion keyword defined by Phase 1;
- final ID3 writing remains Mutagen-only.

---

# 7. REQUIRED SOFTWARE

The implementation requires:

```text
Python
ytmusicapi
yt-dlp
mutagen
requests
FFmpeg
FFprobe
```

The runtime should also include whatever JavaScript-runtime support is required by the installed `yt-dlp` version for current YouTube extraction.

Dependency versions should be pinned in `requirements.txt` once implementation begins.

Example structure:

```text
yt...==...
yt-dlp==...
mutagen==...
requests==...
```

The exact versions should be selected at implementation time and tested together rather than being casually mixed.

---

# 8. DATABASE SCHEMA — `playlist.db`

## 8.1 Table

```sql
CREATE TABLE IF NOT EXISTS playlist_entries (
    serial_number INTEGER PRIMARY KEY,

    playlist_position INTEGER NOT NULL,

    ytm_video_id TEXT NOT NULL,
    ytm_url TEXT NOT NULL,

    title TEXT NOT NULL,
    artist TEXT NOT NULL,
    duration INTEGER,

    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (
            status IN (
                'pending',
                'completed',
                'duplicate',
                'error'
            )
        ),

    error_message TEXT,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

## 8.2 Why `ytm_video_id` is not UNIQUE

The same YTMusic track can appear more than once in a playlist.

Example:

```text
001 | Song A
002 | Song B
003 | Song A
```

Both `001` and `003` must survive as separate playlist entries.

Therefore:

```text
ytm_video_id -> not the permanent identity
serial_number -> permanent identity
```

## 8.3 `playlist_position`

`playlist_position` records the position returned by the playlist ingestion.

It is deliberately different from `serial_number`.

The serial is permanent.

The position describes playlist order.

This allows the project to preserve identity while still recording ordering information.

---

# 9. DATABASE SCHEMA — `songs.db`

## 9.1 Table

```sql
CREATE TABLE IF NOT EXISTS songs (
    serial_number INTEGER PRIMARY KEY,

    -- Core song metadata
    title TEXT NOT NULL,
    title_original TEXT,

    primary_artist TEXT,
    artist TEXT,
    artists_json TEXT,
    album TEXT,
    album_artist TEXT,

    -- Dates
    release_date TEXT,
    release_date_source TEXT,
    upload_date TEXT,

    -- Music identifiers
    isrc TEXT,
    isrc_source TEXT,

    -- Audio information
    duration INTEGER,
    source_duration INTEGER,
    source_codec TEXT,
    source_bitrate INTEGER,
    source_sample_rate INTEGER,
    source_channels INTEGER,

    -- Original YouTube Music source
    ytm_video_id TEXT,
    ytm_url TEXT,

    -- Final retained file
    mp3_path TEXT NOT NULL,
    mp3_size INTEGER,
    mp3_sha256 TEXT,

    -- Selected YouTube music video
    yt_video_id TEXT,
    yt_video_url TEXT,
    yt_video_title TEXT,
    yt_video_channel TEXT,
    yt_video_channel_id TEXT,
    yt_video_views INTEGER,
    yt_video_published_at TEXT,

    -- Search method
    yt_video_match_method TEXT,

    -- Working/source artwork path when retained in metadata
    artwork_path TEXT,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

## 9.2 ISRC Index

```sql
CREATE INDEX IF NOT EXISTS idx_songs_isrc
ON songs(isrc)
WHERE isrc IS NOT NULL;
```

Only non-NULL ISRC values are indexed for duplicate detection.

---

# 10. DATABASE INVARIANTS

The implementation must maintain these rules at all times after a successful commit.

## 10.1 Serial uniqueness

A serial appears at most once in `playlist.db`.

A serial appears at most once in `songs.db`.

## 10.2 Completed relationship

If:

```text
playlist.db.status = completed
```

then there must be:

```text
songs.db.serial_number = same serial
```

and the referenced MP3 must exist and be valid.

## 10.3 Duplicate relationship

If:

```text
playlist.db.status = duplicate
```

then that playlist entry does not own a retained song row in `songs.db`.

## 10.4 Pending relationship

If:

```text
playlist.db.status = pending
```

then it should not currently have a retained `songs.db` row for that serial.

## 10.5 Error relationship

If:

```text
playlist.db.status = error
```

there is no requirement for a retained `songs.db` row for that serial.

---

# 11. STATUS STATE MACHINE

The basic state machine is:

```text
                 +-----------+
                 |  pending  |
                 +-----+-----+
                       |
                 start processing
                       |
                       v
               download/process
                       |
          +------------+-------------+
          |                          |
          v                          v
       success                    failure
          |                          |
          v                          v
     duplicate check              error
          |                          |
    +-----+------+                   |
    |            |                   |
    v            v                   |
  unique      duplicate              |
    |            |                   |
    |        user decision           |
    |         /       \              |
    |        v         v             |
    |   previous     current         |
    |      |           |             |
    |      v           v             |
    |  duplicate   completed         |
    |                  |             |
    v                  |             |
 completed <-----------+-------------+
```

Additional transition:

```text
old completed entry
       |
 current duplicate chooses "keep current"
       |
       v
old entry -> pending
current entry -> completed
```

And retry:

```text
error -> pending
```

---

# 12. STEP 1 — PLAYLIST INGESTION

## 12.1 Read configuration

Read:

```text
yt...music_playlist_id
```

from `config.json`.

## 12.2 Initialize YTMusic

Create the `ytmusicapi.YTMusic` client.

Cookies/authentication may be supplied according to the local configuration if required by the environment.

## 12.3 Retrieve playlist

Retrieve the complete playlist using the YTMusic API.

The project must preserve the order returned by the API.

Do not sort alphabetically.

Do not sort by artist.

Do not sort by duration.

Do not sort by popularity.

Do not sort by ISRC.

The playlist order is authoritative for initial serial assignment.

## 12.4 Extract entry fields

For each returned track, extract at minimum:

```text
video_id
song title
artist(s)
duration
```

Construct:

```text
https://music.youtube.com/watch?v={video_id}
```

## 12.5 Assign serials

Assign serial numbers sequentially in the ingestion order.

Example:

```text
API order:
1. Song A
2. Song B
3. Song C
4. Song D

serials:
001 -> Song A
002 -> Song B
003 -> Song C
004 -> Song D
```

The serial is stored as an integer in SQLite. Zero-padding is a filename/display convention, not a numeric database requirement.

## 12.6 Initial status

New entries receive:

```text
status = pending
```

## 12.7 Existing database behavior

When the application is restarted, existing playlist entries must not be assigned new serials.

Existing serials remain unchanged.

New playlist entries can receive new serials according to the project's append-only serial-allocation policy.

The implementation must never recycle a previously used serial.

## 12.8 Ingestion summary

Print a summary such as:

```text
Playlist ingestion complete.

Total playlist entries: 120
New entries:            14
Existing entries:       106
Pending entries:        12
Completed entries:      91
Duplicate entries:      2
Error entries:          1
```

---

# 13. STEP 2 — SELECT NEXT PENDING ENTRY

Query `playlist.db` for the next entry where:

```text
status = pending
```

Process in playlist order.

Conceptually:

```sql
SELECT *
FROM playlist_entries
WHERE status = 'pending'
ORDER BY playlist_position ASC, serial_number ASC
LIMIT 1;
```

The serial returned becomes the active processing identity.

Example:

```text
003 | Song C | pending
```

The processing directory becomes:

```text
temp/003/
```

---

# 14. STEP 3 — CREATE WORKING DIRECTORY

Create:

```text
temp/{serial_number}/
```

Example:

```text
temp/003/
```

Everything generated for the current processing attempt belongs inside this directory until finalization.

Expected temporary files after acquisition:

```text
temp/003/
├── master.mp3
├── master.info.json
└── master.jpg
```

Additional yt-dlp/FFmpeg temporary files may exist during execution but should be cleaned after successful completion or failure handling.

---

# 15. STEP 4 — ONE COMPLETE YT-DLP ACQUISITION

This step intentionally acquires everything needed from the YTMusic source in one `yt-dlp` operation.

The command is conceptually:

```bash
yt-dlp \
    -x \
    --audio-format mp3 \
    --audio-quality 0 \
    --write-info-json \
    --write-thumbnail \
    --convert-thumbnails jpg \
    -o "temp/{serial_number}/master.%(ext)s" \
    "{ytm_url}"
```

The exact command-line details may be adjusted during implementation to match the installed `yt-dlp`/FFmpeg environment, but the behavior must remain the same.

## 15.1 Acquisition requirements

The single initial command must obtain:

### A. Audio

The exact selected YTMusic source audio.

### B. Complete source metadata

`master.info.json` containing the complete metadata returned by yt-dlp.

### C. Artwork

The highest-quality available thumbnail/artwork selected by yt-dlp from the source.

## 15.2 No general YouTube audio search

The audio is not found by searching general YouTube.

The source audio URL is the stored YTMusic URL belonging to the selected playlist entry.

## 15.3 No `--add-metadata`

Do **not** use:

```text
--add-metadata
```

The initial yt-dlp output is an acquisition artifact, not the final tagged library file.

## 15.4 Why there is no `--add-metadata`

The project intentionally has one final metadata authority:

```text
yt-dlp -> acquisition
Mutagen -> final MP3 metadata
```

This avoids having yt-dlp write one version of tags and Mutagen later overwrite another version.

---

# 16. EXPECTED INITIAL ACQUISITION RESULT

After a successful acquisition:

```text
temp/003/
├── master.mp3
├── master.info.json
└── master.jpg
```

## `master.mp3`

Contains the acquired audio.

It is not yet considered the final library MP3.

## `master.info.json`

Contains the source metadata.

It is parsed by Python.

## `master.jpg`

Contains the acquired artwork.

It is used later by Mutagen.

No second artwork download is performed.

---

# 17. STEP 5 — SOURCE ACQUISITION VALIDATION

Before proceeding, validate the source package.

## 17.1 Required checks

- `master.mp3` exists.
- `master.info.json` exists.
- `master.info.json` is valid JSON.
- `master.mp3` can be opened/read.
- Audio duration can be obtained.
- `master.jpg` exists when the source supplied usable artwork.
- `master.jpg` can be decoded as an image.

## 17.2 Failure

If a required acquisition item is invalid:

1. record the error;
2. set the playlist entry to `error`;
3. do not insert a `songs.db` row;
4. preserve the serial;
5. allow later retry by moving the entry back to `pending`.

---

# 18. STEP 6 — READ `master.info.json`

Parse the complete JSON object.

Do not throw away the source information before the normalized metadata has been built.

The parser should be defensive because individual fields can be absent or `null`.

Important fields to inspect include:

```text
title
artist
artists
album
album_artist
release_date
upload_date
duration
isrc
description
uploader
channel
channel_id
view_count
thumbnails
codec / format data
abr / bitrate
asr / sample rate
channels
```

The exact names/availability depend on the returned yt-dlp metadata.

---

# 19. STEP 7 — METADATA LAYERS

The system should conceptually maintain three metadata layers.

## Layer A — Raw source metadata

Exactly what is supplied by `master.info.json`.

## Layer B — Normalized application metadata

Cleaned fields that are suitable for databases and final tagging.

Examples:

```text
normalized_title
normalized_artist
normalized_album
normalized_isrc
normalized_release_date
```

## Layer C — Final MP3 metadata

The exact ID3 frames written by Mutagen.

This separation makes the system easier to debug.

---

# 20. STEP 8 — CORE SONG METADATA

Extract, where available:

```text
title
title_original
primary_artist
artist
artists_json
album
album_artist
```

## 20.1 Title

`title` is the cleaned display title.

`title_original` preserves the original source title when useful.

## 20.2 Artist

`artist` is the display string written to the main artist field.

`artists_json` can preserve multiple artist contributors without flattening information unnecessarily.

Example:

```json
[
  "Artist A",
  "Artist B"
]
```

## 20.3 Album

Store the album exactly when supplied.

If unavailable:

```text
album = NULL
```

## 20.4 Album Artist

Store album artist separately from track artist when available.

---

# 21. STEP 9 — DATE METADATA

Store:

```text
release_date
release_date_source
upload_date
```

Do not silently treat upload date as release date.

If a real release date is unavailable:

```text
release_date = NULL
```

The source upload date remains separately available as `upload_date`.

---

# 22. STEP 10 — ISRC EXTRACTION

ISRC is extracted from the structured metadata when available.

The application may normalize the representation, for example by trimming whitespace and normalizing case.

If no ISRC is available:

```text
isrc = NULL
isrc_source = NULL
```

Do not invent one.

Do not infer one from unrelated metadata.

Do not generate an internal pseudo-ISRC.

---

# 23. ISRC-ONLY DUPLICATE RULE

This is a locked design rule.

The duplicate checker must only do:

```text
current.isrc != NULL
        |
        v
find songs.db where songs.isrc == current.isrc
```

No other duplicate heuristic is permitted.

Do NOT duplicate-match using:

- title;
- artist;
- album;
- duration;
- YTM video ID;
- YouTube video ID;
- filename;
- audio hash;
- fuzzy title similarity;
- normalized artist/title combinations.

If `isrc = NULL`, the song is simply treated as having no ISRC-based duplicate match.

---

# 24. STEP 11 — DUPLICATE SEARCH

Example current entry:

```text
Serial: 004
Title: Song A
Artist: Artist A
ISRC: USABC1234567
```

Query:

```sql
SELECT *
FROM songs
WHERE isrc = ?
LIMIT 1;
```

If there is no row:

```text
unique -> continue
```

If there is a row:

```text
duplicate -> ask user
```

If the current ISRC is `NULL`:

```text
no duplicate check
```

---

# 25. STEP 12 — DUPLICATE USER DECISION

When a duplicate exists, show both entries clearly.

Example:

```text
DUPLICATE DETECTED

CURRENT ENTRY
Serial: 004
Title: Song A
Artist: Artist A
Album: Album X
ISRC: USABC1234567

EXISTING RETAINED ENTRY
Serial: 001
Title: Song A
Artist: Artist A
Album: Album X
ISRC: USABC1234567

Choose:

1. Keep previous
2. Keep current
```

No automatic winner is chosen.

---

# 26. DUPLICATE OPTION 1 — KEEP PREVIOUS

User selects:

```text
1
```

Actions:

1. Do not modify the previous retained song.
2. Do not delete the previous MP3.
3. Do not modify its `songs.db` row.
4. Delete the current temporary acquisition:
   - `master.mp3`;
   - `master.info.json`;
   - `master.jpg`;
   - any other temporary files.
5. Do not create a current `songs.db` row.
6. Set the current playlist entry to:

```text
status = duplicate
```

Example:

```text
playlist.db

001 | Song A | completed
004 | Song A | duplicate
```

`songs.db`:

```text
001 | Song A
```

Serial `004` remains permanently present in `playlist.db`.

---

# 27. DUPLICATE OPTION 2 — KEEP CURRENT

User selects:

```text
2
```

The current playlist entry becomes the retained song.

Actions:

1. Identify the previous `songs.db` row.
2. Read its `mp3_path`.
3. Delete the previous physical MP3.
4. Delete the previous row from `songs.db`.
5. Continue processing the current temporary acquisition.
6. Search YouTube for the current entry.
7. Build the current final MP3.
8. Validate it.
9. Insert current serial into `songs.db`.
10. Set current playlist entry to `completed`.
11. Set previous playlist entry to `pending`.

Example before:

```text
playlist.db

001 | Song A | completed
004 | Song A | pending
```

`songs.db`:

```text
001 | Song A
```

After choosing current:

```text
playlist.db

001 | Song A | pending
004 | Song A | completed
```

`songs.db`:

```text
004 | Song A
```

The old playlist entry `001` remains permanently assigned to serial `001`.

---

# 28. IMPORTANT DUPLICATE BEHAVIOR

A duplicate decision changes the retained song record, not playlist identity.

The system never does:

```text
rename serial 001 to 004
```

It instead does:

```text
remove retained song 001
retain playlist entry 001 as pending
retain song 004
```

This preserves the playlist history/identity while allowing the currently retained recording to move to the playlist entry chosen by the user.

---

# 29. STEP 13 — YOUTUBE OFFICIAL MUSIC VIDEO SEARCH

After the song has been acquired and duplicate handling has been resolved, perform the YouTube search.

The exact search string is:

```text
{title} {album_name} official video song
```

Use the normalized/current song title and album name being used for enrichment.

If the album is missing, the implementation should follow the exact established query construction rules used by the application rather than inventing a new search strategy.

---

# 30. NO SEARCH SCORING

The video finder must NOT:

- calculate a candidate score;
- compare view counts;
- compare durations;
- compare channels;
- calculate title similarity;
- calculate artist similarity;
- calculate confidence;
- rank candidate videos after retrieval;
- choose the most viewed result;
- choose the closest duration result.

The returned order is authoritative for this Phase 1 selection method.

---

# 31. STEP 14 — YOUTUBE RESULT FILTERING

Retrieve the search results in their returned order.

For each result:

1. Read its title.
2. Convert the title to a case-insensitive comparison form.
3. If the title contains the word:

```text
lyrics
```

skip that result.

4. Otherwise select it immediately.

Example:

```text
1. Song Name - Lyrics Video
2. Song Name - Official Video
3. Song Name - Live Performance
```

Result 1:

```text
lyrics -> skip
```

Result 2:

```text
no lyrics -> select immediately
```

Result 3 is never considered after selection.

---

# 32. NO ACCEPTABLE YOUTUBE RESULT

If all returned results contain `lyrics`, then no acceptable video is selected.

Store:

```text
yt_video_id = NULL
yt_video_url = NULL
yt_video_title = NULL
yt_video_channel = NULL
yt_video_channel_id = NULL
yt_video_views = NULL
yt_video_published_at = NULL
yt_video_match_method = NULL
```

The song can still be successfully completed.

Failure to find an acceptable video is **not** a failure of the song download.

---

# 33. STEP 15 — FETCH SELECTED YOUTUBE VIDEO METADATA

For the selected result, retrieve/store:

```text
yt_video_id
yt_video_url
yt_video_title
yt_video_channel
yt_video_channel_id
yt_video_views
yt_video_published_at
```

Set:

```text
yt_video_match_method = youtube_search
```

No score or confidence field is needed.

---

# 34. STEP 16 — ARTWORK HANDLING

The artwork is already available from the initial yt-dlp command:

```text
temp/{serial_number}/master.jpg
```

Do not download artwork again.

Do not perform a second yt-dlp command just for artwork.

Do not fetch another image source unless the implementation explicitly establishes that the initial acquisition failed to produce usable artwork and a separate fallback is added later as a deliberate scope change.

For the current Phase 1 specification, the intended artwork source is `master.jpg` from the initial acquisition.

---

# 35. STEP 17 — FINAL FILENAME

The final MP3 filename uses the permanent serial number.

Format:

```text
{serial:03d}_{safe_title}_{safe_artist}.mp3
```

Example:

```text
004_Song_A_Artist_A.mp3
```

Directory:

```text
songs/original/
```

Full path:

```text
songs/original/004_Song_A_Artist_A.mp3
```

Filename sanitization must remove or replace filesystem-invalid characters while preserving as much readable information as possible.

The serial number must never be removed.

---

# 36. STEP 18 — FINAL MP3 BUILD STRATEGY

The initial `master.mp3` is the acquired audio source.

The final output should be created as a temporary production file:

```text
songs/original/004_Song_A_Artist_A.mp3.tmp
```

The final metadata-writing stage then writes all desired tags into that file.

After validation:

```text
.mp3.tmp
    |
    | atomic rename
    v
.mp3
```

---

# 37. FINAL METADATA WRITER — MUTAGEN ONLY

Mutagen is the sole final metadata writer.

No `yt-dlp --add-metadata`.

No second metadata utility should overwrite the final tags after Mutagen.

The logical sequence is:

```text
source acquisition
    -> raw metadata
    -> normalization
    -> build final MP3
    -> Mutagen ID3 write
    -> artwork APIC write
    -> custom TXXX write
    -> save
    -> reopen
    -> validate
```

---

# 38. STANDARD ID3 FIELDS

The final MP3 should contain, when values are available:

```text
TIT2 -> Title
TPE1 -> Primary Artist / Track Artist
TPE2 -> Album Artist
TALB -> Album
TDRC -> Release Date
TRCK -> Album Track Number, when actually known
TSRC -> ISRC, when available
```

Do not use playlist serial number as `TRCK`.

The serial number is playlist identity, not album track numbering.

---

# 39. PLAYLIST-SPECIFIC CUSTOM FIELDS

The final MP3 may include playlist/source identity using `TXXX` frames.

Recommended fields:

```text
TXXX:serial_number
TXXX:playlist_position
TXXX:ytm_video_id
TXXX:ytm_url
```

These fields preserve playlist context inside the MP3.

---

# 40. YOUTUBE MUSIC VIDEO CUSTOM FIELDS

Embed selected video details using `TXXX` frames:

```text
TXXX:yt_video_id
TXXX:yt_video_url
TXXX:yt_video_title
TXXX:yt_video_channel
TXXX:yt_video_channel_id
TXXX:yt_video_views
TXXX:yt_video_published_at
```

If no acceptable video is found, those fields should be omitted or left absent rather than being filled with fake values.

---

# 41. ISRC TAGGING

If ISRC exists:

```text
TSRC = normalized ISRC
```

If ISRC is `NULL`:

```text
TSRC is omitted
```

Do not write the string `NULL` into `TSRC`.

---

# 42. ARTWORK EMBEDDING

Use:

```text
temp/{serial_number}/master.jpg
```

Embed it as the front-cover artwork using the ID3 `APIC` frame.

Recommended:

```text
type = 3
```

for front cover.

The artwork becomes physically embedded in the final MP3.

The final MP3 therefore remains usable without the original temporary artwork file.

---

# 43. SOURCE DESCRIPTION

If desired and technically practical, the source description can be stored in a `COMM` frame.

Example:

```text
COMM -> source description
```

Do not blindly embed extremely large or unsuitable text if it would create unnecessary file bloat.

The structured metadata remains in `songs.db`.

---

# 44. STEP 19 — FINAL MP3 VALIDATION

Validation must happen after all final metadata and artwork have been written.

Do not validate only the original `master.mp3`.

The final validation target is:

```text
songs/original/{serial}_{title}_{artist}.mp3.tmp
```

before its atomic rename.

Required checks:

1. File exists.
2. File size is greater than zero.
3. MP3 can be opened.
4. Audio duration can be read.
5. Title tag exists.
6. Artist tag exists.
7. Album tag exists when source metadata supplied it.
8. Release-date tag is valid when present.
9. ISRC is present when expected.
10. ISRC is omitted when source ISRC is `NULL`.
11. Artwork exists inside the final MP3.
12. Artwork can be decoded/read.
13. YouTube fields are correct when a video was found.
14. YouTube fields are absent/empty when no acceptable video was found.
15. The file is readable after closing and reopening it.

---

# 45. STEP 20 — FINAL FILE HASH

After all metadata and artwork have been written and validation succeeds, calculate:

```text
SHA-256(final MP3 bytes)
```

Store:

```text
mp3_size
mp3_sha256
```

The hash must be calculated on the **final fully tagged MP3**, not `master.mp3`.

If metadata is changed later, the file hash will legitimately change.

---

# 46. STEP 21 — ATOMIC FINALIZATION

The finalization sequence should be:

```text
1. Acquire source
2. Extract metadata
3. Resolve duplicate
4. Find YouTube video
5. Prepare final MP3
6. Write Mutagen tags
7. Embed artwork
8. Save temporary final MP3
9. Reopen final MP3
10. Validate final MP3
11. Calculate file size
12. Calculate SHA-256
13. Rename .tmp -> final .mp3
14. Insert/update songs.db
15. Update playlist.db
16. Delete temp directory
```

The physical final rename should occur only after the final MP3 passes validation.

---

# 47. STEP 22 — `songs.db` COMMIT

For a unique/non-duplicate song:

```text
songs.db
INSERT current serial
```

Store all normalized metadata and final file information.

Example conceptual row:

```text
serial_number      = 004
isrc               = USABC1234567
title              = Song A
artist             = Artist A
album              = Album X
release_date       = 2025-01-01
duration           = 243
mp3_path           = songs/original/004_Song_A_Artist_A.mp3
mp3_size           = 8123456
mp3_sha256         = ...
ytm_video_id       = ...
yt_video_id        = ...
yt_video_title     = ...
yt_video_channel   = ...
```

Then:

```text
playlist.db status = completed
```

---

# 48. STEP 23 — SUCCESSFUL COMPLETION

A song is considered successfully completed only when all of the following are true:

- source acquisition succeeded;
- source metadata was successfully parsed;
- duplicate logic was resolved;
- YouTube search completed or was intentionally left without a match;
- artwork handling completed;
- final MP3 was built;
- Mutagen metadata write succeeded;
- final MP3 validation succeeded;
- SHA-256 was calculated;
- final MP3 was atomically finalized;
- `songs.db` commit succeeded;
- `playlist.db` status was updated to `completed`;
- temporary files can be safely deleted.

---

# 49. STEP 24 — ERROR HANDLING

Any unrecoverable processing failure should result in:

```text
playlist.db.status = error
```

and:

```text
playlist.db.error_message = descriptive error
```

The serial remains unchanged.

No serial is reused.

No playlist entry is deleted.

If a final MP3 was only partially created, it must not be registered as a completed `songs.db` record.

Incomplete `.tmp` output must be cleaned or explicitly handled during the next recovery run.

---

# 50. RETRY MODEL

An entry with:

```text
status = error
```

can be returned to:

```text
status = pending
```

for another processing attempt.

The implementation may track retry count separately if desired.

The serial never changes.

---

# 51. TEMP DIRECTORY CLEANUP

For serial `004`:

```text
temp/004/
```

is removed only after finalization succeeds.

Expected cleanup removes:

```text
master.mp3
master.info.json
master.jpg
any other temporary artifacts
```

The retained production MP3 remains:

```text
songs/original/004_....mp3
```

---

# 52. NORMAL NON-DUPLICATE END-TO-END EXAMPLE

Playlist entry:

```text
Serial: 004
Title: Song A
Artist: Artist A
Album: Album X
Status: pending
```

## Acquisition

```text
temp/004/
├── master.mp3
├── master.info.json
└── master.jpg
```

## Metadata extraction

```text
isrc = USABC1234567
```

## Duplicate lookup

No existing `songs.db` row has that ISRC.

## YouTube search

```text
Song A Album X official video song
```

Results:

```text
1. Song A Lyrics Video
2. Song A Official Video
```

Result 1 is skipped.

Result 2 is selected.

## Final MP3

```text
songs/original/004_Song_A_Artist_A.mp3
```

Mutagen writes:

```text
TIT2
TPE1
TPE2
TALB
TDRC
TSRC
APIC
TXXX:serial_number
TXXX:playlist_position
TXXX:ytm_video_id
TXXX:ytm_url
TXXX:yt_video_id
TXXX:yt_video_url
TXXX:yt_video_title
TXXX:yt_video_channel
...
```

## Final result

```text
playlist.db
004 | completed
```

```text
songs.db
004 | Song A | USABC1234567 | songs/original/004_Song_A_Artist_A.mp3
```

---

# 53. DUPLICATE — KEEP PREVIOUS EXAMPLE

Existing:

```text
playlist.db
001 | Song A | completed
004 | Song A | pending
```

`songs.db`:

```text
001 | Song A | USABC1234567
```

Serial `004` is downloaded.

Its ISRC is:

```text
USABC1234567
```

Duplicate found.

User selects:

```text
1. Keep previous
```

Actions:

```text
Delete temp/004/
Keep songs.db/001
Keep MP3 001
Set playlist 004 -> duplicate
```

Final:

```text
playlist.db
001 | completed
004 | duplicate
```

```text
songs.db
001 | Song A
```

---

# 54. DUPLICATE — KEEP CURRENT EXAMPLE

Existing:

```text
playlist.db
001 | Song A | completed
004 | Song A | pending
```

`songs.db`:

```text
001 | Song A | USABC1234567
```

Serial `004` is downloaded.

Its ISRC matches `001`.

User selects:

```text
2. Keep current
```

Actions:

```text
Delete MP3 belonging to 001
Delete songs.db row 001
Finish processing serial 004
Create final MP3 004
Insert songs.db row 004
Set playlist 004 -> completed
Set playlist 001 -> pending
```

Final:

```text
playlist.db
001 | pending
004 | completed
```

```text
songs.db
004 | Song A
```

Serial `001` remains permanent and available for future processing.

---

# 55. MISSING ISRC EXAMPLE

Suppose:

```text
serial = 005
isrc = NULL
```

The system does:

```text
No ISRC
   |
   v
No songs.db duplicate lookup
   |
   v
Continue normally
```

If the song is successfully finalized:

```text
playlist.db
005 | completed
```

and:

```text
songs.db
005 | isrc = NULL
```

Do not write the literal string `NULL` into the MP3 `TSRC` field.

Do not perform title/artist fallback matching.

---

# 56. YOUTUBE SEARCH EXAMPLE

For:

```text
Title = Song A
Album = Album X
```

Search exactly:

```text
Song A Album X official video song
```

Returned:

```text
1. Song A Lyrics Video
2. Song A Official Video
3. Song A Live
4. Song A Cover
```

Processing:

```text
1 -> contains lyrics -> skip
2 -> does not contain lyrics -> select
3 -> not examined
4 -> not examined
```

No view count is compared.

No duration is compared.

No channel is compared.

---

# 57. FILE NAMING RULES

## Final MP3

```text
{serial:03d}_{safe_title}_{safe_artist}.mp3
```

## Temporary working directory

```text
temp/{serial}/
```

## Temporary final file

```text
songs/original/{serial}_{safe_title}_{safe_artist}.mp3.tmp
```

The serial must always be included.

---

# 58. FILESYSTEM SAFETY

Filename sanitization must:

- remove filesystem-invalid characters;
- prevent unintended directory traversal;
- avoid accidental reserved filenames;
- preserve the serial prefix;
- preserve readable title and artist text where possible.

The sanitized filename must never change the database identity.

---

# 59. DATABASE SAFETY

The implementation should use transactions for state changes that must remain consistent.

Examples:

### Normal completion

```text
BEGIN TRANSACTION
    INSERT songs.db row
    UPDATE playlist.db status = completed
COMMIT
```

### Keep previous duplicate

```text
BEGIN TRANSACTION
    UPDATE playlist.db current serial = duplicate
COMMIT
```

### Keep current duplicate

```text
BEGIN TRANSACTION
    DELETE songs.db previous serial
    INSERT songs.db current serial
    UPDATE playlist.db previous serial = pending
    UPDATE playlist.db current serial = completed
COMMIT
```

Filesystem deletions should be coordinated carefully with database transactions because SQLite transactions cannot roll back a physical file deletion.

Therefore, the implementation should structure operations so that a final valid file exists before a successful database commit whenever possible.

---

# 60. KEEP-CURRENT DUPLICATE SAFETY

The most sensitive operation is replacing the existing retained song.

The implementation should not delete the old MP3 until the current song has successfully passed final validation.

Preferred sequence:

```text
1. Current download exists.
2. Current metadata is valid.
3. Current duplicate is confirmed.
4. User chooses current.
5. Current final MP3 is fully built and validated.
6. Current file is ready.
7. Old retained file is removed.
8. Old songs.db row is removed.
9. Current songs.db row is inserted.
10. Current playlist entry becomes completed.
11. Old playlist entry becomes pending.
```

The important principle is:

> Never destroy the previously retained song merely because the replacement download started; replace it only after the new song is valid.

---

# 61. MUTAGEN IMPLEMENTATION RESPONSIBILITY

The embedder module should be the single place responsible for writing final ID3 metadata.

Suggested API concept:

```python
embed_final_mp3(
    source_mp3,
    output_mp3,
    metadata,
    artwork_path,
)
```

It should:

1. open/create ID3 tags;
2. remove or replace the intended application-managed fields;
3. write standard ID3 frames;
4. write custom `TXXX` frames;
5. embed the front-cover `APIC` frame;
6. save;
7. close/reopen if needed for validation.

Do not scatter final tag-writing logic across unrelated modules.

---

# 62. METADATA FIELD OWNERSHIP

## Source-owned

Obtained from yt-dlp/YTMusic:

```text
title
artist
album
duration
release date
upload date
ISRC
source audio information
source video/source IDs
source descriptions
thumbnails
```

## Search-owned

Obtained from the selected YouTube result:

```text
yt_video_id
yt_video_url
yt_video_title
yt_video_channel
yt_video_channel_id
yt_video_views
yt_video_published_at
```

## Application-owned

Generated by the project:

```text
serial_number
playlist_position
status
mp3_path
mp3_size
mp3_sha256
yt_video_match_method
```

---

# 63. SOURCE VS FINAL MP3

The following distinction is important:

```text
master.mp3
```

is the temporary acquired audio.

```text
songs/original/004_....mp3
```

is the final application-owned MP3.

The final MP3 is the file that:

- contains final tags;
- contains embedded artwork;
- contains custom YouTube fields;
- receives the SHA-256 hash;
- is referenced by `songs.db`;
- survives temporary cleanup.

---

# 64. PIPELINE MODULE RESPONSIBILITIES

## `main.py`

Application entry point.

Responsibilities:

- load configuration;
- initialize directories;
- initialize databases;
- start ingestion/processing;
- handle top-level errors.

## `db_playlist.py`

Responsibilities:

- create `playlist.db` schema;
- insert playlist entries;
- allocate/query serials;
- update statuses;
- store errors.

## `db_songs.py`

Responsibilities:

- create `songs.db` schema;
- insert/update/delete song records;
- search by ISRC;
- retrieve retained-song file paths.

## `playlist_ingest.py`

Responsibilities:

- call YTMusic playlist API;
- preserve returned order;
- extract playlist metadata;
- assign serials;
- populate `playlist.db`.

## `downloader.py`

Responsibilities:

- build the single yt-dlp acquisition command;
- execute it;
- locate `master.mp3`;
- locate `master.info.json`;
- locate `master.jpg`;
- validate acquisition output.

## `metadata.py`

Responsibilities:

- parse `master.info.json`;
- normalize metadata;
- extract ISRC;
- derive final structured metadata object.

## `duplicate_checker.py`

Responsibilities:

- accept normalized ISRC;
- query `songs.db` by ISRC;
- trigger duplicate decision when a match exists;
- perform no fallback matching.

## `youtube_finder.py`

Responsibilities:

- construct exact search query;
- retrieve results;
- skip results whose title contains `lyrics`;
- select first remaining result;
- retrieve selected video metadata.

## `embedder.py`

Responsibilities:

- create final MP3 tags;
- embed artwork;
- write custom TXXX fields;
- save final MP3.

## `validator.py`

Responsibilities:

- reopen final MP3;
- validate audio;
- validate tags;
- validate artwork;
- validate expected custom metadata.

## `hashing.py`

Responsibilities:

- calculate SHA-256 of final MP3;
- return byte size and digest.

## `pipeline.py`

Responsibilities:

- orchestrate each stage;
- enforce state transitions;
- coordinate duplicate decisions;
- coordinate finalization;
- guarantee cleanup.

---

# 65. DETAILED SINGLE-SONG EXECUTION ORDER

For one serial, the exact high-level implementation order is:

```text
1. Load playlist row.
2. Confirm status = pending.
3. Create temp/{serial}/.
4. Execute one yt-dlp acquisition.
5. Validate master.mp3/info.json/artwork.
6. Parse info.json.
7. Normalize title/artist/album/dates/ISRC/audio metadata.
8. If ISRC is non-NULL, query songs.db.
9. If duplicate exists, ask user.
10. If user keeps previous:
       delete temp
       mark current duplicate
       finish this serial
11. If user keeps current:
       keep current temporary source
       prepare current final MP3
       only after current is valid, remove old retained MP3
       replace old songs.db record
       mark old playlist entry pending
12. Build YouTube search query.
13. Retrieve YouTube results.
14. Walk results in returned order.
15. Skip titles containing lyrics.
16. Select first remaining result.
17. Retrieve selected video metadata.
18. Use existing master.jpg.
19. Create final .mp3.tmp.
20. Write all final ID3 metadata with Mutagen.
21. Embed APIC artwork with Mutagen.
22. Save.
23. Reopen final file.
24. Validate audio and metadata.
25. Calculate SHA-256 and file size.
26. Atomically rename .tmp -> .mp3.
27. Insert songs.db row.
28. Update playlist.db to completed.
29. Delete temp/{serial}/.
30. Move to next pending playlist entry.
```

---

# 66. COMPLETE PROJECT PIPELINE — EXPANDED

```text
┌──────────────────────────────────────────────────────────────┐
│                    YOUTUBE MUSIC PLAYLIST                   │
└──────────────────────────────┬───────────────────────────────┘
                               │
                               v
┌──────────────────────────────────────────────────────────────┐
│                     YTMUSIC API INGESTION                    │
│                                                              │
│ - read playlist ID                                           │
│ - retrieve complete playlist                                 │
│ - preserve returned/default order                            │
│ - extract video ID/title/artists/duration                    │
│ - assign permanent serial number                             │
└──────────────────────────────┬───────────────────────────────┘
                               │
                               v
┌──────────────────────────────────────────────────────────────┐
│                         playlist.db                           │
│                                                              │
│ serial | position | YTM ID | title | artist | status       │
│                                                              │
│ status = pending                                              │
└──────────────────────────────┬───────────────────────────────┘
                               │
                               v
┌──────────────────────────────────────────────────────────────┐
│                  SELECT NEXT PENDING ENTRY                   │
└──────────────────────────────┬───────────────────────────────┘
                               │
                               v
┌──────────────────────────────────────────────────────────────┐
│                   temp/{serial_number}/                      │
└──────────────────────────────┬───────────────────────────────┘
                               │
                               v
┌──────────────────────────────────────────────────────────────┐
│                   ONE COMPLETE YT-DLP RUN                    │
│                                                              │
│                         yt-dlp                               │
│                                                              │
│                    -x / MP3                                  │
│                    info.json                                 │
│                    thumbnail/artwork                         │
│                                                              │
│                 NO --add-metadata                             │
└───────────────┬────────────────┬────────────────┬────────────┘
                │                │                │
                v                v                v
          master.mp3      master.info.json    master.jpg
                │                │                │
                └────────────────┼────────────────┘
                                 │
                                 v
┌──────────────────────────────────────────────────────────────┐
│                     SOURCE VALIDATION                        │
│                                                              │
│ - audio exists                                              │
│ - JSON valid                                                 │
│ - artwork readable                                           │
│ - duration readable                                          │
└──────────────────────────────┬───────────────────────────────┘
                               │
                               v
┌──────────────────────────────────────────────────────────────┐
│                   METADATA EXTRACTION                        │
│                                                              │
│ title / artists / album / dates / duration                  │
│ ISRC / source audio / source IDs / description              │
└──────────────────────────────┬───────────────────────────────┘
                               │
                               v
┌──────────────────────────────────────────────────────────────┐
│                    METADATA NORMALIZATION                    │
│                                                              │
│ source metadata -> application metadata                      │
└──────────────────────────────┬───────────────────────────────┘
                               │
                               v
                     ┌─────────┴─────────┐
                     │                   │
                 ISRC != NULL        ISRC == NULL
                     │                   │
                     v                   │
             search songs.db             │
                     │                   │
               ┌─────┴──────┐            │
               │            │            │
            no match      match          │
               │            │            │
               │            v            │
               │      DUPLICATE PROMPT   │
               │        /          \     │
               │   previous        current
               │      │                │
               │      v                v
               │  discard current   keep current
               │  temp + mark       replace old
               │  duplicate         retained song
               │                       │
               └──────────┬────────────┘
                          │
                          v
┌──────────────────────────────────────────────────────────────┐
│                 YOUTUBE OFFICIAL VIDEO SEARCH                │
│                                                              │
│ query:                                                       │
│ {title} {album_name} official video song                     │
│                                                              │
│ returned result order is authoritative                       │
└──────────────────────────────┬───────────────────────────────┘
                               │
                               v
┌──────────────────────────────────────────────────────────────┐
│                   SIMPLE RESULT FILTER                       │
│                                                              │
│ If result title contains "lyrics" -> skip                   │
│ Otherwise -> select immediately                              │
│                                                              │
│ No ranking. No scoring. No confidence.                       │
└──────────────────────────────┬───────────────────────────────┘
                               │
                               v
┌──────────────────────────────────────────────────────────────┐
│                  SELECTED VIDEO METADATA                     │
│                                                              │
│ ID / URL / title / channel / channel ID / views / date      │
└──────────────────────────────┬───────────────────────────────┘
                               │
                               v
┌──────────────────────────────────────────────────────────────┐
│                    FINAL MP3 BUILD                           │
│                                                              │
│ source audio + master.jpg                                    │
│                                                              │
│ final file: .mp3.tmp                                         │
└──────────────────────────────┬───────────────────────────────┘
                               │
                               v
┌──────────────────────────────────────────────────────────────┐
│                 MUTAGEN FINAL TAG WRITER                     │
│                                                              │
│ Standard ID3 + custom TXXX + APIC artwork                   │
│                                                              │
│ Mutagen is the sole final metadata authority                 │
└──────────────────────────────┬───────────────────────────────┘
                               │
                               v
┌──────────────────────────────────────────────────────────────┐
│                     FINAL VALIDATION                         │
│                                                              │
│ - audio playable                                             │
│ - duration valid                                             │
│ - tags readable                                               │
│ - artwork embedded                                           │
│ - expected YouTube fields present                            │
└──────────────────────────────┬───────────────────────────────┘
                               │
                               v
┌──────────────────────────────────────────────────────────────┐
│                    HASH + FILE SIZE                          │
│                                                              │
│ SHA-256(final fully-tagged MP3)                              │
└──────────────────────────────┬───────────────────────────────┘
                               │
                               v
┌──────────────────────────────────────────────────────────────┐
│                      ATOMIC RENAME                           │
│                                                              │
│ .mp3.tmp -> final .mp3                                       │
└──────────────────────────────┬───────────────────────────────┘
                               │
                               v
┌──────────────────────────────────────────────────────────────┐
│                         songs.db                             │
│                                                              │
│ current serial + metadata + file path + hash                │
└──────────────────────────────┬───────────────────────────────┘
                               │
                               v
┌──────────────────────────────────────────────────────────────┐
│                       playlist.db                            │
│                                                              │
│ current serial -> completed                                 │
└──────────────────────────────┬───────────────────────────────┘
                               │
                               v
┌──────────────────────────────────────────────────────────────┐
│                      CLEANUP TEMP                            │
│                                                              │
│ delete temp/{serial}/                                        │
└──────────────────────────────┬───────────────────────────────┘
                               │
                               v
                    NEXT PENDING ENTRY
```

---

# 67. COMPLETE STATUS EXAMPLES

## Initial

```text
playlist.db

001 pending
002 pending
003 pending
004 pending
```

## After normal processing

```text
playlist.db

001 completed
002 completed
003 completed
004 completed
```

`songs.db`:

```text
001
002
003
004
```

## After duplicate — keep previous

```text
playlist.db

001 completed
002 completed
003 duplicate
004 pending
```

`songs.db`:

```text
001
002
004
```

## After duplicate — keep current

```text
playlist.db

001 pending
002 completed
003 completed
004 pending
```

`songs.db`:

```text
002
003
```

The old serial still exists in the playlist database.

---

# 68. WHAT `playlist.db` MUST NEVER DO

`playlist.db` must never:

- delete a playlist entry because of duplicate detection;
- change a serial number;
- recycle a serial number;
- replace one playlist entry with another;
- use ISRC as its primary identity;
- use YouTube video ID as its primary identity.

---

# 69. WHAT `songs.db` MUST NEVER DO

`songs.db` must never:

- contain two rows with the same serial;
- contain the discarded current duplicate when the user chose previous;
- retain an old row after its MP3 has been intentionally replaced by the current duplicate;
- invent ISRC values;
- use fallback duplicate matching when ISRC is `NULL`.

---

# 70. WHAT YT-DLP MUST DO

The initial yt-dlp stage must:

- use the selected YTM source URL;
- download the source audio;
- write `info.json`;
- write thumbnail/artwork;
- convert the artwork to the selected image type when necessary;
- not perform final ID3 tagging;
- not be called a second time for artwork only.

---

# 71. WHAT MUTAGEN MUST DO

Mutagen must:

- write standard ID3 tags;
- write ISRC when present;
- write custom source/YouTube fields;
- embed artwork;
- save the final MP3;
- provide the final metadata state that is subsequently validated.

---

# 72. WHAT THE VIDEO FINDER MUST DO

It must:

1. construct the exact query;
2. retrieve results;
3. inspect titles in returned order;
4. skip titles containing `lyrics`;
5. select the first remaining result;
6. stop immediately after selection;
7. retrieve that video's metadata.

It must not:

- score;
- rank;
- compare views;
- compare durations;
- compare channels;
- calculate confidence.

---

# 73. RESTART / RESUME BEHAVIOR

The application must be restart-safe.

If it exits unexpectedly:

- already finalized `completed` rows remain completed;
- duplicate decisions already committed remain committed;
- permanent serials remain unchanged;
- new processing continues from the next `pending` entry;
- `error` entries can be retried.

If a temporary directory remains after a crash, startup/recovery logic should inspect and clean stale temporary directories as appropriate rather than assuming they represent completed songs.

---

# 74. LOGGING REQUIREMENTS

The application should log enough information to understand every processing attempt.

Minimum useful information per serial:

```text
serial
playlist position
title
artist
status before processing
status after processing
ISRC
whether duplicate detected
existing duplicate serial when applicable
user duplicate decision
YouTube search query
selected YouTube video ID when applicable
final MP3 path
final MP3 size
SHA-256
error message when applicable
```

Logs must not replace the databases.

The databases remain authoritative for persistent state.

---

# 75. USER PROMPTS

Normal successful processing should be automatic.

The only required interactive prompt is duplicate handling.

Example:

```text
[DUPLICATE]
Current: 004 - Song A - Artist A
Existing: 001 - Song A - Artist A
ISRC: USABC1234567

1) Keep previous
2) Keep current
Selection:
```

No prompt is required for ordinary YouTube video selection.

The YouTube selection rule is deterministic:

```text
first result whose title does not contain lyrics
```

---

# 76. TEST PLAN

The final implementation should test at least the following cases.

## Test 1 — New unique song

Expected:

```text
pending -> completed
songs.db row created
MP3 created
```

## Test 2 — Duplicate with keep previous

Expected:

```text
current -> duplicate
old song retained
current songs.db row absent
```

## Test 3 — Duplicate with keep current

Expected:

```text
old playlist entry -> pending
current playlist entry -> completed
old songs.db row deleted
current songs.db row created
old MP3 deleted
current MP3 retained
```

## Test 4 — NULL ISRC

Expected:

```text
isrc = NULL
no duplicate query
song processes normally
```

## Test 5 — YouTube result with lyrics first

Expected:

```text
result 1 skipped
result 2 selected
```

## Test 6 — All YouTube results contain lyrics

Expected:

```text
yt_video fields = NULL
song can still complete
```

## Test 7 — YouTube search returns an acceptable result first

Expected:

```text
result 1 selected immediately
```

## Test 8 — Source acquisition fails

Expected:

```text
status = error
no completed songs.db row
serial preserved
```

## Test 9 — Final MP3 validation fails

Expected:

```text
no completed songs.db commit
no playlist completed state
```

## Test 10 — Restart after error

Expected:

```text
error -> pending
retry uses same serial
```

## Test 11 — Duplicate replacement after current file validation

Expected:

```text
old MP3 is not deleted until replacement MP3 validates successfully
```

## Test 12 — Playlist contains same YTM entry twice

Expected:

```text
separate serials
separate playlist rows
no serial collision
```

---

# 77. FINAL ACCEPTANCE CRITERIA

Phase 1 is complete when the system can demonstrate all of the following:

## Playlist identity

- complete playlist is ingested;
- returned order is preserved;
- every playlist entry gets a permanent serial;
- serials never collide;
- serials are never reused.

## Source acquisition

- the exact YTM source is downloaded;
- one yt-dlp command obtains audio + info.json + artwork;
- `--add-metadata` is not used;
- acquisition artifacts are validated.

## Metadata

- detailed source metadata is parsed;
- ISRC is extracted when available;
- missing ISRC is stored as NULL;
- no fallback duplicate match is used.

## Duplicate handling

- ISRC match is found in `songs.db`;
- user chooses previous/current;
- keep previous marks current playlist entry duplicate;
- keep current deletes the old retained song and changes old playlist entry to pending;
- playlist entries are never deleted.

## YouTube video discovery

- exact search query is used;
- results are processed in returned order;
- `lyrics` results are skipped;
- first remaining result is selected;
- no scoring/ranking is used;
- no acceptable result is allowed to leave the song otherwise incomplete.

## Final MP3

- final MP3 is created;
- Mutagen writes all final metadata;
- artwork is embedded;
- YouTube metadata is embedded;
- final file validates after writing;
- SHA-256 is calculated;
- final filename contains permanent serial.

## Persistence

- `songs.db` accurately reflects retained files;
- `playlist.db` accurately reflects processing status;
- completed records point to existing valid MP3 files;
- temporary directories are cleaned after finalization.

---

# 78. FINAL LOCKED RULES

These are the final rules for implementation and should not be changed casually during coding.

1. `playlist.db` is the permanent playlist database.
2. `songs.db` is the retained-song database.
3. Every playlist entry receives one permanent serial number.
4. Serial numbers are unique.
5. Serial numbers are never reused.
6. Serial numbers never change.
7. Serial numbers are assigned according to playlist ingestion order.
8. The API/default playlist order is preserved.
9. `playlist_position` is stored separately from the permanent serial.
10. `ytm_video_id` is not the permanent playlist identity.
11. The same YTM entry can appear multiple times in a playlist.
12. Such repeated entries receive different serial numbers.
13. `pending`, `completed`, `duplicate`, and `error` are the playlist processing statuses.
14. `completed` requires a retained song in `songs.db`.
15. `duplicate` means the entry was processed but the previous retained song was kept.
16. `error` means processing failed and may later be retried.
17. ISRC is the only duplicate key.
18. Missing ISRC is stored as `NULL`.
19. Missing ISRC does not trigger duplicate detection.
20. No fallback duplicate matching is permitted.
21. Duplicate detection is performed against `songs.db`.
22. The user decides whether to keep previous or current duplicate.
23. Keep previous -> current playlist entry becomes `duplicate`.
24. Keep previous -> previous song remains unchanged.
25. Keep current -> previous physical MP3 is deleted only after current replacement is valid.
26. Keep current -> previous `songs.db` row is deleted.
27. Keep current -> previous playlist entry becomes `pending`.
28. Keep current -> current playlist entry becomes `completed`.
29. Playlist entries are never deleted because of duplicates.
30. Initial source acquisition uses one complete yt-dlp command.
31. That acquisition gets audio.
32. That acquisition gets `info.json`.
33. That acquisition gets artwork.
34. No second artwork download is performed.
35. yt-dlp does not write final ID3 metadata.
36. `--add-metadata` is not used.
37. Mutagen is the single final metadata writer.
38. Mutagen writes standard ID3 fields.
39. Mutagen writes custom TXXX source/YouTube fields.
40. Mutagen embeds front-cover artwork.
41. The final MP3 is validated after metadata writing.
42. SHA-256 is calculated after final metadata writing.
43. The final MP3 is atomically renamed after successful validation.
44. YouTube search query is exactly `{title} {album_name} official video song`.
45. Results remain in returned order.
46. Titles containing `lyrics` are skipped case-insensitively.
47. The first remaining result is selected.
48. No YouTube scoring exists.
49. No YouTube ranking exists.
50. No YouTube confidence score exists.
51. No view-count ranking exists.
52. No duration ranking exists.
53. No channel ranking exists.
54. If no acceptable YouTube result exists, the YouTube video fields remain NULL/absent.
55. The song can still complete without an acceptable YouTube video.
56. Final MP3 filename uses the permanent serial.
57. Temporary files are deleted only after successful finalization.
58. Database state must reflect physical file state.
59. A failed processing attempt must never be marked `completed`.
60. Lyrics and Phase 2 audio-analysis features remain outside the Phase 1 scope.

---

# 79. FINAL ONE-PAGE SUMMARY

```text
PLAYLIST
   |
   | YTMusic API
   v
playlist.db
   |
   | permanent serial
   v
PENDING ENTRY
   |
   | ONE yt-dlp command
   +-------------------------------+
   |               |               |
   v               v               v
audio.mp3      info.json       artwork.jpg
   |               |               |
   +---------------+---------------+
                   |
                   v
          metadata normalization
                   |
                   v
              ISRC check
                   |
          +--------+--------+
          |                 |
       NULL/unique       duplicate
          |                 |
          |             ask user
          |             /      \
          |       previous     current
          |          |            |
          |          v            v
          |       duplicate   replace old
          |                       |
          +-----------+-----------+
                      |
                      v
           YouTube search
                      |
       title + album + official video song
                      |
                      v
          skip title containing lyrics
                      |
                      v
          first remaining result
                      |
                      v
         selected video metadata
                      |
                      v
             use master.jpg
                      |
                      v
              final MP3.tmp
                      |
                      v
             Mutagen writes tags
                      |
                      v
              validate final MP3
                      |
                      v
                 SHA-256
                      |
                      v
              atomic final MP3
                      |
                      v
                  songs.db
                      |
                      v
          playlist.db -> completed
                      |
                      v
                cleanup temp
                      |
                      v
              NEXT PENDING ENTRY
```

---

# 80. END STATE

At the end of a successful full run, the project contains:

```text
phase1_project/
│
├── db/
│   ├── playlist.db
│   └── songs.db
│
├── songs/
│   └── original/
│       ├── 001_....mp3
│       ├── 002_....mp3
│       ├── 003_....mp3
│       └── ...
│
├── temp/
│   └── empty after successful cleanup
│
├── config.json
├── cookies.txt
├── requirements.txt
├── main.py
└── src/
```

`playlist.db` preserves the complete playlist-entry identity and status history required by Phase 1.

`songs.db` contains only the currently retained song assets.

Each retained MP3 is self-contained with its final ID3 metadata and embedded artwork.

This is the complete Phase 1 architecture and implementation specification.


--- ARCHIVE B: ORIGINAL PHASE 2 SPECIFICATION ---

# ARCHIVE B: ORIGINAL PHASE 2 SPECIFICATION

The following document is retained verbatim as supplied.

# PHASE 2 — STANDALONE TELUGU WORD-LEVEL LYRIC SYNCHRONISATION

## FINAL CONSOLIDATED ULTRA-DETAILED PROJECT PLAN

**Document status:** FINAL / authoritative consolidated engineering specification  
**Project:** Phase 2 only — standalone word-level lyric synchronisation  
**Revision:** 1.4.0 (consolidated after field runs)  
**Scope:** MP3 + external LRC + cumulative JSON -> final MP3 + final LRC + cumulative JSON  
**Language:** Telugu (`te` / `tel`)  
**Primary acoustic model:** Meta MMS (`facebook/mms-1b-all`) with Telugu adapter  
**Primary alignment method:** reference-driven CTC forced alignment  
**Original source files:** read-only  
**Phase 1 integration:** none  
**Reel / hook selection:** explicitly out of scope and rolled back  

---

# 0. AUTHORITATIVE FINAL DECISIONS / OVERRIDES

This section is the highest-priority specification for the document. It exists because the project evolved through several implementation and field-test iterations. Any older passage later in this document that conflicts with this section is superseded by this section.

## 0.1 Phase boundary

Phase 2 is a separate standalone project. It does not import or call Phase 1 code, Phase 1 databases, or Phase 1 pipeline state. It only consumes files placed in its input directory.

## 0.2 Input package

Every song is a basename-matched package:

```text
songs/original/
├── SongName.mp3
├── SongName.lrc
└── SongName.json
```

The external `.lrc` is the lyric/reference/timing source. The `.json` is the cumulative historical/source record. The `.mp3` is the audio and metadata source.

Embedded `SYLT` or `USLT` inside the MP3 is **not required** and is **not** the lyric source of truth. If embedded lyric frames already exist, they are treated as existing MP3 metadata and must be preserved.

## 0.3 Output package

For every completed song, the only user-facing final files are:

```text
songs/final/
├── SongName.mp3
├── SongName.lrc
└── SongName.json
```

There is **one** final LRC file per song. Earlier drafts that described three final LRC variants are superseded.

## 0.4 No reel / hook-selection pipeline

Automatic hook selection, manual hook-timeline JSON, reel rendering, smart crop, zoom-to-70%-of-9:16 rendering, and line-highlight reel generation are **not part of this Phase 2 project**. Those ideas were explicitly rolled back and must not be reintroduced into the implementation.

## 0.5 JSON preservation

The input JSON is a cumulative record. The final JSON is produced by deep-copying the entire original JSON and adding/updating only the top-level `phase2` namespace.

No existing top-level key, nested field, source metadata, artwork metadata, playlist metadata, YouTube metadata, Spotify metadata, prior processing record, or unknown field may be silently deleted or reconstructed.

## 0.6 MP3 metadata preservation

The output MP3 starts from the original MP3. Phase 2 must patch metadata surgically.

It must not delete all tags and rebuild a reduced tag set. Existing artwork, identifiers, URLs, TXXX frames, UFID frames, WXXX frames, USLT, existing SYLT, and any other pre-existing metadata are preserved. Phase 2 owns only its own dedicated word-level SYLT frame.

## 0.7 Canonical alignment

The canonical word-level result is the internal Phase 2 alignment model represented in SQLite and the final JSON. The final LRC and MP3 SYLT are exports from that same canonical representation.

Never use the rendered LRC as the canonical timing source.

## 0.8 Actual forced alignment, not plain ASR offsets

MMS is used to generate frame-level acoustic emissions. The supplied LRC reference text is tokenized and explicitly aligned against those emissions by a CTC forced-alignment algorithm.

The following is not acceptable as the final alignment method:

```text
MMS -> argmax decode -> predicted transcript -> predicted word offsets
```

The correct flow is:

```text
LRC reference text
        +
MMS frame-level emissions
        |
        v
CTC forced alignment
        |
        v
token spans
        |
        v
word spans
```

## 0.9 LRC timestamps are coarse anchors

The source LRC timestamps are timing anchors, not immutable final word timestamps. They constrain search regions, guide chunking, identify long lyric-free intervals, and provide an independent validation signal.

## 0.10 Blank markers are structural events, not ordinary words

A source LRC blank timestamp line such as:

```text
[04:12.82]
```

is a structural timing marker.

It should strongly influence chunk boundaries and lyric-start constraints.

A word whose **start** occurs inside an explicit blank-marker region is invalid and should trigger re-alignment/review.

A word that starts before a blank marker and whose acoustic/end span extends slightly beyond the marker is not automatically an LRC serialization failure, because the standard LRC export is based on word onset timestamps. Such a crossing must be retained as a quality diagnostic and may be clipped only in derived representations where the semantics explicitly require it. The canonical alignment must not silently invent timing merely to satisfy a serializer.

## 0.11 Global chronology

The canonical sequence must be globally chronological.

Never solve chronology by sorting timestamps independently from their words. Doing so can attach the wrong timestamps to the wrong lyric words.

If chronology is broken:

```text
identify offending candidate/chunk
        ->
resolve duplicate/overlap provenance
        ->
retry affected chunk if necessary
        ->
merge again
        ->
validate again
```

## 0.12 Windows FFmpeg temporary-file rule

A temporary output path such as:

```text
source_16k.wav.tmp
```

must not be passed to FFmpeg without an explicit output format because the final extension is `.tmp`.

The implementation must either:

1. keep `.wav` as the final suffix of the temporary filename, or
2. explicitly pass `-f wav`.

The production implementation does both where practical.

## 0.13 NVIDIA/CUDA requirement

For the intended production batch, Demucs and MMS must be GPU-first on the NVIDIA GPU. The application must detect the actual PyTorch CUDA capability at startup and must clearly print which device each stage is using.

The project must not silently run hundreds of songs on CPU because a CPU-only PyTorch wheel was accidentally installed.

For the observed Windows machine, the diagnostic failure:

```text
AssertionError: Torch not compiled with CUDA enabled
```

means the installed PyTorch build is CPU-only even though `nvidia-smi` can see the GPU. The remediation is to install the CUDA-enabled PyTorch build compatible with the environment and then verify `torch.cuda.is_available()` before starting the batch.

## 0.14 GPU execution policy

Default production policy:

```text
one GPU worker
Demucs on CUDA
release Demucs resources
MMS on CUDA
```

Do not launch multiple large MMS/CTC workers concurrently on one 8 GB GPU merely because the CPU has additional cores.

## 0.15 Model cache

The MMS checkpoint is downloaded once into the configured Hugging Face cache/model directory and reused. The 3.86 GB base checkpoint is not supposed to be downloaded once per song.

Windows symlink warnings are a cache efficiency warning, not a GPU failure. Enabling Windows Developer Mode or running the cache operation with appropriate permissions can improve deduplication. The project must not treat that warning as an alignment failure.

## 0.16 Quality state vs pipeline state

Processing state and output quality are separate dimensions.

Example:

```text
pipeline_status = finished
quality_status  = needs_review
```

is valid and means processing completed but timing quality is suspicious.

## 0.17 Final LRC semantics

There is one final LRC file. It is a project-specific word-level LRC export based on the canonical word start times while preserving the original lyric wording.

The canonical millisecond word spans remain available in JSON/SQLite and the embedded SYLT.

---

# 1. FIELD FAILURE HISTORY AND WHAT EACH FAILURE TAUGHT US

This section is part of the final specification because the production architecture was hardened from actual runs rather than hypothetical assumptions.

## 1.1 Failure A — Windows FFmpeg could not choose WAV format

The initial batch repeatedly failed with:

```text
Unable to choose an output format for ... source_16k.wav.tmp
use a standard extension for the filename or specify the format manually
```

This happened for many songs because the decoder wrote a temporary file whose final extension was `.tmp`.

### Permanent fix

The audio decoder must write something equivalent to:

```text
.source_16k.<pid>.tmp.wav
```

or invoke FFmpeg with:

```text
-f wav
```

and then atomically replace the intended final WAV.

This failure is a common shared-path failure: one decoder bug affected every song, so the correct response was to fix the common audio module rather than special-case songs.

## 1.2 Failure B — CPU-only PyTorch

The Windows environment reported:

```text
cuda_available = False
```

and:

```text
AssertionError: Torch not compiled with CUDA enabled
```

while:

```text
nvidia-smi
```

successfully reported an NVIDIA GeForce RTX 5050 with approximately 8 GB of VRAM.

### Interpretation

The GPU hardware and Windows driver were visible. PyTorch itself was the CPU-only build.

### Permanent design rule

`doctor`, startup checks, and the processing pipeline must distinguish:

```text
GPU exists at OS/driver level
```

from:

```text
PyTorch can actually create CUDA tensors
```

Both must be true before a CUDA stage is considered GPU-enabled.

## 1.3 Failure C — final SYLT chronology validation

A later run produced:

```text
MP3_VALIDATION_FAILED: SYLT timestamps are not chronological
```

for dozens of songs.

### Root cause

Chunk-local alignments could each be locally valid while their merged global sequence contained backward timestamps, particularly around overlapping chunk context and chunk boundaries.

### Permanent fix

The canonical merge layer must enforce global word order before any exporter is called. The final MP3 validator remains as a second defense, not the first place the problem is discovered.

## 1.4 Failure D — LRC chronology validation

After the SYLT-stage problem was tightened, failures moved to:

```text
LRC_GENERATION_FAILED: timestamps are not globally chronological
```

### Root cause

The same timing defect was still reaching the LRC renderer, and some repair logic had only covered certain cross-chunk cases.

### Permanent fix

Chronology is now a canonical alignment property. It must be resolved before LRC generation, not patched in the serializer.

## 1.5 Failure E — words crossing explicit LRC blank markers

The next run produced errors such as:

```text
lyric word crosses blank marker at 272300 ms:
line 59 word 1 ends at 272760 ms
```

### Root cause

The previous validation treated the blank marker as if every word end had to be before it. That was too strict because the LRC blank marker is an onset/structure signal and not necessarily a hard acoustic end boundary for a word that began before the marker.

### Permanent fix

Blank-marker semantics are now explicitly divided into:

```text
word start constraint
word span diagnostic
chunk boundary constraint
instrumental/gap evidence
```

The serializer must not reject a valid word solely because its end span slightly crosses a structural marker when its onset remains on the correct side.

## 1.6 Failure F — residual same-chunk backward timestamps

Some songs still showed messages such as:

```text
lyric word timestamps are not globally chronological: 29940 -> 28090
```

### Root cause

The original repair path concentrated on cross-chunk continuity but did not fully validate the sequence inside an individual chunk.

### Permanent fix

Every chunk receives both:

```text
intra-chunk chronology validation
```

and:

```text
inter-chunk chronology validation
```

before global merge is accepted.

## 1.7 Failure G — mutable merge state

Repeated merge/retry passes could mutate candidate objects and then reuse already-modified timings.

### Permanent fix

Alignment candidates and chunk results are treated as immutable input to merge operations. Each repair pass constructs new output objects.

---

# 2. FINAL PROJECT SCOPE

The remainder of this document defines the complete implementation blueprint. The full detailed architecture from the earlier engineering plan is preserved below, with the field-tested decisions above taking precedence wherever necessary.



# 3. FULL DETAILED ENGINEERING PLAN (RETAINED IN FULL)

# PHASE 2 — STANDALONE WORD-LEVEL TELUGU LYRIC SYNCING

## Ultra-Detailed Production Project Plan

**Document status:** Implementation blueprint / engineering specification  
**Project:** Phase 2 only — standalone word-level lyric synchronization  
**Input model:** MP3 + sidecar LRC + sidecar cumulative JSON  
**Output model:** MP3 + LRC + cumulative JSON  
**Language focus:** Telugu (`te` / ISO-639-3 `tel`)  
**Primary alignment model:** Meta MMS (`facebook/mms-1b-all`) with Telugu adapter  
**Primary alignment method:** Reference-driven CTC forced alignment  
**Original files:** Never modified  
**Phase 1 integration:** None; Phase 2 consumes files only

---

# 1. EXECUTIVE DEFINITION

Phase 2 is a completely independent project that takes an already prepared music collection and upgrades the lyric timing from line-level synchronization to word-level synchronization.

The project must not import, call, modify, or depend on Phase 1's Python code, Phase 1's SQLite database, or Phase 1's processing state machine.

Phase 2 may consume information that was previously generated by Phase 1 because that information is physically present in the input MP3/LRC/JSON package, but this is a **file-level handoff**, not a software integration.

The fundamental input unit is a basename-matched three-file package:

```text
songs/original/
├── SongName.mp3
├── SongName.lrc
└── SongName.json
```

The fundamental output unit is another three-file package with the same basename:

```text
songs/final/
├── SongName.mp3
├── SongName.lrc
└── SongName.json
```

The original source package remains untouched.

The final package contains:

1. The original MP3 audio and all previous MP3 metadata, plus the new Phase 2 word-level synchronized lyrics frame.
2. A newly generated word-level LRC export using the original lyric wording and Phase 2 timing.
3. The original JSON object, completely preserved, with a new `phase2` namespace containing every new Phase 2 detail.

The canonical timing representation is the Phase 2 word alignment stored in the database and inside the final JSON. The final LRC is an export, and the embedded SYLT is another export of the same canonical timing result.

---

# 2. NON-NEGOTIABLE REQUIREMENTS

These are architectural invariants. The implementation must not violate them.

## 2.1 Original directory is read-only

```text
songs/original/
```

must be treated as read-only by the entire application.

No Phase 2 code path may:

- save an MP3 over an input MP3
- rewrite the original LRC
- rewrite the original JSON
- change file permissions unnecessarily
- rename source files
- move source files
- delete source files
- write temporary files beside the source files unless explicitly inside a separate temp directory

## 2.2 Phase 2 does not require embedded lyrics

The authoritative lyric input is:

```text
SongName.lrc
```

The implementation must not require `SYLT` or `USLT` to exist inside the MP3.

The sample MP3 used during planning happens to contain existing `SYLT` and `USLT` frames, but this must not become a hidden dependency. Existing embedded lyric frames are treated as existing MP3 metadata and are preserved rather than becoming the lyric source of truth.

## 2.3 Sidecar matching is by exact basename

Given:

```text
001_Gelupu Thalupule_Mani Sharma, Sreerama Chandra.mp3
```

the associated files are exactly:

```text
001_Gelupu Thalupule_Mani Sharma, Sreerama Chandra.lrc
001_Gelupu Thalupule_Mani Sharma, Sreerama Chandra.json
```

No fuzzy matching is allowed.

## 2.4 Existing JSON must be preserved completely

The original JSON is treated as a cumulative historical record.

Phase 2 must load the entire JSON object and write the final JSON as:

```text
original JSON object
    +
phase2 namespace
```

No previous top-level key may be silently removed, renamed, flattened, overwritten, or reconstructed.

Unknown keys must survive unchanged.

## 2.5 Existing MP3 metadata must be preserved

Phase 2 must modify the output MP3 surgically.

It must not:

```text
remove all ID3 frames
rebuild only a few known fields
save a reduced tag set
```

Instead:

```text
copy original MP3
    -> open existing tags
    -> preserve all existing frames
    -> add/update only the Phase 2-owned synchronized lyric frame
    -> save
```

## 2.6 Exactly three user-facing final files per song

The planned final user-facing package is:

```text
songs/final/SongName.mp3
songs/final/SongName.lrc
songs/final/SongName.json
```

Temporary files, SQLite, logs, model cache, and debugging artifacts may exist elsewhere but are not part of the final song package.

## 2.7 No destructive normalization

Text normalization must be reversible.

The original lyric wording must remain available after normalization.

## 2.8 MMS is an acoustic evidence generator, not the final transcript

The final system must not use:

```text
MMS ASR decode -> predicted words -> predicted timestamps
```

as its word-level lyric alignment method.

The supplied LRC text is the reference transcript. MMS produces frame-level acoustic emissions. The reference transcript is then explicitly aligned against those emissions using a CTC forced-alignment algorithm.

## 2.9 LRC is an anchor, not unquestionable ground truth

Existing LRC line timestamps are coarse timing information.

They are used to:

- constrain search regions
- create chunks
- detect long lyric-free gaps
- validate the new timing

but the exact word timing comes from acoustic alignment.

## 2.10 Word-level timing must have start and end

The canonical representation must keep:

```text
word
start_ms
end_ms
score
source
```

A timestamp-only representation is insufficient for validation and high-quality downstream processing.

---

# 3. REAL SAMPLE INPUT CONTRACT

The sample supplied for planning is:

```text
001_Gelupu Thalupule_Mani Sharma, Sreerama Chandra.mp3
001_Gelupu Thalupule_Mani Sharma, Sreerama Chandra.lrc
001_Gelupu Thalupule_Mani Sharma, Sreerama Chandra.json
```

The sample JSON is a large cumulative record rather than a minimal song description. It contains top-level sections including:

```text
artwork
files
generated_at
lyrics
playlist
project_version
schema_version
song
sources
spotify
youtube_video
```

The existing JSON records, among other things, artwork information, source paths/hashes, lyric provenance, playlist identity, normalized song metadata, raw source metadata, Spotify information, and YouTube information.

The sample JSON also records a 318-second song duration and the synchronized lyric content.

The sample LRC contains:

- 42 timestamp entries
- 39 non-empty lyric entries
- 3 blank timestamp markers

The two major blank-timestamp regions are approximately:

```text
01:37.29 -> 02:12.94 = 35.65 seconds
03:11.72 -> 04:12.82 = 61.10 seconds
```

These are strong candidate non-lyric boundaries and must be treated as important chunking/validation information.

The sample MP3 currently contains 42 ID3 frame keys, including existing `TXXX`, `UFID`, `WXXX`, `USLT`, `SYLT`, and `APIC` data. The existing synchronized lyric frame and artwork must survive Phase 2. A new Phase 2 word-level SYLT frame should be added under its own descriptor rather than deleting the existing lyric frame.

The exact sample is therefore an excellent acceptance test for the implementation.

---

# 4. PROJECT OBJECTIVES

## 4.1 Primary objective

Produce trustworthy word-level timing for every lyric word that can be acoustically aligned.

## 4.2 Secondary objectives

- Preserve all existing song metadata.
- Preserve all previous JSON information.
- Preserve original MP3 audio.
- Preserve the original LRC file as input-only data.
- Produce a clean final LRC.
- Embed word-level synchronization into the final MP3.
- Make the process resumable.
- Make processing idempotent.
- Make every output traceable to exact input hashes, model revision, configuration, and pipeline version.
- Support partial results without pretending that interpolated timing is equivalent to directly aligned timing.
- Support batch processing at large scale.

## 4.3 Non-objectives

Phase 2 does not:

- discover new songs
- download audio
- download lyrics
- search external lyric databases
- change the source MP3
- edit Phase 1's database
- regenerate historical metadata
- replace the existing artwork
- use a transcription-first Whisper workflow

---

# 5. END-TO-END PIPELINE

The full pipeline is:

```text
songs/original/Song.mp3
songs/original/Song.lrc
songs/original/Song.json
          |
          v
[1] FILE MATCHING + INVENTORY
          |
          v
[2] INPUT HASHING + SNAPSHOT
          |
          v
[3] JSON LOAD + PRESERVE
          |
          v
[4] LRC PARSE
          |
          v
[5] AUDIO VALIDATION / DECODE
          |
          v
[6] VOCAL ISOLATION — DEMUCS
          |
          v
[7] VOCAL ACTIVITY — VAD + ENERGY
          |
          v
[8] REVERSIBLE TELUGU NORMALIZATION
          |
          v
[9] LRC ANCHOR MODEL
          |
          v
[10] REFERENCE-AWARE CHUNKING
          |
          v
[11] MMS MODEL INITIALIZATION
          |
          v
[12] MMS FRAME-LEVEL EMISSIONS
          |
          v
[13] CTC REFERENCE FORCED ALIGNMENT
          |
          v
[14] TOKEN -> WORD SPANS
          |
          v
[15] CHUNK QUALITY ANALYSIS
          |
          v
[16] OVERLAP DEDUPLICATION
          |
          v
[17] GLOBAL MERGE
          |
          v
[18] ALIGNMENT VALIDATION
          |
          v
[19] INSTRUMENTAL / GAP ANALYSIS
          |
          v
[20] CANONICAL PHASE2 DATA
          |
          +-----------------------------+
          |                             |
          v                             v
[21] FINAL LRC EXPORT             [22] FINAL MP3 + SYLT
          |                             |
          +-------------+---------------+
                        |
                        v
                [23] FINAL JSON UPDATE
                        |
                        v
                [24] OUTPUT VALIDATION
                        |
                        v
                [25] ATOMIC/STAGED PROMOTION
                        |
                        v
                songs/final/Song.mp3
                songs/final/Song.lrc
                songs/final/Song.json
```

---

# 6. DIRECTORY STRUCTURE

```text
phase2_project/
│
├── main.py
├── config.json
├── requirements.lock
├── README.md
├── CHANGELOG.md
│
├── src/
│   ├── __init__.py
│   ├── config.py
│   ├── db.py
│   ├── scanner.py
│   ├── json_manager.py
│   ├── lrc_reader.py
│   ├── audio.py
│   ├── demucs_isolator.py
│   ├── activity_detector.py
│   ├── telugu_normalizer.py
│   ├── tokenizer.py
│   ├── chunker.py
│   ├── mms_model.py
│   ├── ctc_aligner.py
│   ├── word_builder.py
│   ├── merger.py
│   ├── validator.py
│   ├── lrc_generator.py
│   ├── embedder.py
│   ├── json_updater.py
│   ├── recovery.py
│   ├── pipeline.py
│   └── utils.py
│
├── songs/
│   ├── original/
│   │   ├── Song.mp3
│   │   ├── Song.lrc
│   │   └── Song.json
│   │
│   └── final/
│       ├── Song.mp3
│       ├── Song.lrc
│       └── Song.json
│
├── temp/
│   └── {song_key}/
│       ├── input/
│       │   ├── source.mp3
│       │   ├── source.lrc
│       │   └── source.json
│       │
│       ├── audio/
│       │   ├── source_16k.wav
│       │   └── vocals_16k.wav
│       │
│       ├── chunks/
│       │   ├── chunk_000.wav
│       │   ├── chunk_000.json
│       │   ├── chunk_001.wav
│       │   └── ...
│       │
│       ├── stage_final/
│       │   ├── Song.mp3
│       │   ├── Song.lrc
│       │   └── Song.json
│       │
│       └── logs/
│
├── models/
│   └── mms/
│
└── db/
    └── phase2.db
```

The source files are not copied here for editing; the temp copy is strictly a working snapshot used for processing and debugging.

---

# 7. FILE MATCHING RULES

## 7.1 Scan scope

Default scan:

```text
songs/original/*.mp3
```

The implementation may offer an optional recursive mode, but the default corpus contract should be a flat directory because the user-defined structure is flat.

## 7.2 Basename derivation

Given:

```text
SongName.mp3
```

derive:

```text
SongName
```

Then require:

```text
SongName.lrc
SongName.json
```

## 7.3 Missing LRC

A missing LRC is a hard input failure because Phase 2's task is based on the provided LRC reference.

Set:

```text
quality_status = failed
error_code = LRC_MISSING
```

Do not fetch a replacement lyric source.

## 7.4 Missing JSON

A missing JSON is not necessarily a reason to abandon alignment, but it means the cumulative metadata contract is incomplete.

Preferred policy:

```text
process alignment only if explicitly allowed by configuration
```

Default policy:

```text
missing JSON -> needs_review / skipped
```

because the user's stated input contract includes the JSON historical record.

Do not fabricate historical metadata.

## 7.5 Duplicate basenames

If the scanner encounters duplicate basenames that would collide into the same final filename, stop those records with a clear error.

---

# 8. SOURCE FINGERPRINTING

For every input file calculate SHA-256:

```text
mp3_sha256
lrc_sha256
json_sha256
```

Also store:

```text
file_size
mtime
```

The SHA-256 is the authoritative content identity.

This lets the system distinguish:

```text
same filename, same file
```

from:

```text
same filename, different file
```

---

# 9. IDENTITY MODEL

A processing identity should be derived from:

```text
basename
+
mp3_sha256
+
lrc_sha256
+
json_sha256
+
pipeline_version
+
config_hash
+
model_name
+
model_revision
+
normalizer_version
```

If the same exact input and processing environment already produced valid final outputs, the song can be skipped safely.

If any relevant identity component changes, the song is eligible for reprocessing.

---

# 10. JSON HANDLING

## 10.1 Load the complete JSON

The JSON manager must parse the entire JSON document.

Do not use a schema that only allows known Phase 1 fields.

Unknown data must survive.

## 10.2 Preserve arbitrary top-level keys

The output must preserve every original top-level key:

```text
artwork
files
generated_at
lyrics
playlist
project_version
schema_version
song
sources
spotify
youtube_video
```

as present in the source sample.

Other songs may contain additional keys. Those keys must also survive.

## 10.3 Phase 2 namespace

Phase 2 owns exactly one new top-level namespace:

```json
"phase2": { ... }
```

This namespace is the only location Phase 2 is allowed to mutate in the JSON.

## 10.4 Existing phase2 namespace

If `phase2` already exists, update it in place while retaining historical subfields when possible.

Record:

```text
phase2.previous_run
phase2.current_run
```

or maintain a controlled run history array if the user wants complete historical attempts.

Recommended structure:

```json
"phase2": {
  "current": { ... },
  "history": [ ... ]
}
```

The first implementation may keep only `current` plus a compact `history` summary to avoid unnecessary JSON growth.

---

# 11. JSON PHASE 2 SCHEMA

The final JSON should contain a `phase2` object conceptually like:

```json
{
  "phase2": {
    "schema_version": 1,
    "pipeline_version": "1.0.0",
    "status": "finished",
    "quality_status": "good",

    "input": {
      "basename": "SongName",
      "mp3_filename": "SongName.mp3",
      "lrc_filename": "SongName.lrc",
      "json_filename": "SongName.json",
      "mp3_sha256": "...",
      "lrc_sha256": "...",
      "json_sha256": "..."
    },

    "model": {
      "name": "facebook/mms-1b-all",
      "language_iso1": "te",
      "language_iso3": "tel",
      "revision": "..."
    },

    "audio": {
      "source_duration_ms": 0,
      "alignment_sample_rate": 16000,
      "channels": 1
    },

    "vocal_isolation": {
      "model": "htdemucs",
      "device": "cuda",
      "status": "success"
    },

    "lyrics": {
      "source": "external_lrc",
      "line_count": 0,
      "blank_timing_markers": 0,
      "normalizer_version": "1.0"
    },

    "alignment": {
      "method": "ctc_forced_alignment",
      "word_count": 0,
      "aligned_word_count": 0,
      "missing_word_count": 0,
      "interpolated_word_count": 0,
      "lines": []
    },

    "quality": {
      "mean_score": null,
      "p10_score": null,
      "minimum_score": null,
      "low_score_word_percent": 0,
      "interpolated_word_percent": 0,
      "median_anchor_shift_ms": null,
      "max_anchor_shift_ms": null
    },

    "chunks": {
      "total": 0,
      "aligned": 0,
      "low_confidence": 0,
      "failed": 0
    },

    "instrumental_sections": [],

    "outputs": {
      "mp3": "songs/final/SongName.mp3",
      "lrc": "songs/final/SongName.lrc",
      "json": "songs/final/SongName.json"
    }
  }
}
```

This is an example contract rather than a rigid requirement that every field be populated for every song.

---

# 12. LRC PARSING

## 12.1 Supported timestamp forms

At minimum support:

```text
[mm:ss.xx]
[mm:ss.xxx]
```

Optionally support:

```text
[mm:ss]
```

with exact deterministic conversion.

## 12.2 Metadata tags

Recognize common LRC metadata such as:

```text
[ar:]
[ti:]
[al:]
[by:]
```

but keep them as source metadata rather than treating them as lyric text.

## 12.3 Blank timestamp lines

A line such as:

```text
[01:37.29]
```

with no lyric text is a meaningful source event.

Store it as:

```text
kind = blank_marker
start_ms = 97290
text = ""
```

Do not discard it during parsing.

## 12.4 Duplicate timestamps

If multiple text lines have exactly the same LRC timestamp, preserve order and create separate line records.

## 12.5 Original text preservation

Store the source line exactly enough to reproduce the original visible lyric wording.

Do not normalize the original LRC in place.

---

# 13. LRC INTERNAL DATA MODEL

Each parsed line should conceptually contain:

```text
line_index
original_timestamp_ms
original_text
kind
normalization_record
word_records[]
```

Possible line kinds:

```text
lyric
blank_marker
metadata
```

Only `lyric` and `blank_marker` participate in timing alignment.

---

# 14. AUDIO VALIDATION

Read the MP3 and determine:

```text
duration_ms
sample_rate
channels
bitrate if available
codec
```

The source MP3 remains untouched.

The working alignment representation should be:

```text
16 kHz
mono
float PCM
lossless WAV
```

Example:

```text
temp/{song_key}/audio/source_16k.wav
```

The source MP3 itself should not be repeatedly decoded and re-encoded between stages.

---

# 15. AUDIO DURATION CONSISTENCY

Compare:

```text
MP3 duration
LRC source metadata duration if available
JSON recorded duration if available
```

Do not require exact equality because metadata may differ by a small amount.

Record:

```text
audio.duration_delta_ms
```

Use the actual decoded MP3 duration as the authoritative audio boundary.

---

# 16. DEMUCS VOCAL ISOLATION

## 16.1 Purpose

Provide a cleaner vocal-bearing signal for alignment.

The user-facing output remains the original music mix.

Demucs is only a temporary processing stage.

## 16.2 Model

Default:

```text
htdemucs
```

Prefer a vocals-only separation path where supported.

Do not assume it is computationally cheaper simply because the requested output is one stem; the selected Demucs mode may still perform the underlying separation computation.

## 16.3 Working output

Preferred:

```text
temp/{song_key}/audio/vocals_16k.wav
```

or generate the lossless vocal stem first and resample once.

Avoid MP3 compression in the working vocal path.

## 16.4 GPU-first policy

Attempt:

```text
CUDA
```

first if configured.

On an out-of-memory or compatible runtime failure:

```text
CPU fallback
```

Record the actual device used.

## 16.5 GPU memory recovery

If CUDA OOM occurs:

1. release model/tensor references where safe
2. clear cached CUDA memory
3. reduce Demucs segment size if configured
4. retry
5. fall back to CPU if the retry fails

Do not pretend that "smaller batch size" solves a single-file Demucs invocation when batch size is already one.

## 16.6 Duration alignment

The vocal stem must be normalized to the source time axis.

If the stem is a few samples shorter/longer due to processing, pad or trim deterministically to the exact source sample count before converting to 16 kHz.

## 16.7 Validation

Require:

```text
vocals file exists
vocals readable
finite samples
non-zero usable signal
expected duration within tolerance
```

---

# 17. DUAL AUDIO ALIGNMENT STRATEGY

The primary alignment signal is:

```text
vocals stem
```

However, the original mix should remain available as a fallback.

For a low-confidence vocal-stem alignment:

```text
vocal result low confidence
        |
        v
try original-mix alignment for the same reference region
        |
        v
compare quality
        |
        v
retain better validated result
```

This protects against unusual Demucs artifacts, vocal bleed, whispering, background vocals, or songs where separation reduces useful acoustic information.

---

# 18. VOCAL ACTIVITY ANALYSIS

Use both:

```text
Silero VAD
+
audio energy/RMS activity
```

The output is an activity map rather than a claim of semantic "instrumental truth".

Silero VAD provides speech/activity intervals based on a threshold and related duration/padding parameters.

Suggested starting values:

```text
threshold = 0.50
min_speech_duration_ms = 250
min_silence_duration_ms = 300
speech_pad_ms = 150
```

These values must remain configurable and benchmarked.

---

# 19. WHAT VAD MEANS IN THIS PROJECT

VAD output means approximately:

```text
speech-like activity detected
```

It does not automatically mean:

```text
singing definitely exists
```

and lack of VAD activity does not automatically prove:

```text
instrumental section
```

Therefore VAD is used for:

- chunk boundary refinement
- pause detection
- non-lyric candidate identification
- alignment troubleshooting

It is not the sole source of instrumental markers.

---

# 20. LRC GAP MODEL

The existing LRC provides an especially valuable signal.

For consecutive lyric lines:

```text
line_i.start_ms
line_{i+1}.start_ms
```

compute:

```text
gap = line_{i+1}.start_ms - line_i.start_ms
```

For blank markers, additionally preserve the blank marker timestamp.

A long blank marker interval such as:

```text
01:37.29 -> 02:12.94
```

should become a strong candidate boundary.

The final classifier uses:

```text
LRC blank marker
+
next lyric start
+
VAD activity
+
energy
+
absence of aligned lyric words
```

to classify the region.

---

# 21. TELUGU NORMALIZATION

Normalization must produce two parallel representations.

```text
ORIGINAL TEXT
     |
     +--> display_text
     |
     `--> alignment_text
```

The final LRC and final JSON retain the original wording.

The alignment engine operates on normalized reference text.

---

# 22. NORMALIZATION PIPELINE

Preferred order:

```text
raw line
  |
  v
Unicode NFC
  |
  v
whitespace normalization
  |
  v
number normalization if configured
  |
  v
known abbreviation normalization if configured
  |
  v
alignment-token compatibility normalization
  |
  v
alignment text
```

Do not delete characters before a rule that needs those characters has had a chance to process them.

---

# 23. ORIGINAL WORD RECORD

Each original lyric word should have an internal mapping like:

```text
line_index
word_index
original_word
alignment_word
original_character_start
original_character_end
normalization_operations[]
```

Example:

```text
original_word = "1"
alignment_word = "ఒకటి"
normalization_operations = ["numeric_to_telugu"]
```

The output still displays the original word unless the user configuration explicitly specifies otherwise.

---

# 24. UNSUPPORTED TEXT

The Telugu MMS adapter may not represent every character or Latin word in the input.

Do not silently delete unsupported words.

For every unsupported word classify it as:

```text
alignment_status = unsupported
```

and preserve:

```text
original_word
```

If a timestamp can be inferred from neighboring aligned words, the word may receive:

```text
source = interpolated
```

otherwise:

```text
source = missing
```

This creates an honest distinction between:

```text
direct acoustic alignment
```

and:

```text
estimated timing
```

---

# 25. NORMALIZATION VERSIONING

The normalization algorithm must have a version.

Example:

```text
normalizer_version = 1.0.0
```

Any meaningful normalization rule change should increment this version.

The version must be stored in:

- database
- final JSON
- processing logs

so that later reruns can be compared.

---

# 26. LRC AS COARSE ALIGNMENT SCAFFOLD

For each lyric line, define:

```text
anchor_ms = original LRC timestamp
```

The anchor is used as a soft timing constraint.

A line should normally align close to its anchor unless the acoustic evidence strongly supports another position.

The allowed deviation should be configurable.

Suggested initial search behavior:

```text
normal line:
anchor ± 2.5 seconds
```

but the implementation should expand this window when neighboring anchors and long pauses make the broader region more plausible.

Do not use a hard-coded universal window without benchmarking.

---

# 27. ANCHOR-AWARE CHUNKING

Chunking is not merely slicing the audio every N seconds.

The chunker must consider:

1. LRC line boundaries
2. LRC anchor timestamps
3. blank markers
4. large gaps
5. VAD activity boundaries
6. energy minima
7. lyric word density
8. maximum model context
9. overlap context

---

# 28. CHUNK DURATION POLICY

Default guidance:

```text
minimum useful target: 4 sec
preferred target:       ~12 sec
maximum preferred:      20 sec
```

These are soft goals.

A natural phrase may be shorter than 4 seconds and should not be stretched artificially.

A dense phrase may need to remain slightly longer if splitting would destroy alignment coherence.

---

# 29. CHUNK PRIORITY ORDER

When choosing a boundary, use the following preference order:

```text
1. explicit blank LRC boundary
2. large lyric gap
3. strong LRC line boundary
4. strong vocal pause
5. energy minimum
6. word boundary
7. nearest practical fallback
```

Avoid splitting in the middle of a lyric phrase whenever possible.

---

# 30. OVERLAPPING CONTEXT

Each chunk has two ranges.

### Logical range

The part whose results are eligible for the final merged alignment.

### Model audio range

The larger region actually supplied to MMS.

Example:

```text
logical:
10.000 -> 22.000 sec

model audio:
 9.500 -> 22.500 sec
```

The extra context reduces boundary clipping.

---

# 31. CHUNK DATABASE FIELDS

Each chunk should store:

```text
song_id
chunk_index
logical_start_ms
logical_end_ms
audio_start_ms
audio_end_ms
line_start_index
line_end_index
text_content
normalized_text
word_count
status
attempt_count
audio_path
result_json_path
alignment_score
p10_score
minimum_score
error_code
error_remark
```

---

# 32. MMS MODEL INITIALIZATION

The implementation should use a pinned, tested MMS/Transformers environment.

Conceptually:

```text
model = facebook/mms-1b-all
language adapter = tel
processor = matching MMS processor
```

The current MMS documentation describes loading the processor/model for a target language and switching language adapters with the corresponding target language. The implementation must use the exact API compatible with the pinned Transformers version.

Important:

```text
ISO-639-1 = te
ISO-639-3 = tel
```

Use `tel` for the MMS language adapter where required by the model.

---

# 33. MODEL ADAPTER REQUIREMENT

Do not implement:

```text
set vocab size manually
and assume Telugu is active
```

The correct language-specific MMS configuration must be explicitly loaded using the model's supported target-language/adapter mechanism.

The exact initialization path must be tested against the pinned Transformers version before the full batch run.

---

# 34. MODEL CACHE

The model should be cached under:

```text
models/mms/
```

Cache metadata should include:

```text
model_name
model_revision
files_present
file_sizes
sha256 where practical
language_adapter
```

If a required model file is missing or corrupt, the application should refuse to continue with a false-success state.

---

# 35. MODEL LOADING STRATEGY

Load MMS once per worker process.

Do not load it once for every chunk.

Preferred lifecycle:

```text
start process
  |
  v
load MMS
  |
  v
load Telugu adapter
  |
  v
process many chunks
  |
  v
unload at process exit
```

If the application eventually supports multiple language models, model lifetime should remain worker-scoped.

---

# 36. MMS INPUT

For every chunk:

```text
16 kHz mono float waveform
```

The processor prepares the waveform for the model.

The application should record the exact number of audio samples and the resulting emission frame count.

---

# 37. MMS OUTPUT

The alignment engine needs:

```text
frame-level logits
```

then:

```text
log_probs = log_softmax(logits)
```

Do not immediately take `argmax` and throw away the distribution.

The alignment engine requires the full per-frame probability information.

---

# 38. WHY ARGMAX DECODING IS NOT THE ALIGNMENT

Argmax decoding answers approximately:

```text
what token did the model prefer at each frame?
```

Forced alignment answers:

```text
where does this supplied reference token sequence best fit the acoustic evidence?
```

The Phase 2 objective requires the second behavior.

Therefore the reference text is a direct input to the alignment algorithm.

---

# 39. CTC REFERENCE PREPARATION

For each chunk:

```text
normalized lyric text
       |
       v
MMS tokenizer
       |
       v
reference token IDs
```

The system must also create a mapping from:

```text
token positions
```

to:

```text
word positions
```

because the tokenizer's units and human word boundaries are not necessarily identical.

---

# 40. TOKENIZATION MAPPING

Maintain:

```text
reference_token_index
word_index
line_index
original_word_index
```

A token may map to:

- one word
- a repeated sub-token/character sequence belonging to one word
- a special/blank-compatible element

The token-to-word mapping must be deterministic and unit-tested.

---

# 41. CTC ALIGNMENT ALGORITHM

Implement the actual forced alignment behind:

```text
src/ctc_aligner.py
```

The algorithm should be isolated from MMS loading and LRC parsing.

Inputs:

```text
log_probs[T, C]
reference_tokens[U]
blank_token_id
```

Output:

```text
token spans
path scores
```

---

# 42. CTC TRELLIS CONCEPT

The forced-alignment implementation should use a dynamic-programming/Viterbi-style CTC path search.

Conceptually expand the target:

```text
blank, token1, blank, token2, blank, ..., tokenU, blank
```

At each frame, evaluate the permitted CTC transitions.

The implementation must handle repeated target tokens correctly.

For repeated neighboring tokens, the skip transition must respect CTC's repeated-label rules.

---

# 43. CTC ALIGNMENT OUTPUT

For every target token that receives an aligned span, produce:

```text
token_id
token_start_frame
token_end_frame
mean_log_probability
geometric_mean_probability
```

Frame indices are then converted to absolute milliseconds using the actual model emission frame rate derived for the exact implementation.

Do not hard-code an assumed frame stride without measuring/deriving it from the model configuration.

---

# 44. TOKEN TIMESTAMP CONVERSION

Each token span is initially local to the chunk.

Example:

```text
local_start_ms = 742
local_end_ms = 1038
```

Then convert to song time using the chunk's model-audio origin:

```text
absolute_start_ms = audio_start_ms + local_start_ms
absolute_end_ms   = audio_start_ms + local_end_ms
```

After that, logical-range filtering decides whether the token is retained.

---

# 45. WORD SPAN CONSTRUCTION

Group token spans according to the original reference word boundaries.

For each word:

```text
word_start_ms = first aligned token start
word_end_ms   = last aligned token end
```

If internal tokens contain gaps, preserve the overall word span while recording the alignment score.

---

# 46. WORD ALIGNMENT SCORE

The score should be derived from the CTC alignment path, not from an arbitrary single-frame maximum.

Recommended internal measures:

```text
mean_log_prob
geomean_prob
min_frame_prob
```

A convenience field:

```text
alignment_score
```

may be normalized to a 0–1 range, but it must be documented as a heuristic quality score rather than a calibrated probability of correctness.

---

# 47. LOW-CONFIDENCE WORDS

A word becomes low-confidence if its alignment score falls below the configured threshold or if its span is structurally suspicious.

Potential reasons:

```text
low acoustic score
very short span
very long span
unexpected gap
anchor drift
unsupported tokenization
```

Do not automatically delete a low-confidence word.

Keep it and mark it.

---

# 48. MISSING WORDS

A missing word means the reference word could not be assigned a trustworthy direct acoustic span.

It must remain in the reference order.

Possible final states:

```text
aligned
interpolated
missing
unsupported
```

---

# 49. INTERPOLATED WORDS

Interpolation is allowed only as a fallback.

If:

```text
previous trustworthy word = A
next trustworthy word = D
missing = B, C
```

the system may estimate B/C positions within the gap.

The method must be deterministic.

Every interpolated word must contain:

```text
source = interpolated
is_interpolated = true
```

and an interpolation reason.

Never report an interpolated timestamp as though it came directly from MMS/CTC.

---

# 50. CHUNK RETRIES

Every chunk should have an attempt count.

Recommended maximum:

```text
2 attempts
```

Attempt 1:

```text
standard model audio
```

Attempt 2 may change:

- model-audio overlap
- search window
- alignment audio source
- device
- chunk split

Do not simply rerun the exact same failed computation without changing a known failure condition.

---

# 51. LOW-CONFIDENCE CHUNK RETRY STRATEGY

When a chunk is low-confidence:

```text
1. inspect boundary/anchor drift
2. expand or contract model context
3. try original mix if vocal stem is suspicious
4. if the chunk contains multiple weak lines, split at a safe boundary
5. rerun CTC alignment
6. retain the best validated result
```

This is preferable to blindly averaging poor outputs.

---

# 52. OVERLAP DEDUPLICATION

Because chunks overlap, the same word can appear more than once in different local results.

For each duplicate candidate group:

1. prefer the candidate inside the chunk's logical region
2. then prefer the candidate with higher score
3. then prefer the candidate closer to the original LRC anchor structure
4. then use deterministic tie-breaking by chunk index

Only one word record survives into the canonical song alignment.

---

# 53. GLOBAL MERGE

The merge stage performs:

```text
local token spans
      |
      v
absolute timestamps
      |
      v
word construction
      |
      v
overlap deduplication
      |
      v
line assignment
      |
      v
song-wide chronological ordering
```

The merge must be deterministic.

Given the same inputs, model revision, configuration, and code version, the resulting ordering should be reproducible within the limits of the pinned runtime.

---

# 54. LINE-LEVEL RECONSTRUCTION

For every source LRC lyric line:

```text
original line text
```

remains authoritative for display.

The newly aligned word spans are mapped back into that line.

The new line start is:

```text
start of first directly or acceptably inferred word
```

This becomes the line-level anchor used inside the final JSON and word-level LRC export.

---

# 55. ORIGINAL LRC ANCHOR COMPARISON

For each line compare:

```text
source_lrc_start_ms
new_aligned_line_start_ms
```

Compute:

```text
shift_ms = new - original
absolute_shift_ms
```

Song-level metrics:

```text
median_anchor_shift_ms
mean_absolute_anchor_shift_ms
p90_anchor_shift_ms
max_anchor_shift_ms
```

These are valuable because the LRC already provides coarse timing information.

---

# 56. ANCHOR DRIFT DETECTION

Possible warning conditions:

```text
many consecutive lines shift in the same wrong direction
large unexplained shift at one line
sudden discontinuity between neighboring line shifts
alignment collapses into a neighboring verse/chorus
```

If severe:

```text
quality_status = needs_review
```

Do not fail every small anchor deviation. The purpose is to detect obvious alignment mistakes, not to force identical timestamps.

---

# 57. INSTRUMENTAL SECTION INFERENCE

An instrumental section is a region with:

```text
no reliable aligned lyric words
+
weak vocal/speech activity
+
large LRC gap or blank marker
```

Use evidence from:

- LRC gaps
- blank markers
- VAD
- energy
- final word spans

rather than VAD alone.

---

# 58. INSTRUMENTAL SECTION OUTPUT

The database and final JSON may contain:

```text
start_ms
end_ms
section_type
source_evidence
confidence
```

Possible `section_type` values:

```text
candidate_instrumental
confirmed_instrumental
silence
unknown_non_lyric
```

---

# 59. SAMPLE SONG GAP HANDLING

For the sample song, the two long blank regions should be recognized as high-value boundaries:

```text
97.290 -> 132.940 sec
191.720 -> 252.820 sec
```

The system should not try to force lyric words into those long empty regions.

Instead they should strongly influence:

- chunk boundaries
- model search windows
- validation
- instrumental candidate detection

---

# 60. CANONICAL WORD RECORD

Each final word should conceptually contain:

```json
{
  "line_index": 0,
  "word_index": 0,
  "original": "గెలుపు",
  "normalized": "గెలుపు",
  "start_ms": 25020,
  "end_ms": 25400,
  "score": 0.91,
  "source": "aligned",
  "interpolated": false
}
```

Optional fields:

```text
chunk_index
alignment_token_start
alignment_token_end
mean_log_probability
```

These optional diagnostic fields can remain in the database while the final JSON keeps the most useful subset.

---

# 61. CANONICAL LINE RECORD

Conceptually:

```json
{
  "line_index": 0,
  "source_lrc_start_ms": 25020,
  "aligned_start_ms": 25020,
  "original_text": "గెలుపు తలుపులే తీసే ఆకాశమే",
  "words": [
    {
      "original": "గెలుపు",
      "normalized": "గెలుపు",
      "start_ms": 25020,
      "end_ms": 25400,
      "score": 0.91,
      "source": "aligned"
    }
  ]
}
```

---

# 62. FINAL LRC DESIGN

The user has requested one final `.lrc` per song.

Therefore Phase 2 should not produce three separate LRC files by default.

The single final LRC should be the **word-level project LRC export**.

Recommended syntax:

```text
[00:25.02] ગెలుపు [00:25.40] తలుపులే [00:25.92] తీసే [00:26.20] ఆకాశమే
```

The first timestamp is also the line's start.

Each following word has its own timestamp.

Important:

This is an enhanced/project-specific word-level LRC representation, not something every basic LRC player is guaranteed to parse word-by-word.

The MP3 SYLT and final JSON are the authoritative machine-readable word-level forms.

---

# 63. LRC WORD TIMESTAMP PRECISION

The canonical database and JSON retain integer milliseconds.

The LRC renderer may use centiseconds because conventional LRC syntax is commonly represented to hundredths of a second.

Example:

```text
canonical: 25123 ms
LRC:       00:25.12
```

The rounding rule must be deterministic.

Do not use the rounded LRC timestamps to reconstruct the canonical word timing.

---

# 64. BLANK MARKERS IN FINAL LRC

The final LRC may preserve source blank-marker timestamps where they represent meaningful non-lyric boundaries.

Example:

```text
[01:37.29]
...
[03:11.72]
```

The rendering policy should be configured so that blank markers are either preserved or omitted consistently.

Recommended default:

```text
preserve source blank markers
```

because they can be useful to downstream players and preserve the original synchronization structure.

---

# 65. FINAL MP3 STRATEGY

The output MP3 is created from a direct copy of the original MP3.

Conceptually:

```text
original MP3
   |
   v
byte-for-byte copy
   |
   v
open ID3
   |
   v
add Phase 2 word-level SYLT
   |
   v
save
```

The audio payload must not be recompressed or transcoded by Phase 2 merely to change lyrics metadata.

---

# 66. METADATA PRESERVATION STRATEGY

Before modifying the output MP3, snapshot:

```text
frame keys
frame counts
important frame payload hashes
artwork hash/size
```

After modification, verify that all pre-existing metadata remains present unless the frame is explicitly the Phase 2 target frame.

The sample has existing metadata such as:

```text
TIT2
TPE1
TRCK
TALB
TPOS
TDRC
TCON
TLEN
TSRC
TPE2
TXXX:* custom fields
UFID:* identifiers
WXXX:* URLs
USLT
SYLT
APIC
```

The exact set may differ by song. The code must preserve whatever the input actually contains.

---

# 67. EXISTING SYLT MUST BE PRESERVED

The sample input already contains an existing SYLT frame.

Therefore the Phase 2 embedder must not do:

```python
if "SYLT" in tags:
    delete all SYLT
```

Instead the embedder should target its own unique descriptor.

Example conceptual descriptor:

```text
Phase2-WordLevel
```

Keep the pre-existing SYLT frame untouched.

---

# 68. PHASE 2 SYLT DESIGN

Create a synchronized lyrics frame using:

```text
language = tel
format = milliseconds
content type = lyrics
```

Use Unicode encoding supported by the chosen Mutagen/ID3 configuration.

The text entries should follow the canonical word-level timeline.

The exact SYLT descriptor must be unique and stable so that future Phase 2 reruns can replace only the previous Phase 2 frame.

---

# 69. PHASE 2 SYLT REPLACEMENT RULE

On rerun:

```text
find existing SYLT with Phase2 descriptor
     |
     v
replace only that frame
```

Do not replace:

- unrelated language frames
- unrelated descriptors
- existing lyric frames
- event frames
- other synchronization data

---

# 70. MP3 POST-EMBED VALIDATION

After saving the final-stage MP3:

1. Re-open the file.
2. Read its audio duration.
3. Confirm the audio still decodes.
4. Confirm the new Phase 2 SYLT exists.
5. Confirm the expected number of word entries.
6. Confirm timestamps are sorted.
7. Confirm timestamps are within duration.
8. Confirm the original metadata snapshot still matches.
9. Confirm artwork is still present when it was present before.
10. Confirm the file is not truncated or corrupt.

---

# 71. JSON OUTPUT GENERATION

The final JSON must be created from:

```text
original JSON object
```

plus:

```text
phase2
```

It must not be created from only a subset of the original data.

Recommended serialization:

```text
UTF-8
pretty-printed or consistently indented
ensure_ascii=false
```

so Telugu remains readable.

Do not alter numeric types unnecessarily.

Do not convert every number to strings merely for convenience.

---

# 72. PHASE 2 JSON ALIGNMENT CONTENT

The final JSON should contain enough word-level data to reconstruct the final LRC and inspect alignment without opening SQLite.

Recommended:

```text
phase2.alignment.lines[].words[]
```

For every word:

```text
original
normalized
start_ms
end_ms
score
source
```

This makes the final JSON self-contained.

---

# 73. JSON SIZE MANAGEMENT

The sample JSON is already large because it contains raw metadata.

Adding a few hundred word records is acceptable.

However, avoid duplicating:

- complete MMS logits
- complete chunk audio
- huge intermediate arrays
- entire model metadata blobs

inside the final JSON.

Those belong in temp/debug or logs if needed.

Final JSON should contain results and provenance, not raw inference tensors.

---

# 74. QUALITY METRICS

The final song should record at least:

```text
expected_lines
aligned_lines
expected_words
aligned_words
interpolated_words
missing_words
unsupported_words

total_chunks
successful_chunks
low_confidence_chunks
failed_chunks

mean_alignment_score
p10_alignment_score
minimum_alignment_score
low_score_word_percent
interpolated_word_percent
failed_audio_percent

median_anchor_shift_ms
p90_anchor_shift_ms
max_anchor_shift_ms
```

---

# 75. QUALITY METRIC PRINCIPLE

Never rely on a single average score.

Example:

```text
95% of words excellent
5% of words unusable
```

could still have a high mean.

Therefore use distribution metrics such as:

```text
mean
p10
minimum
low-score fraction
```

The lower tail is important.

---

# 76. VALIDATION — TIMING BOUNDS

Every canonical word must satisfy:

```text
0 <= start_ms < end_ms <= source_duration_ms
```

Any violation is a validation error.

---

# 77. VALIDATION — CHRONOLOGICAL ORDER

Within a line:

```text
word[n].start_ms <= word[n+1].start_ms
```

and generally:

```text
word[n].end_ms <= word[n+1].end_ms
```

Allow exact boundary equality if quantization causes it and the internal continuous representation remains valid.

Do not require every LRC timestamp to be strictly increasing because LRC export precision may collapse nearby millisecond values into the same centisecond.

---

# 78. VALIDATION — WORD DURATION

Every aligned word should have:

```text
end_ms > start_ms
```

Suspicious conditions:

```text
very tiny duration
very long duration
```

should produce warnings or low-confidence status rather than an immediate hard failure unless the span is structurally impossible.

---

# 79. VALIDATION — REFERENCE PRESERVATION

The expected reference sequence is derived from the LRC.

The final word sequence should preserve:

- line order
- word order
- original wording

Any difference must be explicitly classified as:

```text
normalized-only
unsupported
missing
interpolated
```

There should not be silent substitutions.

---

# 80. VALIDATION — ANCHOR DRIFT

Compare every aligned line with its source LRC anchor.

Recommended initial warning threshold:

```text
2500 ms
```

but use aggregate metrics rather than a simplistic rule that every line must be within exactly one threshold.

A song may have one slightly shifted line without being unusable.

A coherent large-scale drift is more serious.

---

# 81. VALIDATION — LRC/JSON CONSISTENCY

The final LRC and final JSON must be generated from the exact same canonical word data.

After generation, test:

```text
number of lines matches
word order matches
line words match
LRC timestamps correspond to JSON start_ms after documented rounding
```

Do not generate the JSON and LRC through independent timing calculations.

---

# 82. VALIDATION — MP3/JSON CONSISTENCY

The final JSON should state the final MP3 path and its final SHA-256 after embedding.

The final output file hash should be computed after the last modification.

This should be different from the original MP3 hash in the normal case because the MP3 metadata changed.

The original hash remains stored under:

```text
phase2.input.mp3_sha256
```

---

# 83. VALIDATION — SOURCE INTEGRITY

Before processing:

```text
source_hash = H(original MP3)
```

After all processing:

```text
source_hash_after = H(original MP3)
```

Require:

```text
source_hash_after == source_hash
```

This is one of the most important safety checks for the project.

---

# 84. PARTIAL RESULT POLICY

A song can be complete as a processing artifact while having some partial alignment.

Therefore classify separately:

```text
pipeline_status
quality_status
```

Example:

```text
pipeline_status = finished
quality_status = partial
```

This means the pipeline finished and produced valid files, but some timing was interpolated or unresolved.

---

# 85. RECOMMENDED QUALITY STATES

```text
good
partial
needs_review
failed
```

### good

Direct alignment is generally strong, outputs validate, and no major drift is detected.

### partial

Some words/chunks required interpolation/fallback but the package is still usable.

### needs_review

Processing completed but validation found suspicious timing or quality.

### failed

A trustworthy final package could not be produced.

---

# 86. DO NOT CLASSIFY ONLY BY FAILED-CHUNK PERCENTAGE

A failed chunk containing an entire chorus can be much more important than several short failed chunks.

Quality should therefore consider:

```text
failed word count
failed audio duration
interpolated word count
low-score word concentration
anchor drift
```

rather than only:

```text
failed_chunks / total_chunks
```

---

# 87. DATABASE ARCHITECTURE

The Phase 2 database is independent:

```text
db/phase2.db
```

It must not modify or attach to the Phase 1 database.

Recommended tables:

```text
songs
chunks
words
instrumental_sections
processing_log
runs
```

---

# 88. `songs` TABLE

Recommended schema:

```sql
CREATE TABLE songs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    basename TEXT NOT NULL UNIQUE,

    original_mp3_path TEXT NOT NULL,
    original_lrc_path TEXT NOT NULL,
    original_json_path TEXT,

    original_mp3_sha256 TEXT NOT NULL,
    original_lrc_sha256 TEXT NOT NULL,
    original_json_sha256 TEXT,

    final_mp3_path TEXT,
    final_lrc_path TEXT,
    final_json_path TEXT,

    title TEXT,
    artist TEXT,
    album TEXT,

    duration_ms INTEGER,

    expected_lines INTEGER DEFAULT 0,
    aligned_lines INTEGER DEFAULT 0,

    expected_words INTEGER DEFAULT 0,
    aligned_words INTEGER DEFAULT 0,
    interpolated_words INTEGER DEFAULT 0,
    missing_words INTEGER DEFAULT 0,
    unsupported_words INTEGER DEFAULT 0,

    total_chunks INTEGER DEFAULT 0,
    successful_chunks INTEGER DEFAULT 0,
    low_confidence_chunks INTEGER DEFAULT 0,
    failed_chunks INTEGER DEFAULT 0,

    mean_alignment_score REAL,
    p10_alignment_score REAL,
    minimum_alignment_score REAL,

    low_score_word_percent REAL,
    interpolated_word_percent REAL,
    failed_audio_percent REAL,

    median_anchor_shift_ms REAL,
    p90_anchor_shift_ms REAL,
    max_anchor_shift_ms REAL,

    pipeline_status TEXT NOT NULL DEFAULT 'pending',
    quality_status TEXT NOT NULL DEFAULT 'unknown',

    retry_count INTEGER DEFAULT 0,
    error_code TEXT,
    error_remark TEXT,

    pipeline_version TEXT,
    model_name TEXT,
    model_revision TEXT,
    normalizer_version TEXT,
    config_hash TEXT,

    started_at TIMESTAMP,
    completed_at TIMESTAMP,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

# 89. `chunks` TABLE

```sql
CREATE TABLE chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    song_id INTEGER NOT NULL,
    chunk_index INTEGER NOT NULL,

    logical_start_ms INTEGER NOT NULL,
    logical_end_ms INTEGER NOT NULL,

    audio_start_ms INTEGER NOT NULL,
    audio_end_ms INTEGER NOT NULL,

    line_start_index INTEGER,
    line_end_index INTEGER,

    text_content TEXT NOT NULL,
    normalized_text TEXT NOT NULL,

    word_count INTEGER DEFAULT 0,

    alignment_score REAL,
    p10_alignment_score REAL,
    minimum_alignment_score REAL,

    status TEXT NOT NULL DEFAULT 'pending',

    audio_path TEXT,
    result_json_path TEXT,

    attempt_count INTEGER DEFAULT 0,

    error_code TEXT,
    error_remark TEXT,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY(song_id) REFERENCES songs(id),
    UNIQUE(song_id, chunk_index)
);
```

---

# 90. `words` TABLE

```sql
CREATE TABLE words (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    song_id INTEGER NOT NULL,
    chunk_id INTEGER,

    line_index INTEGER NOT NULL,
    word_index INTEGER NOT NULL,

    original_word TEXT NOT NULL,
    normalized_word TEXT NOT NULL,

    start_ms INTEGER,
    end_ms INTEGER,

    alignment_score REAL,

    source TEXT NOT NULL,
    -- aligned / interpolated / missing / unsupported

    is_interpolated INTEGER NOT NULL DEFAULT 0,

    FOREIGN KEY(song_id) REFERENCES songs(id),
    FOREIGN KEY(chunk_id) REFERENCES chunks(id)
);
```

---

# 91. `instrumental_sections` TABLE

```sql
CREATE TABLE instrumental_sections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    song_id INTEGER NOT NULL,

    start_ms INTEGER NOT NULL,
    end_ms INTEGER NOT NULL,

    section_type TEXT NOT NULL,

    detector TEXT,
    confidence REAL,
    evidence_json TEXT,

    FOREIGN KEY(song_id) REFERENCES songs(id)
);
```

---

# 92. `processing_log` TABLE

```sql
CREATE TABLE processing_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    song_id INTEGER,

    step TEXT NOT NULL,
    level TEXT NOT NULL,

    attempt INTEGER DEFAULT 1,

    message TEXT,
    metadata_json TEXT,

    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

# 93. `runs` TABLE

```sql
CREATE TABLE runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMP,

    pipeline_version TEXT NOT NULL,
    config_hash TEXT NOT NULL,

    model_name TEXT NOT NULL,
    model_revision TEXT,
    language_code TEXT NOT NULL,
    normalizer_version TEXT NOT NULL
);
```

---

# 94. PIPELINE STATUS STATE MACHINE

Processing state:

```text
pending
   ↓
scanning
   ↓
lyrics_loaded
   ↓
isolating
   ↓
isolated
   ↓
chunking
   ↓
chunked
   ↓
aligning
   ↓
aligned
   ↓
merging
   ↓
merged
   ↓
validating
   ↓
validated
   ↓
embedding
   ↓
finalizing
   ↓
finished
```

Terminal error state can be represented separately by `quality_status` or an explicit error code.

---

# 95. QUALITY STATE MACHINE

Separate field:

```text
unknown
   ↓
good / partial / needs_review / failed
```

This separation prevents ambiguous states such as one string simultaneously meaning "processing stopped" and "output quality is questionable."

---

# 96. RESUME MODEL

On program restart:

```text
open DB
   |
   v
inspect unfinished songs
   |
   v
inspect last safe stage
   |
   v
resume from checkpoint
```

Examples:

```text
isolated
-> resume chunking
```

```text
chunked
-> resume alignment
```

```text
aligned
-> resume merge
```

```text
merged
-> resume validation
```

```text
validated
-> resume embedding/finalization
```

---

# 97. STALE JOB RECOVERY

If a process crashes while a song is marked:

```text
aligning
```

the program must not assume that another process is still working.

Use:

```text
updated_at
started_at
attempt_count
```

and a configurable stale timeout.

A stale record becomes resumable.

---

# 98. IDEMPOTENT RE-RUN

On a new run, if:

```text
original MP3 hash unchanged
original LRC hash unchanged
original JSON hash unchanged
pipeline version unchanged
config hash unchanged
model revision unchanged
final outputs exist
final outputs validate
```

then skip processing.

If any of those change, process again.

---

# 99. FORCE REPROCESS

Recommended CLI mode:

```text
--force
```

This must not edit source files.

It should simply rebuild a new final package and replace the previous final package only after validation succeeds.

---

# 100. RECOVERY AFTER PARTIAL FINALIZATION

A crash could occur after:

```text
final MP3 written
```

but before:

```text
final JSON updated
```

Therefore startup should check:

```text
final MP3 exists
final LRC exists
final JSON exists
DB says finished?
```

If DB does not confirm a successful package, validate the files and either:

```text
complete the database record
```

or:

```text
discard/replace the incomplete staged package
```

Do not mark success solely because files happen to exist.

---

# 101. STAGING FINAL OUTPUTS

Do not write directly into `songs/final/` during processing.

Use:

```text
temp/{song_key}/stage_final/
```

Create and validate:

```text
stage_final/Song.mp3
stage_final/Song.lrc
stage_final/Song.json
```

Only after all three are valid should they be promoted to `songs/final/`.

---

# 102. FINALIZATION ORDER

Recommended order:

```text
1. build final JSON in stage
2. build final LRC in stage
3. build final MP3 in stage
4. validate all three
5. validate source unchanged
6. compute output hashes
7. promote final files
8. record final database state
```

If any validation fails, keep the stage files for debugging and do not claim success.

---

# 103. FINAL OUTPUT FILE NAMES

The basename must not change.

Example:

```text
input:
001_Gelupu Thalupule_Mani Sharma, Sreerama Chandra.mp3

output:
001_Gelupu Thalupule_Mani Sharma, Sreerama Chandra.mp3
```

Likewise for `.lrc` and `.json`.

No title normalization should change filenames during Phase 2.

---

# 104. FINAL LRC GENERATOR MODULE

`src/lrc_generator.py` responsibilities:

- consume canonical word records
- keep original lyric text
- round canonical milliseconds for LRC
- emit line-level first timestamp
- emit word timestamps
- preserve blank marker policy
- preserve relevant LRC metadata if desired
- generate deterministic output

It must not perform fresh alignment calculations.

---

# 105. EMBEDDER MODULE

`src/embedder.py` responsibilities:

- copy source MP3 into staging
- open existing tags
- identify Phase 2-owned SYLT descriptor
- replace only the old Phase 2 frame if present
- add the new Phase 2 SYLT
- save
- reopen
- validate

No audio transcoding.

---

# 106. JSON UPDATER MODULE

`src/json_updater.py` responsibilities:

- deep-copy source JSON structure
- add/update `phase2`
- preserve unknown fields
- include input hashes
- include model provenance
- include output paths
- include final quality results
- include word-level alignment
- serialize UTF-8 safely

---

# 107. SCANNER MODULE

`src/scanner.py` responsibilities:

- scan input directory
- identify basename packages
- detect missing sidecars
- calculate hashes
- detect duplicates
- create/update song rows
- produce scan summary

It should not perform heavy ML work.

---

# 108. JSON MANAGER MODULE

`src/json_manager.py` responsibilities:

- parse JSON
- deep-copy original data
- provide metadata accessors
- compute source JSON hash
- protect unknown fields
- provide a controlled phase2 namespace updater

---

# 109. LRC READER MODULE

`src/lrc_reader.py` responsibilities:

- parse timestamps
- preserve text
- detect blank markers
- detect metadata tags
- construct ordered lyric lines
- calculate source timing intervals

---

# 110. AUDIO MODULE

`src/audio.py` responsibilities:

- inspect MP3
- decode source audio
- resample
- mono conversion
- slice audio for chunks
- validate duration
- support deterministic sample indexing

---

# 111. DEMUCS MODULE

`src/demucs_isolator.py` responsibilities:

- Demucs model execution
- GPU/CPU fallback
- memory management
- vocal stem extraction
- duration normalization
- output validation

---

# 112. ACTIVITY MODULE

`src/activity_detector.py` responsibilities:

- load Silero VAD
- compute speech/activity timestamps
- compute energy map
- merge activity signals
- expose pause candidates

It should not directly modify chunk or song DB rows outside controlled pipeline calls.

---

# 113. NORMALIZER MODULE

`src/telugu_normalizer.py` responsibilities:

- NFC normalization
- whitespace normalization
- optional numeric conversion
- optional abbreviation expansion
- token compatibility normalization
- original-to-normalized mapping
- normalization versioning

---

# 114. TOKENIZER MODULE

`src/tokenizer.py` responsibilities:

- interface with the pinned MMS tokenizer
- tokenize normalized reference text
- map token indices to word indices
- expose blank token ID
- expose vocabulary metadata

---

# 115. CHUNKER MODULE

`src/chunker.py` responsibilities:

- construct logical lyric chunks
- add model context overlap
- use LRC anchors
- use VAD/energy boundaries
- avoid lyric phrase splitting
- generate chunk text
- write chunk DB records

---

# 116. MMS MODEL MODULE

`src/mms_model.py` responsibilities:

- load MMS
- load Telugu adapter
- manage model device
- generate frame-level emissions
- keep model loaded
- provide a narrow interface to CTC aligner

The module must not convert emissions into final words.

---

# 117. CTC ALIGNER MODULE

`src/ctc_aligner.py` responsibilities:

- accept frame-level log probabilities
- accept reference token sequence
- perform reference-driven forced alignment
- return token spans and path scores
- handle repeated target tokens correctly
- handle impossible alignments gracefully

This is the core research/algorithmic module of the project.

---

# 118. WORD BUILDER MODULE

`src/word_builder.py` responsibilities:

- map token spans to reference words
- derive word start/end
- calculate word scores
- map normalized words back to originals
- classify aligned/interpolated/missing/unsupported

---

# 119. MERGER MODULE

`src/merger.py` responsibilities:

- local -> absolute timestamp conversion
- overlap deduplication
- line association
- global ordering
- canonical song alignment creation
- gap/instrumental candidate construction

---

# 120. VALIDATOR MODULE

`src/validator.py` responsibilities:

- timing bounds
- chronological checks
- word-span checks
- reference preservation
- score distribution
- anchor drift
- LRC/JSON consistency
- MP3 metadata preservation
- final output validity

---

# 121. RECOVERY MODULE

`src/recovery.py` responsibilities:

- stale job detection
- retry selection
- checkpoint selection
- cleanup decisions
- idempotency checks
- incomplete-final-package recovery

---

# 122. PIPELINE ORCHESTRATOR

`src/pipeline.py` should be orchestration only.

It should not contain detailed CTC math, LRC parsing, metadata logic, or Demucs commands.

Conceptually:

```text
scan
-> load
-> isolate
-> detect
-> normalize
-> chunk
-> infer
-> align
-> merge
-> validate
-> render
-> embed
-> update JSON
-> validate final package
-> promote
```

---

# 123. CONFIGURATION

Recommended `config.json` sections:

```json
{
  "paths": {
    "original_dir": "songs/original",
    "final_dir": "songs/final",
    "temp_dir": "temp",
    "models_dir": "models",
    "db_path": "db/phase2.db"
  },

  "models": {
    "demucs_model": "htdemucs",
    "mms_model": "facebook/mms-1b-all",
    "language_iso1": "te",
    "language_iso3": "tel"
  },

  "audio": {
    "sample_rate": 16000,
    "channels": 1
  },

  "vad": {
    "enabled": true,
    "threshold": 0.50,
    "min_speech_duration_ms": 250,
    "min_silence_duration_ms": 300,
    "speech_pad_ms": 150
  },

  "chunking": {
    "min_seconds": 4,
    "target_seconds": 12,
    "max_seconds": 20,
    "context_before_ms": 500,
    "context_after_ms": 500,
    "pause_threshold_ms": 1500,
    "large_gap_threshold_ms": 3000,
    "anchor_search_ms": 2500
  },

  "alignment": {
    "word_score_min": 0.40,
    "song_mean_score_min": 0.50,
    "max_low_score_word_percent": 20,
    "max_interpolated_word_percent": 20
  },

  "runtime": {
    "device": "cuda",
    "fallback_device": "cpu",
    "workers": 1
  },

  "recovery": {
    "max_chunk_attempts": 2,
    "max_song_attempts": 3,
    "stale_after_minutes": 30
  }
}
```

All thresholds are configurable and benchmark targets, not universal truths.

---

# 124. DEPENDENCY STRATEGY

The implementation should use an exact lockfile rather than only minimum versions.

Core functional categories:

```text
transformers
PyTorch
audio I/O / decoding
Demucs
Silero VAD
librosa or equivalent analysis utilities
soundfile
Mutagen
NumPy
```

Do not rely on deprecated TorchAudio forced-alignment APIs as the core implementation.

Current TorchAudio documentation states that the forced-alignment APIs discussed in older tutorials were deprecated and removed as TorchAudio moved into maintenance mode. Therefore the project should isolate its own CTC aligner and use it independently of deprecated `torchaudio.functional.forced_align` behavior.

Audio I/O should be compatible with the pinned environment and may use current TorchCodec-backed facilities where appropriate.

---

# 125. MMS LICENSE / PROVENANCE

The `facebook/mms-1b-all` model card currently identifies the model as CC-BY-NC-4.0.

The project documentation should record the model license and model revision used.

This does not change the processing design but is important provenance information.

---

# 126. PERFORMANCE DESIGN

The project is intended for long-running batch processing.

Primary performance costs:

```text
Demucs
MMS inference
CTC dynamic programming
audio I/O
```

LRC parsing, normalization, SQLite updates, and JSON augmentation should be comparatively inexpensive.

---

# 127. MODEL MEMORY POLICY

Do not run Demucs and MMS concurrently on the same GPU by default.

Preferred:

```text
Demucs
   |
   v
release Demucs GPU resources
   |
   v
MMS alignment
```

This minimizes VRAM contention.

---

# 128. WORKER POLICY

Default:

```text
1 GPU worker
```

This is the safest initial production configuration.

Parallelism can be added later after memory and throughput benchmarks.

Do not launch many MMS instances against one GPU simply because multiple CPU workers are available.

---

# 129. PERFORMANCE BENCHMARKING PLAN

Before the full corpus run, benchmark at least:

```text
1 short song
1 medium song
1 long song
1 song with heavy instrumental sections
1 song with dense Telugu lyrics
1 song with difficult/low-volume vocals
```

Measure:

```text
Demucs seconds/song
MMS seconds/chunk
CTC seconds/chunk
peak VRAM
peak RAM
I/O throughput
failure rate
```

Use observed results for final throughput estimates.

---

# 130. BATCH PROGRESS REPORTING

The application should periodically print:

```text
processed
finished
good
partial
needs_review
failed
remaining
average processing time
estimated throughput
```

Do not display speculative completion times as guarantees.

---

# 131. LOGGING LEVELS

Support:

```text
INFO
WARNING
ERROR
DEBUG
```

Every major processing step should produce an INFO event.

Every retry should produce a WARNING.

Every unrecoverable failure should produce an ERROR.

---

# 132. STRUCTURED LOGGING

Prefer structured JSON metadata in `processing_log` rather than putting every detail into a long free-text message.

Example:

```json
{
  "step": "ctc_alignment",
  "chunk_index": 7,
  "attempt": 2,
  "device": "cuda",
  "frame_count": 936,
  "token_count": 52,
  "score": 0.74
}
```

---

# 133. ERROR CODES

Use stable error codes.

Recommended:

```text
MP3_MISSING
LRC_MISSING
JSON_MISSING
JSON_INVALID
MP3_INVALID
LRC_INVALID
HASH_ERROR
AUDIO_DECODE_FAILED
DEMUCS_FAILED
VOCALS_INVALID
VAD_FAILED
NORMALIZATION_FAILED
TOKENIZATION_FAILED
MMS_LOAD_FAILED
MMS_INFERENCE_FAILED
CTC_ALIGNMENT_FAILED
TOO_MANY_FAILED_CHUNKS
TIMESTAMP_INVALID
ANCHOR_DRIFT
LRC_GENERATION_FAILED
SYLT_EMBED_FAILED
MP3_VALIDATION_FAILED
JSON_WRITE_FAILED
FINAL_PROMOTION_FAILED
SOURCE_MODIFIED
```

---

# 134. RETRY CLASSIFICATION

Not every error deserves a retry.

### Retryable examples

```text
CUDA OOM
transient file access
temporary model runtime failure
chunk too large
```

### Usually non-retryable

```text
missing LRC
invalid JSON
unsupported audio
corrupt source file
```

### Review-worthy

```text
low confidence
anchor drift
high interpolation rate
unexpected word-span structure
```

---

# 135. SOURCE FILE SAFETY CHECK

At startup and shutdown for a song:

```text
hash original MP3
hash original LRC
hash original JSON
```

The hashes must remain unchanged during Phase 2.

This should be tested automatically, not assumed.

---

# 136. JSON MERGE SAFETY TEST

Before writing the final JSON:

```text
original = deep copy(input_json)
final = deep copy(input_json)
update(final["phase2"])
```

Then verify:

```text
all original top-level keys still exist
all non-phase2 values are unchanged
```

A regression test should compare the original and final JSON after removing only the intentionally added `phase2` subtree.

---

# 137. MP3 METADATA SAFETY TEST

Before embedding:

```text
snapshot frame structure
```

After embedding:

```text
snapshot frame structure
```

Verify:

```text
all pre-existing frame keys remain
all important frame payloads remain equivalent
APIC remains
UFID remains
WXXX remains
custom TXXX remains
existing USLT remains
existing SYLT remains
```

The only expected new/changed data should be the Phase 2-owned SYLT.

---

# 138. FINAL JSON EXAMPLE FOR THE SAMPLE SONG

Conceptually the existing large JSON remains intact and gains:

```json
"phase2": {
  "schema_version": 1,
  "pipeline_version": "1.0.0",
  "status": "finished",
  "quality_status": "good",

  "input": {
    "basename": "001_Gelupu Thalupule_Mani Sharma, Sreerama Chandra",
    "source_type": "external_lrc_plus_json",
    "mp3_sha256": "<original hash>",
    "lrc_sha256": "<original hash>",
    "json_sha256": "<original hash>"
  },

  "audio": {
    "duration_ms": 317592,
    "sample_rate": 16000,
    "channels": 1
  },

  "lyrics": {
    "line_count": 42,
    "lyric_line_count": 39,
    "blank_timing_markers": 3,
    "source": "external_lrc"
  },

  "model": {
    "name": "facebook/mms-1b-all",
    "language": "tel",
    "revision": "<pinned revision>"
  },

  "vocal_isolation": {
    "model": "htdemucs",
    "device": "cuda",
    "status": "success"
  },

  "alignment": {
    "method": "ctc_forced_alignment",
    "lines": []
  },

  "instrumental_sections": [
    {
      "start_ms": 97290,
      "end_ms": 132940,
      "type": "candidate_instrumental",
      "evidence": ["lrc_blank_marker", "lrc_gap", "activity_gap"]
    },
    {
      "start_ms": 191720,
      "end_ms": 252820,
      "type": "candidate_instrumental",
      "evidence": ["lrc_blank_marker", "lrc_gap", "activity_gap"]
    }
  ],

  "quality": {
    "mean_alignment_score": null,
    "p10_alignment_score": null,
    "minimum_alignment_score": null,
    "interpolated_word_percent": null,
    "median_anchor_shift_ms": null,
    "max_anchor_shift_ms": null
  },

  "outputs": {
    "mp3": "songs/final/001_Gelupu Thalupule_Mani Sharma, Sreerama Chandra.mp3",
    "lrc": "songs/final/001_Gelupu Thalupule_Mani Sharma, Sreerama Chandra.lrc",
    "json": "songs/final/001_Gelupu Thalupule_Mani Sharma, Sreerama Chandra.json"
  }
}
```

The actual final JSON should also contain the complete aligned line/word data.

---

# 139. FINAL JSON WORD-LEVEL EXAMPLE

Inside:

```text
phase2.alignment.lines[]
```

an aligned line should look like:

```json
{
  "line_index": 0,
  "source_lrc_start_ms": 25020,
  "aligned_start_ms": 25020,
  "original_text": "గెలుపు తలుపులే తీసే ఆకాశమే",
  "words": [
    {
      "word_index": 0,
      "original": "గెలుపు",
      "normalized": "గెలుపు",
      "start_ms": 25020,
      "end_ms": 25450,
      "score": 0.89,
      "source": "aligned"
    },
    {
      "word_index": 1,
      "original": "తలుపులే",
      "normalized": "తలుపులే",
      "start_ms": 25480,
      "end_ms": 26110,
      "score": 0.92,
      "source": "aligned"
    }
  ]
}
```

These numbers are illustrative. The actual system must derive them from the model alignment.

---

# 140. FINAL LRC EXAMPLE

For the same illustrative result:

```text
[00:25.02] గెలుపు [00:25.48] తలుపులే [00:26.11] తీసే [00:26.68] ఆకాశమే
```

Do not hard-code example times into the implementation.

---

# 141. FINAL MP3 SYLT EXAMPLE

Canonical data:

```text
(
  "గెలుపు", 25020
)
(
  "తలుపులే", 25480
)
(
  "తీసే", 26110
)
(
  "ఆకాశమే", 26680
)
```

This same timing is stored in the Phase 2-owned SYLT frame.

---

# 142. ONE SOURCE OF TRUTH

The following must all derive from the same canonical word data:

```text
final JSON
final LRC
final MP3 SYLT
SQLite words table
```

There must not be independent timing calculations in each exporter.

---

# 143. EXPORT ORDER

Recommended internal flow:

```text
canonical alignment
       |
       +--> database words
       |
       +--> JSON alignment
       |
       +--> LRC export
       |
       `--> SYLT export
```

---

# 144. VALIDATION BEFORE EXPORT

Do not generate final outputs from an invalid canonical alignment.

Order:

```text
merge
  |
  v
canonical validation
  |
  +-- fail -> review/debug
  |
  `-- pass -> exporters
```

---

# 145. VALIDATION AFTER EXPORT

After all outputs are built:

```text
validate JSON
validate LRC
validate MP3
validate metadata preservation
validate cross-file consistency
```

Only then promote.

---

# 146. LRC VALIDATION

Check:

```text
timestamps parse
words are present
word order is preserved
line order is preserved
timestamps are non-decreasing
all times are within song duration
blank marker policy is respected
```

---

# 147. JSON VALIDATION

Check:

```text
valid UTF-8
valid JSON syntax
all original top-level keys exist
phase2 exists
alignment word count consistent
outputs paths correct
source hashes correct
status correct
```

---

# 148. MP3 VALIDATION

Check:

```text
file exists
file decodes
ID3 loads
new Phase2 SYLT exists
old metadata still exists
artwork still exists where originally present
source audio duration unchanged
```

---

# 149. CROSS-FILE VALIDATION

The three final files should agree on:

```text
basename
title where applicable
duration
line count
word count
processing version
output identity
```

The MP3 and JSON should also agree on the existence of the Phase 2 word-level SYLT output.

---

# 150. FINAL OUTPUT HASHES

Record final hashes:

```text
final_mp3_sha256
final_lrc_sha256
final_json_sha256
```

Store these in the database and in:

```text
phase2.outputs
```

inside the final JSON.

---

# 151. OUTPUT PATHS IN JSON

Always use the actual final relative paths:

```text
songs/final/SongName.mp3
songs/final/SongName.lrc
songs/final/SongName.json
```

Do not store temporary absolute paths in the final JSON unless a dedicated debug field is explicitly desired.

---

# 152. TEMP CLEANUP POLICY

After successful finalization:

```text
remove temp/{song_key}/
```

After `needs_review` or `failed`:

```text
retain temp/{song_key}/
```

This keeps debugging artifacts only when they are useful.

---

# 153. TEMP RETENTION POLICY

Retained failed/review artifacts should include:

```text
source snapshot metadata
source_16k.wav
vocals_16k.wav
chunk audio
chunk normalized text
chunk alignment results
processing logs
```

Do not retain gigantic raw model logits by default.

They may optionally be enabled with a debug flag for one song.

---

# 154. DEBUG MODE

Recommended:

```text
--debug
--keep-temp
--song <basename>
```

Debug mode should make it easy to inspect one problematic song without changing batch behavior.

---

# 155. DRY RUN

Recommended:

```text
--dry-run
```

Dry run should:

- scan files
- verify matching
- hash inputs
- validate JSON
- parse LRC
- inspect audio metadata
- report expected work

It must not run Demucs, MMS, or modify outputs.

---

# 156. SINGLE-SONG MODE

Required for development and acceptance testing:

```text
--song "001_Gelupu Thalupule_Mani Sharma, Sreerama Chandra"
```

The exact CLI syntax can be adjusted during implementation.

---

# 157. BATCH MODES

Recommended operations:

```text
--all
--failed
--needs-review
--missing-output
--song <basename>
--force
```

---

# 158. TESTING STRATEGY

The project must not start with the full corpus.

Test in layers.

---

# 159. UNIT TESTS — FILE MATCHING

Test:

```text
Song.mp3 + Song.lrc + Song.json
```

Also:

```text
Song.mp3 only
Song.mp3 + Song.lrc
Song.mp3 + Song.json
```

and duplicate basenames.

---

# 160. UNIT TESTS — LRC PARSER

Test:

```text
[00:25.02] Telugu line
```

```text
[01:37.29]
```

```text
[00:25.020] line
```

```text
multiple same timestamps
```

Test malformed timestamps.

---

# 161. UNIT TESTS — JSON PRESERVATION

Create a fixture containing:

```text
known keys
unknown keys
nested objects
nested arrays
null values
Unicode Telugu text
```

After Phase 2 update, verify all original values remain identical outside `phase2`.

---

# 162. UNIT TESTS — NORMALIZATION

Test:

```text
Telugu Unicode normalization
multiple spaces
leading/trailing whitespace
numbers
Latin fragments
punctuation
abbreviations
```

Verify the original word remains recoverable.

---

# 163. UNIT TESTS — CTC ALIGNER

Create synthetic emissions where the correct token sequence has an obvious best path.

Test:

```text
single token
multiple tokens
repeated token
blank-heavy region
short reference
long reference
impossible reference
```

The repeated-token test is especially important because CTC transition rules are easy to implement incorrectly.

---

# 164. UNIT TESTS — TOKEN TO WORD MAPPING

Test:

```text
one token per word
multiple tokens per word
repeated characters
blank-separated units
```

Verify word spans map back to correct original words.

---

# 165. UNIT TESTS — LRC RENDERER

Given a known canonical alignment, expected LRC output should be exact.

Test rounding at:

```text
124 ms
125 ms
129 ms
130 ms
```

and near second/minute boundaries.

---

# 166. UNIT TESTS — SYLT EMBEDDING

Use a fixture MP3 containing many different metadata frames.

Before:

```text
snapshot
```

After embedding:

```text
verify old metadata
verify new SYLT
```

This protects the most important metadata-preservation requirement.

---

# 167. INTEGRATION TEST — SAMPLE SONG

Use:

```text
001_Gelupu Thalupule_Mani Sharma, Sreerama Chandra.mp3
001_Gelupu Thalupule_Mani Sharma, Sreerama Chandra.lrc
001_Gelupu Thalupule_Mani Sharma, Sreerama Chandra.json
```

Expected input characteristics:

```text
song duration ~318 sec
39 lyric lines
3 blank markers
2 major blank regions
large metadata JSON
many existing MP3 ID3 frames
existing SYLT/USLT/APIC
```

---

# 168. SAMPLE SONG ACCEPTANCE TESTS

For the sample song, verify:

1. All three source files are matched by basename.
2. Source SHA-256 values are captured.
3. Source JSON loads.
4. Source LRC produces 42 timestamp entries.
5. 39 lyric lines are recognized.
6. 3 blank markers are recognized.
7. The two large lyric-free gaps are detected as candidate boundaries.
8. Demucs produces a valid vocal stem.
9. MMS loads with Telugu adapter.
10. At least one real CTC reference alignment completes.
11. Final word count is recorded.
12. Existing JSON top-level keys survive unchanged.
13. Final JSON gains `phase2`.
14. Existing MP3 artwork survives.
15. Existing MP3 custom TXXX/UFID/WXXX data survives.
16. Existing SYLT/USLT survive.
17. New Phase2 SYLT exists.
18. Final LRC exists.
19. Final JSON exists.
20. All three files share the same basename.
21. Source MP3/LRC/JSON hashes remain unchanged.

---

# 169. FAILURE-INJECTION TESTS

Deliberately test:

```text
missing LRC
invalid JSON
corrupt MP3
Demucs failure
CUDA unavailable
MMS loading failure
MMS OOM
CTC alignment failure
one failed chunk
multiple failed chunks
JSON write interruption
MP3 embed failure
process kill during alignment
process kill during finalization
```

The system must recover or fail cleanly according to its documented state.

---

# 170. METADATA-LOSS REGRESSION TEST

This test is mandatory.

Take the real sample MP3.

Record every existing frame key and a stable representation of each frame.

Run Phase 2.

Reopen the final MP3.

Verify that pre-existing metadata remains.

This test should run automatically before the project is considered production-ready.

---

# 171. JSON-LOSS REGRESSION TEST

Take the real sample JSON.

Run Phase 2.

Remove the `phase2` subtree from the final JSON.

Compare the result to the original JSON semantically.

Expected:

```text
identical
```

If anything else changed, the JSON updater has violated the preservation rule.

---

# 172. ORIGINAL-FILE PROTECTION TEST

Hash the original files before the full run.

Run Phase 2.

Hash the original files afterward.

Expected:

```text
same hashes
```

A changed source hash is a critical failure.

---

# 173. RESUME TEST

Start a song.

Interrupt the process during:

```text
Demucs
chunking
MMS alignment
merge
embedding
```

Restart.

Verify that the pipeline resumes from the appropriate checkpoint rather than restarting the entire song unnecessarily.

---

# 174. IDPOTENCY TEST

Run Phase 2 twice against the same input/config/model.

Expected second run:

```text
skip
```

or safely rebuild only if forced.

No duplicate metadata frame should be created.

No duplicate words should appear.

---

# 175. REPROCESSING TEST

Change one of:

```text
LRC hash
config hash
model revision
pipeline version
```

Then run again.

Expected:

```text
new processing run
```

not a stale skip.

---

# 176. QUALITY REVIEW WORKFLOW

For songs marked `needs_review`, the final JSON should tell the operator why.

Example:

```json
"quality": {
  "status": "needs_review",
  "reasons": [
    "anchor_drift",
    "high_low_score_word_percent"
  ]
}
```

No manual review should be required for normal processing, but the result must contain enough information to inspect problematic songs later.

---

# 177. OPTIONAL ALIGNMENT DEBUG REPORT

For debug mode only, generate a text/JSON report containing:

```text
line
word
source anchor
aligned start
aligned end
score
source type
```

This is not a required final output file.

---

# 178. OPTIONAL AUDIO DEBUG PLOTS

For a future debug tool, one can generate a timeline containing:

```text
original LRC anchors
VAD regions
aligned word spans
instrumental candidates
```

This should remain outside the production final package.

---

# 179. DATA LINEAGE

Every final word should be traceable through:

```text
final JSON word
    -> chunk
    -> normalized reference word
    -> source LRC line
    -> source LRC timestamp
    -> audio segment
    -> MMS emissions
    -> CTC alignment path
```

This is important for future troubleshooting.

---

# 180. PROVENANCE FIELDS

Record:

```text
pipeline_version
schema_version
config_hash
model_name
model_revision
language
normalizer_version
source hashes
run ID
```

This should appear at least in the database and final JSON.

---

# 181. CONFIG HASH

Compute a deterministic SHA-256 of the normalized configuration.

Store:

```text
config_hash
```

This prevents two runs with visibly similar but technically different thresholds from being treated as identical.

---

# 182. PIPELINE VERSION

Use semantic-style versioning:

```text
MAJOR.MINOR.PATCH
```

Examples:

```text
1.0.0
1.1.0
2.0.0
```

A timing algorithm change that can change word boundaries should increment at least the minor version and may warrant a major version depending on scope.

---

# 183. MODEL REVISION

Do not store only:

```text
facebook/mms-1b-all
```

Also store the exact model revision/commit used by the run where possible.

This supports reproducibility.

---

# 184. NORMALIZER REVISION

Do not change normalization rules silently.

Store:

```text
normalizer_version
```

and include it in processing identity.

---

# 185. LRC FORMAT VERSION

The final LRC export should have a format version in JSON:

```text
phase2.outputs.lrc_format = "phase2-wordlevel-lrc-v1"
```

This makes downstream consumers aware of the custom word-level syntax.

---

# 186. CANONICAL TIME UNIT

Internal authoritative time unit:

```text
integer milliseconds
```

Use integer milliseconds in:

- SQLite
- final JSON
- SYLT

Only the LRC renderer converts to display syntax.

---

# 187. FLOATING-POINT POLICY

Do not store canonical timestamps as floating-point seconds when integer milliseconds are sufficient.

Floating-point values may be used internally for signal processing, but final time records should be integer milliseconds.

---

# 188. AUDIO SAMPLE INDEX POLICY

Where precise audio slicing matters, maintain sample indices in addition to milliseconds internally.

Example:

```text
start_sample
end_sample
```

at 16 kHz.

Convert to milliseconds only when creating timing outputs.

This reduces repeated rounding drift.

---

# 189. CHUNK ROUNDING POLICY

Chunk boundaries should be represented internally by sample indices or integer milliseconds consistently.

Do not independently round the same boundary three times in three modules.

The canonical chunk boundaries are created once and passed downstream.

---

# 190. TOKEN FRAME RATE POLICY

Do not assume the emission frame rate based on a generic Wav2Vec2 rule without verifying the exact model configuration.

The implementation should derive the mapping from audio samples to model frames using the actual model's downsampling behavior/configuration.

Store the resulting frame-to-time mapping in the chunk diagnostic data.

---

# 191. CTC MEMORY POLICY

CTC dynamic programming memory is roughly proportional to:

```text
frames × target-state-count
```

Therefore chunking exists not only for Demucs/MMS but also to keep forced alignment computationally manageable.

If a chunk's reference is too large, split it before attempting an alignment that would exceed memory limits.

---

# 192. CTC FAILURE CASES

Possible impossible alignment causes:

```text
reference too long for available frames
empty target token sequence
unsupported target token
corrupt emission shape
model returned invalid values
```

The aligner should return a typed failure rather than raising an opaque exception wherever possible.

---

# 193. NaN / INF DEFENSE

After MMS inference and before CTC alignment, check:

```text
no NaN
no +INF
no -INF
```

If present:

```text
MMS_INFERENCE_FAILED
```

and retry appropriately.

---

# 194. SILENCE / EMPTY REFERENCE CHUNKS

A blank-marker gap must never generate a fake reference alignment.

Do not call the CTC aligner with an empty transcript simply to fill time.

Instead record the region as:

```text
non_lyric candidate
```

and use it for instrumental inference.

---

# 195. LONG INSTRUMENTAL REGIONS

Long gaps are important for processing efficiency.

Do not run MMS on a 60-second purely instrumental region with no reference text.

The chunker should assign chunks around lyric-bearing regions and leave confirmed non-lyric regions out of reference alignment.

This reduces unnecessary GPU work.

---

# 196. LYRIC START / END DETECTION

The song may contain long intro/outro sections.

Do not require the first lyric timestamp to be near zero.

Do not require the last lyric timestamp to be near song duration.

The sample demonstrates this pattern, with the final lyric timing well before the end of the ~318-second audio.

---

# 197. DURATION VALIDATION RULE

The correct rule is:

```text
all aligned timestamps must be within source duration
```

not:

```text
last lyric must be within ±10% of song duration
```

Intros and outros are valid.

---

# 198. WORD COUNT VALIDATION RULE

The expected word sequence comes from the source LRC.

Do not use a broad rule such as:

```text
final word count ±20%
```

as the main validity criterion.

Instead explicitly record:

```text
expected
aligned
interpolated
missing
unsupported
```

This is far more informative.

---

# 199. LINE COUNT VALIDATION

The final alignment must preserve the number and order of source lyric lines except for explicitly classified blank markers.

The sample input has 39 lyric lines and 3 blank timing markers.

The final JSON should retain that structure.

---

# 200. DUPLICATED LYRIC LINES

Repeated chorus lines are valid.

Do not deduplicate them by text.

Each occurrence must retain its own line index and timing.

Example:

```text
గెలుపు తలుపులే...
```

may occur multiple times and each occurrence is a separate alignment event.

---

# 201. CHORUS / VERSE REPETITIONS

The alignment model must use temporal context, not text uniqueness.

The same lyric phrase appearing at:

```text
00:25
01:25
03:00
05:00
```

must remain four separate occurrences.

---

# 202. WORD BOUNDARY POLICY

Word boundaries come from the source lyric text, not from whatever word segmentation MMS happens to predict.

This is an important distinction.

The supplied lyric wording defines the final words.

CTC only determines where the reference tokens associated with those words occur acoustically.

---

# 203. PUNCTUATION POLICY

Punctuation may be omitted from the acoustic token reference if the tokenizer does not support it, but it must not be silently lost from the display text.

Therefore:

```text
original = display form
normalized = alignment form
```

---

# 204. ENGLISH / LATIN WORD POLICY

If the lyric contains English or Latin words inside a Telugu song:

1. Preserve the original word.
2. Determine whether the Telugu MMS tokenizer can represent it.
3. If yes, align normally.
4. If not, classify as unsupported.
5. Attempt a controlled fallback only if explicitly configured.
6. Never silently delete it from the final display.

---

# 205. NUMBER POLICY

Numbers may be handled by one of three modes:

```text
preserve
convert_to_telugu
unsupported
```

Default should be conservative and deterministic.

If conversion is enabled, record the transformation in the normalization mapping.

---

# 206. ABBREVIATION POLICY

Maintain a small versioned dictionary.

Example concept:

```text
"Sr." -> "శ్రీ"
```

The dictionary must not be hard-coded without versioning.

Record:

```text
normalization operation
```

when applied.

---

# 207. ORIGINAL TEXT IS USER-FACING

Every output visible to the user should default to the source LRC's original wording.

Normalization is an internal alignment convenience.

Do not silently replace lyrics with model-spelled variants.

---

# 208. NO ASR SUBSTITUTION

MMS may internally produce a likely transcription that differs from the source lyric.

The system must not overwrite the source lyric with MMS's guessed spelling.

That would defeat the purpose of reference alignment.

---

# 209. OPTIONAL ASR DIAGNOSTIC

For research/debugging, the system may calculate an MMS argmax transcription for diagnostics.

If enabled:

```text
store as diagnostic only
```

Do not use it as the production lyric output unless a future explicitly approved alternate mode is implemented.

---

# 210. CHUNK ALIGNMENT SEARCH WINDOW

For an LRC-anchored chunk, define an expected time window.

The chunk's reference lines should fall generally inside:

```text
logical_start_ms
logical_end_ms
```

The model audio includes overlap.

If alignment lands entirely outside the logical region, treat it as suspicious and retry or subdivide.

---

# 211. SOFT ANCHOR CHECK

Rather than modifying the CTC score too aggressively in the first implementation, use the LRC anchor primarily as a search constraint and post-alignment validator.

Later versions may add soft temporal penalties.

This keeps the first production implementation understandable and testable.

---

# 212. GLOBAL ALIGNMENT VS CHUNK ALIGNMENT

The project uses chunk-level alignment for computational practicality.

However, correctness must be song-aware.

The merger should validate the complete sequence globally after local alignments are produced.

Do not assume independently successful chunks imply a globally correct song.

---

# 213. CHUNK QUALITY AGGREGATION

Song-level score can be weighted by word duration or word count.

Recommended primary weighting:

```text
word-count weighted
```

with additional reporting by audio duration.

Do not let a tiny two-word chunk dominate the overall mean.

---

# 214. P10 SCORE

The 10th percentile of word scores is particularly useful.

If the mean is high but P10 is very low, the song contains a long tail of questionable words.

This should be reflected in `quality_status`.

---

# 215. INTERPOLATION LIMIT

A configurable default can be:

```text
interpolated_word_percent <= 20%
```

but this should be a quality rule rather than an absolute processing failure.

Also report interpolation by line and by gap.

---

# 216. LARGE FAILED GAP POLICY

If a single failed region spans a large portion of the song, classify accordingly even if the total failed-chunk count is small.

Record:

```text
failed_audio_ms
```

and:

```text
failed_word_count
```

---

# 217. QUALITY REASONS

Recommended machine-readable reasons:

```text
LOW_MEAN_SCORE
LOW_P10_SCORE
MANY_LOW_SCORE_WORDS
HIGH_INTERPOLATION
MISSING_WORDS
ANCHOR_DRIFT
TIMESTAMP_ERROR
CHUNK_FAILURE
VOCAL_STEM_QUALITY
```

---

# 218. OUTPUT STATUS IN JSON

The final JSON should contain:

```text
phase2.status
phase2.quality_status
phase2.quality.reasons[]
```

so a downstream consumer can understand both completion and quality.

---

# 219. MP3 METADATA OWNERSHIP

Phase 2 owns only:

```text
Phase2-WordLevel SYLT
```

and optionally a small number of Phase 2-specific TXXX fields if useful.

If Phase 2 adds such fields, their ownership must be namespaced/documented.

Do not overwrite Phase 1 custom TXXX fields.

---

# 220. OPTIONAL PHASE 2 TXXX FIELDS

If desired, add:

```text
TXXX:phase2_version
TXXX:phase2_status
TXXX:phase2_model
TXXX:phase2_alignment_method
```

but keep these additions minimal.

The detailed history belongs in JSON.

---

# 221. ARTWORK PRESERVATION

If the original MP3 has an APIC artwork frame, it must remain unchanged unless the user explicitly asks otherwise.

The sample already contains a front-cover APIC with the artwork referenced in the JSON.

Phase 2 must never re-download or replace artwork as part of word synchronization.

---

# 222. URL / IDENTIFIER PRESERVATION

Existing:

```text
UFID
WXXX
TXXX
```

data must remain intact.

The sample already contains YouTube Music/YouTube/Open Spotify-related identifiers and URLs.

These are historical metadata, not alignment inputs, and must not be discarded.

---

# 223. FINAL JSON ARTWORK / SOURCE DATA

Phase 2 should not duplicate existing artwork/source structures into the `phase2` namespace unless necessary.

It should reference them conceptually through existing JSON sections and add only Phase 2-specific data.

---

# 224. MODEL DOWNLOAD POLICY

The model can be downloaded/cached as part of environment setup or first-run initialization.

The song input pipeline itself must not use external lyric searching or audio downloading.

If model files are unavailable and network access is disabled, the run should fail clearly rather than silently substituting another model.

---

# 225. NETWORK POLICY

No network access should be required for processing a song once:

```text
MMS model is cached
Demucs model is cached
Silero model is cached
```

This is desirable for reproducible batch runs.

---

# 226. MODEL VERSION LOCK

Before the 1,892-song batch, freeze:

```text
Python version
PyTorch version
Transformers version
Demucs version
Silero VAD version
Mutagen version
NumPy version
audio I/O dependencies
model revision
```

Do not upgrade packages halfway through a batch.

---

# 227. BATCH RUN MANIFEST

At batch start, create a run record containing:

```text
run_id
start_time
pipeline_version
config_hash
model revision
language
environment versions
```

At batch end:

```text
finish_time
counts by status
```

This run data should be linked to individual songs.

---

# 228. BATCH SUMMARY

At completion, report:

```text
Total songs discovered
Songs processable
Finished-good
Finished-partial
Needs-review
Failed
Skipped
Total words aligned
Total words interpolated
Average score
Median score where tracked
Total processing time
```

---

# 229. SAFE INTERRUPTION

A Ctrl+C or process termination should:

1. stop launching new work
2. finish current safe DB transaction if possible
3. leave current chunk/song state resumable
4. preserve temp data for incomplete work
5. never modify source files

---

# 230. SQLITE TRANSACTION POLICY

Use transactions for state changes.

For example:

```text
chunk result written
+
chunk status updated
```

should be committed together.

This prevents a chunk from being marked successful when its output row was never saved.

---

# 231. DATABASE FOREIGN KEYS

Enable SQLite foreign keys explicitly.

Use:

```sql
PRAGMA foreign_keys = ON;
```

This protects chunk/word relationships.

---

# 232. DATABASE INDEXES

Recommended indexes:

```text
songs(pipeline_status)
songs(quality_status)
songs(original_mp3_sha256)
chunks(song_id, status)
words(song_id, line_index, word_index)
```

These improve batch resume and inspection.

---

# 233. DATABASE BACKUP

Because the database tracks long-running work, provide a simple backup mechanism.

Recommended:

```text
copy phase2.db before major schema migrations
```

and document migration versions.

---

# 234. DATABASE SCHEMA VERSION

Maintain:

```text
PRAGMA user_version
```

or an explicit schema metadata table.

Never silently change schema during a batch.

---

# 235. MIGRATION STRATEGY

Schema changes should use versioned migrations:

```text
001_initial.sql
002_add_provenance.sql
003_add_quality_metrics.sql
```

This is especially important if the project is developed iteratively during a long corpus run.

---

# 236. OUTPUT DIRECTORY POLICY

If `songs/final/` does not exist:

```text
create it
```

If files already exist:

```text
do not blindly overwrite
```

Use validation plus input/config identity to decide whether to skip or force reprocess.

---

# 237. FINAL REPLACEMENT SAFETY

If an old final package exists and a new package is being generated:

```text
old final files remain untouched during processing
```

Only after the new staged package validates should replacement occur.

This prevents a failed rerun from destroying a previously good result.

---

# 238. PACKAGE CONSISTENCY TOKEN

The final JSON should contain:

```text
phase2.run_id
```

and optionally:

```text
phase2.package_id
```

This identifies which processing result produced all three final files.

---

# 239. PACKAGE ID

A package ID can be derived from:

```text
source hashes
+
run ID
```

or simply use a UUID generated at processing start.

Store it in JSON and DB.

The MP3 need not contain this unless desired.

---

# 240. FINAL JSON COMPLETENESS RULE

The final JSON should never report:

```text
status = finished
```

until:

```text
final MP3 exists
final LRC exists
final JSON exists
final MP3 validates
final LRC validates
final JSON validates
```

---

# 241. FINALIZATION ORDER DETAIL

A robust finalization process:

```text
A. Build canonical alignment
B. Validate canonical alignment
C. Generate staged LRC
D. Generate staged MP3
E. Generate staged JSON
F. Validate each staged output
G. Validate cross-output consistency
H. Validate source hashes unchanged
I. Compute staged output hashes
J. Promote staged files
K. Commit DB final status
L. Cleanup temp
```

---

# 242. IF PROMOTION FAILS

If a file move fails:

```text
final package is not considered complete
```

Do not update the DB to `finished`.

Keep staged files and logs for recovery.

---

# 243. IF JSON PROMOTION SUCCEEDS BUT DB UPDATE FAILS

On restart:

```text
validate final package
compare run/package metadata
reconcile DB
```

The database should be repairable from the final outputs.

---

# 244. RECONCILIATION COMMAND

Recommended:

```text
--repair-state
```

It should inspect:

```text
songs/final/
phase2.db
```

and repair obvious state mismatches without touching source files.

---

# 245. FINAL DIRECTORY SHOULD BE USER-FRIENDLY

The user should only need to look at:

```text
songs/final/
```

for finished results.

Everything else is implementation detail.

---

# 246. EXPLICITLY NO EXTRA PER-SONG FINAL FILES

Do not place these in `songs/final/` by default:

```text
alignment.json
vocals.wav
debug.json
chunk files
log files
model outputs
```

Those belong in temp/debug storage.

The user's requested final package stays exactly three files.

---

# 247. OPTIONAL FUTURE EXPORTS

Future versions may offer:

```text
CSV word timing
WebVTT
JSON-only export
SRT
```

but these are out of scope for the first implementation and must not complicate the final directory now.

---

# 248. CORE IMPLEMENTATION ORDER

Recommended build order:

```text
1. config
2. database
3. scanner
4. JSON preservation layer
5. LRC parser
6. audio decoder
7. Demucs wrapper
8. VAD/activity
9. Telugu normalizer
10. tokenizer mapping
11. independent CTC aligner
12. MMS wrapper
13. word builder
14. merger
15. validator
16. LRC exporter
17. SYLT embedder
18. JSON updater
19. recovery
20. pipeline orchestrator
21. end-to-end sample test
22. batch mode
```

---

# 249. WHY CTC ALIGNER ISOLATION IS CRITICAL

The biggest algorithmic risk is the alignment engine.

By putting it behind:

```text
src/ctc_aligner.py
```

we can replace or improve the actual dynamic-programming implementation later without rewriting:

- LRC parsing
- metadata handling
- Demucs
- database
- exports
- resume logic

---

# 250. CTC ALIGNER ACCEPTANCE CRITERIA

Before full-batch processing, the CTC aligner must demonstrate:

```text
reference text is actually used
repeated tokens align correctly
blank transitions work
word boundaries are preserved
spans are chronological
scores are finite
impossible references fail cleanly
```

This is the primary algorithm gate.

---

# 251. MODEL ADAPTER ACCEPTANCE CRITERIA

Before full-batch processing, verify:

```text
MMS base model loads
Telugu adapter loads
processor vocabulary matches model head
inference returns expected logits shape
```

The exact initialization code must match the pinned Transformers version.

---

# 252. DEMUCS ACCEPTANCE CRITERIA

Before full batch:

```text
GPU path works
CPU fallback works
vocals duration aligns
output is readable
working audio is lossless
```

---

# 253. LRC INPUT ACCEPTANCE CRITERIA

Before full batch:

```text
all source LRC lines parse
blank markers survive
Telugu UTF-8 survives
line order survives
```

---

# 254. JSON ACCEPTANCE CRITERIA

Before full batch:

```text
full input JSON loads
all source keys preserved
phase2 can be added
Unicode survives
unknown nested structures survive
```

---

# 255. MP3 ACCEPTANCE CRITERIA

Before full batch:

```text
existing ID3 reads
artwork preserved
custom frames preserved
existing lyric frames preserved
new Phase2 SYLT writes
file still decodes
```

---

# 256. FULL SAMPLE GATE

The real sample song should be processed successfully from:

```text
songs/original/
```

to:

```text
songs/final/
```

with the source hashes unchanged and no metadata loss.

The sample is the first production-style golden test.

---

# 257. BATCH ROLLOUT PLAN

Do not immediately process the entire corpus.

Use stages:

```text
Stage 1: 1 sample
Stage 2: 5 songs
Stage 3: 25 songs
Stage 4: 100 songs
Stage 5: 500 songs
Stage 6: full corpus
```

At each stage inspect:

```text
failure rate
quality distribution
GPU memory
runtime
metadata preservation
JSON growth
```

---

# 258. GO/NO-GO GATES

Do not increase batch size if any of these are unresolved:

```text
metadata loss
source file mutation
incorrect JSON merge
CTC alignment corruption
systematic anchor drift
frequent GPU OOM
unrecoverable resume bugs
```

---

# 259. QUALITY SAMPLING

Even if automatic validation passes, sample a subset of finished songs for manual listening/inspection.

Recommended sampling:

```text
random songs
lowest-score songs
highest-interpolation songs
largest-anchor-drift songs
longest songs
songs with large instrumental gaps
```

This is a validation strategy, not a requirement for routine user interaction.

---

# 260. REVIEW QUEUE

The DB should make it possible to query:

```sql
SELECT *
FROM songs
WHERE quality_status = 'needs_review';
```

and:

```sql
SELECT *
FROM songs
WHERE quality_status = 'partial';
```

This supports targeted inspection.

---

# 261. REVIEW REASON SUMMARY

For every review song, produce a compact reason string such as:

```text
anchor_drift; low_p10_score
```

rather than only:

```text
needs_review
```

---

# 262. ERROR MESSAGE QUALITY

Error messages should answer:

```text
what failed?
where?
why?
which attempt?
can it retry?
```

Example:

```text
CTC_ALIGNMENT_FAILED: song=187 chunk=12 attempt=2; reference token sequence could not be fit within emission frames; retry budget exhausted
```

---

# 263. NO SILENT FALLBACKS

Do not silently:

- switch languages
- switch models
- use a different lyric source
- drop unsupported words
- discard existing metadata
- replace original text

Every fallback must be recorded.

---

# 264. FALLBACK HIERARCHY

For alignment:

```text
1. normal vocals + CTC reference alignment
2. adjusted chunk/context + CTC
3. original mix + CTC
4. interpolation where neighboring aligned words support it
5. missing/needs_review
```

Do not jump directly from a failed chunk to an unrelated transcription method.

---

# 265. NO WHISPER

Whisper is explicitly outside the Phase 2 architecture.

The system does not need a transcription model to discover the lyrics because the lyric reference already exists in the sidecar LRC.

This is precisely why reference-driven forced alignment is appropriate.

---

# 266. WHY REFERENCE ALIGNMENT IS PREFERRED

The source LRC already tells us:

```text
what the words are
line order
approximate timing
```

The acoustic model only needs to answer:

```text
where those known words occur in the audio
```

That is a better fit than asking a speech recognizer to guess the lyric text from scratch.

---

# 267. MODEL OUTPUT AS EVIDENCE

The MMS model should be thought of as an acoustic scorer.

The lyric text remains the reference.

The CTC aligner connects the two.

This separation makes the architecture easier to reason about and validate.

---

# 268. FINAL CANONICAL DATA FLOW

```text
LRC text
  |
  v
normalized reference
  |
  v
tokens
  |
  +--------------------+
                       |
Audio -> MMS -> emissions
                       |
                       v
                CTC forced alignment
                       |
                       v
                  token spans
                       |
                       v
                  word spans
                       |
                       v
              canonical alignment
```

---

# 269. OUTPUT CANONICAL DATA FLOW

```text
canonical alignment
       |
       +--> SQLite words
       |
       +--> final JSON phase2.alignment
       |
       +--> final word-level LRC
       |
       `--> final MP3 Phase2 SYLT
```

---

# 270. COMPLETE SONG LIFECYCLE

```text
DISCOVER
  |
  v
MATCH MP3/LRC/JSON
  |
  v
HASH SOURCE FILES
  |
  v
LOAD JSON
  |
  v
PARSE LRC
  |
  v
VALIDATE AUDIO
  |
  v
DEMUCS
  |
  v
ACTIVITY MAP
  |
  v
NORMALIZE REFERENCE
  |
  v
ANCHOR MAP
  |
  v
CHUNK
  |
  v
MMS EMISSIONS
  |
  v
CTC FORCED ALIGNMENT
  |
  v
TOKEN -> WORD
  |
  v
QUALITY
  |
  v
MERGE
  |
  v
VALIDATE
  |
  v
LRC EXPORT
  |
  v
MP3 SYLT EMBED
  |
  v
JSON UPDATE
  |
  v
FINAL VALIDATION
  |
  v
ATOMIC/STAGED PROMOTION
  |
  v
DATABASE COMPLETE
  |
  v
CLEANUP
```

---

# 271. FINAL CONTRACT — INPUT

For each song:

```text
songs/original/SongName.mp3
songs/original/SongName.lrc
songs/original/SongName.json
```

The basename must match exactly.

---

# 272. FINAL CONTRACT — PROCESSING

Phase 2:

```text
reads source files
isolates vocals
analyzes activity
normalizes reference text
uses LRC anchors
chunks reference/audio
runs MMS
performs true CTC reference alignment
creates word timestamps
validates result
embeds SYLT
updates cumulative JSON
```

---

# 273. FINAL CONTRACT — OUTPUT

```text
songs/final/SongName.mp3
songs/final/SongName.lrc
songs/final/SongName.json
```

The final JSON contains all previous JSON data plus Phase 2 details.

The final MP3 contains all previous metadata plus the Phase 2 word-level SYLT.

The final LRC contains the original lyric wording with Phase 2 word-level timing.

---

# 274. FINAL CONTRACT — SOURCE PRESERVATION

After completion:

```text
original MP3 unchanged
original LRC unchanged
original JSON unchanged
```

This must be automatically verified.

---

# 275. FINAL CONTRACT — METADATA PRESERVATION

The final MP3 must retain:

```text
existing title
existing artist
existing album
existing track/disc data
existing artwork
existing IDs
existing URLs
existing custom TXXX frames
existing USLT
existing SYLT
```

plus the new Phase 2 word-level SYLT.

---

# 276. FINAL CONTRACT — JSON PRESERVATION

The final JSON must retain:

```text
all original keys
all original nested data
all previous source metadata
all previous processing history
```

and add:

```text
phase2
```

---

# 277. FINAL CONTRACT — TIMING

Canonical timing unit:

```text
milliseconds
```

Canonical word data:

```text
start_ms
end_ms
```

LRC is rounded only at export time.

SYLT uses the canonical millisecond timestamps.

---

# 278. FINAL CONTRACT — ALIGNMENT METHOD

Canonical alignment method:

```text
MMS frame-level acoustic emissions
+
source LRC reference tokens
+
CTC forced alignment
```

Not:

```text
MMS ASR transcription offsets
```

---

# 279. FINAL CONTRACT — RESUME

A stopped run must continue from the last completed safe stage.

Successful chunks must not be recomputed unnecessarily.

Source files must remain untouched.

---

# 280. FINAL CONTRACT — REPRODUCIBILITY

Every output should be traceable to:

```text
source MP3 hash
source LRC hash
source JSON hash
pipeline version
config hash
model name
model revision
normalizer version
run ID
```

---

# 281. PRODUCTION READINESS CHECKLIST

Before processing the entire collection, all must be true:

```text
[ ] File matching works
[ ] Source hashing works
[ ] Original files remain unchanged in tests
[ ] Full JSON preservation test passes
[ ] MP3 metadata preservation test passes
[ ] LRC parser passes fixtures
[ ] Telugu normalization mapping passes
[ ] MMS Telugu adapter loads
[ ] CTC aligner passes synthetic tests
[ ] Token -> word mapping passes
[ ] Chunking passes sample tests
[ ] Overlap dedup passes
[ ] Anchor drift validation passes
[ ] LRC exporter passes
[ ] SYLT embedder passes
[ ] Final JSON updater passes
[ ] Final package validation passes
[ ] Resume test passes
[ ] Idempotency test passes
[ ] Failure recovery tests pass
[ ] Sample song completes
[ ] 5-song pilot completes
[ ] 25-song pilot reviewed
[ ] Environment is locked
[ ] Model revision is recorded
```

---

# 282. FIRST IMPLEMENTATION MILESTONE

Milestone 1 should **not** be the full pipeline.

Build and prove:

```text
scanner
+
LRC parser
+
JSON preservation
+
MP3 metadata snapshot
+
CTC aligner synthetic tests
```

This creates a safe foundation before expensive GPU work is introduced.

---

# 283. SECOND IMPLEMENTATION MILESTONE

Build:

```text
Demucs
VAD/activity
Telugu normalization
MMS emissions
CTC forced alignment
```

Test only on one song.

---

# 284. THIRD IMPLEMENTATION MILESTONE

Build:

```text
merge
validation
LRC export
SYLT embedding
JSON update
```

Run end-to-end on the sample song.

---

# 285. FOURTH IMPLEMENTATION MILESTONE

Add:

```text
resume
retry
stale recovery
idempotency
batch mode
```

Then run the 5-song pilot.

---

# 286. FIFTH IMPLEMENTATION MILESTONE

Scale carefully:

```text
25
-> 100
-> 500
-> full corpus
```

Only advance when quality and metadata-preservation metrics remain stable.

---

# 287. TROUBLESHOOTING PRIORITY

When a result is bad, inspect in this order:

```text
1. source LRC correctness
2. audio duration alignment
3. Demucs stem quality
4. chunk boundaries
5. MMS language adapter
6. CTC reference tokenization
7. CTC alignment path
8. word grouping
9. overlap merge
10. validation/renderer
```

Do not immediately blame MMS if the reference-to-token mapping is wrong.

---

# 288. MOST IMPORTANT ENGINEERING RISKS

The main risks are:

```text
1. incorrect CTC forced alignment implementation
2. wrong MMS language adapter configuration
3. destructive JSON update
4. destructive MP3 tag update
5. chunk boundary errors
6. over-trusting VAD
7. normalization destroying reference wording
8. incorrect token-to-word mapping
9. overlap duplication
10. resume state inconsistencies
```

These receive the strongest tests.

---

# 289. MOST IMPORTANT DATA RISKS

The project must guard against:

```text
wrong MP3/LRC/JSON pairing
wrong basename
changed source file
lost metadata
lost JSON history
wrong repeated chorus assignment
missing words
silent unsupported words
false instrumental classification
```

---

# 290. MOST IMPORTANT ALIGNMENT RISKS

Alignment can fail because:

```text
lyrics differ slightly from sung version
MMS model mishears Telugu
vocal stem contains artifacts
LRC anchor is inaccurate
words are very fast
words overlap musically
background vocals interfere
chunk cuts a phrase
unsupported symbols disrupt tokenization
```

The architecture addresses these through reference alignment, overlap, normalization mapping, retries, and validation.

---

# 291. DO NOT FORCE FALSE PRECISION

A word-level timestamp is only useful if it is reasonably trustworthy.

When the system cannot establish a good direct alignment, it must say:

```text
interpolated
missing
needs_review
```

rather than creating a visually precise but unsupported number and calling it exact.

---

# 292. QUALITY SHOULD BE TRANSPARENT

Every word can be thought of as having:

```text
text
start
end
confidence
provenance
```

This makes the result inspectable rather than magical.

---

# 293. FINAL JSON AS CUMULATIVE SONG RECORD

The final JSON becomes the long-term record for the song.

It contains:

```text
previous source metadata
previous processing information
Phase 2 alignment
Phase 2 quality
Phase 2 provenance
final output paths
```

A later phase or tool can therefore read one final JSON file and understand the song's processing history without needing Phase 1's database.

---

# 294. NO HIDDEN DEPENDENCY ON PHASE 1

Even if Phase 1 originally created:

```text
video_id
playlist position
Spotify IDs
YouTube metadata
artwork data
LRCLIB information
```

Phase 2 must treat these only as data already present in the JSON/MP3.

Phase 2 must still work if the same JSON structure were produced by another project.

---

# 295. FILE-LEVEL CONTRACT OVER PROJECT-LEVEL CONTRACT

The correct dependency is:

```text
Phase 1 (or previous work)
        |
        v
MP3 + LRC + JSON
        |
        v
Phase 2
```

not:

```text
Phase 2
   -> import Phase 1 modules
   -> query Phase 1 DB
```

This keeps Phase 2 portable.

---

# 296. PORTABILITY GOAL

Phase 2 should be movable to another machine by copying:

```text
phase2_project/
songs/original/
models/
```

plus the locked environment.

It should not require the original Phase 1 repository.

---

# 297. REBUILDABILITY GOAL

If `db/phase2.db` is lost, the project should still be able to rescan:

```text
songs/original/
```

and recover the input inventory.

The final JSON also contains enough provenance to understand completed outputs.

---

# 298. FINAL JSON AS RECOVERY SOURCE

If a song has:

```text
final MP3
final LRC
final JSON
```

but its DB row is missing, a future reconciliation command should be able to inspect the final JSON's `phase2` section and reconstruct a completed state.

---

# 299. CUMULATIVE JSON VERSIONING

The existing JSON already contains its own historical project/schema information.

Phase 2 should not rewrite those version fields merely because Phase 2 exists.

Instead add:

```text
phase2.pipeline_version
phase2.schema_version
```

so historical versioning remains understandable.

---

# 300. PROJECT-SCOPE SUMMARY

This project is intentionally narrow:

```text
INPUT
MP3 + LRC + JSON

TRANSFORM
line-level reference -> word-level timing

OUTPUT
MP3 + LRC + JSON

PRESERVE
all original source information

MODEL
MMS Telugu

ALIGNMENT
CTC forced alignment

AUDIO
Demucs vocal isolation

ACTIVITY
Silero VAD + energy

STATE
SQLite

RECOVERY
checkpointed/resumable
```

---

# 301. FINAL REFERENCE PIPELINE

The final architecture, without omissions, is:

```text
                    songs/original/
                           |
          +----------------+----------------+
          |                |                |
          v                v                v
       Song.mp3         Song.lrc         Song.json
          |                |                |
          |                +-------> parse |
          |                                 |
          +-----------> inventory <---------+
                           |
                           v
                    source fingerprints
                           |
                           v
                    JSON preservation
                           |
                           v
                       LRC model
                           |
                           +--------------------+
                           |                    |
                           v                    v
                     line anchors          blank markers
                           |                    |
                           +---------+----------+
                                     |
                                     v
                             audio preparation
                                     |
                                     v
                               Demucs vocals
                                     |
                                     v
                              VAD + energy map
                                     |
                                     v
                         reversible normalization
                                     |
                                     v
                            reference chunking
                                     |
                                     v
                              MMS Telugu adapter
                                     |
                                     v
                           frame-level emissions
                                     |
                                     v
                         CTC forced alignment
                                     |
                                     v
                             token time spans
                                     |
                                     v
                              word time spans
                                     |
                                     v
                       chunk quality / confidence
                                     |
                                     v
                        overlap deduplication
                                     |
                                     v
                             global merge
                                     |
                                     v
                          canonical alignment
                                     |
                 +-------------------+------------------+
                 |                   |                  |
                 v                   v                  v
           SQLite words       final JSON          final LRC
                                     |
                                     v
                              final MP3 copy
                                     |
                                     v
                             Phase 2 SYLT add
                                     |
                                     v
                        final output validation
                                     |
                                     v
                           staged promotion
                                     |
                                     v
                         songs/final/Song.*
```

---

# 302. ABSOLUTE DO-NOT-DO LIST

The implementation must never:

```text
1. Modify songs/original/*.mp3
2. Modify songs/original/*.lrc
3. Modify songs/original/*.json
4. Delete all ID3 tags
5. Delete existing artwork
6. Delete existing custom metadata
7. Delete existing SYLT without ownership checks
8. Replace source lyrics with MMS transcription
9. Treat VAD silence as guaranteed instrumental truth
10. Use argmax ASR offsets as forced alignment
11. Delete unsupported words silently
12. Reconstruct the old JSON from selected fields
13. Require Phase 1's database
14. Require Phase 1's code
15. Depend on video_id for temporary directory identity
16. Hard-code a false last-lyric-near-duration rule
17. Declare quality based only on average confidence
18. Mark interpolated words as directly aligned
19. Generate final LRC from a separate timing calculation
20. Generate final SYLT by reparsing rounded LRC
21. Mark output finished before validating it
22. Overwrite good final files before the replacement package is validated
```

---

# 303. ABSOLUTE MUST-DO LIST

The implementation must:

```text
1. Match MP3/LRC/JSON by basename
2. Hash all input files
3. Preserve full original JSON
4. Preserve all existing MP3 metadata
5. Use external LRC as the lyric reference
6. Treat LRC timestamps as coarse anchors
7. Use Demucs for vocal isolation
8. Use VAD/activity as supporting evidence
9. Normalize Telugu reversibly
10. Use MMS Telugu adapter explicitly
11. Generate frame-level MMS emissions
12. Perform true CTC reference alignment
13. Convert token spans to word spans
14. Keep integer millisecond timestamps
15. Deduplicate overlapping chunks
16. Validate word and line ordering
17. Validate anchor drift
18. Track interpolation separately
19. Generate one final word-level LRC
20. Add a dedicated Phase 2 SYLT frame
21. Validate final MP3
22. Update final JSON with all Phase 2 details
23. Validate cross-file consistency
24. Support resume
25. Support idempotent reruns
26. Verify source files remained unchanged
27. Keep temp files for failures/reviews
28. Clean temp files after success
29. Record exact provenance
30. Process the full corpus only after staged acceptance tests
```

---

# 304. TECHNICAL BASIS USED FOR THIS PLAN

The architecture deliberately reflects current documented behavior of the major technologies used by the project.

## MMS

The current Hugging Face MMS documentation/model card describes language-specific adapter loading and the use of `target_lang` / `load_adapter` together with the MMS tokenizer/model. The model card also documents `facebook/mms-1b-all` and its supported multilingual architecture.

## CTC forced alignment

The current PyTorch documentation demonstrates forced alignment as aligning a supplied transcript against frame-level CTC emissions. Older TorchAudio forced-alignment APIs were deprecated and removed as TorchAudio moved into maintenance mode, so Phase 2 should isolate its own CTC alignment algorithm rather than depend on the deprecated API.

## Silero VAD

Silero VAD exposes speech timestamps based on configurable speech thresholds and related duration/padding parameters. Phase 2 uses those timestamps as activity/boundary evidence, not as definitive semantic instrumental detection.

## Demucs

Demucs documentation supports vocals-only separation mode and provides guidance for GPU memory pressure and segment sizing. Phase 2 uses a lossless working vocal stem for alignment rather than repeatedly encoding intermediate MP3 files.

## Mutagen / ID3 SYLT

The ID3 SYLT specification defines synchronized text entries with absolute timestamps and supports milliseconds as a timestamp unit. Mutagen exposes the SYLT frame and its fields. Phase 2 therefore embeds canonical millisecond word timing directly into SYLT instead of rebuilding it from rounded LRC.

---

# 305. ENGINEERING PRINCIPLE — SEPARATE DATA LAYERS

The project should maintain a clean separation:

```text
SOURCE DATA
    |
    +--> MP3
    +--> LRC
    `--> JSON

PROCESSING DATA
    |
    +--> decoded audio
    +--> vocals
    +--> chunks
    +--> emissions
    +--> alignment

CANONICAL RESULT
    |
    +--> SQLite words
    `--> JSON phase2.alignment

EXPORTS
    |
    +--> final LRC
    `--> final MP3 SYLT
```

This separation prevents export formats from becoming hidden processing dependencies.

---

# 306. ENGINEERING PRINCIPLE — PRESERVE, THEN ADD

For both JSON and MP3:

```text
preserve existing
        |
        v
add Phase 2 data
```

Never:

```text
extract a few fields
        |
        v
rebuild everything
```

---

# 307. ENGINEERING PRINCIPLE — REFERENCE FIRST

The source lyric reference is authoritative for wording.

MMS is authoritative only as acoustic evidence.

CTC alignment is the bridge.

This prevents transcription drift.

---

# 308. ENGINEERING PRINCIPLE — HONEST UNCERTAINTY

When direct timing is uncertain, preserve that uncertainty:

```text
score
source
quality status
interpolation flag
review reason
```

This is preferable to false precision.

---

# 309. ENGINEERING PRINCIPLE — DETERMINISM

Given identical:

```text
source files
models
versions
configuration
```

the pipeline should produce the same logical result as closely as the pinned runtime permits.

Avoid uncontrolled randomness.

If any model stage exposes randomness that is not needed, disable it.

---

# 310. ENGINEERING PRINCIPLE — VERSION EVERYTHING IMPORTANT

Version:

```text
pipeline
schema
normalizer
config
model
model revision
LRC export format
```

---

# 311. ENGINEERING PRINCIPLE — CHECKPOINT EXPENSIVE WORK

Demucs and MMS are expensive.

SQLite should ensure their work is not repeated after a crash unless necessary.

---

# 312. ENGINEERING PRINCIPLE — FINAL OUTPUTS ARE DERIVED

`Songs/final/` is a published-output area.

It is not a scratch area.

Nothing should be placed there until it is validated.

---

# 313. ENGINEERING PRINCIPLE — ONE CANONICAL TIMELINE

There must be exactly one canonical Phase 2 timing result per successful run.

LRC and SYLT are projections of that timeline.

JSON contains that timeline.

SQLite indexes that timeline.

---

# 314. FINAL SAMPLE PACKAGE TARGET

For the supplied sample, the final directory should eventually be:

```text
songs/final/
├── 001_Gelupu Thalupule_Mani Sharma, Sreerama Chandra.mp3
├── 001_Gelupu Thalupule_Mani Sharma, Sreerama Chandra.lrc
└── 001_Gelupu Thalupule_Mani Sharma, Sreerama Chandra.json
```

The original directory remains exactly as supplied.

---

# 315. FINAL SAMPLE JSON TARGET

The sample final JSON must still contain its previous top-level data and gain:

```text
phase2
```

with at minimum:

```text
input hashes
model information
processing status
alignment method
line/word alignment
quality metrics
instrumental/gap candidates
output paths
final output hashes
provenance
```

---

# 316. FINAL SAMPLE MP3 TARGET

The sample final MP3 must retain its original metadata and contain a new Phase 2 word-level SYLT.

The existing lyric frames must remain intact unless an explicit ownership rule says otherwise.

---

# 317. FINAL SAMPLE LRC TARGET

The sample final LRC must retain the same lyric wording and lyric order as the source LRC, but the timing representation is upgraded to word-level timing.

The long blank regions remain represented according to the configured blank-marker policy.

---

# 318. FINAL PROJECT SUCCESS DEFINITION

Phase 2 is successful when the following is true:

```text
For every processable source package:

Song.mp3 + Song.lrc + Song.json
              |
              v
        Phase 2 processing
              |
              v
Song.mp3 + Song.lrc + Song.json
```

with:

```text
source files unchanged
existing JSON preserved
existing MP3 metadata preserved
word-level timing generated
MMS reference alignment performed
outputs validated
database state recorded
resume supported
provenance recorded
```

---

# 319. FINAL IMPLEMENTATION MESSAGE

When this plan is implemented, the project should be thought of as a **word-level timing engine**, not a lyric downloader and not a transcription system.

Its job is:

```text
TAKE KNOWN LYRICS
        +
TAKE KNOWN AUDIO
        |
        v
FIND WHERE EACH KNOWN WORD OCCURS
IN THE AUDIO
```

The source LRC supplies the known words and coarse line timing.

Demucs supplies a cleaner vocal signal.

VAD and energy provide activity/boundary evidence.

MMS supplies frame-level acoustic evidence.

CTC forced alignment connects the supplied lyric reference to that evidence.

The canonical result becomes word start/end timing.

That timing is exported to one final LRC, embedded into a new Phase 2 SYLT frame in a copy of the original MP3, and written into the existing JSON under `phase2` without destroying anything already there.

---

# 320. FINAL DIRECTORY GUARANTEE

```text
songs/original/
    [INPUT — NEVER MODIFIED]

songs/final/
    [PUBLISHED OUTPUT]

    SongName.mp3
    SongName.lrc
    SongName.json
```

That is the final user-facing architecture.

---

# 321. FINAL ONE-LINE ARCHITECTURE

```text
MP3 + external LRC + cumulative JSON -> Demucs -> activity analysis -> reversible Telugu normalization -> LRC-anchored chunks -> MMS Telugu emissions -> true CTC forced alignment -> word spans -> validation -> one word-level LRC + metadata-preserving MP3/SYLT + cumulative JSON -> songs/final/
```

---

# 322. DOCUMENT END STATE

This document is the implementation contract for the standalone Phase 2 project.

No Phase 1 database integration is required.

No Phase 1 source code integration is required.

The file-level handoff is:

```text
MP3 + LRC + JSON
```

and the file-level output is:

```text
MP3 + LRC + JSON
```

with the JSON cumulative and the MP3 metadata-preserving.



---

# APPENDIX A — FINAL IMPLEMENTATION CHECKLIST

## A.1 Input contract

- [ ] `songs/original/` exists.
- [ ] Every MP3 has the expected same-basename LRC.
- [ ] JSON exists for cumulative metadata.
- [ ] Original files are never modified.
- [ ] MP3/LRC/JSON hashes are recorded.

## A.2 Windows audio layer

- [ ] FFmpeg path is discovered.
- [ ] FFprobe path is discovered.
- [ ] Temporary WAV filename ends in `.wav`, or `-f wav` is explicit.
- [ ] Output replacement is atomic.
- [ ] A decoded 16 kHz mono WAV can be reopened.

## A.3 CUDA layer

- [ ] `nvidia-smi` sees the GPU.
- [ ] `torch.cuda.is_available()` is true.
- [ ] A CUDA tensor can be created.
- [ ] The selected GPU name is printed.
- [ ] Demucs reports CUDA when run.
- [ ] MMS reports CUDA when run.
- [ ] CPU fallback policy is explicit rather than silent.

## A.4 Demucs

- [ ] Vocal model loads.
- [ ] Output duration matches source within configured tolerance.
- [ ] Lossless working vocal file is produced.
- [ ] GPU segment size is conservative for an approximately 8 GB card.
- [ ] OOM retry logic is present.

## A.5 LRC

- [ ] Timestamp formats are parsed correctly.
- [ ] Blank markers are retained as structural events.
- [ ] Line order is preserved.
- [ ] Original lyric wording is preserved.
- [ ] Source anchor timestamps remain available.

## A.6 Telugu normalization

- [ ] NFC is applied.
- [ ] Whitespace is normalized.
- [ ] Number expansion happens before destructive numeric filtering.
- [ ] Abbreviation handling occurs before punctuation loss where necessary.
- [ ] Original and normalized forms are both stored.
- [ ] Word mapping is reversible.

## A.7 Chunking

- [ ] LRC anchors are used.
- [ ] Blank markers have high boundary priority.
- [ ] VAD and energy are supporting signals.
- [ ] Chunks have logical and model-audio intervals.
- [ ] Overlap context is present.
- [ ] Chunk boundaries do not silently cross explicit lyric-free regions.

## A.8 MMS

- [ ] Model cache is validated.
- [ ] Telugu adapter is explicitly loaded.
- [ ] Model loads once per worker.
- [ ] Frame emissions are generated.
- [ ] No final reliance on ASR argmax word offsets exists.

## A.9 CTC

- [ ] Reference tokens are aligned against emissions.
- [ ] Blank transitions are handled.
- [ ] Repeated-label transitions are handled correctly.
- [ ] Token spans are converted to milliseconds.
- [ ] Path-derived scores are recorded.

## A.10 Merge

- [ ] Candidate objects are not mutated in place across passes.
- [ ] Overlap duplicates are resolved by provenance.
- [ ] Same-chunk chronology is checked.
- [ ] Cross-chunk chronology is checked.
- [ ] Global chronology is checked.
- [ ] No blind timestamp sorting is used.

## A.11 Validation

- [ ] `0 <= start < end <= duration` for every valid word.
- [ ] Word order matches reference order.
- [ ] Line order matches reference order.
- [ ] LRC anchor drift is measured.
- [ ] Low-score tail is measured.
- [ ] Blank-marker start constraints are checked.
- [ ] Output LRC and JSON are derived from the same canonical data.

## A.12 JSON

- [ ] Original JSON is deep-copied.
- [ ] Unknown fields survive.
- [ ] Only `phase2` is mutated.
- [ ] Input JSON hash is recorded.
- [ ] Final JSON content hash excludes the self-referential hash field if such a field is used.
- [ ] Final byte hash is stored in SQLite.

## A.13 MP3

- [ ] Original file is copied, not transcoded.
- [ ] Existing metadata is preserved.
- [ ] Existing SYLT is preserved.
- [ ] Existing USLT is preserved.
- [ ] Existing artwork is preserved.
- [ ] Only the Phase 2 SYLT is added/replaced.
- [ ] Final MP3 is reopened and revalidated.

## A.14 Recovery

- [ ] Failed chunks can be retried.
- [ ] Finished songs do not re-run unnecessarily.
- [ ] Stale states can be recovered.
- [ ] Temporary artifacts are retained for failures/review.
- [ ] Final package promotion is atomic/staged.

---

# APPENDIX B — WINDOWS CUDA SETUP REFERENCE

The following is the intended sequence for the observed Windows/NVIDIA environment. Exact wheel availability should always be verified against the pinned project environment before installation.

## B.1 Inspect current PyTorch

```powershell
python -c "import torch; print('PyTorch:', torch.__version__); print('CUDA available:', torch.cuda.is_available()); print('CUDA runtime:', torch.version.cuda); print('GPU:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'NONE')"
```

## B.2 Confirm driver visibility

```powershell
nvidia-smi
```

## B.3 Install the CUDA-enabled PyTorch build

For the project environment used during the field investigation, the intended CUDA wheel family was CUDA 12.8. A typical pinned installation is:

```powershell
python -m pip uninstall -y torch torchaudio
python -m pip install torch==2.9.1 torchaudio==2.9.1 --index-url https://download.pytorch.org/whl/cu128
```

Do not install a second, conflicting PyTorch package from the normal PyPI index afterward.

## B.4 Verify a real CUDA tensor

```powershell
python -c "import torch; print(torch.cuda.get_device_name(0)); x=torch.randn(2000,2000,device='cuda'); print(x.device)"
```

Expected conceptual result:

```text
NVIDIA GeForce RTX 5050
cuda:0
```

## B.5 Verify Phase 2

```powershell
python main.py --doctor
```

The doctor output should report CUDA availability and the selected GPU.

## B.6 Monitor while processing

In another PowerShell window:

```powershell
nvidia-smi -l 1
```

The goal is to observe GPU memory use and utilization during Demucs and MMS inference.

## B.7 Demucs-specific GPU principle

Demucs selects `cuda` by default when `torch.cuda.is_available()` is true. Its published documentation notes that GPU acceleration requires a CUDA-enabled PyTorch installation and that reducing the segment size can lower GPU memory requirements. See the official Demucs Windows and README documentation:

- https://github.com/facebookresearch/demucs/blob/main/docs/windows.md
- https://github.com/facebookresearch/demucs/blob/main/README.md

The project uses a conservative segment setting suitable for an approximately 8 GB GPU and records the actual device used.

---

# APPENDIX C — HUGGING FACE MODEL CACHE POLICY

The MMS model is large. The cache is designed to be reused across songs.

Required principles:

```text
first model load
    -> download missing files
    -> cache

later song
    -> resolve same cached revision
    -> reuse files
```

Do not delete the cache between songs.

Do not call a forced network download for each song.

The project should prefer a project-local Hugging Face cache under `models/mms/` when practical so model provenance stays associated with the project.

On Windows, lack of symlink support causes Hugging Face to fall back to a degraded cache layout in which files can be duplicated across snapshots/revisions. Developer Mode or elevated permissions can improve this behavior. See:

- https://huggingface.co/docs/huggingface_hub/main/package_reference/environment_variables
- https://huggingface.co/docs/hub/local-cache

The warning itself does not indicate a GPU problem.

---

# APPENDIX D — MMS LANGUAGE ADAPTER REQUIREMENT

For MMS, changing the tokenizer language is only part of language selection. The corresponding model adapter must be loaded.

Conceptually:

```python
processor.tokenizer.set_target_lang("tel")
model.load_adapter("tel")
```

The exact API must match the pinned Transformers version.

The project must record:

```text
model name
model revision
language adapter
Transformers version
```

Reference:

https://huggingface.co/facebook/mms-1b-all

---

# APPENDIX E — CTC ALIGNMENT IMPLEMENTATION REQUIREMENTS

The internal CTC aligner must be independent of the model loader so it can be unit tested with synthetic emissions.

Required capabilities:

1. Reference token sequence construction.
2. Expanded CTC state sequence.
3. Blank states.
4. Stay transitions.
5. Advance transitions.
6. Correct handling of repeated adjacent labels.
7. Dynamic-programming/Viterbi score tracking.
8. Backtracking.
9. Token span extraction.
10. Frame-to-time conversion.
11. Path-derived score calculation.
12. Explicit failure if the target sequence cannot fit in the available frames.

The implementation must be deterministic given identical emissions, tokens, model revision, configuration, and input audio.

The implementation must not call a deprecated high-level forced-alignment helper as its only alignment mechanism.

---

# APPENDIX F — FINAL CHRONOLOGY ALGORITHM

The chronology correction policy is intentionally conservative.

## F.1 Intra-chunk

For words in reference order:

```text
word[n].start_ms >= word[n-1].start_ms
```

and normally:

```text
word[n].end_ms >= word[n-1].end_ms
```

A violation is first classified:

```text
same-token duplicate?
overlap candidate?
true reference-order failure?
```

If it is a duplicate candidate, select the correct provenance winner.

If it is a true reference-order violation, retry/re-align the chunk.

## F.2 Cross-chunk

At every adjacent chunk boundary:

```text
last accepted word of chunk N
        <=
first accepted word of chunk N+1
```

If overlapping context generated duplicate observations of the same reference word, keep the best candidate using:

1. logical-region inclusion;
2. stronger score;
3. closer source-LRC anchor relationship;
4. narrower unnecessary context dependence.

## F.3 Global

After all chunk candidates are resolved, validate the entire song sequence.

No exporter is invoked until the canonical sequence passes global chronology.

## F.4 Never sort independent timestamps

This is forbidden:

```python
timestamps.sort()
```

when timestamps are separated from their lexical items.

Any corrective operation must modify the candidate word object that owns the timestamp.

---

# APPENDIX G — FINAL BLANK-MARKER ALGORITHM

Suppose the LRC contains:

```text
line N starts at 230000 ms
blank marker at 272300 ms
next line starts at 302000 ms
```

The blank interval should become a high-priority no-new-lyric-start region.

A candidate word:

```text
start = 271900
end   = 272760
```

is not automatically discarded because the onset is before the blank marker. Instead:

- retain the canonical span if acoustic evidence supports it;
- mark a boundary-crossing diagnostic;
- prevent later lyric words from starting inside the blank region;
- ensure the next lyric line begins in the correct subsequent region.

A candidate word:

```text
start = 272500
```

is invalid under the blank-region constraint because its onset occurs in the structural blank interval.

The affected chunk should be re-aligned with the blank marker as a hard logical boundary.

---

# APPENDIX H — FINAL ERROR TAXONOMY

The project should distinguish:

```text
SOURCE / INVENTORY
MP3_MISSING
LRC_MISSING
JSON_MISSING
JSON_INVALID
MP3_INVALID
LRC_INVALID

AUDIO
AUDIO_DECODE_FAILED
AUDIO_DURATION_MISMATCH

DEMUCS
DEMUCS_LOAD_FAILED
DEMUCS_INFERENCE_FAILED
DEMUCS_OOM
VOCALS_INVALID

ACTIVITY
VAD_FAILED
ACTIVITY_ANALYSIS_FAILED

TEXT
NORMALIZATION_FAILED
TOKENIZATION_FAILED
UNSUPPORTED_REFERENCE

MMS
MMS_LOAD_FAILED
MMS_INFERENCE_FAILED
MMS_OOM
CUDA_UNAVAILABLE

ALIGNMENT
CTC_ALIGNMENT_FAILED
CHUNK_LOW_CONFIDENCE
CHUNK_CHRONOLOGY_FAILED
CHUNK_ANCHOR_FAILED

MERGE
OVERLAP_CONFLICT
GLOBAL_CHRONOLOGY_FAILED
BLANK_MARKER_START_VIOLATION
ANCHOR_DRIFT

OUTPUT
LRC_GENERATION_FAILED
SYLT_EMBED_FAILED
MP3_VALIDATION_FAILED
JSON_WRITE_FAILED
FINAL_PROMOTION_FAILED
SOURCE_MODIFIED
```

The error code should identify the real failed subsystem. A final serializer should not mask an earlier alignment failure with a generic file-format message.

---

# APPENDIX I — FIELD RUN INTERPRETATION GUIDE

When a run reports:

```text
finished = X
skipped = Y
failed = Z
```

interpret them as:

- `finished`: final output package exists and passed the configured output gates.
- `skipped`: existing valid work was reused or a skip condition was intentionally applied.
- `failed`: the song could not reach the final output state.

A high skip count during a recovery run is not automatically bad. It usually indicates idempotent reuse.

A high failure count with the exact same error code across many songs usually indicates a shared pipeline defect and should trigger investigation of the common module rather than song-specific tuning.

---

# APPENDIX J — ACCEPTANCE CRITERIA FOR DECLARING PHASE 2 READY FOR THE FULL CORPUS

The project is not considered production-ready until all of the following have been demonstrated on representative songs:

1. MP3/LRC/JSON basename matching.
2. Full JSON preservation.
3. MP3 metadata preservation.
4. Windows FFmpeg decode success.
5. CUDA-enabled PyTorch verification.
6. Demucs CUDA execution.
7. MMS CUDA execution.
8. Telugu adapter loading.
9. Real CTC forced alignment.
10. Same-chunk chronology repair.
11. Cross-chunk chronology repair.
12. Blank-marker handling.
13. LRC export.
14. SYLT export.
15. Final MP3 reopening.
16. Final JSON reopening.
17. Source-file hash stability.
18. Resume after interruption.
19. Retry after chunk failure.
20. No repeat model download across songs.
21. No accidental CPU-only large-batch execution.
22. Final `songs/final/` package correctness.

---

# APPENDIX K — REFERENCE SOURCES USED IN THIS CONSOLIDATED PLAN

## MMS

Meta MMS model card and usage:

https://huggingface.co/facebook/mms-1b-all

## Hugging Face cache

https://huggingface.co/docs/huggingface_hub/main/package_reference/environment_variables

https://huggingface.co/docs/hub/local-cache

## Demucs Windows/GPU behavior

https://github.com/facebookresearch/demucs/blob/main/docs/windows.md

https://github.com/facebookresearch/demucs/blob/main/README.md

## TorchAudio / alignment lifecycle

The implementation intentionally isolates CTC alignment so it is not coupled to deprecated high-level forced-alignment convenience APIs. Pin the actual runtime environment and keep the aligner independent.

---

# APPENDIX L — FINAL OPERATIONAL COMMAND SEQUENCE

## L.1 First setup

```powershell
python --version
python main.py --doctor
nvidia-smi
```

## L.2 Verify CUDA directly

```powershell
python -c "import torch; print('CUDA available:', torch.cuda.is_available()); print('CUDA runtime:', torch.version.cuda); print('GPU:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'NONE')"
```

## L.3 Scan only

```powershell
python main.py --scan-only
```

## L.4 Dry run

```powershell
python main.py --dry-run
```

## L.5 One-song production test

```powershell
python main.py --song "001_Gelupu Thalupule_Mani Sharma, Sreerama Chandra" --debug
```

At the same time:

```powershell
nvidia-smi -l 1
```

## L.6 Failed-song recovery

```powershell
python main.py --failed --debug
```

## L.7 Full batch

```powershell
python main.py --all
```

## L.8 Keep debug artifacts

```powershell
python main.py --all --keep-temp --debug
```

## L.9 Recovery after interruption

```powershell
python main.py --recover
python main.py --all
```

## L.10 Database backup

```powershell
python main.py --backup-db db/phase2_backup.sqlite
```

---

# APPENDIX M — FINAL DO-NOT-DO LIST

Never:

```text
- modify files inside songs/original/
- delete original MP3 metadata to simplify embedding
- replace the original JSON with a newly constructed minimal JSON
- assume embedded lyrics exist
- download the MMS model per song
- silently fall back to CPU for a long production batch
- use plain ASR argmax offsets as forced alignment
- sort timestamps away from their word objects
- let an LRC serializer become the canonical timing engine
- treat every VAD non-speech region as confirmed instrumental
- reject a song solely because lyrics end before the audio ends
- require the last lyric timestamp to be within ±10% of total duration
- let chunk overlap duplicate final words
- let a mutable candidate object be modified repeatedly across retry passes
- write final files directly before validation
- write the original JSON or LRC in place
- reintroduce reel/hook-selection functionality into this project
```

---

# APPENDIX N — FINAL ONE-PARAGRAPH SYSTEM DEFINITION

Phase 2 is a standalone, non-destructive Telugu word-level lyric synchronisation system that reads basename-matched MP3/LRC/JSON packages from `songs/original`, preserves all existing song metadata and cumulative JSON information, decodes and vocal-isolates the audio, uses LRC timestamps and blank markers as coarse structural anchors, normalizes Telugu reversibly, creates overlapping reference-aware chunks, generates Telugu MMS frame-level emissions using the NVIDIA GPU where available, performs genuine CTC reference forced alignment against the supplied LRC text, converts token spans into original-word start/end timings, resolves intra-chunk and inter-chunk chronology without blind timestamp sorting, validates anchor and blank-marker consistency, emits one final word-level LRC, injects only a dedicated Phase 2 word-level SYLT into a copy of the original MP3, updates the original JSON through an additive `phase2` namespace, validates all three outputs, and atomically promotes them as `songs/final/SongName.mp3`, `songs/final/SongName.lrc`, and `songs/final/SongName.json`. The system is resumable, idempotent, CUDA-aware, model-cache-aware, Windows-safe for FFmpeg temporary WAV generation, and explicitly excludes reel creation and hook selection.



--- ARCHIVE C: ORIGINAL PHASE 3 SPECIFICATION ---

# ARCHIVE C: ORIGINAL PHASE 3 SPECIFICATION

The following document is retained verbatim as supplied.

# PHASE 3 — ULTRA-DETAILED STANDALONE PROJECT PLAN

## Project Baseline

**Project:** Phase 3 — Manual-Hook Telugu Music Reel Generator  
**Current implementation baseline discussed:** V1.0.16  
**Document purpose:** Authoritative design/architecture/implementation specification capturing the complete Phase 3 scope, decisions, interfaces, pipeline, data contracts, failure handling, performance requirements, validation strategy, and the history of superseded approaches.

---

# 1. EXECUTIVE DEFINITION

Phase 3 is a **completely standalone Reel-generation project**.

It does **not** depend on Phase 1 or Phase 2 code, databases, runtime state, project directories, processes, APIs, cloud storage, or internal implementation details.

The only upstream contract is a manually populated file package placed into:

```text
phase3_project/
└── songs/
    └── final/
        ├── SongName.mp3
        ├── SongName.lrc
        └── SongName.json
```

For Phase 3, the file basename is the identity key:

```text
SongName.mp3
SongName.lrc
SongName.json
```

must correspond to the same basename.

Phase 3 treats `songs/final/` as **read-only input**. It never rewrites, moves, normalizes, renames, mutates, or otherwise modifies those source files.

Phase 3 creates all working data and generated outputs under its own project directories.

The intended workflow is:

```text
1. User places Phase 2 final package into songs/final/
2. User runs python main.py
3. Phase 3 automatically synchronizes hook_timeline.json with songs/final/
4. User enters/edits hook start/end values in hook_timeline.json
5. User runs python main.py again
6. Phase 3 uses those exact manual timelines
7. Phase 3 analyzes/render-processes everything automatically
8. Phase 3 generates the final Reel and provenance JSON
9. User manually uploads the Reel to Instagram or another platform
```

There is **no Instagram API integration** and no automatic publishing.

---

# 2. FINAL CURRENT REQUIREMENTS — AUTHORITATIVE

The following requirements supersede earlier experiments.

## 2.1 Hook selection

There is **no automatic hook selection** in the final design.

The user directly controls the hook timeline in:

```text
hook_timeline.json
```

The timeline is authoritative.

The final system must never:

- rank hooks,
- select a hook automatically,
- generate hook finalists,
- substitute another hook because of video matching,
- shorten or extend the user's selected timeline merely because a default duration is preferred.

The exact interval supplied by the user becomes the final Reel musical interval.

## 2.2 Reel duration

There is **no maximum Reel duration imposed by Phase 3**.

The duration is exactly:

```text
end - start
```

from `hook_timeline.json`.

Examples that are valid in principle:

```text
00:00.000 → 00:10.000   = 10 seconds
02:12.920 → 03:11.720   = 58.8 seconds
01:00.000 → 02:15.000   = 75 seconds
```

Any duration is acceptable provided:

- the start is non-negative,
- the end is greater than the start,
- the interval lies within the local source song duration,
- the YouTube visual segment can be mapped to the same interval.

## 2.3 YouTube synchronization

The YouTube synchronization model is intentionally simple.

The system does **not** search for each hook independently.

Instead:

1. Take the first 15 seconds of the local original song.
2. Scan the YouTube song audio from the beginning.
3. Find the point where those first 15 seconds best align using a combination of:
   - normalized energy shape,
   - high/low transitions / peak-valley behavior,
   - downsampled waveform correlation.
4. Store one global offset.
5. Apply that same offset to the manually entered hook timeline.

For example:

```text
Original first 15s      = YouTube 00:05.000
Global offset           = +5.000s
```

If the user's hook is:

```text
02:12.920 → 03:11.720
```

the corresponding YouTube interval becomes:

```text
02:17.920 → 03:16.720
```

No per-hook re-matching is performed.

No DTW, multi-anchor verification, complex residual thresholding, or hook-by-hook search is required.

## 2.4 Video framing

Smart Crop has been removed.

There is no face tracking, body tracking, pose tracking, identity tracking, subject switching, or dynamic crop selection in the final design.

The final visual treatment is a **static centered panel**:

- canvas: `1080 x 1920`
- aspect ratio: `9:16`
- source video is scaled so its displayed height is approximately **80% of the 9:16 canvas height**
- 80% of 1920 = 1536 pixels
- the video is centered vertically
- the video remains horizontally centered
- source is cropped as necessary rather than stretched
- no black bars as a design goal
- no frame-by-frame subject tracking

The implementation baseline uses NVIDIA NVENC first for video encoding, with a CPU `libx264` fallback when NVENC cannot be executed.

## 2.5 Audio

The final Reel audio is always sourced from the **local original song**.

YouTube audio is only used as a synchronization reference.

The final hook audio is produced from the exact manual hook interval of the local Demucs stems.

Demucs is required to be executed by Phase 3 when needed; input stems are not assumed to exist.

The intended stem set is:

```text
vocals
 drums
 bass
 other
```

The exact selected hook interval is cropped from each relevant stem.

The stems are then processed into a stereo 8D-style spatial mix:

- vocals: centered / stable intelligibility
- bass: mono-compatible and centered
- drums: center-weighted with subtle spatial movement
- other: primary left/right movement and spatial motion

The 8D process must preserve musical timing.

There is no pitch shifting and no speed change.

The resulting audio becomes the Reel audio.

## 2.6 Lyrics

Lyrics come from the **LRC file**.

Phase 3 does not perform lyric discovery or ASR.

The lyric display is **line by line**, not persistent multi-line karaoke and not the earlier proposed context-block system.

Behavior:

```text
LRC line starts
    ↓
line appears
    ↓
words inside that line are individually highlighted according to word timestamps
    ↓
next LRC line begins
    ↓
previous line disappears
```

The active lyric line is the line whose timestamp range is currently active.

Every word in the line should have timing information when available so the renderer can highlight the current word.

The currently active word receives the strongest emphasis.

Completed/future words can be visually subtler, but the complete current LRC line remains visible until the next line replaces it.

The lyric block is positioned centrally in the Reel rather than at the traditional bottom third.

The baseline typography target discussed is:

```text
Font family: Baloo Tammudu 2 ExtraBold
Font filename: BalooTammudu2-ExtraBold.ttf
```

Fallback fonts may be used only when the requested font is not available.

---

# 3. STANDALONE PROJECT BOUNDARY

## 3.1 Strict isolation from upstream phases

Phase 3 must never import upstream Phase 1/2 Python modules.

Phase 3 must not connect to an upstream database.

Phase 3 must not rely on upstream runtime state.

Phase 3 must not assume upstream working directories.

Phase 3 must not launch or control Phase 1/2 processes.

Phase 3 must not use upstream-generated temporary files as a hidden dependency.

The only valid upstream dependency is the manual file package in:

```text
songs/final/
```

## 3.2 Source immutability

All source files in `songs/final/` are read-only.

Phase 3 may:

- read them,
- hash them,
- probe them,
- copy them into internal caches if desired.

Phase 3 may not modify their contents.

If provenance is needed, hashes should be calculated without changing the source files.

---

# 4. PROJECT DIRECTORY LAYOUT

Recommended structure:

```text
phase3_project/
│
├── main.py
├── config.json
├── hook_timeline.json
├── BUILD_INFO.md
├── CHANGELOG.md
├── README.md
│
├── songs/
│   └── final/
│       ├── SongA.mp3
│       ├── SongA.lrc
│       └── SongA.json
│
├── reels/
│   └── generated/
│       ├── SongA_reel.mp4
│       └── SongA_reel.json
│
├── temp/
│   └── <song-basename>/
│       ├── input/
│       ├── stems/
│       ├── analysis/
│       ├── matching/
│       ├── video/
│       ├── audio/
│       │   ├── cropped_stems/
│       │   └── audio_8d_manifest.json
│       ├── lyrics/
│       ├── render/
│       ├── stage_final/
│       ├── validation/
│       └── provenance/
│
├── assets/
│   ├── fonts/
│   └── models/
│
├── src/
│   ├── config.py
│   ├── utils.py
│   ├── audio_io.py
│   ├── stem_isolator.py
│   ├── audio_analysis.py
│   ├── lyric_parser.py
│   ├── hook_timeline.py
│   ├── youtube_audio.py
│   ├── audio_match.py
│   ├── video_grabber.py
│   ├── video_renderer.py
│   ├── audio_8d.py
│   ├── lyrics_renderer.py
│   ├── assembler.py
│   ├── validator.py
│   ├── provenance.py
│   ├── database.py
│   └── pipeline.py
│
└── tests/
    ├── test_contract.py
    ├── test_hook_timeline.py
    ├── test_lyrics.py
    ├── test_audio_offset.py
    ├── test_video_trim.py
    ├── test_audio_8d.py
    ├── test_validation.py
    └── test_pipeline_smoke.py
```

Directory names may evolve in implementation, but the source/output boundary must remain.

---

# 5. PRIMARY USER-FACING FILE: `hook_timeline.json`

## 5.1 Purpose

This is the authoritative user-editable hook configuration.

The user can manually select any timeline without opening Python code.

## 5.2 Example

```json
{
  "schema_version": 1,
  "songs": [
    {
      "song_path": "songs/final/001_Gelupu Thalupule_Mani Sharma, Sreerama Chandra.mp3",
      "lrc_path": "songs/final/001_Gelupu Thalupule_Mani Sharma, Sreerama Chandra.lrc",
      "hook": {
        "start": "02:12.92",
        "end": "03:11.72"
      }
    }
  ]
}
```

This exact `MM:SS.xx` syntax is valid.

Examples of accepted time forms should include:

```text
02:12.92
02:12.920
00:05
01:02:03.500
132.920
```

The canonical internal representation should be integer milliseconds.

For:

```text
02:12.92
```

canonical value is:

```text
132920 ms
```

For:

```text
03:11.72
```

canonical value is:

```text
191720 ms
```

Duration:

```text
58800 ms
= 58.8 s
```

## 5.3 Automatic JSON synchronization at startup

`python main.py` must automatically synchronize `hook_timeline.json` before processing.

The sync operation:

1. scans `songs/final/` for MP3 files,
2. derives the basename,
3. looks for the matching LRC,
4. looks for the matching JSON,
5. preserves existing hook data,
6. adds missing songs,
7. adds missing LRC paths,
8. does not invent a hook timeline.

The explicit command remains available:

```powershell
python main.py --fillhookjson
```

but normal `python main.py` should automatically perform the same synchronization first.

## 5.4 Preservation rules

If an existing entry already has:

```json
"hook": {
  "start": "02:12.92",
  "end": "03:11.72"
}
```

automatic synchronization must not erase or change those values.

New songs should receive:

```json
"hook": {
  "start": "",
  "end": ""
}
```

The system must not silently choose a hook.

## 5.5 Validation rules

Every configured song must satisfy:

- MP3 exists
- LRC exists, unless explicit project policy allows warning-only behavior
- optional Phase 2 JSON exists
- start is parseable
- end is parseable
- start >= 0
- end > start
- end <= source-song duration

The process should clearly report all unconfigured songs rather than silently skip them.

---

# 6. INPUT CONTRACT

Each input package is identified by basename matching.

Example:

```text
songs/final/001_Gelupu Thalupule_Mani Sharma, Sreerama Chandra.mp3
songs/final/001_Gelupu Thalupule_Mani Sharma, Sreerama Chandra.lrc
songs/final/001_Gelupu Thalupule_Mani Sharma, Sreerama Chandra.json
```

The historical metadata paths inside the Phase 2 JSON are not trusted as current filesystem paths.

Actual current filesystem paths must be derived from:

```text
songs/final/<basename>.*
```

This is important because the sample Phase 2 JSON historically contained paths referring to older directory layouts.

---

# 7. INPUT JSON USAGE

Phase 2 JSON is treated as structured metadata and word-level lyric/timing information.

It is **not modified**.

Useful fields may include:

- song metadata
- artist metadata
- duration
- source provenance
- YouTube Music ID
- selected visual YouTube video ID
- Phase 2 word alignment
- word-level timings

Phase 3 should prefer the actual local file package for current paths and use JSON metadata for semantic information.

Important distinction:

```text
YouTube Music ID
```

and

```text
selected visual YouTube video ID
```

may be different.

The visual matching pipeline should use the selected visual video ID when one exists.

---

# 8. INITIALIZATION / DOCTOR STAGE

## 8.1 `python main.py --doctor`

Doctor should report:

- Python version
- FFmpeg availability
- FFprobe availability
- CUDA availability
- Demucs availability
- NVIDIA NVENC support in FFmpeg
- fonts
- requested Baloo Tammudu 2 font availability or downloader status
- required directories
- `songs/final/`
- `hook_timeline.json`
- LRC pairing status
- source MP3 pairing status
- write permissions for temp/output paths

## 8.2 No false mandatory dependencies

MediaPipe is not a required dependency of the current final design because Smart Crop was removed.

Face/pose tracking should therefore not be part of the required doctor gate.

---

# 9. PIPELINE STAGES

The final pipeline is analysis → decision/input validation → synchronization → render → validation → finalization.

A typical state model:

```text
INPUT_VERIFIED
    ↓
STEMS_READY
    ↓
AUDIO_ANALYZED
    ↓
LYRICS_READY
    ↓
HOOK_PLAN_READY
    ↓
YOUTUBE_AUDIO_READY
    ↓
VIDEO_MATCH_READY
    ↓
VIDEO_SEGMENT_READY
    ↓
AUDIO_8D_READY
    ↓
LYRICS_RENDER_READY
    ↓
VIDEO_RENDERED
    ↓
ASSEMBLED
    ↓
VALIDATED
    ↓
FINALIZED
```

Failure/review states should be explicit.

The system should be resumable where possible.

---

# 10. STAGE 1 — VERIFY INPUT PACKAGE

For each song entry:

1. confirm MP3 exists,
2. confirm LRC exists,
3. locate optional JSON,
4. probe MP3 duration/sample rate/channels/codec,
5. compute source hash,
6. validate hook timeline only if configured,
7. create per-song work directory.

### Important Windows behavior

FFprobe/FFmpeg output may contain Unicode/Telugu metadata.

Subprocess handling must explicitly use UTF-8 with replacement/error tolerance rather than relying on the Windows CP1252 default.

Conceptually:

```python
subprocess.run(
    ...,
    text=True,
    encoding="utf-8",
    errors="replace"
)
```

The probe code must handle:

- empty FFprobe output,
- invalid JSON,
- multiple streams,
- MP3 artwork streams.

The audio stream must be selected correctly even when FFprobe also reports a JPEG/MJPEG artwork stream.

---

# 11. STAGE 2 — DEMUCS STEM ISOLATION

Phase 3 runs Demucs itself.

Expected stems:

```text
vocals
 drums
 bass
 other
```

The stage should:

1. run Demucs on the local MP3,
2. use CUDA when available,
3. verify all expected stem files,
4. record Demucs model/version if possible,
5. save stem paths in provenance.

The final audio output must be derived from the local source song/stems, never from YouTube audio.

The stem stage is computationally expensive; cache results and resume if valid.

---

# 12. STAGE 3 — FULL-SONG AUDIO ANALYSIS

Although hook selection is now manual, the system still performs broad audio analysis for diagnostics, provenance, and downstream quality control.

Analyze:

- full mix
- vocals
- drums
- bass
- other

Potential measurements:

- RMS energy
- peak amplitude
- short-term energy envelope
- spectral centroid
- spectral rolloff
- spectral flux
- onset density
- chroma
- log-mel features
- energy contour
- silence/instrumental regions
- stem activity
- vocal activity
- arrangement density

This information is not allowed to override the manual hook timeline.

---

# 13. STAGE 4 — LRC PARSING

The LRC is canonical for lyric display.

The parser must handle ordinary line timestamps and the supplied word-timestamp structure.

Example style:

```text
[00:25.18]గెలుపు [00:25.98]తలుపులే [00:27.34]తీసే [00:29.52]ఆకాశమే
```

The parser should produce a structure conceptually like:

```json
{
  "start_ms": 25180,
  "end_ms": 29520,
  "text": "గెలుపు తలుపులే తీసే ఆకాశమే",
  "words": [
    {"text": "గెలుపు", "start_ms": 25180, "end_ms": 25980},
    {"text": "తలుపులే", "start_ms": 25980, "end_ms": 27340},
    {"text": "తీసే", "start_ms": 27340, "end_ms": 29520}
  ]
}
```

Exact handling can use the next word timestamp or next line timestamp as the end boundary.

If word timings are unavailable, the renderer should gracefully fall back to line-level display rather than requiring ASR.

---

# 14. LYRIC DISPLAY RULES

## 14.1 Line selection

At render time `t`, select the line active at `t`.

Only the current line is displayed.

When the next line begins, the previous line disappears.

## 14.2 Word highlighting

Within the active line:

- current word: strongest highlight
- earlier words: completed state / subtle
- later words: future state / subtle

The highlight progresses continuously according to the LRC word timing.

## 14.3 Position

Lyrics should be visually centered in the Reel, around the middle region rather than near the bottom edge.

Suggested baseline:

```text
x = 540
center-y around 960
acceptable vertical range roughly 800–1120
```

## 14.4 Typography

Primary:

```text
Baloo Tammudu 2 ExtraBold
```

Fallback may be used if unavailable.

Font loader should be able to:

1. search local system fonts,
2. check project font directory,
3. download the correct official font package if needed,
4. cache it locally.

The font binary itself does not have to be bundled inside the source ZIP if the implementation downloads it on first run.

---

# 15. STAGE 5 — MANUAL HOOK PLAN

For each configured entry:

```text
hook.start
hook.end
```

becomes:

```text
hook_start_ms
hook_end_ms
hook_duration_ms
```

Example:

```text
02:12.92 → 03:11.72
132920 → 191720
58800 ms
```

This is the final content interval.

No automatic adjustment is allowed.

---

# 16. STAGE 6 — DOWNLOAD / EXTRACT COMPLETE YOUTUBE AUDIO

The selected YouTube visual video is acquired so that its audio can be used as a timing reference.

The audio analysis goal is the song's opening, because the current synchronization method depends on the first 15 seconds of the local source.

The full relevant YouTube audio should be available for scanning.

The selected YouTube video can be shorter than the local source song; this is a known real-world condition.

The global offset stage must therefore calculate whether the configured hook is actually present in the available visual video.

If the mapped hook extends beyond available video duration, the pipeline should fail clearly rather than silently creating an incorrect video.

---

# 17. FINAL YOUTUBE OFFSET ALGORITHM

The final algorithm is deliberately simple.

## 17.1 Local reference

Extract:

```text
original audio [0s : 15s]
```

## 17.2 Features

Use low-rate, robust features:

- normalized waveform at reduced sample rate,
- RMS/energy envelope,
- peak/valley structure.

Normalize each representation so overall loudness differences do not dominate.

## 17.3 Search

Scan the YouTube audio from the start.

At each candidate time `t`, compare:

```text
YouTube[t : t+15s]
```

against:

```text
Original[0 : 15s]
```

Score components:

```text
energy correlation
waveform correlation
peak/valley similarity
```

Combine them into one score.

## 17.4 Earliest near-best policy

If multiple points are nearly equally good, prefer the earliest strong match.

This avoids accidentally selecting a later repeated chorus solely because it has similar energy.

## 17.5 Output

Store:

```text
match_method = "first_15s_anchor"
offset_ms
score
energy_score
peak_valley_score
waveform_score
```

## 17.6 Applying the offset

```text
video_start_ms = hook_start_ms + offset_ms
video_end_ms   = hook_end_ms   + offset_ms
```

No additional hook search.

---

# 18. OFFSET EXAMPLE

Suppose:

```text
original first 15 sec
matches YouTube starting at 1.8 sec
```

then:

```text
offset = +1800 ms
```

Manual hook:

```text
132920 → 191720 ms
```

maps to:

```text
134720 → 193520 ms
```

The local audio remains:

```text
132920 → 191720
```

The YouTube timeline is only used to choose the visual interval.

---

# 19. VIDEO GUARD BAND

A small guard band may be downloaded around the mapped YouTube interval to make FFmpeg extraction safer and preserve natural transitions.

Baseline discussed:

```text
±1.5 seconds
```

For example:

```text
mapped_start = 134720
mapped_end   = 193520
```

download approximately:

```text
133220 → 195020
```

Then exact trim must skip the leading guard.

This was an important implementation correction:

> the exact trim must not take the first `duration` milliseconds from the guarded file as if the guard did not exist.

The trim operation must explicitly receive `trim_start_ms = guard_before_ms`.

---

# 20. EXACT VIDEO TRIM

The final video section duration must equal:

```text
hook_end_ms - hook_start_ms
```

The trim implementation must:

1. seek to `guard_before_ms`,
2. extract exactly `hook_duration_ms`,
3. encode with NVENC if possible,
4. fall back to libx264 if NVENC fails,
5. probe the result,
6. verify duration error is within an accepted tolerance.

The `trim_start_ms` variable must be correctly propagated to both encoder paths.

A previous regression occurred where it was passed to `trim_exact()` but not `_encode_trim()`. The final design must include regression coverage for this.

---

# 21. VIDEO RENDERING — STATIC 80% PANEL

No Smart Crop.

No tracking.

No subject detection.

No identity persistence.

No face-based composition.

The video layout is deterministic.

## 21.1 Target canvas

```text
1080 x 1920
```

## 21.2 Panel size

80% of canvas height:

```text
1920 × 0.80 = 1536 px
```

## 21.3 Placement

The source is scaled to a displayed height of approximately 1536 px.

The resulting image is centered horizontally and vertically within the 1080×1920 canvas.

Horizontal overflow is cropped centrally when necessary.

Vertical overflow should not occur if scaling is based on panel height.

The final composition therefore produces a strong centered cinematic panel occupying about 80% of the Reel's height.

---

# 22. NVIDIA / FFmpeg ACCELERATION

NVIDIA should be used as much as practical in the expensive video encoding stages.

## 22.1 Demucs

If a CUDA-enabled PyTorch environment is available:

```text
Demucs → CUDA
```

## 22.2 Video encoding

Primary:

```text
h264_nvenc
```

Fallback:

```text
libx264
```

## 22.3 Compatibility

Do not assume every FFmpeg build exposes the same optional NVENC flags.

The pipeline should use conservative, widely supported NVENC parameters.

Do not require unsupported flags such as version-sensitive AQ/lookahead options merely because a particular FFmpeg build supports them.

The renderer should:

1. detect `h264_nvenc`,
2. attempt NVENC,
3. detect immediate FFmpeg failure,
4. fall back to CPU encoding cleanly.

The Python frame writer must check that the FFmpeg process is still alive before continuing to write to stdin.

Otherwise a failed encoder can misleadingly manifest as:

```text
OSError: [Errno 22] Invalid argument
```

when the actual cause was FFmpeg exiting because of an unsupported option.

---

# 23. FINAL 8D AUDIO PIPELINE

The local hook interval is cropped from every relevant Demucs stem.

Expected files:

```text
work/audio/cropped_stems/vocals_hook.wav
work/audio/cropped_stems/drums_hook.wav
work/audio/cropped_stems/bass_hook.wav
work/audio/cropped_stems/other_hook.wav
```

## 23.1 Stem timing

Every stem extraction must use exactly:

```text
hook_start_ms
hook_end_ms
```

There must be no independent stem timing drift.

## 23.2 Vocals

Vocals should remain centered to preserve lyric intelligibility.

A very small width may be permitted, but dramatic movement should not compromise clarity.

## 23.3 Bass

Bass should be mono/centered or strongly center-weighted for mono compatibility.

## 23.4 Drums

Drums may use subtle widening or movement while retaining a stable center impression.

## 23.5 Other

`other` is the primary carrier of smooth left/right panning motion.

## 23.6 Motion model

The 8D movement should be smooth rather than random frame-level bouncing.

Suitable movement:

```text
slow sinusoidal / LFO-style panning
gradual width changes
small phase-coherent movement
```

The exact movement rate may vary by implementation, but the motion must not change the musical timeline.

## 23.7 Audio preservation

Do not:

- pitch shift,
- time stretch,
- speed up,
- slow down.

## 23.8 Loudness

A final loudness/peak normalization stage should prevent clipping and maintain comfortable playback.

## 23.9 Audit manifest

Create:

```text
work/audio/audio_8d_manifest.json
```

with at least:

```json
{
  "source_song": "...",
  "hook_start_ms": 132920,
  "hook_end_ms": 191720,
  "duration_ms": 58800,
  "stems": {
    "vocals": "...",
    "drums": "...",
    "bass": "...",
    "other": "..."
  },
  "processing": {
    "vocals": "centered",
    "bass": "mono_center",
    "drums": "center_weighted_moving",
    "other": "primary_8d_motion"
  }
}
```

---

# 24. LYRIC RENDERING PIPELINE

## 24.1 Input

Primary lyric text source:

```text
songs/final/<basename>.lrc
```

Phase 2 JSON can be used for validation/reference but does not replace the LRC as requested display source.

## 24.2 Line lifecycle

For each LRC line:

```text
line_start
   ↓
line visible
   ↓
word highlights progress
   ↓
next line starts
   ↓
previous line removed
```

## 24.3 Word layout

The line should support Telugu shaping correctly.

Intelligent wrapping may be used when a line is too long for the central area, but the line remains conceptually one lyric line rather than a karaoke page containing multiple independent LRC lines.

## 24.4 Emphasis

Strongest visual emphasis should remain on the currently sung word.

Potential visual states:

```text
future word    = normal / subdued
current word   = strongest highlight
past word      = completed/subdued
```

The text itself should not jump unpredictably.

## 24.5 Rendering technology

ASS subtitle generation or direct frame text rendering may be used.

If ASS is used:

- correct UTF-8 handling is mandatory,
- font directory must be passed correctly,
- Windows path escaping must be safe,
- font paths should be represented as `Path` objects internally.

A previous crash came from treating a string as a `Path` and calling `.as_posix()` on it.

The implementation must normalize config paths with `Path(...)` before use.

---

# 25. AUDIO/VIDEO ASSEMBLY

The final assembly combines:

```text
video = exact YouTube visual segment
+
lyrics = generated lyric overlay
+
 audio = local 8D hook audio
```

The YouTube audio is not used as the final Reel audio.

The final container should contain:

```text
video codec: H.264
video size: 1080x1920
video fps: source/selected stable FPS, typically 29.97 or equivalent

audio codec: AAC
channels: stereo
```

The exact audio sample rate may vary depending on implementation but should be consistent and validated.

---

# 26. FINAL OUTPUT FILES

Primary outputs:

```text
reels/generated/<basename>_reel.mp4
reels/generated/<basename>_reel.json
```

The output JSON must be a new Phase 3 provenance file.

It must never modify the upstream Phase 2 JSON.

---

# 27. PROVENANCE JSON

Recommended structure:

```json
{
  "schema_version": 1,
  "pipeline_version": "1.0.16",
  "song": {
    "basename": "...",
    "mp3": "...",
    "lrc": "...",
    "phase2_json": "...",
    "mp3_sha256": "...",
    "lrc_sha256": "...",
    "json_sha256": "..."
  },
  "hook": {
    "mode": "manual_json",
    "start_ms": 132920,
    "end_ms": 191720,
    "duration_ms": 58800
  },
  "youtube_match": {
    "method": "first_15s_anchor",
    "video_id": "...",
    "offset_ms": 1800,
    "score": 0.3376,
    "energy_score": 0.4143,
    "peak_valley_score": 0.1403
  },
  "video": {
    "mapped_start_ms": 134720,
    "mapped_end_ms": 193520,
    "guard_before_ms": 1500,
    "guard_after_ms": 1500,
    "panel_percent_height": 80,
    "output_width": 1080,
    "output_height": 1920
  },
  "audio": {
    "source": "local_demucs_stems",
    "eight_d": true,
    "manifest": "..."
  },
  "lyrics": {
    "source": "lrc",
    "word_highlighting": true,
    "line_by_line": true,
    "font": "Baloo Tammudu 2 ExtraBold"
  },
  "validation": {
    "overall": true,
    "failed_checks": []
  },
  "output": {
    "mp4": "...",
    "sha256": "..."
  }
}
```

---

# 28. VALIDATION SYSTEM

Validation must be explicit and informative.

For a finished Reel, validate at minimum:

## 28.1 File existence

- final MP4 exists
- final JSON exists

## 28.2 Video dimensions

Expected:

```text
1080 × 1920
```

## 28.3 Aspect ratio

Expected:

```text
9:16
```

## 28.4 Duration

Expected:

```text
final duration ≈ hook_end - hook_start
```

No hard 30s or 40s limit.

## 28.5 Audio

Must be:

- present
- stereo
- non-zero duration
- playable

## 28.6 Codec

Expected video codec:

```text
h264
```

Expected audio codec:

```text
aac
```

## 28.7 Encoding/render checks

Ensure final assembly did not produce a zero-length or corrupt file.

## 28.8 Validation JSON

Write:

```text
temp/<basename>/validation/final_validation.json
```

with:

```json
{
  "overall": true,
  "checks": [...],
  "failed_checks": [],
  "probe": {...}
}
```

The validator must derive `overall` from current checks rather than trusting stale state.

An empty `checks=[]` should not automatically become a false failure when the direct required-output checks pass.

If a failure occurs, `failed_checks` must contain descriptive names/messages.

Do not emit only:

```text
Final validation failed
```

without the failed check details.

---

# 29. ERROR HANDLING PRINCIPLES

Errors must preserve the actual root cause.

## 29.1 FFprobe decoding

Do not allow Windows CP1252 decoding to create secondary JSON errors.

## 29.2 FFprobe output

If output is empty:

```text
FFPROBE_EMPTY_OUTPUT
```

If output is malformed:

```text
FFPROBE_JSON_INVALID
```

## 29.3 JSON serialization

Do not create circular references when saving matching results.

A previous error came from:

```text
best = candidates[0]
best['candidates'] = candidates
```

because `best` became recursively self-referential.

Correct behavior:

```python
best = dict(best)
best['candidates'] = [dict(c) for c in candidates]
```

or an equivalent detached structure.

## 29.4 FFmpeg stdin failures

If FFmpeg exits early, the Python writer must detect that condition rather than blindly writing to a dead pipe.

## 29.5 Missing hook configuration

Report:

```text
NOT CONFIGURED — fill hook.start and hook.end
```

Do not guess.

## 29.6 Missing LRC

Report the exact song and expected LRC path.

## 29.7 Missing visual availability

If global offset maps the hook outside the available YouTube video, fail clearly.

Do not silently change the user's hook timeline.

---

# 30. RESUMABILITY AND CACHING

The pipeline is intended to support resumability.

Stage artifacts should be stored per song.

Example:

```text
stems/
analysis/
matching/
video/
audio/
lyrics/
render/
stage_final/
validation/
```

Caching should be invalidated when any of these changes:

- source MP3 hash
- source LRC hash
- input JSON hash
- hook start
- hook end
- YouTube video ID
- synchronization method/version
- renderer version
- key config options

A previous failure showed why stale cached video sections are dangerous.

Cache keys must incorporate the requested timeline and visual-match parameters.

Never blindly reuse a cached section if it was produced for a different hook interval or video mapping.

---

# 31. DATABASE / STATE MACHINE

An internal database/state table may be used for resumability.

The database is **internal to Phase 3** and must not be shared with Phase 1/2.

Useful states:

```text
input_verified
stems_ready
audio_analyzed
lyrics_ready
hook_plan_ready
youtube_audio_ready
video_match_ready
video_segment_ready
audio_8d_ready
lyrics_render_ready
video_rendered
assembled
validated
finalized
failed
review_required
```

Each state should record:

- timestamp
- attempt count
- artifact paths
- error code
- error message
- software version

---

# 32. COMMAND-LINE INTERFACE

## 32.1 Normal processing

```powershell
python main.py
```

Normal behavior:

1. auto-sync `hook_timeline.json`,
2. load the hook plan,
3. process every configured song,
4. report unconfigured songs,
5. generate Reels for configured songs.

## 32.2 Process all explicit alias

Historical implementation supported:

```powershell
python main.py --process-all
```

This should remain supported where practical for compatibility.

## 32.3 Fill/sync hook JSON explicitly

```powershell
python main.py --fillhookjson
```

This only synchronizes song/LRC entries.

It does not choose hooks.

## 32.4 Doctor

```powershell
python main.py --doctor
```

## 32.5 Force rebuild

A force option may be retained:

```powershell
python main.py --force
```

or equivalent, to invalidate cached stages.

---

# 33. USER WORKFLOW — FINAL

## First run after adding songs

```powershell
python main.py
```

Phase 3 automatically creates/updates:

```text
hook_timeline.json
```

The user edits the file.

Example:

```json
{
  "song_path": "songs/final/001_Gelupu Thalupule_Mani Sharma, Sreerama Chandra.mp3",
  "lrc_path": "songs/final/001_Gelupu Thalupule_Mani Sharma, Sreerama Chandra.lrc",
  "hook": {
    "start": "02:12.92",
    "end": "03:11.72"
  }
}
```

Then:

```powershell
python main.py
```

Everything else is automatic.

---

# 34. SAMPLE SONG — KNOWN REAL-WORLD BEHAVIOR

The discussed sample song was:

```text
001_Gelupu Thalupule_Mani Sharma, Sreerama Chandra
```

Observed source duration was approximately:

```text
317.592 seconds
```

The actual sample also demonstrated that the selected visual YouTube video can be much shorter than the local song.

That is why visual availability must be checked after applying the global offset.

The supplied timeline example is:

```text
02:12.92 → 03:11.72
```

which equals:

```text
132920 ms → 191720 ms
58.800 seconds
```

The recent execution logs confirmed this parsing is correct.

---

# 35. HISTORY OF SUPERSEDED HOOK SELECTION DESIGN

This section records the earlier ideas so they are not lost, but they are **not part of the current final implementation**.

Originally, Phase 3 was designed to automatically discover hooks through:

- full-song Demucs analysis,
- lyric memorability,
- repeated phrases,
- repeated section families,
- energy contours,
- vocal quality,
- arrangement richness,
- phrase completeness,
- timing quality,
- 10–15 diverse candidates,
- context expansion,
- downstream YouTube eligibility filtering.

Variable hook length was discussed, roughly 10–35 seconds core with possible 15–60 second final Reel.

A manual `--manualhook` override mode was also designed.

These mechanisms were subsequently removed in favor of the direct JSON timeline workflow.

They should not be accidentally reintroduced into the current pipeline.

---

# 36. HISTORY OF SUPERSEDED SMART CROP DESIGN

Earlier designs included:

- face detection,
- body/pose tracking,
- persistent track IDs,
- A/B identity hysteresis,
- multi-person selection,
- shot-aware crop,
- lyric collision avoidance,
- dynamic subject framing.

A later tracker implementation was tested but produced:

```json
{
  "primary_track_id": null,
  "samples": [],
  "tracking_confidence_mean": 0,
  "subject_switches": 0
}
```

The decision was then made to remove Smart Crop entirely.

The current design is the simpler static centered 80%-height panel.

No tracking code should be required for the final pipeline.

---

# 37. HISTORY OF SUPERSEDED YOUTUBE MATCHING DESIGNS

Several matching strategies were considered and then simplified.

Removed/obsolete approaches include:

- hook-by-hook query matching,
- DTW,
- multi-anchor verification,
- residual-error acceptance gates,
- complex global alignment logic,
- repeated peak-pattern hook matching.

A whole-track correlation implementation produced an obviously wrong example:

```text
-99.822 seconds
```

That strategy was discarded.

The final approach is the simpler 15-second anchor scan described earlier.

---

# 38. HISTORY OF DURATION LIMITS

Earlier requirements used approximately 30 seconds as a preferred target.

That was later changed to a 40-second maximum.

That was then removed entirely.

The current final requirement is:

> **No maximum Reel duration. Use exactly the start/end timeline entered by the user.**

This is authoritative.

---

# 39. HISTORY OF VIDEO PANEL SIZE

Earlier static video layout used approximately 70% of 9:16 height.

The user later changed it to:

```text
80%
```

Current authoritative value:

```text
80% of 1920 = 1536 px
```

---

# 40. HISTORY OF LYRICS DESIGN

Earlier plans proposed a persistent lyric context block with current-word highlighting and potentially two lines.

The user later clarified the desired behavior:

- one LRC line at a time,
- line disappears when next line appears,
- current word in the line highlighted.

Current authoritative design is therefore **line-by-line LRC display with word-by-word highlighting**.

---

# 41. HISTORY OF 8D AUDIO DESIGN

The final design evolved from a conceptual 8D stage into explicit stem-based processing.

Current requirements:

- crop exact hook from Demucs stems,
- process vocals/drums/bass/other separately,
- recombine into stereo,
- preserve timeline,
- no pitch/speed alteration.

The local song is always the final audio source.

---

# 42. WINDOWS-SPECIFIC LESSONS

Phase 3 is expected to run on Windows.

Known implementation pitfalls:

## 42.1 Encoding

Always explicitly handle subprocess output as UTF-8.

## 42.2 Path types

Normalize configuration paths to `Path` objects before calling:

```python
.as_posix()
```

Never assume a config field is already a `Path`.

## 42.3 FFmpeg subprocess pipes

Check subprocess liveness before writing frames.

## 42.4 File locks

Windows can retain file handles longer than expected.

Use context managers and close FFmpeg/decoder processes deterministically.

## 42.5 Unicode song names

Song names and metadata may contain Telugu and other non-ASCII text.

All JSON should be written using:

```python
ensure_ascii=False
encoding='utf-8'
```

---

# 43. SOURCE JSON VS PHASE 3 JSON

Two different JSON roles must remain distinct.

## Input JSON

```text
songs/final/Song.json
```

This belongs to the upstream Phase 2 package.

Phase 3 reads it but does not mutate it.

## Output JSON

```text
reels/generated/Song_reel.json
```

This belongs entirely to Phase 3.

It records the exact decision and transformation history.

Never reuse the input file for output provenance.

---

# 44. PROVENANCE REQUIREMENTS

Every Reel should be reproducible from recorded metadata.

Record:

- source MP3 SHA-256
- source LRC SHA-256
- source JSON SHA-256
- hook start/end
- hook duration
- YouTube video ID
- YouTube global offset
- synchronization score/components
- guard band
- actual video mapped start/end
- Demucs model
- stems paths
- 8D processing parameters
- lyric parser/render configuration
- font name
- panel size
- output resolution
- output FPS
- encoder used
- fallback status if applicable
- validation results
- output SHA-256
- pipeline version

---

# 45. TEST PLAN

The project should maintain regression coverage for all bugs encountered.

## 45.1 Contract tests

- MP3/LRC/JSON basename pairing
- actual sample metadata
- Unicode metadata

## 45.2 Windows UTF-8 test

Run a subprocess that emits Telugu plus Unicode symbols and confirm decoding succeeds.

## 45.3 FFprobe test

Confirm audio stream selection when cover art is present.

## 45.4 Circular JSON test

Confirm candidate match output serializes without circular-reference errors.

## 45.5 Timeline parser tests

Required:

```text
02:12.92
02:12.920
00:05
01:02:03.500
```

## 45.6 Hook duration test

Confirm:

```text
03:11.72 - 02:12.92 = 58.8 sec
```

## 45.7 JSON auto-sync test

Confirm:

- new MP3 added,
- matching LRC added,
- existing hook preserved.

## 45.8 LRC tests

Confirm word-level inline timestamp parsing for Telugu.

## 45.9 15-second anchor test

Use synthetic audio with known offset and confirm the recovered offset.

## 45.10 Trim guard test

Confirm:

```text
source = guarded clip
trim_start_ms = guard
output duration = requested hook duration
```

## 45.11 Renderer test

Confirm:

```text
1080x1920
80% panel
NVENC path when available
CPU fallback when NVENC unavailable
```

## 45.12 8D test

Confirm cropped stem files exist and final WAV is stereo.

## 45.13 Final validation test

Confirm valid output yields:

```text
overall = true
failed_checks = []
```

---

# 46. ACCEPTANCE CRITERIA

Phase 3 is considered complete when all of the following are true.

## Inputs

- standalone project
- no Phase 1/2 imports
- source folder read-only
- same-basename package handling

## Hook control

- automatic hook selection absent
- hook timeline JSON authoritative
- arbitrary duration allowed
- `MM:SS.xx` supported
- `python main.py` auto-updates JSON
- existing times preserved

## YouTube synchronization

- first 15 seconds of local song used as anchor
- YouTube scanned from start
- one global offset calculated
- manual hook shifted by offset
- no per-hook re-search

## Video

- exact requested hook duration
- guard band correctly handled
- centered static panel
- 80% of 9:16 height
- 1080×1920 final output
- NVENC-first
- CPU fallback

## Audio

- local source only
- Demucs executed by Phase 3
- exact hook crop from stems
- 8D stem-based processing
- stereo final audio
- no pitch/speed manipulation

## Lyrics

- LRC source
- line-by-line display
- line disappears when next line begins
- word-level highlighting
- Telugu-compatible font rendering
- centered lyric placement

## Validation

- final MP4 valid
- final JSON valid
- duration matches hook
- 1080×1920
- stereo audio
- `overall=true`
- empty `failed_checks`

---

# 47. PERFORMANCE REQUIREMENTS

## GPU

Use CUDA for Demucs where available.

Use NVENC for video encoding where available.

## CPU

CPU remains appropriate for:

- JSON parsing
- LRC parsing
- timeline validation
- feature extraction where small
- FFmpeg orchestration
- provenance

## Cache

Cache expensive stages:

- Demucs stems
- downloaded YouTube audio
- matched YouTube section
- 8D intermediate stems
- lyric render assets

Never compromise correctness for cache reuse.

---

# 48. LOGGING REQUIREMENTS

Logs should clearly show:

```text
Auto-updated hook timeline
Loaded hook timeline
HOOK PLAN
YouTube 15s anchor offset
VideoGrabber download status
VideoRenderer encoder
8D audio generation
Lyrics rendering
Assembly
Validation
Final output path
```

For failures, include:

- stage
- error code
- affected song
- source path
- relevant artifact path
- exact failed check

Example:

```text
ERROR OUTPUT_VALIDATION_FAILED: duration mismatch
song=...
expected=58800ms
actual=58512ms
artifact=...
```

---

# 49. OUTPUT ORGANIZATION

Generated Reels should never overwrite source packages.

Use:

```text
reels/generated/
```

Recommended naming:

```text
<basename>_reel.mp4
<basename>_reel.json
```

The basename must match the input song basename so the provenance is obvious.

---

# 50. FAILURE RECOVERY STRATEGY

When one song fails, `process_all` should continue to the next song when safe.

Each song gets an independent work directory.

The summary should report:

```text
processed
succeeded
failed
unconfigured
```

A failed song must retain its stage artifacts and error log so debugging does not require rerunning Demucs unnecessarily.

---

# 51. QUALITY PRINCIPLES

Phase 3 should prioritize:

1. user-entered timeline correctness,
2. local audio correctness,
3. visual synchronization correctness,
4. exact output duration,
5. lyric timing correctness,
6. stable video composition,
7. GPU acceleration where available,
8. reproducible provenance.

It should not introduce complicated heuristics merely to make the system appear more intelligent.

The user deliberately chose a simpler and more deterministic architecture.

---

# 52. IMPORTANT NON-NEGOTIABLES

The final implementation must **not** silently reintroduce any of these removed ideas:

```text
automatic hook selection
30-second limit
40-second limit
Smart Crop
face tracking
pose tracking
per-hook YouTube audio matching
DTW
complex anchor verification
YouTube audio as final Reel audio
ASR-based lyrics discovery
Instagram automatic publishing
mutation of songs/final source files
```

The intended current design is deterministic and user-controlled.

---

# 53. END-TO-END FINAL PIPELINE

The complete final pipeline is:

```text
                    ┌─────────────────────────────┐
                    │ songs/final/                │
                    │ MP3 + LRC + JSON            │
                    └──────────────┬──────────────┘
                                   │
                                   ▼
                    ┌─────────────────────────────┐
                    │ AUTO-SYNC hook_timeline.json│
                    │ add new songs + LRC paths   │
                    │ preserve existing hooks     │
                    └──────────────┬──────────────┘
                                   │
                                   ▼
                    ┌─────────────────────────────┐
                    │ LOAD MANUAL HOOK TIMELINE   │
                    │ start/end are authoritative │
                    └──────────────┬──────────────┘
                                   │
                                   ▼
                    ┌─────────────────────────────┐
                    │ VERIFY SOURCE + PROBE MP3   │
                    └──────────────┬──────────────┘
                                   │
                                   ▼
                    ┌─────────────────────────────┐
                    │ RUN DEMUCS ON LOCAL SONG     │
                    │ vocals / drums / bass / other│
                    └──────────────┬──────────────┘
                                   │
                   ┌───────────────┴───────────────┐
                   │                               │
                   ▼                               ▼
        ┌──────────────────────┐       ┌──────────────────────┐
        │ FULL AUDIO ANALYSIS  │       │ PARSE LRC             │
        │ diagnostics/features │       │ line + word timing    │
        └──────────┬───────────┘       └──────────┬───────────┘
                   │                               │
                   └───────────────┬───────────────┘
                                   │
                                   ▼
                    ┌─────────────────────────────┐
                    │ YOUTUBE AUDIO REFERENCE     │
                    │ take original first 15s      │
                    │ scan YT from 00:00           │
                    │ find simple strongest match  │
                    │ produce one global offset    │
                    └──────────────┬──────────────┘
                                   │
                                   ▼
                    ┌─────────────────────────────┐
                    │ APPLY OFFSET TO MANUAL HOOK │
                    │ video_start = hook + offset │
                    │ video_end   = hook + offset │
                    └──────────────┬──────────────┘
                                   │
                                   ▼
                    ┌─────────────────────────────┐
                    │ DOWNLOAD YOUTUBE VIDEO      │
                    │ ± guard band                │
                    └──────────────┬──────────────┘
                                   │
                                   ▼
                    ┌─────────────────────────────┐
                    │ EXACT TRIM                  │
                    │ skip guard                  │
                    │ exact hook duration         │
                    │ NVENC → CPU fallback        │
                    └──────────────┬──────────────┘
                                   │
                    ┌──────────────┴──────────────┐
                    │                             │
                    ▼                             ▼
        ┌──────────────────────┐      ┌─────────────────────────┐
        │ CROP EXACT DEMUCS    │      │ GENERATE LINE-BY-LINE   │
        │ HOOK STEMS           │      │ LYRIC OVERLAY FROM LRC  │
        └──────────┬───────────┘      │ current-word highlight  │
                   │                  └────────────┬────────────┘
                   ▼                               │
        ┌──────────────────────┐                   │
        │ GENERATE 8D AUDIO    │                   │
        │ vocals center        │                   │
        │ bass center          │                   │
        │ drums subtle motion  │                   │
        │ other main movement  │                   │
        └──────────┬───────────┘                   │
                   │                               │
                   └──────────────┬────────────────┘
                                  │
                                  ▼
                    ┌─────────────────────────────┐
                    │ STATIC VIDEO RENDER         │
                    │ 1080×1920                   │
                    │ 80% height panel            │
                    │ centered                    │
                    │ NVENC-first                 │
                    └──────────────┬──────────────┘
                                   │
                                   ▼
                    ┌─────────────────────────────┐
                    │ ASSEMBLE                    │
                    │ local 8D audio + video      │
                    │ + lyric overlay             │
                    └──────────────┬──────────────┘
                                   │
                                   ▼
                    ┌─────────────────────────────┐
                    │ FINAL VALIDATION            │
                    │ duration / size / audio /   │
                    │ codec / integrity           │
                    └──────────────┬──────────────┘
                                   │
                              PASS │
                                   ▼
                    ┌─────────────────────────────┐
                    │ reels/generated/            │
                    │ Song_reel.mp4               │
                    │ Song_reel.json              │
                    └─────────────────────────────┘
```

---

# 54. FINAL IMPLEMENTATION CHECKLIST

Before calling the project complete, verify all of the following.

## Project boundary

- [ ] Phase 3 standalone
- [ ] no upstream imports
- [ ] no upstream DB dependency
- [ ] source folder read-only

## Input management

- [ ] basename matching
- [ ] automatic `hook_timeline.json` synchronization
- [ ] LRC paths recorded
- [ ] existing hooks preserved
- [ ] unconfigured songs reported

## Hook control

- [ ] manual-only
- [ ] no automatic selection
- [ ] no duration cap
- [ ] `MM:SS.xx` accepted
- [ ] exact duration derived from JSON

## Analysis

- [ ] Demucs CUDA support
- [ ] full-song audio analysis
- [ ] LRC parsing
- [ ] word timing parsing

## YouTube synchronization

- [ ] first 15 seconds anchor
- [ ] scan from YouTube start
- [ ] energy + peaks/valleys + waveform correlation
- [ ] one global offset
- [ ] earliest strong match preference
- [ ] mapped hook interval

## Video

- [ ] guard band
- [ ] exact trim start offset
- [ ] exact duration
- [ ] static centered rendering
- [ ] 80% panel height
- [ ] 1080×1920
- [ ] NVENC first
- [ ] CPU fallback

## Audio

- [ ] local song only
- [ ] crop exact hook stems
- [ ] 8D mixing
- [ ] vocal centered
- [ ] bass centered
- [ ] drums subtly spatial
- [ ] other carries movement
- [ ] no pitch/speed alteration

## Lyrics

- [ ] LRC is display source
- [ ] one line at a time
- [ ] word highlighting
- [ ] next line replaces previous
- [ ] centered placement
- [ ] Telugu font shaping

## Validation

- [ ] final MP4 exists
- [ ] final JSON exists
- [ ] 1080×1920
- [ ] 9:16
- [ ] duration matches JSON
- [ ] stereo audio
- [ ] valid H.264/AAC
- [ ] `overall=true`
- [ ] `failed_checks=[]`

## Provenance

- [ ] source hashes
- [ ] hook timeline
- [ ] YouTube offset
- [ ] video IDs
- [ ] Demucs/stem information
- [ ] 8D manifest
- [ ] lyric configuration
- [ ] renderer configuration
- [ ] validation results
- [ ] output hash

---

# 55. FINAL DECISION SUMMARY

The final Phase 3 architecture is intentionally **simple, deterministic, standalone, and manually controlled at the hook-selection layer**.

The user chooses the musical interval.

Phase 3 performs everything else:

```text
manual timeline
→ source validation
→ Demucs
→ analysis
→ first-15-second YouTube synchronization
→ global offset
→ exact visual extraction
→ static 80% centered visual
→ exact stem crop
→ 8D audio
→ LRC line-by-line lyrics with word highlighting
→ NVENC render
→ final assembly
→ validation
→ provenance
```

The two most important authoritative inputs are:

```text
songs/final/<basename>.mp3
hook_timeline.json
```

The authoritative manual hook entry is:

```json
"hook": {
  "start": "02:12.92",
  "end": "03:11.72"
}
```

The system must use that interval exactly, without a maximum-duration policy and without automatically replacing it with another hook.

The final Reel's audio comes from the local song's Demucs stems, not YouTube.

YouTube contributes only the visual timing reference through one simple first-15-second global offset.

The final video is a centered static 80%-height panel on a 1080×1920 canvas.

Lyrics are displayed one LRC line at a time with per-word highlighting.

The project remains completely independent from Phase 1 and Phase 2 implementation internals.

---

# 56. VERSION / CHANGE HISTORY REFERENCE

The implementation evolved through multiple builds while debugging real Windows runs. The meaningful progression was:

```text
V1.0.1  Windows UTF-8 / FFprobe reliability fix
V1.0.2  Circular JSON match-result fix
V1.0.3  Simplified YouTube hook matching
V1.0.4  Path fix + NVIDIA/NVENC rendering
V1.0.5  NVENC compatibility + broken-pipe protection
V1.0.6  Advanced Smart Crop experiment (later rolled back)
V1.0.7  Rollback + 40s experiment + stem 8D implementation
V1.0.8  Manual hook JSON architecture + Smart Crop removal + LRC line rendering
V1.0.9  --fillhookjson
V1.0.10 automatic hook JSON synchronization at startup
V1.0.11 no duration cap + simplified global offset
V1.0.12 exact guarded video trim / cache correction
V1.0.13 first-15-second YouTube anchor synchronization
V1.0.14 guard-aware trimming + better validation diagnostics
V1.0.15 trim_start propagation regression fix
V1.0.16 80% static panel + final validation correction
```

These version notes are historical. The **current design is defined by the requirements in Sections 2 through 55**, not by any older experimental behavior.

---

# 57. FINAL PROJECT STATEMENT

Phase 3 is a standalone, deterministic Telugu music Reel generator driven by manually selected hook timelines.

It accepts a local song package, automatically maintains a user-editable hook plan, calculates one simple visual synchronization offset from the first 15 seconds of the original song, extracts the exact visual interval from the selected YouTube video, creates the final Reel audio from exact Demucs stem crops with controlled 8D spatialization, renders LRC lyrics line by line with per-word highlighting, places the video as a centered 80%-height panel in a 1080×1920 canvas, uses NVIDIA acceleration where available, and produces a validated MP4 plus independent provenance JSON without touching the upstream input package.

The system is intentionally not an automatic hook recommender and intentionally not a smart-crop tracker. The user's hook timeline is the source of truth.

---

# 58. UNIFIED PERSISTENT DATABASE ARCHITECTURE

The unified project uses three persistent SQLite databases:

```text
db/
├── playlist.db
├── songs.db
└── reels.db
```

The separation is intentional:

```text
playlist.db
    = one-time playlist ingestion, playlist identity, permanent serials,
      view/play priority, unique YT Music IDs, playlist membership,
      frozen queue state, playlist completion state, and ingestion history.

songs.db
    = retained songs, source/enrichment metadata, Spotify overlay,
      YouTube visual-video selection + global 30-second offset, lyrics,
      Demucs, audio analysis, word alignment, whole-song 8D,
      stage fingerprints, source artifacts, and song lifecycle.

reels.db
    = manual hook entries, YouTube hook mapping,
      8D hook extraction, video extraction/rendering,
      lyric rendering, Reel validation, Reel outputs,
      Reel lifecycle and Reel-specific audit history.
```

These are the unified application's final operational databases; they are not the old independent Phase 1/2/3 databases copied unchanged.

## 58.1 Stable cross-database identity

SQLite cannot enforce a foreign key between two independently opened database files. The stable application identity is therefore:

```text
song_key
```

`reels.db.reels.song_key` must exactly match `songs.db.songs.song_key`.

The application must never identify a Reel only by a mutable filename.

## 58.2 `songs.db` ownership

`songs.db` owns:

```text
playlist_occurrences
songs
metadata
youtube_matches
youtube_offset_candidates
lyric_sources
demucs_runs
analysis_runs
alignment_runs
lyric_lines
lyric_words
audio_8d_runs
stage_runs
source_artifacts
audit_events
```

The delivered baseline schema is `songs_schema.sql` and the initialized database is `songs.db`.

## 58.3 `reels.db` ownership

`reels.db` owns:

```text
reels
reel_hooks
reel_youtube_sync
reel_audio
reel_video
reel_lyrics
reel_validation
reel_runs
reel_audit_events
```

The delivered baseline schema is `reels_schema.sql` and the initialized database is `reels.db`.

## 58.4 Song lifecycle

Terminal states:

```text
FINALIZED
SKIPPED_NO_SYNCED_LRC
DUPLICATE_KEEP_PREVIOUS
```

Non-terminal states include:

```text
PENDING
PROCESSING
HOOK_REQUIRED
NEEDS_REVIEW
RETRYABLE_ERROR
```

Normal batch selection must never pick terminal rows.

A song with no synchronized LRC is therefore skipped once and is not automatically processed again on later normal runs.

## 58.5 Reel lifecycle

Recommended Reel states:

```text
PENDING
HOOK_REQUIRED
VIDEO_SYNC_READY
RENDERING
VALIDATING
FINALIZED
FAILED
NEEDS_REVIEW
```

A changed hook invalidates only Reel-dependent work. It must not regenerate acquisition, YouTube song offset, alignment, or the whole-song 8D master.

## 58.6 Stage fingerprints

Every reusable stage records a dependency fingerprint. Representative identities are:

```text
YOUTUBE_SYNC
    = source audio hash + selected YouTube video ID + sync algorithm version

ALIGNMENT
    = source MP3 hash + source LRC hash + Demucs fingerprint
      + MMS fingerprint + normalizer version + alignment config

WHOLE_SONG_8D
    = source MP3 hash + Demucs stem hashes + canonical word timeline hash
      + 8D configuration hash + 8D engine version

REEL
    = YouTube video ID + stored YouTube offset + hook start/end
      + whole-song 8D hash + word-level LRC hash + renderer version
```

This is the basis for dependency-aware caching and selective reruns.

## 58.7 Finalization transaction

The finalization sequence is:

```text
1. Build artifacts in temp.
2. Validate all required outputs.
3. Assemble cumulative Song.json.
4. Assemble final MP3 metadata.
5. Re-open and validate the MP3.
6. Atomically promote final song files.
7. Atomically promote Reel files when a Reel exists.
8. Commit database final state.
9. Mark the song/Reel terminal state.
10. Clean temp only after successful finalization.
```

A row must never be marked `FINALIZED` before its corresponding final artifacts have passed validation.

## 58.8 Recovery and reconciliation

The databases are operational state stores; they are not the sole recovery source. The final `SongName.json` remains independently inspectable.

A reconciliation command must be able to scan:

```text
songs/final/
reels/generated/
```

verify hashes and reconstruct safe missing database state.

## 58.9 Database integrity requirements

The application must enforce:

```text
stable song_key
permanent playlist serials
ISRC-only duplicate policy
source hashes for source versions
terminal skip protection
word/alignment ownership
8D run ownership
Reel/song relationship via song_key
millisecond hook storage
millisecond YouTube offset storage
finalize-after-validation
recoverable failures
provenance consistency
```

---

# 59. FINAL PERSISTENT DATABASE FILES

The unified project package includes the initialized databases:

```text
UNIFIED_PROJECT_DATABASES/
├── playlist.db
├── songs.db
├── reels.db
├── playlist_schema.sql
├── songs_schema.sql
├── reels_schema.sql
└── README.md
```

These files are the starting persistent data layer for the unified implementation.

The schemas are intentionally explicit rather than being dynamically created by unrelated Phase modules. Schema versioning and migrations must be handled by the unified CLI.

---

# 60. FINAL PROJECT FILE LAYOUT INCLUDING DATABASES

```text
project/
├── main.py
├── config.json
├── requirements.txt
├── requirements-dev.txt
├── README.md
├── BUILD_INFO.md
├── CHANGELOG.md
│
├── db/
│   ├── playlist.db
│   ├── songs.db
│   └── reels.db
│
├── database/
│   ├── playlist_schema.sql
│   ├── songs_schema.sql
│   └── reels_schema.sql
│
├── songs/
│   └── final/
│       ├── SongName.mp3
│       ├── SongName.lrc
│       ├── SongName_wordlevel.lrc
│       ├── SongName.json
│       └── SongName_8D.mp3
│
├── reels/
│   └── generated/
│       ├── SongName_reel.mp4
│       └── SongName_reel.json
│
├── temp/
│   └── <song_key>/
│
├── models/
│   ├── demucs/
│   ├── mms/
│   └── vad/
│
├── assets/
│   └── fonts/
│
└── logs/
```

`hook_timeline.json` is not part of the unified project.

Hook start/end values are entered manually in the terminal and persisted in the database and cumulative Song/Reel JSON records.

---

# 61. FINAL DATABASE DELIVERY / VALIDATION

The supplied SQLite files must pass:

```text
PRAGMA integrity_check;
```

and the schema SQL files must reproduce the initialized database structure.

Before production use, the implementation must add migration handling, backup/restore, transactional tests, crash-recovery tests, and reconciliation tests.
