from pathlib import Path
from types import SimpleNamespace
import json
import pytest


def test_acquire_uses_selected_ytmusic_url_with_download_client(monkeypatch, tmp_path):
    from src import acquisition
    calls={}
    class FakeClient:
        def __init__(self,*args,**kwargs):
            calls['init']=(args,kwargs)
            self.api=SimpleNamespace()
        def download_audio_mp3(self, video_id, destination, **kwargs):
            calls['download']=(video_id,destination,kwargs)
            Path(destination).write_bytes(b'0'*20000)
            Path(kwargs['info_path']).write_text(json.dumps({'videoDetails':{'title':'T'},'stream':{},'duration':{'x':1}}),encoding='utf-8')
            return {'duration_ms':200000,'raw':{}}
    monkeypatch.setattr(acquisition,'YTMusicMediaClient',FakeClient)
    monkeypatch.setattr(acquisition,'search_ytmusic_audio',lambda cfg,sr: ('Title Album', {'index':1,'video_id':'A','title':'T','duration_ms':200000,'delta_ms':0,'raw':{}}, []))
    cfg=SimpleNamespace(root=tmp_path, get=lambda k,d=None: d, path=lambda k: tmp_path/'browser.json')
    monkeypatch.setattr(acquisition, '_make_ytm_client', lambda cfg: FakeClient())
    sr=SimpleNamespace(track_name='Title',album_name='Album',duration_ms=200000,track_id='S1')
    result=acquisition.acquire(cfg,tmp_path/'work',sr)
    assert result['mp3'].exists()
    assert calls['download'][0]=='A'
    assert calls['download'][2]['source_url']=='https://music.youtube.com/watch?v=A'
    assert calls['download'][2]['download_timeout_seconds']==1800
    assert calls['download'][2]['source_metadata']['title']=='T'


def test_ytmusic_search_requires_known_duration_match(monkeypatch):
    from src.acquisition import search_ytmusic_audio, AcquisitionError
    class API:
        def search(self,*args,**kwargs):
            assert kwargs['filter']=='songs'
            return [{'videoId':'A','title':'A','duration_seconds':None}]
    class Client: api=API()
    monkeypatch.setattr('src.acquisition._make_ytm_client',lambda cfg: Client())
    sr=SimpleNamespace(track_name='T',album_name='A',duration_ms=100000)
    cfg=SimpleNamespace(get=lambda k,d=None: 10 if k=='ytmusic.search_limit' else 2000)
    with pytest.raises(AcquisitionError,match='MATCH_NOT_FOUND'):
        search_ytmusic_audio(cfg,sr)


def test_ytdlp_downloader_receives_only_selected_ytmusic_url(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from src.ytm_media import YTMusicMediaClient
    calls = {}
    class FakeYDL:
        def __init__(self, opts):
            calls['opts'] = opts
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def extract_info(self, url, download):
            calls['url'] = url
            calls['download'] = download
            out = Path(calls['opts']['outtmpl'].replace('.%(ext)s', '.mp3'))
            out.write_bytes(b'0' * 20000)
            return {'id':'A','title':'T','duration':100}
    fake = SimpleNamespace(YoutubeDL=FakeYDL, version=SimpleNamespace(__version__='test'))
    monkeypatch.setitem(__import__('sys').modules, 'yt_dlp', fake)
    monkeypatch.setattr('src.ytm_media.duration_ms', lambda p: 100000)
    client = object.__new__(YTMusicMediaClient)
    client.timeout = 60
    client.ytdlp_config = {'audio_quality_kbps': 320, 'format_selector':'bestaudio/best'}
    dest = tmp_path / 'master.mp3'
    result = client.download_audio_mp3('A', dest, spotify_duration_ms=100000, source_url='https://music.youtube.com/watch?v=A')
    assert result['mp3'] == dest
    assert calls['url'] == 'https://music.youtube.com/watch?v=A'
    assert calls['download'] is True
    assert calls['opts']['format'] == 'bestaudio/best'
    assert calls['opts']['noplaylist'] is True
    assert calls['opts']['postprocessors'][0]['preferredquality'] == '320'


def test_ytdlp_downloader_rejects_non_ytmusic_search_url(tmp_path):
    from src.ytm_media import YTMusicMediaClient, YTMusicMediaError
    client = object.__new__(YTMusicMediaClient)
    client.timeout = 60
    client.ytdlp_config = {}
    with pytest.raises(YTMusicMediaError, match='SOURCE_URL_INVALID'):
        client.download_audio_mp3('A', tmp_path/'x.mp3', source_url='https://www.youtube.com/results?search_query=test')


def test_nested_master_info_json_normalizes_without_redownload(tmp_path):
    from src.acquisition import normalized_from_acquisition
    info = {
        'id': 'A',
        'webpage_url': 'https://music.youtube.com/watch?v=A',
        'stream': {'title': 'Bommanu Geesthey', 'duration': 244},
        'yt_dlp': {'title': 'Bommanu Geesthey', 'duration': 244},
        'ytmusic_search': {
            'selected_candidate': {
                'title': 'Bommanu Geesthey',
                'artists': [{'name': 'Devi Sri Prasad'}],
                'album': {'name': 'Bommanu Geesthey'},
            }
        },
    }
    path = tmp_path/'master.info.json'
    path.write_text(json.dumps(info), encoding='utf-8')
    acq = {'info_json': path, 'duration_ms': 244000}
    md = normalized_from_acquisition(acq)
    assert md.title == 'Bommanu Geesthey'
    assert md.artist == 'Devi Sri Prasad'


def test_playlist_metadata_is_final_fallback_for_nested_source(tmp_path):
    from src.acquisition import normalized_from_acquisition
    path = tmp_path/'master.info.json'
    path.write_text(json.dumps({'stream': {}, 'yt_dlp': {}}), encoding='utf-8')
    md = normalized_from_acquisition(
        {'info_json': path, 'duration_ms': 100000},
        playlist_item={'title': 'Fallback Title', 'artists': [{'name': 'Fallback Artist'}], 'album': {'name': 'Fallback Album'}},
    )
    assert md.title == 'Fallback Title'
    assert md.artist == 'Fallback Artist'
    assert md.album == 'Fallback Album'


def test_ytmusic_search_selects_closest_duration_not_first_match(monkeypatch):
    from src.acquisition import search_ytmusic_audio
    class API:
        def search(self,*args,**kwargs):
            return [
                {'videoId':'A','title':'wrong-ish','duration_seconds':200.8},
                {'videoId':'B','title':'better','duration_seconds':200.1},
                {'videoId':'C','title':'exact','duration_seconds':200.0},
            ]
    class Client: api=API()
    monkeypatch.setattr('src.acquisition._make_ytm_client',lambda cfg: Client())
    sr=SimpleNamespace(track_name='T',album_name='A',duration_ms=200000,track_id='S1')
    cfg=SimpleNamespace(get=lambda k,d=None: 10 if k=='ytmusic.search_limit' else (2000 if k=='ytmusic.duration_tolerance_ms' else 60))
    _, selected, candidates = search_ytmusic_audio(cfg,sr)
    assert selected['video_id']=='C'
    assert len(candidates)==3
    assert selected['match_score'] >= max(c['match_score'] for c in candidates if c['video_id'] != 'C')


def test_ytmusic_search_uses_unauthenticated_client_contract(monkeypatch):
    from src import acquisition
    calls = {}
    class FakeClient:
        def __init__(self, auth_file=None, **kwargs): calls["auth"] = auth_file; self.api = type("API", (), {"search": lambda self, *a, **k: []})()
    monkeypatch.setattr(acquisition, "YTMusicMediaClient", FakeClient)
    cfg = SimpleNamespace(root=Path("."), _ytm_client=None, get=lambda k,d=None: d, path=lambda k: Path("browser.json"))
    try: acquisition._make_ytm_client(cfg)
    except Exception: pass
    assert calls["auth"] is None


def test_ytmusic_scoring_prefers_correct_artist_over_tiny_duration_difference(monkeypatch):
    from src.acquisition import search_ytmusic_audio
    class API:
        def search(self,*args,**kwargs):
            return [
                {"videoId":"wrong","title":"Song","artists":[{"name":"Wrong Artist"}],"album":{"name":"Album"},"duration_seconds":180.0},
                {"videoId":"right","title":"Song","artists":[{"name":"Right Artist"}],"album":{"name":"Album"},"duration_seconds":180.8},
            ]
    class Client: api=API()
    monkeypatch.setattr("src.acquisition._make_ytm_client",lambda cfg: Client())
    sr=SimpleNamespace(track_name="Song",artists=["Right Artist"],album_name="Album",duration_ms=180000,track_id="S1")
    cfg=SimpleNamespace(get=lambda k,d=None: 10 if k=='ytmusic.search_limit' else (1000 if k=='ytmusic.duration_tolerance_ms' else 60))
    _, selected, _ = search_ytmusic_audio(cfg,sr)
    assert selected["video_id"] == "right"
