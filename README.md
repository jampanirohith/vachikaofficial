# Vachika Official

A Spotify-first pipeline for selecting matching YT Music audio, obtaining synchronized Telugu lyrics, aligning lyrics word-by-word, producing a whole-song 8D MP3, selecting a visual YouTube source, and rendering validated lyric Reels.

## Requirements

- Python **3.12 or newer** (the rendering code uses the Python 3.12 f-string grammar).
- FFmpeg and FFprobe available on `PATH` (or configured with `ffmpeg_location`).
- A Spotify Developer application and user-authorized token for playlist/catalog access.
- Enough disk space and RAM for the Telugu MMS/CTC, HTDemucs and Silero VAD models. A compatible NVIDIA GPU is preferred; CPU fallback is enabled by default.
- A network connection for the first-time model and media downloads.

## Install and configure (Windows PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Install/verify FFmpeg and FFprobe, then edit the included `config.json`. The included file is a **safe template**: it contains no Spotify credentials and has an empty playlist ID. Set `playlist.spotify_playlist_id`, add your Spotify app client ID as configured for your setup, and check the paths and render settings. `config.json.example` is the shareable baseline; do not put private credentials in it.

Create a Spotify user token:

```powershell
python scripts/setup_spotify_auth.py
```

The redirect URI configured in Spotify and `config.json` must match exactly. The default is `http://127.0.0.1:8888/callback`. To require CUDA instead of permitting fallback, set `runtime.require_cuda=true` and `runtime.allow_cpu_fallback=false`; those settings must not contradict each other.

## Recommended first-run sequence

```powershell
python main.py --doctor
python main.py --prepare-models
python main.py --dry-run
python main.py
```

`--prepare-models` downloads/caches the active Telugu MMS/CTC model and adapter, HTDemucs weights, and Silero VAD model into the project-owned `models/` cache. It does not access Spotify, initialize song databases, or process audio. A per-model result is written to `models/preparation_manifest.json`; if the command exits non-zero, inspect the model failures and rerun after fixing connectivity/dependencies.

The first normal run for a new playlist fetches and freezes the complete Spotify playlist snapshot in exact playlist order, then exits without processing a song. A subsequent run processes actionable entries in that frozen order. Existing playlist IDs are not silently refetched.

## Pipeline overview

```text
Spotify playlist snapshot (one-time, ordered)
  -> Spotify track/album/artist metadata, artwork and ISRC
  -> finalized-song ISRC duplicate gate
  -> YT Music search + duration/title/artist matching
  -> yt-dlp download of the exact selected YT Music URL + FFmpeg MP3
  -> LRCLIB /api/get then /api/search for synchronized lyrics
  -> source and 16 kHz audio derivation with provenance-bound caches
  -> HTDemucs stems (attempt-isolated, manifest-verified)
  -> Telugu MMS/CTC + VAD/chunking/word alignment and timing repair
  -> final MP3, line/word LRC, SYLT and canonical song JSON
  -> whole-song 8D processing
  -> one visual YouTube search; cache the exact selected URL
  -> selected-video reference-audio download + whole-song offset confidence check
  -> manual/deferred hook selection
  -> absolute-timeline source video trim, vertical crop, word-by-word overlay
  -> validation and artifact promotion
```

Spotify audio is **never downloaded**. `ytmusicapi` is used for discovery; yt-dlp is used only with the already selected `music.youtube.com/watch?v=...` URL. The video/offset stage rejects low-confidence or ambiguous matches rather than always accepting the top score. Adjust `youtube_offset.min_match_score` and `min_peak_margin` only after evaluating representative matches; overly strict thresholds can route legitimate videos to review.

## Useful commands

```powershell
python main.py --doctor                 # dependency/runtime diagnostics
python main.py --prepare-models          # download/cache all ML models
python main.py --dry-run                # read-only view of actionable DB entries
python main.py --status                 # playlist progress
python main.py --single 12              # process one actionable playlist serial
python main.py --resume                 # reconcile interrupted entries, then process
python main.py --ingest-only            # verify/freeze playlist snapshot without song processing
python main.py --reconcile              # recover interrupted state only
python main.py --backup-db .\db-backup  # back up configured DB files
python main.py --check-db               # SQLite integrity verification (not a schema migration)
pytest -q                                 # automated regression suite
```

`--check-db` verifies SQLite integrity; the older `--migrate-db` flag remains as an alias but does not migrate schemas.

`--dry-run` is strictly read-only: it does not create directories, initialize databases, call Spotify, or write snapshots. `--resume` reconciles interrupted states before the normal processing loop. `--ingest-only` verifies a frozen snapshot and exits without processing songs.

## Runtime files and credentials

Generated databases, workspaces, model weights, logs, final songs and rendered Reels are runtime state and are ignored by Git. The replacement source ZIP intentionally contains **no live hook queue or generated song state**; `hook.queue_file` is created by the application when it first needs it. Keep `spotify_auth.json` and any yt-dlp cookie file private.

## Source organization

Active code is organized under `src/providers/`, `src/audio/`, `src/video/`, `src/storage/`, `src/orchestration/`, `src/common/`, `src/diagnostics/` and `src/alignment/`. The production Telugu alignment and FFmpeg/ASS rendering engines remain in `src/phase2core/` and `src/rendercore/`. Final song JSON uses the versioned `unified_song_master_v2` manifest contract.

## Architecture and troubleshooting

- [Current project plan](PROJECT_PLAN.md)
- [Pipeline architecture](docs/ARCHITECTURE.md)
- [Source code map](src/README.md)
- [Final project audit](docs/FULL_PROJECT_AUDIT.md)
- [Configuration reference](docs/CONFIGURATION.md)
- [Development roadmap](docs/ROADMAP.md)
- [Build and validation record](docs/BUILD_INFO.md)

For model-download failures run `python main.py --prepare-models` again after fixing the reported dependency/network issue. For an offset rejection, inspect the per-song work state and YouTube match row, then review or replace the selected video before forcing that stage to rebuild. Do not manually copy cached stems or reference audio between songs: cache manifests bind artifacts to their source content.
