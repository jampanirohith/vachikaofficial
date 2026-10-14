from __future__ import annotations

import json
from pathlib import Path

from src.video.selection import YouTubeFinder
from src.providers.ytm_media import YTMusicMediaClient


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
    """Download reference audio for the exact selected video with provenance-bound caching."""
    import os
    from src.common.hashing import sha256_file
    from src.common.utils import run

    out = Path(work) / "youtube"
    out.mkdir(parents=True, exist_ok=True)
    wav = out / "reference_audio.wav"
    mp3 = out / "reference_audio.mp3"
    info = out / "reference_audio.info.json"
    manifest_path = out / "reference_audio.manifest.json"
    source_url = str(source_url or "").strip()
    if not source_url:
        raise RuntimeError("YOUTUBE_SELECTED_URL_REQUIRED: reference download must use the exact URL selected by the single visual search")
    if not source_url.startswith(("https://www.youtube.com/watch?v=", "https://youtube.com/watch?v=", "https://youtu.be/")):
        raise RuntimeError(f"YOUTUBE_SELECTED_URL_INVALID: {source_url}")
    from urllib.parse import parse_qs, urlparse
    parsed_source = urlparse(source_url)
    if parsed_source.hostname == "youtu.be":
        source_video_id = parsed_source.path.strip("/").split("/", 1)[0]
    else:
        source_video_id = (parse_qs(parsed_source.query).get("v") or [""])[0]
    if str(source_video_id) != str(video_id):
        raise RuntimeError(
            f"YOUTUBE_SELECTED_VIDEO_ID_MISMATCH: requested video_id={video_id!r}, URL video_id={source_video_id!r}"
        )

    expected = {
        "schema_version": 2,
        "video_id": str(video_id),
        "source_url": source_url,
        "sample_rate": 8000,
        "channels": 1,
        "derivation_version": "yt-dlp-mp3-to-8k-mono-wav-v2",
    }
    cached_valid = False
    if wav.exists() and wav.stat().st_size > 10000 and manifest_path.exists() and mp3.exists() and info.exists():
        try:
            cached = json.loads(manifest_path.read_text(encoding="utf-8"))
            cached_valid = (
                all(cached.get(k) == v for k, v in expected.items())
                and cached.get("wav_sha256") == sha256_file(wav)
                and cached.get("mp3_sha256") == sha256_file(mp3)
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            cached_valid = False
    if cached_valid:
        return wav

    # Invalidate all artifacts together. Keeping an MP3 from video A while converting
    # it to WAV after selecting video B would poison the subsequent offset estimate.
    for stale in (wav, mp3, info, manifest_path):
        stale.unlink(missing_ok=True)
    client = _client(cfg)
    try:
        client.download_youtube_reference_mp3(source_url, mp3, info_path=info)
        tmp_wav = wav.with_name(f".{wav.stem}.tmp-{os.getpid()}.wav")
        try:
            run(["ffmpeg", "-y", "-v", "error", "-i", str(mp3), "-ar", "8000", "-ac", "1", "-c:a", "pcm_s16le", "-f", "wav", str(tmp_wav)], timeout=900)
            if not tmp_wav.exists() or tmp_wav.stat().st_size < 10000:
                raise RuntimeError("FFmpeg produced an empty or unusable reference WAV")
            tmp_wav.replace(wav)
        finally:
            tmp_wav.unlink(missing_ok=True)
    except Exception as exc:
        for stale in (wav, mp3, info, manifest_path):
            stale.unlink(missing_ok=True)
        if isinstance(exc, RuntimeError) and str(exc).startswith("YOUTUBE_REFERENCE_AUDIO_FAILED:"):
            raise
        raise RuntimeError(f"YOUTUBE_REFERENCE_AUDIO_FAILED: {type(exc).__name__}: {exc}") from exc
    if not wav.exists() or wav.stat().st_size < 10000:
        raise RuntimeError("YOUTUBE_REFERENCE_AUDIO_FAILED: no reference WAV")
    from src.common.utils import duration_ms
    meta = {
        **expected,
        "mp3_path": str(mp3),
        "mp3_sha256": sha256_file(mp3),
        "wav_path": str(wav),
        "wav_sha256": sha256_file(wav),
        "duration_ms": duration_ms(wav),
    }
    tmp_manifest = manifest_path.with_name(manifest_path.name + ".tmp")
    tmp_manifest.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_manifest.replace(manifest_path)
    return wav
