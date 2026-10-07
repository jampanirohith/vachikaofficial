from __future__ import annotations

import json
from pathlib import Path
from contextlib import contextmanager
import requests

from .youtube_finder import YouTubeFinder
from .ytm_media import YTMusicMediaClient, YTMusicMediaError


@contextmanager
def _requests_timeout(seconds: int):
    original=requests.sessions.Session.request
    timeout=max(5,int(seconds))
    def request(session, method, url, **kwargs):
        kwargs.setdefault("timeout", timeout)
        return original(session, method, url, **kwargs)
    requests.sessions.Session.request=request
    try: yield
    finally: requests.sessions.Session.request=original

def _client(cfg):
    cached=getattr(cfg, '_ytm_client', None)
    if cached is not None:
        return cached
    # Media client is also initialized without YT Music account authentication.
    auth = None
    ytdlp_cfg = dict(cfg.get("ytmusic.yt_dlp", {}) or {})
    cookie_file = ytdlp_cfg.get("cookie_file")
    if cookie_file:
        cp = Path(cookie_file)
        ytdlp_cfg["cookie_file"] = str(cp if cp.is_absolute() else cfg.root / cp)
    timeout=int(cfg.get("ytmusic.timeout_seconds",60))
    ytdlp_cfg.setdefault("download_timeout_seconds", int(cfg.get("ytmusic.download_timeout_seconds", 1800)))
    try:
        with _requests_timeout(timeout):
            client=YTMusicMediaClient(auth, timeout=timeout, ytdlp_config=ytdlp_cfg)
    except Exception as exc:
        raise RuntimeError(f"YTM_CLIENT_INIT_FAILED: {type(exc).__name__}: {exc}") from exc
    try: setattr(cfg,'_ytm_client',client)
    except Exception: pass
    return client


def select_video(cfg, title, album, duration_ms, work, artist=None):
    finder = YouTubeFinder(cfg.data, cfg.root)
    video = finder.find_visual(title, album, duration_ms, artist=artist)
    if video is None:
        raise RuntimeError("YOUTUBE_VISUAL_VIDEO_UNAVAILABLE: no non-lyrics result matched title + album + video song hd")
    out = Path(work) / "youtube"
    out.mkdir(parents=True, exist_ok=True)
    (out / "selected_video.json").write_text(
        json.dumps(
            {
                "query": video.search_query,
                "video": video.raw,
                "video_id": video.video_id,
                "url": video.video_url,
                "title": video.title,
                "duration_seconds": video.duration,
                "search_result_index": video.search_result_index,
                "search_results_fetched": video.search_results_fetched,
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    return video.search_query, video


def download_audio(cfg, video_id, work, source_url=None):
    """Download audio from the already-selected visual URL; never search here."""
    out = Path(work) / "youtube"
    out.mkdir(parents=True, exist_ok=True)
    wav = out / "reference_audio.wav"
    if wav.exists() and wav.stat().st_size > 10000:
        return wav
    mp3 = out / "reference_audio.mp3"
    info = out / "reference_audio.info.json"
    if not source_url:
        raise RuntimeError("YOUTUBE_SELECTED_URL_REQUIRED: reference download must use the exact URL selected by the single visual search")
    client = _client(cfg)
    try:
        client.download_youtube_reference_mp3(source_url, mp3, info_path=info)
        from .utils import run
        run(["ffmpeg", "-y", "-v", "error", "-i", str(mp3), "-ar", "8000", "-ac", "1", "-c:a", "pcm_s16le", "-f", "wav", str(wav)], timeout=900)
    except YTMusicMediaError as exc:
        raise RuntimeError(f"YOUTUBE_REFERENCE_AUDIO_FAILED: {exc}") from exc
    if not wav.exists() or wav.stat().st_size < 10000:
        raise RuntimeError("YOUTUBE_REFERENCE_AUDIO_FAILED: no reference WAV")
    return wav
