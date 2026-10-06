from __future__ import annotations

import json
import subprocess
import uuid
from urllib.parse import urlparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import requests

from .utils import run, duration_ms


class YTMusicMediaError(RuntimeError):
    pass


@dataclass(frozen=True)
class YTMFormat:
    itag: int
    url: str
    mime: str
    bitrate: int
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    content_length: int | None = None
    approx_duration_ms: int | None = None
    has_audio: bool = False
    has_video: bool = False


class YTMusicMediaClient:
    """YT Music API client for direct stream acquisition; legacy download utility is intentionally absent."""

    def __init__(self, auth_file=None, timeout=60, ytdlp_config=None):
        try:
            from ytmusicapi import YTMusic
        except ImportError as exc:
            raise YTMusicMediaError("ytmusicapi is not installed") from exc
        self.api = YTMusic(str(auth_file)) if auth_file else YTMusic()
        self.timeout = max(10, int(timeout))
        self.ytdlp_config = dict(ytdlp_config or {})
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "Mozilla/5.0"})
        self._signature_timestamp: int | None = None

    @staticmethod
    def _formats(streaming: Mapping[str, Any] | None) -> list[YTMFormat]:
        out: list[YTMFormat] = []
        if not isinstance(streaming, Mapping):
            return out
        for bucket in ("formats", "adaptiveFormats"):
            for raw in streaming.get(bucket) or []:
                if not isinstance(raw, Mapping):
                    continue
                url = str(raw.get("url") or "").strip()
                if not url:
                    # `signatureCipher` is deliberately not guessed or decoded here.
                    # A direct URL is required because this implementation does not use a legacy downloader.
                    continue
                mime = str(raw.get("mimeType") or "").split(";", 1)[0].strip().lower()
                audio = mime.startswith("audio/")
                video = mime.startswith("video/")
                if not audio and not video:
                    continue

                def iv(key: str) -> int | None:
                    try:
                        return int(raw[key]) if raw.get(key) is not None else None
                    except (TypeError, ValueError):
                        return None

                def fv(key: str) -> float | None:
                    try:
                        return float(raw[key]) if raw.get(key) is not None else None
                    except (TypeError, ValueError):
                        return None

                out.append(
                    YTMFormat(
                        itag=iv("itag") or 0,
                        url=url,
                        mime=mime,
                        bitrate=iv("bitrate") or iv("averageBitrate") or 0,
                        width=iv("width"),
                        height=iv("height"),
                        fps=fv("fps"),
                        content_length=iv("contentLength"),
                        approx_duration_ms=iv("approxDurationMs"),
                        has_audio=audio,
                        has_video=video,
                    )
                )
        return out

    def _get_song_once(self, video_id: str) -> dict[str, Any]:
        if self._signature_timestamp is None:
            try:
                getter = getattr(self.api, "get_signatureTimestamp", None)
                if getter:
                    self._signature_timestamp = int(getter())
            except Exception:
                self._signature_timestamp = None
        if self._signature_timestamp is not None:
            return self.api.get_song(video_id, signatureTimestamp=self._signature_timestamp)
        return self.api.get_song(video_id)

    def get_song(self, video_id: str) -> dict[str, Any]:
        try:
            data = self._get_song_once(video_id)
        except Exception as exc:
            raise YTMusicMediaError(f"YTM_GET_SONG_FAILED: {type(exc).__name__}: {exc}") from exc
        play = data.get("playabilityStatus") or {}
        status = str(play.get("status") or "")
        if status and status != "OK":
            reason = play.get("reason") or status
            raise YTMusicMediaError(f"YTM_PLAYBACK_UNAVAILABLE: {reason}")
        streaming = data.get("streamingData") or {}
        if not streaming:
            raise YTMusicMediaError(
                "YTM_AUDIO_STREAM_UNAVAILABLE: get_song returned no streamingData. "
                "This can require authentication or a currently playable YT Music source."
            )
        return data

    def _choose_audio(self, data: Mapping[str, Any]) -> YTMFormat:
        formats = [f for f in self._formats(data.get("streamingData")) if f.has_audio]
        if not formats:
            raise YTMusicMediaError(
                "YTM_AUDIO_STREAM_UNAVAILABLE: no direct audio URL was returned by ytmusicapi. "
                "No legacy downloader fallback is permitted by this project."
            )
        formats.sort(key=lambda f: (1 if f.mime == "audio/mp4" else 0, f.bitrate), reverse=True)
        return formats[0]

    def _choose_progressive_video(self, data: Mapping[str, Any], max_height=1080):
        formats = self._formats(data.get("streamingData"))
        candidates = [f for f in formats if f.has_video and f.has_audio and (f.height or 0) <= max_height]
        return max(candidates, key=lambda f: (f.height or 0, f.fps or 0, f.bitrate), default=None)

    def _choose_adaptive_video(self, data: Mapping[str, Any], max_height=1080):
        formats = self._formats(data.get("streamingData"))
        videos = [f for f in formats if f.has_video and (f.height or 0) <= max_height]
        audios = [f for f in formats if f.has_audio]
        video = max(videos, key=lambda f: ((f.height or 0), f.fps or 0, f.bitrate), default=None)
        audio = max(audios, key=lambda f: (1 if f.mime == "audio/mp4" else 0, f.bitrate), default=None)
        return video, audio

    def _download(self, url: str, dst: Path):
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name("." + dst.name + ".part")
        tmp.unlink(missing_ok=True)
        try:
            last_error = None
            for attempt in range(1, 4):
                try:
                    with self.session.get(url, stream=True, timeout=(15, self.timeout)) as response:
                        if not response.ok:
                            raise YTMusicMediaError(f"YTM_STREAM_HTTP_FAILED: HTTP {response.status_code}")
                        with tmp.open("wb") as fh:
                            for chunk in response.iter_content(1024 * 1024):
                                if chunk:
                                    fh.write(chunk)
                    break
                except (requests.RequestException, YTMusicMediaError) as exc:
                    last_error = exc
                    tmp.unlink(missing_ok=True)
                    if attempt == 3:
                        raise
            if not tmp.exists() or tmp.stat().st_size < 1024:
                raise YTMusicMediaError("YTM_STREAM_DOWNLOAD_FAILED: empty/short response")
            tmp.replace(dst)
            return dst
        except Exception as exc:
            if isinstance(exc, YTMusicMediaError):
                raise
            raise YTMusicMediaError(f"YTM_STREAM_DOWNLOAD_FAILED: {exc}") from exc
        finally:
            tmp.unlink(missing_ok=True)

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

    def download_video(self, video_id: str, destination: Path, max_height=1080):
        data = self.get_song(video_id)
        destination = Path(destination)
        progressive = self._choose_progressive_video(data, max_height)
        if progressive:
            src = destination.with_suffix(".source" + (".mp4" if progressive.mime == "video/mp4" else ".bin"))
            self._download(progressive.url, src)
            try:
                if progressive.mime == "video/mp4":
                    src.replace(destination)
                else:
                    run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-c", "copy", "-movflags", "+faststart", str(destination)], timeout=1800)
            finally:
                src.unlink(missing_ok=True)
        else:
            video, audio = self._choose_adaptive_video(data, max_height)
            if not video or not audio:
                raise YTMusicMediaError(
                    "YTM_VIDEO_STREAM_UNAVAILABLE: no direct video+audio URLs were returned by ytmusicapi."
                )
            vf = destination.with_suffix(".video.source")
            af = destination.with_suffix(".audio.source")
            try:
                self._download(video.url, vf)
                self._download(audio.url, af)
                run(
                    ["ffmpeg", "-y", "-v", "error", "-i", str(vf), "-i", str(af), "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(destination)],
                    timeout=1800,
                )
            finally:
                vf.unlink(missing_ok=True)
                af.unlink(missing_ok=True)
        if not destination.exists() or destination.stat().st_size < 10000:
            raise YTMusicMediaError("YTM_VIDEO_DOWNLOAD_FAILED: output missing/empty")
        return destination
