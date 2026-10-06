# Full Project Audit — Spotify-First Unified Release 1.4.3

## 1. Audit scope

This audit covers the active unified implementation, the current `PROJECT_PLAN.md`, the three supplied phase ZIP codebases preserved under `archives/`, the operator runtime failure log supplied during integration, the three SQLite schemas/databases, and the final regression test matrix. The audit distinguishes active runtime behavior from historical archived behavior.

## 2. Final architecture decision

The current authority chain is:

```text
Spotify playlist
  -> one-time frozen playlist.db snapshot
  -> exact saved playlist order
  -> one song at a time
  -> Spotify full catalog enrichment + artwork + ISRC
  -> finalized-only ISRC duplicate gate
  -> YT Music title + album search
  -> closest Spotify-duration song result
  -> ytmusicapi get_song direct stream
  -> HTTP download + FFmpeg MP3
  -> LRCLIB synchronized lyrics gate
  -> one full Demucs separation
  -> Telugu MMS/CTC word timing
  -> canonical word-level LRC + SYLT
  -> whole-song 8D
  -> manual hook
  -> YT Music visual source + global 30-second offset
  -> Reel
  -> validation + atomic promotion
```

No source is allowed to silently take another source's authority.

## 3. Spotify playlist ingestion audit

The active first-run path uses the current Spotify Web API playlist-items endpoint (`GET /playlists/{playlist_id}/items`) and paginates in API order with a request limit of 50. The playlist-detail request is used for playlist-level metadata; the item endpoint supplies the complete ordered item set. Spotify documents the item endpoint, its OAuth requirement, its 50-item maximum, and the fact that it is restricted to playlists the user owns or collaborates on. citeturn565606search0turn565606search1

A new playlist ID is processed as a frozen job. The entire returned item set is normalized from the already-fetched page payload, then `playlist.db` is committed atomically before downstream databases or pipeline stages are initialized. The first normal run exits after the snapshot commit. No per-track Spotify detail requests, artwork downloads, YT Music calls, media downloads, lyrics, Demucs, alignment, 8D, hook entry or Reel work occurs in that first run.

Later normal runs do not call Spotify playlist endpoints again for an already ingested playlist ID. The stored `playlist_position` is the sole processing order.

Every returned playlist item is persisted, including non-track/local/unavailable entries, with raw item JSON and an explicit terminal reason where the item is not actionable.

## 4. Removal of popularity/view/play logic

The active `playlist_entries` schema contains no priority/ranking/view/play columns. An active-runtime static scan found zero occurrences of the retired identifiers `priority_count`, `priority_metric`, `processing_rank`, `ytm_views`, `ytm_plays`, `ytm_engagement_count`, `viewCount`, and `playCount`. There is no resolver loop and no YT Music popularity request. The prepared Spotify playlist order is accepted as authoritative.

Historical references to popularity in archived Phase 1 material are retained only as provenance.

## 5. Spotify per-song catalog audit

For each actionable playlist entry, `SpotifyClient.get_full_track()` performs the currently required individual catalog retrieval flow: track, full album, each unique track artist, and each unique album artist. The implementation caches album/artist detail calls within a processing client session. Raw track, album and artist records are retained.

The active `SpotifyResult` retains title, all track artists, artist IDs/URLs, album data, album artists and IDs, album type, release date and precision, album track count, artwork list and largest-artwork selection, label, copyrights, artist genres, explicit flag, track/disc numbers, duration and normalized ISRC. The master JSON also retains the original raw payloads rather than flattening away source detail.

Spotify artwork is downloaded as original bytes with no resampling or recompression. The largest valid image is selected deterministically from the returned album image set.

Spotify's current 2026 API migration removed bulk track/album/artist fetch endpoints; current documentation explicitly directs clients to individual `/tracks/{id}`, `/albums/{id}`, and `/artists/{id}` requests. The implementation follows that current contract. citeturn565606search6

Spotify audio is never requested or downloaded. Spotify is used for metadata, playlist membership and artwork only. Spotify's developer reference explicitly notes that Spotify content may not be downloaded. citeturn565606search0

## 6. ISRC duplicate audit

ISRC is normalized by removing separators, uppercasing and validating the canonical 12-character form. No ISRC is invented.

A duplicate lookup happens only after the current song has been fully enriched from Spotify and only against existing `songs` rows whose `pipeline_status='FINALIZED'`. This prevents in-progress or failed work from acting as the canonical duplicate.

The operator is shown the duplicate ISRC and chooses:

```text
1 = keep previous
2 = keep current
```

`keep previous` terminates the current playlist entry as a duplicate without YT Music acquisition. `keep current` completes and validates the current package first, then safely marks the older finalized song as replaced and removes the older public artifacts. The duplicate decision itself is persisted in `isrc_duplicates`.

The duplicate check intentionally occurs before expensive YT Music media acquisition.

## 7. YT Music audio source audit

The active audio source is `ytmusicapi` only. Search uses the exact Spotify-derived query:

```text
<Spotify title> <Spotify album>
```

The `songs` filter is used. Returned results are inspected in their original order. A result is eligible only if it has a usable known duration and its duration is within the configured tolerance of the Spotify catalog duration. The first eligible result wins. Unknown-duration results are rejected rather than guessed.

The selected ID is converted to the canonical `https://music.youtube.com/watch?v=<video_id>` URL. yt-dlp receives only that URL (`noplaylist=True`) and performs the audio download/FFmpeg MP3 extraction. It is never given a search query. The local MP3 duration is measured and compared against the Spotify catalog duration.

Acquisition metadata is written with canonical top-level title/artist/album/duration fields plus nested downloader provenance, and the normalizer accepts both the current and older nested layouts so cached masters remain recoverable after upgrades.

The current `ytmusicapi` API documents `search()` with `songs` and `videos` filters and `get_song()` as returning metadata and streaming information. citeturn840619search2turn840619search0

## 8. YT Music visual-source audit

The Reel visual source is selected with the Phase-1 YouTube search rule: `title + album + video song hd`. Results are inspected in returned order; titles containing `lyric`, `lyrics`, or `lyrical` are rejected, and the first remaining result wins. Duration is deliberately ignored for visual selection. The selected video ID is then used for reference-audio acquisition and global offset matching.

The visual source remains separate from the audio-source selection so the two provenance records can be audited independently.

## 9. YouTube global offset audit

The global offset is retained from the integrated Phase 3 design. It uses the original song, never the 8D output, and takes the first 30 seconds as reference. The reference is scanned across the full selected visual audio using structural signals including normalized waveform, energy/RMS envelope, peaks/valleys and transitions.

Only candidates within the hard 0–30,000 ms offset bound are accepted. If the highest-scoring candidate is outside the bound, the next valid candidate is selected. There is one persisted global offset and no per-hook rematching or DTW.

## 10. Lyrics gate audit

LRCLIB remains the synchronized lyric provider. The pipeline first tries the exact `/api/get` metadata lookup, then falls back to `/api/search` using title+artist and title when exact metadata matching fails. Only responses containing valid timestamped `syncedLyrics` are accepted. Lyrics are fetched before the visual-video stage, so a visual-search/download failure cannot prevent an otherwise available synced LRC from being written. A missing synchronized LRC causes terminal `SKIPPED_NO_SYNCED_LRC`.

No Demucs, MMS/CTC, whole-song 8D, hook prompt or Reel work is performed after this terminal skip.

## 11. Shared audio/Demucs audit

The source MP3 is decoded once for shared downstream analysis. Source identity is hash-based. One full Demucs separation is performed per source/config identity, producing vocals, drums, bass and other stems. Phase 2 alignment and the whole-song 8D stage consume the same stems.

Demucs cache identity includes source identity and configuration so a stale stem package is not reused for a changed source/configuration. Windows temporary audio handling retains explicit WAV-safe extensions to avoid FFmpeg container mis-detection.

## 12. Telugu alignment audit

The inherited Phase 2 implementation remains responsible for Telugu reversible normalization, VAD/activity analysis, chunking, MMS inference, CTC reference alignment, token-to-word mapping, canonical integer-millisecond timing, overlap deduplication, chronology repair, word-level LRC generation and SYLT embedding. The unified pipeline consumes the canonical results rather than rebuilding timings from rounded LRC text.

## 13. Whole-song 8D audit

The 8D stage runs over the complete original song from 0:00 through the actual song end. It receives original audio, Demucs stems, audio/activity information and the canonical lyric timeline. Vocals stay comparatively centered; bass remains mono-compatible and center-oriented; drums and other layers receive smooth controlled spatial movement. No pitch or speed manipulation is introduced.

The whole-song 8D output is stored as `SongName_8D.mp3` and is used only by the Reel stage. Hook changes do not trigger another 8D pass.

## 14. Manual hook audit

The operator enters:

```text
Hook start (MM:SS.xxx):
>
Hook end (MM:SS.xxx):
>
```

The values are converted to integer milliseconds, validated against the original song duration, and persisted immediately. There is no configured maximum hook duration.

The Reel audio crop is taken directly from the completed whole-song 8D master.

## 15. Final Reel audit

The visual Reel is generated only after the song package dependencies are available. YouTube hook mapping is deterministic:

```text
youtube_start = hook_start_ms + global_offset_ms
youtube_end   = hook_end_ms   + global_offset_ms
```

The Reel is kept separate from the five-file song package. Reel JSON records hook, visual source, global offset, whole-song 8D source and output hashes.

## 16. Final package and promotion

The public song package is:

```text
songs/final/SongName.mp3
songs/final/SongName.lrc
songs/final/SongName_wordlevel.lrc
songs/final/SongName.json
songs/final/SongName_8D.mp3
```

The Reel is:

```text
reels/generated/SongName_reel.mp4
reels/generated/SongName_reel.json
```

No public package is exposed before validation. Validation checks existence, non-zero size, song/8D duration against Spotify duration, and SHA-256 values stored in the master JSON. Promotion uses the validated staged artifacts.

## 17. Three-database audit

`playlist.db` owns the frozen Spotify playlist snapshot and queue lifecycle. `songs.db` owns song identity, catalog metadata, ISRC, YT Music matches, lyrics, Demucs, alignment, 8D and stage state. `reels.db` owns hooks, visual mapping, Reel audio/video/lyrics and validation. All three use SQLite with full synchronous durability and Windows path values are stringified before SQL binding.

The final empty databases were checked with `PRAGMA integrity_check` and have user version 400.

## 18. Cache/resume audit

Stage fingerprints are dependency-aware. A source change invalidates downstream source-dependent stages; a lyrics change invalidates alignment/8D/Reel; an 8D parameter change invalidates 8D/Reel; a hook change invalidates only Reel-dependent outputs; YouTube visual/offset changes do not invalidate Spotify metadata or Demucs.

A crash between stages leaves retryable state and stage records rather than forcing unnecessary upstream recomputation.

## 19. Historical runtime defects carried forward

The following observed defects were explicitly reviewed and fixed or isolated in the final build:

1. `playlist_snapshot()` missing `log` argument.
2. Heavy Phase 2/3 initialization occurring before the first visible status output.
3. Passing a plain configuration dictionary to `acquire()` instead of the full `Config`.
4. `WindowsPath` objects being bound directly into SQLite after a successful acquisition.
5. Old YT Music playlist parser/authentication failures are removed from active source because Spotify now owns playlist ingestion.
6. Retired view/play priority lookup and ranking logic removed from active runtime.
7. First-run playlist snapshot commits before downstream DB initialization.
8. Unknown-duration YT Music candidates rejected instead of guessed.
9. ISRC comparison normalizes separators/case before lookup.
10. Windows FFmpeg temporary WAV handling retained.
11. Demucs cache identity/run IDs stabilized.
12. Historical Phase 3 test-root/import issue is preserved only in archive and tested from its original project root.
13. YT Music Premium-only errors are handled as explicit unavailable-playback conditions rather than bypass attempts.

## 20. Complete test matrix

Active unified tests: **22 passed**.

Archived Phase 1 suite: **31 passed**.

Archived Phase 2 suite: **39 passed, 1 skipped**.

Archived Phase 3 suite: **19 passed** from its original project root.

Python compilation: **passed**.

Static active-runtime scan: no obsolete playlist priority/count columns or modules; no legacy downloader import/subprocess/requirement.

## 21. Current external API audit

Spotify:

- `GET /playlists/{id}/items` — current playlist item endpoint; max 50 per request; user OAuth for private/collaborative playlist access. citeturn565606search0
- `GET /playlists/{id}` — playlist metadata. citeturn565606search4
- Current February 2026 migration removed old `/playlists/{id}/tracks` and bulk `/tracks`, `/albums`, `/artists` endpoints; individual endpoints are now the supported replacements. citeturn565606search6

YT Music / ytmusicapi:

- `search(query, filter='songs'|'videos', ...)` for media discovery. citeturn840619search2
- `get_song(videoId, signatureTimestamp=...)` returns metadata and streaming information. citeturn840619search0

## 22. Security and secrets

No Spotify access token, refresh token, YT Music browser auth JSON, or other private credential is included in the final release package. Setup scripts create local credentials on the operator machine.

## 23. Final conclusion

The final active implementation matches the revised architecture: Spotify is the only playlist source and catalog authority; the first run is a one-time frozen playlist scan; playlist order is immutable; no popularity ranking exists; Spotify audio is never downloaded; ISRC is the finalized-song duplicate identity; YT Music is used for authoritative audio search/media acquisition through `ytmusicapi`, while visual-video discovery uses the explicit Phase-1 YouTube search rule; and the original Telugu alignment, whole-song 8D, hook, visual synchronization, Reel, cache, recovery and validation systems remain integrated downstream.

This audit does not claim a real external production run from the build environment. The final target machine must run `python main.py --doctor` and then perform the online Spotify/YT Music/LRCLIB/model/media operations with the operator's own credentials and hardware.
