# Vachika Official

## Setup
## NVIDIA GPU / CUDA policy

The runtime is **CUDA-first** for PyTorch workloads. The configured preferred device is `cuda` (GPU 0 by default), with CPU fallback only when CUDA is unavailable or a CUDA inference/separation attempt fails. The GPU-capable stages are:

- **HTDemucs**: launched with `demucs.separate --device cuda` and recorded in `audio/stems/demucs_manifest.json`.
- **Telugu MMS alignment**: the Hugging Face CTC model is moved to CUDA and input tensors follow the model device.
- **Silero VAD**: the Torch model and audio tensor are moved to CUDA when available.
- **Reel video encoding**: FFmpeg prefers `h264_nvenc` and falls back to `libx264` only if NVENC is unavailable or fails.

Network requests, Spotify/YT Music metadata, SQLite, file hashing, Mutagen/ID3, PCM file I/O, and CPU-oriented NumPy/SciPy transforms remain on CPU because moving those operations to CUDA would not provide a supported or meaningful acceleration path.

The project also invalidates/rebuilds cached Demucs and alignment/VAD work when a prior run was CPU-backed and a CUDA-capable runtime is now available. This prevents a later GPU run from silently reusing CPU results.

For Windows NVIDIA setup:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/install_windows_cuda.ps1
powershell -ExecutionPolicy Bypass -File scripts/verify_windows_gpu.ps1
python main.py --doctor
```

### 1. Create & Start Envirnment:
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### 2. Install dependencies:
```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### 3. Install and verify FFmpeg/FFprobe.

### 4. Configure Spotify OAuth for playlist and catalog access:

```powershell
python scripts/setup_spotify_auth.py
```

The generated `spotify_auth.json` uses the playlist-read scopes required by the current Spotify playlist-items API.


### 5. Put your Spotify playlist ID in `config.json`:

```json
"playlist": {
  "provider": "spotify",
  "spotify_playlist_id": "YOUR_SPOTIFY_PLAYLIST_ID"
}
```

### 6. Run:

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
