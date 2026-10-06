from pathlib import Path


def test_parent_song_exists_before_stage_and_audit(tmp_path):
    from src.db_songs import SongsDB
    db = SongsDB(tmp_path / "songs.db", Path(__file__).resolve().parents[1] / "database" / "songs_schema.sql")
    key = "1_spotify123"
    db.ensure_song({"song_key": key, "playlist_serial": 1, "spotify_track_id": "spotify123", "title": "X", "artist": "A", "pipeline_status": "PROCESSING"})
    rid = db.stage_start(key, "spotify_metadata", "fp1", tmp_path / "x.json")
    db.audit(key, "song_failed", "spotify_metadata", "error", "test")
    assert db.one("SELECT song_key FROM songs WHERE song_key=?", (key,))["song_key"] == key
    assert db.one("SELECT run_id FROM stage_runs WHERE run_id=?", (rid,))["run_id"] == rid
    assert db.one("SELECT song_key FROM audit_events WHERE song_key=?", (key,))["song_key"] == key


def test_stage_and_audit_failures_are_not_second_fk_errors(tmp_path):
    from src.db_songs import SongsDB
    db = SongsDB(tmp_path / "songs.db", Path(__file__).resolve().parents[1] / "database" / "songs_schema.sql")
    key = "2_spotify456"
    db.ensure_song({"song_key": key, "playlist_serial": 2, "spotify_track_id": "spotify456", "pipeline_status": "PROCESSING"})
    rid = db.stage_start(key, "spotify_metadata", "fp2")
    db.stage_finish(rid, "error", error_message="SPOTIFY_AUTH_REQUIRED")
    db.audit(key, "song_failed", "pipeline", "error", "Spotify auth missing", "SPOTIFY_AUTH_REQUIRED")
    assert db.one("SELECT status FROM stage_runs WHERE run_id=?", (rid,))["status"] == "error"


def test_pipeline_bootstraps_song_before_spotify_stage(tmp_path, monkeypatch):
    from src.config import Config
    from src.db_playlist import PlaylistDB
    from src.db_songs import SongsDB
    from src.db_reels import ReelsDB
    from src.pipeline import Pipeline
    cfg_data = {
        "project": {"version": "test"},
        "duplicate_detection": {"enabled": False},
        "paths": {"database_dir": "db", "work_dir": "temp", "songs_final": "songs/final", "skipped_no_sync": "songs/skipped", "reels_generated": "reels/generated", "fonts_dir": "assets/fonts", "logs_dir": "logs"},
    }
    from src.hashing import stable_json_hash
    cfg = Config(tmp_path, cfg_data, stable_json_hash(cfg_data))
    class FakePlaylistDB:
        def set_status(self, *args, **kwargs):
            pass
        def audit(self, *args, **kwargs):
            pass
    sdb = SongsDB(tmp_path/"songs.db", Path(__file__).resolve().parents[1]/"database"/"songs_schema.sql")
    rdb = ReelsDB(tmp_path/"reels.db", Path(__file__).resolve().parents[1]/"database"/"reels_schema.sql")
    pdb = FakePlaylistDB()
    pipeline = Pipeline(cfg,pdb,sdb,rdb)
    def fail(*args, **kwargs):
        raise RuntimeError("SPOTIFY_TEST_FAILURE")
    monkeypatch.setattr(pipeline, "_load_spotify_track", fail)
    entry={"playlist_serial":1,"spotify_track_id":"sp1","title":"Nammaka Tappani","artist":"Artist","album":"Album","duration_ms":120000,"playlist_position":1,"playlist_id":"P"}
    result=pipeline.process_entry(entry)
    assert result == "error"
    assert sdb.one("SELECT song_key FROM songs WHERE song_key=?", ("1_sp1",)) is not None
    assert sdb.one("SELECT status FROM stage_runs WHERE song_key=?", ("1_sp1",)) is None
    assert sdb.one("SELECT event_type FROM audit_events WHERE song_key=?", ("1_sp1",))["event_type"] == "song_failed"


def test_bootstrap_null_duration_is_normalized(tmp_path):
    from src.db_songs import SongsDB
    db = SongsDB(tmp_path / "songs.db", Path(__file__).resolve().parents[1] / "database" / "songs_schema.sql")
    row = db.ensure_song({"song_key":"null-duration","playlist_serial":1,"spotify_track_id":"sp","title":"X","artist":"A","duration_ms":None})
    assert row["duration_ms"] == 0


def test_stage_start_defensively_creates_missing_parent(tmp_path):
    from src.db_songs import SongsDB
    db = SongsDB(tmp_path / "songs.db", Path(__file__).resolve().parents[1] / "database" / "songs_schema.sql")
    rid = db.stage_start("missing-parent","spotify_metadata","fp")
    assert db.one("SELECT song_key FROM songs WHERE song_key=?", ("missing-parent",))["song_key"] == "missing-parent"
    assert db.one("SELECT run_id FROM stage_runs WHERE run_id=?", (rid,))["run_id"] == rid


def test_audit_defensively_creates_missing_parent(tmp_path):
    from src.db_songs import SongsDB
    db = SongsDB(tmp_path / "songs.db", Path(__file__).resolve().parents[1] / "database" / "songs_schema.sql")
    db.audit("missing-audit-parent","song_failed","pipeline","error","boom","TEST")
    assert db.one("SELECT song_key FROM audit_events WHERE song_key=?", ("missing-audit-parent",))["song_key"] == "missing-audit-parent"
