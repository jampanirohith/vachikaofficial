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
    # YT Music catalog search is intentionally unauthenticated. Authentication
    # is not used to improve search ranking and must not be a pipeline prerequisite.
    auth = None
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


def _norm_text(value: Any) -> str:
    import re
    import unicodedata
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return "".join(ch for ch in text if ch.isalnum() or ch.isspace()).strip()


def _tokens(value: Any) -> set[str]:
    return {x for x in _norm_text(value).split() if x}


def _candidate_names(value: Any) -> list[str]:
    if isinstance(value, dict):
        name = value.get("name") or value.get("title")
        return [str(name).strip()] if name else []
    if isinstance(value, (list, tuple)):
        out: list[str] = []
        for item in value:
            out.extend(_candidate_names(item))
        return out
    return [str(value).strip()] if value else []


def _text_similarity(expected: str, candidates: list[str]) -> tuple[float, str]:
    expected_n = _norm_text(expected)
    if not expected_n:
        return 0.0, "none"
    expected_tokens = _tokens(expected_n)
    best = 0.0
    kind = "none"
    for raw in candidates:
        actual_n = _norm_text(raw)
        if not actual_n:
            continue
        actual_tokens = _tokens(actual_n)
        if actual_n == expected_n:
            score, label = 1.0, "exact"
        elif expected_n in actual_n or actual_n in expected_n:
            score, label = 0.85, "contains"
        elif expected_tokens and actual_tokens:
            overlap = len(expected_tokens & actual_tokens) / len(expected_tokens | actual_tokens)
            score, label = overlap, "token_overlap"
        else:
            score, label = 0.0, "none"
        if score > best:
            best, kind = score, label
    return best, kind


def _score_ytmusic_candidate(candidate: dict[str, Any], spotify_result: SpotifyResult, tolerance: int) -> tuple[float, dict[str, Any]]:
    title_score, title_kind = _text_similarity(spotify_result.track_name, _candidate_names(candidate.get("title")))
    artist_expected = getattr(spotify_result, "artist_string", ", ".join(getattr(spotify_result, "artists", []) or []))
    artist_score, artist_kind = _text_similarity(artist_expected, _candidate_names(candidate.get("artists")))
    album_expected = spotify_result.album_name or ""
    album_score, album_kind = _text_similarity(album_expected, _candidate_names(candidate.get("album"))) if album_expected else (0.0, "not_requested")
    duration_score = max(0.0, 1.0 - (candidate["delta_ms"] / max(1, tolerance)))

    # Identity is more important than search-result position or tiny duration differences.
    # Title + artist establish the recording; album and duration break legitimate ties.
    score = (
        45.0 * title_score
        + 35.0 * artist_score
        + 10.0 * album_score
        + 10.0 * duration_score
    )
    breakdown = {
        "title_score": round(title_score, 6),
        "title_match": title_kind,
        "artist_score": round(artist_score, 6),
        "artist_match": artist_kind,
        "album_score": round(album_score, 6),
        "album_match": album_kind,
        "duration_score": round(duration_score, 6),
        "total_score": round(score, 6),
    }
    return score, breakdown


def search_ytmusic_audio(cfg, spotify_result: SpotifyResult) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    """Search unauthenticated YT Music and select the strongest Spotify-matching song candidate."""
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
        score, breakdown = _score_ytmusic_candidate(candidate, spotify_result, tolerance)
        candidate["match_score"] = score
        candidate["match_breakdown"] = breakdown
        candidates.append(candidate)

    matches = [c for c in candidates if c["delta_ms"] <= tolerance]
    if not matches:
        raise AcquisitionError(
            f'YTM_AUDIO_MATCH_NOT_FOUND: no YT Music song result for "{query}" matched '
            f"Spotify duration {spotify_result.duration_ms}±{tolerance} ms"
        )
    selected = max(matches, key=lambda c: (c["match_score"], -c["delta_ms"], -c["index"]))
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
        "selection_rule": "strongest_title_artist_album_duration_match_within_spotify_tolerance",
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
