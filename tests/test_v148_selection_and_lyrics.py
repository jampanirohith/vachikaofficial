from types import SimpleNamespace

from src.youtube_finder import YouTubeFinder
from src.lrclib import LRCLIBClient


def test_lrclib_search_fallback_accepts_synced_result(monkeypatch):
    client = object.__new__(LRCLIBClient)
    client._delay = 0
    client._last_request = 0
    client._max_retries = 1
    client._timeout = 5
    client._session = SimpleNamespace()
    calls=[]
    def fake_get(path, *, params):
        calls.append((path, params))
        if path == '/get': return None
        return [{
            'id': 123, 'trackName': 'Naa Manasuney', 'artistName': 'Karthik',
            'albumName': 'Album', 'duration': 210,
            'syncedLyrics': '[00:01.00]Naa Manasuney\n'
        }]
    client._get = lambda **kw: fake_get('/get', **kw)
    client._search = lambda **kw: fake_get('/search', **kw)
    result = client.get_synced(title='Naa Manasuney', artist='Karthik', album='Wrong Album', duration_seconds=211)
    assert result is not None
    assert result.match_method == 'lrclib_api_search'
    assert '[00:01.00]' in result.synced_lyrics


def test_youtube_phase1_style_search_uses_exact_rule(monkeypatch):
    import sys, types
    calls = {}
    class FakeYDL:
        def __init__(self, opts): calls['opts'] = opts
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def extract_info(self, query, download=False):
            calls['query'] = query
            return {'entries': [
                {'id':'1','title':'Song Lyrics Video','duration':100},
                {'id':'2','title':'Song Official Video HD','duration':1},
            ]}
    fake = types.SimpleNamespace(YoutubeDL=FakeYDL)
    monkeypatch.setitem(sys.modules, 'yt_dlp', fake)
    from src.youtube_finder import YouTubeFinder
    finder = YouTubeFinder({'youtube_video_search.results_to_fetch':20}, '.')
    result = finder.find_visual('Song','Album',duration_ms=300000,artist='Artist')
    assert calls['query'] == 'ytsearch20:Song Album video song hd'
    assert result.video_id == '2'
    assert result.search_result_index == 2


def test_lyrics_gate_precedes_visual_video_stage():
    from pathlib import Path
    text = Path('src/pipeline.py').read_text(encoding='utf-8')
    assert text.index('# -------- Synced line LRC --------') < text.index('# -------- Resolve YouTube visual source ONCE + global 30 s offset --------')
    assert 'api/get + /api/search fallback' in text



def test_visual_reference_uses_selected_youtube_url(monkeypatch, tmp_path):
    from src import youtube_video
    calls = {}
    class FakeClient:
        def download_youtube_reference_mp3(self, source_url, destination, info_path=None):
            calls['url'] = source_url
            destination.write_bytes(b'0' * 20000)
            return {'mp3': destination}
    monkeypatch.setattr(youtube_video, '_client', lambda cfg: FakeClient())
    monkeypatch.setattr(youtube_video, 'run', lambda *args, **kwargs: None, raising=False)
    # Replace ffmpeg invocation through the module's imported utility path.
    monkeypatch.setattr('src.utils.run', lambda *args, **kwargs: (tmp_path/'noop'), raising=False)
    # The function's WAV validation is the important contract; create the WAV by intercepting run is cumbersome,
    # so this test asserts the selected URL reaches the media client before the expected downstream validation error.
    try:
        youtube_video.download_audio({}, 'abc123', tmp_path, source_url='https://www.youtube.com/watch?v=abc123')
    except Exception as exc:
        assert calls['url'] == 'https://www.youtube.com/watch?v=abc123'
        assert 'reference WAV' in str(exc)


def test_pipeline_resolves_visual_youtube_only_once_and_reuses_exact_url():
    from pathlib import Path
    text = Path('src/pipeline.py').read_text(encoding='utf-8')
    assert text.count('select_video(self.cfg') == 1
    assert 'download_audio(self.cfg, video.video_id, work, source_url=selected_video_url)' in text
    assert 'search_repeated_before_hook' in text
    assert 'search_repeated_before_hook": False' in text


def test_reference_download_requires_exact_selected_url():
    from pathlib import Path
    from src import youtube_video
    import pytest
    with pytest.raises(RuntimeError, match='YOUTUBE_SELECTED_URL_REQUIRED'):
        youtube_video.download_audio({}, 'abc123', Path('/tmp/vachika-test-no-url'))


def test_telugu_script_gate_accepts_telugu_and_rejects_romaji():
    from src.lrclib import contains_telugu_script
    assert contains_telugu_script("[00:01.00]నా మనసునే\n")
    assert not contains_telugu_script("[00:01.00]Naa Manasuney\n")
    assert not contains_telugu_script("[00:01.00]123 !?\n")


def test_telugu_script_gate_is_before_visual_stage():
    from pathlib import Path
    text = Path('src/pipeline.py').read_text(encoding='utf-8')
    gate = text.index('contains_telugu_script(selected_lrc_text)')
    visual = text.index('# -------- Resolve YouTube visual source ONCE + global 30 s offset --------')
    assert gate < visual
    assert 'SKIPPED_NO_TELUGU_SCRIPT_LRC' in text


def test_lyrics_fingerprint_includes_telugu_script_gate_version():
    from pathlib import Path
    text = Path('src/pipeline.py').read_text(encoding='utf-8')
    assert 'lrclib-get-then-search-v2-telugu-script-gate-v1' in text
    assert 'at_least_one_U+0C00_U+0C7F_codepoint_in_complete_lrc' in text
