from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

import requests
from requests import RequestException

from .metadata import normalize_isrc, _normalize_date

SPOTIFY_ACCOUNTS_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_API_URL = "https://api.spotify.com/v1"


class SpotifyError(RuntimeError):
    pass


@dataclass(frozen=True)
class SpotifyImage:
    url: str
    width: int | None
    height: int | None


@dataclass(frozen=True)
class SpotifyResult:
    track_id: str
    track_name: str
    track_url: str | None
    uri: str | None
    artists: list[str]
    artist_ids: list[str]
    artist_urls: list[str]
    album_name: str | None
    album_id: str | None
    album_url: str | None
    album_artists: list[str]
    album_artist_ids: list[str]
    album_type: str | None
    album_release_date: str | None
    album_release_precision: str | None
    album_total_tracks: int | None
    album_artwork_url: str | None
    album_artwork_width: int | None
    album_artwork_height: int | None
    album_images: list[SpotifyImage]
    album_label: str | None
    album_copyrights: list[str]
    artist_genres: list[str]
    duration_ms: int
    explicit: bool | None
    isrc: str | None
    track_number: int | None
    disc_number: int | None
    raw: Mapping[str, Any] = field(repr=False)
    album_raw: Mapping[str, Any] = field(repr=False)
    artists_raw: list[Mapping[str, Any]] = field(repr=False, default_factory=list)
    artist_details: list[Mapping[str, Any]] = field(repr=False, default_factory=list)

    @property
    def artist_string(self) -> str:
        return ", ".join(self.artists)

    @property
    def album_artist_string(self) -> str | None:
        return ", ".join(self.album_artists) or self.artist_string or None


def playlist_id_from_value(value: str) -> str:
    text = str(value or "").strip()
    if "playlist/" in text:
        text = text.split("playlist/", 1)[1].split("?", 1)[0].split("/", 1)[0]
    return text


class SpotifyClient:
    """Small Web API client for playlist snapshots and complete per-track catalog enrichment."""

    def __init__(self, config: Mapping[str, Any], project_root: str | Path | None = None):
        self.config = dict(config)
        self.project_root = Path(project_root or ".").resolve()
        self._session = requests.Session()
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._max_retries = max(1, int(self.config.get("max_retries", 3)))
        self._timeout = max(5, int(self.config.get("timeout_seconds", 30)))
        self._client_id = str(self.config.get("client_id") or "").strip()
        self._client_secret = str(self.config.get("client_secret") or "").strip()
        self._access_token = str(self.config.get("access_token") or "").strip() or None
        self._refresh_token = str(self.config.get("refresh_token") or "").strip() or None
        self._auth_file: Path | None = None
        auth_file = self.config.get("auth_file")
        if auth_file:
            self._auth_file = Path(str(auth_file))
            if not self._auth_file.is_absolute():
                self._auth_file = self.project_root / self._auth_file
            if self._auth_file.exists():
                try:
                    auth = json.loads(self._auth_file.read_text(encoding="utf-8"))
                    self._client_id = self._client_id or str(auth.get("client_id") or "").strip()
                    self._client_secret = self._client_secret or str(auth.get("client_secret") or "").strip()
                    self._access_token = self._access_token or str(auth.get("access_token") or "").strip() or None
                    self._refresh_token = self._refresh_token or str(auth.get("refresh_token") or "").strip() or None
                    self._token_expires_at = float(auth.get("expires_at") or 0)
                except Exception as exc:
                    raise SpotifyError(f"Invalid Spotify auth file: {self._auth_file}: {exc}") from exc
        if bool(self.config.get("use_env", True)):
            self._client_id = os.getenv("SPOTIFY_CLIENT_ID", self._client_id).strip()
            self._client_secret = os.getenv("SPOTIFY_CLIENT_SECRET", self._client_secret).strip()
            self._access_token = os.getenv("SPOTIFY_ACCESS_TOKEN", self._access_token or "").strip() or self._access_token
            self._refresh_token = os.getenv("SPOTIFY_REFRESH_TOKEN", self._refresh_token or "").strip() or self._refresh_token
        self.market = str(self.config.get("market") or "IN").strip().upper() or None
        self._album_cache: dict[str, dict[str, Any]] = {}
        self._artist_cache: dict[str, dict[str, Any]] = {}

    @property
    def configured(self) -> bool:
        return bool(self._access_token or self._refresh_token or (self._client_id and self._client_secret))

    @property
    def has_user_auth(self) -> bool:
        return bool(self._access_token or self._refresh_token)

    def _save_refreshed_auth(self) -> None:
        if not self._auth_file or not self._refresh_token:
            return
        payload = {
            "access_token": self._access_token,
            "refresh_token": self._refresh_token,
            "token_type": "Bearer",
            "expires_at": int(self._token_expires_at),
            "client_id": self._client_id,
        }
        tmp = self._auth_file.with_suffix(self._auth_file.suffix + ".tmp")
        try:
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            tmp.replace(self._auth_file)
        except OSError:
            tmp.unlink(missing_ok=True)

    def _get_token(self, force_refresh: bool = False) -> str:
        now = time.time()
        if self._token and not force_refresh and now < self._token_expires_at - 60:
            return self._token

        if self._refresh_token and (force_refresh or not self._access_token or now >= self._token_expires_at - 60):
            data = {"grant_type": "refresh_token", "refresh_token": self._refresh_token}
            headers = {"Content-Type": "application/x-www-form-urlencoded"}
            if self._client_secret:
                basic = base64.b64encode(f"{self._client_id}:{self._client_secret}".encode()).decode("ascii")
                headers["Authorization"] = f"Basic {basic}"
            else:
                # PKCE refresh tokens are refreshed using client_id in the body.
                data["client_id"] = self._client_id
            try:
                response = self._session.post(SPOTIFY_ACCOUNTS_URL, headers=headers, data=data, timeout=self._timeout)
            except RequestException as exc:
                raise SpotifyError(f"Spotify refresh-token request failed: {type(exc).__name__}: {exc}") from exc
            if not response.ok:
                raise SpotifyError(f"Spotify refresh-token request failed: HTTP {response.status_code}: {response.text[:500]}")
            payload = response.json()
            token = payload.get("access_token")
            if not token:
                raise SpotifyError("Spotify refresh-token response contained no access_token")
            self._token = str(token)
            self._access_token = self._token
            self._token_expires_at = time.time() + float(payload.get("expires_in") or 3600)
            if payload.get("refresh_token"):
                self._refresh_token = str(payload["refresh_token"])
            self._save_refreshed_auth()
            return self._token

        if self._access_token:
            self._token = self._access_token
            self._token_expires_at = now + 300
            return self._token

        if not (self._client_id and self._client_secret):
            raise SpotifyError(
                "Spotify credentials are not configured. Use scripts/setup_spotify_auth.py for a user token "
                "or provide SPOTIFY_CLIENT_ID/SPOTIFY_CLIENT_SECRET for public-resource access."
            )

        basic = base64.b64encode(f"{self._client_id}:{self._client_secret}".encode()).decode("ascii")
        try:
            response = self._session.post(
                SPOTIFY_ACCOUNTS_URL,
                headers={"Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"},
                data={"grant_type": "client_credentials"},
                timeout=self._timeout,
            )
        except RequestException as exc:
            raise SpotifyError(f"Spotify client-credentials request failed: {type(exc).__name__}: {exc}") from exc
        if not response.ok:
            raise SpotifyError(f"Spotify client-credentials request failed: HTTP {response.status_code}: {response.text[:500]}")
        payload = response.json()
        self._token = str(payload.get("access_token") or "")
        if not self._token:
            raise SpotifyError("Spotify token response contained no access_token")
        self._token_expires_at = time.time() + float(payload.get("expires_in") or 3600)
        return self._token

    def _request(self, method: str, path: str, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        last = ""
        refreshed = False
        for attempt in range(1, self._max_retries + 1):
            token = self._get_token(force_refresh=refreshed)
            try:
                response = self._session.request(
                    method,
                    f"{SPOTIFY_API_URL}{path}",
                    headers={"Authorization": f"Bearer {token}"},
                    params=dict(params or {}),
                    timeout=self._timeout,
                )
            except RequestException as exc:
                last = f"{type(exc).__name__}: {exc}"
                if attempt < self._max_retries:
                    time.sleep(0.75 * attempt)
                    continue
                break
            if response.status_code == 401 and self._refresh_token and not refreshed and attempt < self._max_retries:
                self._token = None
                self._token_expires_at = 0
                refreshed = True
                continue
            if response.status_code == 429 and attempt < self._max_retries:
                try:
                    wait = float(response.headers.get("Retry-After", "1") or "1")
                except ValueError:
                    wait = 1
                time.sleep(max(0.2, min(wait, 60)))
                continue
            if 500 <= response.status_code < 600 and attempt < self._max_retries:
                time.sleep(0.75 * attempt)
                continue
            if not response.ok:
                last = f"HTTP {response.status_code}: {response.text[:500]}"
                break
            try:
                payload = response.json()
            except ValueError as exc:
                raise SpotifyError(f"Spotify API returned invalid JSON for {path}") from exc
            if not isinstance(payload, dict):
                raise SpotifyError(f"Spotify API returned unexpected JSON for {path}")
            return payload
        if last.startswith("HTTP 403") and not self.has_user_auth:
            raise SpotifyError(
                "Spotify returned HTTP 403. This playlist may require a user-authorized token. "
                "Run scripts/setup_spotify_auth.py and use the generated spotify_auth.json."
            )
        raise SpotifyError(f"Spotify API request failed for {path}: {last or 'unknown error'}")

    def get_playlist(self, playlist_id: str) -> dict[str, Any]:
        pid = playlist_id_from_value(playlist_id)
        return self._request("GET", f"/playlists/{pid}", params={"market": self.market} if self.market else {})

    def has_playlist_read_auth(self) -> bool:
        return self.has_user_auth or bool(self._client_id and self._client_secret)

    def get_playlist_items(self, playlist_id: str, progress=None) -> list[dict[str, Any]]:
        pid = playlist_id_from_value(playlist_id)
        out: list[dict[str, Any]] = []
        offset = 0
        limit = 50
        while True:
            params: dict[str, Any] = {"limit": limit, "offset": offset, "additional_types": "track"}
            if self.market:
                params["market"] = self.market
            page = self._request("GET", f"/playlists/{pid}/items", params=params)
            items = page.get("items")
            if items is None and isinstance(page.get("tracks"), Mapping):
                items = page["tracks"].get("items")
            page_items = [dict(x) for x in (items or []) if isinstance(x, Mapping)]
            out.extend(page_items)
            total = int(page.get("total") or len(out))
            if progress:
                progress(len(out), total)
            if not page_items or len(out) >= total:
                break
            offset += len(page_items)
            if not page.get("next") and len(page_items) < limit:
                break
        return out

    def fetch_playlist_snapshot(self, playlist_id: str, progress=None):
        meta = self.get_playlist(playlist_id)
        items = self.get_playlist_items(playlist_id, progress=progress)
        return meta, items, playlist_id_from_value(playlist_id)

    @staticmethod
    def _artist_values(artists: Any):
        names: list[str] = []
        ids: list[str] = []
        urls: list[str] = []
        for artist in artists or []:
            if not isinstance(artist, Mapping):
                continue
            if artist.get("name"):
                names.append(str(artist["name"]))
            if artist.get("id"):
                ids.append(str(artist["id"]))
            eu = artist.get("external_urls") if isinstance(artist.get("external_urls"), Mapping) else {}
            if eu.get("spotify"):
                urls.append(str(eu["spotify"]))
        return names, ids, urls

    @staticmethod
    def _images(album: Any) -> list[SpotifyImage]:
        result: list[SpotifyImage] = []
        for img in album.get("images") or [] if isinstance(album, Mapping) else []:
            if not isinstance(img, Mapping) or not img.get("url"):
                continue
            try:
                width = int(img["width"]) if img.get("width") is not None else None
            except (TypeError, ValueError):
                width = None
            try:
                height = int(img["height"]) if img.get("height") is not None else None
            except (TypeError, ValueError):
                height = None
            result.append(SpotifyImage(str(img["url"]), width, height))
        return result

    def get_track(self, track_id: str) -> dict[str, Any]:
        params = {"market": self.market} if self.market else {}
        return self._request("GET", f"/tracks/{track_id}", params=params)

    def get_album(self, album_id: str) -> dict[str, Any]:
        if album_id not in self._album_cache:
            self._album_cache[album_id] = self._request("GET", f"/albums/{album_id}", params={"market": self.market} if self.market else {})
        return self._album_cache[album_id]

    def get_artist(self, artist_id: str) -> dict[str, Any]:
        if artist_id not in self._artist_cache:
            self._artist_cache[artist_id] = self._request("GET", f"/artists/{artist_id}")
        return self._artist_cache[artist_id]

    def get_full_track(self, track_id: str) -> SpotifyResult:
        track = self.get_track(track_id)
        album_obj = track.get("album") if isinstance(track.get("album"), Mapping) else {}
        album_id = str(album_obj.get("id") or "").strip() or None
        album_raw = self.get_album(album_id) if album_id else dict(album_obj)

        artist_details: list[dict[str, Any]] = []
        seen_artist_ids: set[str] = set()
        for artist in list(track.get("artists") or []) + list(album_raw.get("artists") or []):
            if not isinstance(artist, Mapping):
                continue
            aid = str(artist.get("id") or "").strip()
            if aid and aid in seen_artist_ids:
                continue
            if aid:
                seen_artist_ids.add(aid)
            artist_details.append(self.get_artist(aid) if aid else dict(artist))

        track_artists, artist_ids, artist_urls = self._artist_values(track.get("artists"))
        album_artists, album_artist_ids, _ = self._artist_values(album_raw.get("artists"))
        images = self._images(album_raw) or self._images(album_obj)
        largest = max(images, key=lambda x: ((x.width or 0) * (x.height or 0), x.width or 0, x.height or 0), default=None)
        copyrights = [str(c["text"]) for c in album_raw.get("copyrights") or [] if isinstance(c, Mapping) and c.get("text")]
        genres: list[str] = []
        for detail in artist_details:
            for genre in detail.get("genres") or []:
                if genre and str(genre) not in genres:
                    genres.append(str(genre))

        ext_urls = track.get("external_urls") if isinstance(track.get("external_urls"), Mapping) else {}
        ext_ids = track.get("external_ids") if isinstance(track.get("external_ids"), Mapping) else {}
        album_urls = album_raw.get("external_urls") if isinstance(album_raw.get("external_urls"), Mapping) else album_obj.get("external_urls")
        duration = int(track.get("duration_ms") or 0)
        total_tracks = album_raw.get("total_tracks") or album_obj.get("total_tracks")

        return SpotifyResult(
            track_id=str(track.get("id") or track_id),
            track_name=str(track.get("name") or ""),
            track_url=str(ext_urls.get("spotify") or "") or None,
            uri=str(track.get("uri") or "") or None,
            artists=track_artists,
            artist_ids=artist_ids,
            artist_urls=artist_urls,
            album_name=str(album_raw.get("name") or album_obj.get("name") or "") or None,
            album_id=album_id,
            album_url=str(album_urls.get("spotify") or "") if isinstance(album_urls, Mapping) and album_urls.get("spotify") else None,
            album_artists=album_artists,
            album_artist_ids=album_artist_ids,
            album_type=str(album_raw.get("album_type") or album_obj.get("album_type") or "") or None,
            album_release_date=str(album_raw.get("release_date") or album_obj.get("release_date") or "") or None,
            album_release_precision=str(album_raw.get("release_date_precision") or album_obj.get("release_date_precision") or "") or None,
            album_total_tracks=int(total_tracks) if total_tracks is not None else None,
            album_artwork_url=largest.url if largest else None,
            album_artwork_width=largest.width if largest else None,
            album_artwork_height=largest.height if largest else None,
            album_images=images,
            album_label=str(album_raw.get("label") or "") or None,
            album_copyrights=copyrights,
            artist_genres=genres,
            duration_ms=duration,
            explicit=bool(track.get("explicit")) if track.get("explicit") is not None else None,
            isrc=normalize_isrc(ext_ids.get("isrc")),
            track_number=int(track.get("track_number")) if track.get("track_number") is not None else None,
            disc_number=int(track.get("disc_number")) if track.get("disc_number") is not None else None,
            raw=dict(track),
            album_raw=dict(album_raw),
            artists_raw=[dict(x) for x in (track.get("artists") or []) if isinstance(x, Mapping)],
            artist_details=[dict(x) for x in artist_details],
        )

    @staticmethod
    def to_json(result: SpotifyResult | None):
        if result is None:
            return None
        data = {k: getattr(result, k) for k in result.__dataclass_fields__}
        data["album_images"] = [img.__dict__ for img in result.album_images]
        return data

    @staticmethod
    def from_json(data: Mapping[str, Any]) -> SpotifyResult:
        images = [SpotifyImage(str(x.get("url")), x.get("width"), x.get("height")) for x in data.get("album_images") or [] if isinstance(x, Mapping) and x.get("url")]
        def mapping_list(value):
            return [dict(x) for x in value or [] if isinstance(x, Mapping)]
        return SpotifyResult(
            track_id=str(data.get("track_id") or ""), track_name=str(data.get("track_name") or ""),
            track_url=data.get("track_url"), uri=data.get("uri"), artists=list(data.get("artists") or []),
            artist_ids=list(data.get("artist_ids") or []), artist_urls=list(data.get("artist_urls") or []),
            album_name=data.get("album_name"), album_id=data.get("album_id"), album_url=data.get("album_url"),
            album_artists=list(data.get("album_artists") or []), album_artist_ids=list(data.get("album_artist_ids") or []),
            album_type=data.get("album_type"), album_release_date=data.get("album_release_date"),
            album_release_precision=data.get("album_release_precision"),
            album_total_tracks=data.get("album_total_tracks"), album_artwork_url=data.get("album_artwork_url"),
            album_artwork_width=data.get("album_artwork_width"), album_artwork_height=data.get("album_artwork_height"),
            album_images=images, album_label=data.get("album_label"), album_copyrights=list(data.get("album_copyrights") or []),
            artist_genres=list(data.get("artist_genres") or []), duration_ms=int(data.get("duration_ms") or 0),
            explicit=data.get("explicit"), isrc=data.get("isrc"), track_number=data.get("track_number"), disc_number=data.get("disc_number"),
            raw=data.get("raw") or {}, album_raw=data.get("album_raw") or {}, artists_raw=mapping_list(data.get("artists_raw")), artist_details=mapping_list(data.get("artist_details")),
        )

    def download_artwork(self, result: SpotifyResult, destination: Path) -> dict[str, Any]:
        if not result.album_artwork_url:
            raise SpotifyError("SPOTIFY_ARTWORK_UNAVAILABLE: track/album response has no artwork image URL")
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp = destination.with_name("." + destination.name + ".part")
        temp.unlink(missing_ok=True)
        try:
            response = self._session.get(result.album_artwork_url, timeout=self._timeout, stream=True)
            if not response.ok:
                raise SpotifyError(f"SPOTIFY_ARTWORK_DOWNLOAD_FAILED: HTTP {response.status_code}")
            mime = (response.headers.get("Content-Type") or "image/jpeg").split(";", 1)[0].strip().lower()
            with temp.open("wb") as fh:
                for chunk in response.iter_content(1024 * 128):
                    if chunk:
                        fh.write(chunk)
            if temp.stat().st_size == 0:
                raise SpotifyError("SPOTIFY_ARTWORK_DOWNLOAD_FAILED: empty response")
            temp.replace(destination)
        finally:
            temp.unlink(missing_ok=True)
        return {
            "path": destination,
            "mime": mime,
            "url": result.album_artwork_url,
            "width": result.album_artwork_width,
            "height": result.album_artwork_height,
        }


def apply_spotify_metadata(metadata, result: SpotifyResult):
    """Overlay Spotify catalog values onto source metadata without deleting source-only fields."""
    changes: dict[str, Any] = {}
    if result.track_name:
        changes["title"] = result.track_name
    if result.artists:
        changes["artists"] = list(result.artists)
        changes["artist"] = result.artist_string
        changes["primary_artist"] = result.artists[0]
    if result.album_name:
        changes["album"] = result.album_name
    if result.isrc:
        changes["isrc"] = result.isrc
    if result.album_artist_string:
        changes["album_artist"] = result.album_artist_string
    if result.album_release_date:
        changes["release_date"] = _normalize_date(result.album_release_date)
        changes["release_date_source"] = "spotify.album.release_date"
    if result.track_number is not None:
        changes["track_number"] = str(result.track_number)
        if result.album_total_tracks:
            changes["track_number"] = f"{result.track_number}/{result.album_total_tracks}"
    if result.disc_number is not None:
        changes["disc_number"] = str(result.disc_number)
    if result.artist_genres:
        changes["genre"] = ", ".join(result.artist_genres)
    if result.album_copyrights:
        changes["copyright"] = " | ".join(result.album_copyrights)
    if result.album_label:
        changes["publisher"] = result.album_label
    return type(metadata)(
        **{
            **{f.name: getattr(metadata, f.name) for f in metadata.__dataclass_fields__.values() if f.name != "raw"},
            **changes,
            "raw": metadata.raw,
        }
    )
