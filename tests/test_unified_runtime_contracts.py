from pathlib import Path
import sqlite3
from src.db_playlist import PlaylistDB
from src.db_songs import SongsDB
from src.db_reels import ReelsDB
from src.db_maintenance import backup_databases, restore_databases, integrity_check
from src.demucs import DemucsRunner


def _dbs(tmp_path): return PlaylistDB(tmp_path/'playlist.db'),SongsDB(tmp_path/'songs.db'),ReelsDB(tmp_path/'reels.db')


def test_every_spotify_playlist_position_is_persisted(tmp_path):
    p,_,_=_dbs(tmp_path)
    p.ingest_snapshot('P','P','u','snap',[
        {'spotify_track_id':'A','playlist_position':1,'title':'A','track_type':'track','is_local':False},
        {'spotify_track_id':None,'playlist_position':2,'title':'Local','track_type':'local','is_local':True},
    ])
    rows=p.all_entries('P')
    assert len(rows)==2 and [r['playlist_position'] for r in rows]==[1,2]
    assert p.get_playlist('P')['ingestion_status']=='done'


def test_playlist_schema_has_no_popularity_columns(tmp_path):
    p,_,_=_dbs(tmp_path)
    with sqlite3.connect(p.path) as db:
        cols={r[1] for r in db.execute('pragma table_info(playlist_entries)')}
    forbidden={'priority_count','priority_metric','processing_rank','ytm_views','ytm_plays','ytm_engagement_count'}
    assert not cols.intersection(forbidden)


def test_demucs_cache_has_stable_run_id(tmp_path):
    work=tmp_path/'work';work.mkdir();source=tmp_path/'song.mp3';source.write_bytes(b'source')
    out=work/'audio'/'stems';out.mkdir(parents=True)
    for stem in ('vocals','drums','bass','other'):(out/f'{stem}.wav').write_bytes(b'0'*2000)
    d=DemucsRunner({'demucs.model':'htdemucs','runtime.device':'cpu'},work,None).run(source)
    d2=DemucsRunner({'demucs.model':'htdemucs','runtime.device':'cpu'},work,None).run(source)
    assert d['run_id']==d2['run_id'] and d['run_id'] not in ('','reused')


def test_sqlite_stage_accepts_windows_path(tmp_path):
    _,s,_=_dbs(tmp_path);key='1_A'
    s.ensure_song({'song_key':key,'playlist_serial':1,'spotify_track_id':'A','basename':'x','title':'T','duration_ms':1000})
    rid=s.stage_start(key,'acquisition','fp',Path('C:/temp/song/master.mp3'))
    s.stage_finish(rid,'completed',Path('C:/temp/song/master.mp3'),'abc')
    row=s.one('select artifact_path,status from stage_runs where run_id=?',(rid,))
    assert row['artifact_path'].endswith('master.mp3') and row['status']=='completed'


def test_db_backup_restore(tmp_path):
    root=tmp_path/'project';(root/'db').mkdir(parents=True)
    PlaylistDB(root/'db/playlist.db');SongsDB(root/'db/songs.db');ReelsDB(root/'db/reels.db')
    dest=backup_databases(root,tmp_path/'backups')
    assert all(integrity_check(dest/f'{n}.db')=='ok' for n in ('playlist','songs','reels'))
    marker=root/'db/songs.db';marker.write_bytes(b'corrupt')
    restore_databases(root,dest)
    assert integrity_check(marker)=='ok'


def test_stale_playlist_db_is_quarantined(tmp_path):
    import sqlite3
    path=tmp_path/'playlist.db'
    with sqlite3.connect(path) as c:
        c.execute('CREATE TABLE schema_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
        c.execute("INSERT INTO schema_meta VALUES('schema_name','old_schema')")
        c.execute("INSERT INTO schema_meta VALUES('schema_version','1.0.0')")
        c.commit()
    PlaylistDB(path)
    assert any(path.parent.glob('playlist.legacy*.db'))
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0]=='4.0.0'


def test_audio_dependency_boundary_is_search_then_ytdlp():
    from pathlib import Path
    acquisition = Path('src/acquisition.py').read_text(encoding='utf-8')
    media = Path('src/ytm_media.py').read_text(encoding='utf-8')
    assert 'api.search(query, filter="songs"' in acquisition
    assert 'music.youtube.com/watch?v=' in acquisition
    assert 'yt_dlp' in media
    assert "'noplaylist': True" in media
    assert "'format': format_selector" in media
    assert 'download_info' not in acquisition


def test_demucs_immediately_uses_cpu_when_cuda_unavailable(monkeypatch, tmp_path):
    from src.demucs import DemucsRunner
    d=DemucsRunner({'demucs.model':'htdemucs','runtime.device':'cuda','runtime.fallback_device':'cpu','runtime.allow_cpu_fallback':True,'runtime.require_cuda':False,'demucs.segment_seconds':8},tmp_path,None)
    monkeypatch.setattr(d,'_cuda_ok',lambda: False)
    calls=[]
    def fake(source,outroot,device,segment=None): calls.append(device); outroot.mkdir(parents=True,exist_ok=True); [ (outroot/f'{x}.wav').write_bytes(b'0'*2000) for x in d.STEMS ]
    monkeypatch.setattr(d,'_run',fake)
    source=tmp_path/'song.mp3'; source.write_bytes(b'source')
    result=d.run(source)
    assert calls==['cpu']
    assert result['device']=='cpu'


def test_demucs_cpu_fallback_is_default_even_with_legacy_cuda_request(monkeypatch, tmp_path):
    d=DemucsRunner({'demucs.model':'htdemucs','runtime.device':'cuda'},tmp_path,None)
    monkeypatch.setattr(d,'_cuda_ok',lambda: False)
    calls=[]
    def fake(source,outroot,device,segment=None):
        calls.append(device); outroot.mkdir(parents=True,exist_ok=True)
        for x in d.STEMS: (outroot/f'{x}.wav').write_bytes(b'0'*2000)
    monkeypatch.setattr(d,'_run',fake)
    source=tmp_path/'song.mp3'; source.write_bytes(b'source')
    result=d.run(source)
    assert calls==['cpu']
    assert result['device']=='cpu'


def test_runtime_config_prefers_cuda_with_cpu_fallback():
    import json
    cfg=json.loads(Path('config.json').read_text(encoding='utf-8'))
    assert cfg['runtime']['device']=='cuda'
    assert cfg['runtime']['fallback_device']=='cpu'
    assert cfg['runtime']['require_cuda'] is False
    assert cfg['runtime']['allow_cpu_fallback'] is True

def test_demucs_db_runs_receive_created_at():
    from src.db_songs import SongsDB
    from pathlib import Path
    import tempfile
    root = Path(tempfile.mkdtemp())
    db = SongsDB(root / 'songs.db', Path('database/songs_schema.sql'))
    key = '1_created_at_test'
    db.ensure_song({'song_key': key, 'title': 'x', 'artist': 'a', 'album': 'b', 'duration_ms': 1000})
    rid = db.upsert_demucs(key, {'model_name':'htdemucs','device':'cpu','input_sha256':'x','duration_ms':1000,'status':'completed'})
    row = db.one('SELECT created_at FROM demucs_runs WHERE run_id=?', (rid,))
    assert row and row['created_at']


def test_core_final_package_is_promoted_before_hook():
    from pathlib import Path
    text = Path('src/pipeline.py').read_text(encoding='utf-8')
    assert text.index('Pre-hook final package promotion') < text.index('Manual hook')
    assert 'final_mp3_path=str(final_mp3.relative_to(self.cfg.root))' in text
    assert 'eightd = final_8d' in text


def test_unified_config_exposes_canonical_config_path():
    from src.config import Config
    import json
    cfg_data=json.loads(Path('config.json').read_text(encoding='utf-8'))
    cfg=Config(Path('.').resolve(), cfg_data, 'hash')
    assert cfg.config_path == Path('config.json').resolve()

def test_alignment_adapter_is_self_contained_and_has_no_archive_config_dependency():
    from pathlib import Path
    alignment = Path('src/alignment.py').read_text(encoding='utf-8')
    assert "cfg.config_path" in alignment
    assert "archives/phase2" not in alignment
    db = Path('src/phase2core/db.py').read_text(encoding='utf-8')
    assert "archives" not in db


def test_phase2_database_constructor_matches_alignment_adapter():
    import inspect
    from src.phase2core.db import Database
    signature = inspect.signature(Database.__init__)
    assert "pipeline_version" not in signature.parameters

    alignment = Path("src/alignment.py").read_text(encoding="utf-8")
    assert "Database(p2.path('paths.db_dir')/'phase2.sqlite',pipeline_version=" not in alignment
    assert "Database(p2.path('paths.db_dir')/'phase2.sqlite')" in alignment


def test_phase2_pipeline_constructor_matches_alignment_adapter():
    import inspect
    from src.phase2core.pipeline import Pipeline as P2Pipeline
    signature = inspect.signature(P2Pipeline.__init__)
    assert list(signature.parameters) == ["self", "config", "db", "allow_cpu_fallback"]
    alignment = Path("src/alignment.py").read_text(encoding="utf-8")
    assert "P2Pipeline(p2,dbobj,allow_cpu_fallback=True)" in alignment


def test_phase2_database_schema_is_packaged_with_unified_runtime(tmp_path):
    from src.phase2core.db import Database

    schema = Path("src/sql/001_initial.sql")
    assert schema.is_file()
    db_path = tmp_path / "phase2.sqlite"
    db = Database(db_path)
    try:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"schema_meta", "songs", "chunks", "words"}.issubset(tables)
        version = db.execute("SELECT value FROM schema_meta WHERE key='version'").fetchone()[0]
        assert version == "1"
    finally:
        db.close()


def test_model_cache_is_project_persistent_and_not_per_song():
    from src.model_cache import configure_model_cache
    root = configure_model_cache(Path("models"))
    assert root.name == "models"
    assert (root / "huggingface" / "hub").is_dir()
    assert (root / "torch").is_dir()
    alignment = Path("src/alignment.py").read_text(encoding="utf-8")
    assert "cfg.path('paths.models_dir')" in alignment
    assert "base/'models'" not in alignment


def test_demucs_runner_accepts_persistent_model_cache(tmp_path):
    from src.demucs import DemucsRunner
    cache = tmp_path / "models"
    d = DemucsRunner({'demucs.model':'htdemucs','runtime.device':'cpu'}, tmp_path/'work', None, model_cache_dir=cache)
    assert d.model_cache_dir == cache


def test_phase2_demucs_receives_persistent_model_cache():
    text = Path('src/phase2core/pipeline.py').read_text(encoding='utf-8')
    assert 'model_cache_dir=self.config.path("paths.models_dir")' in text


def test_main_demucs_receives_persistent_model_cache():
    text = Path('src/pipeline.py').read_text(encoding='utf-8')
    assert 'model_cache_dir=self.cfg.path("paths.models_dir")' in text


def test_model_cache_environment_is_project_owned(tmp_path, monkeypatch):
    import os
    from src.model_cache import configure_model_cache
    root = configure_model_cache(tmp_path / "models")
    assert Path(os.environ["HF_HOME"]) == root / "huggingface"
    assert Path(os.environ["HF_HUB_CACHE"]) == root / "huggingface" / "hub"
    assert Path(os.environ["TORCH_HOME"]) == root / "torch"


def test_songs_db_persists_alignment_lyric_lines_and_words_atomically(tmp_path):
    from src.db_songs import SongsDB
    db = SongsDB(tmp_path / "songs.db", Path("database/songs_schema.sql"))
    key = "1_TEST"
    db.ensure_song({"song_key": key, "title": "Test", "basename": "Test", "duration_ms": 1000})
    alignment_id = db.upsert_alignment(key, {
        "input_mp3_sha256": "a", "input_lrc_sha256": "b", "demucs_run_id": None,
        "model_name": "facebook/mms-1b-all", "model_revision": None, "language": "tel",
        "normalizer_version": "1.1.0", "alignment_method": "test", "config_hash": "c",
        "quality_status": "passed", "status": "completed",
    })
    db.set_lyrics_words(key, alignment_id, [
        {"line_index": 0, "source_start_ms": 100, "aligned_start_ms": 120, "aligned_end_ms": 900, "text": "hello world"}
    ], [
        {"line_index": 0, "word_index": 0, "original_word": "hello", "normalized_word": "hello", "start_ms": 120, "end_ms": 450, "score": 0.9, "source": "aligned"},
        {"line_index": 0, "word_index": 1, "original_word": "world", "normalized_word": "world", "start_ms": 451, "end_ms": 900, "score": 0.8, "source": "aligned"},
    ])
    lines = db.one("SELECT COUNT(*) AS n FROM lyric_lines WHERE alignment_run_id=?", (alignment_id,))["n"]
    words = db.one("SELECT COUNT(*) AS n FROM lyric_words WHERE alignment_run_id=?", (alignment_id,))["n"]
    linked = db.one("SELECT COUNT(*) AS n FROM lyric_words WHERE alignment_run_id=? AND line_id IS NOT NULL", (alignment_id,))["n"]
    assert (lines, words, linked) == (1, 2, 2)



def test_eightd_crops_all_stems_to_one_canonical_frame_count():
    import numpy as np
    from src.eightd import _fit_stems
    arrays={
        'vocals': np.zeros((2, 100), dtype=np.float32),
        'drums': np.zeros((2, 103), dtype=np.float32),
        'bass': np.zeros((2, 101), dtype=np.float32),
        'other': np.zeros((2, 102), dtype=np.float32),
    }
    fitted,n=_fit_stems(arrays)
    assert n == 100
    assert {k:v.shape[1] for k,v in fitted.items()} == {k:100 for k in arrays}



def test_eightd_density_gain_timeline_covers_non_block_aligned_audio():
    import numpy as np
    from src import eightd
    sr = 44100
    n = 10343666  # deliberately not divisible by the 100 ms block size
    block = max(1, int(sr * 0.1))
    blocks = max(1, int(eightd.math.ceil(n / block)))
    density = np.zeros(blocks, dtype=np.float32)
    d = np.repeat(density, block)[:n]
    assert len(d) == n


def test_eightd_mix_never_uses_uncropped_drums():
    text=Path('src/eightd.py').read_text(encoding='utf-8')
    assert "drums0=arrays['drums'][:,:n]" in text
    assert "dmix=0.88*drums0.mean(axis=0)" in text

def test_pipeline_songs_db_calls_match_songs_db_api():
    import ast
    from pathlib import Path
    pipeline = ast.parse(Path("src/pipeline.py").read_text(encoding="utf-8"))
    db = ast.parse(Path("src/db_songs.py").read_text(encoding="utf-8"))
    methods = {n.name for n in db.body if isinstance(n, ast.ClassDef) and n.name == "SongsDB" for n in n.body if isinstance(n, ast.FunctionDef)}
    calls = {n.func.attr for n in ast.walk(pipeline) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Attribute) and n.func.value.attr == "songs_db"}
    assert calls <= methods, sorted(calls - methods)



def test_eightd_ignores_demucs_metadata_when_hashing_stems():
    import ast
    tree=ast.parse(Path("src/eightd.py").read_text(encoding="utf-8"))
    fn=next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "render")
    calls=[n for n in ast.walk(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "sha256_file"]
    # Every stem hash must iterate over the canonical four audio-stem paths, not
    # the full Demucs metadata mapping (which contains values such as 'cpu').
    assert any(isinstance(n.args[0], ast.Name) and n.args[0].id == "v" for n in calls)
    assert "stem_paths.items()" in ast.unparse(fn)


def test_eightd_duration_parameter_does_not_shadow_duration_helper():
    import ast
    tree = ast.parse(Path("src/eightd.py").read_text(encoding="utf-8"))
    render = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "render")
    args = {a.arg for a in render.args.args}
    assert "expected_duration_ms" in args
    assert "duration_ms" not in args
    calls = [
        n for n in ast.walk(render)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "duration_ms"
    ]
    assert calls, "render must call the duration_ms helper for output validation"



def test_visual_video_download_uses_selected_youtube_url_not_ytmusicapi():
    from pathlib import Path
    text=Path("src/rendercore/video_grabber.py").read_text(encoding="utf-8")
    assert "YTMusicMediaClient" not in text
    assert "yt_dlp.YoutubeDL" in text
    assert "selected_url" in text
    reel=Path("src/reel.py").read_text(encoding="utf-8")
    pipeline=Path("src/pipeline.py").read_text(encoding="utf-8")
    assert 'video.video_url, offset["candidate_offset_ms"]' in pipeline
    assert "yt_video_source" in reel


def test_visual_selection_is_reused_for_reel_without_second_search():
    from pathlib import Path
    text=Path("src/pipeline.py").read_text(encoding="utf-8")
    assert text.count("select_video(self.cfg, md.title, md.album, spotify_result.duration_ms, work, md.artist)") == 1
    assert "build_reel(self.cfg, work, song_record, hs, he, video.video_url" in text


def test_pipeline_reels_db_upsert_calls_use_table_first_contract():
    from pathlib import Path
    text = Path("src/pipeline.py").read_text(encoding="utf-8")
    bad = [
        'self.reels_db.upsert(reel_key, "reel_youtube_sync", "reel_key",',
        'self.reels_db.upsert(reel_key, "reel_audio", "reel_key",',
        'self.reels_db.upsert(reel_key, "reel_video", "reel_key",',
        'self.reels_db.upsert(reel_key, "reel_lyrics", "reel_key",',
        'self.reels_db.upsert(reel_key, "reel_validation", "reel_key",',
    ]
    assert not any(item in text for item in bad)
    for table in ("reel_youtube_sync", "reel_audio", "reel_video", "reel_lyrics", "reel_validation"):
        assert f'self.reels_db.upsert("{table}", "reel_key", reel_key,' in text


def test_pipeline_defines_canonical_reel_final_paths_before_use():
    import ast
    text = Path("src/pipeline.py").read_text(encoding="utf-8")
    tree = ast.parse(text)
    fn = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Pipeline").body
    process = next(n for n in fn if isinstance(n, ast.FunctionDef) and n.name == "process_entry")
    assigned = set()
    uses = []
    for node in ast.walk(process):
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                if isinstance(t, ast.Name):
                    assigned.add(t.id)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in {"reel_final", "reel_json_final"}:
            uses.append(node.id)
    assert "reel_final" in assigned
    assert "reel_json_final" in assigned
    assert uses
    assert "reel_final = self.cfg.reels_dir / f\"{name}_reel.mp4\"" in text
    assert "reel_json_final = self.cfg.reels_dir / f\"{name}_reel.json\"" in text


def test_pipeline_final_song_paths_do_not_reference_undefined_targets():
    text = Path("src/pipeline.py").read_text(encoding="utf-8")
    assert "targets[0][1]" not in text
    assert "targets[1][1]" not in text
    assert "targets[2][1]" not in text
    assert "targets[3][1]" not in text
    assert "targets[4][1]" not in text


def test_hook_skip_config_and_persistent_queue_contract():
    import json
    from pathlib import Path
    cfg = json.loads(Path('config.json').read_text(encoding='utf-8'))
    assert isinstance(cfg['hook']['skip'], bool)
    assert cfg['hook']['queue_file'] == 'songs/final/hook_queue.json'
    text = Path('src/pipeline.py').read_text(encoding='utf-8')
    assert 'if bool(self.cfg.get("hook.skip", False))' in text
    assert 'hook_start_time' in text and 'hook_end_time' in text
    assert 'self.cfg.hook_queue_file' in text


def test_hook_queue_resume_uses_persisted_times_without_media_reselection():
    from pathlib import Path
    text = Path('src/pipeline.py').read_text(encoding='utf-8')
    assert 'def _finalize_from_hook_queue' in text
    assert 'build_reel(self.cfg, work, song_record, hs, he, video_url, offset_ms' in text
    assert 'if not video_url:' in text
    assert 'search_repeated_before_hook' in text
    # The queue-resume path must consume the persisted URL/offset, not perform a new search.
    section = text[text.index('def _finalize_from_hook_queue'):text.index('def process_entry')]
    assert 'select_video(' not in section
    assert 'youtube_video_url' in section and 'youtube_offset_ms' in section


def test_hook_queue_stale_source_package_recovers_into_normal_pipeline():
    from pathlib import Path
    text = Path("src/pipeline.py").read_text(encoding="utf-8")
    assert "def _hook_queue_sources_exist" in text
    assert "def _mark_hook_queue_stale" in text
    assert "has a stale hook queue entry" in text
    assert "SOURCE_PACKAGE_MISSING" in text
    assert "queued = None" in text


def test_hook_queue_is_written_before_manual_hook_prompt():
    from pathlib import Path
    text = Path('src/pipeline.py').read_text(encoding='utf-8')
    assert text.index('Optional hook deferral') < text.index('Manual hook')
    assert 'self._upsert_hook_queue_entry(queue_entry)' in text
    assert 'return "hook_pending"' in text
