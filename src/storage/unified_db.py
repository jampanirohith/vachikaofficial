"""Creation and data-preserving migration for the consolidated operational database."""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.storage.playlist_db import PlaylistDB
from src.storage.song_db import SongsDB
from src.storage.reel_db import ReelsDB

LEGACY_DATABASES = ("playlist.db", "songs.db", "reels.db")


def _integrity(path: Path) -> str:
    with sqlite3.connect(path) as conn:
        return str(conn.execute("PRAGMA integrity_check").fetchone()[0])


def _tables(conn: sqlite3.Connection, schema: str = "main") -> set[str]:
    return {str(r[0]) for r in conn.execute(f"SELECT name FROM {schema}.sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}


def _import_legacy(target: Path, sources: list[Path]) -> dict[str, Any]:
    """Import legacy stores into the unified schema without active parallel DBs.

    Compatible columns are copied table-by-table. Integer ``id`` primary keys are
    reallocated to avoid cross-store collisions; the lyric-lines -> lyric-words FK is
    remapped along with them. Exact duplicate event rows are skipped on retry, making
    migration recoverable after an interrupted import.
    """
    present = [p for p in sources if p.is_file()]
    if not present:
        return {"imported": [], "tables": {}}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_dir = target.parent / "backups" / f"legacy-before-unification-{stamp}"
    backup_dir.mkdir(parents=True, exist_ok=False)

    backup_snapshots: dict[str, dict[str, Any]] = {}
    for source in present:
        if _integrity(source).lower() != "ok":
            raise RuntimeError(f"Cannot migrate corrupt legacy database: {source}")
        # Take a consistent SQLite online backup first; raw originals and any sidecars
        # are preserved separately after a successful import.
        snapshot = backup_dir / source.name
        source_conn = sqlite3.connect(source, timeout=60)
        snapshot_conn = sqlite3.connect(snapshot, timeout=60)
        try:
            source_conn.backup(snapshot_conn)
            snapshot_conn.commit()
        finally:
            snapshot_conn.close()
            source_conn.close()
        snapshot_integrity = _integrity(snapshot)
        if snapshot_integrity.lower() != "ok":
            raise RuntimeError(f"Legacy DB snapshot failed integrity check: {snapshot} ({snapshot_integrity})")
        backup_snapshots[source.name] = {
            "snapshot": str(snapshot.relative_to(target.parent)),
            "integrity": snapshot_integrity,
            "sha256": hashlib.sha256(snapshot.read_bytes()).hexdigest(),
        }

    counts: dict[str, int] = {}
    conn = sqlite3.connect(target, timeout=60)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        for idx, source in enumerate(present):
            alias = f"legacy_{idx}"
            conn.execute(f"ATTACH DATABASE ? AS {alias}", (str(source.resolve()),))
            try:
                source_tables = _tables(conn, alias)
                target_tables = _tables(conn, "main")
                id_maps: dict[str, dict[int, int]] = {}
                for table in sorted(source_tables):
                    if table == "schema_meta" or table not in target_tables:
                        continue
                    source_info = list(conn.execute(f"PRAGMA {alias}.table_info('{table}')"))
                    src_cols_all = [str(row[1]) for row in source_info]
                    target_info = list(conn.execute(f"PRAGMA main.table_info('{table}')"))
                    dst_cols = {str(row[1]) for row in target_info}
                    cols = [name for name in src_cols_all if name in dst_cols]
                    if not cols:
                        continue
                    rows = conn.execute(f'SELECT {",".join(chr(34)+c+chr(34) for c in cols)} FROM {alias}."{table}"').fetchall()
                    pk_info = {str(row[1]): (int(row[5]), str(row[2]).upper()) for row in source_info}
                    auto_id = (
                        "id" in cols and pk_info.get("id", (0, ""))[0] == 1
                        and pk_info.get("id", (0, ""))[1] == "INTEGER"
                    )
                    source_pk_map: dict[int, int] = {}
                    if auto_id:
                        id_maps[table] = source_pk_map
                    fk_info = list(conn.execute(f"PRAGMA {alias}.foreign_key_list('{table}')"))
                    inserted = 0
                    for row in rows:
                        payload = {cols[i]: row[i] for i in range(len(cols))}
                        old_id = int(payload["id"]) if auto_id and payload.get("id") is not None else None
                        if auto_id:
                            payload.pop("id", None)

                        # An offset from the retired envelope matcher must never become
                        # valid just because the destination already has the new columns.
                        if table == "youtube_matches":
                            current_method = str(payload.get("method_version") or "")
                            if current_method != "mms-lrclib-dual-audio-offset-v3":
                                for column in ("offset_ms", "offset_seconds", "match_score", "matched_word_count",
                                               "median_absolute_deviation_ms", "p90_p10_spread_ms"):
                                    if column in dst_cols:
                                        payload[column] = None
                                if "method_version" in dst_cols:
                                    payload["method_version"] = "legacy_offset_invalidated"
                                if "status" in dst_cols:
                                    payload["status"] = "superseded_legacy_offset"

                        # Remap foreign keys that point at an auto-increment ID copied
                        # earlier from this same legacy source (notably lyric_words.line_id).
                        for fk in fk_info:
                            from_col = str(fk[3] or "")
                            ref_table = str(fk[2] or "")
                            ref_col = str(fk[4] or "")
                            if not from_col or from_col not in payload or payload[from_col] is None:
                                continue
                            if not ref_col:
                                ref_info = list(conn.execute(f"PRAGMA {alias}.table_info('{ref_table}')"))
                                ref_pk = next((str(r[1]) for r in ref_info if int(r[5]) == 1), "")
                                ref_col = ref_pk
                            if ref_col == "id" and ref_table in id_maps:
                                try:
                                    payload[from_col] = id_maps[ref_table].get(int(payload[from_col]), payload[from_col])
                                except (TypeError, ValueError):
                                    pass

                        # For event/child tables with generated IDs, detect exact retries
                        # without relying on source integer IDs that may collide globally.
                        existing = None
                        if auto_id and payload:
                            if table == "lyric_lines" and {"alignment_run_id", "line_index"}.issubset(payload):
                                existing = conn.execute(
                                    'SELECT id FROM main."lyric_lines" WHERE alignment_run_id IS ? AND line_index IS ?',
                                    (payload["alignment_run_id"], payload["line_index"]),
                                ).fetchone()
                            else:
                                where = " AND ".join(f'"{name}" IS ?' for name in payload)
                                existing = conn.execute(
                                    f'SELECT id FROM main."{table}" WHERE {where} LIMIT 1', list(payload.values())
                                ).fetchone()
                        if existing is not None:
                            if old_id is not None:
                                source_pk_map[old_id] = int(existing[0])
                            continue

                        ins_cols = list(payload)
                        placeholders = ",".join("?" for _ in ins_cols)
                        quoted = ",".join('"' + name.replace('"', '""') + '"' for name in ins_cols)
                        before = conn.total_changes
                        if auto_id:
                            conn.execute(f'INSERT OR IGNORE INTO main."{table}" ({quoted}) VALUES ({placeholders})',
                                         [payload[name] for name in ins_cols])
                            changed = conn.total_changes > before
                            if changed:
                                new_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
                                inserted += 1
                            else:
                                # A non-identical natural-key conflict can be ignored by
                                # SQLite constraints; resolve by lyric-line identity if available.
                                found = None
                                if table == "lyric_lines" and {"alignment_run_id", "line_index"}.issubset(payload):
                                    found = conn.execute(
                                        'SELECT id FROM main."lyric_lines" WHERE alignment_run_id IS ? AND line_index IS ?',
                                        (payload["alignment_run_id"], payload["line_index"]),
                                    ).fetchone()
                                new_id = int(found[0]) if found else None
                            if old_id is not None and new_id is not None:
                                source_pk_map[old_id] = new_id
                        else:
                            conn.execute(f'INSERT OR IGNORE INTO main."{table}" ({quoted}) VALUES ({placeholders})',
                                         [payload[name] for name in ins_cols])
                            inserted += int(conn.total_changes > before)
                    if inserted:
                        counts[f"{source.name}:{table}"] = counts.get(f"{source.name}:{table}", 0) + inserted
                conn.commit()
            finally:
                if conn.in_transaction:
                    conn.rollback()
                conn.execute(f"DETACH DATABASE {alias}")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.commit()
        foreign_key_violations = conn.execute("PRAGMA main.foreign_key_check").fetchmany(10)
        if foreign_key_violations:
            raise RuntimeError(f"Legacy database import left foreign-key violations: {foreign_key_violations}")
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()

    if _integrity(target).lower() != "ok":
        raise RuntimeError("Unified DB failed PRAGMA integrity_check after legacy import")
    # Move the original database and sidecars into the rollback archive. The online
    # backup snapshots above are the canonical consistent restore copies.
    archived: list[str] = []
    archived_hashes: dict[str, str] = {}
    for source in present:
        for suffix in ("", "-wal", "-shm"):
            original = Path(str(source) + suffix)
            if not original.is_file():
                continue
            dest = backup_dir / f"active-{source.name}{suffix}"
            os.replace(original, dest)
            archived.append(str(dest.relative_to(target.parent)))
            archived_hashes[str(dest.relative_to(target.parent))] = hashlib.sha256(dest.read_bytes()).hexdigest()
    manifest = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_databases": [str(p.name) for p in present],
        "archived_legacy_files": archived,
        "archived_original_sha256": archived_hashes,
        "sqlite_backup_snapshots": backup_snapshots,
        "backup_directory": str(backup_dir.relative_to(target.parent)),
        "imported_row_counts": counts,
        "target_integrity": _integrity(target),
    }
    _write_json_atomic(backup_dir / "migration_manifest.json", manifest)
    return {"imported": [p.name for p in present], "tables": counts, "backup_dir": str(backup_dir)}


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _phase2_identity_for_legacy(work_root: Path, source: Path) -> str:
    """Return the same stable per-song identity used by the active adapter."""
    try:
        relative = source.relative_to(work_root)
        candidate = relative.parts[0] if relative.parts else source.parent.name
    except ValueError:
        candidate = source.parent.name
    import re
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(candidate))[:150] or "song"


def _import_one_legacy_phase2(source: Path, target_path: Path, stable_identity: str) -> dict[str, int]:
    """Import a legacy per-song Phase-2 database into namespaced unified tables.

    Row IDs in each legacy file restarted at 1, so all parent/child IDs are remapped.
    The old `master` basename becomes the stable song identity used by today's adapter.
    Column intersection keeps this migration forward/backward compatible with older
    Phase-2 schemas. Source DBs are only removed after this import and an online backup
    both pass integrity checks.
    """
    target_tables = {
        "songs": "phase2_songs",
        "chunks": "phase2_chunks",
        "words": "phase2_words",
        "instrumental_sections": "phase2_instrumental_sections",
        "processing_log": "phase2_processing_log",
        "runs": "phase2_runs",
    }
    counts = {name: 0 for name in target_tables}
    with sqlite3.connect(source, timeout=60) as src:
        src.row_factory = sqlite3.Row
        source_tables = _tables(src)
        if "songs" not in source_tables:
            # There is no Phase-2 state to import; callers still preserve an archive.
            return counts
        source_rows = [dict(row) for row in src.execute("SELECT * FROM songs ORDER BY id")]
        source_runs = [dict(row) for row in src.execute("SELECT * FROM runs ORDER BY id")] if "runs" in source_tables else []
        source_chunks = [dict(row) for row in src.execute("SELECT * FROM chunks ORDER BY id")] if "chunks" in source_tables else []
        source_words = [dict(row) for row in src.execute("SELECT * FROM words ORDER BY id")] if "words" in source_tables else []
        source_sections = [dict(row) for row in src.execute("SELECT * FROM instrumental_sections ORDER BY id")] if "instrumental_sections" in source_tables else []
        source_logs = [dict(row) for row in src.execute("SELECT * FROM processing_log ORDER BY id")] if "processing_log" in source_tables else []

    conn = sqlite3.connect(target_path, timeout=60)
    conn.row_factory = sqlite3.Row
    run_ids: dict[int, int] = {}
    song_ids: dict[int, int] = {}
    chunk_ids: dict[int, int] = {}
    reused_song_ids: set[int] = set()
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("BEGIN IMMEDIATE")
        for old in source_runs:
            old_id = old.get("id")
            payload = {k: v for k, v in old.items() if k != "id"}
            target_cols = {str(row[1]) for row in conn.execute("PRAGMA table_info(phase2_runs)")}
            payload = {k: v for k, v in payload.items() if k in target_cols}
            if not payload:
                continue
            cols = list(payload)
            cur = conn.execute(
                f"INSERT INTO phase2_runs ({','.join(chr(34)+c+chr(34) for c in cols)}) VALUES ({','.join('?' for _ in cols)})",
                [payload[c] for c in cols],
            )
            run_ids[int(old_id)] = int(cur.lastrowid)
            counts["runs"] += 1

        song_target_cols = {str(row[1]) for row in conn.execute("PRAGMA table_info(phase2_songs)")}
        for index, old in enumerate(source_rows):
            old_id = int(old.get("id") or index + 1)
            basename = stable_identity if index == 0 or old.get("basename") in {None, "", "master"} else f"{stable_identity}_{old.get('basename')}"
            # Keep basename unique without losing a conflicting legacy row.
            identity = basename
            existing = conn.execute("SELECT * FROM phase2_songs WHERE basename=?", (identity,)).fetchone()
            if existing is not None:
                hash_fields = ("original_mp3_sha256", "original_lrc_sha256", "original_json_sha256")
                if all(str(existing[k] or "") == str(old.get(k) or "") for k in hash_fields):
                    song_ids[old_id] = int(existing["id"])
                    # If the target already has chunks/words, keep its newer checkpoint
                    # state. Otherwise import the legacy child rows into this matching
                    # song so a partially initialized target cannot shadow good progress.
                    child_count = int(conn.execute(
                        "SELECT (SELECT COUNT(*) FROM phase2_chunks WHERE song_id=?) + "
                        "(SELECT COUNT(*) FROM phase2_words WHERE song_id=?)",
                        (int(existing["id"]), int(existing["id"])),
                    ).fetchone()[0])
                    if child_count:
                        reused_song_ids.add(old_id)
                    continue
                identity = f"{basename}_legacy_{hashlib.sha256(json.dumps(old, sort_keys=True, default=str).encode()).hexdigest()[:8]}"
                suffix = 1
                while conn.execute("SELECT 1 FROM phase2_songs WHERE basename=?", (identity,)).fetchone():
                    suffix += 1
                    identity = f"{basename}_legacy_{suffix}"
            payload = {k: v for k, v in old.items() if k != "id" and k in song_target_cols}
            payload["basename"] = identity
            if payload.get("run_id") is not None:
                payload["run_id"] = run_ids.get(int(payload["run_id"]), None)
            cols = list(payload)
            cur = conn.execute(
                f"INSERT INTO phase2_songs ({','.join(chr(34)+c+chr(34) for c in cols)}) VALUES ({','.join('?' for _ in cols)})",
                [payload[c] for c in cols],
            )
            song_ids[old_id] = int(cur.lastrowid)
            counts["songs"] += 1

        chunk_target_cols = {str(row[1]) for row in conn.execute("PRAGMA table_info(phase2_chunks)")}
        for old in source_chunks:
            old_id = int(old.get("id") or 0)
            old_song_id = int(old.get("song_id") or 0)
            if old_song_id not in song_ids or old_song_id in reused_song_ids:
                continue
            payload = {k: v for k, v in old.items() if k != "id" and k in chunk_target_cols}
            payload["song_id"] = song_ids[old_song_id]
            cols = list(payload)
            cur = conn.execute(
                f"INSERT INTO phase2_chunks ({','.join(chr(34)+c+chr(34) for c in cols)}) VALUES ({','.join('?' for _ in cols)})",
                [payload[c] for c in cols],
            )
            chunk_ids[old_id] = int(cur.lastrowid)
            counts["chunks"] += 1

        for table, rows, target_table, count_key in (
            ("words", source_words, "phase2_words", "words"),
            ("instrumental_sections", source_sections, "phase2_instrumental_sections", "instrumental_sections"),
            ("processing_log", source_logs, "phase2_processing_log", "processing_log"),
        ):
            target_cols = {str(row[1]) for row in conn.execute(f'PRAGMA table_info("{target_table}")')}
            for old in rows:
                old_song_id = old.get("song_id")
                if old_song_id is not None and int(old_song_id) not in song_ids:
                    continue
                if old_song_id is not None and int(old_song_id) in reused_song_ids:
                    continue
                payload = {k: v for k, v in old.items() if k != "id" and k in target_cols}
                if "song_id" in payload and payload["song_id"] is not None:
                    payload["song_id"] = song_ids[int(payload["song_id"])]
                if "chunk_id" in payload and payload["chunk_id"] is not None:
                    payload["chunk_id"] = chunk_ids.get(int(payload["chunk_id"]))
                cols = list(payload)
                if not cols:
                    continue
                conn.execute(
                    f"INSERT INTO {target_table} ({','.join(chr(34)+c+chr(34) for c in cols)}) VALUES ({','.join('?' for _ in cols)})",
                    [payload[c] for c in cols],
                )
                counts[count_key] += 1
        conn.execute("COMMIT")
        conn.execute("PRAGMA foreign_keys=ON")
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(f"Phase-2 import left foreign-key violations from {source}: {violations[:5]}")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        conn.close()
    return counts


def _archive_legacy_phase2_databases(cfg, unified_db_path: Path) -> list[dict[str, Any]]:
    """Import old Phase-2 checkpoints into vachika.db, then archive verified originals.

    The archive is retained for rollback/audit; the per-song file is removed from the
    active workspace only after imported rows and archive checks pass.
    """
    work_root = Path(cfg.work_dir)
    if not work_root.is_dir():
        return []
    matches = sorted(work_root.rglob("phase2.sqlite"))
    if not matches:
        return []
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    archive_root = Path(cfg.db_dir) / "backups" / f"legacy-phase2-{stamp}"
    archived: list[dict[str, Any]] = []
    for src in matches:
        rel = src.relative_to(work_root) if src.is_relative_to(work_root) else Path(hashlib.sha256(str(src).encode("utf-8")).hexdigest()[:12]) / src.name
        dest = archive_root / rel
        if _integrity(src).lower() != "ok":
            raise RuntimeError(f"PHASE2_IMPORT_FAILED: legacy DB integrity check failed: {src}")
        stable_identity = _phase2_identity_for_legacy(work_root, src)
        counts = _import_one_legacy_phase2(src, unified_db_path, stable_identity)
        dest.parent.mkdir(parents=True, exist_ok=True)
        source_conn = sqlite3.connect(src, timeout=60)
        target_conn = sqlite3.connect(dest, timeout=60)
        try:
            source_conn.backup(target_conn)
            target_conn.commit()
        finally:
            target_conn.close()
            source_conn.close()
        if _integrity(dest).lower() != "ok":
            dest.unlink(missing_ok=True)
            raise RuntimeError(f"PHASE2_IMPORT_FAILED: backup integrity check failed: {dest}")
        digest = hashlib.sha256(dest.read_bytes()).hexdigest()
        with sqlite3.connect(unified_db_path) as conn:
            conn.commit()
        if _integrity(unified_db_path).lower() != "ok":
            raise RuntimeError(f"PHASE2_IMPORT_FAILED: unified DB failed integrity check after importing {src}")
        src.unlink(missing_ok=True)
        Path(str(src) + "-wal").unlink(missing_ok=True)
        Path(str(src) + "-shm").unlink(missing_ok=True)
        archived.append({"source": str(src), "archive": str(dest), "sha256": digest,
                         "stable_song_identity": stable_identity, "imported_rows": counts})
    _write_json_atomic(archive_root / "manifest.json", {
        "schema_version": 2, "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "reason": "legacy Phase-2 checkpoint rows imported into db/vachika.db; originals archived after verification",
        "active_database": "db/vachika.db", "databases": archived,
    })
    return archived


def _backup_database_before_schema_cleanup(db_path: Path, backup_root: Path) -> Path:
    """Take a consistent rollback snapshot before dropping obsolete offset fields."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination_dir = backup_root / f"pre-mms-schema-cleanup-{stamp}"
    destination_dir.mkdir(parents=True, exist_ok=False)
    destination = destination_dir / "vachika.db"
    source_conn = sqlite3.connect(db_path, timeout=60)
    target_conn = sqlite3.connect(destination, timeout=60)
    try:
        source_conn.backup(target_conn)
        target_conn.commit()
    finally:
        target_conn.close()
        source_conn.close()
    check = _integrity(destination)
    if check.lower() != "ok":
        shutil.rmtree(destination_dir, ignore_errors=True)
        raise RuntimeError(f"SCHEMA_CLEANUP_BACKUP_FAILED: backup integrity={check}: {destination}")
    manifest = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "reason": "pre-cleanup snapshot before removing retired energy-envelope offset schema",
        "database": str(destination.relative_to(db_path.parent)),
        "integrity_check": check,
        "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
    }
    _write_json_atomic(destination_dir / "manifest.json", manifest)
    return destination


def _migrate_obsolete_offset_schema(db_path: Path, backup_root: Path) -> dict[str, Any]:
    """Remove retired energy-envelope columns/table and invalidate persisted old offsets.

    Old values are preserved in a verified pre-migration database backup. An offset from
    the retired algorithm must not remain marked valid in the active song metadata.
    """
    with sqlite3.connect(db_path, timeout=60) as conn:
        tables = {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "youtube_matches" not in tables:
            return {"changed": False, "reason": "youtube_matches table absent"}
        columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(youtube_matches)")}
        retired_columns = {"waveform_score", "energy_score", "peak_valley_score", "transition_score", "max_accepted_offset_ms"}
        legacy_candidates_present = "youtube_offset_candidates" in tables
        new_columns = {"matched_word_count", "median_absolute_deviation_ms", "p90_p10_spread_ms", "youtube_duration_ms", "status"}
        if not (columns & retired_columns or legacy_candidates_present or not new_columns.issubset(columns)):
            return {"changed": False, "reason": "current MMS-only schema already present"}

    backup = _backup_database_before_schema_cleanup(db_path, backup_root)
    conn = sqlite3.connect(db_path, timeout=60)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("BEGIN IMMEDIATE")
        tables = {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(youtube_matches)")}
        if "youtube_matches" in tables:
            old_name = "youtube_matches_pre_mms_cleanup"
            if old_name in tables:
                conn.execute(f'DROP TABLE "{old_name}"')
            conn.execute(f'ALTER TABLE youtube_matches RENAME TO "{old_name}"')
            conn.execute("""CREATE TABLE youtube_matches(
                song_key TEXT PRIMARY KEY,
                video_id TEXT, video_url TEXT, video_title TEXT, selection_query TEXT,
                selection_rule TEXT NOT NULL, audio_path TEXT, audio_sha256 TEXT,
                offset_ms INTEGER, offset_seconds REAL, match_score REAL,
                matched_word_count INTEGER, median_absolute_deviation_ms REAL, p90_p10_spread_ms INTEGER,
                reference_duration_ms INTEGER NOT NULL DEFAULT 0, youtube_duration_ms INTEGER NOT NULL DEFAULT 0,
                method_version TEXT, status TEXT, selected_at TEXT,
                FOREIGN KEY(song_key) REFERENCES songs(song_key) ON DELETE CASCADE
            )""")
            old_columns = {str(row[1]) for row in conn.execute(f'PRAGMA table_info("{old_name}")')}
            carry = [name for name in ("song_key", "video_id", "video_url", "video_title", "selection_query", "selection_rule", "audio_path", "audio_sha256", "selected_at") if name in old_columns]
            # Source identity is useful, but old offsets/scores are intentionally discarded.
            if carry:
                expressions = []
                target_columns = []
                for name in carry:
                    expressions.append(f'"{name}"'); target_columns.append(name)
                for name, expression in (
                    ("offset_ms", "NULL"), ("offset_seconds", "NULL"), ("match_score", "NULL"),
                    ("matched_word_count", "NULL"), ("median_absolute_deviation_ms", "NULL"),
                    ("p90_p10_spread_ms", "NULL"), ("reference_duration_ms", "0"),
                    ("youtube_duration_ms", "0"), ("method_version", "'legacy_offset_invalidated'"),
                    ("status", "'superseded_legacy_offset'"),
                ):
                    target_columns.append(name); expressions.append(expression)
                conn.execute(
                    f'INSERT OR IGNORE INTO youtube_matches({",".join(target_columns)}) '
                    f'SELECT {",".join(expressions)} FROM "{old_name}"'
                )
            conn.execute(f'DROP TABLE "{old_name}"')

        if "youtube_offset_candidates" in tables:
            conn.execute("DROP TABLE youtube_offset_candidates")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_youtube_evidence_song ON youtube_offset_evidence(song_key,line_index,word_index)")
        conn.execute("CREATE TABLE IF NOT EXISTS unified_schema_meta (key TEXT PRIMARY KEY,value TEXT NOT NULL)")
        conn.execute("INSERT OR REPLACE INTO unified_schema_meta(key,value) VALUES('schema_version','2')")
        conn.execute("COMMIT")
        conn.execute("PRAGMA foreign_keys=ON")
    except Exception:
        try: conn.execute("ROLLBACK")
        except sqlite3.Error: pass
        raise
    finally:
        conn.close()
    check = _integrity(db_path)
    if check.lower() != "ok":
        raise RuntimeError(f"MMS_OFFSET_SCHEMA_MIGRATION_FAILED: unified DB integrity={check}")
    return {"changed": True, "backup": str(backup), "integrity_check": check,
            "retired_table_removed": legacy_candidates_present}


def initialize_unified_database(cfg) -> tuple[PlaylistDB, SongsDB, ReelsDB, dict[str, Any]]:
    """Initialize all legacy APIs on one DB file, importing the old three stores once."""
    cfg.db_dir.mkdir(parents=True, exist_ok=True)
    main_path = cfg.db_dir / "vachika.db"
    source_paths = [cfg.db_dir / x for x in LEGACY_DATABASES]
    # Every repository is bound to the same physical database; no domain store can
    # initialize a separate path.
    pdb = PlaylistDB(main_path)
    sdb = SongsDB(main_path)
    rdb = ReelsDB(main_path)
    migration = {"imported": [], "tables": {}}
    manifest_path = cfg.db_dir / "migration_manifest.json"
    existing_manifest: dict[str, Any] = {}
    manifest_existed = manifest_path.is_file()
    if manifest_existed:
        try:
            parsed_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(parsed_manifest, dict):
                existing_manifest = parsed_manifest
        except Exception:
            # Preserve a malformed manifest in a sidecar before regenerating it.
            malformed_backup = manifest_path.with_name(
                f"migration_manifest.corrupt-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}.json"
            )
            shutil.copy2(manifest_path, malformed_backup)
    legacy_present = any(p.is_file() for p in source_paths)
    if legacy_present:
        migration = _import_legacy(main_path, source_paths)
    # Phase-2 live checkpoints share the same file with a table namespace. No active
    # per-song phase2.sqlite databases are allowed after migration.
    from src.phase2core.db import Database as Phase2Database
    p2 = Phase2Database(main_path, namespace="phase2_")
    p2.close()
    offset_schema_migration = _migrate_obsolete_offset_schema(main_path, cfg.db_dir / "backups")
    if offset_schema_migration.get("changed"):
        migration["offset_schema_migration"] = offset_schema_migration
    archived_phase2 = _archive_legacy_phase2_databases(cfg, main_path)
    if archived_phase2:
        migration["legacy_phase2_import"] = archived_phase2
    if archived_phase2 or offset_schema_migration.get("changed") or legacy_present or not manifest_existed or int(existing_manifest.get("schema_version", 0) or 0) < 3:
        history = list(existing_manifest.get("migration_history") or [])
        record: dict[str, Any] = {"created_at_utc": datetime.now(timezone.utc).isoformat()}
        if legacy_present:
            record["legacy_database_import"] = migration
        if offset_schema_migration.get("changed"):
            record["offset_schema_migration"] = offset_schema_migration
        if archived_phase2:
            record["legacy_phase2_import"] = archived_phase2
        if legacy_present or offset_schema_migration.get("changed") or archived_phase2:
            history.append(record)
        payload = dict(existing_manifest)
        payload.update({"schema_version": 3, "updated_at_utc": datetime.now(timezone.utc).isoformat(),
                        "database": "vachika.db", "migration_history": history})
        if record.keys() - {"created_at_utc"}:
            payload["last_migration"] = record
        elif not payload.get("created_at_utc"):
            payload["created_at_utc"] = datetime.now(timezone.utc).isoformat()
        _write_json_atomic(manifest_path, payload)
    with sqlite3.connect(main_path) as conn:
        check = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if str(check).lower() != "ok":
            raise RuntimeError(f"UNIFIED_DB_INTEGRITY_FAILED: {check}")
        conn.execute("CREATE TABLE IF NOT EXISTS unified_schema_meta (key TEXT PRIMARY KEY,value TEXT NOT NULL)")
        conn.execute("INSERT OR REPLACE INTO unified_schema_meta(key,value) VALUES('schema_name','vachika_unified'),('schema_version','3')")
    return pdb, sdb, rdb, migration
