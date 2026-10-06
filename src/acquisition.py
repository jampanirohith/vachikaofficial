from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from contextlib import contextmanager

import requests

from .metadata import normalize_metadata
from .spotify import SpotifyResult
from .ytm_media import YTMusicMediaClient, YTMusicMediaError


class AcquisitionError(RuntimeError):
    pass


def _duration_to_ms(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        # ytmusicapi's duration_seconds is seconds.
        return int(round(float(value) * 1000))
    text = str(value).strip()
    if not text:
        return None
    if ":" in text:
        try:
            parts = [int(x) for x in text.split(":")]
            seconds = 0
            for part in parts:
                seconds = seconds * 60 + part
            return seconds * 1000
        except ValueError:
            return None
    try:
        return int(round(float(text) * 1000))
    except ValueError:
        return None


@contextmanager
def _requests_timeout(seconds: int):
    """Bound ytmusicapi requests that otherwise omit a timeout (notably visitor-id bootstrap)."""
    original = requests.sessions.Session.request
    timeout = max(5, int(seconds))

    def request(session, method, url, **kwargs):
        kwargs.setdefault("timeout", timeout)
        return original(session, method, url, **kwargs)

    requests.sessions.Session.request = request
    try:
        yield
    finally:
        requests.sessions.Session.request = original


def _make_ytm_client(cfg):
    cached = getattr(cfg, "_ytm_client", None)
    if cached is not None:
        return cached
    auth = cfg.path("ytmusic.auth_file") if cfg.get("ytmusic.auth_file") else None
    if not auth or not Path(auth).exists():
        raise AcquisitionError(
            "YTM_AUTH_REQUIRED: configure ytmusic.auth_file (for example browser.json) "
            "before YT Music search/media acquisition."
        )
    ytdlp_cfg = dict(cfg.get("ytmusic.yt_dlp", {}) or {})
    cookie_file = ytdlp_cfg.get("cookie_file")
    if cookie_file:
        cp = Path(cookie_file)
        ytdlp_cfg["cookie_file"] = str(cp if cp.is_absolute() else cfg.root / cp)
    timeout = int(cfg.get("ytmusic.timeout_seconds", 60))
    ytdlp_cfg.setdefault("download_timeout_seconds", int(cfg.get("ytmusic.download_timeout_seconds", 1800)))
    try:
        with _requests_timeout(timeout):
            client = YTMusicMediaClient(auth, timeout=timeout, ytdlp_config=ytdlp_cfg)
    except Exception as exc:
        raise AcquisitionError(f"YTM_CLIENT_INIT_FAILED: {type(exc).__name__}: {exc}") from exc
    try:
        setattr(cfg, "_ytm_client", client)
    except Exception:
        pass
    return client


def search_ytmusic_audio(cfg, spotify_result: SpotifyResult) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    """Search YT Music with Spotify title + album and select the first duration-matching result."""
    query = f"{spotify_result.track_name} {spotify_result.album_name}".strip() if spotify_result.album_name else spotify_result.track_name.strip()
    limit = max(1, min(50, int(cfg.get("ytmusic.search_limit", 10))))
    tolerance = int(cfg.get("ytmusic.duration_tolerance_ms", 2000))
    client = _make_ytm_client(cfg)
    try:
        with _requests_timeout(int(cfg.get("ytmusic.timeout_seconds", 60))):
            results = client.api.search(query, filter="songs", limit=limit)
    except Exception as exc:
        raise AcquisitionError(f"YTM_AUDIO_SEARCH_FAILED: {type(exc).__name__}: {exc}") from exc

    candidates: list[dict[str, Any]] = []
    for index, item in enumerate(results or [], 1):
        if not isinstance(item, dict) or not item.get("videoId"):
            continue
        candidate_ms = _duration_to_ms(item.get("duration_seconds") if item.get("duration_seconds") is not None else item.get("duration"))
        if candidate_ms is None:
            continue
        delta_ms = abs(candidate_ms - int(spotify_result.duration_ms))
        candidate = {
            "index": index,
            "video_id": str(item["videoId"]),
            "title": item.get("title"),
            "album": item.get("album"),
            "artists": item.get("artists") or [],
            "duration_ms": candidate_ms,
            "delta_ms": delta_ms,
            "raw": dict(item),
        }
        candidates.append(candidate)

    # Never take the first search result merely because it is first. Search the
    # complete result window, then choose the closest known duration. This is the
    # explicit Spotify-duration gate requested by the pipeline contract.
    matches = [c for c in candidates if c["delta_ms"] <= tolerance]
    if not matches:
        raise AcquisitionError(
            f'YTM_AUDIO_MATCH_NOT_FOUND: no YT Music song result for "{query}" matched '
            f"Spotify duration {spotify_result.duration_ms}±{tolerance} ms"
        )
    selected = min(matches, key=lambda c: (c["delta_ms"], c["index"]))
    return query, selected, candidates


def acquire(cfg, work: Path, spotify_result: SpotifyResult):
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    query, candidate, candidates = search_ytmusic_audio(cfg, spotify_result)
    client = _make_ytm_client(cfg)
    mp3 = work / "master.mp3"
    info_json = work / "master.info.json"
    try:
        result = client.download_audio_mp3(
            candidate["video_id"],
            mp3,
            spotify_duration_ms=spotify_result.duration_ms,
            duration_tolerance_ms=int(cfg.get("ytmusic.duration_tolerance_ms", 2000)),
            info_path=info_json,
            source_url=f"https://music.youtube.com/watch?v={candidate['video_id']}",
            download_timeout_seconds=int(cfg.get("ytmusic.download_timeout_seconds", 1800)),
            source_metadata=candidate,
        )
    except YTMusicMediaError as exc:
        raise AcquisitionError(str(exc)) from exc

    info = json.loads(info_json.read_text(encoding="utf-8"))
    info["ytmusic_search"] = {
        "query": query,
        "selection_rule": "closest_duration_within_spotify_tolerance",
        "selected_result_index": candidate["index"],
        "selected_candidate": {k: v for k, v in candidate.items() if k != "raw"},
        "candidates_considered": [{k: v for k, v in x.items() if k != "raw"} for x in candidates],
        "spotify_track_id": spotify_result.track_id,
        "spotify_duration_ms": spotify_result.duration_ms,
        "duration_tolerance_ms": int(cfg.get("ytmusic.duration_tolerance_ms", 2000)),
    }
    info_json.write_text(json.dumps(info, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    return {
        "mp3": mp3,
        "info_json": info_json,
        "duration_ms": int(result["duration_ms"]),
        "ytm_video_id": candidate["video_id"],
        "ytm_url": f"https://music.youtube.com/watch?v={candidate['video_id']}",
        "ytm_title": candidate.get("title"),
        "ytm_query": query,
        "ytm_candidate": candidate,
        "ytm_candidates": candidates,
        "ytm_raw": result.get("raw") or {},
        "artwork_source_url": None,
        "artwork_width": None,
        "artwork_height": None,
    }


def normalized_from_acquisition(acq, playlist_item=None):
    info = json.loads(Path(acq["info_json"]).read_text(encoding="utf-8"))
    # ``master.info.json`` has changed shape across yt-dlp/ytmusicapi revisions.
    # normalize_metadata intentionally understands flat and nested layouts, while
    # playlist_item provides a final authoritative fallback for title/artist/album.
    return normalize_metadata(info, playlist_item=playlist_item, actual_duration=int(round(acq["duration_ms"] / 1000)))
