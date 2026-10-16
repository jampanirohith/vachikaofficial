#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sqlite3
import json
import logging
import shutil
import sys
import time
from pathlib import Path

from src.config import Config
from src.orchestration.recovery import Recovery
from src.diagnostics.doctor import run_doctor
from src.storage.maintenance import backup_databases, restore_databases, migrate_databases, integrity_check
from src.providers.spotify import SpotifyClient, SpotifyError
from src.storage.unified_db import initialize_unified_database
from src.storage.song_catalog import sync_songs_json


def logger(cfg):
    path = cfg.path("paths.logs_dir")
    path.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger("unified")
    log.setLevel(logging.INFO)
    log.handlers.clear()
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(formatter)
    file_handler = logging.FileHandler(path / "unified.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    log.addHandler(stream)
    log.addHandler(file_handler)
    return log


def playlist_snapshot(cfg, pdb, log):
    """One-time Spotify playlist snapshot. A new playlist always ends the run after commit."""
    playlist_id = str(cfg.get("playlist.spotify_playlist_id") or "").strip()
    if not playlist_id or playlist_id == "YOUR_SPOTIFY_PLAYLIST_ID":
        raise SystemExit("Set playlist.spotify_playlist_id in config.json before running.")

    current = pdb.get_playlist(playlist_id)
    if current:
        if int(current.get("playlist_done") or 0):
            print("PLAYLIST IS DONE", flush=True)
            print(f"Playlist {playlist_id} is already complete. Update playlist.spotify_playlist_id in config.json for a new playlist.", flush=True)
            return "done"
        if current.get("ingestion_status") != "done" or int(current.get("entry_count") or 0) == 0:
            raise SystemExit(
                f"Playlist {playlist_id} exists but does not contain a committed Spotify snapshot. "
                "The normal run will not re-fetch an existing playlist ID. Restore or inspect db/vachika.db."
            )
        print(f"Using frozen Spotify playlist snapshot {playlist_id}; no playlist re-fetch will occur.", flush=True)
        return "existing"

    log.info("Spotify API playlist snapshot fetch started: %s", playlist_id)
    print(f"Fetching complete Spotify playlist via Spotify Web API: {playlist_id}", flush=True)
    client = SpotifyClient(cfg.get("spotify", {}) or {}, project_root=cfg.root)
    if bool(cfg.get("spotify.require_user_auth", True)) and not client.has_user_auth:
        raise SystemExit(
            "SPOTIFY_USER_AUTH_REQUIRED: this project requires a user-authorized Spotify token for playlist snapshot "
            "and catalog access. Run python scripts/setup_spotify_auth.py first."
        )
    try:
        meta, raw_items, pid = client.fetch_playlist_snapshot(
            playlist_id,
            progress=lambda done, total: print(f"Spotify playlist snapshot {done}/{total}", flush=True) if (done == 1 or done == total or done % 50 == 0) else None,
        )
    except SpotifyError:
        raise

    # Normalize directly from the already-fetched page data: this does not make any per-track
    # Spotify API calls during the first-run snapshot.
    from src.providers.spotify_playlist import normalize_playlist_item
    normalized_items = [normalize_playlist_item(item, i) for i, item in enumerate(raw_items, 1)]
    snapshot_id = str(meta.get("snapshot_id") or "") or None
    playlist_url = str(((meta.get("external_urls") or {}).get("spotify") if isinstance(meta.get("external_urls"), dict) else "") or f"https://open.spotify.com/playlist/{pid}")
    result = pdb.ingest_snapshot(
        pid,
        meta.get("name") or meta.get("title") or "Spotify Playlist",
        playlist_url,
        snapshot_id,
        normalized_items,
        raw_playlist=meta,
    )
    log.info("Spotify playlist snapshot committed: %d entries", result["entry_count"])
    print(f"Spotify playlist snapshot recorded: {result['entry_count']} entries in exact Spotify playlist order", flush=True)
    return "new"


def _serial_arg(value):
    try:
        return int(str(value))
    except Exception as exc:
        raise SystemExit(f"Invalid song serial: {value}") from exc


def _state_path(cfg, entry):
    track = str(entry.get("spotify_track_id") or "unavailable")
    return cfg.work_dir / f"{int(entry['playlist_serial'])}_{track}" / "state.json"


def invalidate_stage(cfg, pdb, sdb, rdb, stage, serial):
    """Invalidate one stage and its true downstream dependencies.

    The offset now depends on LRCLIB, YT Music reference audio, the selected YouTube
    audio, and MMS configuration, but deliberately does *not* depend on Demucs or the
    later full-song word-LRC stage.
    """
    entry = pdb.get_entry(serial)
    if not entry:
        return False, "song serial not found"
    key = f"{serial}_{entry.get('spotify_track_id') or 'unavailable'}"
    state_path = _state_path(cfg, entry)
    state = {}
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            state = {}

    downstream = {
        "spotify": ("spotify_metadata", "lyrics", "ytmusic_audio", "youtube", "reference_lyric_anchor", "youtube_lyric_anchor", "youtube_offset", "demucs", "alignment", "8d", "hook", "reel"),
        "spotify_metadata": ("spotify_metadata", "lyrics", "ytmusic_audio", "youtube", "reference_lyric_anchor", "youtube_lyric_anchor", "youtube_offset", "demucs", "alignment", "8d", "hook", "reel"),
        "lrclib": ("lyrics", "reference_lyric_anchor", "youtube_lyric_anchor", "youtube_offset", "alignment", "8d", "hook", "reel"),
        "lyrics": ("lyrics", "reference_lyric_anchor", "youtube_lyric_anchor", "youtube_offset", "alignment", "8d", "hook", "reel"),
        "ytmusic_audio": ("ytmusic_audio", "reference_lyric_anchor", "youtube_offset", "demucs", "alignment", "8d", "hook", "reel"),
        "youtube_selection": ("youtube", "youtube_lyric_anchor", "youtube_offset", "hook", "reel"),
        "youtube": ("youtube", "youtube_lyric_anchor", "youtube_offset", "hook", "reel"),
        "reference_lyric_anchor": ("reference_lyric_anchor", "youtube_offset", "hook", "reel"),
        "youtube_lyric_anchor": ("youtube_lyric_anchor", "youtube_offset", "hook", "reel"),
        "youtube_offset": ("youtube_offset", "hook", "reel"),
        "demucs": ("demucs", "alignment", "8d", "hook", "reel"),
        "alignment": ("alignment", "8d", "hook", "reel"),
        "8d": ("8d", "hook", "reel"),
        "audio_8d": ("8d", "hook", "reel"),
        "hook": ("hook", "reel"),
        "reel": ("reel",),
    }
    stage = str(stage).lower().strip()
    if stage not in downstream:
        return False, f"unsupported stage: {stage}"
    for state_key in downstream[stage]:
        state.pop(state_key, None)

    work = cfg.work_dir / key
    workspaces = [work, *sorted(work.glob("source_*"))]
    targets = []
    # Stage-specific locations relative to the key workspace.
    if stage in {"spotify", "spotify_metadata"}:
        targets.extend([work / "metadata"])
    if stage in {"spotify", "spotify_metadata", "ytmusic_audio"}:
        targets.extend([work / "acquisition"])
    if stage in {"spotify", "spotify_metadata", "lrclib", "lyrics"}:
        targets.extend([work / "lyrics"])
    if stage in {"spotify", "spotify_metadata", "youtube_selection", "youtube"}:
        targets.extend(ws / "youtube" for ws in workspaces)
    else:
        if stage in {"reference_lyric_anchor"}:
            targets.extend(ws / "reference_audio" for ws in workspaces)
        if stage in {"youtube_lyric_anchor"}:
            targets.extend(ws / "youtube" / "lyric_anchor.json" for ws in workspaces)
        if stage == "youtube_offset":
            targets.extend(ws / "youtube" / "offset.json" for ws in workspaces)

    if stage in {"lyrics", "lrclib", "spotify", "spotify_metadata", "ytmusic_audio"}:
        targets.extend(ws / "alignment" for ws in workspaces)
        targets.extend(ws / "audio" / "8d" for ws in workspaces)
        targets.extend(ws / "final_reel" for ws in workspaces)
    elif stage == "demucs":
        for ws in workspaces:
            targets.extend([ws / "audio" / "stems", ws / "alignment", ws / "audio" / "8d", ws / "final_reel"])
    elif stage == "alignment":
        for ws in workspaces:
            targets.extend([ws / "alignment", ws / "audio" / "8d", ws / "final_reel"])
    elif stage in {"8d", "audio_8d"}:
        for ws in workspaces:
            targets.extend([ws / "audio" / "8d", ws / "final_reel"])
    elif stage == "hook":
        targets.extend(ws / "final_reel" for ws in workspaces)
    elif stage == "reel":
        targets.extend(ws / "final_reel" for ws in workspaces)

    for target in dict.fromkeys(targets):
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
        elif target.exists():
            target.unlink(missing_ok=True)

    state["forced_rebuild_stage"] = stage
    state["forced_rebuild_requested_at"] = time.time()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    pdb.set_status(serial, "pending", terminal=0, reason=f"FORCE_REBUILD_{stage}")
    sdb.update_song(key, pipeline_status="pending", terminal=0, terminal_reason=f"FORCE_REBUILD_{stage}")
    if stage in {"hook", "reel"}:
        rdb.update(f"{key}_reel_v1", pipeline_status="pending", terminal=0, terminal_reason=f"FORCE_REBUILD_{stage}")
    return True, f"invalidated {stage} and downstream dependencies"

def reset_skipped(cfg, pdb, sdb, serial):
    entry = pdb.get_entry(serial)
    if not entry: return False
    key = f"{serial}_{entry.get('spotify_track_id') or 'unavailable'}"
    row = sdb.one("SELECT basename FROM songs WHERE song_key=?", (key,)) or {}
    if row.get("basename"):
        for target in (cfg.skipped_dir / f"{row['basename']}.mp3", cfg.skipped_dir / f"{row['basename']}.json"):
            target.unlink(missing_ok=True)
    pdb.set_status(serial, "pending", terminal=0, reason="RESET_SKIPPED_BY_OPERATOR")
    sdb.update_song(key, pipeline_status="pending", quality_status=None, terminal=0, terminal_reason="RESET_SKIPPED_BY_OPERATOR")
    return True


def print_status(cfg, pdb, sdb, rdb, playlist_id):
    playlist = pdb.get_playlist(playlist_id)
    if not playlist:
        print(f"Playlist {playlist_id} is not ingested.")
        return
    print(f"Playlist: {playlist_id}")
    print(f"Title: {playlist.get('display_name') or ''}")
    print(f"Snapshot entries: {playlist.get('entry_count') or 0}")
    print(f"Done: {bool(playlist.get('playlist_done'))}")
    for status, count in sorted(pdb.counts(playlist_id).items()): print(f"{status:12} {count}")
    print("DB integrity:")
    print(f"  vachika: {integrity_check(cfg.db_dir / 'vachika.db')}")


def print_review(cfg, pdb, sdb, rdb, serial):
    entry = pdb.get_entry(serial)
    if not entry: raise SystemExit(f"Song serial {serial} not found")
    key = f"{serial}_{entry.get('spotify_track_id') or 'unavailable'}"
    song = sdb.one("SELECT * FROM songs WHERE song_key=?", (key,)) or {}
    meta = sdb.one("SELECT * FROM metadata WHERE song_key=?", (key,)) or {}
    ytm = sdb.one("SELECT * FROM ytmusic_audio_matches WHERE song_key=?", (key,)) or {}
    youtube = sdb.one("SELECT * FROM youtube_matches WHERE song_key=?", (key,)) or {}
    lyrics = sdb.one("SELECT * FROM lyric_sources WHERE song_key=?", (key,)) or {}
    alignment = sdb.one("SELECT * FROM alignment_runs WHERE song_key=? ORDER BY created_at DESC LIMIT 1", (key,)) or {}
    eightd = sdb.one("SELECT * FROM audio_8d_runs WHERE song_key=? ORDER BY created_at DESC LIMIT 1", (key,)) or {}
    reel = rdb.one("SELECT * FROM reels WHERE reel_key=?", (f"{key}_reel_v1",)) or {}
    print(f"REVIEW #{serial}: {entry.get('title') or song.get('title') or 'Untitled'}")
    print(f"Spotify: {meta.get('spotify_track_id')} | ISRC={meta.get('spotify_isrc')} | artwork={meta.get('spotify_artwork_path')}")
    print(f"YTM audio: {ytm.get('selected_video_id')} | {ytm.get('selected_duration_ms')} ms | delta={ytm.get('duration_delta_ms')} ms")
    print(f"Visual YTM: {youtube.get('video_id')} | offset={youtube.get('offset_ms')} ms")
    print(f"LRC: {lyrics.get('status')} | synced={lyrics.get('synced')} | lines={lyrics.get('line_count')}")
    print(f"Alignment: {alignment.get('status')} | quality={alignment.get('quality_status')}")
    print(f"8D: {eightd.get('status')} | validation={eightd.get('validation_status')}")
    print(f"Reel: {reel.get('pipeline_status')} | {reel.get('final_reel_path')}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=None, help="Configuration file (defaults to config.json, or config.json.example when config.json is absent)")
    parser.add_argument("--doctor", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--load-playlist", action="store_true", help="Fetch and freeze the Spotify playlist only; do not process songs")
    parser.add_argument("--process-playlist", action="store_true", help="Process an already-frozen playlist; refuses to fetch a new snapshot")
    parser.add_argument("--single")
    parser.add_argument("--retry-errors", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--reconcile", action="store_true")
    parser.add_argument("--force-rebuild", nargs=2, metavar=("STAGE", "SONG"))
    parser.add_argument("--reset-skipped", metavar="SONG")
    parser.add_argument("--review", metavar="SONG")
    parser.add_argument("--backup-db", metavar="DIR")
    parser.add_argument("--restore-db", metavar="DIR")
    parser.add_argument("--check-db", "--migrate-db", dest="check_db", action="store_true",
                        help="Migrate any legacy stores into db/vachika.db, refresh db/songs.json, then verify integrity")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--prepare-models", "--download-models", dest="prepare_models", action="store_true",
                        help="Download/cache all pipeline ML models without processing songs")
    args = parser.parse_args()

    if args.load_playlist and args.process_playlist:
        parser.error("--load-playlist and --process-playlist are separate commands; run them in separate invocations")
    if args.prepare_models and (args.load_playlist or args.process_playlist or args.single or args.retry_errors or args.resume):
        parser.error("--download-models is a standalone preparation command; run it separately from playlist processing")

    print("Vachika: Spotify metadata -> LRCLIB lyrics -> YT Music audio + YouTube video search -> MMS-only LRCLIB offset -> Demucs/alignment/8D/Reel", flush=True)
    print("Loading configuration...", flush=True)
    config_arg = args.config
    if config_arg is None:
        default_config = Path(__file__).resolve().parent / "config.json"
        example_config = Path(__file__).resolve().parent / "config.json.example"
        if default_config.is_file():
            config_arg = default_config
        elif example_config.is_file():
            config_arg = example_config
            print("NOTICE: config.json is absent; using config.json.example for configuration defaults. Copy it to config.json and fill in credentials before processing.", flush=True)
    commands_not_requiring_playlist = any((
        args.prepare_models, args.doctor, args.dry_run, args.status,
        args.check_db, bool(args.backup_db), bool(args.restore_db),
    ))
    cfg = Config.load(config_arg, require_playlist=not commands_not_requiring_playlist)

    if args.prepare_models:
        from src.common.model_cache import prepare_models
        report = prepare_models(cfg)
        for name, item in report.get("models", {}).items():
            suffix = f": {item.get('error')}" if item.get("status") != "ready" else ""
            print(f"{name}: {item.get('status')}{suffix}")
        print(f"Model cache: {report.get('models_dir')}")
        models_dir = cfg.path('paths.models_dir') or (cfg.root / "models")
        print(f"Preparation manifest: {models_dir / 'preparation_manifest.json'}")
        return 0 if report.get("ok") else 2

    # Dry-run is deliberately read-only: it never creates a DB directory, scans the
    # Spotify API, writes snapshots, initializes schemas, or changes queue state.
    if args.dry_run:
        db_path = cfg.db_dir / "vachika.db" if cfg.db_dir else None
        if not db_path or not db_path.is_file():
            legacy = [name for name in ("playlist.db", "songs.db", "reels.db")
                      if cfg.db_dir and (cfg.db_dir / name).is_file()]
            if legacy:
                print("DRY RUN (read-only): legacy database files detected: " + ", ".join(legacy))
                print("No old store was read or changed. Run `python main.py --check-db` to migrate them into db/vachika.db.")
            else:
                print("DRY RUN (read-only): no frozen playlist database exists; no network calls or writes were made.")
            print(f"Configured playlist ID: {str(cfg.get('playlist.spotify_playlist_id') or '(not set)')}")
            return
        try:
            uri = db_path.resolve().as_uri() + "?mode=ro"
            with sqlite3.connect(uri, uri=True) as ro:
                ro.row_factory = sqlite3.Row
                playlist_id = str(cfg.get("playlist.spotify_playlist_id") or "").strip()
                rows = ro.execute(
                    "SELECT playlist_position,title,artist,spotify_track_id,status FROM playlist_entries "
                    "WHERE playlist_id=? AND spotify_track_id IS NOT NULL AND is_local=0 "
                    "AND track_type='track' AND status IN ('pending','error','needs_review') AND terminal=0 "
                    "ORDER BY playlist_position ASC",
                    (playlist_id,),
                ).fetchall()
        except sqlite3.Error as exc:
            print(f"DRY RUN (read-only): cannot inspect the frozen playlist database: {exc}")
            return 2
        if args.single:
            serial = _serial_arg(args.single)
            with sqlite3.connect(uri, uri=True) as ro:
                ro.row_factory = sqlite3.Row
                rows = ro.execute(
                    "SELECT playlist_position,title,artist,spotify_track_id,status FROM playlist_entries "
                    "WHERE playlist_id=? AND playlist_serial=? AND spotify_track_id IS NOT NULL "
                    "AND is_local=0 AND track_type='track' AND status IN ('pending','error','needs_review') AND terminal=0",
                    (playlist_id, serial),
                ).fetchall()
        print(f"DRY RUN (read-only): {len(rows)} actionable playlist entr(y/ies); no network calls or writes were made.")
        for row in rows:
            print(f"playlist-position={row['playlist_position']} | {row['title']} | {row['artist']} | Spotify={row['spotify_track_id']} | status={row['status']}")
        return

    # Diagnostics and database maintenance must not instantiate the live playlist
    # schema as a side effect (especially before a restore/backup operation).
    if args.doctor:
        raise SystemExit(0 if run_doctor(cfg) else 2)
    if args.backup_db:
        # Backups are always of the one authoritative DB. If this is an older
        # installation, migration/archive happens before the backup operation.
        pdb, sdb, rdb, migration = initialize_unified_database(cfg)
        sync_songs_json(cfg, pdb, sdb, rdb)
        if migration.get("imported"):
            print("Migrated legacy stores before backup:", ", ".join(migration["imported"]))
        print(f"Backup: {backup_databases(cfg.root, Path(args.backup_db), cfg.db_dir)}")
        return 0
    if args.restore_db:
        # Restore converts historical multi-database backups to vachika.db. Reinitialize
        # any current schema additions and rebuild the derived catalog from restored rows.
        restore_databases(cfg.root, Path(args.restore_db), cfg.db_dir)
        pdb, sdb, rdb, migration = initialize_unified_database(cfg)
        sync_songs_json(cfg, pdb, sdb, rdb)
        remaining = [name for name in ("playlist.db", "songs.db", "reels.db") if (cfg.db_dir / name).exists()]
        if remaining:
            raise SystemExit("DB_RESTORE_FAILED: non-unified active database files remain: " + ", ".join(remaining))
        print("Database restored to db/vachika.db; db/songs.json regenerated and integrity verified.")
        return 0
    if args.check_db:
        # Unlike the former implementation, this command performs the pending data
        # migration, rather than merely checking the integrity of whichever files exist.
        pdb, sdb, rdb, migration = initialize_unified_database(cfg)
        sync_songs_json(cfg, pdb, sdb, rdb)
        result = migrate_databases(cfg.root, cfg.db_dir)
        remaining = [name for name in ("playlist.db", "songs.db", "reels.db") if (cfg.db_dir / name).exists()]
        if remaining:
            raise SystemExit("DB_MIGRATION_FAILED: old active stores remain: " + ", ".join(remaining))
        result["active_database_count"] = 1
        catalog_path = (cfg.db_dir / "songs.json").resolve()
        try:
            result["catalog"] = catalog_path.relative_to(cfg.root.resolve()).as_posix()
        except ValueError:
            # A custom db_dir may legitimately live outside the project root. Report
            # the absolute path rather than failing after migration already succeeded.
            result["catalog"] = str(catalog_path)
        result["migrated_legacy_stores"] = migration.get("imported", [])
        print(json.dumps(result, indent=2))
        return 0 if result.get("vachika", "").lower() == "ok" else 2

    cfg.db_dir.mkdir(parents=True, exist_ok=True)
    pdb, sdb, rdb, db_migration = initialize_unified_database(cfg)
    # songs.json is a deterministic export of the authoritative DB; regenerate it
    # after initialization/migration so a restore or upgrade cannot leave it stale.
    sync_songs_json(cfg, pdb, sdb, rdb)
    if db_migration.get("imported"):
        print("Migrated legacy databases into db/vachika.db:", ", ".join(db_migration["imported"]), flush=True)
    playlist_id = str(cfg.get("playlist.spotify_playlist_id") or "").strip()
    existing_playlist = pdb.get_playlist(playlist_id) if playlist_id else None

    # Administrative/read-only commands never trigger a remote playlist scan.
    if args.status:
        if not existing_playlist:
            print(f"Playlist {playlist_id} is not ingested.")
            return
        print_status(cfg, pdb, sdb, rdb, playlist_id); return

    if args.reconcile or args.review or args.force_rebuild or args.reset_skipped:
        if not existing_playlist:
            raise SystemExit(f"Playlist {playlist_id} is not ingested; this administrative command requires an existing frozen snapshot.")
        if args.reconcile:
            print("Recovered:", Recovery(cfg, pdb, sdb, rdb, logger(cfg)).reconcile(playlist_id)); return
        if args.review:
            print_review(cfg, pdb, sdb, rdb, _serial_arg(args.review)); return
        if args.force_rebuild:
            ok, msg = invalidate_stage(cfg, pdb, sdb, rdb, args.force_rebuild[0], _serial_arg(args.force_rebuild[1])); print(msg); return 0 if ok else 2
        if args.reset_skipped:
            ok = reset_skipped(cfg, pdb, sdb, _serial_arg(args.reset_skipped)); print("Skipped song reset." if ok else "Song not found."); return 0 if ok else 2

    if not playlist_id or playlist_id == "YOUR_SPOTIFY_PLAYLIST_ID":
        raise SystemExit("Set playlist.spotify_playlist_id in config.json before normal processing. Use --download-models to cache ML models first, --doctor to inspect dependencies, or --dry-run for a read-only check.")

    log = logger(cfg)
    if args.load_playlist:
        result = playlist_snapshot(cfg, pdb, log)
        sync_songs_json(cfg, pdb, sdb, rdb)
        if result == "new":
            print("PLAYLIST LOAD COMPLETE. No songs were processed. Run python main.py --process-playlist when ready.", flush=True)
        else:
            print("PLAYLIST LOAD COMPLETE. Existing frozen snapshot retained; no refetch was performed.", flush=True)
        return 0

    if args.process_playlist:
        if not existing_playlist or existing_playlist.get("ingestion_status") != "done" or int(existing_playlist.get("entry_count") or 0) == 0:
            raise SystemExit("No committed Spotify playlist snapshot exists. Run python main.py --load-playlist first.")
        snapshot = "existing"
    else:
        # Backward-compatible behavior: a normal run snapshots a new playlist and exits;
        # a committed snapshot can proceed to song processing on later runs.
        snapshot = playlist_snapshot(cfg, pdb, log)
        if snapshot in {"new", "done"}:
            sync_songs_json(cfg, pdb, sdb, rdb)
            if snapshot == "new":
                print("FIRST SPOTIFY PLAYLIST SNAPSHOT COMPLETE", flush=True)
                print("No song was processed.", flush=True)
                print("Run python main.py --process-playlist to process the frozen playlist.", flush=True)
            return 0
    sync_songs_json(cfg, pdb, sdb, rdb)
    from src.orchestration.pipeline import Pipeline
    if args.retry_errors:
        changed = pdb.retry_errors(playlist_id); print(f"Retryable entries reset: {changed}")
    if args.resume:
        recovered = Recovery(cfg, pdb, sdb, rdb, log).reconcile(playlist_id)
        print(f"RESUME MODE: reconciled {len(recovered)} interrupted entries; stage/workspace checkpoints will be reused.", flush=True)
    pipeline = Pipeline(cfg, pdb, sdb, rdb, log)
    entries = pdb.actionable(playlist_id)
    if args.single:
        serial = _serial_arg(args.single); entries = [e for e in entries if int(e["playlist_serial"]) == serial]
        if not entries: raise SystemExit(f"Song serial {serial} is not actionable in the current frozen Spotify playlist.")
    for e in entries:
        print(f"\nPROCESSING playlist-position={e['playlist_position']} | {e['title']} | Spotify={e['spotify_track_id']}", flush=True)
        print("Spotify metadata → LRCLIB lyrics → YT Music reference audio → YouTube video search → MMS LRCLIB offset → Demucs → word-level LRC → 8D → hook → Reel", flush=True)
        status = pipeline.process_entry(e)
        sync_songs_json(cfg, pdb, sdb, rdb)
        print("RESULT:", status, flush=True)
        if args.single: break
    if args.single: return
    if pdb.mark_playlist_done_if_complete(playlist_id):
        print("\nPLAYLIST IS DONE", flush=True)
        print(f"Playlist {playlist_id} is complete. Update playlist.spotify_playlist_id in config.json for a new playlist.", flush=True)
    else:
        print("\nPLAYLIST NOT DONE", flush=True)
        print("The frozen Spotify snapshot remains saved; the next run resumes from the database without re-fetching the playlist.", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
