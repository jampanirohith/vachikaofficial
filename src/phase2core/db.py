from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .utils import now_iso

SCHEMA_VERSION = 1


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, timeout=60, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA busy_timeout=60000")
        self.initialize()

    def close(self) -> None:
        self.conn.close()

    def initialize(self) -> None:
        self.conn.executescript("CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);")
        current = self.conn.execute("SELECT value FROM schema_meta WHERE key='version'").fetchone()
        if current is None:
            schema = Path(__file__).resolve().parent.parent / "sql" / "001_initial.sql"
            if not schema.is_file():
                raise FileNotFoundError(f"Phase-2 database schema 001_initial.sql not found: {schema}")
            self.conn.executescript(schema.read_text(encoding="utf-8"))
            self.conn.execute("INSERT OR REPLACE INTO schema_meta(key,value) VALUES('version',?)", (str(SCHEMA_VERSION),))
        elif int(current["value"]) != SCHEMA_VERSION:
            raise RuntimeError(f"Unsupported DB schema version: {current['value']} expected {SCHEMA_VERSION}")

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield self.conn
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, params)

    def executemany(self, sql: str, seq: list[tuple[Any, ...]]) -> None:
        self.conn.executemany(sql, seq)

    def get_song(self, song_id: int) -> sqlite3.Row | None:
        return self.execute("SELECT * FROM songs WHERE id=?", (song_id,)).fetchone()

    def get_song_by_basename(self, basename: str) -> sqlite3.Row | None:
        return self.execute("SELECT * FROM songs WHERE basename=?", (basename,)).fetchone()

    def upsert_song(self, values: dict[str, Any]) -> int:
        row = self.get_song_by_basename(values["basename"])
        if row is None:
            cols = list(values)
            cur = self.execute(
                f"INSERT INTO songs ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",
                tuple(values[c] for c in cols),
            )
            return int(cur.lastrowid)

        source_changed = any(
            row[key] != values.get(key)
            for key in ("original_mp3_sha256", "original_lrc_sha256", "original_json_sha256")
        )
        assignments = [f"{c}=?" for c in values if c != "basename"]
        params = [values[c] for c in values if c != "basename"]
        if source_changed:
            assignments += [
                "final_mp3_path=NULL", "final_lrc_path=NULL", "final_json_path=NULL",
                "final_mp3_sha256=NULL", "final_lrc_sha256=NULL", "final_json_sha256=NULL",
                "pipeline_status='pending'", "quality_status='unknown'",
                "retry_count=0", "error_code=NULL", "error_remark=NULL",
                "completed_at=NULL",
            ]
        assignments.append("updated_at=CURRENT_TIMESTAMP")
        params.append(values["basename"])
        self.execute(f"UPDATE songs SET {','.join(assignments)} WHERE basename=?", tuple(params))
        if source_changed:
            self.clear_song_children(int(row["id"]))
        return int(row["id"])

    def set_song_status(self, song_id: int, pipeline_status: str | None = None,
                        quality_status: str | None = None, **fields: Any) -> None:
        assignments: list[str] = []
        params: list[Any] = []
        if pipeline_status is not None:
            assignments.append("pipeline_status=?"); params.append(pipeline_status)
        if quality_status is not None:
            assignments.append("quality_status=?"); params.append(quality_status)
        for key, value in fields.items():
            assignments.append(f"{key}=?"); params.append(value)
        assignments.append("updated_at=CURRENT_TIMESTAMP")
        params.append(song_id)
        self.execute(f"UPDATE songs SET {','.join(assignments)} WHERE id=?", tuple(params))

    def reset_for_reprocess(self, song_id: int) -> None:
        self.clear_song_children(song_id)
        self.set_song_status(
            song_id, pipeline_status='pending', quality_status='unknown',
            final_mp3_path=None, final_lrc_path=None, final_json_path=None,
            final_mp3_sha256=None, final_lrc_sha256=None, final_json_sha256=None,
            retry_count=0, error_code=None, error_remark=None, completed_at=None,
        )

    def clear_song_children(self, song_id: int) -> None:
        with self.transaction():
            self.execute("DELETE FROM words WHERE song_id=?", (song_id,))
            self.execute("DELETE FROM chunks WHERE song_id=?", (song_id,))
            self.execute("DELETE FROM instrumental_sections WHERE song_id=?", (song_id,))

    def add_chunk(self, song_id: int, values: dict[str, Any]) -> int:
        fields = {"song_id": song_id, **values}
        cols = list(fields)
        cur = self.execute(
            f"INSERT INTO chunks ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",
            tuple(fields[c] for c in cols),
        )
        return int(cur.lastrowid)

    def update_chunk(self, chunk_id: int, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = now_iso()
        assignments = ",".join(f"{k}=?" for k in fields)
        self.execute(f"UPDATE chunks SET {assignments} WHERE id=?", tuple(fields.values()) + (chunk_id,))

    def get_chunks(self, song_id: int) -> list[sqlite3.Row]:
        return list(self.execute('SELECT * FROM chunks WHERE song_id=? ORDER BY chunk_index', (song_id,)).fetchall())

    def get_words(self, song_id: int) -> list[sqlite3.Row]:
        return list(self.execute('SELECT * FROM words WHERE song_id=? ORDER BY line_index, word_index', (song_id,)).fetchall())

    def get_instrumental_sections(self, song_id: int) -> list[sqlite3.Row]:
        return list(self.execute('SELECT * FROM instrumental_sections WHERE song_id=? ORDER BY start_ms', (song_id,)).fetchall())

    def add_words(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        cols = list(rows[0])
        self.executemany(
            f"INSERT INTO words ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",
            [tuple(row[c] for c in cols) for row in rows],
        )

    def add_instrumental_sections(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        cols = list(rows[0])
        self.executemany(
            f"INSERT INTO instrumental_sections ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",
            [tuple(row[c] for c in cols) for row in rows],
        )

    def log(self, song_id: int | None, step: str, level: str, message: str,
            attempt: int = 1, metadata: dict[str, Any] | None = None) -> None:
        self.execute(
            "INSERT INTO processing_log(song_id,step,level,attempt,message,metadata_json) VALUES(?,?,?,?,?,?)",
            (song_id, step, level, attempt, message,
             json.dumps(metadata, ensure_ascii=False, sort_keys=True, default=str) if metadata else None),
        )

    def create_run(self, values: dict[str, Any]) -> int:
        cols = list(values)
        cur = self.execute(
            f"INSERT INTO runs ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",
            tuple(values[c] for c in cols),
        )
        return int(cur.lastrowid)

    def finish_run(self, run_id: int) -> None:
        self.execute("UPDATE runs SET finished_at=CURRENT_TIMESTAMP WHERE id=?", (run_id,))

    def recoverable_songs(self) -> list[sqlite3.Row]:
        return list(self.execute(
            "SELECT * FROM songs WHERE pipeline_status != 'finished' ORDER BY id"
        ).fetchall())

    def songs_by_quality(self, quality: str) -> list[sqlite3.Row]:
        return list(self.execute("SELECT * FROM songs WHERE quality_status=? ORDER BY id", (quality,)).fetchall())

    def backup(self, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        target = sqlite3.connect(destination)
        with target:
            self.conn.backup(target)
        target.close()
