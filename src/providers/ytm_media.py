from __future__ import annotations

import json
import uuid
import time
from urllib.parse import urlparse
from pathlib import Path
from typing import Any, Mapping

from src.common.utils import duration_ms


class YTMusicMediaError(RuntimeError):
    pass


class YTMusicMediaClient:
    """Search YT Music and download only already-selected URLs through yt-dlp."""

    def __init__(self, auth_file=None, timeout=60, ytdlp_config=None):
        try:
            from ytmusicapi import YTMusic
        except ImportError as exc:
            raise YTMusicMediaError("ytmusicapi is not installed") from exc
        self.timeout = max(10, int(timeout))
        # ytmusicapi supports an injected Requests session. Scope the timeout to
        # this client rather than changing requests.sessions.Session.request
        # globally, which could affect unrelated network calls.
        from src.common.http_utils import make_timeout_session
        self.session = make_timeout_session(self.timeout)
        self.api = YTMusic(str(auth_file), requests_session=self.session) if auth_file else YTMusic(requests_session=self.session)
        self.ytdlp_config = dict(ytdlp_config or {})

    @staticmethod
    def _validate_ytmusic_url(url: str) -> str:
        value = str(url or '').strip()
        try:
            parsed = urlparse(value)
        except ValueError as exc:
            raise YTMusicMediaError(f"YTDLP_SOURCE_URL_INVALID: {exc}") from exc
        if parsed.scheme != 'https' or parsed.hostname != 'music.youtube.com' or parsed.path != '/watch':
            raise YTMusicMediaError(
                "YTDLP_SOURCE_URL_INVALID: audio downloader accepts only a selected https://music.youtube.com/watch URL"
            )
        from urllib.parse import parse_qs
        params = parse_qs(parsed.query)
        if not params.get('v') or not params['v'][0].strip():
            raise YTMusicMediaError("YTDLP_SOURCE_URL_INVALID: selected YT Music URL has no video id")
        return value

    def _download_audio_with_ytdlp(self, source_url: str, destination: Path, timeout_seconds: int, allow_youtube: bool = False):
        source_url = self._validate_youtube_video_url(source_url) if allow_youtube else self._validate_ytmusic_url(source_url)
        try:
            import yt_dlp
        except ImportError as exc:
            raise YTMusicMediaError(
                "YTDLP_NOT_INSTALLED: install requirements.txt so yt-dlp is available"
            ) from exc

        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp_base = destination.with_name(f".{destination.stem}.ytdlp-{uuid.uuid4().hex}")
        temp_output = Path(str(temp_base) + '.mp3')
        ydl_cfg = dict(self.ytdlp_config)
        deadline = time.monotonic() + max(1, int(timeout_seconds))
        from src.common.ytdlp_utils import deadline_progress_hook
        format_selector = str(ydl_cfg.get('format_selector') or 'bestaudio/best')
        audio_quality = str(ydl_cfg.get('audio_quality_kbps') or '320')
        opts = {
            'format': format_selector,
            'noplaylist': True,
            'quiet': False,
            'no_warnings': False,
            'noprogress': False,
            'retries': int(ydl_cfg.get('retries', 3)),
            'fragment_retries': int(ydl_cfg.get('fragment_retries', 3)),
            'socket_timeout': int(ydl_cfg.get('socket_timeout_seconds', getattr(self, 'timeout', 60))),
            'progress_hooks': [deadline_progress_hook(yt_dlp, deadline, str(source_url))],
            'outtmpl': str(temp_base) + '.%(ext)s',
            'paths': {'home': str(destination.parent)},
            'postprocessors': [
                {
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': audio_quality,
                }
            ],
            'keepvideo': False,
            'overwrites': True,
            'windowsfilenames': True,
            'prefer_ffmpeg': True,
            'extract_flat': False,
        }
        cookie_file = ydl_cfg.get('cookie_file')
        if cookie_file:
            opts['cookiefile'] = str(cookie_file)
        browser = ydl_cfg.get('cookies_from_browser')
        if browser:
            opts['cookiesfrombrowser'] = (str(browser), None, None, None)

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(source_url, download=True)
        except Exception as exc:
            for residual in destination.parent.glob(temp_base.name + '.*'):
                residual.unlink(missing_ok=True)
            raise YTMusicMediaError(f"YTDLP_AUDIO_DOWNLOAD_FAILED: {type(exc).__name__}: {exc}") from exc

        if not temp_output.exists() or temp_output.stat().st_size < 1024:
            for residual in destination.parent.glob(temp_base.name + '.*'):
                residual.unlink(missing_ok=True)
            raise YTMusicMediaError("YTDLP_AUDIO_DOWNLOAD_FAILED: yt-dlp produced no MP3 output")

        try:
            destination.unlink(missing_ok=True)
            temp_output.replace(destination)
        finally:
            for residual in destination.parent.glob(temp_base.name + '.*'):
                residual.unlink(missing_ok=True)

        if not isinstance(info, Mapping):
            info = {}
        version = getattr(getattr(yt_dlp, 'version', None), '__version__', None)
        source_info = {
            'id': info.get('id'),
            'title': info.get('title'),
            'duration': info.get('duration'),
            'duration_string': info.get('duration_string'),
            'webpage_url': info.get('webpage_url') or source_url,
            'original_url': info.get('original_url') or source_url,
            'extractor': info.get('extractor'),
            'extractor_key': info.get('extractor_key'),
            'format_id': info.get('format_id'),
            'format': info.get('format'),
            'ext': info.get('ext'),
            'protocol': info.get('protocol'),
            'acodec': info.get('acodec'),
            'abr': info.get('abr'),
            'tbr': info.get('tbr'),
            'asr': info.get('asr'),
            'audio_channels': info.get('audio_channels'),
            'filesize': info.get('filesize'),
            'filesize_approx': info.get('filesize_approx'),
            'format_note': info.get('format_note'),
            'language': info.get('language'),
            'uploader': info.get('uploader'),
            'channel': info.get('channel'),
            'source_video_id': info.get('display_id') or info.get('id'),
            'yt_dlp_version': version,
        }
        requested = info.get('requested_formats')
        if isinstance(requested, list):
            source_info['requested_formats'] = [
                {
                    k: item.get(k)
                    for k in ('format_id', 'format', 'ext', 'protocol', 'acodec', 'abr', 'asr', 'filesize', 'filesize_approx')
                    if item.get(k) is not None
                }
                for item in requested if isinstance(item, Mapping)
            ]
        return source_info

    @staticmethod
    def _validate_youtube_video_url(url: str) -> str:
        value = str(url or '').strip()
        try:
            parsed = urlparse(value)
        except ValueError as exc:
            raise YTMusicMediaError(f"YTDLP_VIDEO_SOURCE_URL_INVALID: {exc}") from exc
        if parsed.scheme != 'https' or parsed.hostname not in {'www.youtube.com', 'youtube.com', 'music.youtube.com'} or parsed.path != '/watch':
            raise YTMusicMediaError(
                "YTDLP_VIDEO_SOURCE_URL_INVALID: visual downloader accepts only an https://youtube.com/watch URL"
            )
        from urllib.parse import parse_qs
        params = parse_qs(parsed.query)
        if not params.get('v') or not params['v'][0].strip():
            raise YTMusicMediaError("YTDLP_VIDEO_SOURCE_URL_INVALID: selected YouTube URL has no video id")
        return value

    def download_youtube_reference_mp3(
        self,
        source_url: str,
        destination: Path,
        info_path: Path | None = None,
        download_timeout_seconds: int | None = None,
    ):
        """Download the already-selected public YouTube visual-video URL only.

        This is intentionally separate from authoritative YT Music audio acquisition.
        The caller supplies the exact selected video URL; no title search or replacement
        recording is permitted here.
        """
        source_url = self._validate_youtube_video_url(source_url)
        timeout_seconds = int(download_timeout_seconds or self.ytdlp_config.get('download_timeout_seconds', 1800))
        info = self._download_audio_with_ytdlp(source_url, Path(destination), timeout_seconds, allow_youtube=True)
        if info_path:
            info_path = Path(info_path)
            info_path.parent.mkdir(parents=True, exist_ok=True)
            info_payload = dict(info or {})
            info_payload['source_url_authority'] = 'youtube-search-selected-url'
            info_payload['download_method'] = 'youtube-search-selected-url -> yt-dlp -> FFmpegExtractAudio -> MP3'
            info_path.write_text(json.dumps(info_payload, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
        return {'mp3': Path(destination), 'info_json': info_path, 'duration_ms': duration_ms(Path(destination)), 'format': info, 'raw': {'yt_dlp': info, 'source_url': source_url}}

    def download_audio_mp3(
        self,
        video_id: str,
        destination: Path,
        spotify_duration_ms: int | None = None,
        duration_tolerance_ms: int = 2000,
        info_path: Path | None = None,
        source_url: str | None = None,
        download_timeout_seconds: int | None = None,
        source_metadata: Mapping[str, Any] | None = None,
    ):
        destination = Path(destination)
        source_url = source_url or f"https://music.youtube.com/watch?v={video_id}"
        source_url = self._validate_ytmusic_url(source_url)
        from urllib.parse import parse_qs, urlparse
        source_video_id = (parse_qs(urlparse(source_url).query).get('v') or [''])[0]
        if str(source_video_id) != str(video_id):
            raise YTMusicMediaError(
                f"YTDLP_SOURCE_ID_MISMATCH: selected video_id={video_id!r}, source URL video_id={source_video_id!r}"
            )
        timeout_seconds = int(download_timeout_seconds or self.ytdlp_config.get('download_timeout_seconds', 1800))
        # Do not call get_song() here. ytmusicapi has already selected the source;
        # yt-dlp is deliberately responsible only for fetching the selected URL.
        ytdlp_info = self._download_audio_with_ytdlp(source_url, destination, timeout_seconds)
        ytdlp_info['download_timeout_seconds_configured'] = timeout_seconds
        if not destination.exists() or destination.stat().st_size < 10000:
            raise YTMusicMediaError("YTDLP_AUDIO_DOWNLOAD_FAILED: final MP3 is missing or too small")
        actual_ms = duration_ms(destination)
        if spotify_duration_ms is not None:
            delta = abs(actual_ms - int(spotify_duration_ms))
            if delta > int(duration_tolerance_ms):
                destination.unlink(missing_ok=True)
                raise YTMusicMediaError(
                    f"YTDLP_DURATION_MISMATCH: Spotify={spotify_duration_ms} ms, downloaded={actual_ms} ms, "
                    f"delta={delta} ms > tolerance={duration_tolerance_ms} ms"
                )
        if info_path:
            # Keep a canonical flat metadata layer at the top level.  This is
            # important because downstream metadata code should not have to know
            # whether a field came from ytmusicapi search or yt-dlp extraction.
            selected = dict(source_metadata or {}) if isinstance(source_metadata, Mapping) else {}
            info = {
                'id': video_id,
                'title': selected.get('title') or ytdlp_info.get('title'),
                'artists': selected.get('artists') or selected.get('artist') or ytdlp_info.get('artists'),
                'artist': selected.get('artist') or ytdlp_info.get('artist'),
                'album': selected.get('album') or ytdlp_info.get('album'),
                'duration': ytdlp_info.get('duration') or (int(selected.get('duration_ms', 0)) / 1000 if selected.get('duration_ms') else None),
                'webpage_url': source_url,
                'original_url': source_url,
                'extractor': 'yt-dlp',
                'extractor_key': 'yt-dlp',
                'download_method': 'ytmusicapi-search-selected-url -> yt-dlp -> FFmpegExtractAudio -> MP3',
                'output_audio': {
                    'codec': 'mp3/libmp3lame',
                    'bitrate_kbps': int(self.ytdlp_config.get('audio_quality_kbps', 320)),
                },
                'selection_metadata': selected,
                'stream': ytdlp_info,
                'yt_dlp': ytdlp_info,
                'source_url_authority': 'ytmusicapi-search',
            }
            info_path.parent.mkdir(parents=True, exist_ok=True)
            info_path.write_text(json.dumps(info, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
        return {
            'mp3': destination,
            'info_json': info_path,
            'duration_ms': actual_ms,
            'format': ytdlp_info,
            'raw': {'yt_dlp': ytdlp_info, 'source_url': source_url},
        }
