import sqlite3
from pathlib import Path
from src.storage.playlist_db import PlaylistDB
from src.orchestration.hook import parse_timecode, validate


def test_spotify_playlist_order_is_exact(tmp_path):
    p=PlaylistDB(tmp_path/'vachika.db')
    p.ingest_snapshot('P','P','u',None,[
        {'spotify_track_id':'A','playlist_position':1,'title':'A','track_type':'track','is_local':False},
        {'spotify_track_id':'B','playlist_position':2,'title':'B','track_type':'track','is_local':False},
    ])
    assert [x['spotify_track_id'] for x in p.actionable('P')]==['A','B']
    with sqlite3.connect(p.path) as db:
        cols={r[1] for r in db.execute('pragma table_info(playlist_entries)')}
    assert not cols.intersection({'priority_count','priority_metric','processing_rank','ytm_views','ytm_plays','ytm_engagement_count'})


def test_timecode_exact_and_hook_no_cap():
    assert parse_timecode('02:03.45')==123450
    assert parse_timecode('00:00.001')==1
    assert validate(0,1000,100000)==1000


def test_current_project_plan_matches_active_pipeline_contract():
    text=Path('PROJECT_PLAN.md').read_text(encoding='utf-8').lower()
    assert 'playlist authority:** spotify web api' in text
    assert '**spotify audio:** never downloaded' in text
    assert 'yt-dlp' in text
    assert 'ytmusicapi' in text
    assert 'current release authority — 1.5.1-unified-db-mms-offset-v3' in text
    assert 'duration-compatible recording' in text
    assert 'isrc duplicate gate' in text
    assert 'mms/ctc' in text and 'energy-envelope matching is not used as a fallback' in text


def test_reels_repository_uses_shared_schema_metadata(tmp_path):
    from src.storage.reel_db import ReelsDB
    db=ReelsDB(tmp_path/'vachika.db')
    with db.connect() as c:
        assert c.execute("SELECT value FROM reels_schema_meta WHERE key='schema_name'").fetchone()[0] == 'unified_reels'
