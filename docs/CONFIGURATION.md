# Configuration reference

`config.json` is the local safe template; `config.json.example` is the shareable baseline. With no `--config` argument, the application loads `config.json` beside `main.py`, independent of the current working directory. With `--config path/to/file.json`, the file is selected relative to the command's current directory; all configured relative paths are resolved from the directory containing that selected config.

## Required local settings

- `playlist.spotify_playlist_id`: playlist ID to snapshot/process.
- Spotify app ID/token settings required by the configured Spotify auth flow. Keep `spotify_auth.json` private.
- `spotify.redirect_uri`: must exactly match the Spotify Developer Dashboard; the default local callback is `http://127.0.0.1:8888/callback`.
- `paths.database_dir`, `paths.work_dir`, `paths.songs_final`, `paths.reels_generated`, `paths.models_dir`, `paths.fonts_dir` and `paths.logs_dir`: relative paths resolve from the selected config's parent unless absolute.
- `ffmpeg_location`: leave null when FFmpeg/FFprobe are on `PATH`; otherwise use the installation path format supported by the runtime.

## Runtime policy

- `runtime.device` defaults to `cuda`, `runtime.fallback_device` to `cpu`, `runtime.require_cuda` is false and `runtime.allow_cpu_fallback` is true. To require CUDA, set `runtime.require_cuda=true` and `runtime.allow_cpu_fallback=false`. Contradictory settings are rejected by config validation.
- `models.mms_model`, `models.language_iso3`, and `demucs.model` identify active alignment/separation models. `python main.py --prepare-models` warms their local caches.
- `youtube_offset.frame_ms`, `coarse_step_ms`, `fine_step_ms`, `min_match_score`, `min_peak_margin`, and `ambiguity_window_ms` are consumed by the current full-song matcher and included in its cache fingerprint. Defaults are starting thresholds, not universally calibrated cutoffs.
- `ytmusic.yt_dlp.cookie_file` and `cookies_from_browser` are optional. Keep cookie files outside Git and use them only for media you are entitled to access. The `ytmusic.timeout_seconds` HTTP timeout applies to the private `requests.Session` supplied to `ytmusicapi`; it does not change global Requests behavior. The download timeout is separately enforced for yt-dlp.

## Telugu font configuration

- `lyrics.font_filename` selects an asset below `paths.fonts_dir`; the packaged default is `Suravaram-Regular.ttf`.
- `lyrics.font_name` is the font's embedded family name, **`Suravaram`**, not the filename or style string. Baloo's embedded family is `Baloo Tammudu 2`.
- `lyrics.font_path`, when set, is authoritative. A missing or invalid explicit file raises a clear rendering error instead of silently substituting an unrelated font.
- The manifest and Reel DB record the resolved font family, actual file path, filename and the family-name source used by ASS rendering.

## Model download and cache

Run `python main.py --prepare-models`. Outputs live under `paths.models_dir`, plus `preparation_manifest.json`, which records success/failure per model. Successful caches are kept if another model download fails; the command exits non-zero when any required model remains unavailable.

## Database maintenance commands

Use `python main.py --check-db` to run SQLite integrity checks. The old `--migrate-db` spelling remains as a compatibility alias for the same check; it **does not** execute a schema migration. Database backup and restore use `paths.database_dir`; schema changes need explicit versioned migration work rather than an integrity check.
