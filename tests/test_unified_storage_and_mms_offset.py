import json
import hashlib
import sqlite3
from pathlib import Path

import pytest

from src.config import Config
from src.storage.playlist_db import PlaylistDB
from src.storage.song_db import SongsDB
from src.storage.reel_db import ReelsDB
from src.storage.unified_db import initialize_unified_database
from src.storage.song_catalog import sync_songs_json


def _cfg(tmp_path):
    data = json.loads(Path('config.json.example').read_text(encoding='utf-8'))
    data['playlist']['spotify_playlist_id'] = 'P-UNIFIED'
    data['paths'].update({
        'database_dir': 'db', 'work_dir': 'temp', 'songs_final': 'songs/final',
        'skipped_no_sync': 'songs/skipped/no_synced_lrc', 'reels_generated': 'reels/generated',
        'models_dir': 'models', 'logs_dir': 'logs', 'fonts_dir': 'assets/fonts',
    })
    path = tmp_path / 'config.json'
    path.write_text(json.dumps(data, indent=2), encoding='utf-8')
    return Config.load(path)


def test_unified_database_has_all_store_namespaces_and_no_parallel_active_db(tmp_path):
    cfg = _cfg(tmp_path)
    pdb, sdb, rdb, migration = initialize_unified_database(cfg)
    assert migration['imported'] == []
    with sqlite3.connect(cfg.db_dir / 'vachika.db') as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {'playlists', 'playlist_entries', 'songs', 'metadata', 'reels', 'reel_hooks',
            'phase2_songs', 'phase2_chunks', 'phase2_words', 'phase2_schema_meta'}.issubset(tables)
    assert not any((cfg.db_dir / name).exists() for name in ('playlist.db', 'songs.db', 'reels.db'))
    assert (cfg.db_dir / 'migration_manifest.json').is_file()
    sync_songs_json(cfg, pdb, sdb, rdb)
    catalog = json.loads((cfg.db_dir / 'songs.json').read_text(encoding='utf-8'))
    assert catalog['schema_version'] == 1 and catalog['song_count'] == 0


def test_legacy_stores_and_phase2_checkpoints_are_imported_then_archived(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.db_dir.mkdir(parents=True, exist_ok=True)
    # Simulate the actual pre-unification layout with three raw SQLite stores. Current
    # repository classes intentionally refuse to create these legacy filenames.
    key = '1_TRACK1'
    # Seed the new unified file first to simulate an interrupted/partially upgraded
    # installation. Its generated event ID intentionally collides with an old DB row.
    from src.storage.playlist_db import PlaylistDB
    from src.storage.song_db import SongsDB
    from src.storage.reel_db import ReelsDB
    current_path = cfg.db_dir / 'vachika.db'
    PlaylistDB(current_path)
    current_songs = SongsDB(current_path)
    ReelsDB(current_path)
    current_songs.ensure_song({'song_key': 'existing_0', 'playlist_serial': 0, 'spotify_track_id': 'EXISTING',
                               'basename': 'existing', 'title': 'Existing', 'artist': 'Artist',
                               'album': 'Album', 'duration_ms': 1000})
    current_songs.audit('existing_0', 'migration_test', stage='preexisting', status='kept', message='already in unified DB')
    legacy_paths = {
        'playlist.db': (Path('database/playlist_schema.sql'),),
        'songs.db': (Path('database/songs_schema.sql'),),
        'reels.db': (Path('database/reels_schema.sql'),),
    }
    for filename, (schema_path,) in legacy_paths.items():
        with sqlite3.connect(cfg.db_dir / filename) as legacy:
            legacy.executescript(schema_path.read_text(encoding='utf-8'))
    with sqlite3.connect(cfg.db_dir / 'playlist.db') as legacy:
        legacy.execute("""INSERT INTO playlists(playlist_id,display_name,playlist_url,snapshot_id,
                       snapshot_scanned_once,ingestion_status,entry_count)
                       VALUES(?,?,?,?,?,?,?)""", ('P-UNIFIED','test playlist',
                       'https://open.spotify.com/playlist/P-UNIFIED','snap1',1,'done',1))
        legacy.execute("""INSERT INTO playlist_entries(playlist_serial,playlist_id,playlist_position,
                       spotify_track_id,spotify_uri,spotify_url,title,artist,album,spotify_duration_ms,
                       spotify_isrc,track_type,is_local,status,terminal,raw_item_json)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (1,'P-UNIFIED',1,'TRACK1',
                       'spotify:track:TRACK1','https://open.spotify.com/track/TRACK1','A Song','An Artist',
                       'An Album',120000,'USAAA0000001','track',0,'pending',0,'{}'))
        legacy.execute("INSERT INTO playlist_snapshots(playlist_id,snapshot_id,raw_playlist_json,fetched_entry_count) VALUES(?,?,?,?)", ('P-UNIFIED','snap1','{}',1))
    with sqlite3.connect(cfg.db_dir / 'songs.db') as legacy:
        legacy.execute("""INSERT INTO songs(song_key,playlist_serial,spotify_track_id,basename,title,artist,album,
                       duration_ms,pipeline_status) VALUES(?,?,?,?,?,?,?,?,?)""",
                       (key,1,'TRACK1','001_A Song - An Artist','A Song','An Artist','An Album',120000,'pending'))
        legacy.execute("""INSERT INTO metadata(song_key,normalized_title,normalized_artist,normalized_album,release_date)
                       VALUES(?,?,?,?,?)""", (key,'A Song','An Artist','An Album','2020-01-01'))
        legacy.execute("""INSERT INTO audit_events(id,song_key,event_type,stage_name,status,message,error_code,created_at)
                       VALUES(1,?,?,?,?,?,?,?)""", (key,'migration_test','legacy','pending','old event',None,'2025-01-01T00:00:00Z'))
        # This row resembles the former offset format: it has a numeric offset but no
        # current MMS method version. Migration must invalidate it instead of retaining it.
        legacy.execute("""INSERT INTO youtube_matches(song_key,video_id,video_url,video_title,selection_query,
                       selection_rule,offset_ms,offset_seconds,match_score)
                       VALUES(?,?,?,?,?,?,?,?,?)""", (key,'VIDEO1','https://www.youtube.com/watch?v=VIDEO1','old video',
                       'old query','old_energy_selector',4300,4.3,0.99))
    reel_key = f'{key}_reel_v1'
    with sqlite3.connect(cfg.db_dir / 'reels.db') as legacy:
        legacy.execute("""INSERT INTO reels(reel_key,song_key,pipeline_status,hook_start_ms,hook_end_ms,
                       created_at,updated_at) VALUES(?,?,?,?,?,?,?)""",
                       (reel_key,key,'HOOK_READY',1000,11000,'2026-01-01T00:00:00Z','2026-01-01T00:00:00Z'))

    # Create a representative old per-song Phase-2 database.
    from src.phase2core.db import Database as Phase2Database
    p2_path = cfg.work_dir / key / 'source_deadbeef' / 'alignment' / 'db' / 'phase2.sqlite'
    p2_path.parent.mkdir(parents=True, exist_ok=True)
    p2 = Phase2Database(p2_path)
    legacy_song_id = p2.upsert_song({
        'basename': 'master', 'serial_number': 1, 'original_mp3_path': 'source.mp3',
        'original_lrc_path': 'source.lrc', 'original_json_path': 'source.json',
        'original_mp3_sha256': 'a' * 64, 'original_lrc_sha256': 'b' * 64,
        'original_json_sha256': 'c' * 64, 'title': 'A Song', 'artist': 'An Artist', 'album': 'An Album',
        'duration_ms': 120000, 'pipeline_status': 'completed', 'quality_status': 'passed',
    })
    legacy_chunk_id = p2.add_chunk(legacy_song_id, {
        'chunk_index': 0, 'logical_start_ms': 0, 'logical_end_ms': 1000,
        'audio_start_ms': 0, 'audio_end_ms': 1200, 'text_content': 'నమస్కారం',
        'normalized_text': 'నమస్కారం', 'word_count': 1, 'status': 'completed',
    })
    p2.add_words([{
        'song_id': legacy_song_id, 'chunk_id': legacy_chunk_id, 'line_index': 0,
        'word_index': 0, 'original_word': 'నమస్కారం', 'normalized_word': 'నమస్కారం',
        'start_ms': 100, 'end_ms': 700, 'alignment_score': 0.92, 'source': 'ctc',
        'is_interpolated': 0, 'reason': None,
    }])
    p2.close()

    pdb, sdb, rdb, migration = initialize_unified_database(cfg)
    assert set(migration['imported']) == {'playlist.db', 'songs.db', 'reels.db'}
    assert pdb.get_playlist('P-UNIFIED')['entry_count'] == 1
    assert sdb.one('SELECT title FROM songs WHERE song_key=?', (key,))['title'] == 'A Song'
    with sqlite3.connect(cfg.db_dir / 'vachika.db') as unified:
        events = unified.execute("SELECT id,song_key,message FROM audit_events ORDER BY id").fetchall()
        assert len(events) == 2 and len({row[0] for row in events}) == 2
        assert any(row[1] == key and row[2] == 'old event' for row in events)
        old_offset = unified.execute("SELECT offset_ms,method_version,status FROM youtube_matches WHERE song_key=?", (key,)).fetchone()
        assert old_offset == (None, 'legacy_offset_invalidated', 'superseded_legacy_offset')
    assert rdb.one('SELECT pipeline_status FROM reels WHERE reel_key=?', (reel_key,))['pipeline_status'] == 'HOOK_READY'
    assert not p2_path.exists()
    with sqlite3.connect(cfg.db_dir / 'vachika.db') as unified:
        migrated_p2 = unified.execute("SELECT id,basename,pipeline_status FROM phase2_songs WHERE basename=?", (key,)).fetchone()
        assert migrated_p2 is not None and migrated_p2[2] == 'completed'
        assert unified.execute("SELECT COUNT(*) FROM phase2_chunks WHERE song_id=?", (migrated_p2[0],)).fetchone()[0] == 1
        assert unified.execute("SELECT original_word FROM phase2_words WHERE song_id=?", (migrated_p2[0],)).fetchone()[0] == 'నమస్కారం'
        assert unified.execute("SELECT value FROM unified_schema_meta WHERE key='schema_version'").fetchone()[0] == '3'
    assert not any((cfg.db_dir / name).exists() for name in ('playlist.db', 'songs.db', 'reels.db'))
    migration_dirs = list((cfg.db_dir / 'backups').glob('legacy-before-unification-*'))
    assert migration_dirs and all((migration_dirs[0] / f'active-{name}').is_file() for name in ('playlist.db', 'songs.db', 'reels.db'))
    import_manifest = json.loads((migration_dirs[0] / 'migration_manifest.json').read_text(encoding='utf-8'))
    snapshots = import_manifest['sqlite_backup_snapshots']
    assert set(snapshots) == {'playlist.db', 'songs.db', 'reels.db'}
    for item in snapshots.values():
        backup_snapshot = cfg.db_dir / item['snapshot']
        assert backup_snapshot.is_file() and item['integrity'] == 'ok'
        assert hashlib.sha256(backup_snapshot.read_bytes()).hexdigest() == item['sha256']
    phase2_archives = list((cfg.db_dir / 'backups').glob('legacy-phase2-*'))
    assert phase2_archives and list(phase2_archives[0].rglob('phase2.sqlite'))
    sync_songs_json(cfg, pdb, sdb, rdb)
    catalog = json.loads((cfg.db_dir / 'songs.json').read_text(encoding='utf-8'))
    assert catalog['song_count'] == 2
    song = next(item for item in catalog['songs'] if item['song_key'] == key)
    assert song['title'] == 'A Song'
    assert song['paths']['song_json'] is None
    assert 'raw' not in song and 'raw_item_json' not in song


def test_catalog_is_generated_atomically_from_unified_db(tmp_path):
    cfg = _cfg(tmp_path)
    pdb, sdb, rdb, _ = initialize_unified_database(cfg)
    pdb.ingest_snapshot('P-UNIFIED', 'test playlist', 'u', 'snapshot', [{
        'playlist_position': 1, 'spotify_track_id': 'A', 'title': 'Song A', 'artist': 'Artist',
        'album': 'Album', 'duration_ms': 1000, 'track_type': 'track', 'is_local': False,
    }])
    sdb.ensure_song({'song_key': '1_A', 'playlist_serial': 1, 'spotify_track_id': 'A', 'basename': '001_Song A',
                     'title': 'Song A', 'artist': 'Artist', 'album': 'Album', 'duration_ms': 1000,
                     'source_mp3_path': 'temp/1_A/source.mp3', 'final_json_path': 'songs/final/001_Song A.json'})
    sdb.upsert_metadata('1_A', {'normalized_title': 'Song A', 'normalized_artist': 'Artist', 'normalized_album': 'Album',
                                'album_artist': 'Artist', 'release_date': '2021-01-01', 'track_number': 3})
    out = sync_songs_json(cfg, pdb, sdb, rdb)
    data = json.loads(out.read_text(encoding='utf-8'))
    song = data['songs'][0]
    assert song['details']['track_number'] == 3
    assert song['paths']['source_audio'] == 'temp/1_A/source.mp3'
    assert song['record_path'] == 'songs/final/001_Song A.json'
    assert 'debug' not in json.dumps(song).lower()


class _OffsetConfig:
    def get(self, key, default=None):
        return {
            'youtube_offset.anchor_min_word_confidence': 0.003,
            'youtube_offset.offset_min_matched_words': 4,
            'youtube_offset.offset_max_mad_ms': 800,
        }.get(key, default)


def _anchor(label, timestamps, confidence=0.8):
    words = []
    for index, stamp in enumerate(timestamps):
        words.append({'line_index': 0, 'word_index': index, 'original_word': f'వర్డ్{index}',
                      'start_ms': stamp, 'end_ms': stamp + 200, 'confidence': confidence})
    return {'source_label': label, 'audio_sha256': label * 8,
            'chosen': {'phrase_start_ms': timestamps[0], 'words': words}}


def test_anchor_policy_prefers_earliest_candidate_near_best_score():
    from src.video.lyric_offset import _choose_anchor_candidate
    candidates = [
        {"phrase_start_ms": 1000, "score": 0.004},
        {"phrase_start_ms": 12000, "score": 0.080},
        {"phrase_start_ms": 24000, "score": 0.075},
    ]
    chosen, strong, best, floor = _choose_anchor_candidate(candidates, 0.75)
    assert chosen["phrase_start_ms"] == 12000
    assert [c["phrase_start_ms"] for c in strong] == [12000, 24000]
    assert best == 0.080
    assert floor == 0.060


def test_mms_offset_is_median_of_paired_lrclib_word_timestamps():
    from src.video.lyric_offset import calculate_offset_from_anchors, METHOD_VERSION
    reference = _anchor('r', [1000, 1600, 2300, 2800, 3500])
    youtube = _anchor('y', [3000, 3600, 4350, 4800, 100500])
    result = calculate_offset_from_anchors(youtube, reference, _OffsetConfig())
    assert result['accepted'] is True
    assert result['candidate_offset_ms'] == 2000
    assert result['method_version'] == METHOD_VERSION == 'mms-lrclib-dual-audio-offset-v3'
    assert result['lyrics_source'] == 'LRCLIB'
    assert result['energy_match_used'] is False
    assert result['matched_word_count'] == 4
    assert max(abs(item['offset_ms'] - result['candidate_offset_ms']) for item in result['matched_words']) <= 50


def test_mms_offset_rejects_too_few_paired_words():
    from src.video.lyric_offset import calculate_offset_from_anchors
    with pytest.raises(RuntimeError, match='MMS_OFFSET_INSUFFICIENT_MATCHES'):
        calculate_offset_from_anchors(_anchor('y', [2000, 3000]), _anchor('r', [1000, 2000]), _OffsetConfig())


def test_offset_module_has_no_energy_matcher_fallback():
    source = Path('src/video/lyric_offset.py').read_text(encoding='utf-8')
    assert 'calculate_offset_from_anchors' in source
    assert 'No energy-envelope fallback is enabled' in source
    assert 'find_offset(' not in source
    pipeline = Path('src/orchestration/pipeline.py').read_text(encoding='utf-8')
    assert 'calculate_offset_from_anchors(anchor, reference_anchor, self.cfg)' in pipeline
    assert 'calculate_offset_from_word_alignments(' not in pipeline


def test_model_download_config_can_be_loaded_before_playlist_setup(tmp_path):
    data = json.loads(Path('config.json.example').read_text(encoding='utf-8'))
    data['playlist']['spotify_playlist_id'] = ''
    path = tmp_path / 'config.json'
    path.write_text(json.dumps(data, indent=2), encoding='utf-8')
    cfg = Config.load(path, require_playlist=False)
    assert cfg.get('playlist.spotify_playlist_id') == ''
    with pytest.raises(ValueError, match='playlist.spotify_playlist_id is required'):
        Config.load(path)
