from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .db import Database
from .json_manager import JSONManager
from .utils import sha256_file


class RecoveryManager:
    def __init__(self, db: Database, stale_after_minutes: int = 30):
        self.db = db
        self.stale_after_minutes = int(stale_after_minutes)
        self.json_manager = JSONManager()

    def mark_stale_as_pending(self) -> int:
        rows = self.db.execute(
            "SELECT id, updated_at, pipeline_status FROM songs "
            "WHERE pipeline_status IN ('scanning','lyrics_loaded','isolating','isolated','chunking','chunked','aligning','aligned','merging','merged','validating','validated','embedding')"
        ).fetchall()
        count = 0
        for row in rows:
            raw = str(row["updated_at"] or "")
            try:
                ts = datetime.fromisoformat(raw.replace("Z", "+00:00").replace(" ", "T"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                age = (datetime.now(timezone.utc) - ts).total_seconds() / 60.0
            except Exception:
                age = self.stale_after_minutes + 1
            if age >= self.stale_after_minutes:
                self.db.set_song_status(int(row["id"]), pipeline_status="pending")
                self.db.log(int(row["id"]), "recovery", "WARN", "Stale processing state reset to pending")
                count += 1
        return count

    def reconcile(self, final_dir: Path) -> dict[str, int]:
        """Repair obvious DB/output mismatches without modifying source files."""
        final_dir = final_dir.resolve()
        repaired = 0
        reset = 0
        checked = 0
        for row in self.db.execute("SELECT * FROM songs ORDER BY id").fetchall():
            checked += 1
            basename = str(row["basename"])
            mp3 = final_dir / f"{basename}.mp3"
            lrc = final_dir / f"{basename}.lrc"
            js = final_dir / f"{basename}.json"
            if mp3.exists() and lrc.exists() and js.exists():
                try:
                    data = self.json_manager.load(js)
                    p2: dict[str, Any] = data.get("phase2", {})
                    inp = p2.get("input", {})
                    outputs = p2.get("outputs", {}) if isinstance(p2, dict) else {}
                    identity_ok = (
                        inp.get("mp3_sha256") == row["original_mp3_sha256"]
                        and inp.get("lrc_sha256") == row["original_lrc_sha256"]
                        and inp.get("json_sha256") == row["original_json_sha256"]
                        and outputs.get("mp3_sha256") == sha256_file(mp3)
                        and outputs.get("lrc_sha256") == sha256_file(lrc)
                    )
                    if identity_ok and self.json_manager.validate_content_hash(data):
                        self.db.set_song_status(
                            int(row["id"]),
                            pipeline_status="finished",
                            quality_status=str(p2.get("quality_status") or row["quality_status"] or "unknown"),
                            final_mp3_path=str(mp3), final_lrc_path=str(lrc), final_json_path=str(js),
                            final_mp3_sha256=sha256_file(mp3),
                            final_lrc_sha256=sha256_file(lrc),
                            final_json_sha256=sha256_file(js),
                        )
                        repaired += 1
                except Exception:
                    pass
            elif row["pipeline_status"] == "finished":
                self.db.set_song_status(
                    int(row["id"]), pipeline_status="pending", quality_status="unknown",
                    final_mp3_path=None, final_lrc_path=None, final_json_path=None,
                    final_mp3_sha256=None, final_lrc_sha256=None, final_json_sha256=None,
                )
                reset += 1
        return {"checked": checked, "repaired": repaired, "reset_missing_finished": reset}
