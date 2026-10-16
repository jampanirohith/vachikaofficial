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

Install/verify FFmpeg and FFprobe. The source package ships `config.json.example`; it intentionally does not ship a private/runtime `config.json`. The first model-preparation and diagnostic commands work from the example config without Spotify setup. Copy `config.json.example` to `config.json` before playlist processing, then set `playlist.spotify_playlist_id`, your Spotify app client ID, redirect URI and path/render settings. `config.json.example` is the shareable baseline; never put private credentials in it.

Create a Spotify user token:

```powershell
python scripts/setup_spotify_auth.py
```

The redirect URI configured in Spotify and `config.json` must match exactly. The default is `http://127.0.0.1:8888/callback`. To require CUDA instead of permitting fallback, set `runtime.require_cuda=true` and `runtime.allow_cpu_fallback=false`; those settings must not contradict each other.

## Recommended first-run sequence

```powershell
python main.py --download-models
python main.py --doctor
Copy-Item config.json.example config.json
# Edit config.json and complete Spotify OAuth setup before playlist commands.
python main.py --load-playlist
python main.py --process-playlist --resume
```

`--download-models` (`--prepare-models` remains an alias) downloads/caches the active Telugu MMS/CTC model and adapter, HTDemucs weights, and Silero VAD model into the project-owned `models/` cache. It can run with an empty playlist ID, does not access Spotify, initialize the operational database, or process songs. A per-model result is written to `models/preparation_manifest.json`; if the command exits non-zero, inspect the model failures and rerun after fixing connectivity/dependencies. This command prepares model weights, not Python packages or FFmpeg.

`--load-playlist` fetches and commits one frozen Spotify playlist snapshot in exact order; it never processes a song. `--process-playlist` refuses to fetch a new snapshot and processes only an already committed snapshot. Existing playlist IDs are not silently refetched. A plain `python main.py` retains the backwards-compatible snapshot-first behavior for a new playlist.

## Pipeline overview

```text
Spotify playlist snapshot (one-time, ordered)
  -> Spotify track/album/artist metadata, artwork and ISRC
  -> finalized-song ISRC duplicate gate
  -> LRCLIB /api/get then /api/search for synchronized Telugu lyrics
  -> YT Music search + title/artist/album/duration matching for reference song audio
  -> yt-dlp download of the exact selected YT Music audio URL
  -> one visual YouTube video search; cache the exact selected visual URL
  -> download audio from that exact video URL
  -> MMS/CTC searches the same opening LRCLIB lyric phrase in both audio timelines
  -> median of corresponding word-timestamp differences = YouTube offset
  -> source and 16 kHz audio derivation with provenance-bound caches
  -> HTDemucs stems (attempt-isolated, manifest-verified)
  -> full Telugu MMS/CTC + VAD/chunking/word alignment and timing repair
  -> final MP3, line/word LRC, SYLT and progressively updated song JSON
  -> whole-song 8D processing
  -> manual/deferred hook selection
  -> absolute-timeline source video trim, vertical crop, word-by-word overlay
  -> validation and artifact promotion
```

Spotify supplies catalog metadata and artwork; Spotify audio is **not downloaded**. The acquired YT Music recording is the song-audio reference. For offset calculation, LRCLIB lyrics are the only transcript used: MMS/CTC searches for the same opening lyric phrase independently in the YT Music reference audio and selected YouTube video's audio, then takes the robust median of the paired word timestamp differences. There is no energy-envelope offset fallback. If the lyric anchor is ambiguous or does not meet the configured quality checks, the song is sent to `needs_review` instead of generating a potentially mis-synchronized Reel. The complete word-level LRC is still generated later for subtitles and audio processing.

## Useful commands

```powershell
python main.py --doctor                 # dependency/runtime diagnostics
python main.py --download-models         # download/cache all ML models
python main.py --dry-run                # read-only view of actionable DB entries
python main.py --status                 # playlist progress
python main.py --single 12              # process one actionable playlist serial
python main.py --resume                 # reconcile interrupted entries, then process
python main.py --load-playlist           # fetch/freeze the Spotify snapshot only
python main.py --process-playlist        # process only a committed snapshot
python main.py --reconcile              # recover interrupted state only
python main.py --backup-db .\db-backup  # back up configured DB files
python main.py --check-db               # migrate legacy stores/schema, sync catalog and verify integrity
pytest -q                                 # automated regression suite
```

`--check-db` is the safe upgrade entry point: it imports legacy databases and Phase-2 checkpoints into `db/vachika.db`, applies the retired-offset schema migration when needed, regenerates `db/songs.json`, and verifies database integrity. `--migrate-db` remains as a compatibility alias for this migration-and-validation command.

`--dry-run` is strictly read-only: it does not create directories, initialize databases, call Spotify, or write snapshots. `--resume` reconciles interrupted states before the normal processing loop. Playlist ingestion is explicit via `--load-playlist`.

## Runtime files and credentials

Generated databases, workspaces, model weights, logs, final media and rendered Reels are runtime state. The active operational database is `db/vachika.db`; `db/songs.json` is an atomically regenerated, human-readable catalog export from that database and must not be edited by hand. Each song's `songs/final/<song>.json` is the detailed progressive processing/provenance manifest. Old `playlist.db`, `songs.db`, and `reels.db` stores are imported then archived during migration. Existing per-workspace `phase2.sqlite` checkpoint rows are migrated into namespaced `phase2_*` tables in `vachika.db`; their original files are retained only under `db/backups/`. No separate legacy database is created during normal operation. Keep `spotify_auth.json` and any yt-dlp cookie file private.

## Source organization

Active code is organized under `src/providers/`, `src/audio/`, `src/video/`, `src/storage/`, `src/orchestration/`, `src/common/`, `src/diagnostics/` and `src/alignment/`. The production Telugu alignment and FFmpeg/ASS rendering engines remain in `src/phase2core/` and `src/rendercore/`. Final song JSON uses the versioned `unified_song_master_v2` manifest contract.

## Architecture and troubleshooting

- [Current project plan](PROJECT_PLAN.md)
- [Pipeline architecture](docs/ARCHITECTURE.md)
- [Upgrade and migration guide](docs/UPGRADING_1_5_1.md)
- [Source code map](src/README.md)
- [Configuration reference](docs/CONFIGURATION.md)
- [Development roadmap](docs/ROADMAP.md)
- [Build and validation record](docs/BUILD_INFO.md)

For model-download failures run `python main.py --prepare-models` again after fixing the reported dependency/network issue. For an MMS offset rejection, inspect `reference_audio/lyric_anchor.json`, `youtube/lyric_anchor.json` and `youtube/offset.json` in the song workspace, then review or replace the selected video before forcing the relevant stage to rebuild. Do not manually copy cached stems or reference audio between songs: cache manifests bind artifacts to their source content.
