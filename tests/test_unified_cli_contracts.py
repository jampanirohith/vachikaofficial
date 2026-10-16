from pathlib import Path


def test_main_initializes_unified_database_and_catalog_before_pipeline():
    src = Path('main.py').read_text(encoding='utf-8')
    assert 'initialize_unified_database(cfg)' in src
    assert "sync_songs_json(cfg, pdb, sdb, rdb)" in src
    body = src.split('def main():', 1)[1]
    initialized = body.index('initialize_unified_database(cfg)')
    pipe = body.index('from src.orchestration.pipeline import Pipeline')
    assert initialized < pipe
    assert body.index('sync_songs_json(cfg, pdb, sdb, rdb)', initialized) < pipe


def test_cli_exposes_independent_model_playlist_and_processing_commands():
    src = Path('main.py').read_text(encoding='utf-8')
    assert '"--prepare-models", "--download-models"' in src
    assert '"--load-playlist"' in src
    assert '"--process-playlist"' in src
    # The process-only command checks for a previously committed snapshot.
    assert 'No committed Spotify playlist snapshot exists. Run python main.py --load-playlist first.' in src


def test_pipeline_passes_full_config_to_acquisition():
    src = Path('src/orchestration/pipeline.py').read_text(encoding='utf-8')
    assert 'acquire(self.cfg, work_base / "acquisition", spotify_result)' in src
    assert 'acquire(self.cfg.data' not in src


def test_active_runtime_has_ytdlp_audio_dependency_boundary():
    root = Path('.')
    media = (root / 'src' / 'providers' / 'ytm_media.py').read_text(encoding='utf-8')
    acquisition = (root / 'src' / 'providers' / 'acquisition.py').read_text(encoding='utf-8')
    requirements = (root / 'requirements.txt').read_text(encoding='utf-8')
    assert 'import yt_dlp' in media
    assert "'noplaylist': True" in media
    assert "'format': format_selector" in media
    assert 'music.youtube.com/watch?v=' in acquisition
    assert 'yt-dlp' in requirements


def test_normal_first_run_snapshot_only_uses_unified_db_and_catalog(monkeypatch, tmp_path):
    import json, sys
    import main as app
    root = tmp_path / 'project'; root.mkdir()
    cfg = {
        'project': {'name': 'test', 'version': 'x', 'pipeline_version': 'x'},
        'playlist': {'provider': 'spotify', 'spotify_playlist_id': 'P-FIRST', 'scan_complete_once': True,
                     'process_in_exact_playlist_order': True},
        'paths': {'database_dir': 'db', 'work_dir': 'temp', 'songs_final': 'songs/final',
                  'skipped_no_sync': 'songs/skipped/no_synced_lrc', 'reels_generated': 'reels/generated',
                  'fonts_dir': 'assets/fonts', 'logs_dir': 'logs', 'models_dir': 'models'},
        'spotify': {'require_user_auth': True, 'auth_file': 'spotify_auth.json', 'use_env': False, 'client_id': 'x'},
    }
    config_path = root / 'config.json'; config_path.write_text(json.dumps(cfg), encoding='utf-8')

    class FakeSpotify:
        has_user_auth = True
        def __init__(self, *a, **k): pass
        def fetch_playlist_snapshot(self, playlist_id, progress=None):
            items = [{'track': {'type': 'track', 'id': 'A', 'name': 'A', 'duration_ms': 1000,
                                'artists': [{'name': 'Artist'}], 'album': {'name': 'Album'}}, 'added_at': None}]
            return {'id': playlist_id, 'name': 'Prepared', 'snapshot_id': 'S1'}, items, playlist_id

    monkeypatch.setattr(app, 'SpotifyClient', FakeSpotify)
    monkeypatch.setattr(sys, 'argv', ['main.py', '--config', str(config_path), '--load-playlist'])
    result = app.main()
    assert result == 0
    assert (root / 'db' / 'vachika.db').is_file()
    assert (root / 'db' / 'songs.json').is_file()
    assert not (root / 'db' / 'playlist.db').exists()
    assert not (root / 'db' / 'songs.db').exists()
    assert not (root / 'db' / 'reels.db').exists()
    catalog = json.loads((root / 'db' / 'songs.json').read_text(encoding='utf-8'))
    assert catalog['song_count'] == 1
    assert catalog['songs'][0]['spotify']['track_id'] == 'A'
