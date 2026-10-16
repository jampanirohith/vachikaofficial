"""Consistent backup, restore and integrity verification for Vachika databases.

New installations use one ``vachika.db`` file.  The old three-file format is
still recognized so an existing installation can be restored and migrated.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LEGACY_DB_NAMES = ("playlist", "songs", "reels")


def integrity_check(path: Path) -> str:
    try:
        with sqlite3.connect(path) as conn:
            row = conn.execute("PRAGMA integrity_check").fetchone()
        return str(row[0]) if row else "unknown"
    except sqlite3.DatabaseError as exc:
        # A restore command must be able to repair a corrupt active database.
        return f"error: {exc}"


def _copy_sqlite(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    src_conn = sqlite3.connect(src)
    dst_conn = sqlite3.connect(dst)
    try:
        src_conn.backup(dst_conn)
        dst_conn.commit()
    finally:
        dst_conn.close()
        src_conn.close()


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".tmp-{uuid.uuid4().hex}")
    try:
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def _current_sources(source_db_dir: Path) -> tuple[str, dict[str, Path]]:
    """Return only the unified operational database as a backup source.

    Legacy stores are migrated by the CLI before backup; the backup layer itself must
    never perpetuate a multi-database layout.
    """
    unified = source_db_dir / "vachika.db"
    if unified.is_file():
        return "unified", {"vachika": unified}
    legacy_found = [name for name in LEGACY_DB_NAMES if (source_db_dir / f"{name}.db").is_file()]
    if legacy_found:
        raise RuntimeError(
            "DB_NOT_UNIFIED: legacy stores are still present (" + ", ".join(legacy_found) + "). "
            "Run `python main.py --check-db` to migrate them into db/vachika.db before backup."
        )
    raise FileNotFoundError(f"No database found in {source_db_dir}; expected vachika.db")


def backup_databases(root: Path, out_dir: Path, db_dir: Path | None = None) -> Path:
    """Create a consistent backup; include catalog/migration manifests when available."""
    root, out_dir = Path(root), Path(out_dir)
    source_db_dir = Path(db_dir) if db_dir is not None else root / "db"
    kind, sources = _current_sources(source_db_dir)
    for name, src in sources.items():
        check = integrity_check(src)
        if check.lower() != "ok":
            raise RuntimeError(f"DB_BACKUP_FAILED: {src} integrity={check}")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    dest = out_dir / stamp
    dest.mkdir(parents=True, exist_ok=False)
    manifest: dict[str, Any] = {"created_at_utc": datetime.now(timezone.utc).isoformat(), "format": kind, "databases": {}, "files": {}}
    try:
        for name, src in sources.items():
            dst = dest / f"{name}.db"
            _copy_sqlite(src, dst)
            copied_check = integrity_check(dst)
            if copied_check.lower() != "ok":
                raise RuntimeError(f"DB_BACKUP_FAILED: copied database {dst} integrity={copied_check}")
            manifest["databases"][name] = {"path": dst.name, "integrity_check": copied_check}
        # The catalog is derived, but backing it up makes a restore immediately usable.
        # SQLite remains authoritative and can regenerate this file if absent.
        for filename in ("songs.json", "migration_manifest.json"):
            src = source_db_dir / filename
            if src.is_file():
                dst = dest / filename
                shutil.copy2(src, dst)
                manifest["files"][filename] = {"path": filename, "size_bytes": dst.stat().st_size}
        _write_json_atomic(dest / "manifest.json", manifest)
        return dest
    except Exception:
        shutil.rmtree(dest, ignore_errors=True)
        raise


def _convert_legacy_backup_to_unified(backup_dir: Path, target_dir: Path) -> tuple[Path, Path]:
    """Convert a pre-unification backup into a temporary unified DB without creating
    legacy DB files in the live database directory.
    """
    legacy_sources = {name: backup_dir / f"{name}.db" for name in LEGACY_DB_NAMES}
    missing = [path for path in legacy_sources.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError(
            "Backup is neither unified nor a complete legacy backup; missing: "
            + ", ".join(str(path) for path in missing)
        )
    for src in legacy_sources.values():
        check = integrity_check(src)
        if check.lower() != "ok":
            raise RuntimeError(f"DB_RESTORE_FAILED: legacy backup {src} integrity={check}")

    conversion_dir = target_dir / f".legacy-conversion-{uuid.uuid4().hex}"
    legacy_dir = conversion_dir / "legacy-inputs"
    legacy_dir.mkdir(parents=True, exist_ok=False)
    copied_sources: list[Path] = []
    try:
        for name, src in legacy_sources.items():
            dst = legacy_dir / f"{name}.db"
            _copy_sqlite(src, dst)
            if integrity_check(dst).lower() != "ok":
                raise RuntimeError(f"DB_RESTORE_FAILED: copied legacy source failed integrity check: {dst}")
            copied_sources.append(dst)

        unified = conversion_dir / "vachika.db"
        # These are table repositories bound to a single shared DB; none accepts a
        # separate file path anymore.
        from src.storage.playlist_db import PlaylistDB
        from src.storage.song_db import SongsDB
        from src.storage.reel_db import ReelsDB
        PlaylistDB(unified)
        SongsDB(unified)
        ReelsDB(unified)
        from src.phase2core.db import Database as Phase2Database
        phase2 = Phase2Database(unified, namespace="phase2_")
        phase2.close()

        from src.storage.unified_db import _import_legacy
        _import_legacy(unified, copied_sources)
        check = integrity_check(unified)
        if check.lower() != "ok":
            raise RuntimeError(f"DB_RESTORE_FAILED: converted unified DB integrity={check}")
        return conversion_dir, unified
    except Exception:
        shutil.rmtree(conversion_dir, ignore_errors=True)
        raise


def restore_databases(root: Path, backup_dir: Path, db_dir: Path | None = None) -> None:
    """Restore a backup while guaranteeing one active DB file: ``vachika.db``.

    Older backups containing playlist.db / songs.db / reels.db are converted in a
    temporary directory first. They are never installed as active files.
    """
    import os

    root, backup_dir = Path(root), Path(backup_dir)
    target_dir = Path(db_dir) if db_dir is not None else root / "db"
    if not backup_dir.is_dir():
        raise FileNotFoundError(f"DB_RESTORE_FAILED: backup directory not found: {backup_dir}")
    manifest_path = backup_dir / "manifest.json"
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise RuntimeError(f"DB_RESTORE_FAILED: invalid backup manifest: {exc}") from exc
    else:
        manifest = {}

    conversion_dir: Path | None = None
    unified_source = backup_dir / "vachika.db"
    if unified_source.is_file():
        source = unified_source
    else:
        conversion_dir, source = _convert_legacy_backup_to_unified(backup_dir, target_dir)

    check = integrity_check(source)
    if check.lower() != "ok":
        if conversion_dir:
            shutil.rmtree(conversion_dir, ignore_errors=True)
        raise RuntimeError(f"DB_RESTORE_FAILED: {source} integrity={check}")

    target_dir.mkdir(parents=True, exist_ok=True)
    # Create an online-backup rollback copy before replacing active state. Preserve raw
    # bytes and sidecars for corrupt active files, since those can be the only evidence
    # from which data recovery is possible.
    active_names = ("vachika", *LEGACY_DB_NAMES)
    active_paths = [target_dir / f"{name}.db" for name in active_names if (target_dir / f"{name}.db").is_file()]
    active_integrities = {active.name: integrity_check(active) for active in active_paths}
    if active_paths:
        backup_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        durable_backup = target_dir / "backups" / f"pre-restore-{backup_stamp}"
        durable_backup.mkdir(parents=True, exist_ok=False)
        for active in active_paths:
            dest = durable_backup / active.name
            if active_integrities[active.name].lower() == "ok":
                _copy_sqlite(active, dest)
            else:
                shutil.copy2(active, dest)
                for suffix in ("-wal", "-shm"):
                    sidecar = Path(str(active) + suffix)
                    if sidecar.is_file():
                        shutil.copy2(sidecar, Path(str(dest) + suffix))
        for filename in ("songs.json", "migration_manifest.json"):
            current = target_dir / filename
            if current.is_file():
                shutil.copy2(current, durable_backup / filename)
        _write_json_atomic(durable_backup / "manifest.json", {
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "format": "pre_restore_active_state",
            "reason": "durable rollback copy created before database restore",
            "databases": {
                path.stem: {
                    "path": path.name,
                    "integrity_check": active_integrities[path.name],
                    "backup_kind": "sqlite_backup" if active_integrities[path.name].lower() == "ok" else "raw_file_copy_corrupt",
                } for path in active_paths
            },
        })

    transaction_dir = target_dir / f".restore-{uuid.uuid4().hex}"
    transaction_dir.mkdir(parents=False, exist_ok=False)
    staged = transaction_dir / "vachika.db"
    previous = transaction_dir / "previous-vachika.db"
    replaced = False
    moved_old: dict[str, Path] = {}
    try:
        _copy_sqlite(source, staged)
        if integrity_check(staged).lower() != "ok":
            raise RuntimeError(f"DB_RESTORE_FAILED: staged database failed integrity check: {staged}")

        # Move all other active filenames out of db/ before installing the one DB.
        # Their durable snapshots above are retained even after the short transaction dir
        # is removed.
        for old_name in LEGACY_DB_NAMES:
            old_path = target_dir / f"{old_name}.db"
            if old_path.exists():
                saved = transaction_dir / f"retired-{old_path.name}"
                os.replace(old_path, saved)
                moved_old[old_name] = saved
                for suffix in ("-wal", "-shm"):
                    sidecar = Path(str(old_path) + suffix)
                    if sidecar.exists():
                        os.replace(sidecar, Path(str(saved) + suffix))

        target = target_dir / "vachika.db"
        if target.exists():
            os.replace(target, previous)
        os.replace(staged, target)
        replaced = True

        # songs.json is derived from SQLite and must be regenerated, never trusted as
        # independent state after a restore. The normal CLI does this immediately.
        (target_dir / "songs.json").unlink(missing_ok=True)
        backed_migration = backup_dir / "migration_manifest.json"
        active_migration = target_dir / "migration_manifest.json"
        if backed_migration.is_file() and "vachika" in manifest.get("databases", {}):
            shutil.copy2(backed_migration, active_migration)
        else:
            active_migration.unlink(missing_ok=True)

        if integrity_check(target).lower() != "ok":
            raise RuntimeError(f"DB_RESTORE_FAILED: active DB failed after restore: {target}")
    except Exception:
        target = target_dir / "vachika.db"
        if replaced:
            target.unlink(missing_ok=True)
        if previous.exists():
            os.replace(previous, target)
        for old_name, saved in moved_old.items():
            if saved.exists():
                os.replace(saved, target_dir / f"{old_name}.db")
                for suffix in ("-wal", "-shm"):
                    archived_sidecar = Path(str(saved) + suffix)
                    if archived_sidecar.exists():
                        os.replace(archived_sidecar, Path(str(target_dir / f"{old_name}.db") + suffix))
        raise
    finally:
        shutil.rmtree(transaction_dir, ignore_errors=True)
        if conversion_dir:
            shutil.rmtree(conversion_dir, ignore_errors=True)

def migrate_databases(root: Path, db_dir: Path | None = None) -> dict[str, str]:
    """Validate the one unified DB after ``initialize_unified_database`` migrated it."""
    root = Path(root)
    source_db_dir = Path(db_dir) if db_dir is not None else root / "db"
    unified = source_db_dir / "vachika.db"
    if not unified.is_file():
        legacy_found = [name for name in LEGACY_DB_NAMES if (source_db_dir / f"{name}.db").is_file()]
        if legacy_found:
            raise RuntimeError(
                "DB_NOT_UNIFIED: legacy stores remain (" + ", ".join(legacy_found) + "). "
                "Run `python main.py --check-db` to import them into vachika.db."
            )
        raise FileNotFoundError(f"No unified database found at {unified}")

    results: dict[str, str] = {"vachika": integrity_check(unified)}
    if results["vachika"].lower() != "ok":
        raise RuntimeError(f"DB_MIGRATION_FAILED: {unified} integrity={results['vachika']}")
    with sqlite3.connect(unified) as conn:
        tables = {str(r[0]) for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        required = {
            "playlists", "playlist_entries", "songs", "metadata", "reels",
            "phase2_songs", "phase2_chunks", "phase2_words", "phase2_processing_log",
            "phase2_runs", "youtube_matches", "youtube_offset_evidence",
        }
        missing = sorted(required - tables)
        if missing:
            results["schema_check"] = "missing: " + ", ".join(missing)
            raise RuntimeError("DB_MIGRATION_FAILED: unified DB is missing tables: " + ", ".join(missing))
        legacy_active = sorted(name for name in LEGACY_DB_NAMES if (source_db_dir / f"{name}.db").exists())
        if legacy_active:
            raise RuntimeError("DB_MIGRATION_FAILED: separate active database files remain: " + ", ".join(legacy_active))
        foreign_key_violations = conn.execute("PRAGMA foreign_key_check").fetchmany(10)
        if foreign_key_violations:
            raise RuntimeError(f"DB_MIGRATION_FAILED: foreign-key violations: {foreign_key_violations}")
    results["schema_check"] = "ok"
    results["foreign_key_check"] = "ok"
    results["active_database_count"] = "1"
    return results
