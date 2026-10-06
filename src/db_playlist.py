from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

TERMINAL_STATUSES = {"completed", "skipped", "duplicate"}
VALID_STATUSES = {"pending", "processing", "completed", "skipped", "duplicate", "error", "needs_review"}


class PlaylistDB:
    """Durable, immutable-in-order Spotify playlist snapshot database."""

    SCHEMA_NAME = "spotify_playlist_snapshot"
    SCHEMA_VERSION = "4.0.0"
    DB_USER_VERSION = 400

    def __init__(self, path, schema_path=None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.schema_path = Path(schema_path) if schema_path else Path(__file__).resolve().parent.parent / "database" / "playlist_schema.sql"
        self.initialize()

    def connect(self):
        c = sqlite3.connect(self.path, timeout=60)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA busy_timeout=60000")
        c.execute("PRAGMA synchronous=FULL")
        return c

    def _schema_identity(self):
        if not self.path.exists() or self.path.stat().st_size == 0:
            return None, None
        try:
            with self.connect() as c:
                rows = dict(c.execute("SELECT key,value FROM schema_meta WHERE key IN ('schema_name','schema_version')").fetchall())
                return rows.get('schema_name'), rows.get('schema_version')
        except sqlite3.DatabaseError:
            return None, None

    def _quarantine_legacy(self):
        legacy = self.path.with_name(self.path.stem + ".legacy.db")
        idx = 1
        while legacy.exists():
            legacy = self.path.with_name(self.path.stem + f".legacy-{idx}.db")
            idx += 1
        self.path.replace(legacy)
        return legacy

    def initialize(self):
        existing_name, existing_version = self._schema_identity()
        if existing_name and (existing_name != self.SCHEMA_NAME or existing_version != self.SCHEMA_VERSION):
            self._quarantine_legacy()
        elif self.path.exists() and self.path.stat().st_size > 0 and not existing_name:
            # A non-conforming/unknown DB must never be silently mutated into the new schema.
            self._quarantine_legacy()
        with self.connect() as c:
            c.executescript(self.schema_path.read_text(encoding="utf-8"))
            c.execute(f"PRAGMA user_version={self.DB_USER_VERSION}")
            c.commit()

    def one(self, q: str, args=()):
        with self.connect() as c:
            r = c.execute(q, args).fetchone()
            return dict(r) if r else None

    def get_playlist(self, playlist_id: str):
        return self.one("SELECT * FROM playlists WHERE playlist_id=?", (playlist_id,))

    def get_entry(self, serial: int):
        return self.one("SELECT * FROM playlist_entries WHERE playlist_serial=?", (int(serial),))

    def all_entries(self, playlist_id: str):
        with self.connect() as c:
            return [dict(r) for r in c.execute(
                "SELECT * FROM playlist_entries WHERE playlist_id=? ORDER BY playlist_position ASC",
                (playlist_id,),
            )]

    def actionable(self, playlist_id: str):
        with self.connect() as c:
            return [dict(r) for r in c.execute(
                """SELECT * FROM playlist_entries
                   WHERE playlist_id=? AND spotify_track_id IS NOT NULL
                     AND is_local=0 AND track_type='track'
                     AND status IN ('pending','error','needs_review') AND terminal=0
                   ORDER BY playlist_position ASC""",
                (playlist_id,),
            )]

    def set_status(self, serial, status, error_code=None, error_message=None, terminal=None, reason=None):
        if status not in VALID_STATUSES:
            raise ValueError(status)
        if terminal is None:
            terminal = int(status in TERMINAL_STATUSES)
        with self.connect() as c:
            c.execute(
                """UPDATE playlist_entries
                   SET status=?,terminal=?,terminal_reason=?,error_code=?,error_message=?,updated_at=CURRENT_TIMESTAMP
                   WHERE playlist_serial=?""",
                (status, int(terminal), reason, error_code, error_message, int(serial)),
            )
            c.commit()

    def audit(self, playlist_id, event, message="", serial=None):
        with self.connect() as c:
            c.execute(
                "INSERT INTO playlist_audit_events(playlist_id,playlist_serial,event_type,message) VALUES(?,?,?,?)",
                (playlist_id, serial, event, message),
            )
            c.commit()

    def ingest_snapshot(self, pid, title, url, snapshot_id, items: Iterable[dict[str, Any]], raw_playlist=None):
        if self.get_playlist(pid):
            raise RuntimeError(f"PLAYLIST_ALREADY_SNAPSHOTTED: {pid}")
        items = list(items)
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            c.execute(
                """INSERT INTO playlists(
                    playlist_id,provider,playlist_url,display_name,owner_display_name,owner_id,snapshot_id,
                    snapshot_scanned_once,ingestion_status,ingestion_started_at
                ) VALUES(?,?,?,?,?,?,?,1,'running',CURRENT_TIMESTAMP)""",
                (pid, "spotify", url, title, None, None, snapshot_id),
            )
            for serial, item in enumerate(items, 1):
                raw = json.dumps(item.get("raw_item_json", item), ensure_ascii=False, sort_keys=True, default=str)
                is_track = item.get("track_type") == "track" and bool(item.get("spotify_track_id")) and not bool(item.get("is_local"))
                status = "pending" if is_track else "skipped"
                terminal_reason = None if is_track else (item.get("terminal_reason") or "UNPROCESSABLE_PLAYLIST_ITEM")
                terminal = 0 if is_track else 1
                c.execute(
                    """INSERT INTO playlist_entries(
                        playlist_serial,playlist_id,playlist_position,added_at,added_by_user_id,added_by_user_name,
                        spotify_track_id,spotify_uri,spotify_url,title,artist,album,spotify_duration_ms,spotify_isrc,
                        track_type,is_local,status,terminal,terminal_reason,raw_item_json
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        serial, pid, int(item["playlist_position"]), item.get("added_at"), item.get("added_by_user_id"), item.get("added_by_user_name"),
                        item.get("spotify_track_id"), item.get("spotify_uri"), item.get("spotify_url"), item.get("title"),
                        item.get("artist"), item.get("album"), item.get("duration_ms"), item.get("isrc"), item.get("track_type"),
                        int(bool(item.get("is_local"))), status, terminal, terminal_reason, raw,
                    ),
                )
            raw_payload = raw_playlist if raw_playlist is not None else {"id": pid, "name": title, "snapshot_id": snapshot_id}
            c.execute(
                "INSERT INTO playlist_snapshots(playlist_id,snapshot_id,raw_playlist_json,fetched_entry_count) VALUES(?,?,?,?)",
                (pid, snapshot_id, json.dumps(raw_payload, ensure_ascii=False, sort_keys=True, default=str), len(items)),
            )
            c.execute(
                """UPDATE playlists SET ingestion_status='done',ingestion_completed_at=CURRENT_TIMESTAMP,entry_count=? WHERE playlist_id=?""",
                (len(items), pid),
            )
            c.execute(
                "INSERT INTO playlist_audit_events(playlist_id,event_type,message) VALUES(?,?,?)",
                (pid, "snapshot_committed", f"Frozen Spotify playlist snapshot committed in exact source order: {len(items)} entries"),
            )
            c.execute("COMMIT")
        return {"entry_count": len(items)}

    def mark_playlist_done_if_complete(self, pid: str):
        rows = self.all_entries(pid)
        if not rows:
            return False
        done = all(int(r["terminal"]) == 1 for r in rows) and not any(r["status"] in {"error", "needs_review", "processing"} for r in rows)
        if done:
            with self.connect() as c:
                c.execute(
                    """UPDATE playlists SET playlist_done=1,
                       completed_count=(SELECT COUNT(*) FROM playlist_entries WHERE playlist_id=? AND status='completed'),
                       skipped_count=(SELECT COUNT(*) FROM playlist_entries WHERE playlist_id=? AND status='skipped'),
                       duplicate_count=(SELECT COUNT(*) FROM playlist_entries WHERE playlist_id=? AND status='duplicate'),
                       error_count=(SELECT COUNT(*) FROM playlist_entries WHERE playlist_id=? AND status IN ('error','needs_review'))
                       WHERE playlist_id=?""",
                    (pid, pid, pid, pid, pid),
                )
                c.commit()
        return done

    def retry_errors(self, pid: str):
        with self.connect() as c:
            c.execute(
                """UPDATE playlist_entries
                   SET status='pending',terminal=0,terminal_reason=NULL,error_code=NULL,error_message=NULL,updated_at=CURRENT_TIMESTAMP
                   WHERE playlist_id=? AND status IN ('error','needs_review')""",
                (pid,),
            )
            changed = c.rowcount
            c.commit()
            return changed

    def counts(self, pid: str):
        with self.connect() as c:
            return {str(r["status"]): int(r["c"]) for r in c.execute(
                "SELECT status,COUNT(*) c FROM playlist_entries WHERE playlist_id=? GROUP BY status", (pid,)
            )}
