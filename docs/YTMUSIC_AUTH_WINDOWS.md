# YouTube Music authentication on Windows

Release contract: 1.4.7-final

The unified project uses **ytmusicapi** for YT Music search/selection and **yt-dlp** only for downloading the already-selected YT Music URL. It does not use YT Music as the playlist source.

## Setup

Create the YT Music authentication file:

```powershell
python .\scripts\setup_ytmusic_auth.py --output .\browser.json
```

or:

```powershell
.\scripts\setup_ytmusic_auth.ps1
```

The credential file is used for YT Music `search()` and `get_song()` calls during per-song processing and for visual-video retrieval. Keep it private.

## Role separation

Spotify owns the playlist and catalog metadata. YT Music is only the media source:

```text
Spotify playlist
    -> frozen playlist.db
    -> Spotify full track/album/artist metadata + artwork + ISRC
    -> YT Music title+album search
    -> first duration-compatible result
    -> selected music.youtube.com URL
    -> yt-dlp (single URL, no search)
    -> FFmpeg -> MP3
```

No playlist is fetched from YT Music and no view/play-count lookup is performed. ytmusicapi remains the search/selection layer, while yt-dlp downloads only the selected YT Music audio URL.

## Direct-stream limitation

The active implementation passes only the selected `https://music.youtube.com/watch?v=<video_id>` URL to yt-dlp. If the authorized environment cannot fetch that selected source, the song fails with an explicit media error. The application does not bypass access controls or silently search for a different recording.
