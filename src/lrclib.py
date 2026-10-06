from __future__ import annotations

from dataclasses import dataclass, field
import re
import time
from typing import Any, Mapping

import requests
from requests import RequestException

LRCLIB_API_URL = "https://lrclib.net/api"
TIMESTAMP_RE = re.compile(r"\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\]")


class LRCError(RuntimeError):
    pass


@dataclass(frozen=True)
class LyricsResult:
    lyrics_id: int | None
    track_name: str | None
    artist_name: str | None
    album_name: str | None
    duration: int | None
    instrumental: bool | None
    synced_lyrics: str
    query_track: str
    query_artist: str
    query_album: str
    duration_delta_seconds: float | None
    match_method: str
    raw: Mapping[str, Any] = field(repr=False)


class LRCLIBClient:
    """Robust LRCLIB client: exact lookup first, then indexed search fallback.

    Phase-1 used only /api/get. That is fast but brittle because an album/duration mismatch
    can return no record even when LRCLIB has synced lyrics for the same song. We retain the
    exact lookup, then use /api/search and rank only candidates that actually contain synced
    lyrics.
    """

    def __init__(self, config: Mapping[str, Any]) -> None:
        self.config = config
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": str(config.get("user_agent") or "VachikaUnifiedPipeline/1.4.8 (LRCLIB client)"),
        })
        self._timeout = max(5, int(config.get("timeout_seconds", 30)))
        self._max_retries = max(1, int(config.get("max_retries", 3)))
        self._delay = max(0.2, float(config.get("request_delay_seconds", 0.5)))
        self._last_request = 0.0

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < self._delay:
            time.sleep(self._delay - elapsed)
        self._last_request = time.monotonic()

    def _request_json(self, path: str, *, params: Mapping[str, Any]) -> Any:
        last_error = ""
        for attempt in range(1, self._max_retries + 1):
            self._throttle()
            try:
                response = self._session.get(f"{LRCLIB_API_URL}{path}", params=dict(params), timeout=self._timeout)
            except RequestException as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt < self._max_retries:
                    time.sleep(0.75 * attempt)
                    continue
                break
            if response.status_code == 404:
                return None
            if response.status_code == 429:
                retry_after_raw = response.headers.get("Retry-After", "1") or "1"
                try: retry_after = float(retry_after_raw)
                except ValueError: retry_after = 1.0
                if attempt < self._max_retries:
                    time.sleep(max(self._delay, min(retry_after, 60.0)))
                    continue
            if 500 <= response.status_code < 600 and attempt < self._max_retries:
                time.sleep(0.75 * attempt)
                continue
            if not response.ok:
                last_error = f"HTTP {response.status_code}: {response.text[:500]}"
                break
            try:
                return response.json()
            except ValueError as exc:
                raise LRCError(f"LRCLIB returned invalid JSON from {path}") from exc
        raise LRCError(f"LRCLIB request failed for {path}: {last_error or 'unknown error'}")

    def _get(self, *, params: Mapping[str, Any]) -> Any:
        return self._request_json("/get", params=params)

    def _search(self, *, params: Mapping[str, Any]) -> Any:
        return self._request_json("/search", params=params)

    @staticmethod
    def _duration_delta(candidate: Any, target: int | None) -> float | None:
        if candidate is None or target is None: return None
        try: return abs(float(candidate) - float(target))
        except (TypeError, ValueError): return None

    @staticmethod
    def is_valid_synced_lyrics(value: Any) -> bool:
        if not isinstance(value, str) or not value.strip(): return False
        return any(TIMESTAMP_RE.search(line) for line in value.splitlines() if line.strip())

    @staticmethod
    def _norm(value: Any) -> str:
        value = str(value or "").casefold()
        value = re.sub(r"\b(feat(?:uring)?|ft)\.?\b", " ", value)
        return "".join(ch for ch in value if ch.isalnum())

    @classmethod
    def _token_score(cls, candidate: Any, target: str) -> float:
        a = cls._norm(candidate); b = cls._norm(target)
        if not a or not b: return 0.0
        if a == b: return 1.0
        if a in b or b in a: return 0.9
        at=set(re.findall(r"[a-z0-9]+", str(candidate or '').casefold()))
        bt=set(re.findall(r"[a-z0-9]+", str(target or '').casefold()))
        return len(at & bt) / max(1, len(bt))

    @classmethod
    def _result_from_payload(cls, payload: Mapping[str, Any], *, title: str, artist: str, album: str | None, duration_seconds: int | None, method: str) -> LyricsResult:
        candidate_duration = payload.get("duration")
        try: duration = int(round(float(candidate_duration))) if candidate_duration is not None else None
        except (TypeError, ValueError): duration = None
        lyrics_id_value = payload.get("lyricsId", payload.get("id"))
        lyrics_id = int(lyrics_id_value) if isinstance(lyrics_id_value, (int,float,str)) and str(lyrics_id_value).isdigit() else None
        synced = str(payload.get("syncedLyrics") or "").rstrip() + "\n"
        return LyricsResult(
            lyrics_id=lyrics_id,
            track_name=str(payload.get("trackName") or payload.get("name") or "") or None,
            artist_name=str(payload.get("artistName") or "") or None,
            album_name=str(payload.get("albumName") or "") or None,
            duration=duration,
            instrumental=bool(payload.get("instrumental")) if payload.get("instrumental") is not None else None,
            synced_lyrics=synced,
            query_track=title,
            query_artist=artist,
            query_album=album or "",
            duration_delta_seconds=cls._duration_delta(candidate_duration, duration_seconds),
            match_method=method,
            raw=dict(payload),
        )

    def _accept_payload(self, payload: Any, *, title: str, artist: str, album: str | None, duration_seconds: int | None, method: str) -> LyricsResult | None:
        if not isinstance(payload, Mapping) or not self.is_valid_synced_lyrics(payload.get("syncedLyrics")):
            return None
        return self._result_from_payload(payload, title=title, artist=artist, album=album, duration_seconds=duration_seconds, method=method)

    def get_synced(self, *, title: str, artist: str, album: str | None, duration_seconds: int | None, alternate_signatures: list[tuple[str, str, str | None, int | None]] | None = None) -> LyricsResult | None:
        signatures: list[tuple[str, str, str | None, int | None]] = [(title, artist, album, duration_seconds)]
        for signature in alternate_signatures or []:
            if signature not in signatures: signatures.append(signature)

        # Phase-1 exact lookups, including metadata variants supplied by the caller.
        for track_name, artist_name, album_name, duration in signatures:
            if not track_name or not artist_name or duration is None: continue
            params = {"track_name": track_name, "artist_name": artist_name, "album_name": album_name or "", "duration": int(duration)}
            candidate = self._accept_payload(self._get(params=params), title=track_name, artist=artist_name, album=album_name, duration_seconds=duration, method="lrclib_api_get")
            if candidate is not None:
                return candidate

        # Fallback: search the LRCLIB index. Try title+artist first, then title alone.
        queries = []
        for q in (f"{title} {artist}", title):
            q = " ".join(str(q or "").split())
            if q and q not in queries: queries.append(q)
        candidates: list[tuple[float, LyricsResult]] = []
        for q in queries:
            payload = self._search(params={"q": q})
            if not isinstance(payload, list): continue
            for item in payload:
                if not isinstance(item, Mapping) or not self.is_valid_synced_lyrics(item.get("syncedLyrics")):
                    continue
                title_score = self._token_score(item.get("trackName"), title)
                artist_score = self._token_score(item.get("artistName"), artist)
                album_score = self._token_score(item.get("albumName"), album or "") if album else 0.5
                delta = self._duration_delta(item.get("duration"), duration_seconds)
                duration_score = 1.0 if delta is None else max(0.0, 1.0 - min(delta, 30.0) / 30.0)
                # Strong title/artist identity; album and duration break ties rather than gate.
                score = 0.52 * title_score + 0.33 * artist_score + 0.10 * album_score + 0.05 * duration_score
                result = self._result_from_payload(item, title=title, artist=artist, album=album, duration_seconds=duration_seconds, method="lrclib_api_search")
                candidates.append((score, result))
        if not candidates: return None
        candidates.sort(key=lambda x: (-x[0], x[1].duration_delta_seconds if x[1].duration_delta_seconds is not None else 10**9))
        best_score, best = candidates[0]
        # Do not accept an unrelated search hit merely because it has synced lyrics.
        if self._token_score(best.track_name, title) < 0.55 or self._token_score(best.artist_name, artist) < 0.45:
            return None
        return best
