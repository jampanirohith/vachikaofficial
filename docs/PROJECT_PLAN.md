# UNIFIED SPOTIFY PLAYLIST -> YT MUSIC -> TELUGU WORD-LEVEL LRC -> WHOLE-SONG 8D -> REEL

## Final Ultra-Detailed Engineering Specification — Version 1.4.7

**Status:** Active authoritative implementation plan

**Operating system:** Windows-first

**Processing mode:** Sequential; exactly one actionable playlist song is processed at a time

**Playlist authority:** Spotify Web API

**Media selection authority:** YouTube Music via `ytmusicapi` search; **audio download authority:** yt-dlp against the selected YT Music URL

**Spotify audio:** Never downloaded

**yt-dlp:** Required only for the actual audio download of the URL selected by `ytmusicapi`; never used for search or ranking

**Lyrics authority:** LRCLIB `/api/get` for synchronized source LRC

**Alignment:** Telugu MMS + frame-level emissions + CTC reference forced alignment + canonical millisecond word timeline

**Audio separation:** Demucs; one full-song separation shared by alignment and whole-song 8D

**Spatial audio:** Full-song 0:00 through source end; final Reel only consumes its hook crop

**Visual source:** YouTube Music search result selected by title + album + duration compatibility; direct YT Music media stream retrieval

**YouTube sync:** One global offset found from the first 30 seconds of the original song against the selected visual video's full audio timeline

**Hook input:** Terminal, after word-level LRC and full-song 8D are ready

**Final song package:** MP3 + line LRC + word-level LRC + JSON + full-song 8D MP3

**Final Reel package:** MP4 + JSON under `reels/generated/`

> This document is the active authority for the executable project. `PROJECT_PLAN_ORIGINAL.md` and the complete Phase 1/2/3 source archives are retained as historical source material. They are not runtime authority when they conflict with this document.

---

# 1. FINAL CONTRACT IN ONE PAGE

The project has one source queue: a Spotify playlist.

On the first `python main.py` run for a playlist ID not yet present in `playlist.db`, the program performs one and only one playlist job:

```text
Spotify Web API
    |
    +--> get playlist metadata
    |
    +--> GET /playlists/{playlist_id}/items with pagination
    |
    +--> normalize the already-returned items in memory
    |
    +--> atomically record every playlist position in playlist.db
    |
    +--> commit snapshot
    |
    +--> print snapshot complete
    |
    +--> EXIT
```

The first run does **not**:

- download a song;
- call Spotify track/album/artist detail endpoints per song;
- download Spotify audio;
- download Spotify artwork;
- search YouTube Music;
- download YouTube Music media;
- run yt-dlp;
- fetch LRCLIB lyrics;
- run VAD;
- run Demucs;
- run MMS;
- run CTC alignment;
- generate word-level LRC;
- generate 8D audio;
- prompt for a hook;
- search/download a visual video;
- calculate a YouTube offset;
- render a Reel.

The first-run scan is a queue snapshot only.

On later runs, the application reads the already-committed Spotify snapshot and processes entries strictly by ascending `playlist_position`.

For each actionable track, the processing path is:

```text
Frozen Spotify playlist entry
        |
        v
Spotify API: full track + album + artists + artwork + ISRC
        |
        v
ISRC duplicate gate
        |
        +--> duplicate finalized song -> operator decision
        |
        v
YT Music search: "Spotify title + Spotify album"
        |
        v
FIRST result whose duration is within tolerance
        |
        v
YT Music search(title + album) -> duration match -> selected music.youtube.com URL -> yt-dlp download
        |
        v
HTTP stream download + FFmpeg -> master.mp3
        |
        v
LRCLIB /api/get -> synchronized LRC
        |
        +--> no synchronized LRC -> terminal SKIPPED_NO_SYNCED_LRC
        |
        v
One shared Demucs separation
        |
        +--> full-song stems
        |
        +--> shared by alignment and 8D
        |
        v
Telugu MMS / CTC alignment
        |
        v
Canonical word-level timeline + word-level LRC + SYLT
        |
        v
Whole-song advanced 8D
        |
        v
Manual hook input
        |
        v
YT Music visual-video search: title + album
        |
        v
Global 30-second offset matching
        |
        v
Crop hook from already-generated whole-song 8D
        |
        v
Render vertical Reel
        |
        v
Full validation
        |
        v
Atomic final promotion
```

---

# 2. SOURCE-OF-TRUTH MATRIX

| Information | Authoritative source | Runtime behavior |
|---|---|---|
| Playlist ID | `config.json` | Active job identifier |
| Playlist order | Spotify playlist items | Frozen exactly as scanned |
| Playlist position | Spotify item order | Never re-ranked |
| Playlist membership metadata | Spotify playlist items response | Stored in playlist.db |
| Track identity | Spotify track ID | Entry identity within playlist |
| Track title | Spotify full track | Final catalog title |
| Track artist(s) | Spotify full track | Final catalog artist(s) |
| Album | Spotify album/track | Final catalog album |
| Album artist(s) | Spotify album | Final catalog album artist |
| Release date | Spotify album | Final catalog release date |
| Track/disc number | Spotify track | Final catalog track/disc number |
| Album type / total tracks | Spotify album | Final catalog metadata |
| Explicit flag | Spotify track | Final catalog metadata |
| ISRC | Spotify track external IDs | Canonical duplicate identifier |
| Artwork | Spotify album images | Downloaded once as exact original bytes |
| Audio | YouTube Music | Acquired via direct YT Music stream URL + FFmpeg |
| Audio duration used for YTM match | Spotify catalog duration | First YTM search result within tolerance |
| Synchronized LRC | LRCLIB `/api/get` | Gate for Phase 2/8D/Reel |
| Word timing | MMS + CTC alignment | Canonical timeline |
| Full-song stems | Demucs | Shared downstream cache |
| Whole-song 8D | Unified 8D engine | One complete master |
| Visual video | YouTube Music search | Title + album + duration selection |
| YouTube global offset | Local original song vs visual video audio | One global persisted offset |
| Hook | Operator terminal entry | Persisted integer milliseconds |
| Reel audio | Existing whole-song 8D | Exact hook crop |

No component may silently replace an authority with another source.

---

# 3. SPOTIFY PLAYLIST: FIRST RUN

## 3.1 Required first-run behavior

The first normal run is a **snapshot run**, not a processing run.

The sequence is:

1. Load configuration.
2. Open/create `playlist.db` only as needed for snapshot inspection.
3. Read `playlist.spotify_playlist_id`.
4. Check whether that exact playlist ID already exists.
5. If it does not exist, authenticate against Spotify.
6. Fetch playlist metadata.
7. Fetch all playlist items through Spotify Web API pagination.
8. Normalize each already-returned item without any per-track API calls.
9. Store every returned playlist item and its original position.
10. Atomically commit the complete snapshot.
11. Print a clear snapshot completion message.
12. Exit.

The application must not instantiate the expensive Phase 2/3 runtime before this snapshot has been committed.

## 3.2 Spotify endpoint

The implementation uses the current Spotify playlist-items endpoint:

```text
GET /playlists/{playlist_id}/items
```

The endpoint is paginated. The active implementation requests up to 50 items per page and continues until the response `total` is satisfied or the API indicates there is no next page.

The endpoint currently exposes `items`; the historical `tracks` field is deprecated/changed in the 2026 API contract. The implementation accepts `items` and retains a narrow compatibility read for older response fixtures only.

## 3.3 No per-track calls on first run

This is a hard requirement.

The first-run playlist snapshot must not call:

```text
GET /tracks/{track_id}
GET /albums/{album_id}
GET /artists/{artist_id}
```

for every item.

The playlist item response already contains enough information for the queue snapshot: position, track ID, basic title/artist/album, duration, external IDs when present, and raw JSON.

The complete per-song catalog enrichment occurs only when that particular song reaches its processing turn on a later run.

## 3.4 Every entry is persisted

Every returned playlist position receives a durable database row, including entries that cannot later be processed.

Examples:

- normal Spotify track -> `pending`;
- local track -> terminal `skipped` with `LOCAL_TRACK`;
- episode or other unsupported object -> terminal `skipped` with `NON_TRACK_ITEM`;
- missing track object -> terminal `skipped` with `NON_TRACK_ITEM`.

The project never silently deletes an entry from the snapshot.

## 3.5 Source order is immutable

Processing order is simply:

```sql
ORDER BY playlist_position ASC
```

There is no:

```text
view count ranking
play count ranking
popularity sorting
engagement sorting
priority score
processing rank
re-ranking pass

```

The user has already prepared the playlist in the desired order. The application trusts that order.

---

# 4. SPOTIFY PLAYLIST: LATER RUNS

## 4.1 Existing unfinished playlist

If `playlist.db` contains the active playlist ID and it is not complete:

```text
DO NOT call Spotify playlist endpoints again.
```

The program resumes from the database.

## 4.2 Existing completed playlist

If `playlist_done=1`, normal execution prints:

```text
PLAYLIST IS DONE
Playlist <id> is complete. Update playlist.spotify_playlist_id in config.json for a new playlist.
```

Then exits.

## 4.3 No periodic refresh

There is no background synchronization of the playlist.

There is no automatic detection of newly inserted Spotify playlist tracks during normal execution.

To start another prepared playlist, the operator changes `playlist.spotify_playlist_id` in `config.json`.

## 4.4 Incomplete snapshot protection

If a playlist ID exists but the snapshot transaction is incomplete or inconsistent, normal execution must not silently re-fetch it. It must report the damaged state and direct the operator to recovery/migration tooling.

This prevents accidental mixing of two different snapshots under one playlist ID.

---

# 5. SPOTIFY AUTHENTICATION

## 5.1 Preferred authentication

Use a Spotify user-authorized OAuth token stored in:

```text
spotify_auth.json
```

The included setup script performs a PKCE authorization flow using a local callback.

## 5.2 Required scopes

The active setup requests playlist read scopes sufficient for the current private/collaborative playlist-items API:

```text
playlist-read-private
playlist-read-collaborative
```

## 5.3 Token refresh

The client supports:

- access token reuse while valid;
- refresh token exchange;
- rotated refresh token persistence;
- 401 retry after refresh;
- 429 retry honoring `Retry-After` where practical;
- transient 5xx retry with bounded backoff.

## 5.4 Authentication state integrity

The local PKCE callback validates the returned OAuth `state` against the generated state value.

The token file is written atomically and is best-effort restricted to the current user.

## 5.5 Playlist access failure

A Spotify 403 or equivalent permission failure must be shown as an authentication/access error. The application must not switch silently to unrelated public-client behavior for a private playlist.

---

# 6. SPOTIFY PER-SONG FULL METADATA

Once a track reaches its processing turn, Spotify becomes the catalog authority.

## 6.1 Required requests

The per-song metadata stage may request:

1. full track record;
2. full album record;
3. individual artist records for each track artist where artist details are useful.

This is intentionally sequential per song, not a bulk playlist enrichment job.

The current Spotify API removed older bulk track/album/artist endpoints in the 2026 API migration. The implementation therefore performs the supported individual resource calls.

## 6.2 Catalog fields

The master Spotify record preserves, where the API returns them:

- Spotify track ID;
- Spotify URI;
- Spotify track URL;
- track name;
- track artists and artist IDs/URLs;
- album ID;
- album name;
- album URL;
- album artists and IDs;
- album type;
- release date;
- release date precision;
- total album tracks;
- track number;
- disc number;
- explicit flag;
- catalog duration in milliseconds;
- external IDs including ISRC;
- album label;
- album copyrights;
- artist genres;
- complete raw track JSON;
- complete raw album JSON;
- raw artist details.

The application never pretends that an absent Spotify field exists.

## 6.3 Spotify duration is catalog authority

The Spotify catalog duration is the duration used for YT Music search matching.

The actual downloaded MP3 duration is separately measured after conversion.

The actual file duration is authoritative for physical media boundaries later in the pipeline.

## 6.4 Artwork

The largest valid Spotify album image is preferred.

The application downloads the original image bytes exactly as provided by the Spotify CDN.

No crop, resample, recompression, square normalization, or logo overlay is performed on the source artwork.

Artwork metadata records:

- source URL;
- width;
- height;
- MIME type from the HTTP response;
- local path;
- SHA-256.

The Spotify artwork bytes are then embedded into the final MP3 as the front-cover APIC frame.

---

# 7. SPOTIFY ISRC AND DUPLICATE IDENTITY

ISRC is the canonical duplicate identifier.

## 7.1 Normalization

Valid ISRCs are normalized to uppercase compact form with separators removed.

Example:

```text
us-xyz-12-34567
```

becomes:

```text
USXYZ1234567
```

No invented or inferred ISRC is allowed.

## 7.2 Missing ISRC

If Spotify does not provide a valid ISRC:

- no duplicate lookup is performed;
- processing continues normally;
- the missing value is preserved as `NULL`.

The lack of ISRC is not an automatic error.

## 7.3 Duplicate search scope

The duplicate search is performed only against:

```sql
songs.canonical_isrc
WHERE pipeline_status = 'FINALIZED'
```

This intentionally ignores failed, skipped, processing, and incomplete songs.

## 7.4 Duplicate prompt

When a finalized duplicate ISRC exists, prompt:

```text
DUPLICATE ISRC DETECTED
ISRC: <value>
Current: <title> - <artist>
Previous: <title> - <artist>
1 = keep previous / 2 = keep current
>
```

## 7.5 Keep previous

If the operator chooses `1`:

- record the duplicate decision in `isrc_duplicates`;
- mark the current playlist entry terminal `duplicate`;
- mark the current song row `DUPLICATE_KEEP_PREVIOUS`;
- do not download YT Music audio;
- do not run lyrics, Demucs, MMS, 8D, hook, or Reel;
- continue to the next playlist position.

## 7.6 Keep current

If the operator chooses `2`:

- allow the current song to complete normally;
- perform full final validation first;
- atomically promote the current package;
- only after successful promotion, mark the old finalized song as:

```text
REPLACED_BY:<current_song_key>
```

- remove old final package files only after the new package exists and validates;
- preserve the replacement decision in audit history.

## 7.7 Why duplicate checking occurs before YT Music acquisition

This prevents unnecessary network/media work when the operator already has a finalized copy of the same recording.

The YT Music acquisition stage therefore begins only after the duplicate gate has been resolved.

---

# 8. YT MUSIC AUDIO SEARCH — YTMUSICAPI ONLY

## 8.1 Purpose

YT Music is the media source for the actual local MP3.

Spotify audio is never downloaded.

The active runtime uses `ytmusicapi` for search and selection only. yt-dlp is not given search terms.

## 8.2 Search query

Construct exactly:

```text
<Spotify track title> <Spotify album name>
```

If the Spotify album name is unavailable, use the track title alone.

Do not append artist name unless a future plan revision explicitly changes the authority rule.

## 8.3 Search filter

Use:

```python
YTMusic.search(query, filter="songs", limit=N)
```

The current YT Music API exposes a `songs` filter.

## 8.4 Selection rule

Search results are examined in returned order.

The selected result is the **first** result whose duration satisfies:

```text
abs(YTM_duration_ms - Spotify_duration_ms) <= duration_tolerance_ms
```

There is no scoring model for the YT Music audio selection.

There is no popularity ranking.

There is no manual source override in the normal pipeline.

## 8.5 Duration parsing

Accept YT Music duration forms such as:

```text
278 seconds
04:38
```

Convert everything to integer milliseconds before comparison.

## 8.6 Search failure

If no result matches duration tolerance:

```text
YTM_AUDIO_MATCH_NOT_FOUND
```

The song becomes retryable `error`.

The application must not silently choose a duration-incompatible source.

## 8.7 Search provenance

The stage records:

- complete query string;
- search limit;
- number of results returned;
- selected result index;
- selected YT Music video ID;
- selected title;
- selected artists;
- selected album field;
- selected candidate duration;
- Spotify duration;
- duration delta;
- configured tolerance;
- raw search result JSON for candidates considered.

---

# 9. YT MUSIC AUDIO DOWNLOAD VIA YT-DLP

## 9.1 Boundary and authority

The audio path is intentionally split into two responsibilities:

1. `ytmusicapi.search()` is the only discovery/selection mechanism.
2. yt-dlp is the only actual audio-download mechanism for the selected result.

The active runtime never gives yt-dlp a search query. It gives yt-dlp only the canonical URL of the already-selected YT Music result:

```text
https://music.youtube.com/watch?v=<selected_video_id>
```

The Spotify catalog duration remains the matching authority.

## 9.2 No second candidate search

After the first duration-compatible YT Music search result is selected, acquisition must not ask yt-dlp to search by title, artist, album, or any other text. This prevents the downloader from silently selecting a different recording than the one selected by ytmusicapi.

## 9.3 yt-dlp configuration

The default active options are equivalent to:

```text
format = bestaudio/best
noplaylist = true
retries = 3
fragment_retries = 3
```

The downloader uses the Python `yt_dlp.YoutubeDL` API rather than shell parsing. FFmpeg is configured as the post-processing engine.

## 9.4 High-quality audio target

The final pipeline artifact remains MP3 because the downstream unified package requires MP3. The default output target is:

```text
codec: libmp3lame
bitrate: 320 kbps
```

The source is first selected with `bestaudio/best`. If the source itself is below 320 kbps, the conversion does not claim that extra information was created; actual source bitrate/sample-rate/codec provenance is recorded.

## 9.5 Atomic temporary output

The downloader writes to a unique temporary stem under the acquisition work directory. The final `master.mp3` is replaced only after yt-dlp and FFmpeg complete successfully. Partial files are deleted on failure.

## 9.6 Authentication options

The project supports the user's own authenticated environment through optional configuration:

```json
"cookies_from_browser": null,
"cookie_file": null
```

When configured, these settings are passed to yt-dlp. They are intended for normal authorized playback in the user's own account/session. No login bypass, CAPTCHA bypass, Premium bypass, DRM circumvention, or other access-control circumvention is implemented.

## 9.7 Provenance

`master.info.json` records at least:

- selected YT Music video ID and canonical URL;
- ytmusicapi search query and selected result index;
- Spotify duration, selected duration, and final downloaded duration;
- yt-dlp extractor and version;
- source format ID/container/codec;
- source bitrate and sample rate when supplied;
- final output codec and configured bitrate;
- the fact that the source-selection authority was ytmusicapi search.

Signed media URLs and cookies are not persisted as project provenance.

## 9.8 Duration validation

After yt-dlp/FFmpeg output, the final MP3 duration is measured locally. If the difference from Spotify's catalog duration exceeds `ytmusic.duration_tolerance_ms`, the new MP3 is deleted and the acquisition stage fails explicitly:

```text
YTDLP_DURATION_MISMATCH
```

No downstream lyrics, Demucs, alignment, 8D, hook, or Reel work starts until the acquired source passes this gate.

# 10. YT MUSIC VISUAL VIDEO SOURCE

The final Reel still needs a visual source.

This stage is independent of the YT Music audio-source selection.

## 10.1 Query

Use:

```text
<Spotify title> <Spotify album>
```

## 10.2 Search type

Use:

```python
YTMusic.search(query, filter="videos", limit=N)
```

## 10.3 Selection

Use the returned search order.

The first non-lyrics result whose duration matches the Spotify song duration within tolerance is selected.

Titles containing the configured lyrics keyword are skipped.

There is no YouTube view-count ranking and no source scoring.

## 10.4 Media retrieval

The selected YT Music video is downloaded with the same direct `ytmusicapi` stream layer.

No yt-dlp call exists in the visual-video direct-stream helper; that helper remains ytmusicapi/FFmpeg based because the current requested yt-dlp migration is for audio acquisition.

Progressive video is preferred when directly available; otherwise separate direct video/audio streams are fetched and combined with FFmpeg.

## 10.5 Visual-source failure

If no acceptable video result exists:

```text
YTM_VISUAL_VIDEO_UNAVAILABLE
```

The song remains retryable.

---

# 11. 30-SECOND GLOBAL YOUTUBE OFFSET

This stage retains the previously locked synchronization method, but the input media source is now the selected YT Music visual video.

## 11.1 Reference

Use the first 30,000 ms of the original locally acquired song.

Never use the 8D file.

Never use the hook.

Never use the word-level LRC as the acoustic reference.

## 11.2 Scan domain

Scan the entire selected visual video's audio timeline.

## 11.3 Signals

The matcher combines structural signals including:

- normalized waveform shape;
- RMS / energy envelope;
- high/low transitions;
- peaks and valleys;
- low-rate transition structure.

## 11.4 Candidate scoring

Each candidate receives component scores and a combined score.

Candidates are persisted for audit and debugging.

## 11.5 Hard offset boundary

Only candidate offsets in:

```text
0 <= offset_ms <= 30000
```

are acceptable by default.

If the highest-scoring global candidate lies outside 30 seconds, reject it and examine the next highest scoring valid candidate.

If no valid candidate remains, fail the YouTube-sync stage explicitly.

## 11.6 One global offset

Only one global offset is stored.

For a hook later:

```text
youtube_start_ms = hook_start_ms + offset_ms
youtube_end_ms   = hook_end_ms   + offset_ms
```

No per-hook re-matching.

No DTW.

No second offset search after hook entry.

## 11.7 Persisted offset

Store:

- selected visual video ID;
- visual URL;
- selection query/rule;
- reference duration = 30,000 ms;
- maximum accepted offset = 30,000 ms;
- selected offset;
- total score;
- waveform score;
- energy score;
- peak/valley score;
- transition score;
- rejected candidates;
- method version.

Store this in:

- songs.db;
- master JSON;
- final MP3 custom metadata.

---

# 12. LRCLIB SYNCHRONIZED LYRIC GATE

## 12.1 Provider

Use LRCLIB `/api/get`.

## 12.2 Query

Use the Spotify-authoritative:

- title;
- artist;
- album;
- duration.

## 12.3 Synchronized-only rule

The pipeline accepts the synchronized lyric payload only.

If no synchronized lyrics are returned:

```text
SKIPPED_NO_SYNCED_LRC
```

is terminal.

## 12.4 No downstream processing after skip

After this state, do not:

- run Demucs;
- run MMS;
- generate word-level LRC;
- render 8D;
- ask for hook;
- search visual video again;
- calculate new offset;
- render Reel.

## 12.5 Skip outputs

The skipped song keeps its local YT Music MP3 plus a JSON provenance record under:

```text
songs/skipped/no_synced_lrc/
```

The MP3 receives the Spotify metadata/artwork available at that point and the Spotify ISRC.

---

# 13. AUDIO DECODE AND SOURCE IDENTITY

## 13.1 One source decode

The original YT Music MP3 is decoded once into the reusable working representation required by Phase 2/3.

## 13.2 Hash identity

Compute source MP3 SHA-256 immediately after acquisition.

Derived preprocessing is fingerprinted from the source hash plus relevant settings.

## 13.3 8 kHz reference audio

The offset matcher may generate a low-rate analysis/reference WAV from the original source.

This is an analysis artifact, not a final output.

## 13.4 Full-resolution source

The full source decode used by Demucs and 8D is retained under the per-song temporary directory.

The application does not repeatedly decode the same MP3 for each stage.

---

# 14. DEMUCS SHARED SEPARATION

## 14.1 One separation

Run Demucs exactly once per unique source MP3/model fingerprint.

The full song is separated.

Expected stems:

```text
vocals.wav
drums.wav
bass.wav
other.wav
```

## 14.2 Shared consumers

The same stem set is used by:

- Phase 2 alignment/acoustic analysis;
- whole-song 8D processing.

No second Demucs pass is permitted for the 8D stage.

## 14.3 Cache key

Demucs reuse is based on:

- source MP3 SHA-256;
- Demucs model;
- relevant runtime/model revision;
- expected duration.

## 14.4 Device

The project is CUDA-first.

If `require_cuda=true` and CPU fallback is disabled, a missing CUDA device is an explicit environment error.

No silent CPU substitution occurs when the configuration forbids it.

## 14.5 Windows safety

The historical FFmpeg/Demucs issue involving `.wav.tmp` filenames is avoided by generating explicit safe temporary WAV filenames with supported extensions.

The cleanup layer never treats a temporary filename as the main media file unless it is explicitly promoted.

---

# 15. TELUGU WORD-LEVEL ALIGNMENT

The Phase 2 architecture remains active after source replacement.

## 15.1 Language

Primary language:

```text
ISO-639-1: te
ISO-639-3: tel
```

## 15.2 Preprocessing

The alignment layer continues to use:

- Telugu reversible normalization;
- activity/VAD analysis;
- lyric line parsing;
- chunk creation;
- context windows;
- MMS language adapter;
- frame-level model emissions.

## 15.3 Alignment method

Use true CTC reference alignment rather than a simple word-duration heuristic.

The pipeline retains frame-to-time mapping before converting to integer milliseconds.

## 15.4 Canonical timeline

The canonical timeline is the single source for:

- aligned lyric lines;
- word start/end boundaries;
- word-level LRC;
- Reel lyric highlighting;
- 8D lyric-aware automation.

## 15.5 Integer milliseconds

All public timing boundaries are integer milliseconds.

Float seconds may exist internally only where a model/library requires them.

Before persistence/export:

```text
round/quantize -> integer ms
```

## 15.6 Overlap deduplication

If VAD/chunk overlap produces duplicate word hypotheses, retain the best chronological instance according to the Phase 2 overlap-dedup rules.

## 15.7 Chronology repair

The alignment implementation retains the previously required chronology safeguards:

- global ordering by time;
- no negative durations;
- no invalid cross-line ordering;
- blank marker handling;
- final timing repair where bounded by configuration.

---

# 16. WORD-LEVEL LRC EXPORT

The final word-level LRC is generated from the canonical timeline, not reconstructed by parsing rounded export timestamps.

## 16.1 Format

Each word receives a timestamp derived from canonical `start_ms`.

The exact export formatting remains deterministic.

## 16.2 Source text

The lyric wording remains the LRCLIB source wording normalized only as required by Phase 2 for alignment.

The source lyric itself is not rewritten into unrelated text.

## 16.3 SYLT

The final MP3 contains a dedicated word-level `SYLT` frame.

Language:

```text
TEL
```

The frame is identified with the deterministic description:

```text
UnifiedWordLevel
```

Existing unrelated tags are preserved.

---

# 17. WHOLE-SONG ADVANCED 8D

## 17.1 Full-song requirement

The 8D stage processes the entire original song:

```text
0:00 -> exact source end
```

It never renders only the hook.

## 17.2 Input sources

8D may use:

- original full-resolution source audio;
- vocals stem;
- drums stem;
- bass stem;
- other stem;
- canonical word timeline;
- activity/energy information;
- audio duration;
- configured spatial parameters.

## 17.3 Intended spatial behavior

The approved base treatment is:

- vocals stable/near-center;
- bass centered and mono-compatible;
- drums controlled spatial movement;
- other/main instrumental material carrying wider spatial motion;
- smooth periodic motion;
- gradual width changes;
- no pitch changes;
- no speed changes.

## 17.4 Lyric-aware automation

The 8D engine may use the canonical word timeline to shape spatial emphasis in a music/lyric-aware manner.

The word timeline does not become the audio timing authority; the source audio duration remains authoritative.

## 17.5 Output

Save:

```text
SongName_8D.mp3
```

with the exact full source duration within configured validation tolerance.

## 17.6 No second pass after hook input

Once this master exists, hook changes do not rerun 8D.

A hook change invalidates only Reel-dependent work.

---

# 18. MANUAL HOOK ENTRY

## 18.1 Timing

Prompt only after:

1. synchronized LRC exists;
2. word-level alignment is complete;
3. whole-song 8D is complete.

## 18.2 Prompt

```text
Hook start (MM:SS.xxx):
>
Hook end (MM:SS.xxx):
>
```

## 18.3 Parsing

Accept:

```text
MM:SS
MM:SS.xxx
```

Normalize to integer milliseconds.

## 18.4 Validation

Require:

```text
0 <= start_ms < end_ms <= original_duration_ms
```

There is no maximum hook duration cap.

## 18.5 Persistence

Persist hook immediately after successful validation in:

- reels.db;
- per-song state JSON.

A crash after this persistence does not require another hook prompt.

## 18.6 Crop source

The Reel audio comes from the already-finished whole-song 8D MP3.

No new 8D processing occurs.

---

# 19. REEL YOUTUBE MAPPING

Once the hook is persisted:

```text
mapped_start = hook_start + global_youtube_offset
mapped_end   = hook_end   + global_youtube_offset
```

The guard window remains configured independently.

The visual-source stage downloads enough material for the guarded interval using direct YT Music streams.

The Reel trim stage then cuts exactly the requested hook duration.

---

# 20. REEL GENERATION

## 20.1 Canvas

Default output:

```text
1080 x 1920
```

## 20.2 Audio

Audio source:

```text
whole-song 8D MP3 -> exact hook crop
```

No original non-8D song audio is used in the final Reel.

## 20.3 Lyrics

The canonical word-level lyric timeline drives the highlighted Telugu word rendering.

Existing Phase 3 visual behavior remains active, including:

- one lyric line at a time;
- word progression/highlight;
- configured Telugu font;
- center placement;
- configured outline/shadow;
- safe area handling;
- deterministic line selection.

## 20.4 Encoding

Prefer:

```text
h264_nvenc
```

when available and configured.

Use the configured software encoder fallback when NVENC cannot be used.

## 20.5 Validation

Validate:

- file exists;
- non-zero size;
- duration matches hook target within tolerance;
- 1080x1920 resolution;
- 9:16 aspect ratio;
- H.264-compatible video codec;
- audio exists;
- stereo audio;
- no unintended clipping/empty output according to Phase 3 checks.

---

# 21. FINAL SONG PACKAGE

The successful song package is exactly:

```text
songs/final/
    SongName.mp3
    SongName.lrc
    SongName_wordlevel.lrc
    SongName.json
    SongName_8D.mp3
```

The final Reel is separate:

```text
reels/generated/
    SongName_reel.mp4
    SongName_reel.json
```

## 21.1 No partial final publication

The final package remains under `temp/<song_key>/final/` until all validation gates succeed.

Only then are files promoted into public final directories.

## 21.2 Atomic promotion

Each final source file is prepared before replacement.

A partially-created file must never cause the song to appear `FINALIZED`.

The database is updated to `FINALIZED` only after the public files and Reel outputs exist and validate.

---

# 22. MASTER JSON

`SongName.json` is cumulative and provenance-rich.

It retains the original YT Music acquisition information and includes controlled unified namespaces.

## 22.1 Main namespaces

```text
playlist
spotify
metadata
yt_music_audio
youtube
youtube_offset
lyrics
demucs
alignment
audio_8d
hook
reel
duplicate_detection
provenance
outputs
hashes
status
```

## 22.2 Spotify namespace

Contains the serialized `SpotifyResult`, artwork provenance, catalog identifiers, raw catalog records, and ISRC.

## 22.3 YT Music namespace

Contains only metadata useful for explaining the selected YT Music source. Signed stream URLs are not persisted.

## 22.4 Provenance flags

The final record explicitly states:

```json
"spotify_audio_downloaded": false,
"yt_dlp_used": true
```

The provenance field now confirms that yt-dlp was used only for the selected YT Music audio URL.

## 22.5 Hashes

Persist SHA-256 hashes for all final artifacts and important intermediate inputs.

---

# 23. MP3 METADATA AUTHORITY

The final MP3 metadata is written with Mutagen.

## 23.1 Spotify-controlled major fields

Use Spotify values for:

- title;
- artist(s);
- album;
- album artist(s);
- release date;
- track number;
- disc number;
- ISRC;
- genre where available;
- composer/publisher only when Spotify actually provides a value and the field is supported by the returned catalog data.

## 23.2 Artwork

Embed Spotify's original album image bytes as APIC front cover.

## 23.3 Preservation

Existing metadata not controlled by the authoritative Spotify overlay must not be indiscriminately deleted.

Existing unrelated frames are preserved where compatible with the Mutagen write operation.

## 23.4 Custom application frames

The final MP3 may include deterministic TXXX metadata such as:

```text
SPOTIFY_TRACK_ID
SPOTIFY_ALBUM_ID
SPOTIFY_ISRC
SPOTIFY_TRACK_URL
SPOTIFY_TRACK_URI
UNIFIED_YTM_AUDIO_VIDEO_ID
UNIFIED_YTM_AUDIO_URL
UNIFIED_YTM_AUDIO_QUERY
UNIFIED_YT_VISUAL_VIDEO_ID
UNIFIED_YT_VISUAL_VIDEO_URL
UNIFIED_YT_OFFSET_MS
UNIFIED_HOOK_START_MS
UNIFIED_HOOK_END_MS
UNIFIED_8D_SOURCE_SHA256
UNIFIED_WORDLEVEL_LRC_SHA256
```

The application never stores a signed, expiring stream URL.

---

# 24. THREE-DATABASE ARCHITECTURE

## 24.1 playlist.db

Owns:

- Spotify playlist snapshot;
- source order;
- playlist entry status;
- one-time scan lifecycle;
- playlist-level audit trail.

## 24.2 songs.db

Owns:

- per-song identity;
- Spotify catalog metadata;
- ISRC identity;
- YT Music audio source selection;
- visual video and offset;
- lyrics provenance;
- Demucs runs;
- alignment runs;
- lyric words;
- whole-song 8D;
- stage fingerprints;
- source artifacts;
- song audit trail.

## 24.3 reels.db

Owns:

- Reel identity;
- hook entry;
- YT visual mapping;
- 8D crop;
- visual trim;
- lyric rendering configuration;
- validation;
- Reel stage runs;
- Reel audit trail.

---

# 25. SONG IDENTITY

The active song key is:

```text
<playlist_serial>_<spotify_track_id>
```

Example:

```text
001_3n3Ppam7vgaVa1iaRUc9Lp
```

Why playlist serial remains in the key:

- the same Spotify track can legitimately appear at multiple playlist positions;
- each playlist occurrence can receive its own duplicate decision and processing status;
- ISRC comparison still collapses exact recording identity when a finalized duplicate exists.

No YT Music video ID is required for song identity.

---

# 26. STATE MACHINE

## 26.1 Playlist entry states

```text
pending
processing
completed
skipped
duplicate
error
needs_review
```

Terminal states are:

```text
completed
skipped
duplicate
```

## 26.2 Song states

Important terminal/major states include:

```text
pending
SPOTIFY_METADATA_READY
SOURCE_READY
SKIPPED_NO_SYNCED_LRC
DUPLICATE_KEEP_PREVIOUS
AUDIO_8D_READY
FINALIZED
REPLACED_BY:<song_key>
error
```

## 26.3 Quality vs processing state

Do not overload the state machine with quality semantics.

Example:

```text
pipeline_status = FINALIZED
quality_status = passed
```

or:

```text
pipeline_status = FINALIZED
quality_status = review
```

depending on the actual validation contract.

---

# 27. STAGE FINGERPRINTS AND CACHE INVALIDATION

## 27.1 Spotify metadata change

Invalidates:

- Spotify metadata stage;
- YT Music audio search/download;
- visual video search/download;
- offset;
- lyrics;
- Demucs;
- alignment;
- 8D;
- hook/reel as appropriate.

## 27.2 YT Music audio source change

Invalidates:

- source decode;
- lyrics duration context;
- Demucs;
- alignment;
- 8D;
- Reel.

## 27.3 Lyrics change

Invalidates:

- alignment;
- word-level LRC;
- 8D when its automation depends on words;
- Reel.

## 27.4 Demucs model/config change

Invalidates:

- Demucs;
- alignment;
- 8D;
- Reel.

## 27.5 8D parameter change

Invalidates only:

- whole-song 8D;
- Reel.

No second audio acquisition is required.

## 27.6 Hook change

Invalidates only:

- hook-dependent Reel crop/trim/render;
- Reel JSON and validation.

The following must remain reusable:

- Spotify metadata;
- Spotify artwork;
- YT Music audio;
- lyric LRC;
- Demucs;
- alignment;
- word-level LRC;
- whole-song 8D;
- visual selection;
- global YouTube offset.

---

# 28. RESUME AFTER CRASH

Every expensive stage has a persistent stage row and a per-song state JSON.

Resume checks:

1. stage fingerprint matches;
2. expected artifact exists;
3. artifact is non-empty;
4. stored hash matches when available;
5. downstream dependencies are still compatible.

Only then can a stage be reused.

A row marked `completed` with a missing artifact is not accepted as valid cache.

---

# 29. RETRYABLE ERROR POLICY

Network/transient/media failures are retryable unless explicitly terminal.

Examples:

```text
SPOTIFY_API_429
SPOTIFY_API_5XX
YTM_AUDIO_SEARCH_FAILED
YTM_GET_SONG_FAILED
YTM_STREAM_HTTP_FAILED
YTM_AUDIO_DOWNLOAD_FAILED
YTM_DURATION_MISMATCH
YTM_VISUAL_VIDEO_UNAVAILABLE
YTM_REFERENCE_AUDIO_FAILED
DEMUCS_ERROR
ALIGNMENT_ERROR
REEL_RENDER_ERROR
```

The queue stays `error` with `terminal=0`.

A future run may use:

```text
python main.py --retry-errors
```

to move retryable entries back to `pending`.

---

# 30. TERMINAL ERROR POLICY

The following are terminal by design:

```text
SKIPPED_NO_SYNCED_LRC
DUPLICATE_KEEP_PREVIOUS
NON_TRACK_ITEM
LOCAL_TRACK
```

The operator may explicitly reset skipped data with the supported administrative command where appropriate.

---

# 31. HISTORICAL RUN ERROR FIXES CARRIED FORWARD

The final source tree incorporates the following concrete failure fixes discovered from previous runs.

## 31.1 Missing playlist_snapshot logger argument

Historical failure:

```text
TypeError: playlist_snapshot() missing 1 required positional argument: 'log'
```

The current `main.py` passes the logger consistently.

## 31.2 Premature heavy initialization

Historical symptom: no visible terminal progress before the first playlist operation.

Current behavior prints startup immediately and does not instantiate the expensive Pipeline until a committed playlist snapshot already exists.

## 31.3 YT Music playlist parser failure

Historical failure:

```text
KeyError: 'contents'
```

That occurred in the old YT Music playlist ingestion architecture.

The current design eliminates YT Music as the playlist authority completely. Spotify owns playlist discovery, so this parser path is no longer part of the active runtime.

## 31.4 Dict vs Config acquisition bug

Historical failure:

```text
AttributeError: 'dict' object has no attribute 'data'
```

The active acquisition contract accepts the `Config` object consistently.

## 31.5 WindowsPath SQLite binding bug

Historical failure:

```text
sqlite3.ProgrammingError:
Error binding parameter ... type 'WindowsPath' is not supported
```

All SQLite-bound path values are stringified before binding.

## 31.6 FFmpeg temporary WAV extension issue

Temporary audio files use valid explicit media extensions rather than names ending in `.wav.tmp` where FFmpeg interprets the wrong output format.

## 31.7 Phase 3 nested pytest import issue

The package keeps its active test root and archived phase roots separated so pytest can locate the intended `src` package from each root.

## 31.8 YT Music Premium-only media

A prior release recorded a media failure stating:

```text
This video is only available to Music Premium members
```

The current release does not attempt to bypass that restriction. The selected YT Music URL is downloaded with yt-dlp using the user's normal authorized environment. Optional browser-cookie or cookie-file configuration is supported so an authenticated session may be supplied when legitimately required.

No access-control bypass is implemented.


# 32. IMPORTANT FIRST-RUN PERFORMANCE DESIGN

The first run should be comparatively light.

The first run is intentionally only the Spotify playlist snapshot.

For a playlist with 1,885 entries, the first run may involve multiple Spotify playlist-item pages, but it must not process 1,885 songs.

The expensive work starts on the second run.

This prevents the previous architecture's long priority lookup/enrichment phase entirely.

---

# 33. WHY THE FIRST RUN IS NOT A SONG-PROCESSING RUN

The user explicitly prepares the playlist order before the pipeline begins.

Therefore the first scan creates a stable job definition.

This has several benefits:

- the playlist cannot change underneath the processing run;
- no continuous playlist polling occurs;
- restarts are deterministic;
- processing order is stable;
- every skipped/unusable playlist entry has an auditable record;
- the expensive processing stages begin only when the operator intentionally launches the next run.

---

# 34. NO VIEW/PLAY/POPULARITY LOGIC

The active project contains no playlist ranking mechanism.

No playlist code may:

- call view-count endpoints;
- call play-count endpoints;
- compute a popularity metric;
- compute engagement;
- sort by views;
- sort by plays;
- assign a processing rank.

The only ordering field is:

```text
playlist_position
```

This is stored from the Spotify API response order.

---

# 35. NO SPOTIFY AUDIO

The Spotify API is used for metadata, artwork, playlist membership, and ISRC.

Spotify audio bytes are never requested by this project.

The local audio file is always obtained from YT Music.

This keeps the data-source boundary explicit.

---

# 36. HISTORICAL: DIRECT-STREAM-ONLY RELEASE

The active runtime must have:

```text
no standalone yt-dlp executable requirement (Python module API is used)
yt-dlp Python import confined to `ytm_media.py` audio download
no yt-dlp search/subprocess path
yt-dlp requirements entry present
optional yt-dlp browser/cookie authentication
yt-dlp provenance metadata model
```

The only active discovery/selection library for YouTube Music audio is `ytmusicapi`; actual audio download is performed by yt-dlp on the selected YT Music URL.

yt-dlp retrieves the selected source and FFmpeg performs final MP3 extraction/post-processing.

---

# 37. YT MUSIC AUTHENTICATION

The project expects:

```text
browser.json
```

or another ytmusicapi-compatible auth file configured as:

```json
"ytmusic": {
  "auth_file": "browser.json",
  "require_auth": true
}
```

This authentication is used only for YT Music operations:

- search;
- `get_song()`;
- direct media stream retrieval.

It is not involved in Spotify playlist scanning.

---

# 38. YT MUSIC DIRECT-STREAM CAVEATS

The audio pipeline does not depend on ytmusicapi direct stream URLs. ytmusicapi search provides the selected track/video identity and duration; yt-dlp performs the actual download from the selected YT Music URL.

The project therefore uses that API contract directly.

Because YT Music stream URLs can be expiring/signed:

- signed URLs are used transiently only;
- they are not stored as durable source metadata;
- failed audio downloads can retry yt-dlp on the same selected YT Music URL; candidate selection is not repeated unless the search stage itself is invalidated;
- if the API returns no direct URL, fail clearly rather than inventing an extractor.

---

# 39. FILESYSTEM LAYOUT

```text
project/
├── main.py
├── config.json
├── requirements.txt
├── requirements-dev.txt
├── README.md
├── BUILD_INFO.md
├── CHANGELOG.md
├── PROJECT_PLAN.md
├── PROJECT_PLAN_ORIGINAL.md
├── BUILD_MANIFEST.json
├── db/
│   ├── playlist.db
│   ├── songs.db
│   └── reels.db
├── database/
│   ├── playlist_schema.sql
│   ├── songs_schema.sql
│   └── reels_schema.sql
├── songs/
│   ├── final/
│   └── skipped/no_synced_lrc/
├── reels/
│   └── generated/
├── temp/<song_key>/
├── models/
├── assets/fonts/
├── logs/
├── scripts/
└── src/
```

Historical source implementations are under:

```text
archives/phase1/
archives/phase2/
archives/phase3/
```

Those archives are not imported by the active runtime.

---

# 40. PER-SONG TEMP DIRECTORY

Recommended structure:

```text
TEMP/<serial>_<spotify_track_id>/
├── state.json
├── metadata/
│   ├── spotify_full.json
│   └── spotify_artwork.*
├── acquisition/
│   ├── master.mp3
│   └── master.info.json
├── youtube/
│   ├── selected_video.json
│   ├── reference_audio.wav
│   └── reference_audio.info.json
├── lyrics/
│   └── source.lrc
├── audio/
│   ├── source.wav
│   ├── full_source.wav
│   ├── stems/
│   └── 8d/
├── alignment/
│   └── final/
├── final_reel/
└── final/
```

All intermediate work is local to the song.

---

# 41. DATABASE SCHEMAS

## 41.1 playlist_entries core fields

```text
playlist_serial
playlist_id
playlist_position
added_at
added_by_user_id
added_by_user_name
spotify_track_id
spotify_uri
spotify_url
title
artist
album
spotify_duration_ms
spotify_isrc
track_type
is_local
status
terminal
terminal_reason
error_code
error_message
raw_item_json
created_at
updated_at
```

There are no active priority fields.

## 41.2 songs core fields

```text
song_key
playlist_serial
spotify_track_id
canonical_isrc
ytM_video_id
ytM_url
basename
title
artist
album
duration_ms
source_mp3_path
source_lrc_path
source_json_path
final_mp3_path
final_lrc_path
final_wordlevel_lrc_path
final_json_path
final_8d_mp3_path
hashes
pipeline_status
quality_status
terminal
terminal_reason
```

## 41.3 reels core fields

Reels retains:

- hook start/end;
- YouTube video identity and offset;
- whole-song 8D source;
- Reel crop metadata;
- visual source metadata;
- lyric rendering metadata;
- validation metadata;
- stage history.

---

# 42. BACKUP AND RECOVERY

The project includes database backup and restore tooling.

## 42.1 Backup

Use SQLite backup semantics to create consistent copies.

Validate:

```sql
PRAGMA integrity_check;
```

on every backup.

## 42.2 Restore

Validate backup files before replacing active DBs.

Restore to temporary filenames and atomically replace active DB files only after validation.

## 42.3 Reconcile

`--reconcile` checks processing rows against persisted song state and public artifact validity.

It never marks a fake/incomplete final package as completed.

---

# 43. ADMINISTRATIVE COMMANDS

Examples:

```powershell
python main.py
python main.py --doctor
python main.py --status
python main.py --retry-errors
python main.py --reconcile
python main.py --single 12
python main.py --dry-run
python main.py --force-rebuild alignment 12
python main.py --reset-skipped 12
python main.py --review 12
python main.py --backup-db backups
python main.py --restore-db backups\<timestamp>
python main.py --migrate-db
```

The normal path remains:

```text
python main.py
```

---

# 44. FIRST-RUN CLI EXAMPLE

```text
Unified Spotify Playlist -> YT Music Audio -> Word LRC -> Whole-song 8D -> Reel pipeline
Initializing configuration and playlist database...
Spotify API playlist snapshot fetch started: <playlist_id>
Fetching complete Spotify playlist via Spotify Web API: <playlist_id>
Spotify playlist snapshot 1/<total>
Spotify playlist snapshot 50/<total>
...
Spotify playlist snapshot <total>/<total>
Spotify playlist snapshot recorded: <total> entries in exact Spotify playlist order
FIRST SPOTIFY PLAYLIST SNAPSHOT COMPLETE
No song was processed on the first run.
Run python main.py again to process songs one at a time in the exact Spotify playlist order.
```

No song-processing banner appears before the snapshot exit.

---

# 45. SECOND-RUN CLI EXAMPLE

```text
Unified Spotify Playlist -> YT Music Audio -> Word LRC -> Whole-song 8D -> Reel pipeline
Initializing configuration and playlist database...
Using frozen Spotify playlist snapshot <playlist_id>; no playlist re-fetch will occur.

PROCESSING playlist-position=1 | Song | Spotify=<track_id>
Spotify metadata + artwork -> YT Music audio search -> lyrics -> Demucs -> alignment -> 8D -> hook -> Reel
```

This begins one song at a time.

---

# 46. PROGRESS OUTPUT

Progress must identify actual work.

Examples:

```text
SPOTIFY METADATA
Spotify track: <id>
Spotify artwork: downloading
Spotify artwork: complete

YT MUSIC SEARCH
Query: <title> <album>
Results: 10
Duration match: result 1 / 10

YT MUSIC AUDIO
Stream: itag=140
Downloading...
Converting with FFmpeg...
Duration validation: PASS

LYRICS
LRCLIB: synchronized LRC found

DEMUCS
Device: cuda
Stems: vocals/drums/bass/other

ALIGNMENT
MMS: ...
CTC: ...
Word timeline: ... words

WHOLE-SONG 8D
Rendering 0:00 -> 4:02

HOOK
Hook start (MM:SS.xxx):
>
Hook end (MM:SS.xxx):
>

REEL
Visual source: <video_id>
Offset: <ms>
Rendering 1080x1920...

FINAL VALIDATION
PASS
```

Progress must not claim work that was not performed.

---

# 47. LOGGING

Use both console and file logging.

Log entries should include:

- UTC/local timestamp according to configured logger;
- level;
- song serial;
- song key when available;
- stage;
- error code;
- human-readable message.

Stack traces are written for unexpected stage failures.

---

# 48. SECURITY AND SECRET HANDLING

Do not store:

- Spotify client secret in source control;
- OAuth authorization code;
- signed YT Music stream URLs;
- unrelated browser cookies.

Auth files should be ignored by git.

The release package does not contain personal auth material.

---

# 49. NETWORK RETRY POLICY

Spotify:

- bounded retries;
- 401 refresh;
- 429 delay;
- 5xx retry.

YT Music API:

- bounded API retry only where safe;
- yt-dlp retry/fragment-retry for transient audio download failures;
- rerun yt-dlp against the same selected YT Music URL for transient/expired media failures; re-selection is governed by the acquisition-stage fingerprint.

LRCLIB:

- bounded retry;
- request delay;
- no endless retry loop.

A terminal state must remain terminal unless an explicit operator reset occurs.

---

# 50. ARTIFACT CLEANUP

On successful processing:

- final artifacts are promoted;
- intermediate temp files remain only when useful for recovery/debugging, or are cleaned according to retention configuration.

On failure:

- partial direct-stream `.part` files are deleted;
- incomplete final targets are not marked finalized;
- state JSON retains failure information.

A failed media download must not destroy the previously valid cache for another stage.

---

# 51. PATH NORMALIZATION

Windows path handling is strict:

- `Path` objects are used within Python;
- DB writes convert `Path` to `str`;
- JSON serialization converts paths to strings;
- subprocess arguments receive strings;
- relative public paths are stored relative to project root.

This directly prevents the observed SQLite `WindowsPath` binding failure.

---

# 52. JSON ATOMICITY

State and final JSON writes use temporary files followed by atomic replacement.

A crash during JSON writing must not leave a half-written `state.json` that appears valid to the resume layer.

---

# 53. SQLITE SAFETY

Connections configure:

```text
foreign_keys=ON
busy_timeout
synchronous=FULL
```

Generic upserts do not delete parent rows or break foreign-key child records.

Stage identifiers remain stable after a stage is reused.

---

# 54. TEST MATRIX

The final release includes tests for:

## Playlist

- first run is snapshot-only;
- all positions are persisted;
- order is preserved exactly;
- no popularity fields exist;
- second run does not fetch playlist again;
- completed playlist exits without refetch;
- malformed/incomplete snapshot handling.

## Spotify

- playlist pagination;
- `items` response handling;
- no per-track detail calls in first snapshot;
- full track/album/artist parsing;
- artwork download bytes and provenance;
- ISRC normalization;
- token refresh;
- PKCE state validation.

## YT Music

- title+album query;
- first duration-match selection;
- ytmusicapi duration-compatible selection;
- duration mismatch rejection;
- yt-dlp dependency for selected-URL audio download;
- unavailable playback error;
- retained ytmusicapi direct-stream handling for visual video reference acquisition;
- signatureTimestamp integration where available.

## ISRC duplicate

- no ISRC -> no lookup;
- finalized-only duplicate lookup;
- keep previous prevents media acquisition;
- keep current replacement occurs only after successful final validation.

## Phase 2

- canonical integer milliseconds;
- word chronology;
- blank marker safeguards;
- Demucs reuse;
- word-level LRC and SYLT.

## Phase 3

- whole-song 8D duration;
- hook exact crop from 8D;
- visual offset mapping;
- Reel dimensions and duration;
- H.264 validation;
- stereo audio.

## Windows regression

- `Path` objects bind safely to SQLite as strings;
- FFmpeg temporary files have valid extensions;
- package paths remain valid on Windows.

---

# 55. STATIC CONFORMANCE CHECKS

Before release:

1. scan active runtime for forbidden imports;
2. confirm `requirements.txt` contains yt-dlp;
3. confirm `config.json` contains no YT Music playlist ID;
4. confirm active playlist schema contains no priority/count fields;
5. confirm first-run code does not instantiate Pipeline before snapshot completion;
6. confirm first-run code does not call per-track Spotify detail methods;
7. confirm active acquisition uses ytmusicapi for search and yt-dlp for the selected audio URL;
8. confirm final JSON says Spotify audio was not downloaded;
9. confirm final JSON provenance records yt-dlp as the audio downloader and ytmusicapi as the search authority.

Archived historical plans may contain old terms, but archives are excluded from runtime conformance scans.

---

# 56. CURRENT EXTERNAL API CONTRACTS

The final implementation is aligned to the current API documentation available during release preparation:

### Spotify

- Get Playlist Items: `GET /playlists/{playlist_id}/items`
- playlist item page maximum: 50
- current response field: `items`
- playlist access can require user authorization for private/collaborative playlists
- Spotify content/audio is not downloaded by this application

### YT Music

- `YTMusic.search(query, filter='songs'/'videos', limit=...)`
- `YTMusic.get_song(videoId, signatureTimestamp=...)`
- `get_song()` may return `streamingData` and adaptive formats with direct media URLs

The implementation intentionally depends only on the documented metadata/search/player interface and local FFmpeg conversion.

---

# 57. CONFIGURATION REFERENCE

The active configuration contains these major groups:

```text
project
playlist
paths
ffmpeg_location
spotify
ytMusic
lyrics
duplicate_detection
youtube_offset
models
runtime
audio
demucs
vad
chunking
alignment
audio_8d
hook
video_match
video_layout
render
recovery
```

There is no active:

```text
ytmusic_playlist_id
view priority
play priority
yt_dlp_binary
cookies.txt
```

The release config intentionally leaves only:

```text
playlist.spotify_playlist_id
```

for the playlist source identity.

---

# 58. OPERATOR SETUP

## Step 1: install Python dependencies

```powershell
python -m pip install -r requirements.txt
```

## Step 2: verify FFmpeg

```powershell
ffmpeg -version
ffprobe -version
```

## Step 3: set Spotify app credentials

Use environment variables or `config.json` as documented.

## Step 4: authorize Spotify

```powershell
python scripts/setup_spotify_auth.py
```

This produces:

```text
spotify_auth.json
```

## Step 5: authorize YT Music

```powershell
python scripts/setup_ytmusic_auth.py
```

or the Windows helper script.

This produces the ytmusicapi-compatible browser/auth configuration, for example:

```text
browser.json
```

## Step 6: set playlist ID

Edit:

```json
"playlist": {
  "spotify_playlist_id": "<your-playlist-id>"
}
```

## Step 7: run doctor

```powershell
python main.py --doctor
```

## Step 8: first snapshot

```powershell
python main.py
```

## Step 9: process songs

Run:

```powershell
python main.py
```

again.

---

# 59. NORMAL OPERATING PROCEDURE

The user does not need to manipulate per-song directories.

The normal loop is:

```text
prepare Spotify playlist
        |
        v
set playlist.spotify_playlist_id
        |
        v
run main.py once
        |
        v
snapshot committed + exit
        |
        v
run main.py again
        |
        v
process playlist entry 1
        |
        +--> terminal skip/duplicate -> next
        |
        +--> error -> retain retryable state
        |
        +--> complete -> next
        |
        v
playlist complete
        |
        v
message to change playlist ID
```

---

# 60. FAILURE HANDLING EXAMPLES

## Spotify playlist 403

Message should identify access/authentication and point to Spotify authorization.

## Spotify 429

Honor retry delay and continue.

## YT Music search returns no duration match

Mark `YTM_AUDIO_MATCH_NOT_FOUND`; do not choose an incompatible source.

## YT Music playability unavailable

Mark explicit `YTM_PLAYBACK_UNAVAILABLE`; do not bypass access restrictions.

## Direct stream URL unavailable

Mark `YTM_AUDIO_STREAM_UNAVAILABLE`; do not invoke a hidden alternative extractor.

## LRCLIB no synced LRC

Terminal:

```text
SKIPPED_NO_SYNCED_LRC
```

## Demucs fails

Keep source and earlier stages reusable; mark Demucs/alignment/8D/Reel incomplete.

## Alignment fails

Do not render 8D from an incomplete word timeline.

## 8D fails validation

Do not prompt for hook until a valid full-song 8D exists.

## Reel fails validation

Do not promote the final package to `FINALIZED`.

---

# 61. DUPLICATE REPLACEMENT SAFETY

The `keep_current` path is deliberately two-phase:

```text
current package complete
       |
       v
full validation
       |
       v
atomic promotion
       |
       v
mark current finalized
       |
       v
mark old replaced
       |
       v
clean old public files
```

The old final song is not deleted before the new one exists.

This prevents a failed replacement from destroying the previously finalized copy.

---

# 62. EXACT NO-SYNCED-LRC TERMINAL SEMANTICS

A song with no synchronized LRC is considered complete for the current playlist job because the requested downstream products cannot be safely produced without synchronization.

The result is:

```text
playlist_entries.status = skipped
playlist_entries.terminal = 1
songs.pipeline_status = SKIPPED_NO_SYNCED_LRC
songs.terminal = 1
```

The song remains auditable and recoverable through an explicit operator reset, but normal runs do not pick it again.

---

# 63. 8D ISOLATION RULE

The whole-song 8D file is a downstream artifact only.

It is forbidden as input to:

- Spotify matching;
- YT Music search duration matching;
- source acquisition validation;
- LRCLIB lookup;
- VAD;
- MMS acoustic recognition;
- CTC alignment;
- YouTube global offset matching.

It is allowed as input only to the Reel stage and final package reporting.

---

# 64. SOURCE AUDIO ISOLATION RULE

The original acquired YT Music source MP3 is the only authoritative source audio for:

- source duration;
- source hash;
- Demucs;
- Phase 2 acoustic alignment;
- YouTube offset reference;
- source metadata validation.

The whole-song 8D is never substituted for it.

---

# 65. VISUAL VIDEO IDENTITY RULE

The selected visual video is separate from the YT Music audio source if the search returns different IDs.

Both identities are preserved:

```text
YT Music audio video ID
YT Music visual video ID
```

This prevents confusion in audit records and Reel provenance.

---

# 66. SOURCE MEDIA DOWNLOAD COUNT

For a successfully processed song, expected media downloads are:

1. one YT Music audio-source stream for the song;
2. one visual YT Music video stream set needed for the Reel/offset stage.

No duplicate song audio acquisition should occur after caching.

No second 8D rendering should occur for the hook.

---

# 67. VALIDATION GATES

## Gate A — playlist snapshot

- complete pagination;
- every position recorded;
- transaction committed.

## Gate B — Spotify metadata

- track exists;
- duration positive;
- metadata JSON valid;
- artwork present when Spotify supplies it.

## Gate C — YT Music audio

- matching search result found;
- direct stream retrieved;
- FFmpeg output valid;
- duration compatible.

## Gate D — lyrics

- synced LRC exists;
- parse succeeds.

## Gate E — Demucs

- four expected stems;
- non-empty files;
- duration coverage valid.

## Gate F — alignment

- canonical timeline exists;
- chronological;
- word-level LRC exists;
- SYLT can be written.

## Gate G — 8D

- complete source duration;
- stereo;
- finite samples;
- configured mono-compatibility threshold.

## Gate H — hook

- start/end valid;
- persisted.

## Gate I — visual sync

- acceptable visual source;
- global offset within hard bound;
- mapped hook range valid.

## Gate J — Reel

- 1080x1920;
- expected duration;
- stereo audio;
- H.264-compatible codec;
- output files valid.

## Gate K — final promotion

- JSON hashes agree;
- all final files exist;
- Reel exists;
- DB state updated after promotion.

---

# 68. RELEASE AUDIT CHECKLIST

Before shipping a release ZIP:

```text
[ ] no active ytdlp import
[ ] yt-dlp dependency present
[ ] yt-dlp package/config present for audio acquisition
[ ] no YT Music playlist config
[ ] no view/plays ranking config
[ ] first run is Spotify snapshot-only
[ ] first run makes no per-track Spotify detail calls
[ ] first run does no YT Music media work
[ ] source order preserved
[ ] Spotify full catalog enrichment is per-song
[ ] Spotify artwork exact bytes
[ ] Spotify ISRC canonical
[ ] duplicate gate finalized-only
[ ] duplicate keep-previous stops before acquisition
[ ] duplicate keep-current is replacement-safe
[ ] YTM query title+album
[ ] first duration-compatible result selected
[ ] direct stream only
[ ] no signed URLs persisted
[ ] original source used for sync
[ ] 30 s reference and 30 s offset cap enforced
[ ] no DTW
[ ] whole-song 8D generated once
[ ] hook stored in ms
[ ] hook cropped from 8D
[ ] final Reel separate
[ ] final package atomic
[ ] DB integrity checks pass
[ ] all tests pass
[ ] clean ZIP extraction tested
```

---

# 69. REFERENCE DOCUMENTATION USED FOR CURRENT API CONTRACTS

Spotify:

- https://developer.spotify.com/documentation/web-api/reference/get-playlists-items
- https://developer.spotify.com/documentation/web-api/reference/get-playlist
- https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide

YT Music / ytmusicapi:

- https://ytmusicapi.readthedocs.io/en/stable/reference/search.html
- https://ytmusicapi.readthedocs.io/en/stable/reference/browsing.html

These references describe the current playlist-items endpoint, current `items` response shape, search filters, and `get_song()` streaming-data contract used by this implementation.

---

# 70. HISTORICAL SOURCE PRESERVATION

The project intentionally retains:

```text
PROJECT_PLAN_ORIGINAL.md
archives/phase1/
archives/phase2/
archives/phase3/
```

The reason is provenance, not runtime reuse.

The archived material documents the earlier standalone mechanics and the previous failures that shaped this final design.

The active source code is the implementation of this version 1.4.3 plan.

---

# 71. FINAL VERSION LOCK

This release locks the following decisions:

1. Spotify playlist is the sole playlist source.
2. First run is snapshot-only.
3. Playlist order is the only processing order.
4. No view/play ranking exists.
5. Spotify owns catalog metadata.
6. Spotify owns artwork.
7. Spotify owns ISRC.
8. Spotify audio is never downloaded.
9. YT Music is the media source.
10. YT Music search query is title + album.
11. First duration-compatible result wins.
12. YT Music audio candidates are discovered/selected through ytmusicapi.
13. The selected YT Music audio URL is downloaded through yt-dlp and post-processed to MP3; yt-dlp is not used for search.
14. ISRC duplicate detection is finalized-only and operator-resolved.
15. No synced LRC is terminally skipped.
16. Demucs runs once and is shared.
17. Word alignment is canonical and millisecond based.
18. 8D is whole-song.
19. Hook is manual and uncapped.
20. Hook audio comes from whole-song 8D.
21. YouTube visual offset is global and <=30 seconds.
22. Final song package is separate from final Reel.
23. Final outputs are atomically promoted.
24. Resume is dependency-aware.
25. All observed Windows/runtime failures remain addressed.

---

# 72. END-TO-END PSEUDOCODE

```python
main():
    print_banner()
    cfg = Config.load()
    pdb = PlaylistDB(...)

    playlist_id = cfg.get("playlist.spotify_playlist_id")

    playlist = pdb.get_playlist(playlist_id)

    if playlist is None:
        spotify = SpotifyClient(...)
        meta, raw_items = spotify.fetch_playlist_snapshot(playlist_id)
        normalized = [normalize_playlist_item(item, position) ...]
        pdb.ingest_snapshot(...)
        print("FIRST SPOTIFY PLAYLIST SNAPSHOT COMPLETE")
        exit()

    if playlist.playlist_done:
        print("PLAYLIST IS DONE")
        exit()

    sdb = SongsDB(...)
    rdb = ReelsDB(...)
    pipeline = Pipeline(...)

    for entry in pdb.actionable(playlist_id):
        result = pipeline.process_entry(entry)
        print(result)

    if pdb.mark_playlist_done_if_complete(playlist_id):
        print("PLAYLIST IS DONE")
```

`Pipeline.process_entry()`:

```python
load_or_create_state()
mark playlist entry processing

spotify = get_full_track_and_artwork()

ensure song row

if spotify.isrc:
    duplicate = songs_db.isrc_match(spotify.isrc, finalized_only=True)
    if duplicate:
        decision = prompt()
        if decision == KEEP_PREVIOUS:
            mark duplicate terminal
            return

search YT Music songs using title + album
select first duration-compatible result
get_song()
download direct audio stream
FFmpeg -> master.mp3
validate duration

fetch LRCLIB synchronized LRC
if absent:
    terminal SKIPPED_NO_SYNCED_LRC
    return

prepare source once
Demucs once
alignment once
word-level LRC once
whole-song 8D once

prompt hook
persist hook

search YT Music visual videos using title + album
select first duration-compatible non-lyrics result
download direct visual media
match first 30 seconds of original source against full visual audio
persist one global offset
map hook using offset
crop exact hook from whole-song 8D
render Reel
validate Reel

write cumulative master JSON
validate every final artifact
atomically promote
mark FINALIZED

if duplicate and KEEP_CURRENT:
    replace old final song only after new package is valid

mark playlist entry completed
```

---

# 73. FINAL OPERATING PRINCIPLE

The pipeline should be predictable:

```text
Spotify tells us WHAT and IN WHAT ORDER.

YT Music gives us PLAYABLE MEDIA.

LRCLIB tells us WHETHER synchronized lyrics exist.

MMS + CTC tells us WHERE the Telugu words occur.

Demucs gives the stem layers.

8D creates ONE complete spatial master.

The operator tells us WHERE the hook is.

The stored global offset tells us WHERE the matching visual segment is.

The Reel generator combines those already-validated artifacts.
```

No subsystem is allowed to silently take over another subsystem's authority.

That separation is the central invariant of the final architecture.


## 74. HISTORICAL VERSION 1.4.2 IMPLEMENTATION LOCK

This historical release locked Spotify Web API as the sole playlist authority, Spotify as the complete catalog/artwork source, ISRC as the canonical duplicate key, and ytmusicapi direct streams as the then-current YT Music media path. The later 1.4.5 release supersedes the media-acquisition portion only.

# 75. HISTORICAL VERSION 1.4.3 RELEASE LOCK

This historical release recorded the Spotify-first implementation and ytmusicapi direct-stream acquisition. Its direct-stream audio mechanism is superseded by the current 1.4.5 audio contract below; playlist snapshot, metadata, ISRC, downstream alignment, 8D, hook, visual sync, Reel rendering, cache, recovery, validation, and atomic promotion remain in force.

## 70.9 SQLite parent-row lifecycle

Before any per-song stage begins, `songs.db` MUST contain the parent `songs(song_key)` row. This parent is created idempotently from the frozen Spotify playlist entry using the playlist serial, Spotify track ID, title/artist/album, and safe bootstrap values. Only after that row is committed may the pipeline insert `stage_runs`, `audit_events`, or any child artifact record.

This ordering is mandatory because `stage_runs.song_key` and `audit_events.song_key` are foreign keys to `songs(song_key)`. If Spotify metadata retrieval or any earlier operation fails, the error handler must still be able to record the original failure without producing a second foreign-key exception.

The SongsDB bootstrap/upsert implementation must perform its existence check and write on one SQLite connection; required `basename` and `duration_ms` columns receive deterministic safe defaults before insertion.

## 70.9 Songs parent bootstrap and SQLite FK safety
A frozen Spotify playlist snapshot may omit catalog duration on an entry. Before any stage/audit child rows are written, the `songs` parent is created with a schema-safe duration sentinel of `0`; the authoritative Spotify duration replaces it during Spotify metadata enrichment. Stage and audit writers defensively create the parent with `INSERT OR IGNORE` so the original processing error cannot be masked by a foreign-key error.


## Current release authority — 1.4.7-final

For the active unified pipeline, the current audio contract supersedes earlier direct-stream/no-yt-dlp wording preserved in historical provenance sections above:

1. Spotify owns the frozen playlist snapshot and all authoritative per-song catalog metadata, artwork, and ISRC.
2. ytmusicapi searches YT Music with `title + album` and returns candidates in API order. The first candidate with known duration within the configured Spotify-duration tolerance is selected.
3. The selected candidate is converted to `https://music.youtube.com/watch?v=<video_id>`. Only that URL is passed to yt-dlp. yt-dlp is configured for a single URL (`noplaylist=True`), `bestaudio/best`, retries, and FFmpeg MP3 extraction.
4. The default final audio output is 320 kbps MP3. The resulting duration must remain within the configured Spotify duration tolerance. Source extractor/format/bitrate/sample-rate metadata is written to `master.info.json` and propagated into `songs.db`.
5. yt-dlp is never used to search, rank, discover, or select candidates. The ytmusicapi search result remains the source-selection authority.
6. Optional authenticated browser-cookie or cookie-file settings may be supplied for the user's own permitted playback session; no access-control bypass is implemented.
7. The remainder of the pipeline—LRCLIB synchronized LRC, one shared Demucs separation, Telugu MMS/CTC alignment, whole-song 8D, manual hook, YouTube visual source/global offset, Reel rendering, validation, and atomic promotion—remains unchanged.
