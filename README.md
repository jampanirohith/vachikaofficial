# Unified Spotify Playlist -> YT Music -> Telugu Word LRC -> Whole-song 8D -> Reel

Version: `1.4.7-final`

This is the unified Windows-first implementation built from the supplied Phase 1, Phase 2, and Phase 3 codebases.

## Source rules

Spotify is the sole playlist source. On the first normal run for a new playlist ID, the program fetches the complete playlist through the Spotify Web API, records every returned entry in exact Spotify order in `playlist.db`, prints completion, and exits. It performs no song processing during that first run.

Later runs use the frozen playlist snapshot and process one actionable song at a time in exact stored Spotify order. The playlist is never refreshed during normal processing. There is no view/play/popularity/ranking stage.

For each song, Spotify is the catalog authority: the complete track/album/artist API payload is stored, including raw catalog JSON, exact album artwork bytes, and ISRC. Spotify audio is never downloaded.

YT Music is used only for media selection. The query is `title + album`; the all returned search candidates with known durations are evaluated; the candidate with the closest duration to the Spotify catalog duration is selected, and no candidate is accepted outside the configured tolerance. The selected result is converted to its canonical `https://music.youtube.com/watch?v=...` URL and passed directly to `yt-dlp` for high-quality audio acquisition. yt-dlp is not used to search, rank, or discover candidates. The final audio is converted to MP3 at the configured output bitrate through FFmpeg.

## Setup

0. Create & Run Virtual Environment

```powershell
python -m venv .venv
source .venv/bin/activate
```

1. Install dependencies:

```powershell
python -m pip install -r requirements.txt
```

2. Install and verify FFmpeg/FFprobe.

3. Configure Spotify OAuth for playlist and catalog access:

```powershell
python scripts/setup_spotify_auth.py
```

The generated `spotify_auth.json` uses the playlist-read scopes required by the current Spotify playlist-items API.

4. Configure YT Music authentication:

```powershell
python scripts/setup_ytmusic_auth.py
```

5. Put your Spotify playlist ID in `config.json`:

```json
"playlist": {
  "provider": "spotify",
  "spotify_playlist_id": "YOUR_SPOTIFY_PLAYLIST_ID"
}
```

6. Run:

```powershell
python main.py --doctor
```

## First run

```powershell
python main.py
```

For a new playlist ID this records the complete frozen snapshot and exits. It does not download or process songs.

## Processing run

```powershell
python main.py
```

Each actionable entry follows:

```text
Spotify full track + album + artists + artwork + ISRC
-> ISRC duplicate gate
-> YT Music title+artist+album search
-> closest Spotify-duration result
-> yt-dlp(selected YT Music URL) -> FFmpeg MP3
-> LRCLIB exact `/api/get` + `/api/search` fallback -> synchronized LRC
-> one full Demucs separation
-> Telugu MMS/CTC alignment
-> canonical word timeline + word-level LRC + SYLT
-> whole-song 8D
-> manual hook
-> YT Music visual search + global offset
-> Reel
-> validation + atomic promotion
```

A missing synchronized LRC is terminal `SKIPPED_NO_SYNCED_LRC` and does not trigger downstream alignment/8D/hook/Reel work.

## ISRC duplicate handling

When Spotify supplies an ISRC, the application checks finalized prior songs with the same normalized ISRC. The operator chooses:

```text
1 = keep previous
2 = keep current
```

Choosing the previous song makes the current playlist entry terminally duplicate without downloading its YT Music media. Choosing the current song completes the new pipeline result first and then safely replaces the older public finalized artifact after validation.

## YT Music audio acquisition contract

The active audio path is deliberately split: `ytmusicapi.search()` performs discovery and duration matching; `yt-dlp` performs the actual download from the already-selected `music.youtube.com` URL. yt-dlp is never given a search query and `noplaylist=True` is enforced. The default selector is `bestaudio/best` and the default final MP3 target is 320 kbps.

Optional `ytmusic.yt_dlp.cookies_from_browser` or `ytmusic.yt_dlp.cookie_file` settings may be configured on the Windows machine when the selected source is only playable with the user's own authenticated browser session. The pipeline does not bypass access controls.

Read `PROJECT_PLAN.md` for the complete implementation specification.

## SQLite stage/audit lifecycle

At the start of every song, the pipeline creates the `songs(song_key)` parent row before opening any stage or audit record. This guarantees that a Spotify/YT Music/lyrics failure can be persisted as a retryable song error rather than producing a secondary SQLite foreign-key exception.
