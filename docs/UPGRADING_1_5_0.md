# Upgrading an existing Vachika project to 1.5.0

## Before replacing source files

1. Keep your existing `config.json`, `spotify_auth.json`, optional yt-dlp cookies, model cache and generated song files. They are local runtime data and are not included in the source ZIP.
2. Make a full project backup. You can also run `python main.py --backup-db .\db-backup` from the old version before updating.
3. Stop any running Vachika process before database migration.

## First run after updating

1. Confirm `config.json` still has your Spotify playlist ID, OAuth settings and paths. If starting from the clean source package, copy `config.json.example` to `config.json` and configure it.
2. Run `python main.py --doctor`, and then `python main.py --download-models` if model weights are not already cached at the configured model path. Model preparation can also be done before filling in Spotify settings.
3. Run `python main.py --status` or `python main.py --load-playlist` to initialize the unified database.
4. The migration imports compatible rows from the old `db/playlist.db`, `db/songs.db` and `db/reels.db` into `db/vachika.db`, then archives the old database files under `db/backups/legacy-before-unification-*`. Per-workspace `phase2.sqlite` databases are integrity-checked and archived under `db/backups/legacy-phase2-*`; the new Phase-2 tables are namespaced in the unified DB. Review `db/migration_manifest.json` after migration.
5. `db/songs.json` is regenerated from SQLite. Do not edit it as operational state. Per-song JSON manifests continue to live with song outputs.
6. The offset algorithm changed. Pending hook-queue entries that do not identify the new MMS/LRCLIB offset method or do not match their song JSON are marked stale and sent through the new offset stage. This prevents use of an old energy-envelope offset.
7. Review any song marked `needs_review`; the pipeline intentionally refuses to generate a Reel if the opening lyric anchor is ambiguous or fails the timestamp-consistency checks.

## New command separation

```powershell
python main.py --download-models
python main.py --load-playlist
python main.py --process-playlist --resume
```

`--load-playlist` freezes metadata/order and exits; it does not process songs. `--process-playlist` only processes a previously committed snapshot.

## Rollback

The migration archives are retained under `db/backups/`. If migration fails, do not delete these backups. A database backup can be restored using `python main.py --restore-db <backup-directory>` after reviewing which backup format is being restored. Keep a separate whole-project backup because databases do not contain the generated media files.
